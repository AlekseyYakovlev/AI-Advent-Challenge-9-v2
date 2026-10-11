"""Tests that AgentSupervisor passes its host to the Agent subprocess."""

import inspect
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ui.supervisor import AgentSupervisor


async def _launch_and_get_args(
    supervisor: AgentSupervisor, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[object, ...]:
    """Launch with a mocked subprocess spawn and return the positional arguments."""
    fake_process = MagicMock()
    fake_process.pid = 1
    fake_process.returncode = None
    spawn = AsyncMock(return_value=fake_process)
    monkeypatch.setattr("ui.supervisor.asyncio.create_subprocess_exec", spawn)
    monkeypatch.chdir(tmp_path)
    try:
        await supervisor._launch_agent()
    finally:
        log_fd = getattr(supervisor, "_log_fd", None)
        if isinstance(log_fd, int):
            os.close(log_fd)
    return spawn.call_args.args


def test_default_host_is_loopback() -> None:
    """Two positional arguments still work and default to loopback."""
    assert AgentSupervisor(1234, "x.db").host == "127.0.0.1"


async def test_launch_passes_given_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The host given to the supervisor follows --host."""
    args = await _launch_and_get_args(
        AgentSupervisor(1234, "x.db", host="0.0.0.0"), monkeypatch, tmp_path
    )
    assert args[args.index("--host") + 1] == "0.0.0.0"


async def test_launch_defaults_to_loopback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without an explicit host the Agent binds to loopback."""
    args = await _launch_and_get_args(AgentSupervisor(1234, "x.db"), monkeypatch, tmp_path)
    assert args[args.index("--host") + 1] == "127.0.0.1"


def test_probes_stay_on_loopback() -> None:
    """Health and route probes keep using 127.0.0.1."""
    assert "http://127.0.0.1:" in inspect.getsource(AgentSupervisor._ping_health)
    assert "http://127.0.0.1:" in inspect.getsource(AgentSupervisor._has_websocket_route)
