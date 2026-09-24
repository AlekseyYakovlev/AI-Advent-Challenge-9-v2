"""Regression tests for MCP session registry robustness: cancellation, locking, liveness."""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from httpx import AsyncClient

from agent import mcp_client
from agent.schemas import McpConnectionStatus, McpConnectResult, McpErrorCode

FIXTURE: str = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
BASE: str = "/api/v1/mcp/servers"
KEY: mcp_client.SessionKey = (1, 1)


def _fixture_args(mode: str, *extra: str) -> list[str]:
    """Return argv for launching the fixture server in the given mode."""
    return [FIXTURE, mode, *extra]


def _fixture_children(mode: str) -> dict[int, float]:
    """Map pid to create_time for this test process's descendants running the fixture mode."""
    found: dict[int, float] = {}
    for child in psutil.Process().children(recursive=True):
        try:
            cmdline = child.cmdline()
            if FIXTURE in cmdline and mode in cmdline:
                found[child.pid] = child.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def _is_gone(pid: int, create_time: float) -> bool:
    """Return whether that exact process (pid plus start time) no longer exists."""
    if not psutil.pid_exists(pid):
        return True
    try:
        return psutil.Process(pid).create_time() != create_time
    except psutil.NoSuchProcess:
        return True


async def _kill_dead_handle() -> mcp_client.SessionHandle:
    """Connect an ok server for KEY, then end its owner task while it stays registered."""
    result = await mcp_client.connect_server(
        *KEY, sys.executable, _fixture_args("ok"), None, None
    )
    assert result.status == McpConnectionStatus.CONNECTED
    handle = mcp_client._sessions[KEY]
    handle.close_requested.set()
    await asyncio.wait_for(handle.closed.wait(), 10.0)
    return handle


async def test_cancelled_open_session_leaves_no_child_and_no_registration() -> None:
    """Cancelling a request stuck in the handshake kills the child and registers nothing."""
    before = set(_fixture_children("hang"))
    task = asyncio.create_task(
        mcp_client.connect_server(*KEY, sys.executable, _fixture_args("hang"), None, None)
    )

    spawned: dict[int, float] = {}
    deadline = time.monotonic() + 10.0
    while not spawned and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        spawned = {
            pid: created
            for pid, created in _fixture_children("hang").items()
            if pid not in before
        }
    assert spawned, "the hang fixture child never appeared"
    pid, created = next(iter(spawned.items()))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    deadline = time.monotonic() + 5.0
    while not _is_gone(pid, created) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    assert _is_gone(pid, created)
    assert KEY not in mcp_client._sessions
    assert KEY not in mcp_client._last_results


async def test_status_check_loses_to_concurrent_disconnect() -> None:
    """A disconnect that lands during a dead-handle status check leaves NOT_CONNECTED."""
    handle = await _kill_dead_handle()
    lock = mcp_client._lock_for(KEY)

    async with lock:
        status_task = asyncio.create_task(mcp_client.get_status(*KEY))
        await asyncio.sleep(0.2)
        assert not status_task.done()
        mcp_client._sessions.pop(KEY, None)
        mcp_client._last_results.pop(KEY, None)
    outcome = await status_task
    await mcp_client.close_handle(handle)

    assert outcome.status == McpConnectionStatus.NOT_CONNECTED
    assert KEY not in mcp_client._last_results
    assert (await mcp_client.get_status(*KEY)).status == McpConnectionStatus.NOT_CONNECTED


async def test_status_check_loses_to_concurrent_reconnect() -> None:
    """A successful reconnect during a dead-handle status check is never overwritten."""
    handle = await _kill_dead_handle()
    lock = mcp_client._lock_for(KEY)

    async with lock:
        status_task = asyncio.create_task(mcp_client.get_status(*KEY))
        await asyncio.sleep(0.2)
        fresh, _ = await mcp_client.open_session(sys.executable, _fixture_args("ok"), None, None)
        assert fresh is not None
        mcp_client._sessions[KEY] = fresh
        mcp_client._last_results.pop(KEY, None)
    outcome = await status_task
    await mcp_client.close_handle(handle)

    assert outcome.status == McpConnectionStatus.CONNECTED
    assert KEY not in mcp_client._last_results
    assert not fresh.closed.is_set()
    assert (await mcp_client.get_status(*KEY)).status == McpConnectionStatus.CONNECTED
    assert mcp_client._sessions[KEY] is fresh


async def test_cleanup_server_removes_lock() -> None:
    """cleanup_server disconnects the session and leaves no lock entry behind."""
    await mcp_client.connect_server(*KEY, sys.executable, _fixture_args("ok"), None, None)
    assert KEY in mcp_client._locks

    await mcp_client.cleanup_server(*KEY)

    assert KEY not in mcp_client._locks
    assert KEY not in mcp_client._sessions


@pytest.mark.parametrize("cleanup_first", [True, False])
async def test_cleanup_server_concurrent_with_connect(cleanup_first: bool) -> None:
    """cleanup_server racing a connect never raises and leaves a consistent registry."""
    connect = mcp_client.connect_server(*KEY, sys.executable, _fixture_args("ok"), None, None)
    cleanup = mcp_client.cleanup_server(*KEY)
    coros = [cleanup, connect] if cleanup_first else [connect, cleanup]

    await asyncio.gather(*coros)

    handle = mcp_client._sessions.get(KEY)
    if handle is None:
        assert KEY not in mcp_client._locks
    else:
        assert mcp_client.is_connected(*KEY)
        assert KEY in mcp_client._locks


async def test_busy_server_is_not_killed_by_slow_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that answers pings late stays CONNECTED and keeps its session."""
    monkeypatch.setattr(mcp_client, "LIVENESS_TIMEOUT", 0.5)
    result = await mcp_client.connect_server(
        *KEY, sys.executable, _fixture_args("slow_ping", "3"), None, None
    )
    assert result.status == McpConnectionStatus.CONNECTED
    handle = mcp_client._sessions[KEY]

    first = await mcp_client.get_status(*KEY)
    second = await mcp_client.get_status(*KEY)

    assert first.status == McpConnectionStatus.CONNECTED
    assert second.status == McpConnectionStatus.CONNECTED
    assert mcp_client.is_connected(*KEY)
    assert not handle.closed.is_set()
    assert mcp_client._sessions[KEY] is handle
    assert KEY not in mcp_client._last_results


async def test_dead_process_is_still_reported_process_exited() -> None:
    """A process that died is detected from the ping failure, well under the ping timeout."""
    result = await mcp_client.connect_server(
        *KEY, sys.executable, _fixture_args("die_after"), None, None
    )
    assert result.status == McpConnectionStatus.CONNECTED
    await asyncio.sleep(2.5)

    started = time.monotonic()
    status = await mcp_client.get_status(*KEY)

    assert status.status == McpConnectionStatus.ERROR
    assert status.error_code == McpErrorCode.PROCESS_EXITED
    assert time.monotonic() - started < mcp_client.LIVENESS_TIMEOUT
    assert not mcp_client.is_connected(*KEY)


async def _create_connected_slow_servers(client: AsyncClient, count: int) -> list[int]:
    """Create and connect count slow_ping servers through the API; return their ids."""
    ids: list[int] = []
    for index in range(count):
        body: dict[str, Any] = {
            "name": f"slow-{index}",
            "command": sys.executable,
            "args": _fixture_args("slow_ping", "3"),
            "env": {},
            "cwd": None,
            "enabled": True,
        }
        created = await client.post(BASE, json=body)
        assert created.status_code == 201, created.text
        server_id = created.json()["id"]
        connected = await client.post(f"{BASE}/{server_id}/connect")
        assert connected.json()["connection"]["status"] == "connected", connected.text
        ids.append(server_id)
    return ids


async def test_list_pings_unresponsive_servers_concurrently(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three unresponsive servers cost about one ping timeout, and all stay CONNECTED."""
    await _create_connected_slow_servers(authenticated_client, 3)
    monkeypatch.setattr(mcp_client, "LIVENESS_TIMEOUT", 0.5)

    started = time.monotonic()
    resp = await authenticated_client.get(BASE)
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert [row["connection"]["status"] for row in resp.json()] == ["connected"] * 3
    assert elapsed < 1.3


async def test_list_survives_one_failing_status_check(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failing status check is reported for that server only and does not fail the list."""
    ids = await _create_connected_slow_servers(authenticated_client, 2)
    real_get_status = mcp_client.get_status

    async def flaky(user_id: int, server_id: int) -> McpConnectResult:
        if server_id == ids[0]:
            raise RuntimeError("boom")
        return await real_get_status(user_id, server_id)

    monkeypatch.setattr(mcp_client, "LIVENESS_TIMEOUT", 0.5)
    monkeypatch.setattr(mcp_client, "get_status", flaky)

    resp = await authenticated_client.get(BASE)

    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.json()}
    assert [row["id"] for row in resp.json()] == ids
    assert rows[ids[0]]["connection"]["status"] == "error"
    assert rows[ids[0]]["connection"]["error_code"] == McpErrorCode.PROTOCOL_ERROR.value
    assert rows[ids[1]]["connection"]["status"] == "connected"
