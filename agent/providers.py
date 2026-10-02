"""User-scoped LLM providers: CRUD, seeding, resolution, connection check and model cache."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.llm_client import LLMClient, get_lm_studio_client
from agent.schemas import normalize_base_url
from shared.config import is_valid_env_name, resolve_env_secret, settings
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import LlmProvider, LlmProviderSeed

logger = get_logger(__name__)

KIND_OPENAI = "openai"
KIND_LM_STUDIO = "lm_studio"
SEED_LM_STUDIO = "lm_studio"
SEED_DEEPSEEK = "deepseek"
LM_STUDIO_PROVIDER_NAME = "LM Studio"
DEEPSEEK_PROVIDER_NAME = "DeepSeek"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"

NAME_MAX_LENGTH = 100
BASE_URL_MAX_LENGTH = 500

MSG_NAME_REQUIRED = "Укажите название."
MSG_NAME_TOO_LONG = "Название не длиннее 100 символов."
MSG_BAD_URL = "Base URL должен начинаться с http:// или https://."
MSG_URL_TOO_LONG = "Base URL не длиннее 500 символов."
MSG_BAD_ENV_NAME = "Недопустимое имя переменной окружения."
MSG_NAME_TAKEN = "Провайдер с таким названием уже есть."
MSG_BAD_KEY = (
    "Неверный или отсутствующий API-ключ. "
    "Проверьте переменную {env} в .env и нажмите «Проверить»."
)
MSG_KEY_REQUIRED = (
    "Сервер требует API-ключ. "
    "Укажите переменную окружения с ключом и нажмите «Проверить»."
)
MSG_ENV_MISSING = "Переменная {env} не найдена в .env. Добавьте её и перезапустите приложение."
MSG_UNREACHABLE = "Сервер недоступен. Проверьте Base URL и что сервис запущен."
MSG_TIMEOUT = "Превышено время ожидания ответа. Повторите проверку позже."
MSG_HTTP = "Сервер вернул ошибку {status}. Проверьте Base URL (ожидается путь /v1/models)."
MSG_BAD_RESPONSE = (
    "Сервер вернул неожиданный ответ. Проверьте Base URL (ожидается путь /v1/models)."
)
MSG_PROVIDER_UNAVAILABLE = (
    "Провайдер «{name}» недоступен. "
    "Выберите другую модель или проверьте настройки провайдера."
)
MSG_PROVIDER_UNAVAILABLE_UNNAMED = (
    "Провайдер недоступен. Выберите другую модель или проверьте настройки провайдера."
)

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


class ProviderUnavailableError(Exception):
    """The requested provider is missing, foreign, deleted or disabled."""

    def __init__(self, name: str | None) -> None:
        self.name: str | None = name
        self.message: str = (
            MSG_PROVIDER_UNAVAILABLE.format(name=name)
            if name
            else MSG_PROVIDER_UNAVAILABLE_UNNAMED
        )
        super().__init__(self.message)


class ProviderValidationError(Exception):
    """Provider fields failed validation; the message is user-facing."""

    def __init__(self, message: str) -> None:
        self.message: str = message
        super().__init__(message)


class ProviderNameConflictError(Exception):
    """The user already has a provider with this name."""


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a provider connection check (never carries the API key)."""

    status: str
    code: str | None
    message: str | None
    models: list[dict[str, Any]]
    checked_at: datetime


MODEL_CACHE: dict[tuple[int, int], CheckResult] = {}
_seeded_users: set[int] = set()
# Users seeded while the DeepSeek key was absent; rechecked on every call.
_deepseek_pending: set[int] = set()
_seed_locks: dict[int, asyncio.Lock] = {}


def reset_state() -> None:
    """Clear all in-memory provider state (model cache and seeding bookkeeping)."""
    MODEL_CACHE.clear()
    _seeded_users.clear()
    _deepseek_pending.clear()
    _seed_locks.clear()


def validate_provider_fields(
    name: str | None,
    base_url: str | None,
    api_key_env: str | None,
    *,
    partial: bool,
) -> dict[str, Any]:
    """Validate and clean provider fields; with partial=True only provided fields are returned."""
    cleaned: dict[str, Any] = {}

    if name is not None or not partial:
        value = (name or "").strip()
        if not value:
            raise ProviderValidationError(MSG_NAME_REQUIRED)
        if len(value) > NAME_MAX_LENGTH:
            raise ProviderValidationError(MSG_NAME_TOO_LONG)
        cleaned["name"] = value

    if base_url is not None or not partial:
        url = (base_url or "").strip()
        if not _URL_RE.match(url):
            raise ProviderValidationError(MSG_BAD_URL)
        if len(url) > BASE_URL_MAX_LENGTH:
            raise ProviderValidationError(MSG_URL_TOO_LONG)
        cleaned["base_url"] = normalize_base_url(url)

    if api_key_env is not None or not partial:
        env = (api_key_env or "").strip()
        if not env:
            cleaned["api_key_env"] = None
        elif is_valid_env_name(env):
            cleaned["api_key_env"] = env
        else:
            raise ProviderValidationError(MSG_BAD_ENV_NAME)

    return cleaned


async def list_providers(session: AsyncSession, user_id: int) -> list[LlmProvider]:
    """Return the user's providers ordered by id."""
    result = await session.exec(
        select(LlmProvider).where(LlmProvider.user_id == user_id).order_by(LlmProvider.id),
    )
    return list(result.all())


async def get_provider(
    session: AsyncSession,
    user_id: int,
    provider_id: int,
) -> LlmProvider | None:
    """Return the provider if it belongs to the user, else None."""
    row = await session.get(LlmProvider, provider_id)
    if row is None or row.user_id != user_id:
        return None
    return row


async def create_provider(
    session: AsyncSession,
    user_id: int,
    fields: dict[str, Any],
) -> LlmProvider:
    """Create an OpenAI-compatible provider for the user."""
    row = LlmProvider(user_id=user_id, kind=KIND_OPENAI, **fields)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except IntegrityError as exc:
        await session.rollback()
        raise ProviderNameConflictError(MSG_NAME_TAKEN) from exc
    except Exception:
        await session.rollback()
        raise
    logger.info("llm_provider_created", user_id=user_id, provider_id=row.id)
    return row


async def update_provider(
    session: AsyncSession,
    row: LlmProvider,
    fields: dict[str, Any],
) -> LlmProvider:
    """Apply cleaned fields to a provider and drop its cached model list."""
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except IntegrityError as exc:
        await session.rollback()
        raise ProviderNameConflictError(MSG_NAME_TAKEN) from exc
    except Exception:
        await session.rollback()
        raise
    MODEL_CACHE.pop((row.user_id, row.id), None)
    logger.info("llm_provider_updated", user_id=row.user_id, provider_id=row.id)
    return row


async def delete_provider(session: AsyncSession, row: LlmProvider) -> None:
    """Delete a provider; its seed marker stays so it is never re-created."""
    user_id, provider_id = row.user_id, row.id
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    MODEL_CACHE.pop((user_id, provider_id), None)
    logger.info("llm_provider_deleted", user_id=user_id, provider_id=provider_id)


async def _insert_seed(
    session: AsyncSession,
    user_id: int,
    seed_key: str,
    fields: dict[str, Any],
) -> None:
    """Insert a seeded provider with its marker; on conflict fall back to the marker alone."""
    session.add(LlmProvider(user_id=user_id, **fields))
    session.add(LlmProviderSeed(user_id=user_id, seed_key=seed_key))
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent seeder won, or the user already owns a provider with this name.
        await session.rollback()
        session.add(LlmProviderSeed(user_id=user_id, seed_key=seed_key))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
    except Exception:
        await session.rollback()
        raise
    logger.info("llm_provider_seeded", user_id=user_id, seed_key=seed_key)


def _seed_fields(seed_key: str) -> dict[str, Any]:
    """Return the provider columns for a seed key."""
    if seed_key == SEED_LM_STUDIO:
        return {
            "name": LM_STUDIO_PROVIDER_NAME,
            "base_url": normalize_base_url(settings.LM_STUDIO_BASE_URL),
            "kind": KIND_LM_STUDIO,
            "api_key_env": None,
        }
    return {
        "name": DEEPSEEK_PROVIDER_NAME,
        "base_url": DEEPSEEK_BASE_URL,
        "kind": KIND_OPENAI,
        "api_key_env": DEEPSEEK_KEY_ENV,
    }


async def ensure_seeded(session: AsyncSession, user_id: int) -> None:
    """Create the user's default providers once; a deleted seeded provider is never re-created."""
    if user_id in _seeded_users and (
        user_id not in _deepseek_pending or not resolve_env_secret(DEEPSEEK_KEY_ENV)
    ):
        return

    lock = _seed_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        result = await session.exec(
            select(LlmProviderSeed.seed_key).where(LlmProviderSeed.user_id == user_id),
        )
        done = set(result.all())

        if SEED_LM_STUDIO not in done:
            await _insert_seed(session, user_id, SEED_LM_STUDIO, _seed_fields(SEED_LM_STUDIO))
            done.add(SEED_LM_STUDIO)
        if SEED_DEEPSEEK not in done and resolve_env_secret(DEEPSEEK_KEY_ENV):
            await _insert_seed(session, user_id, SEED_DEEPSEEK, _seed_fields(SEED_DEEPSEEK))
            done.add(SEED_DEEPSEEK)

        _seeded_users.add(user_id)
        if SEED_DEEPSEEK in done:
            _deepseek_pending.discard(user_id)
        else:
            _deepseek_pending.add(user_id)


async def get_provider_row(
    session: AsyncSession,
    user_id: int,
    provider_id: int | None,
) -> LlmProvider:
    """Return the usable provider row; None selects the user's seeded LM Studio provider."""
    await ensure_seeded(session, user_id)
    if provider_id is None:
        result = await session.exec(
            select(LlmProvider)
            .where(
                LlmProvider.user_id == user_id,
                LlmProvider.kind == KIND_LM_STUDIO,
                LlmProvider.enabled.is_(True),
            )
            .order_by(LlmProvider.id),
        )
        legacy = result.first()
        if legacy is None:
            raise ProviderUnavailableError(None)
        return legacy

    row = await get_provider(session, user_id, provider_id)
    if row is None:
        raise ProviderUnavailableError(None)
    if not row.enabled:
        raise ProviderUnavailableError(row.name)
    return row


def build_client(row: LlmProvider) -> LLMClient:
    """Build a fresh client so edits to the base URL or key apply immediately."""
    return LLMClient(
        base_url=row.base_url,
        api_key=resolve_env_secret(row.api_key_env) or "",
    )


async def resolve_client(
    user_id: int | None,
    provider_id: int | None,
) -> tuple[LLMClient, LlmProvider | None]:
    """Resolve the LLM client for a user's provider using a private DB session."""
    if user_id is None:
        return LLMClient(base_url=settings.LM_STUDIO_BASE_URL), None
    async with async_session_factory() as session:
        row = await get_provider_row(session, user_id, provider_id)
    return build_client(row), row


def _parse_models(data: Any, *, lm_studio: bool) -> list[dict[str, Any]]:
    """Validate a /v1/models `data` list and normalize it to id/loaded dicts."""
    if not isinstance(data, list):
        raise ValueError("data is not a list")
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError("model entry without id")
        loaded = bool(item.get("loaded", False)) if lm_studio else None
        models.append({"id": str(item["id"]), "loaded": loaded})
    return models


async def _fetch_models(row: LlmProvider, key: str | None) -> list[dict[str, Any]]:
    """Fetch and parse the provider's model list; raises httpx/ValueError on failure."""
    timeout = settings.LLM_PROVIDER_CHECK_TIMEOUT
    if row.kind == KIND_LM_STUDIO:
        data = await asyncio.wait_for(get_lm_studio_client(row.base_url).list_models(), timeout)
        return _parse_models(data, lm_studio=True)

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = f"{normalize_base_url(row.base_url)}/v1/models"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict):
        raise ValueError("body is not an object")
    return _parse_models(body.get("data"), lm_studio=False)


def _error(code: str, message: str) -> CheckResult:
    """Build an error CheckResult."""
    return CheckResult("error", code, message, [], datetime.now(timezone.utc))


async def check_provider(row: LlmProvider) -> CheckResult:
    """Check connectivity and list models; never raises and never exposes the key."""
    key = resolve_env_secret(row.api_key_env) if row.api_key_env else None
    if row.api_key_env and key is None:
        result = _error("env_missing", MSG_ENV_MISSING.format(env=row.api_key_env))
    else:
        try:
            models = await _fetch_models(row, key)
            result = CheckResult("ok", None, None, models, datetime.now(timezone.utc))
        except httpx.ConnectError:
            result = _error("unreachable", MSG_UNREACHABLE)
        except (httpx.TimeoutException, asyncio.TimeoutError):
            result = _error("timeout", MSG_TIMEOUT)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                message = (
                    MSG_BAD_KEY.format(env=row.api_key_env)
                    if row.api_key_env
                    else MSG_KEY_REQUIRED
                )
                result = _error("bad_key", message)
            else:
                result = _error("http", MSG_HTTP.format(status=status))
        except (ValueError, AttributeError, KeyError, TypeError):
            result = _error("bad_response", MSG_BAD_RESPONSE)
        except httpx.HTTPError:
            result = _error("unreachable", MSG_UNREACHABLE)

    MODEL_CACHE[(row.user_id, row.id)] = result
    logger.info(
        "llm_provider_checked",
        user_id=row.user_id,
        provider_id=row.id,
        status=result.status,
        code=result.code,
        model_count=len(result.models),
    )
    return result


def cached_check(row: LlmProvider) -> CheckResult | None:
    """Return the cached check result for a provider, if any."""
    return MODEL_CACHE.get((row.user_id, row.id))


async def list_model_groups(
    session: AsyncSession,
    user_id: int,
    refresh: bool,
) -> list[dict[str, Any]]:
    """Return per-provider model groups for enabled providers, checking concurrently."""
    await ensure_seeded(session, user_id)
    rows = [row for row in await list_providers(session, user_id) if row.enabled]

    async def _result_for(row: LlmProvider) -> CheckResult:
        cached = cached_check(row)
        if cached is not None and not refresh:
            return cached
        return await check_provider(row)

    results = await asyncio.gather(*(_result_for(row) for row in rows))
    return [
        {
            "provider_id": row.id,
            "name": row.name,
            "kind": row.kind,
            "models": result.models if result.status == "ok" else [],
            "error": None if result.status == "ok" else result.message,
        }
        for row, result in zip(rows, results)
    ]
