"""Tests for lazy MCP auto-connect when a chat turn builds its toolset."""

import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent import mcp_client, mcp_config, mcp_tools
from agent.mcp_tools import build_mcp_toolset
from agent.schemas import McpConnectionStatus, McpErrorCode
from agent.tools import TOOL_REGISTRY
from shared.config import settings
from shared.database import async_session_factory
from tests.conftest import _create_user

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
BAD_COMMAND = "definitely-not-a-real-mcp-command-2n8"
RESERVED = set(TOOL_REGISTRY)
ECHO = "mcp__fixture__echo"


async def _add(
    user_id: int,
    name: str = "Fixture",
    mode: str = "tools_extra",
    enabled: bool = True,
    command: str | None = None,
) -> int:
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session,
            user_id,
            name,
            command or sys.executable,
            [FIXTURE, mode],
            {},
            None,
            enabled,
        )
        return row.id


async def _build(user_id: int) -> mcp_tools.McpToolset:
    async with async_session_factory() as session:
        return await build_mcp_toolset(session, user_id, RESERVED)


def _count_spawns(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Wrap open_session so tests can see how many processes were spawned."""
    calls: list[str] = []
    original = mcp_client.open_session

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(str(args[0]))
        return await original(*args, **kwargs)

    monkeypatch.setattr(mcp_client, "open_session", counting)
    return lambda: len(calls)


async def test_auto_connects_enabled_unconnected_server(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _create_user("auto1", "pw")
    server_id = await _add(user_id)
    spawns = _count_spawns(monkeypatch)

    toolset = await _build(user_id)

    assert ECHO in toolset.bindings
    assert mcp_client.is_connected(user_id, server_id)
    assert spawns() == 1


async def test_second_build_reuses_session(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _create_user("auto2", "pw")
    server_id = await _add(user_id)
    spawns = _count_spawns(monkeypatch)

    await _build(user_id)
    handle = mcp_client._sessions[(user_id, server_id)]
    live_session = handle.session
    toolset = await _build(user_id)

    assert spawns() == 1
    assert mcp_client._sessions[(user_id, server_id)] is handle
    assert handle.session is live_session
    assert ECHO in toolset.bindings


async def test_concurrent_builds_do_not_replace_session(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _create_user("auto3", "pw")
    server_id = await _add(user_id)
    spawns = _count_spawns(monkeypatch)

    first, second = await asyncio.gather(_build(user_id), _build(user_id))

    assert spawns() == 1
    assert ECHO in first.bindings and ECHO in second.bindings
    handle = mcp_client._sessions[(user_id, server_id)]
    assert handle.session is not None and not handle.closed.is_set()
    assert mcp_client.is_connected(user_id, server_id)


async def test_failed_server_not_retried_until_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _create_user("auto4", "pw")
    server_id = await _add(user_id, name="Broken", command=BAD_COMMAND)
    spawns = _count_spawns(monkeypatch)

    first = await _build(user_id)
    second = await _build(user_id)

    assert first.schemas == [] and second.schemas == []
    assert spawns() == 1
    assert mcp_client.has_recorded_failure(user_id, server_id)

    await mcp_client.disconnect_server(user_id, server_id)
    assert not mcp_client.has_recorded_failure(user_id, server_id)
    await _build(user_id)

    assert spawns() == 2


async def test_failure_does_not_block_other_servers() -> None:
    user_id = await _create_user("auto5", "pw")
    await _add(user_id, name="Broken", command=BAD_COMMAND)
    await _add(user_id, name="Fixture")

    toolset = await _build(user_id)

    assert ECHO in toolset.bindings
    assert not any(name.startswith("mcp__broken__") for name in toolset.bindings)


async def test_exception_in_ensure_connected_never_breaks_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _create_user("auto6", "pw")
    bad_id = await _add(user_id, name="Exploding")
    await _add(user_id, name="Fixture")
    original = mcp_tools.ensure_connected

    async def flaky(uid: int, server_id: int, *rest: Any) -> Any:
        if server_id == bad_id:
            raise RuntimeError("boom secret-value")
        return await original(uid, server_id, *rest)

    monkeypatch.setattr(mcp_tools, "ensure_connected", flaky)

    toolset = await _build(user_id)

    assert ECHO in toolset.bindings
    assert not any(name.startswith("mcp__exploding__") for name in toolset.bindings)


async def test_concurrent_fan_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MCP_CONNECT_TIMEOUT", 1.5)
    user_id = await _create_user("auto7", "pw")
    first = await _add(user_id, name="Hang one", mode="hang")
    second = await _add(user_id, name="Hang two", mode="hang")

    started = time.monotonic()
    toolset = await _build(user_id)
    elapsed = time.monotonic() - started

    assert toolset.schemas == []
    # One hung connect costs the handshake timeout plus about 2 s of child-process teardown
    # (about 3.5 s here), so two sequential connects would take 7 s or more.
    assert elapsed < 5.5, f"connects ran sequentially ({elapsed:.2f}s)"
    for server_id in (first, second):
        assert mcp_client.has_recorded_failure(user_id, server_id)
        recorded = mcp_client._last_results[(user_id, server_id)]
        assert recorded.error_code == McpErrorCode.HANDSHAKE_TIMEOUT


async def test_disabled_server_never_auto_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _create_user("auto8", "pw")
    server_id = await _add(user_id, enabled=False)
    spawns = _count_spawns(monkeypatch)

    toolset = await _build(user_id)

    assert toolset.schemas == []
    assert spawns() == 0
    assert (user_id, server_id) not in mcp_client._sessions


async def test_other_users_servers_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    user_a = await _create_user("autoa", "pw")
    user_b = await _create_user("autob", "pw")
    server_b = await _add(user_b)
    spawns = _count_spawns(monkeypatch)

    toolset = await _build(user_a)

    assert toolset.schemas == []
    assert spawns() == 0
    assert (user_b, server_b) not in mcp_client._sessions
    assert (user_b, server_b) not in mcp_client._last_results


async def test_auto_connect_disabled_setting_keeps_old_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MCP_AUTO_CONNECT", False)
    user_id = await _create_user("auto10", "pw")
    server_id = await _add(user_id)
    spawns = _count_spawns(monkeypatch)

    toolset = await _build(user_id)
    assert toolset.schemas == []
    assert spawns() == 0

    result = await mcp_client.connect_server(
        user_id, server_id, sys.executable, [FIXTURE, "tools_extra"], None, None,
    )
    assert result.status == McpConnectionStatus.CONNECTED
    assert ECHO in (await _build(user_id)).bindings


async def test_dead_session_is_not_respawned(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _create_user("auto11", "pw")
    server_id = await _add(user_id)
    result = await mcp_client.connect_server(
        user_id, server_id, sys.executable, [FIXTURE, "tools_extra"], None, None,
    )
    assert result.status == McpConnectionStatus.CONNECTED
    # The owner task ends (as it does when the transport dies) while the handle stays registered.
    handle = mcp_client._sessions[(user_id, server_id)]
    handle.close_requested.set()
    await asyncio.wait_for(handle.closed.wait(), 10.0)
    assert not mcp_client.is_connected(user_id, server_id)
    spawns = _count_spawns(monkeypatch)

    outcome = await mcp_client.ensure_connected(
        user_id, server_id, sys.executable, [FIXTURE, "tools_extra"], None, None,
    )

    assert outcome.status == McpConnectionStatus.ERROR
    assert outcome.error_code == McpErrorCode.PROCESS_EXITED
    assert spawns() == 0
    assert mcp_client.has_recorded_failure(user_id, server_id)
