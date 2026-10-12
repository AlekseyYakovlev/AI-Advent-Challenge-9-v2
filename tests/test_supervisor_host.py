"""Tests that AgentSupervisor passes its host to the Agent subprocess."""

import inspect
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

import ui.supervisor as supervisor_module
from agent.main import app
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
    assert "http://127.0.0.1:" in inspect.getsource(AgentSupervisor._agent_routes_ready)


PORT = 18999
ROUTES_URL = f"http://127.0.0.1:{PORT}/debug/routes"


async def _real_unauthenticated_status() -> int:
    """Return the status the real Agent app gives /debug/routes without credentials."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return (await c.get("/debug/routes")).status_code


@respx.mock
async def test_route_probe_accepts_401_only() -> None:
    """Only a 401 counts as ready; other statuses and connection errors do not."""
    supervisor = AgentSupervisor(PORT, "x.db")
    route = respx.get(ROUTES_URL)
    route.respond(401)
    assert await supervisor._agent_routes_ready() is True
    for code in (200, 404, 500):
        route.respond(code)
        assert await supervisor._agent_routes_ready() is False
    route.side_effect = httpx.ConnectError("down")
    assert await supervisor._agent_routes_ready() is False


@respx.mock
async def test_route_probe_matches_the_real_app() -> None:
    """The probe accepts exactly the status the real guarded route returns."""
    status = await _real_unauthenticated_status()
    respx.get(ROUTES_URL).respond(status)
    assert await AgentSupervisor(PORT, "x.db")._agent_routes_ready() is True


@respx.mock
async def test_wait_for_agent_ready_needs_no_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the real unauthenticated status the Agent is ready without a restart."""
    supervisor = AgentSupervisor(PORT, "x.db")
    respx.get(ROUTES_URL).respond(await _real_unauthenticated_status())
    restarts: list[int] = []

    async def healthy() -> bool:
        return True

    async def restart() -> None:
        restarts.append(1)

    monkeypatch.setattr(supervisor, "_ping_health", healthy)
    monkeypatch.setattr(supervisor, "_restart_agent", restart)
    await supervisor._wait_for_agent_ready()
    assert restarts == []


@respx.mock
async def test_wait_for_agent_ready_logs_when_never_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Agent that never becomes ready is restarted and reported in the log."""
    supervisor = AgentSupervisor(PORT, "x.db")
    respx.get(ROUTES_URL).respond(200)
    restarts: list[int] = []
    errors: list[str] = []

    class _Rec:
        def error(self, event: str, **kw: object) -> None:
            errors.append(event)

        def __getattr__(self, name: str):
            return lambda *a, **k: None

    async def healthy() -> bool:
        return True

    async def restart() -> None:
        restarts.append(1)

    monkeypatch.setattr(supervisor, "_ping_health", healthy)
    monkeypatch.setattr(supervisor, "_restart_agent", restart)
    monkeypatch.setattr(supervisor_module, "AGENT_STARTUP_CYCLES", 1)
    monkeypatch.setattr(supervisor_module, "AGENT_STARTUP_RETRIES", 1)
    monkeypatch.setattr(supervisor_module, "AGENT_STARTUP_DELAY", 0)
    monkeypatch.setattr(supervisor_module, "logger", _Rec())
    await supervisor._wait_for_agent_ready()
    assert restarts == [1]
    assert errors == ["agent_not_ready"]
