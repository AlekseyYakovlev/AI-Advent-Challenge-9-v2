"""WebSocket tests for multi-step tool turns: text-call recovery, nudges, fallback, system message."""

import json
from typing import Any

import httpx
import respx
from starlette.testclient import TestClient

from agent.main import app
from agent.tool_guard import (
    ACTION_ANNOUNCE_REMINDER,
    MULTI_STEP_TOOL_HINT,
    TOOL_ERROR_REMINDER,
    TOOL_USE_RULE,
)
from tests.conftest import login_test_client
from tests.test_mcp_chat_ws import _setup as _setup_mcp
from tests.test_mcp_chat_ws import _run_turn as _mcp_turn
from tests.test_memory_ws import (
    BASE_URL,
    WS_ORIGIN,
    _get_message,
    _plain_content_response,
    _send_and_drain,
    _sse_body,
    _tool_calls_response,
)
from tests.test_tool_guard_ws import _stream_bodies, _system_content
from tests.test_tool_rounds_ws import _memory_call, _stream_queue, _token_text, _tool_frames

HERMES_CALL = (
    '<tool_call>{"name": "save_working_memory", '
    '"arguments": {"key": "k1", "content": "v"}}</tool_call>'
)
QWEN_ALIAS_CALL = (
    "<tool_call><function=save-working-memory>"
    "<parameter=key>k2</parameter><parameter=content>v2</parameter>"
    "</function></tool_call>"
)


def _chunked_response(text: str, size: int = 4) -> httpx.Response:
    """Build a plain-content SSE response that splits the text into tiny chunks."""
    chunks = [text[i:i + size] for i in range(0, len(text), size)]
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": chunk}, "finish_reason": None}]})}'
        for chunk in chunks
    ]
    lines.append('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}')
    lines.append("data: [DONE]")
    return httpx.Response(200, content=_sse_body(lines))


def _run(responses: list[httpx.Response], text: str = "save it") -> tuple[
    respx.Route, list[dict[str, Any]], Any,
]:
    """Run one WS turn against queued responses; return route, frames and the saved message."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(responses),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Multi"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, text)
        message = client.portal.call(_get_message, frames[-1]["message_id"])
    return route, frames, message


def _last_user_content(body: dict[str, Any]) -> str:
    """Return the content of the last message of a request body."""
    return body["messages"][-1]["content"]


@respx.mock
def test_hermes_text_call_is_dispatched_and_hidden() -> None:
    """A JSON tool call written as text runs as a real call and never reaches the user."""
    route, frames, message = _run(
        [_chunked_response("Записываю.\n" + HERMES_CALL), _plain_content_response("Готово.")],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 2
    tool_frames = _tool_frames(frames)
    assert len(tool_frames) == 1 and tool_frames[0]["ok"] is True
    assert tool_frames[0]["name"] == "save_working_memory"
    assert len(frames[-1]["memory_writes"]) == 1
    for text in (_token_text(frames), message.content):
        assert "<tool_call" not in text and "<function=" not in text
    assert message.content.endswith("Готово.")
    assistant = next(m for m in bodies[1]["messages"] if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["id"].startswith("call_text_")


@respx.mock
def test_qwen_xml_alias_call_in_followup_is_dispatched_as_round_two() -> None:
    """A qwen-XML call with an alias name in a follow-up is dispatched as the next round."""
    route, frames, message = _run(
        [
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _chunked_response(QWEN_ALIAS_CALL),
            _plain_content_response("Готово."),
        ],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert [f["name"] for f in _tool_frames(frames)] == [
        "save_working_memory", "save_working_memory",
    ]
    assert all(f["ok"] for f in _tool_frames(frames))
    assert "<function=" not in message.content and "<tool_call" not in message.content
    assert message.content.endswith("Готово.")


@respx.mock
def test_announcement_before_first_round_gets_one_nudge() -> None:
    """An announced step with no tool call is re-prompted once and its call is dispatched."""
    route, frames, message = _run(
        [
            _plain_content_response("Сначала создам файл."),
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _plain_content_response("Готово."),
        ],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert _last_user_content(bodies[1]) == ACTION_ANNOUNCE_REMINDER
    assert "tools" in bodies[1]
    assert len(_tool_frames(frames)) == 1
    assert message.content.startswith("Сначала создам файл.")
    assert message.content.endswith("Готово.")


@respx.mock
def test_announcement_before_first_round_is_nudged_only_once() -> None:
    """A second announcement in the nudge answer does not trigger another nudge."""
    route, frames, message = _run(
        [
            _plain_content_response("Сначала создам файл."),
            _plain_content_response("Затем создам второй."),
        ],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 2
    assert not _tool_frames(frames)
    assert message.content == "Сначала создам файл.\n\nЗатем создам второй."


@respx.mock
def test_announcement_between_rounds_gets_one_nudge_with_tools() -> None:
    """After a round the model announces the next step; one nudge makes it call the tool."""
    route, frames, message = _run(
        [
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _plain_content_response("Теперь создам второй файл."),
            _tool_calls_response([_memory_call("c2", "save_long_term_memory", "k2")]),
            _plain_content_response("Итог."),
        ],
    )

    bodies = _stream_bodies(route)
    assert len(bodies) == 4
    assert _last_user_content(bodies[2]) == ACTION_ANNOUNCE_REMINDER
    assert "tools" in bodies[2]
    assert [f["name"] for f in _tool_frames(frames)] == [
        "save_working_memory", "save_long_term_memory",
    ]
    assert "Теперь создам второй файл." in message.content
    assert message.content.endswith("Итог.")


@respx.mock
def test_announcement_between_rounds_is_nudged_only_once() -> None:
    """A repeated announcement after the nudge ends the turn instead of nudging again."""
    route, frames, message = _run(
        [
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _plain_content_response("Теперь создам второй файл."),
            _plain_content_response("Затем создам третий."),
        ],
    )

    assert len(_stream_bodies(route)) == 3
    assert len(_tool_frames(frames)) == 1
    assert frames[-1]["type"] == "done"
    assert message.content == "Теперь создам второй файл.\n\nЗатем создам третий."


@respx.mock
def test_mcp_failure_gets_one_error_nudge() -> None:
    """After a failed MCP call and an apology, one nudge asks the model to try another way."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([("call_fail", "mcp__fixture__fail", "{}")]),
                _plain_content_response("The tool failed."),
                _plain_content_response("Tried another way."),
            ],
        ),
    )

    with TestClient(app) as client:
        chat_id = _setup_mcp(client)
        frames = _mcp_turn(client, chat_id)
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert _last_user_content(bodies[2]) == TOOL_ERROR_REMINDER
    assert "tools" in bodies[2]
    assert len(_tool_frames(frames)) == 1
    assert message.content == "The tool failed.\n\nTried another way."


@respx.mock
def test_empty_replies_after_tools_fall_back_to_russian_summary() -> None:
    """An empty follow-up and empty retry are replaced by a summary of the tool results."""
    route, frames, message = _run(
        [
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _plain_content_response(""),
            _plain_content_response(""),
        ],
        text="сохрани это",
    )

    assert len(_stream_bodies(route)) == 3
    assert frames[-1]["type"] == "done"
    assert message.content.startswith("Инструменты выполнены, но модель не дала ответа.")
    assert "- save_working_memory: OK" in message.content
    assert _token_text(frames) == message.content


@respx.mock
def test_empty_replies_after_tools_fall_back_to_english_summary() -> None:
    """A non-Cyrillic user message gets the English fallback header."""
    _, frames, message = _run(
        [
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _plain_content_response(""),
            _plain_content_response(""),
        ],
        text="save this",
    )

    assert frames[-1]["type"] == "done"
    assert message.content.startswith("Tools ran but the model gave no answer.")


@respx.mock
def test_system_message_has_clock_hint_and_removable_rule() -> None:
    """The clock and hint stay for the whole turn; only the rule is stripped after round one."""
    route, _, _ = _run(
        [
            _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
            _plain_content_response("Готово."),
        ],
    )

    bodies = _stream_bodies(route)
    first = _system_content(bodies[0])
    assert "Current local date and time:" in first
    assert MULTI_STEP_TOOL_HINT in first
    assert first.endswith("\n\n" + TOOL_USE_RULE)
    second = _system_content(bodies[1])
    assert TOOL_USE_RULE not in second
    assert "Current local date and time:" in second
    assert MULTI_STEP_TOOL_HINT in second
    assert second + "\n\n" + TOOL_USE_RULE == first
