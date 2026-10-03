"""LM Studio embeddings client with model identity guard, explicit load and batching."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from agent.kb_limits import MAX_EMBED_CHARS
from agent.llm_client import get_lm_studio_client
from agent.schemas import normalize_base_url
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

MSG_MODEL_NOT_FOUND = "Модель {model} не найдена или не загружается в LM Studio."
MSG_MODEL_NOT_EMBEDDING = (
    "Модель {model} не поддерживает эмбеддинги в LM Studio (тип {type}). "
    "Выберите модель типа embeddings, например nomic-embed-text."
)
MSG_LM_STUDIO_DOWN = "LM Studio не запущен. Запустите его и попробуйте снова."
MSG_LM_STUDIO_TIMEOUT = "LM Studio не ответил вовремя. Повторите попытку позже."
MSG_BAD_EMBED_RESPONSE = "LM Studio вернул некорректный ответ на запрос эмбеддингов."

# Substring of the lower-cased model id -> (query prefix, document prefix).
MODEL_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic": ("search_query: ", "search_document: "),
}
EMBED_RETRIES = 2
EMBED_BATCH_SIZE = 32
_PREFIX_SLACK = 64


class EmbeddingError(Exception):
    """Embedding failure carrying a user-facing Russian message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def prefixes_for(model_id: str) -> tuple[str, str]:
    """Return (query_prefix, doc_prefix) for a model id."""
    lowered = model_id.lower()
    for marker, prefixes in MODEL_PREFIXES.items():
        if marker in lowered:
            return prefixes
    return ("", "")


def _base(base_url: str | None) -> str:
    """Normalized LM Studio base URL, defaulting to the configured host."""
    return normalize_base_url(base_url or settings.LM_STUDIO_BASE_URL)


def _fail(model_id: str, exc: Exception, message: str) -> EmbeddingError:
    """Log a failure without input text and build the matching EmbeddingError."""
    logger.error("kb_embed_failed", model_id=model_id, error=type(exc).__name__)
    return EmbeddingError(message)


async def _fetch_v0_models(base: str, model_id: str) -> list[dict[str, Any]]:
    """GET /api/v0/models, mapping transport errors to EmbeddingError."""
    try:
        async with httpx.AsyncClient(timeout=settings.KB_EMBED_TIMEOUT) as client:
            response = await client.get(f"{base}/api/v0/models")
            response.raise_for_status()
            data = response.json().get("data")
    except httpx.ConnectError as exc:
        raise _fail(model_id, exc, MSG_LM_STUDIO_DOWN) from exc
    except httpx.TimeoutException as exc:
        raise _fail(model_id, exc, MSG_LM_STUDIO_TIMEOUT) from exc
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise _fail(model_id, exc, MSG_BAD_EMBED_RESPONSE) from exc
    if not isinstance(data, list):
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE)
    return [item for item in data if isinstance(item, dict)]


async def list_embedding_models(base_url: str | None = None) -> list[dict[str, Any]]:
    """List LM Studio models with type, loaded state and embedding eligibility."""
    items = await _fetch_v0_models(_base(base_url), "")
    return [
        {
            "id": str(item.get("id", "")),
            "type": str(item.get("type", "")),
            "loaded": item.get("state") == "loaded",
            "eligible": item.get("type") == "embeddings",
        }
        for item in items
        if item.get("id")
    ]


def _find(items: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    """Find a /api/v0/models entry by exact id."""
    for item in items:
        if item.get("id") == model_id:
            return item
    return None


async def _post_load(base: str, model_id: str) -> None:
    """Load a model without unloading anything; the switch lock covers this call only."""
    try:
        async with get_lm_studio_client(base).model_switch_lock:
            async with httpx.AsyncClient(timeout=settings.KB_EMBED_TIMEOUT) as client:
                response = await client.post(
                    f"{base}/api/v1/models/load", json={"model": model_id}
                )
                response.raise_for_status()
    except httpx.ConnectError as exc:
        raise _fail(model_id, exc, MSG_LM_STUDIO_DOWN) from exc
    except httpx.TimeoutException as exc:
        raise _fail(model_id, exc, MSG_LM_STUDIO_TIMEOUT) from exc
    except httpx.HTTPError as exc:
        raise _fail(model_id, exc, MSG_MODEL_NOT_FOUND.format(model=model_id)) from exc


async def ensure_embedding_model(
    model_id: str,
    base_url: str | None = None,
    on_loading: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Require model_id to be a loaded embeddings model, loading it once if needed."""
    base = _base(base_url)
    entry = _find(await _fetch_v0_models(base, model_id), model_id)
    if entry is None:
        raise EmbeddingError(MSG_MODEL_NOT_FOUND.format(model=model_id))
    model_type = str(entry.get("type", ""))
    if model_type != "embeddings":
        raise EmbeddingError(MSG_MODEL_NOT_EMBEDDING.format(model=model_id, type=model_type))
    if entry.get("state") == "loaded":
        return
    if on_loading is not None:
        await on_loading()
    await _post_load(base, model_id)
    entry = _find(await _fetch_v0_models(base, model_id), model_id)
    if entry is None or entry.get("state") != "loaded":
        raise EmbeddingError(MSG_MODEL_NOT_FOUND.format(model=model_id))


def _validate_vectors(data: Any, expected: int) -> list[list[float]]:
    """Order by index and check count, list type and constant dimension."""
    if not isinstance(data, list) or len(data) != expected:
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE)
    try:
        ordered = sorted(data, key=lambda item: item["index"])
        vectors = [item["embedding"] for item in ordered]
    except (KeyError, TypeError) as exc:
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE) from exc
    if not all(isinstance(v, list) and v for v in vectors):
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE)
    if len({len(v) for v in vectors}) != 1:
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE)
    return vectors


async def _embed_batch(base: str, model_id: str, batch: list[str]) -> list[list[float]]:
    """POST one batch to /v1/embeddings with bounded retries on timeout and 5xx."""
    last: EmbeddingError | None = None
    for attempt in range(EMBED_RETRIES + 1):
        if attempt:
            await asyncio.sleep(float(attempt))
        try:
            async with httpx.AsyncClient(timeout=settings.KB_EMBED_TIMEOUT) as client:
                response = await client.post(
                    f"{base}/v1/embeddings", json={"model": model_id, "input": batch}
                )
                response.raise_for_status()
                body = response.json()
        except httpx.ConnectError as exc:
            raise _fail(model_id, exc, MSG_LM_STUDIO_DOWN) from exc
        except httpx.TimeoutException as exc:
            last = _fail(model_id, exc, MSG_LM_STUDIO_TIMEOUT)
            continue
        except httpx.HTTPStatusError as exc:
            last = _fail(model_id, exc, MSG_BAD_EMBED_RESPONSE)
            if exc.response.status_code >= 500:
                continue
            raise last from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise _fail(model_id, exc, MSG_BAD_EMBED_RESPONSE) from exc
        data = body.get("data") if isinstance(body, dict) else None
        return _validate_vectors(data, len(batch))
    if last is None:
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE)
    raise last


async def embed_texts(
    model_id: str, texts: list[str], base_url: str | None = None
) -> list[list[float]]:
    """Embed texts in batches; returns one vector per input with a constant dimension."""
    if any(len(text) > MAX_EMBED_CHARS + _PREFIX_SLACK for text in texts):
        raise ValueError("input exceeds the maximum embedding length")
    base = _base(base_url)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(await _embed_batch(base, model_id, texts[start : start + EMBED_BATCH_SIZE]))
    if len({len(v) for v in vectors}) > 1:
        raise EmbeddingError(MSG_BAD_EMBED_RESPONSE)
    return vectors


async def embed_passages(
    model_id: str, texts: list[str], base_url: str | None = None
) -> list[list[float]]:
    """Embed document passages with the model's document prefix."""
    prefix = prefixes_for(model_id)[1]
    return await embed_texts(model_id, [prefix + text for text in texts], base_url)


async def embed_query(model_id: str, text: str, base_url: str | None = None) -> list[float]:
    """Embed a search query with the model's query prefix."""
    prefix = prefixes_for(model_id)[0]
    return (await embed_texts(model_id, [prefix + text], base_url))[0]
