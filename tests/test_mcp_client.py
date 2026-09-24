"""Tests for the MCP stdio client: connect, list tools, failure classification, registry."""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import IO

import psutil
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
    spawn_failure_reason,
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
    assert classify_mcp_error(PermissionError(13, "Access is denied"))[0] == (
        McpErrorCode.SPAWN_FAILED
    )
    assert classify_mcp_error(OSError(193, "%1 is not a valid Win32 application"))[0] == (
        McpErrorCode.SPAWN_FAILED
    )
    assert classify_mcp_error(NotADirectoryError(20, "Not a directory"))[0] == (
        McpErrorCode.SPAWN_FAILED
    )
    assert classify_mcp_error(ConnectionResetError())[0] == McpErrorCode.PROCESS_EXITED
    wrapped = ExceptionGroup("outer", [PermissionError(13, "Access is denied")])
    assert classify_mcp_error(wrapped)[0] == McpErrorCode.SPAWN_FAILED


def test_spawn_failure_reason_uses_errno_and_strerror_only() -> None:
    """The reason carries errno/strerror, never the filename."""
    reason = spawn_failure_reason(PermissionError(13, "Access is denied", "C:/secret/path.exe"))
    assert "13" in reason
    assert "Access is denied" in reason
    assert "secret" not in reason
    assert spawn_failure_reason(OSError()) == "OSError"
    assert spawn_failure_reason(RuntimeError("x")) == "RuntimeError"


def _fixture_children_pids() -> set[int]:
    """Return pids of this test process's descendants that run the fixture script."""
    pids: set[int] = set()
    for child in psutil.Process().children(recursive=True):
        try:
            if FIXTURE in child.cmdline():
                pids.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


@pytest.mark.parametrize("kind", ["missing", "file"])
async def test_bad_cwd_gives_cwd_specific_error_without_spawning(
    tmp_path: Path, kind: str
) -> None:
    """A cwd that is missing or not a directory fails up front and starts no process."""
    cwd = tmp_path / "missing"
    if kind == "file":
        cwd.write_text("not a directory", encoding="utf-8")
    before = _fixture_children_pids()

    result = await connect_once_and_list(sys.executable, _fixture_args("ok"), cwd=str(cwd))

    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.SPAWN_FAILED
    assert result.error_message is not None
    assert "Рабочая папка" in result.error_message
    assert str(cwd) in result.error_message
    assert _fixture_children_pids() == before


async def test_missing_command_with_valid_cwd_is_still_command_not_found(tmp_path: Path) -> None:
    """A missing command keeps its own message even when cwd is a real directory."""
    result = await connect_once_and_list(
        "C:/definitely/not/here/nope.exe", [], cwd=str(tmp_path)
    )

    assert result.error_code == McpErrorCode.COMMAND_NOT_FOUND
    assert result.error_message == MCP_ERROR_MESSAGES[McpErrorCode.COMMAND_NOT_FOUND]


async def test_unspawnable_file_is_spawn_failed_without_leaking_env(tmp_path: Path) -> None:
    """A file that cannot be executed reports errno/strerror and never the env values."""
    not_executable = tmp_path / "not_a_program.txt"
    not_executable.write_text("plain text", encoding="utf-8")
    secret = "supersecret123"

    result = await connect_once_and_list(
        str(not_executable), [], env={"SECRET_TOKEN": secret}
    )

    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.SPAWN_FAILED
    assert result.error_message is not None
    assert "errno" in result.error_message
    for text in (result.error_message, result.detail or "", result.stderr_tail or ""):
        assert secret not in text


def _stderr_file(content: bytes) -> IO[str]:
    """Create a text-mode stderr capture file pre-filled with raw bytes."""
    errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    errfile.buffer.write(content)
    errfile.buffer.flush()
    return errfile


class _SpyErrFile:
    """Stand-in stderr file that records the size of every read on its binary layer."""

    def __init__(self, inner: IO[str]) -> None:
        self._inner = inner
        self.read_sizes: list[int] = []
        self.buffer = self

    def flush(self) -> None:
        self._inner.flush()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._inner.buffer.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._inner.buffer.read(size)


def test_read_stderr_tail_is_bounded_to_last_64_kib() -> None:
    """A huge capture file yields the last 20 whole lines from a bounded read."""
    lines = [f"line {index:06d} " + "x" * 40 for index in range(5000)]
    content = ("\n".join(lines) + "\n").encode("utf-8")
    assert len(content) > 2 * mcp_client.STDERR_TAIL_MAX_BYTES
    inner = _stderr_file(content)
    spy = _SpyErrFile(inner)

    tail = mcp_client.read_stderr_tail(spy)  # type: ignore[arg-type]

    assert tail.splitlines() == lines[-20:]
    assert spy.read_sizes
    assert all(0 <= size <= mcp_client.STDERR_TAIL_MAX_BYTES for size in spy.read_sizes)
    inner.close()


def test_read_stderr_tail_drops_partial_first_line_of_a_window() -> None:
    """When only a window is read, the cut-off first line is discarded, not returned."""
    body = "".join(f"row {index:05d}\n" for index in range(20000))
    errfile = _stderr_file(body.encode("utf-8"))

    tail = mcp_client.read_stderr_tail(errfile, max_lines=100000)

    parsed = tail.splitlines()
    assert parsed[-1] == "row 19999"
    assert all(row.startswith("row ") and len(row) == 9 for row in parsed)
    assert len(body) > mcp_client.STDERR_TAIL_MAX_BYTES
    assert len(tail.encode("utf-8")) <= mcp_client.STDERR_TAIL_MAX_BYTES
    errfile.close()


def test_read_stderr_tail_small_and_closed_files() -> None:
    """A small file returns all its lines; a closed file returns an empty string."""
    errfile = _stderr_file(b"a\nb\nc\n")
    assert mcp_client.read_stderr_tail(errfile) == "a\nb\nc"
    errfile.close()
    assert mcp_client.read_stderr_tail(errfile) == ""


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

    deadline: float = time.monotonic() + 10.0
    while True:
        status = await get_status(1, 1)
        if status.status == McpConnectionStatus.ERROR or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.1)

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
