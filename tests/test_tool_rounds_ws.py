"""WebSocket tests for the bounded multi-round tool-call loop."""

import json
import time
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
    _get_message,
    _list_messages,
    _plain_content_response,
    _send_and_drain,
    _tool_calls_response,
)
from tests.test_tool_guard_ws import _stream_bodies


def _stream_queue(responses: list[httpx.Response]):
    """Serve queued responses to streaming requests; answer the facts call with an empty JSON."""
    queue = list(responses)

    def _side_effect(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream") is not True:
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        return queue.pop(0)

    return _side_effect


def _memory_call(call_id: str, name: str, key: str) -> tuple[str, str, str]:
    """Build one (id, name, raw_args) tool call saving a distinct key."""
    return (call_id, name, json.dumps({"key": key, "content": f"value-{key}"}))


def _drain_until_terminal(ws: Any, content: str) -> list[dict[str, Any]]:
    """Send one message and collect frames up to the first done or error frame."""
    ws.send_json({"content": content, "model": MODEL})
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") in ("done", "error"):
            return frames


def _wait_for_user_message_removal(client: TestClient, chat_id: int) -> list[Any]:
    """Poll until the failed turn's cleanup removed the user message (the error frame precedes it)."""
    messages: list[Any] = []
    for _ in range(50):
        messages = client.portal.call(_list_messages, chat_id)
        if not [m for m in messages if m.role == "user"]:
            break
        time.sleep(0.1)
    return messages


def _tool_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the tool_call frames in order."""
    return [f for f in frames if f.get("type") == "tool_call"]


@respx.mock
def test_two_round_turn_runs_both_tools_and_replays_in_order() -> None:
    """Tool A then tool B then text runs both; the next turn replays them in order."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
                _tool_calls_response([_memory_call("c2", "save_long_term_memory", "k2")]),
                _plain_content_response("Готово."),
                _plain_content_response("Ещё раз."),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Rounds"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "save two things")
            message = client.portal.call(_get_message, frames[-1]["message_id"])
            second_frames = _send_and_drain(ws, "and again")

    bodies = _stream_bodies(route)
    assert len(bodies) == 4
    assert "tools" in bodies[1] and "tools" in bodies[2]
    assert [f["name"] for f in _tool_frames(frames)] == [
        "save_working_memory", "save_long_term_memory",
    ]
    assert len(frames[-1]["memory_writes"]) == 2
    assert [e["name"] for e in json.loads(message.tool_trace)] == [
        "save_working_memory", "save_long_term_memory",
    ]
    assert message.content.endswith("Готово.")

    round_three = [m for m in bodies[2]["messages"] if m["role"] in ("assistant", "tool")]
    assert [m["role"] for m in round_three] == ["assistant", "tool", "assistant", "tool"]
    assert round_three[0]["tool_calls"][0]["id"] == "c1"
    assert round_three[2]["tool_calls"][0]["id"] == "c2"

    replay = [m for m in bodies[3]["messages"] if m["role"] in ("assistant", "tool")]
    assert [m["role"] for m in replay] == ["assistant", "tool", "tool", "assistant"]
    assert [c["function"]["name"] for c in replay[0]["tool_calls"]] == [
        "save_working_memory", "save_long_term_memory",
    ]
    assert replay[3]["content"] == message.content
    assert second_frames[-1]["type"] == "done"


@respx.mock
def test_round_cap_ends_with_one_toolless_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    """After MAX_TOOL_ROUNDS rounds one tools-less follow-up produces the text answer."""
    monkeypatch.setattr("agent.ws.MAX_TOOL_ROUNDS", 3)
    responses = [
        _tool_calls_response([_memory_call(f"c{i}", "save_working_memory", f"k{i}")])
        for i in range(1, 4)
    ]
    responses.append(_plain_content_response("Финал."))
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(responses),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Cap"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "many steps")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 4
    assert all("tools" in body for body in bodies[:3])
    assert "tools" not in bodies[3]
    assert len(_tool_frames(frames)) == 3
    assert frames[-1]["type"] == "done"
    assert len(frames[-1]["memory_writes"]) == 3
    assert message.content.endswith("Финал.")


@respx.mock
def test_repeated_identical_call_is_not_dispatched_again() -> None:
    """A call identical to the previous round's ends the turn with a tools-less follow-up."""
    call = ("c1", "save_working_memory", json.dumps({"key": "k", "content": "v"}))
    repeat = ("c2", "save_working_memory", json.dumps({"content": "v", "key": "k"}))
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([call]),
                _tool_calls_response([repeat]),
                _plain_content_response("Stop."),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Loop"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "loop please")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert "tools" not in bodies[2]
    assert len(_tool_frames(frames)) == 1
    assert frames[-1]["type"] == "done"
    assert len(json.loads(message.tool_trace)) == 1
    assert "Stop." in message.content


@respx.mock
def test_round_two_llm_failure_deletes_user_message() -> None:
    """A failing second-round follow-up sends LLM_ERROR, no done frame and drops the user message."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
                _tool_calls_response([_memory_call("c2", "save_working_memory", "k2")]),
                httpx.Response(500, json={"error": "boom"}),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Fail"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _drain_until_terminal(ws, "two then fail")
            messages = _wait_for_user_message_removal(client, chat_id)

    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "LLM_ERROR"
    assert not [f for f in frames if f.get("type") == "done"]
    assert len(_tool_frames(frames)) == 2
    assert not [m for m in messages if m.role in ("user", "assistant")]


@respx.mock
def test_tool_errors_are_aggregated_after_all_rounds() -> None:
    """TOOL_ERROR frames of every round arrive after all tool_call frames and before done."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([("c1", "save_working_memory", "{bad")]),
                _tool_calls_response([("c2", "save_working_memory", "{bad2")]),
                _plain_content_response("Не вышло."),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Errs"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "break twice")

    types = [f.get("type") for f in frames]
    error_idx = [i for i, f in enumerate(frames) if f.get("code") == "TOOL_ERROR"]
    tool_idx = [i for i, t in enumerate(types) if t == "tool_call"]
    assert len(error_idx) == 2
    assert len(tool_idx) == 2
    assert min(error_idx) > max(tool_idx)
    assert max(error_idx) < types.index("done")


def _token_text(frames: list[dict[str, Any]]) -> str:
    """Concatenate the content of all token frames."""
    return "".join(f.get("content", "") for f in frames if f.get("type") == "token")


@respx.mock
def test_empty_followup_with_tools_retries_once_without_tools() -> None:
    """An empty tools-enabled follow-up is retried once without tools and that text is the answer."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
                _plain_content_response(""),
                _plain_content_response("Содержимое файла."),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "EmptyRetry"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "read the file")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert "tools" in bodies[1]
    assert "tools" not in bodies[2]
    assert frames[-1]["type"] == "done"
    assert "Содержимое файла." in _token_text(frames)
    assert message.content == "Содержимое файла."
    assert len(json.loads(message.tool_trace)) == 1


@respx.mock
def test_empty_toolless_retry_ends_turn_without_error() -> None:
    """When the tools-less retry is empty too the turn ends normally with no further requests."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
                _plain_content_response(""),
                _plain_content_response(""),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "EmptyTwice"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "read the file")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    assert "tools" not in bodies[2]
    assert frames[-1]["type"] == "done"
    assert not [f for f in frames if f.get("type") == "error"]
    assert message.content == ""
    assert message.tool_trace is not None
    assert len(json.loads(message.tool_trace)) == 1


@respx.mock
def test_text_followup_does_not_trigger_empty_retry() -> None:
    """A follow-up that returns text is used as is, without an extra request."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
                _plain_content_response("Готово."),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "NoRetry"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "save it")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 2
    assert "tools" in bodies[1]
    assert frames[-1]["type"] == "done"
    assert message.content == "Готово."


@respx.mock
def test_empty_followup_in_round_two_retries_and_keeps_trace() -> None:
    """An empty follow-up after round two is retried once and both rounds stay in the trace."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_memory_call("c1", "save_working_memory", "k1")]),
                _tool_calls_response([_memory_call("c2", "save_long_term_memory", "k2")]),
                _plain_content_response(""),
                _plain_content_response("Итог."),
            ],
        ),
    )
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "EmptyR2"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "save two things")
        message = client.portal.call(_get_message, frames[-1]["message_id"])

    bodies = _stream_bodies(route)
    assert len(bodies) == 4
    assert "tools" in bodies[1] and "tools" in bodies[2]
    assert "tools" not in bodies[3]
    assert [f["name"] for f in _tool_frames(frames)] == [
        "save_working_memory", "save_long_term_memory",
    ]
    assert len(frames[-1]["memory_writes"]) == 2
    assert [e["name"] for e in json.loads(message.tool_trace)] == [
        "save_working_memory", "save_long_term_memory",
    ]
    assert message.content == "Итог."
