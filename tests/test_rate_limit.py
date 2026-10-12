"""Shared per-chat message limit for WebSocket and HTTP chat (LIM-01, LIM-02)."""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlmodel import select
from starlette.testclient import TestClient

from agent import limits
from agent import state as agent_state
from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, Message
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
COMPLETIONS = f"{BASE_URL}/v1/chat/completions"
MODEL = "rate-test-model"
WS_ORIGIN = "http://localhost:8000"
REPLY = "Ответ готов."


def _ok_response(text: str = REPLY) -> httpx.Response:
    """Build a plain-content SSE response."""
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


class _Upstream:
    """Mocked completions endpoint that records each call."""

    def __init__(self) -> None:
        self.failure: Exception | None = None
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return _ok_response()


def _mock_upstream() -> _Upstream:
    upstream = _Upstream()
    respx.post(COMPLETIONS).mock(side_effect=upstream)
    return upstream


@pytest.fixture(autouse=True)
def _quiet_background_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep title generation and fact extraction from calling the mocked upstream."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> list[float]:
    """Replace the limits clock with a controllable one and return its holder."""
    clock = [start]
    monkeypatch.setattr(limits, "_now", lambda: clock[0])
    return clock


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _key(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/api-keys", json={"name": "t"})
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


async def _chat(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/chats", json={"title": "Rate chat"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _api_client() -> AsyncClient:
    """Build a cookie-less client so only the Authorization header authenticates."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post(
    client: AsyncClient, chat_id: int, key: str, **body: Any
) -> httpx.Response:
    body.setdefault("content", "привет")
    body.setdefault("model", MODEL)
    return await client.post(
        f"/api/v1/chats/{chat_id}/messages", json=body, headers=_bearer(key)
    )


async def _rows(chat_id: int) -> list[Message]:
    async with async_session_factory() as session:
        result = await session.exec(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
        )
        return list(result.all())


async def _leaf(chat_id: int) -> int | None:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        return chat.current_leaf_message_id


def _ws_send_and_wait(ws: Any, content: str) -> dict[str, Any]:
    """Send one message and return the first done or error frame."""
    ws.send_json({"content": content, "model": MODEL})
    while True:
        frame = ws.receive_json()
        if frame.get("type") in {"done", "error"}:
            return frame


@respx.mock
async def test_http_429_contract(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-04: the third message over the limit gets 429 with Retry-After and the fixed body."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
        assert (await _post(api, chat_id, key)).status_code == 200
        resp = await _post(api, chat_id, key)
    assert resp.status_code == 429
    header = resp.headers["Retry-After"]
    assert header.isdigit()
    assert 1 <= int(header) <= 60
    assert resp.json() == {
        "detail": "Rate limit exceeded",
        "code": "RATE_LIMITED",
        "user_message_kept": False,
        "retry_after": int(header),
    }


@respx.mock
async def test_429_stores_nothing_and_calls_no_model(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-04: a refused message leaves no row, keeps the leaf and never reaches the model."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        await _post(api, chat_id, key)
        second = (await _post(api, chat_id, key)).json()
        assert (await _post(api, chat_id, key)).status_code == 429
    rows = await _rows(chat_id)
    assert len(rows) == 4
    assert await _leaf(chat_id) == second["assistant_message_id"]
    assert upstream.calls == 2
    assert not agent_state.chat_locks[chat_id].locked()


@respx.mock
async def test_429_takes_no_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-03: refused requests do not extend the window."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        await _post(api, chat_id, key)
        await _post(api, chat_id, key)
        assert (await _post(api, chat_id, key)).status_code == 429
        assert len(agent_state.ws_rate_limiter[chat_id]) == 2
        assert (await _post(api, chat_id, key)).status_code == 429
        assert len(agent_state.ws_rate_limiter[chat_id]) == 2


@respx.mock
async def test_409_takes_no_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-03: a busy chat answers 409 CHAT_BUSY and records nothing."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    lock = asyncio.Lock()
    await lock.acquire()
    agent_state.chat_locks[chat_id] = lock
    async with _api_client() as api:
        resp = await _post(api, chat_id, key)
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAT_BUSY"
    assert not agent_state.ws_rate_limiter.get(chat_id)


@respx.mock
async def test_409_comes_before_429(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy chat with a full window answers 409, not 429."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
        lock = asyncio.Lock()
        await lock.acquire()
        agent_state.chat_locks[chat_id] = lock
        resp = await _post(api, chat_id, key)
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAT_BUSY"


@respx.mock
async def test_404_takes_no_slot(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """A chat of another user answers 404 and records no timestamp."""
    _mock_upstream()
    key = await _key(authenticated_client)
    foreign_chat = await _chat(second_authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, foreign_chat, key)
    assert resp.status_code == 404
    assert not agent_state.ws_rate_limiter.get(foreign_chat)


@respx.mock
async def test_context_overflow_takes_a_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-03: a turn that fails with context overflow still counts."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    put = await authenticated_client.put(
        "/api/v1/settings",
        json={
            "chat_id": chat_id,
            "strategy": "no_compression",
            "context_length": 4096,
            "max_tokens": 512,
        },
    )
    assert put.status_code == 200, put.text
    async with _api_client() as api:
        for _ in range(2):
            resp = await _post(api, chat_id, key, content="word " * 6000)
            assert resp.status_code == 413
        resp = await _post(api, chat_id, key, content="word " * 6000)
    assert resp.status_code == 429


@respx.mock
async def test_model_error_takes_a_slot(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-03: a turn that fails because the model is down still counts."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    upstream = _mock_upstream()
    upstream.failure = httpx.ConnectError("refused")
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 503
        assert (await _post(api, chat_id, key)).status_code == 429


@respx.mock
def test_window_is_shared_websocket_then_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-02: a WebSocket message and an HTTP message take slots in the same window."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    _mock_upstream()
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Shared"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            assert _ws_send_and_wait(ws, "one")["type"] == "done"
        url = f"/api/v1/chats/{chat_id}/messages"
        body = {"content": "two", "model": MODEL}
        headers = {"Origin": WS_ORIGIN}
        assert client.post(url, json=body, headers=headers).status_code == 200
        assert client.post(url, json=body, headers=headers).status_code == 429


@respx.mock
def test_window_is_shared_http_then_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-02, D-05: after an HTTP message the WebSocket gets a RATE_LIMITED frame."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _mock_upstream()
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Shared"}).json()["id"]
        resp = client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "one", "model": MODEL},
            headers={"Origin": WS_ORIGIN},
        )
        assert resp.status_code == 200
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frame = _ws_send_and_wait(ws, "two")
            assert frame["type"] == "error"
            assert frame["code"] == "RATE_LIMITED"
            assert isinstance(frame["retry_after"], int)
            assert frame["retry_after"] >= 1
            ws.send_json({"content": "three", "model": MODEL})
            assert ws.receive_json()["code"] == "RATE_LIMITED"


@respx.mock
def test_same_seconds_on_both_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-05: the WebSocket retry_after equals the HTTP Retry-After under a frozen clock."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _freeze_clock(monkeypatch)
    _mock_upstream()
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Seconds"}).json()["id"]
        url = f"/api/v1/chats/{chat_id}/messages"
        headers = {"Origin": WS_ORIGIN}
        assert (
            client.post(url, json={"content": "a", "model": MODEL}, headers=headers).status_code
            == 200
        )
        refused = client.post(url, json={"content": "b", "model": MODEL}, headers=headers)
        assert refused.status_code == 429
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frame = _ws_send_and_wait(ws, "c")
    assert frame["retry_after"] == int(refused.headers["Retry-After"])


@respx.mock
async def test_window_expires(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window frees a slot once its oldest message is older than a minute."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    clock = _freeze_clock(monkeypatch)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
        refused = await _post(api, chat_id, key)
        assert refused.status_code == 429
        assert refused.headers["Retry-After"] == "60"
        clock[0] += 61.0
        assert (await _post(api, chat_id, key)).status_code == 200


@respx.mock
async def test_limit_zero_switches_the_limit_off(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-01: 0 means no limit and nothing is recorded."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 0)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        for _ in range(5):
            assert (await _post(api, chat_id, key)).status_code == 200
    assert chat_id not in agent_state.ws_rate_limiter


@respx.mock
def test_default_limit_is_30(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-01: the default in force is 30; the old constant of 10 is gone."""
    assert settings.RATE_LIMIT_MESSAGES_PER_MINUTE == 30
    _mock_upstream()
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Default"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            for i in range(11):
                assert _ws_send_and_wait(ws, f"m{i}")["type"] == "done"


@respx.mock
async def test_limit_is_per_chat(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each chat has its own window."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_a = await _chat(authenticated_client)
    chat_b = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_a, key)).status_code == 200
        assert (await _post(api, chat_b, key)).status_code == 200


@respx.mock
async def test_deleting_chat_clears_its_window(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a chat removes its window from the in-memory table."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 5)
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
    assert chat_id in agent_state.ws_rate_limiter
    resp = await authenticated_client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code in (200, 204)
    assert chat_id not in agent_state.ws_rate_limiter
