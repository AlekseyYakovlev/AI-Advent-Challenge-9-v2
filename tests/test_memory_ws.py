"""End-to-end WebSocket tests: a mocked tool-calling LLM drives a real memory turn."""

import json

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import LongTermMemory, Message, WorkingMemory
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"


def _sse_body(lines: list[str]) -> bytes:
    """Join raw SSE `data: ...` lines into a byte body."""
    return ("\n".join(lines) + "\n").encode()


def _plain_content_response(text: str) -> httpx.Response:
    """Build a plain-content-only SSE response (no tool calls), one word-plus-space per chunk."""
    words = text.split(" ")
    chunks = [word + (" " if i < len(words) - 1 else "") for i, word in enumerate(words)]
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": chunk}, "finish_reason": None}]})}'
        for chunk in chunks
    ]
    lines.append('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}')
    lines.append("data: [DONE]")
    return httpx.Response(200, content=_sse_body(lines))


def _tool_calls_response(calls: list[tuple[str, str, str]]) -> httpx.Response:
    """Build an SSE response that ends in finish_reason=tool_calls.

    `calls` is a list of (tool_call_id, function_name, raw_arguments_json_string).
    Each raw argument string is embedded as-is (may be malformed JSON).
    """
    tool_calls_delta = [
        {
            "index": i,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": raw_args},
        }
        for i, (call_id, name, raw_args) in enumerate(calls)
    ]
    chunk1 = {"choices": [{"delta": {"tool_calls": tool_calls_delta}, "finish_reason": None}]}
    chunk2 = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    lines = [f"data: {json.dumps(chunk1)}", f"data: {json.dumps(chunk2)}", "data: [DONE]"]
    return httpx.Response(200, content=_sse_body(lines))


def _queue_responses(responses: list[httpx.Response]):
    """Return a respx side_effect callable serving responses in order, one per request."""
    queue = list(responses)

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        return queue.pop(0)

    return _side_effect


def _send_and_drain(ws, content: str) -> list[dict]:
    """Send one chat message and collect every frame up to and including `done`."""
    ws.send_json({"content": content, "model": MODEL})
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done":
            break
    return frames


@respx.mock
def test_tool_call_turn_writes_memory_and_reports_it() -> None:
    """A single save_long_term_memory tool call writes a row and reports it on `done`."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
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

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Memory turn"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "remember my name is Alex")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert len(done_frame["memory_writes"]) == 1
        write = done_frame["memory_writes"][0]
        assert write["layer"] == "long_term"
        assert write["key"] == "user_name"

        assistant_msg = client.portal.call(_get_message, done_frame["message_id"])
        assert "Got it, I saved your name." in assistant_msg.content

        rows = client.portal.call(_list_long_term_rows)
        assert len(rows) == 1
        assert rows[0].key == "user_name"
        assert rows[0].value == "Alex"
        assert route.call_count == 2


@respx.mock
def test_turn_without_tool_calls_writes_no_memory() -> None:
    """A plain-content-only reply must never write memory (MEM-03)."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=_plain_content_response("Just a normal reply."),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "No tools"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hello there")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert done_frame["memory_writes"] == []

        working_rows = client.portal.call(_list_working_rows, chat_id)
        long_term_rows = client.portal.call(_list_long_term_rows)
        assert working_rows == []
        assert long_term_rows == []


@respx.mock
def test_two_tool_calls_in_one_turn_both_persist() -> None:
    """Two tool calls in one turn both persist, in different tables, in order."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [
                        (
                            "call_lt",
                            "save_long_term_memory",
                            json.dumps({"key": "user_name", "content": "Alex"}),
                        ),
                        (
                            "call_wk",
                            "save_working_memory",
                            json.dumps(
                                {"key": "current_task_step", "content": "drafting intro"},
                            ),
                        ),
                    ],
                ),
                _plain_content_response("Saved both."),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Two tool calls"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "remember my name and note the task step")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        writes = done_frame["memory_writes"]
        assert len(writes) == 2
        assert writes[0]["layer"] == "long_term"
        assert writes[0]["key"] == "user_name"
        assert writes[1]["layer"] == "working"
        assert writes[1]["key"] == "current_task_step"

        long_term_rows = client.portal.call(_list_long_term_rows)
        working_rows = client.portal.call(_list_working_rows, chat_id)
        assert len(long_term_rows) == 1
        assert len(working_rows) == 1


@respx.mock
def test_malformed_tool_arguments_do_not_kill_the_turn() -> None:
    """A malformed tool call yields TOOL_ERROR but the turn still completes."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [("call_bad", "save_working_memory", "{not valid json")],
                ),
                _plain_content_response("Sorry, something went wrong, but here is my answer."),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Malformed args"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "note something for me")

        error_frames = [f for f in frames if f.get("type") == "error"]
        assert any(f.get("code") == "TOOL_ERROR" for f in error_frames)
        done_frames = [f for f in frames if f.get("type") == "done"]
        assert len(done_frames) == 1

        messages = client.portal.call(_list_messages, chat_id)
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) == 1
        assert user_messages[0].content == "note something for me"


@respx.mock
def test_saved_memory_appears_in_next_turn_system_prompt() -> None:
    """A saved key/value is present in the very next turn's outbound system message."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [
                        (
                            "call_1",
                            "save_long_term_memory",
                            json.dumps({"key": "favorite_color", "content": "Blue"}),
                        ),
                    ],
                ),
                _plain_content_response("Noted, your favorite color is blue."),
                _plain_content_response("Yes, I remember."),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Recall"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "my favorite color is blue")

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "what is my favorite color?")

    assert route.call_count == 3
    last_request_body = json.loads(route.calls[-1].request.content)
    system_message = next(
        msg["content"] for msg in last_request_body["messages"] if msg["role"] == "system"
    )
    assert "favorite_color" in system_message
    assert "Blue" in system_message


@respx.mock
def test_memory_write_is_scoped_to_the_connected_user() -> None:
    """A second user's chat receives none of the first user's memory."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
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
                _plain_content_response("Saved."),
                _plain_content_response("Hi there."),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client, "wsuser", "wspass")
        chat_a = client.post("/api/v1/chats", json={"title": "User A chat"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_a}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "remember my name is Alex")

        login_test_client(client, "otheruser", "otherpass")
        chat_b = client.post("/api/v1/chats", json={"title": "User B chat"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_b}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "hello")

    last_request_body = json.loads(route.calls[-1].request.content)
    system_message = next(
        msg["content"] for msg in last_request_body["messages"] if msg["role"] == "system"
    )
    assert "user_name" not in system_message
    assert "Alex" not in system_message


async def _get_message(message_id: int) -> Message:
    """Fetch a Message row by id."""
    async with async_session_factory() as session:
        return await session.get(Message, message_id)


async def _list_messages(chat_id: int) -> list[Message]:
    """Fetch all Message rows for a chat."""
    async with async_session_factory() as session:
        result = await session.exec(select(Message).where(Message.chat_id == chat_id))
        return list(result.all())


async def _list_working_rows(chat_id: int) -> list[WorkingMemory]:
    """Fetch all WorkingMemory rows for a chat."""
    async with async_session_factory() as session:
        result = await session.exec(
            select(WorkingMemory).where(WorkingMemory.chat_id == chat_id),
        )
        return list(result.all())


async def _list_long_term_rows() -> list[LongTermMemory]:
    """Fetch every LongTermMemory row in the test database."""
    async with async_session_factory() as session:
        result = await session.exec(select(LongTermMemory))
        return list(result.all())
