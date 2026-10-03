"""Tests for the LM Studio embeddings client: guard, load, batching, prefixes, errors."""

import json
from typing import Any

import httpx
import pytest
import respx

from agent import embeddings
from agent.embeddings import (
    MSG_BAD_EMBED_RESPONSE,
    MSG_LM_STUDIO_DOWN,
    MSG_LM_STUDIO_TIMEOUT,
    MSG_MODEL_NOT_EMBEDDING,
    MSG_MODEL_NOT_FOUND,
    EmbeddingError,
    embed_passages,
    embed_query,
    embed_texts,
    ensure_embedding_model,
    list_embedding_models,
    prefixes_for,
)
from agent.llm_client import get_lm_studio_client
from shared.config import settings

BASE = settings.LM_STUDIO_BASE_URL.rstrip("/")
NOMIC = "text-embedding-nomic-embed-text-v1.5"
GIGA = "giga-embeddings-instruct-480m-0826"


def _models(nomic_state: str = "loaded") -> dict[str, Any]:
    return {
        "data": [
            {"id": NOMIC, "type": "embeddings", "state": nomic_state},
            {"id": GIGA, "type": "llm", "state": "loaded"},
            {"id": "liquid/lfm2-1.2b", "type": "llm", "state": "not-loaded"},
        ]
    }


def _vec_response(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]},
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of sleeping."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(embeddings.asyncio, "sleep", fake_sleep)
    return delays


def test_prefixes_for() -> None:
    assert prefixes_for(NOMIC) == ("search_query: ", "search_document: ")
    assert prefixes_for(GIGA) == ("", "")
    assert prefixes_for("bge-m3") == ("", "")


@respx.mock
async def test_list_embedding_models() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models()))
    result = {m["id"]: m for m in await list_embedding_models()}
    assert result[NOMIC]["eligible"] is True and result[NOMIC]["loaded"] is True
    assert result[GIGA]["eligible"] is False
    assert result["liquid/lfm2-1.2b"]["loaded"] is False


@respx.mock
async def test_ensure_loaded_makes_no_load_request() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models()))
    load = respx.post(f"{BASE}/api/v1/models/load").mock(return_value=httpx.Response(200))
    await ensure_embedding_model(NOMIC)
    assert not load.called


@respx.mock
async def test_ensure_rejects_llm_typed_model() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models()))
    load = respx.post(f"{BASE}/api/v1/models/load").mock(return_value=httpx.Response(200))
    with pytest.raises(EmbeddingError) as info:
        await ensure_embedding_model(GIGA)
    assert info.value.message == MSG_MODEL_NOT_EMBEDDING.format(model=GIGA, type="llm")
    assert not load.called


@respx.mock
async def test_ensure_missing_model() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models()))
    with pytest.raises(EmbeddingError) as info:
        await ensure_embedding_model("missing")
    assert info.value.message == MSG_MODEL_NOT_FOUND.format(model="missing")


@respx.mock
async def test_ensure_loads_not_loaded_without_touching_chat_model() -> None:
    client = get_lm_studio_client(BASE)
    before = client._current_loaded_model
    respx.get(f"{BASE}/api/v0/models").mock(
        side_effect=[
            httpx.Response(200, json=_models("not-loaded")),
            httpx.Response(200, json=_models("loaded")),
        ]
    )
    load = respx.post(f"{BASE}/api/v1/models/load").mock(return_value=httpx.Response(200, json={}))
    unload = respx.post(f"{BASE}/api/v1/models/unload").mock(return_value=httpx.Response(200))
    calls: list[str] = []

    async def on_loading() -> None:
        calls.append("x")

    await ensure_embedding_model(NOMIC, on_loading=on_loading)
    assert calls == ["x"]
    assert load.call_count == 1
    assert json.loads(load.calls.last.request.content) == {"model": NOMIC}
    assert not unload.called
    assert client._current_loaded_model == before


@respx.mock
async def test_ensure_still_not_loaded_after_load_fails() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(
        return_value=httpx.Response(200, json=_models("not-loaded"))
    )
    respx.post(f"{BASE}/api/v1/models/load").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(EmbeddingError) as info:
        await ensure_embedding_model(NOMIC)
    assert info.value.message == MSG_MODEL_NOT_FOUND.format(model=NOMIC)


@respx.mock
async def test_ensure_load_http_error() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(
        return_value=httpx.Response(200, json=_models("not-loaded"))
    )
    respx.post(f"{BASE}/api/v1/models/load").mock(return_value=httpx.Response(500))
    with pytest.raises(EmbeddingError) as info:
        await ensure_embedding_model(NOMIC)
    assert info.value.message == MSG_MODEL_NOT_FOUND.format(model=NOMIC)


@respx.mock
async def test_connect_error_and_timeout_mapping() -> None:
    respx.get(f"{BASE}/api/v0/models").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(EmbeddingError) as info:
        await ensure_embedding_model(NOMIC)
    assert info.value.message == MSG_LM_STUDIO_DOWN
    respx.get(f"{BASE}/api/v0/models").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(EmbeddingError) as info:
        await ensure_embedding_model(NOMIC)
    assert info.value.message == MSG_LM_STUDIO_TIMEOUT


@respx.mock
async def test_embed_texts_orders_by_index_and_posts_model() -> None:
    route = respx.post(f"{BASE}/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [3.0, 4.0]},
                    {"index": 0, "embedding": [1.0, 2.0]},
                ]
            },
        )
    )
    vectors = await embed_texts(NOMIC, ["a", "b"])
    assert vectors == [[1.0, 2.0], [3.0, 4.0]]
    assert json.loads(route.calls.last.request.content) == {"model": NOMIC, "input": ["a", "b"]}


@respx.mock
async def test_embed_texts_batches() -> None:
    route = respx.post(f"{BASE}/v1/embeddings").mock(
        side_effect=lambda request: _vec_response(
            [[1.0, 2.0] for _ in json.loads(request.content)["input"]]
        )
    )
    vectors = await embed_texts(NOMIC, [f"t{i}" for i in range(70)])
    assert len(vectors) == 70
    assert route.call_count == 3


@respx.mock
@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [1.0]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}],
        [{"index": 0, "embedding": "x"}, {"index": 1, "embedding": [1.0]}],
    ],
)
async def test_embed_texts_bad_response(data: list[dict[str, Any]]) -> None:
    respx.post(f"{BASE}/v1/embeddings").mock(return_value=httpx.Response(200, json={"data": data}))
    with pytest.raises(EmbeddingError) as info:
        await embed_texts(NOMIC, ["a", "b"])
    assert info.value.message == MSG_BAD_EMBED_RESPONSE


@respx.mock
async def test_embed_retries_5xx_then_succeeds(no_sleep: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/embeddings").mock(
        side_effect=[httpx.Response(503), httpx.Response(500), _vec_response([[1.0]])]
    )
    assert await embed_texts(NOMIC, ["a"]) == [[1.0]]
    assert route.call_count == 3
    assert no_sleep == [1.0, 2.0]


@respx.mock
async def test_embed_retries_exhausted_timeout() -> None:
    route = respx.post(f"{BASE}/v1/embeddings").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(EmbeddingError) as info:
        await embed_texts(NOMIC, ["a"])
    assert info.value.message == MSG_LM_STUDIO_TIMEOUT
    assert route.call_count == 3


@respx.mock
async def test_embed_4xx_not_retried() -> None:
    route = respx.post(f"{BASE}/v1/embeddings").mock(return_value=httpx.Response(400))
    with pytest.raises(EmbeddingError):
        await embed_texts(NOMIC, ["a"])
    assert route.call_count == 1


@respx.mock
async def test_embed_connect_error() -> None:
    respx.post(f"{BASE}/v1/embeddings").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(EmbeddingError) as info:
        await embed_texts(NOMIC, ["a"])
    assert info.value.message == MSG_LM_STUDIO_DOWN


@respx.mock
async def test_passages_and_query_prefixes() -> None:
    route = respx.post(f"{BASE}/v1/embeddings").mock(return_value=_vec_response([[1.0, 2.0]]))
    await embed_passages(NOMIC, ["doc"])
    assert json.loads(route.calls.last.request.content)["input"] == ["search_document: doc"]
    assert await embed_query(NOMIC, "q") == [1.0, 2.0]
    assert json.loads(route.calls.last.request.content)["input"] == ["search_query: q"]
    await embed_passages(GIGA, ["doc"])
    assert json.loads(route.calls.last.request.content)["input"] == ["doc"]


async def test_oversized_input_raises_value_error() -> None:
    with pytest.raises(ValueError):
        await embed_texts(NOMIC, ["x" * 5000])
