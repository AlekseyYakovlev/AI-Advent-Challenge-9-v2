"""MCP stdio client: connect, list tools, and hold persistent sessions in memory."""

import asyncio
import contextlib
import tempfile
from typing import IO, Literal

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from agent.schemas import (
    McpConnectionStatus,
    McpConnectResult,
    McpErrorCode,
    McpServerInfo,
    McpToolInfo,
)
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

STDERR_TAIL_LINES = 20
LIVENESS_TIMEOUT = 2.0
CLOSE_TIMEOUT = 5.0
READY_GRACE_SECONDS = 5.0
DETAIL_MAX_LENGTH = 2000

MCP_ERROR_MESSAGES: dict[McpErrorCode, str] = {
    McpErrorCode.COMMAND_NOT_FOUND: "Команда не найдена. Проверьте путь к исполняемому файлу.",
    McpErrorCode.PROCESS_EXITED: "Процесс сервера неожиданно завершился.",
    McpErrorCode.HANDSHAKE_TIMEOUT: "Превышено время ожидания подключения ({timeout} с).",
    McpErrorCode.PROTOCOL_ERROR: "Ошибка протокола MCP при подключении к серверу.",
}

_PROCESS_GONE_ERRORS = (BrokenPipeError, ConnectionResetError, EOFError)
# A ping that fails with one of these means the transport streams are gone, i.e. the process died.
_DEAD_STREAM_ERRORS = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    anyio.EndOfStream,
    *_PROCESS_GONE_ERRORS,
)

ProbeResult = Literal["alive", "unresponsive", "dead"]

SessionKey = tuple[int, int]


class SessionHandle:
    """State shared between the owner task and callers for one MCP session."""

    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.result: McpConnectResult | None = None
        self.ready: asyncio.Event = asyncio.Event()
        self.close_requested: asyncio.Event = asyncio.Event()
        self.closed: asyncio.Event = asyncio.Event()
        self.error: BaseException | None = None
        self.task: asyncio.Task[None] | None = None
        # stdio_client hands the fd to the OS, so stderr needs a real file, not an in-memory buffer.
        self.errfile: IO[str] = tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        )


_sessions: dict[SessionKey, SessionHandle] = {}
_last_results: dict[SessionKey, McpConnectResult] = {}
_locks: dict[SessionKey, asyncio.Lock] = {}


def _flatten_exception_group(exc: BaseException) -> list[BaseException]:
    """Recursively unwrap nested exception groups to their leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for sub in exc.exceptions:
            leaves.extend(_flatten_exception_group(sub))
        return leaves
    return [exc]


def classify_mcp_error(exc: BaseException) -> tuple[McpErrorCode, str]:
    """Map a raw connect-sequence exception to a fixed error code and a detail string."""
    leaves = _flatten_exception_group(exc)
    detail = "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves)[:DETAIL_MAX_LENGTH]

    if any(isinstance(leaf, TimeoutError) for leaf in leaves):
        return McpErrorCode.HANDSHAKE_TIMEOUT, detail
    if any(isinstance(leaf, _PROCESS_GONE_ERRORS) for leaf in leaves):
        return McpErrorCode.PROCESS_EXITED, detail
    if any(isinstance(leaf, OSError) for leaf in leaves):
        return McpErrorCode.COMMAND_NOT_FOUND, detail
    if any("Connection closed" in str(leaf) for leaf in leaves):
        return McpErrorCode.PROCESS_EXITED, detail
    return McpErrorCode.PROTOCOL_ERROR, detail


def read_stderr_tail(errfile: IO[str], max_lines: int = STDERR_TAIL_LINES) -> str:
    """Return the last lines the child process wrote to its stderr capture file."""
    try:
        errfile.flush()
        errfile.seek(0)
        lines = errfile.read().splitlines()
    except (OSError, ValueError):
        return ""
    return "\n".join(lines[-max_lines:])


def _format_timeout(timeout_seconds: float) -> str:
    """Render a timeout without a trailing .0 when it is a whole number."""
    if float(timeout_seconds).is_integer():
        return str(int(timeout_seconds))
    return str(timeout_seconds)


def build_error_result(
    code: McpErrorCode,
    detail: str,
    stderr_tail: str,
    timeout_seconds: float,
) -> McpConnectResult:
    """Build an ERROR result carrying the localized message for the given code."""
    message = MCP_ERROR_MESSAGES[code].format(timeout=_format_timeout(timeout_seconds))
    return McpConnectResult(
        status=McpConnectionStatus.ERROR,
        error_code=code,
        error_message=message,
        detail=detail,
        stderr_tail=stderr_tail,
    )


async def _owner_task(
    handle: SessionHandle,
    params: StdioServerParameters,
    timeout_seconds: float,
) -> None:
    """Enter stdio_client and ClientSession in one task and park until close is requested.

    anyio cancel scopes must be exited by the task that entered them, so this task is the
    only place that opens or closes the transport.
    """
    try:
        async with asyncio.timeout(timeout_seconds) as deadline:
            async with stdio_client(params, errlog=handle.errfile) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    listed = await session.list_tools()
                    handle.result = McpConnectResult(
                        status=McpConnectionStatus.CONNECTED,
                        server_info=McpServerInfo(
                            name=init.serverInfo.name,
                            version=init.serverInfo.version,
                            protocol_version=str(init.protocolVersion),
                        ),
                        tools=[
                            McpToolInfo(
                                name=tool.name,
                                description=tool.description,
                                input_schema=tool.inputSchema,
                            )
                            for tool in listed.tools
                        ],
                    )
                    handle.session = session
                    # The timeout only guards the handshake, not the parked session.
                    deadline.reschedule(None)
                    handle.ready.set()
                    await handle.close_requested.wait()
    except Exception as exc:
        handle.error = exc
    finally:
        handle.session = None
        handle.ready.set()
        handle.closed.set()


async def close_handle(handle: SessionHandle) -> None:
    """Ask the owner task to shut the session down, force-cancelling on timeout."""
    handle.close_requested.set()
    task = handle.task
    try:
        await asyncio.wait_for(handle.closed.wait(), CLOSE_TIMEOUT)
    except TimeoutError:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        with contextlib.suppress(OSError):
            handle.errfile.close()


async def open_session(
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: str | None,
    timeout_seconds: float | None = None,
) -> tuple[SessionHandle | None, McpConnectResult]:
    """Spawn the server, run the handshake and list tools; never raises to the caller."""
    timeout = settings.MCP_CONNECT_TIMEOUT if timeout_seconds is None else timeout_seconds
    # The SDK merges env over its safe default environment, so PATH and friends survive.
    params = StdioServerParameters(command=command, args=args, env=env or None, cwd=cwd or None)
    handle = SessionHandle()
    handle.task = asyncio.create_task(_owner_task(handle, params, timeout))

    try:
        await asyncio.wait_for(handle.ready.wait(), timeout + READY_GRACE_SECONDS)
    except asyncio.CancelledError:
        # Nothing is registered yet, so nobody else could close this half-open session.
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await handle.task
        with contextlib.suppress(OSError):
            handle.errfile.close()
        raise
    except TimeoutError:
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await handle.task
        handle.error = TimeoutError("connect sequence did not finish in time")

    if handle.error is None and handle.result is not None:
        logger.info("mcp_connect_ok", command=command, tool_count=len(handle.result.tools))
        return handle, handle.result

    error: BaseException = handle.error or RuntimeError("server did not report a result")
    code, detail = classify_mcp_error(error)
    stderr_tail = read_stderr_tail(handle.errfile)
    await close_handle(handle)
    logger.warning("mcp_connect_failed", command=command, error_code=code.value)
    return None, build_error_result(code, detail, stderr_tail, timeout)


async def connect_once_and_list(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout_seconds: float | None = None,
) -> McpConnectResult:
    """Connect, list tools, then close the session (used by the CLI)."""
    handle, result = await open_session(command, args, env, cwd, timeout_seconds)
    if handle is not None:
        await close_handle(handle)
    return result


def _lock_for(key: SessionKey) -> asyncio.Lock:
    """Return the per-server lock that serializes connect and disconnect."""
    return _locks.setdefault(key, asyncio.Lock())


async def connect_server(
    user_id: int,
    server_id: int,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: str | None,
) -> McpConnectResult:
    """Open a persistent session for a server, replacing any existing one."""
    key: SessionKey = (user_id, server_id)
    async with _lock_for(key):
        previous = _sessions.pop(key, None)
        if previous is not None:
            await close_handle(previous)
        _last_results.pop(key, None)

        handle, result = await open_session(command, args, env, cwd)
        if handle is not None:
            _sessions[key] = handle
        else:
            _last_results[key] = result
        return result


def has_recorded_failure(user_id: int, server_id: int) -> bool:
    """Return whether a failed connect (or a dead session) is remembered for the server."""
    return (user_id, server_id) in _last_results


async def ensure_connected(
    user_id: int,
    server_id: int,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: str | None,
) -> McpConnectResult:
    """Connect a server on demand without replacing a live session or retrying a failure.

    A live session is returned untouched so concurrent chat turns share it. A remembered
    failure is returned as is; only disconnect, edit or a manual connect clears it.
    """
    key: SessionKey = (user_id, server_id)
    async with _lock_for(key):
        handle = _sessions.get(key)
        if handle is not None and is_connected(user_id, server_id) and handle.result is not None:
            return handle.result

        remembered = _last_results.get(key)
        if remembered is not None:
            return remembered

        if handle is not None:
            # The session is registered but its owner task is gone: record the death the same
            # way the Settings status check does, instead of silently respawning the process.
            _sessions.pop(key, None)
            stderr_tail = read_stderr_tail(handle.errfile)
            await close_handle(handle)
            result = build_error_result(
                McpErrorCode.PROCESS_EXITED,
                "server exited",
                stderr_tail,
                settings.MCP_CONNECT_TIMEOUT,
            )
            _last_results[key] = result
            logger.warning("mcp_session_dead", server_id=server_id, user_id=user_id)
            return result

        opened, result = await open_session(command, args, env, cwd)
        if opened is not None:
            _sessions[key] = opened
        else:
            _last_results[key] = result
        return result


async def _disconnect_locked(key: SessionKey) -> None:
    """Close a server's session and forget its last result; the caller holds the key's lock."""
    handle = _sessions.pop(key, None)
    if handle is not None:
        await close_handle(handle)
    _last_results.pop(key, None)


async def disconnect_server(user_id: int, server_id: int) -> McpConnectResult:
    """Close a server's session and forget its last result; safe to call when idle."""
    key: SessionKey = (user_id, server_id)
    async with _lock_for(key):
        await _disconnect_locked(key)
    return McpConnectResult(status=McpConnectionStatus.NOT_CONNECTED)


def is_connected(user_id: int, server_id: int) -> bool:
    """Return whether a live session is registered for the server."""
    handle = _sessions.get((user_id, server_id))
    return handle is not None and handle.session is not None and not handle.closed.is_set()


def get_live_session(user_id: int, server_id: int) -> ClientSession | None:
    """Return the live ClientSession for a server, or None when not connected."""
    if not is_connected(user_id, server_id):
        return None
    return _sessions[(user_id, server_id)].session


def get_live_tools(user_id: int, server_id: int) -> list[McpToolInfo] | None:
    """Return the tools cached at connect time, or None when the server is not connected.

    Reads the registry only (no ping) so the chat path never stalls a busy server.
    """
    if not is_connected(user_id, server_id):
        return None
    result = _sessions[(user_id, server_id)].result
    if result is None:
        return None
    return list(result.tools)


def _handle_is_finished(handle: SessionHandle) -> bool:
    """Return whether the owner task ended or the session was closed."""
    return handle.task is None or handle.task.done() or handle.closed.is_set()


async def _probe(handle: SessionHandle, *, user_id: int, server_id: int) -> ProbeResult:
    """Classify a session as alive, unresponsive (slow to answer) or dead.

    Only evidence that the transport is gone counts as dead. A ping that merely times out
    means a busy single-threaded server, which must not be killed.
    """
    session = handle.session
    if _handle_is_finished(handle) or session is None:
        return "dead"
    try:
        await asyncio.wait_for(session.send_ping(), LIVENESS_TIMEOUT)
    except _DEAD_STREAM_ERRORS:
        return "dead"
    except McpError as exc:
        if "Connection closed" in str(exc):
            return "dead"
        failure = type(exc).__name__
    except Exception as exc:
        failure = type(exc).__name__
    else:
        return "alive"

    # A transport failure ends the owner task, so give it one loop turn before judging.
    await asyncio.sleep(0)
    if _handle_is_finished(handle):
        return "dead"
    logger.warning(
        "mcp_ping_unresponsive", user_id=user_id, server_id=server_id, error=failure
    )
    return "unresponsive"


def _registry_status(key: SessionKey) -> McpConnectResult:
    """Return a server's status from the registry alone, without pinging."""
    handle = _sessions.get(key)
    if handle is not None and is_connected(*key) and handle.result is not None:
        return handle.result
    return _last_results.get(key) or McpConnectResult(status=McpConnectionStatus.NOT_CONNECTED)


async def get_status(user_id: int, server_id: int) -> McpConnectResult:
    """Report a server's status, flipping to PROCESS_EXITED if its process died.

    The healthy path takes no lock so Settings polling never waits on a connect; only the
    dead-handle branch serializes with connect and disconnect.
    """
    key: SessionKey = (user_id, server_id)
    handle = _sessions.get(key)
    if handle is None:
        return _last_results.get(key) or McpConnectResult(status=McpConnectionStatus.NOT_CONNECTED)

    if await _probe(handle, user_id=user_id, server_id=server_id) != "dead":
        if handle.result is not None:
            return handle.result

    async with _lock_for(key):
        if _sessions.get(key) is not handle:
            # A concurrent disconnect or reconnect already replaced this handle and wins.
            return _registry_status(key)
        _sessions.pop(key, None)
        stderr_tail = read_stderr_tail(handle.errfile)
        await close_handle(handle)
        result = build_error_result(
            McpErrorCode.PROCESS_EXITED,
            "server exited",
            stderr_tail,
            settings.MCP_CONNECT_TIMEOUT,
        )
        _last_results[key] = result
        logger.warning("mcp_session_dead", server_id=server_id, user_id=user_id)
        return result


async def cleanup_server(user_id: int, server_id: int) -> None:
    """Disconnect a server and drop its lock while still holding it."""
    key: SessionKey = (user_id, server_id)
    lock = _lock_for(key)
    async with lock:
        await _disconnect_locked(key)
        if _locks.get(key) is lock:
            del _locks[key]


async def cleanup_user_sessions(user_id: int) -> None:
    """Disconnect every server owned by a user."""
    keys = {key for key in (*_sessions, *_last_results) if key[0] == user_id}
    for key in keys:
        await cleanup_server(*key)


async def cleanup_all_sessions() -> None:
    """Close every live session (Agent shutdown) and clear all registry state."""
    handles = list(_sessions.values())
    await asyncio.gather(*(close_handle(handle) for handle in handles), return_exceptions=True)
    _sessions.clear()
    _last_results.clear()
    _locks.clear()
