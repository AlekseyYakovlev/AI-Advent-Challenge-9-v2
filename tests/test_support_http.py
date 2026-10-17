"""The /support command over the plain-HTTP chat endpoint."""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest
import respx
from httpx import AsyncClient

from agent import state as agent_state
from agent.support_command import SUPPORT_NO_BASE_TEXT, SUPPORT_USAGE_TEXT
from shared.config import settings
from shared.database import async_session_factory
from shared.models import SupportConfig
from tests.test_help_http import (
    QUESTION,
    _add_rag_config,
    _help_setup,  # autouse fixture: fake embeddings and quiet background jobs
    _rag_config,
    _ready_kb,
    _setup,
)
from tests.test_http_chat import (
    RESPONSE_KEYS,
    _api_client,
    _chat,
    _mock_upstream,
    _post,
    _rows,
)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SERVER: str = str(REPO_ROOT / "mcp_servers" / "support_desk.py")
SEED: Path = REPO_ROOT / "mcp_servers" / "support_data.json"
AUTH_QUESTION = "Почему не работает авторизация?"


async def _add_support(client: AsyncClient, tmp_path: Path) -> None:
    data = tmp_path / "support_data.json"
    shutil.copyfile(SEED, data)
    created = await client.post(
        "/api/v1/mcp/servers",
        json={"name": "support", "command": sys.executable, "args": [SERVER, f"--data={data}"]},
    )
    assert created.status_code == 201, created.text


async def _save_threshold(user_id: int, threshold: float) -> None:
    async with async_session_factory() as session:
        session.add(SupportConfig(user_id=user_id, threshold=threshold))
        await session.commit()


@respx.mock
async def test_ticket_turn_body(authenticated_client: AsyncClient, tmp_path: Path) -> None:
    """A ticket turn returns the answer, the direct lookup as tool_calls[0] and the support block."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    await _save_threshold(authenticated_client.seeded_user_id, 0.0)
    await _add_support(authenticated_client, tmp_path)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content=f"/support #T-1002 {AUTH_QUESTION}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == RESPONSE_KEYS and data["content"]
    assert data["support"]["label"] == "Поддержка · T-1002"
    assert data["tool_calls"][0]["name"] == "mcp__support__get_ticket"
    assert data["rag"]["support"] == data["support"]
    assert len(await _rows(chat_id)) == 2


@respx.mock
async def test_usage_notice_body(authenticated_client: AsyncClient) -> None:
    """A bare /support returns the support notice body and stores nothing."""
    upstream = _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="/support")
    assert resp.status_code == 200
    assert resp.json() == {
        "chat_id": chat_id,
        "stored": False,
        "support": {"kind": "usage", "text": SUPPORT_USAGE_TEXT},
    }
    assert await _rows(chat_id) == [] and upstream.requests == []


@respx.mock
async def test_no_base_notice(authenticated_client: AsyncClient) -> None:
    """A question without a marked base returns the no_base notice."""
    upstream = _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client, mark=False)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="/support вопрос")
    assert resp.status_code == 200
    assert resp.json()["support"] == {"kind": "no_base", "text": SUPPORT_NO_BASE_TEXT}
    assert await _rows(chat_id) == [] and upstream.requests == []


@respx.mock
async def test_kb_id_is_ignored_for_support(authenticated_client: AsyncClient) -> None:
    """A kb_id in the body neither changes nor creates the chat's RAG config."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    other_kb = await _ready_kb(authenticated_client.seeded_user_id)
    other_chat = await _chat(authenticated_client)
    await _add_rag_config(other_chat, None, "off")
    before = await _rag_config(other_chat)
    async with _api_client() as api:
        first = await _post(api, chat_id, key, content=f"/support {QUESTION}", kb_id=other_kb)
        second = await _post(api, other_chat, key, content=f"/support {QUESTION}", kb_id=999999)
    assert first.status_code == 200 and second.status_code == 200
    assert await _rag_config(chat_id) is None
    after = await _rag_config(other_chat)
    assert before is not None and after is not None
    assert (after.kb_id, after.mode, after.updated_at) == (
        before.kb_id, before.mode, before.updated_at,
    )


@respx.mock
async def test_unknown_ticket_warning_in_body(
    authenticated_client: AsyncClient, tmp_path: Path
) -> None:
    """A not-found ticket still answers and reports the warning in the support block."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    await _save_threshold(authenticated_client.seeded_user_id, 0.0)
    await _add_support(authenticated_client, tmp_path)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, content="/support #T-9999 вопрос")
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["support"]["warnings"]
    assert warnings[0]["text"] == "Тикет T-9999 не найден, ответ дан без данных тикета."


@respx.mock
async def test_notices_take_no_rate_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two /support usage notices leave the single slot for an ordinary message."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key, content="/support")).status_code == 200
        assert (await _post(api, chat_id, key, content="/support")).status_code == 200
        assert (await _post(api, chat_id, key, content="обычный")).status_code == 200


@respx.mock
async def test_busy_chat_returns_409(authenticated_client: AsyncClient) -> None:
    """A real /support turn on a busy chat gets 409 like any message; a notice does not."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    lock = agent_state.chat_locks.setdefault(chat_id, asyncio.Lock())
    await lock.acquire()
    try:
        async with _api_client() as api:
            busy = await _post(api, chat_id, key, content=f"/support {QUESTION}")
            notice = await _post(api, chat_id, key, content="/support")
    finally:
        lock.release()
    assert busy.status_code == 409 and busy.json()["code"] == "CHAT_BUSY"
    assert notice.status_code == 200 and notice.json()["stored"] is False


@respx.mock
async def test_ordinary_and_help_bodies_have_support_null(
    authenticated_client: AsyncClient,
) -> None:
    """Ordinary and /help responses keep their keys and report support: null."""
    _mock_upstream()
    key, _, chat_id, _ = await _setup(authenticated_client)
    async with _api_client() as api:
        ordinary = await _post(api, chat_id, key, content="привет")
        helped = await _post(api, chat_id, key, content=f"/help {QUESTION}")
    for resp in (ordinary, helped):
        assert resp.status_code == 200 and set(resp.json()) == RESPONSE_KEYS
        assert resp.json()["support"] is None
