"""The /help command over the plain-HTTP chat endpoint."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import AsyncClient

from agent import kb_search
from agent import state as agent_state
from agent.events import hub as events_hub
from agent.help_command import HELP_USAGE_TEXT
from agent.kb_indexer import run_index_job
from shared.config import settings
from shared.database import async_session_factory
from shared.models import ChatRagConfig, HelpConfig, KbStatus
from tests.kb_helpers import get_kb, install_fake_embedder, seed_kb, vector_for
from tests.test_http_chat import (
    COMPLETIONS,
    RESPONSE_KEYS,
    _api_client,
    _chat,
    _key,
    _mock_upstream,
    _post,
    _rows,
)
from tests.test_memory_ws import _plain_content_response, _queue_responses, _tool_calls_response

QUESTION = "О чём раздел номер 3?"
DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
LOCAL_GIT_PREFIX = "mcp__local-git__"


@pytest.fixture(autouse=True)
def _help_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake embeddings with a fixed query vector and quiet background jobs."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)


async def _ready_kb(user_id: int) -> int:
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    kb = await get_kb(kb_id)
    assert kb is not None and kb.status == KbStatus.READY
    return kb_id


async def _mark(user_id: int, kb_id: int | None) -> None:
    async with async_session_factory() as session:
        session.add(HelpConfig(user_id=user_id, kb_id=kb_id, threshold=0.0))
        await session.commit()


async def _rag_config(chat_id: int) -> ChatRagConfig | None:
    async with async_session_factory() as session:
        return await session.get(ChatRagConfig, chat_id)


async def _add_rag_config(chat_id: int, kb_id: int | None, mode: str) -> None:
    async with async_session_factory() as session:
        session.add(ChatRagConfig(chat_id=chat_id, kb_id=kb_id, mode=mode, top_k=3, strict=False))
        await session.commit()


async def _setup(client: AsyncClient, mark: bool = True) -> tuple[str, int, int, str]:
    """Create a key, a ready base (marked when asked) and a chat; return key, kb, chat, name."""
    key = await _key(client)
    user_id = client.seeded_user_id
    kb_id = await _ready_kb(user_id)
    if mark:
        await _mark(user_id, kb_id)
    chat_id = await _chat(client)
    kb = await get_kb(kb_id)
    assert kb is not None
    return key, kb_id, chat_id, kb.name


async def _add_server(client: AsyncClient, name: str) -> None:
    created = await client.post(
        "/api/v1/mcp/servers",
        json={"name": name, "command": sys.executable, "args": [FIXTURE, "tools_extra"]},
    )
    assert created.status_code == 201, created.text
    connected = await client.post(f"/api/v1/mcp/servers/{created.json()['id']}/connect")
    assert connected.json()["connection"]["status"] == "connected"


@respx.mock
async def test_usage_notice_body(authenticated_client: AsyncClient) -> None:
    """D-14: a bare /help returns the 200 notice body and stores nothing."""
    upstream = _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="/help")
    assert resp.status_code == 200
    assert resp.json() == {
        "chat_id": chat_id,
        "stored": False,
        "help": {"kind": "usage", "text": HELP_USAGE_TEXT},
    }
    assert await _rows(chat_id) == [] and upstream.requests == []


@respx.mock
async def test_no_base_notice(authenticated_client: AsyncClient) -> None:
    """A question without a marked base returns the no_base notice and no model call."""
    upstream = _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client, mark=False)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="/help как запустить?")
    body = resp.json()
    assert resp.status_code == 200 and body["stored"] is False
    assert body["help"]["kind"] == "no_base"
    assert await _rows(chat_id) == [] and upstream.requests == []


@respx.mock
async def test_real_turn_body(authenticated_client: AsyncClient) -> None:
    """A real /help turn returns the answer, sources, quotes, tool_calls and the help block."""
    _mock_upstream()
    key, kb_id, chat_id, name = await _setup(authenticated_client)
    await _add_server(authenticated_client, "local-git")
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=f"/help {QUESTION}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["content"] and data["user_message_id"] and data["assistant_message_id"]
    assert data["rag"]["kb_id"] == kb_id and data["rag"]["sources"]
    assert isinstance(data["rag"]["quotes"], list) and data["tool_calls"] == []
    assert data["help"] == {"kb_name": name, "warnings": []}
    assert len(await _rows(chat_id)) == 2


@respx.mock
async def test_tool_call_is_reported(authenticated_client: AsyncClient) -> None:
    """D-09: a tool call of the local-git server appears in tool_calls."""
    respx.post(COMPLETIONS).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [("c1", f"{LOCAL_GIT_PREFIX}echo", json.dumps({"text": "git-ok"}))]
                ),
                _plain_content_response("Ветка проверена."),
            ]
        )
    )
    key, _, chat_id, _ = await _setup(authenticated_client)
    await _add_server(authenticated_client, "local-git")
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=f"/help {QUESTION}")
    assert resp.status_code == 200, resp.text
    calls = resp.json()["tool_calls"]
    assert [(c["name"], c["ok"]) for c in calls] == [(f"{LOCAL_GIT_PREFIX}echo", True)]


@respx.mock
async def test_missing_default_server_warns(authenticated_client: AsyncClient) -> None:
    """D-08: no local-git server still gives 200 and one warning in help.warnings."""
    upstream = _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=f"/help {QUESTION}")
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["help"]["warnings"]
    assert [w["server"] for w in warnings] == ["local-git"]
    assert "tools" not in upstream.requests[0]


@respx.mock
async def test_kb_id_is_ignored_for_help(authenticated_client: AsyncClient) -> None:
    """D-16: the marked base answers, and the chat's RAG config is neither created nor changed."""
    _mock_upstream()
    key, marked_kb, chat_id, _ = await _setup(authenticated_client)
    other_kb = await _ready_kb(authenticated_client.seeded_user_id)
    other_chat = await _chat(authenticated_client)
    await _add_rag_config(other_chat, None, "off")
    before = await _rag_config(other_chat)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=f"/help {QUESTION}", kb_id=other_kb)
        again = await _post(api, other_chat, key, content=f"/help {QUESTION}", kb_id=other_kb)
    assert resp.status_code == 200 and resp.json()["rag"]["kb_id"] == marked_kb
    assert again.status_code == 200
    assert await _rag_config(chat_id) is None
    after = await _rag_config(other_chat)
    assert before is not None and after is not None
    assert (after.kb_id, after.mode, after.updated_at) == (
        before.kb_id, before.mode, before.updated_at,
    )


@respx.mock
async def test_missing_kb_id_is_not_validated_for_help(
    authenticated_client: AsyncClient,
) -> None:
    """D-16: a non-existent kb_id gives 200 with /help and 404 with an ordinary message."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        help_resp = await _post(api, chat_id, key, content=f"/help {QUESTION}", kb_id=999999)
        ordinary = await _post(api, chat_id, key, content="обычный", kb_id=999999)
    assert help_resp.status_code == 200
    assert ordinary.status_code == 404


@respx.mock
async def test_null_kb_id_keeps_the_chat_config(authenticated_client: AsyncClient) -> None:
    """D-16: kb_id null with /help does not turn RAG off for the chat."""
    _mock_upstream()
    key, kb_id, chat_id, _ = await _setup(authenticated_client)
    await _add_rag_config(chat_id, kb_id, "rag")
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=f"/help {QUESTION}", kb_id=None)
    assert resp.status_code == 200, resp.text
    row = await _rag_config(chat_id)
    assert row is not None and row.mode == "rag" and row.kb_id == kb_id


@respx.mock
async def test_notices_take_no_rate_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-14: two usage notices leave the single slot for an ordinary message."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key, content="/help")).status_code == 200
        assert (await _post(api, chat_id, key, content="/help")).status_code == 200
        assert (await _post(api, chat_id, key, content="обычный")).status_code == 200


@respx.mock
async def test_real_help_turn_takes_a_rate_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real /help turn counts toward the limit: the next message gets 429."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key, content=f"/help {QUESTION}")).status_code == 200
        resp = await _post(api, chat_id, key, content="обычный")
    assert resp.status_code == 429 and "Retry-After" in resp.headers


@respx.mock
async def test_notice_needs_no_chat_lock(authenticated_client: AsyncClient) -> None:
    """A notice is answered even while another turn holds the chat lock."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    lock = agent_state.chat_locks.setdefault(chat_id, asyncio.Lock())
    await lock.acquire()
    try:
        async with _api_client() as api:
            resp = await _post(api, chat_id, key, content="/help")
    finally:
        lock.release()
    assert resp.status_code == 200 and resp.json()["stored"] is False


@respx.mock
async def test_chat_updated_event_only_for_real_turns(
    authenticated_client: AsyncClient,
) -> None:
    """A real /help turn publishes chat_updated; a notice does not."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    queue = events_hub.subscribe(authenticated_client.seeded_user_id)

    def updated() -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        while not queue.empty():
            frame = queue.get_nowait()
            if frame.get("type") == "chat_updated":
                frames.append(frame)
        return frames

    async with _api_client() as api:
        await _post(api, chat_id, key, content="/help")
        assert updated() == []
        await _post(api, chat_id, key, content=f"/help {QUESTION}")
        assert updated() == [{"type": "chat_updated", "chat_id": chat_id}]


@respx.mock
async def test_help_without_credentials_is_401(authenticated_client: AsyncClient) -> None:
    """No key and no cookie: 401 for a /help message."""
    _mock_upstream()
    _, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, None, content="/help")
    assert resp.status_code == 401


@respx.mock
async def test_ordinary_message_body_has_help_null(authenticated_client: AsyncClient) -> None:
    """An ordinary message keeps its body and gains only help: null."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="привет")
    data = resp.json()
    assert resp.status_code == 200 and set(data) == RESPONSE_KEYS
    assert data["help"] is None
    assert len(await _rows(chat_id)) == 2
