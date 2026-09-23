"""Tests for the MCP stdio client: connect, list tools, failure classification, registry."""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from agent import mcp_client
from agent.mcp_client import (
    MCP_ERROR_MESSAGES,
    classify_mcp_error,
    cleanup_all_sessions,
    connect_once_and_list,
    connect_server,
    disconnect_server,
    get_live_session,
    get_status,
    is_connected,
)
from agent.schemas import McpConnectionStatus, McpErrorCode

FIXTURE: str = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
FILESYSTEM_EXE: str = os.environ.get(
    "MCP_FILESYSTEM_EXE", r"C:\Users\Aleksey\go\bin\filesystem.exe"
)
requires_filesystem_exe = pytest.mark.skipif(
    not Path(FILESYSTEM_EXE).exists(),
    reason="filesystem.exe not installed",
)


def _fixture_args(mode: str) -> list[str]:
    """Return argv for launching the fixture server in the given mode."""
    return [FIXTURE, mode]


async def test_connect_once_success_fixture() -> None:
    """A healthy server yields serverInfo and the full tool list."""
    result = await connect_once_and_list(sys.executable, _fixture_args("ok"))

    assert result.status == McpConnectionStatus.CONNECTED
    assert result.server_info is not None
    assert result.server_info.name == "fixture-server"
    assert result.server_info.protocol_version
    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {"echo", "add"}
    assert "a" in tools["add"].input_schema["required"]
    assert tools["echo"].description == "Echo the given text back."


async def test_command_not_found() -> None:
    """A nonexistent command maps to COMMAND_NOT_FOUND without leaking group text."""
    result = await connect_once_and_list("C:/definitely/not/here/nope.exe", [])

    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.COMMAND_NOT_FOUND
    assert result.error_message == MCP_ERROR_MESSAGES[McpErrorCode.COMMAND_NOT_FOUND]
    for text in (result.error_message, result.detail or ""):
        assert "TaskGroup" not in text
        assert "ExceptionGroup" not in text


async def test_process_exited_garbage() -> None:
    """A server writing non-JSON-RPC output and exiting maps to PROCESS_EXITED."""
    result = await connect_once_and_list(sys.executable, _fixture_args("garbage"))

    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.PROCESS_EXITED


async def test_stderr_tail_last_20_lines() -> None:
    """Only the last 20 stderr lines are returned on failure."""
    result = await connect_once_and_list(sys.executable, _fixture_args("stderr_exit"))

    assert result.error_code == McpErrorCode.PROCESS_EXITED
    assert result.stderr_tail is not None
    lines = result.stderr_tail.splitlines()
    assert len(lines) == 20
    assert lines[0] == "stderr line 6"
    assert lines[-1] == "stderr line 25"


async def test_handshake_timeout() -> None:
    """A hung server maps to HANDSHAKE_TIMEOUT with the timeout in the message."""
    started = time.monotonic()
    result = await connect_once_and_list(
        sys.executable, _fixture_args("hang"), timeout_seconds=1.5
    )
    elapsed = time.monotonic() - started

    assert result.error_code == McpErrorCode.HANDSHAKE_TIMEOUT
    assert result.error_message is not None
    assert "1.5 с" in result.error_message
    assert elapsed < 1.5 + 2.0 + 4.0


async def test_protocol_error_bad_version() -> None:
    """An unsupported protocol version maps to PROTOCOL_ERROR."""
    result = await connect_once_and_list(sys.executable, _fixture_args("bad_protocol"))

    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.PROTOCOL_ERROR


def test_classify_mcp_error_unwraps_nested_groups() -> None:
    """Exception groups are flattened and every known leaf maps to its code."""
    leaf = McpError(ErrorData(code=-32000, message="Connection closed"))
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [leaf])])

    code, detail = classify_mcp_error(nested)
    assert code == McpErrorCode.PROCESS_EXITED
    assert "Connection closed" in detail
    assert "ExceptionGroup" not in detail

    assert classify_mcp_error(FileNotFoundError("x"))[0] == McpErrorCode.COMMAND_NOT_FOUND
    assert classify_mcp_error(TimeoutError())[0] == McpErrorCode.HANDSHAKE_TIMEOUT
    assert classify_mcp_error(BrokenPipeError())[0] == McpErrorCode.PROCESS_EXITED
    assert (
        classify_mcp_error(RuntimeError("Unsupported protocol version from the server: x"))[0]
        == McpErrorCode.PROTOCOL_ERROR
    )


async def test_connect_server_registry_and_disconnect() -> None:
    """A connected session is usable from another task and removed on disconnect."""
    result = await connect_server(1, 1, sys.executable, _fixture_args("ok"), None, None)
    assert result.status == McpConnectionStatus.CONNECTED
    assert is_connected(1, 1)

    session = get_live_session(1, 1)
    assert session is not None
    listed = await asyncio.create_task(session.list_tools())
    assert len(listed.tools) == 2

    disconnected = await disconnect_server(1, 1)
    assert disconnected.status == McpConnectionStatus.NOT_CONNECTED
    assert not is_connected(1, 1)
    assert get_live_session(1, 1) is None


async def test_connect_server_twice_replaces_session() -> None:
    """Reconnecting the same key keeps one registry entry and closes the old handle."""
    await connect_server(1, 1, sys.executable, _fixture_args("ok"), None, None)
    first_handle = mcp_client._sessions[(1, 1)]

    second = await connect_server(1, 1, sys.executable, _fixture_args("ok"), None, None)

    assert second.status == McpConnectionStatus.CONNECTED
    assert len(mcp_client._sessions) == 1
    assert mcp_client._sessions[(1, 1)] is not first_handle
    assert first_handle.closed.is_set()


async def test_registry_is_user_scoped() -> None:
    """One user's session is invisible to another user with the same server id."""
    await connect_server(1, 1, sys.executable, _fixture_args("ok"), None, None)

    assert is_connected(2, 1) is False
    status = await get_status(2, 1)
    assert status.status == McpConnectionStatus.NOT_CONNECTED


async def test_get_status_detects_exited_process() -> None:
    """A server that died after connecting is reported as PROCESS_EXITED lazily."""
    result = await connect_server(1, 1, sys.executable, _fixture_args("die_after"), None, None)
    assert result.status == McpConnectionStatus.CONNECTED

    await asyncio.sleep(2.5)
    status = await get_status(1, 1)

    assert status.status == McpConnectionStatus.ERROR
    assert status.error_code == McpErrorCode.PROCESS_EXITED
    assert is_connected(1, 1) is False


async def test_failed_connect_status_is_error_then_disconnect_clears() -> None:
    """A failed connect is remembered as an error until the user disconnects."""
    failed = await connect_server(1, 1, "C:/definitely/not/here/nope.exe", [], None, None)
    assert failed.error_code == McpErrorCode.COMMAND_NOT_FOUND

    status = await get_status(1, 1)
    assert status.status == McpConnectionStatus.ERROR
    assert status.error_code == McpErrorCode.COMMAND_NOT_FOUND

    await disconnect_server(1, 1)
    cleared = await get_status(1, 1)
    assert cleared.status == McpConnectionStatus.NOT_CONNECTED


async def test_cleanup_all_sessions_closes_everything() -> None:
    """Shutdown cleanup closes every live session and empties the registry."""
    await connect_server(1, 1, sys.executable, _fixture_args("ok"), None, None)
    await connect_server(1, 2, sys.executable, _fixture_args("ok"), None, None)
    handles = list(mcp_client._sessions.values())
    assert len(handles) == 2

    await cleanup_all_sessions()

    assert all(handle.closed.is_set() for handle in handles)
    assert not is_connected(1, 1)
    assert not is_connected(1, 2)


@requires_filesystem_exe
async def test_real_filesystem_server(tmp_path: Path) -> None:
    """The real Go filesystem server connects and advertises its 17 tools."""
    result = await connect_once_and_list(FILESYSTEM_EXE, [str(tmp_path)])

    assert result.status == McpConnectionStatus.CONNECTED
    assert result.server_info is not None
    assert result.server_info.name == "filesystem-mcp-server"
    assert len(result.tools) == 17


@requires_filesystem_exe
async def test_real_filesystem_list_flag_exits(tmp_path: Path) -> None:
    """The -list flag makes the binary print plain text and exit, mapping to PROCESS_EXITED."""
    result = await connect_once_and_list(FILESYSTEM_EXE, ["-list", str(tmp_path)])

    assert result.error_code == McpErrorCode.PROCESS_EXITED
