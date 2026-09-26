"""End-to-end WebSocket turns where a mocked LLM calls tools of a real MCP fixture server."""

import json
import sys
from pathlib import Path

import pytest
import respx
from starlette.testclient import TestClient

from agent.main import app
from agent.tool_guard import TOOL_ERROR_REMINDER
from agent.tools import TOOL_REGISTRY
from shared.config import settings
from tests.conftest import login_test_client
from tests.test_memory_ws import (
    BASE_URL,
    WS_ORIGIN,
    _plain_content_response,
    _queue_responses,
    _send_and_drain,
    _tool_calls_response,
)

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
COMPLETIONS_URL = f"{BASE_URL}/v1/chat/completions"


def _setup(client: TestClient, connect: bool = True) -> int:
    """Log in, register the fixture MCP server, optionally connect it, and create a chat."""
    login_test_client(client)
    created = client.post(
        "/api/v1/mcp/servers",
        json={"name": "Fixture", "command": sys.executable, "args": [FIXTURE, "tools_extra"]},
    )
    assert created.status_code == 201, created.text
    if connect:
        resp = client.post(f"/api/v1/mcp/servers/{created.json()['id']}/connect")
        assert resp.status_code == 200
        assert resp.json()["connection"]["status"] == "connected"
    return client.post("/api/v1/chats", json={"title": "MCP chat"}).json()["id"]


def _request_body(route: respx.Route, index: int) -> dict:
    return json.loads(route.calls[index].request.content)


def _tool_names(body: dict) -> list[str]:
    return [tool["function"]["name"] for tool in body.get("tools", [])]


def _run_turn(client: TestClient, chat_id: int, text: str = "use the tool") -> list[dict]:
    with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
        return _send_and_drain(ws, text)


@respx.mock
def test_mcp_tool_call_turn_returns_result_to_model() -> None:
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [("call_mcp_1", "mcp__fixture__echo", json.dumps({"text": "ping-42"}))],
                ),
                _plain_content_response("The tool said ping-42."),
            ],
        ),
    )

    with TestClient(app) as client:
        chat_id = _setup(client)
        frames = _run_turn(client, chat_id)

    tool_frames = [f for f in frames if f["type"] == "tool_call"]
    assert len(tool_frames) == 1
    frame = tool_frames[0]
    assert frame["ok"] is True
    assert frame["tool"] == "echo"
    assert frame["server"] == "Fixture"
    assert frame["name"] == "mcp__fixture__echo"
    assert "ping-42" in frame["result"]
    assert [f for f in frames if f["type"] == "done"] == [frames[-1]]
    assert not [f for f in frames if f["type"] == "error"]

    first_tools = _tool_names(_request_body(route, 0))
    assert "mcp__fixture__echo" in first_tools
    assert set(TOOL_REGISTRY) <= set(first_tools)

    follow_up = _request_body(route, 1)
    tool_messages = [m for m in follow_up["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_mcp_1"
    assert "ping-42" in tool_messages[0]["content"]


@respx.mock
def test_empty_arguments_are_echoed_as_empty_object() -> None:
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response([("call_noargs", "mcp__fixture__noargs", "")]),
                _plain_content_response("Done with noargs."),
            ],
        ),
    )

    with TestClient(app) as client:
        chat_id = _setup(client)
        frames = _run_turn(client, chat_id)

    tool_frames = [f for f in frames if f["type"] == "tool_call"]
    assert len(tool_frames) == 1 and tool_frames[0]["ok"] is True
    assert [f["type"] for f in frames].count("done") == 1

    follow_up = _request_body(route, 1)
    assistant = next(m for m in follow_up["messages"] if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["arguments"] == "{}"
    tool_message = next(m for m in follow_up["messages"] if m["role"] == "tool")
    assert "noargs-ok" in tool_message["content"]


@respx.mock
def test_mcp_tool_failure_is_a_tool_result_not_an_error_frame() -> None:
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response([("call_fail", "mcp__fixture__fail", "{}")]),
                _plain_content_response("The tool failed."),
                _plain_content_response("Tried another way."),
            ],
        ),
    )

    with TestClient(app) as client:
        chat_id = _setup(client)
        frames = _run_turn(client, chat_id)

    tool_frames = [f for f in frames if f["type"] == "tool_call"]
    assert len(tool_frames) == 1 and tool_frames[0]["ok"] is False
    assert not [f for f in frames if f["type"] == "error"]
    assert [f["type"] for f in frames].count("done") == 1

    follow_up = _request_body(route, 1)
    tool_message = next(m for m in follow_up["messages"] if m["role"] == "tool")
    assert "fixture failure" in tool_message["content"]

    nudge = _request_body(route, 2)
    assert nudge["messages"][-1] == {"role": "user", "content": TOOL_ERROR_REMINDER}


@respx.mock
def test_unconnected_server_is_auto_connected_on_first_turn() -> None:
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [("call_auto", "mcp__fixture__echo", json.dumps({"text": "auto-7"}))],
                ),
                _plain_content_response("The tool said auto-7."),
            ],
        ),
    )

    with TestClient(app) as client:
        chat_id = _setup(client, connect=False)
        frames = _run_turn(client, chat_id)
        listing = client.get("/api/v1/mcp/servers")

    tool_frames = [f for f in frames if f["type"] == "tool_call"]
    assert len(tool_frames) == 1
    assert tool_frames[0]["ok"] is True
    assert "auto-7" in tool_frames[0]["result"]
    assert "mcp__fixture__echo" in _tool_names(_request_body(route, 0))
    assert [f["type"] for f in frames].count("done") == 1
    assert not [f for f in frames if f["type"] == "error"]

    assert listing.status_code == 200
    (server,) = listing.json()
    assert server["connection"]["status"] == "connected"


@respx.mock
def test_disconnected_server_adds_no_tools_and_unknown_call_is_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MCP_AUTO_CONNECT", False)
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [("call_x", "mcp__fixture__echo", json.dumps({"text": "hi"}))],
                ),
                _plain_content_response("No such tool."),
            ],
        ),
    )

    with TestClient(app) as client:
        chat_id = _setup(client, connect=False)
        frames = _run_turn(client, chat_id)

    assert sorted(_tool_names(_request_body(route, 0))) == sorted(TOOL_REGISTRY)

    tool_frames = [f for f in frames if f["type"] == "tool_call"]
    assert len(tool_frames) == 1 and tool_frames[0]["ok"] is False
    assert "unknown tool" in tool_frames[0]["result"]
    assert [f["type"] for f in frames].count("done") == 1
