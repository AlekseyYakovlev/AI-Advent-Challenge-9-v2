"""WebSocket tests: chat auto-title trigger, request shape, fallback and delivery."""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from agent.events import hub
from agent.llm_client import ChatCompletionResult
from agent.main import app
from agent.titles import TITLE_SYSTEM_PROMPT, fallback_title
from tests.conftest import _create_user, login_test_client
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


USER_TEXT = "Как настроить WebSocket в FastAPI?"
ANSWER_TEXT = "Используйте декоратор app.websocket"
TITLE_TAG = "<user_message>"


def _title_json(content: str) -> httpx.Response:
    """Build a non-streaming completion body carrying the given content."""
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _title_side_effect(
    stream_responses: list[httpx.Response],
    title_response: httpx.Response | Exception,
):
    """Serve streams in order, the title call from title_response, facts calls an empty JSON."""
    queue = list(stream_responses)

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream") is True:
            return queue.pop(0)
        if TITLE_TAG in body["messages"][-1]["content"]:
            if isinstance(title_response, Exception):
                raise title_response
            return title_response
        return _title_json("{}")

    return _side_effect


def _title_bodies(route: respx.Route) -> list[dict[str, Any]]:
    """Return the JSON bodies of non-streaming requests that ask for a title."""
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [
        b for b in bodies
        if b.get("stream") is not True and TITLE_TAG in b["messages"][-1]["content"]
    ]


async def _subscribe(user_id: int) -> asyncio.Queue[dict[str, Any]]:
    """Subscribe to the user's event frames on the app loop."""
    return hub.subscribe(user_id)


async def _next_frame(queue: asyncio.Queue[dict[str, Any]], timeout: float = 5.0) -> dict[str, Any]:
    """Wait for the next event frame."""
    return await asyncio.wait_for(queue.get(), timeout)


async def _queue_empty(queue: asyncio.Queue[dict[str, Any]]) -> bool:
    """Report whether the queue holds no frames."""
    return queue.empty()


def _run_first_turn(
    client: TestClient,
    chat_id: int,
    user_text: str = USER_TEXT,
) -> list[dict[str, Any]]:
    """Open the chat socket, run one turn and return its frames."""
    with client.websocket_connect(
        f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
    ) as ws:
        return _send_and_drain(ws, user_text)


def _new_chat(client: TestClient) -> int:
    """Create a default-titled chat and return its id."""
    return client.post("/api/v1/chats", json={"title": "New Chat"}).json()["id"]


@respx.mock
def test_title_frame_and_stored_title() -> None:
    """The owner gets one chat_title_updated frame and the chat list shows the title."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)],
            _title_json("Настройка WebSocket в FastAPI"),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        _run_first_turn(client, chat_id)
        frame = client.portal.call(_next_frame, queue)
        titles = _chat_titles(client)

    assert frame == {
        "type": "chat_title_updated",
        "chat_id": chat_id,
        "title": "Настройка WebSocket в FastAPI",
    }
    assert titles[chat_id] == "Настройка WebSocket в FastAPI"


@respx.mock
def test_title_request_shape() -> None:
    """The title call is non-streaming, temperature 0, max_tokens 30, no tools, no chat prompt."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)], _title_json("Настройка WebSocket"),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        _run_first_turn(client, _new_chat(client))
        client.portal.call(_next_frame, queue)

    bodies = _title_bodies(route)
    assert len(bodies) == 1
    body = bodies[0]
    assert body["stream"] is False
    assert body["temperature"] == 0
    assert body["max_tokens"] == 30
    assert body["reasoning_effort"] == "none"
    assert body["model"] == MODEL
    assert "tools" not in body
    assert len(body["messages"]) == 2
    assert body["messages"][0] == {"role": "system", "content": TITLE_SYSTEM_PROMPT}
    assert f"<user_message>{USER_TEXT}</user_message>" in body["messages"][1]["content"]
    assert f"<assistant_answer>{ANSWER_TEXT}" in body["messages"][1]["content"]


@respx.mock
def test_reasoning_only_title_answer_falls_back_and_turn_completes() -> None:
    """A reasoning-only title answer still ends the turn with done and the fallback title."""
    reasoning = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Thinking Process: ...",
                    },
                }
            ],
            "usage": {"completion_tokens": 30},
        },
    )
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect([_plain_content_response(ANSWER_TEXT)], reasoning),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        turn_frames = _run_first_turn(client, _new_chat(client))
        frame = client.portal.call(_next_frame, queue)

    assert turn_frames[-1]["type"] == "done"
    assert frame["type"] == "chat_title_updated"
    assert frame["title"] == fallback_title(USER_TEXT)


@respx.mock
def test_quoted_labelled_answer_is_cleaned() -> None:
    """A quoted, labelled model answer is stored and pushed without quotes and label."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)], _title_json('"Title: Настройка WebSocket"'),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        _run_first_turn(client, chat_id)
        frame = client.portal.call(_next_frame, queue)
        titles = _chat_titles(client)

    assert frame["type"] == "chat_title_updated"
    assert frame["title"] == "Настройка WebSocket"
    assert titles[chat_id] == "Настройка WebSocket"


@respx.mock
def test_http_error_falls_back_to_user_message() -> None:
    """An HTTP 500 on the title call titles the chat with the truncated user message."""
    long_text = "слово " * 30
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)], httpx.Response(500, json={"error": "x"}),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        _run_first_turn(client, chat_id, long_text)
        frame = client.portal.call(_next_frame, queue)

    assert frame["title"] == fallback_title(long_text)
    assert len(frame["title"]) <= 50
    assert frame["title"].endswith("…")


@respx.mock
def test_connect_error_falls_back_and_turn_still_completes() -> None:
    """A connection error on the title call yields the fallback; the turn still ends in done."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)], httpx.ConnectError("down"),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        frames = _run_first_turn(client, chat_id)
        frame = client.portal.call(_next_frame, queue)

    assert frames[-1]["type"] == "done"
    assert frame["title"] == fallback_title(USER_TEXT)


@respx.mock
def test_frame_goes_to_owner_only() -> None:
    """Another user subscribed to events receives nothing."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)], _title_json("Настройка WebSocket"),
        ),
    )
    with TestClient(app) as client:
        owner_id = login_test_client(client)
        other_id = client.portal.call(_create_user, "bob", "pw")
        owner_queue = client.portal.call(_subscribe, owner_id)
        other_queue = client.portal.call(_subscribe, other_id)
        _run_first_turn(client, _new_chat(client))
        client.portal.call(_next_frame, owner_queue)
        other_empty = client.portal.call(_queue_empty, other_queue)

    assert other_empty is True


@respx.mock
def test_title_lands_after_socket_closed() -> None:
    """Leaving the chat socket right after done does not lose the title."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response(ANSWER_TEXT)], _title_json("Настройка WebSocket"),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        _run_first_turn(client, chat_id)
        frame = client.portal.call(_next_frame, queue)
        titles = _chat_titles(client)

    assert frame["chat_id"] == chat_id
    assert titles[chat_id] == "Настройка WebSocket"


@respx.mock
def test_done_is_not_delayed_by_blocked_title_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """done arrives while the title call is still blocked; the title lands once it is released."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect([_plain_content_response(ANSWER_TEXT)], _title_json("{}")),
    )

    async def _make_event() -> asyncio.Event:
        return asyncio.Event()

    async def _release(event: asyncio.Event) -> None:
        event.set()

    with TestClient(app) as client:
        user_id = login_test_client(client)
        gate = client.portal.call(_make_event)

        async def _fake_complete_chat(**kwargs: Any) -> ChatCompletionResult:
            await gate.wait()
            return ChatCompletionResult(
                content="Отложенный заголовок",
                finish_reason="stop",
                has_reasoning=False,
                completion_tokens=None,
            )

        monkeypatch.setattr(
            "agent.titles.llm_client.complete_chat_detailed", _fake_complete_chat
        )
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        frames = _run_first_turn(client, chat_id)
        blocked_titles = _chat_titles(client)
        client.portal.call(_release, gate)
        frame = client.portal.call(_next_frame, queue)
        titles = _chat_titles(client)

    assert frames[-1]["type"] == "done"
    assert blocked_titles[chat_id] == "New Chat"
    assert frame["title"] == "Отложенный заголовок"
    assert titles[chat_id] == "Отложенный заголовок"


@respx.mock
def test_second_turn_sends_no_further_title_request() -> None:
    """After the title landed, another turn in the chat makes no title request."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_title_side_effect(
            [_plain_content_response("Первый"), _plain_content_response("Второй")],
            _title_json("Настройка WebSocket"),
        ),
    )
    with TestClient(app) as client:
        user_id = login_test_client(client)
        queue = client.portal.call(_subscribe, user_id)
        chat_id = _new_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "Один")
            client.portal.call(_next_frame, queue)
            _send_and_drain(ws, "Два")
        titles = _chat_titles(client)
        extra_frames_empty = client.portal.call(_queue_empty, queue)

    assert len(_title_bodies(route)) == 1
    assert titles[chat_id] == "Настройка WebSocket"
    assert extra_frames_empty is True
