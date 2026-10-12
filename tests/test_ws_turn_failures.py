"""Failed chat turns restore the chat leaf and LLM_ERROR frames carry a machine-readable cause."""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent import state as agent_state
from agent import ws as agent_ws
from agent.headless import RecordingSink
from agent.main import app
from agent.schemas import MessagePayload
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, Message
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"
COMPLETIONS = f"{BASE_URL}/v1/chat/completions"


def _ok_response(text: str = "Готово.") -> httpx.Response:
    """Build a plain-content SSE response."""
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


class _Upstream:
    """Mutable behaviour of the mocked completions endpoint."""

    def __init__(self) -> None:
        self.failure: Exception | httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        return _ok_response()


def _mock_upstream() -> _Upstream:
    upstream = _Upstream()
    respx.post(COMPLETIONS).mock(side_effect=upstream)
    return upstream


async def _lock_released(chat_id: int) -> None:
    """Wait until the turn, including its failure cleanup, has released the chat lock."""
    for _ in range(100):
        lock = agent_state.chat_locks.get(chat_id)
        if lock is None or not lock.locked():
            return
        await asyncio.sleep(0.05)


def _send_and_drain(
    client: TestClient, ws: Any, chat_id: int, content: str,
) -> list[dict[str, Any]]:
    """Send one message and collect frames up to the terminal frame, then wait for cleanup."""
    ws.send_json({"content": content, "model": MODEL})
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done" or frame.get("code") in _TERMINAL_CODES:
            client.portal.call(_lock_released, chat_id)
            return frames


_TERMINAL_CODES = {
    "CONTEXT_OVERFLOW",
    "LLM_ERROR",
    "RAG_GATED_FAILED",
    "PROVIDER_UNAVAILABLE",
}


async def _leaf(chat_id: int) -> int | None:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        return chat.current_leaf_message_id


async def _messages(chat_id: int) -> list[Message]:
    async with async_session_factory() as session:
        result = await session.exec(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
        )
        return list(result.all())


def _new_chat(client: TestClient) -> int:
    login_test_client(client)
    return client.post("/api/v1/chats", json={"title": "Failures"}).json()["id"]


def _set_strategy(client: TestClient, chat_id: int, strategy: str) -> None:
    resp = client.put(
        "/api/v1/settings",
        json={
            "chat_id": chat_id,
            "strategy": strategy,
            "context_length": 4096,
            "max_tokens": 512,
        },
    )
    assert resp.status_code == 200


def _connect(client: TestClient, chat_id: int) -> Any:
    return client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN})


@respx.mock
def test_overflow_restores_leaf() -> None:
    _mock_upstream()
    with TestClient(app) as client:
        chat_id = _new_chat(client)
        _set_strategy(client, chat_id, "no_compression")
        with _connect(client, chat_id) as ws:
            first = _send_and_drain(client, ws, chat_id, "hello")
            assert first[-1]["type"] == "done"
            reply_id = first[-1]["message_id"]
            frames = _send_and_drain(client, ws, chat_id, "word " * 6000)
        error = frames[-1]
        assert error["code"] == "CONTEXT_OVERFLOW"
        assert error["suggested_strategy"] == "sliding"
        assert len(client.portal.call(_messages, chat_id)) == 2
        leaf = client.portal.call(_leaf, chat_id)
        assert leaf is not None
        assert leaf == reply_id


@respx.mock
def test_next_turn_after_failure_continues_branch() -> None:
    _mock_upstream()
    with TestClient(app) as client:
        chat_id = _new_chat(client)
        _set_strategy(client, chat_id, "no_compression")
        with _connect(client, chat_id) as ws:
            first = _send_and_drain(client, ws, chat_id, "hello")
            reply_id = first[-1]["message_id"]
            failed = _send_and_drain(client, ws, chat_id, "word " * 6000)
            assert failed[-1]["code"] == "CONTEXT_OVERFLOW"
            _set_strategy(client, chat_id, "sliding")
            third = _send_and_drain(client, ws, chat_id, "again")
            assert third[-1]["type"] == "done"
        rows = client.portal.call(_messages, chat_id)
        again = [m for m in rows if m.content == "again"][0]
        assert again.parent_id == reply_id
        tree = client.get(f"/api/v1/chats/{chat_id}/tree").json()
        assert len(tree) == 4
        assert {m["content"] for m in tree} >= {"hello", "again"}
        assert tree[-1]["content"] == "hello"


@respx.mock
def test_llm_unreachable_restores_leaf_and_sets_cause() -> None:
    upstream = _mock_upstream()
    with TestClient(app) as client:
        chat_id = _new_chat(client)
        with _connect(client, chat_id) as ws:
            first = _send_and_drain(client, ws, chat_id, "hello")
            reply_id = first[-1]["message_id"]
            upstream.failure = httpx.ConnectError("refused")
            frames = _send_and_drain(client, ws, chat_id, "second")
        error = frames[-1]
        assert error["code"] == "LLM_ERROR"
        assert error["cause"] == "unreachable"
        assert error["provider_kind"] == "lm_studio"
        assert error["detail"]
        assert len(client.portal.call(_messages, chat_id)) == 2
        assert client.portal.call(_leaf, chat_id) == reply_id


@pytest.mark.parametrize(
    ("failure", "cause"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.Response(500, text="boom"), "upstream"),
    ],
)
@respx.mock
def test_llm_timeout_and_http_error_causes(
    failure: Exception | httpx.Response, cause: str,
) -> None:
    upstream = _mock_upstream()
    with TestClient(app) as client:
        chat_id = _new_chat(client)
        with _connect(client, chat_id) as ws:
            first = _send_and_drain(client, ws, chat_id, "hello")
            reply_id = first[-1]["message_id"]
            upstream.failure = failure
            frames = _send_and_drain(client, ws, chat_id, "second")
        error = frames[-1]
        assert error["code"] == "LLM_ERROR"
        assert error["cause"] == cause
        assert client.portal.call(_leaf, chat_id) == reply_id


@respx.mock
def test_first_turn_failure_leaves_empty_chat() -> None:
    upstream = _mock_upstream()
    upstream.failure = httpx.ConnectError("refused")
    with TestClient(app) as client:
        chat_id = _new_chat(client)
        with _connect(client, chat_id) as ws:
            frames = _send_and_drain(client, ws, chat_id, "hello")
        assert frames[-1]["code"] == "LLM_ERROR"
        assert client.portal.call(_messages, chat_id) == []
        assert client.portal.call(_leaf, chat_id) is None


async def _seed_two_messages(chat_id: int) -> tuple[int, int]:
    async with async_session_factory() as session:
        m1 = Message(chat_id=chat_id, parent_id=None, role="user", content="m1", token_count=1)
        session.add(m1)
        await session.flush()
        m2 = Message(
            chat_id=chat_id, parent_id=m1.id, role="assistant", content="m2", token_count=1,
        )
        session.add(m2)
        await session.flush()
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        chat.current_leaf_message_id = m2.id
        session.add(chat)
        await session.commit()
        return m1.id, m2.id


async def _discard(chat_id: int, message_id: int, parent_id: int | None) -> None:
    async with async_session_factory() as session:
        await agent_ws._discard_user_message(session, chat_id, message_id, parent_id)


def test_discard_user_message_helper() -> None:
    with TestClient(app) as client:
        chat_id = _new_chat(client)
        m1, m2 = client.portal.call(_seed_two_messages, chat_id)
        client.portal.call(_discard, chat_id, m2, m1)
        assert [m.id for m in client.portal.call(_messages, chat_id)] == [m1]
        assert client.portal.call(_leaf, chat_id) == m1
        # Already gone: no error, leaf unchanged.
        client.portal.call(_discard, chat_id, m2, m1)
        assert client.portal.call(_leaf, chat_id) == m1
        # Leaf elsewhere: only the message is removed, the leaf stays.
        m3 = client.portal.call(_seed_sibling, chat_id, m1)
        client.portal.call(_discard, chat_id, m3, None)
        assert client.portal.call(_leaf, chat_id) == m1


async def _seed_sibling(chat_id: int, parent_id: int) -> int:
    async with async_session_factory() as session:
        sibling = Message(
            chat_id=chat_id, parent_id=parent_id, role="user", content="m3", token_count=1,
        )
        session.add(sibling)
        await session.commit()
        await session.refresh(sibling)
        return sibling.id


def test_handle_chat_message_takes_lock_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    chat_id = 987

    async def fake_turn(websocket: Any, cid: int, payload: MessagePayload) -> None:
        calls.append(agent_state.chat_locks[cid].locked())

    monkeypatch.setattr(agent_ws, "_run_chat_turn", fake_turn)
    agent_state.chat_locks.pop(chat_id, None)

    async def run() -> None:
        await agent_ws._handle_chat_message(
            RecordingSink(), chat_id, MessagePayload(content="x", model="m"),
        )

    asyncio.run(run())
    assert calls == [True]
    assert not agent_state.chat_locks[chat_id].locked()
    agent_state.chat_locks.pop(chat_id, None)


@respx.mock
def test_run_chat_turn_works_with_recording_sink() -> None:
    _mock_upstream()
    with TestClient(app) as client:
        chat_id = _new_chat(client)

        async def run() -> list[dict[str, Any]]:
            sink = RecordingSink()
            lock = agent_state.chat_locks.setdefault(chat_id, asyncio.Lock())
            async with lock:
                await agent_ws._run_chat_turn(
                    sink, chat_id, MessagePayload(content="hello", model=MODEL),
                )
            return sink.frames

        frames = client.portal.call(run)
        assert frames[-1]["type"] == "done"
        rows = client.portal.call(_messages, chat_id)
        assistant = [m for m in rows if m.role == "assistant"]
        assert [m.id for m in assistant] == [frames[-1]["message_id"]]
