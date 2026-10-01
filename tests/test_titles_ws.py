"""WebSocket tests: chat auto-title trigger, request shape, fallback and delivery."""

import json
from typing import Any

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from agent.main import app
from tests.conftest import login_test_client
from tests.test_memory_ws import (
    BASE_URL,
    MODEL,
    WS_ORIGIN,
    _plain_content_response,
    _send_and_drain,
)
from tests.test_tool_rounds_ws import _drain_until_terminal, _stream_queue


def _install_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Replace the title scheduler used by the WS turn with a call recorder."""
    calls: list[tuple[Any, ...]] = []

    def _recorder(*args: Any) -> None:
        calls.append(args)

    monkeypatch.setattr("agent.ws.schedule_title_generation", _recorder)
    return calls


def _chat_titles(client: TestClient) -> dict[int, str]:
    """Return chat id -> title for every chat of the logged-in user."""
    resp = client.get("/api/v1/chats")
    assert resp.status_code == 200
    return {chat["id"]: chat["title"] for chat in resp.json()}


@respx.mock
def test_first_turn_of_default_titled_chat_schedules_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first successful turn of a "New Chat" chat schedules exactly one title job."""
    calls = _install_recorder(monkeypatch)
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue([_plain_content_response("Привет всем")]),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "New Chat"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "Как дела?")

    assert frames[-1]["type"] == "done"
    assert calls == [(chat_id, user_id, "Как дела?", "Привет всем", MODEL)]


@respx.mock
def test_chat_created_with_schema_default_title_is_titled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chat created with an empty body carries the default title and gets one job."""
    calls = _install_recorder(monkeypatch)
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue([_plain_content_response("Ответ")]),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "Вопрос")

    assert len(calls) == 1
    assert calls[0][0] == chat_id


@respx.mock
def test_custom_title_never_schedules_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chat that already has a custom title never starts a title job."""
    calls = _install_recorder(monkeypatch)
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue([_plain_content_response("Ответ")]),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "My project"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "Вопрос")
        titles = _chat_titles(client)

    assert calls == []
    assert titles[chat_id] == "My project"


@respx.mock
def test_second_turn_does_not_schedule_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later turn of the same chat has a parent user message and starts no new job."""
    calls = _install_recorder(monkeypatch)
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [_plain_content_response("Первый"), _plain_content_response("Второй")],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "New Chat"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "Один")
            _send_and_drain(ws, "Два")

    assert len(calls) == 1


@respx.mock
def test_failed_turn_never_schedules_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """A turn that ends in an LLM_ERROR frame starts no job and keeps the default title."""
    calls = _install_recorder(monkeypatch)
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"}),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "New Chat"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _drain_until_terminal(ws, "Вопрос")
        titles = _chat_titles(client)

    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "LLM_ERROR"
    assert calls == []
    assert titles[chat_id] == "New Chat"
