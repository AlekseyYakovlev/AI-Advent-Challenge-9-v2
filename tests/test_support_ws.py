"""The /support command over WebSocket: notices, the direct lookup card, warnings, history, side jobs."""

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import respx
from starlette.testclient import TestClient

from agent import kb_search
from agent.main import app
from agent.support_command import SUPPORT_NO_BASE_TEXT, SUPPORT_USAGE_TEXT
from shared.config import settings
from shared.database import async_session_factory
from shared.models import SupportConfig
from tests.kb_helpers import install_fake_embedder, vector_for
from tests.test_help_ws import (
    QUESTION,
    WS_ORIGIN,
    _chat_row,
    _drain,
    _open,
    _rows,
    _types,
    _upstream,
)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SERVER: str = str(REPO_ROOT / "mcp_servers" / "support_desk.py")
SEED: Path = REPO_ROOT / "mcp_servers" / "support_data.json"
AUTH_QUESTION = "Почему не работает авторизация?"


@pytest.fixture(autouse=True)
def _support_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake embeddings with a fixed query vector and quiet background jobs."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)


@pytest.fixture
def data_copy(tmp_path: Path) -> Path:
    """A private copy of the seed support data."""
    path = tmp_path / "support_data.json"
    shutil.copyfile(SEED, path)
    return path


def _add_support(client: TestClient, data: Path) -> None:
    created = client.post(
        "/api/v1/mcp/servers",
        json={"name": "support", "command": sys.executable, "args": [SERVER, f"--data={data}"]},
    )
    assert created.status_code == 201, created.text


async def _save_threshold(user_id: int, threshold: float | None) -> None:
    async with async_session_factory() as session:
        session.add(SupportConfig(user_id=user_id, threshold=threshold))
        await session.commit()


def _last_user_text(body: dict[str, Any]) -> str:
    return body["messages"][-1]["content"]


@respx.mock
def test_ticket_turn_streams_card_before_tokens(data_copy: Path) -> None:
    """A ticket lookup sends its tool_call frame first, then streams; the payload is stored."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        user_id, _, chat_id = _open(client)
        client.portal.call(_save_threshold, user_id, 0.0)
        _add_support(client, data_copy)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/support #T-1001 {AUTH_QUESTION}")
        rows = client.portal.call(_rows, chat_id)
    types = _types(frames)
    call = next(f for f in frames if f["type"] == "tool_call")
    assert call["name"] == "mcp__support__get_ticket" and call["ok"] is True
    first_token = next((i for i, t in enumerate(types) if t == "token"), len(types))
    assert types.index("tool_call") < first_token
    done = frames[-1]
    assert done["type"] == "done"
    assert done["rag"]["support"]["label"] == "Поддержка · T-1001"
    last_user = _last_user_text(captured[0])
    assert "<support_data>" in last_user and "Ticket T-1001" in last_user
    assert f"Вопрос: {AUTH_QUESTION}" in last_user
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == f"/support #T-1001 {AUTH_QUESTION}"
    assert "support" in json.loads(rows[1].rag_sources)
    assert json.loads(rows[1].tool_trace)[0]["name"] == "mcp__support__get_ticket"


@respx.mock
def test_unknown_ticket_warns_and_still_answers(data_copy: Path) -> None:
    """A not-found ticket never refuses the turn: warning in the done frame, no data block."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        user_id, _, chat_id = _open(client)
        client.portal.call(_save_threshold, user_id, 0.0)
        _add_support(client, data_copy)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/support #T-9999 вопрос")
    done = frames[-1]
    assert done["type"] == "done"
    warnings = done["rag"]["support"]["warnings"]
    assert "Тикет T-9999 не найден, ответ дан без данных тикета." in [w["text"] for w in warnings]
    assert "<support_data>" not in _last_user_text(captured[0])


@respx.mock
def test_missing_support_server_warns_and_still_answers() -> None:
    """Without a support server row the answer is produced with a not-added warning."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        user_id, _, chat_id = _open(client)
        client.portal.call(_save_threshold, user_id, 0.0)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/support #T-1001 вопрос")
    assert frames[-1]["type"] == "done"
    assert not [f for f in frames if f["type"] == "tool_call"]
    warnings = frames[-1]["rag"]["support"]["warnings"]
    assert any("(не добавлен)" in w["text"] for w in warnings)


@respx.mock
def test_usage_notice_stores_nothing() -> None:
    """A bare /support sends one support usage notice, no model call, no rows."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/support")
        rows = client.portal.call(_rows, chat_id)
    assert len(frames) == 1
    assert frames[0]["type"] == "help_notice" and frames[0]["command"] == "support"
    assert frames[0]["kind"] == "usage" and frames[0]["text"] == SUPPORT_USAGE_TEXT
    assert rows == [] and captured == []


@respx.mock
def test_no_base_notice() -> None:
    """With no marked base the no_base notice is sent and nothing is stored."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client, mark=False)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/support вопрос")
        rows = client.portal.call(_rows, chat_id)
    assert len(frames) == 1 and frames[0]["kind"] == "no_base"
    assert frames[0]["command"] == "support" and frames[0]["text"] == SUPPORT_NO_BASE_TEXT
    assert rows == [] and captured == []


@respx.mock
def test_notices_take_no_rate_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A /support usage notice leaves the rate slot free for an ordinary message."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            assert _drain(ws, "/support")[-1]["type"] == "help_notice"
            assert _drain(ws, "/support")[-1]["type"] == "help_notice"
            assert _drain(ws, "обычный")[-1]["type"] == "done"


@respx.mock
def test_history_carries_only_support_pairs(data_copy: Path) -> None:
    """D-05: earlier /support pairs reach the model; ordinary and /help pairs do not."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        user_id, _, chat_id = _open(client)
        client.portal.call(_save_threshold, user_id, 0.0)
        _add_support(client, data_copy)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            _drain(ws, "MARK-ORDINARY-TEXT")
            _drain(ws, "/support #T-1001 q1")
            _drain(ws, "/help MARK-HELP-TEXT")
            frames = _drain(ws, "/support а что ещё можно попробовать?")
    assert frames[-1]["type"] == "done"
    dump = json.dumps(captured[-1]["messages"], ensure_ascii=False)
    assert "Тикет T-1001. q1" in dump
    assert "MARK-ORDINARY-TEXT" not in dump and "MARK-HELP-TEXT" not in dump


@respx.mock
def test_side_jobs_do_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A /support turn runs no title, facts, task memory or invariant critique."""
    calls: list[str] = []

    async def critique(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append("critique")
        return {"conflict": False}

    async def task_memory(*args: object, **kwargs: object) -> None:
        calls.append("task_memory")

    monkeypatch.setattr("agent.ws.invariants.run_self_critique", critique)
    monkeypatch.setattr("agent.ws.update_task_memory", task_memory)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *a, **k: calls.append("facts"))
    monkeypatch.setattr(
        "agent.ws.schedule_title_generation", lambda *a, **k: calls.append("title")
    )
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/support вопрос")
        chat = client.portal.call(_chat_row, chat_id)
    assert frames[-1]["type"] == "done" and frames[-1]["invariant_conflict"] is None
    assert calls == [] and chat.title == "Help chat"


@respx.mock
def test_help_turn_is_unchanged() -> None:
    """A /help turn still answers with the help payload and no support key."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
    done = frames[-1]
    assert done["type"] == "done" and "help" in done["rag"] and "support" not in done["rag"]
