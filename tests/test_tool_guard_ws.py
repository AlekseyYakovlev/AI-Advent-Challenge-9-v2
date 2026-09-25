"""WebSocket tests for the tool-use rule and the one-shot claimed-action guard."""

import json
from typing import Any

import httpx
import respx
from starlette.testclient import TestClient

from agent import context_engine
from agent.main import app
from agent.schemas import MessagePayload
from agent.tool_guard import ACTION_CLAIM_REMINDER, TOOL_TRACE_HEADER, TOOL_USE_RULE
from agent.ws import _handle_chat_message
from shared.database import async_session_factory
from shared.models import Chat
from tests.conftest import login_test_client
from tests.test_memory_ws import (
    BASE_URL,
    MODEL,
    WS_ORIGIN,
    _get_message,
    _plain_content_response,
    _queue_responses,
    _send_and_drain,
    _tool_calls_response,
)

CLAIM = "Файл 1.txt создан."
REAL_REPLY = "Файл `1.txt` успешно скопирован."
LEAKY_REPLY = (
    REAL_REPLY
    + "\n\n"
    + TOOL_TRACE_HEADER
    + "\n- mcp__filesystem_2_copy_file({}) -> ok: Successfully copied"
)
SAVE_CALL = ("call_1", "save_working_memory", json.dumps({"key": "k", "content": "v"}))


def _stream_bodies(route: respx.Route) -> list[dict[str, Any]]:
    """Return JSON bodies of streaming chat requests only (ignores facts extraction)."""
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [body for body in bodies if body.get("stream") is True]


def _system_content(body: dict[str, Any]) -> str:
    """Return the system message text of a request body."""
    return next(m["content"] for m in body["messages"] if m["role"] == "system")


def _run_turn(responses: list[httpx.Response], content: str = "create 1.txt") -> tuple[
    respx.Route, list[dict[str, Any]], Any,
]:
    """Run one WS turn against queued mock responses; return route, frames, persisted message."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(responses),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Guard"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, content)
        message = client.portal.call(_get_message, frames[-1]["message_id"])
    return route, frames, message


@respx.mock
def test_claim_without_tool_triggers_single_retry_with_reminder() -> None:
    """A claim with no tool call gets exactly one retry carrying the reminder."""
    route, frames, message = _run_turn(
        [_plain_content_response(CLAIM), _plain_content_response("Sorry, I did not do that.")],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 2
    retry = bodies[1]
    assert _system_content(bodies[0]).endswith(TOOL_USE_RULE)
    assert _system_content(retry).endswith(TOOL_USE_RULE)
    assert "tools" in retry
    assert retry["messages"][-2]["role"] == "assistant"
    assert retry["messages"][-2]["content"] == CLAIM
    assert retry["messages"][-1] == {"role": "user", "content": ACTION_CLAIM_REMINDER}
    streamed = "".join(f["content"] for f in frames if f.get("type") == "token")
    assert "Sorry, I did not do that." in streamed
    assert frames[-1]["type"] == "done"
    assert message.content.startswith(CLAIM)
    assert "Sorry, I did not do that." in message.content


@respx.mock
def test_retry_tool_call_is_dispatched() -> None:
    """A tool call produced by the retry runs through the normal dispatch path."""
    route, frames, _ = _run_turn(
        [
            _plain_content_response(CLAIM),
            _tool_calls_response([SAVE_CALL]),
            _plain_content_response("Now it is really saved."),
        ],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert _system_content(bodies[0]).endswith(TOOL_USE_RULE)
    assert _system_content(bodies[1]).endswith(TOOL_USE_RULE)
    assert TOOL_USE_RULE not in _system_content(bodies[2])
    assert [f for f in frames if f.get("type") == "tool_call"]
    assert frames[-1]["type"] == "done"
    assert len(frames[-1]["memory_writes"]) == 1


@respx.mock
def test_retry_claim_is_not_retried_again() -> None:
    """A second claim after the retry never triggers another request."""
    route, frames, _ = _run_turn(
        [_plain_content_response(CLAIM), _plain_content_response("Каталог создан.")],
    )

    assert len(_stream_bodies(route)) == 2
    assert frames[-1]["type"] == "done"


@respx.mock
def test_failed_retry_is_non_fatal() -> None:
    """A retry HTTP failure keeps the original text and still sends done."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=[
            _plain_content_response(CLAIM),
            httpx.Response(500, json={"error": "boom"}),
        ],
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Fail"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "create 1.txt")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    assert len(_stream_bodies(route)) == 2
    assert not [f for f in frames if f.get("type") == "error"]
    assert message.content == CLAIM


@respx.mock
def test_plain_reply_sends_one_request_and_rule_is_in_system_prompt() -> None:
    """A plain chat reply is never retried; the rule is appended when tools are offered."""
    route, frames, _ = _run_turn(
        [_plain_content_response("Привет! Чем могу помочь?")], content="hi",
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 1
    assert _system_content(bodies[0]).endswith(TOOL_USE_RULE)
    assert ACTION_CLAIM_REMINDER not in json.dumps(bodies[0])
    assert frames[-1]["type"] == "done"


@respx.mock
def test_tool_call_turn_never_gets_reminder() -> None:
    """When the first response is a tool call the guard stays silent."""
    route, _, _ = _run_turn(
        [_tool_calls_response([SAVE_CALL]), _plain_content_response("Файл создан.")],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 2
    assert "tools" in bodies[1]
    assert all(ACTION_CLAIM_REMINDER not in json.dumps(body) for body in bodies)


@respx.mock
def test_leaked_trace_block_is_not_streamed_or_persisted() -> None:
    """A model-imitated trace block in the follow-up reply never reaches client or DB."""
    route, frames, message = _run_turn(
        [_tool_calls_response([SAVE_CALL]), _plain_content_response(LEAKY_REPLY)],
        content="copy 1.txt",
    )

    assert len(_stream_bodies(route)) == 2
    tokens = [f["content"] for f in frames if f.get("type") == "token"]
    assert "".join(tokens) == REAL_REPLY
    assert all(TOOL_TRACE_HEADER not in t and "mcp__filesystem_2" not in t for t in tokens)
    assert message.content == REAL_REPLY
    assert TOOL_TRACE_HEADER not in message.content
    assert "mcp__filesystem_2_copy_file" not in message.content
    assert frames[-1]["type"] == "done"


class _FakeWebSocket:
    """Minimal websocket collecting outbound frames."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        """Record one outbound frame."""
        self.frames.append(data)


@respx.mock
async def test_no_tools_offered_sends_one_plain_request() -> None:
    """Unowned chats offer no tools: no rule, no guard, a single request."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=_plain_content_response("Файл создан."),
    )
    async with async_session_factory() as session:
        chat = Chat(title="Unowned", user_id=None)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    websocket = _FakeWebSocket()
    try:
        await _handle_chat_message(
            websocket, chat_id, MessagePayload(content="create it", model=MODEL),
        )
    finally:
        for task in list(context_engine._debounce_tasks.values()):
            task.cancel()

    bodies = _stream_bodies(route)
    assert len(bodies) == 1
    assert "tools" not in bodies[0]
    assert TOOL_USE_RULE not in _system_content(bodies[0])
    assert websocket.frames[-1]["type"] == "done"
