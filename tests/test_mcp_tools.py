"""Tests for MCP tool exposure: naming, schema building, dispatch and failure mapping."""

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

from agent import mcp_client, mcp_config
from agent.mcp_tools import (
    MCP_TOOL_PREFIX,
    McpToolBinding,
    build_mcp_toolset,
    build_toolset_from_servers,
    call_mcp_tool,
    server_slug,
)
from agent.schemas import McpConnectionStatus, McpToolInfo
from agent.tools import TOOL_REGISTRY, build_tool_schemas, dispatch_tool_calls
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat
from tests.conftest import _create_user

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
RESERVED = set(TOOL_REGISTRY)


def _tool(name: str, description: str = "d", schema: dict | None = None) -> McpToolInfo:
    return McpToolInfo(
        name=name,
        description=description,
        input_schema=schema or {"type": "object", "properties": {}},
    )


def _call(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


async def _add_server(user_id: int, name: str = "Fixture", enabled: bool = True) -> int:
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, name, sys.executable, [FIXTURE, "tools_extra"], {}, None, enabled,
        )
        return row.id


async def _connect(user_id: int, server_id: int, mode: str = "tools_extra") -> None:
    result = await mcp_client.connect_server(
        user_id, server_id, sys.executable, [FIXTURE, mode], None, None,
    )
    assert result.status == McpConnectionStatus.CONNECTED


async def _toolset(user_id: int):
    async with async_session_factory() as session:
        return await build_mcp_toolset(session, user_id, RESERVED)


async def _dispatch(user_id: int, bindings: dict, calls: list[dict], chat_id: int = 1) -> list:
    async with async_session_factory() as session:
        return await dispatch_tool_calls(session, user_id, chat_id, calls, mcp_bindings=bindings)


async def _connected_fixture(username: str = "mcpuser") -> tuple[int, int, dict]:
    user_id = await _create_user(username, "pw")
    server_id = await _add_server(user_id)
    await _connect(user_id, server_id)
    toolset = await _toolset(user_id)
    return user_id, server_id, toolset.bindings


def test_server_slug_sanitizes_and_falls_back() -> None:
    assert server_slug("Filesystem", 3) == "filesystem"
    assert server_slug("!!!", 3) == "server3"
    assert server_slug("My Server / v2", 1) == "my_server___v2"
    assert len(server_slug("a" * 100, 1)) <= 20


def test_build_toolset_names_bindings_and_clean_schema() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "echoArguments",
        "type": "object",
        "properties": {"text": {"title": "Text", "type": "string"}},
        "required": ["text"],
    }
    toolset = build_toolset_from_servers(
        7, [(1, "Fixture", [_tool("echo", schema=schema), _tool("add")])], RESERVED,
    )

    assert set(toolset.bindings) == {"mcp__fixture__echo", "mcp__fixture__add"}
    binding = toolset.bindings["mcp__fixture__echo"]
    assert (binding.user_id, binding.server_id, binding.tool_name) == (7, 1, "echo")
    params = toolset.schemas[0]["function"]["parameters"]
    assert params["type"] == "object"
    assert "title" not in params and "$schema" not in params
    assert "title" not in params["properties"]["text"]
    assert params["required"] == ["text"]
    assert toolset.schemas[0]["function"]["description"].startswith("[MCP server: Fixture]")


def test_non_dict_input_schema_becomes_empty_object() -> None:
    tool = McpToolInfo(name="x", description=None, input_schema={})
    toolset = build_toolset_from_servers(1, [(1, "S", [tool])], RESERVED)
    assert toolset.schemas[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_duplicate_slugs_and_colliding_tool_names_get_distinct_names() -> None:
    toolset = build_toolset_from_servers(
        1,
        [
            (1, "Fixture", [_tool("echo"), _tool("a.b"), _tool("a_b")]),
            (2, "fixture", [_tool("echo")]),
        ],
        RESERVED,
    )

    names = list(toolset.bindings)
    assert len(names) == len(set(names)) == 4
    assert "mcp__fixture__echo" in names
    assert "mcp__fixture-2__echo" in names
    assert toolset.bindings["mcp__fixture-2__echo"].server_id == 2


def test_long_tool_name_is_hashed_within_limit() -> None:
    toolset = build_toolset_from_servers(1, [(1, "Fixture", [_tool("t" * 100)])], RESERVED)
    (name,) = toolset.bindings
    assert len(name) <= 64
    assert NAME_PATTERN.match(name)
    assert toolset.bindings[name].tool_name == "t" * 100


def test_exposed_names_never_shadow_builtins_and_match_pattern() -> None:
    tools = [_tool(name) for name in ("echo", "a b", "ü", "..", "x" * 90, "save_working_memory")]
    toolset = build_toolset_from_servers(1, [(1, "!!!", tools)], RESERVED)

    assert len(toolset.bindings) == len(tools)
    for name in toolset.bindings:
        assert NAME_PATTERN.match(name)
        assert name.startswith(MCP_TOOL_PREFIX)
        assert name not in RESERVED


async def test_toolset_empty_without_servers_or_connection() -> None:
    user_id = await _create_user("nosrv", "pw")
    assert (await _toolset(user_id)).schemas == []

    await _add_server(user_id)
    toolset = await _toolset(user_id)
    assert toolset.schemas == [] and toolset.bindings == {}


async def test_toolset_skips_disabled_connected_server() -> None:
    user_id = await _create_user("disabled", "pw")
    server_id = await _add_server(user_id, enabled=False)
    await _connect(user_id, server_id)

    assert (await _toolset(user_id)).schemas == []


async def test_toolset_exposes_connected_fixture_tools() -> None:
    user_id, _server_id, bindings = await _connected_fixture()
    assert {"mcp__fixture__echo", "mcp__fixture__add", "mcp__fixture__fail"} <= set(bindings)
    assert "mcp__fixture__noargs" in bindings


async def test_cross_user_isolation() -> None:
    user_a, server_a, bindings_a = await _connected_fixture("usera")
    user_b = await _create_user("userb", "pw")

    assert (await _toolset(user_b)).schemas == []

    results = await _dispatch(
        user_b, {}, [_call("c1", "mcp__fixture__echo", json.dumps({"text": "hi"}))],
    )
    assert results[0]["ok"] is False
    assert "unknown tool" in results[0]["content"]

    forged = McpToolBinding("mcp__fixture__echo", user_b, server_a, "Fixture", "echo")
    results = await _dispatch(
        user_b,
        {forged.exposed_name: forged},
        [_call("c2", forged.exposed_name, json.dumps({"text": "hi"}))],
    )
    assert results[0]["ok"] is False
    assert "not connected" in results[0]["content"]
    assert user_a != user_b and bindings_a


async def test_dispatch_echo_success() -> None:
    user_id, _sid, bindings = await _connected_fixture()
    results = await _dispatch(
        user_id, bindings, [_call("c1", "mcp__fixture__echo", json.dumps({"text": "hello"}))],
    )

    (result,) = results
    assert result["ok"] is True
    assert result["mcp"] == {"server_id": bindings["mcp__fixture__echo"].server_id,
                             "server_name": "Fixture", "tool": "echo"}
    body = json.loads(result["content"])
    assert body["server"] == "Fixture" and body["tool"] == "echo"
    assert body["is_error"] is False
    assert body["content"] == "hello"


async def test_dispatch_tool_is_error() -> None:
    user_id, _sid, bindings = await _connected_fixture()
    results = await _dispatch(user_id, bindings, [_call("c1", "mcp__fixture__fail", "{}")])

    assert results[0]["ok"] is False
    body = json.loads(results[0]["content"])
    assert body["is_error"] is True
    assert "fixture failure" in body["error"]


async def test_dispatch_invalid_argument_types_report_error() -> None:
    user_id, _sid, bindings = await _connected_fixture()
    results = await _dispatch(
        user_id, bindings, [_call("c1", "mcp__fixture__add", json.dumps({"a": "notanint"}))],
    )
    assert results[0]["ok"] is False


async def test_dispatch_non_object_arguments_rejected() -> None:
    user_id, _sid, bindings = await _connected_fixture()
    results = await _dispatch(user_id, bindings, [_call("c1", "mcp__fixture__echo", "[1]")])

    assert results[0]["ok"] is False
    assert "arguments must be a JSON object" in results[0]["content"]


async def test_dispatch_malformed_arguments_rejected() -> None:
    user_id, _sid, bindings = await _connected_fixture()
    results = await _dispatch(user_id, bindings, [_call("c1", "mcp__fixture__echo", "{oops")])

    assert results[0]["ok"] is False
    assert "malformed arguments" in results[0]["content"]


@pytest.mark.parametrize("raw", ["", "   "])
async def test_empty_arguments_treated_as_empty_object(raw: str) -> None:
    user_id, _sid, bindings = await _connected_fixture()
    results = await _dispatch(user_id, bindings, [_call("c1", "mcp__fixture__noargs", raw)])

    assert results[0]["ok"] is True
    assert "noargs-ok" in results[0]["content"]


async def test_dispatch_after_disconnect_reports_not_connected() -> None:
    user_id, server_id, bindings = await _connected_fixture()
    await mcp_client.disconnect_server(user_id, server_id)

    results = await _dispatch(
        user_id, bindings, [_call("c1", "mcp__fixture__echo", json.dumps({"text": "x"}))],
    )
    assert results[0]["ok"] is False
    assert "not connected" in results[0]["content"]


async def test_server_dying_mid_session_never_raises() -> None:
    user_id = await _create_user("dier", "pw")
    server_id = await _add_server(user_id)
    await _connect(user_id, server_id, mode="die_after")
    async with async_session_factory() as session:
        bindings = (await build_mcp_toolset(session, user_id, RESERVED)).bindings
    assert "mcp__fixture__echo" in bindings

    await asyncio.sleep(2.0)
    results = await _dispatch(
        user_id, bindings, [_call("c1", "mcp__fixture__echo", json.dumps({"text": "x"}))],
    )
    assert results[0]["ok"] is False


async def test_call_timeout_becomes_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, _sid, bindings = await _connected_fixture()
    monkeypatch.setattr(settings, "MCP_TOOL_CALL_TIMEOUT", 0.5)

    results = await _dispatch(
        user_id, bindings, [_call("c1", "mcp__fixture__slow", json.dumps({"seconds": 3}))],
    )
    assert results[0]["ok"] is False
    assert "timed out" in results[0]["content"]


async def test_result_is_capped_with_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, _sid, bindings = await _connected_fixture()
    monkeypatch.setattr(settings, "MCP_TOOL_RESULT_MAX_CHARS", 1000)

    outcome = await call_mcp_tool(bindings["mcp__fixture__big"], {"size": 50000})
    assert outcome["ok"] is True
    assert outcome["truncated"] is True
    assert outcome["text"].startswith("x" * 1000)
    assert "…[truncated" in outcome["text"]
    assert len(outcome["text"]) < 1100

    results = await _dispatch(
        user_id, bindings, [_call("c1", "mcp__fixture__big", json.dumps({"size": 50000}))],
    )
    assert json.loads(results[0]["content"])["truncated"] is True


async def test_builtin_dispatch_unchanged_with_bindings_and_mixed_order() -> None:
    user_id, _sid, bindings = await _connected_fixture()
    async with async_session_factory() as session:
        chat = Chat(title="Mixed", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    results = await _dispatch(
        user_id,
        bindings,
        [
            _call("c1", "save_working_memory", json.dumps({"key": "k", "content": "v"})),
            _call("c2", "mcp__fixture__echo", json.dumps({"text": "after"})),
        ],
        chat_id=chat_id,
    )

    assert [r["name"] for r in results] == ["save_working_memory", "mcp__fixture__echo"]
    assert results[0]["ok"] is True and results[0]["mcp"] is None
    assert results[1]["ok"] is True
    assert len(build_tool_schemas()) == 6
