"""Plain-HTTP chat endpoint: knowledge-base selection through kb_id and tool use."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import AsyncClient
from sqlmodel import select

from agent import kb_search
from agent import state as agent_state
from agent.kb_indexer import run_index_job
from agent.rag_api import MSG_KB_NOT_READY
from shared.config import settings
from shared.database import async_session_factory
from shared.models import ChatRagConfig, KbStatus, LongTermMemory
from tests.kb_helpers import get_kb, install_fake_embedder, seed_kb, vector_for
from tests.test_http_chat import (
    _api_client,
    _bearer,
    _chat,
    _key,
    _mock_upstream,
    _post,
    _rows,
)
from tests.test_memory_ws import _plain_content_response, _queue_responses, _tool_calls_response

COMPLETIONS = f"{settings.LM_STUDIO_BASE_URL}/v1/chat/completions"
BLOCK_MARK = "=== Фрагменты из базы знаний"
QUESTION = "О чём раздел номер 3?"
DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


@pytest.fixture(autouse=True)
def _rag_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake embeddings and quiet background jobs for every test in this module."""
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


async def _config(chat_id: int) -> ChatRagConfig | None:
    async with async_session_factory() as session:
        return await session.get(ChatRagConfig, chat_id)


def _has_block(request_body: dict[str, Any]) -> bool:
    users = [m for m in request_body["messages"] if m["role"] == "user"]
    return BLOCK_MARK in users[-1]["content"]


async def _save_config(
    client: AsyncClient, chat_id: int, kb_id: int | None, mode: str = "rag", **extra: Any
) -> None:
    body = {"mode": mode, "kb_id": kb_id, "top_k": 3, "strict": False, **extra}
    resp = await client.put(f"/api/v1/chats/{chat_id}/rag", json=body)
    assert resp.status_code == 200, resp.text


async def test_key_lists_the_kb_to_select(authenticated_client: AsyncClient) -> None:
    """D-07: the key lists the user's knowledge bases, whose ids go into kb_id."""
    key = await _key(authenticated_client)
    kb_id = await _ready_kb(authenticated_client.seeded_user_id)
    async with _api_client() as api:
        listed = await api.get("/api/v1/kb", headers=_bearer(key))
    assert listed.status_code == 200
    assert kb_id in [item["id"] for item in listed.json()]


@respx.mock
async def test_kb_id_selects_kb_and_saves_config(authenticated_client: AsyncClient) -> None:
    """D-06: an id uses that KB for the turn and saves kb_id with mode rag."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    kb_id = await _ready_kb(authenticated_client.seeded_user_id)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=QUESTION, kb_id=kb_id)
    assert resp.status_code == 200, resp.text
    rag = resp.json()["rag"]
    assert rag is not None and rag["mode"] == "rag"
    assert _has_block(upstream.requests[0])
    row = await _config(chat_id)
    assert row is not None and row.kb_id == kb_id and row.mode == "rag"


@respx.mock
async def test_kb_id_keeps_saved_search_settings(authenticated_client: AsyncClient) -> None:
    """D-06: choosing another KB never resets top_k, hybrid or strict."""
    _mock_upstream()
    key = await _key(authenticated_client)
    user_id = authenticated_client.seeded_user_id
    kb_a = await _ready_kb(user_id)
    kb_b = await _ready_kb(user_id)
    chat_id = await _chat(authenticated_client)
    await _save_config(authenticated_client, chat_id, kb_a, top_k=7, hybrid=True, strict=False)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=QUESTION, kb_id=kb_b)
    assert resp.status_code == 200, resp.text
    row = await _config(chat_id)
    assert row is not None
    assert row.kb_id == kb_b
    assert row.top_k == 7
    assert row.hybrid is True
    assert row.strict is False


@respx.mock
async def test_explicit_null_turns_rag_off(authenticated_client: AsyncClient) -> None:
    """D-06: "kb_id": null runs the turn without RAG and saves mode off."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    kb_id = await _ready_kb(authenticated_client.seeded_user_id)
    chat_id = await _chat(authenticated_client)
    await _save_config(authenticated_client, chat_id, kb_id)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=QUESTION, kb_id=None)
        assert resp.status_code == 200, resp.text
        assert not _has_block(upstream.requests[0])
        saved = await api.get(f"/api/v1/chats/{chat_id}/rag", headers=_bearer(key))
    row = await _config(chat_id)
    assert row is not None and row.kb_id is None and row.mode == "off"
    assert saved.json()["mode"] == "off"


@respx.mock
async def test_omitted_field_uses_saved_config(authenticated_client: AsyncClient) -> None:
    """D-06: without kb_id the saved RAG configuration is used and left unchanged."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    kb_id = await _ready_kb(authenticated_client.seeded_user_id)
    chat_id = await _chat(authenticated_client)
    await _save_config(authenticated_client, chat_id, kb_id, top_k=4)
    before = await _config(chat_id)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=QUESTION)
    assert resp.status_code == 200, resp.text
    assert _has_block(upstream.requests[0])
    after = await _config(chat_id)
    assert before is not None and after is not None
    assert (after.kb_id, after.mode, after.top_k) == (before.kb_id, before.mode, before.top_k)
    assert after.updated_at == before.updated_at


@respx.mock
async def test_new_chat_omitted_field_runs_without_rag(
    authenticated_client: AsyncClient,
) -> None:
    """D-06: a chat that never had a config answers without RAG and gets no config row."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    await _ready_kb(authenticated_client.seeded_user_id)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=QUESTION)
    assert resp.status_code == 200, resp.text
    assert not _has_block(upstream.requests[0])
    assert await _config(chat_id) is None


@respx.mock
async def test_selection_persists_to_the_next_turn(authenticated_client: AsyncClient) -> None:
    """D-06: a kb_id sent once keeps working for turns that omit the field."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    kb_id = await _ready_kb(authenticated_client.seeded_user_id)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key, content=QUESTION, kb_id=kb_id)).status_code == 200
        assert (await _post(api, chat_id, key, content=QUESTION)).status_code == 200
    assert _has_block(upstream.requests[0])
    # Auxiliary calls (query condensing, follow-ups) sit between the chat calls.
    assert sum(1 for request in upstream.requests if _has_block(request)) == 2


@respx.mock
async def test_foreign_kb_gives_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient,
) -> None:
    """T-19-26: another user's KB answers 404 with the default shape and stores nothing."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    foreign_kb = await _ready_kb(second_authenticated_client.seeded_user_id)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, kb_id=foreign_kb)
        assert resp.status_code == 404
        assert set(resp.json().keys()) == {"detail"}
        assert not agent_state.chat_locks[chat_id].locked()
    assert await _rows(chat_id) == []
    assert await _config(chat_id) is None
    assert upstream.requests == []


@respx.mock
async def test_not_ready_kb_gives_422(authenticated_client: AsyncClient) -> None:
    """A KB that is not READY answers 422 with the readiness message and stores nothing."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    kb_id = await seed_kb(authenticated_client.seeded_user_id, DISTINCT)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, kb_id=kb_id)
    assert resp.status_code == 422
    assert resp.json()["detail"] == MSG_KB_NOT_READY
    assert await _rows(chat_id) == []
    assert await _config(chat_id) is None
    assert upstream.requests == []


@respx.mock
async def test_kb_id_zero_is_rejected(authenticated_client: AsyncClient) -> None:
    """The schema bound kb_id >= 1 gives 422."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, kb_id=0)
    assert resp.status_code == 422


@respx.mock
async def test_builtin_tool_call_over_http(authenticated_client: AsyncClient) -> None:
    """D-09, D-13: a built-in tool runs and is reported in tool_calls and memory_writes."""
    respx.post(COMPLETIONS).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [
                        (
                            "call_1",
                            "save_long_term_memory",
                            json.dumps({"key": "user_name", "content": "Alex"}),
                        ),
                    ],
                ),
                _plain_content_response("Got it, I saved your name."),
            ],
        ),
    )
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="remember my name is Alex")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [f["tool"] for f in data["tool_calls"]] == ["save_long_term_memory"]
    assert data["memory_writes"]
    assert data["content"] == "Got it, I saved your name."
    async with async_session_factory() as session:
        rows = (await session.exec(select(LongTermMemory))).all()
    assert [(r.key, r.user_id) for r in rows] == [
        ("user_name", authenticated_client.seeded_user_id)
    ]


@respx.mock
async def test_mcp_tools_are_offered_without_allowlist(
    authenticated_client: AsyncClient,
) -> None:
    """D-09: built-in and namespaced MCP tools are both offered to the model."""
    created = await authenticated_client.post(
        "/api/v1/mcp/servers",
        json={"name": "Fixture", "command": sys.executable, "args": [FIXTURE, "tools_extra"]},
    )
    assert created.status_code == 201, created.text
    connected = await authenticated_client.post(
        f"/api/v1/mcp/servers/{created.json()['id']}/connect"
    )
    assert connected.json()["connection"]["status"] == "connected"
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="use the tool")
    assert resp.status_code == 200, resp.text
    names = [t["function"]["name"] for t in upstream.requests[0].get("tools", [])]
    assert "save_long_term_memory" in names
    assert "mcp__fixture__echo" in names
