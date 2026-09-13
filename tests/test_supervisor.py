"""Agent supervisor restart tests."""

import asyncio
import socket
import sys
import time

import httpx
import pytest

from ui.main import app as ui_app
from ui.supervisor import AgentSupervisor


def _free_port() -> int:
    """Bind to port 0 and return the assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_agent(port: int, timeout: float) -> None:
    """Poll /health until the agent responds or timeout expires."""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=1.0)
                if resp.status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.2)
    raise TimeoutError(f"Agent on port {port} did not become healthy")


@pytest.mark.asyncio
async def test_agent_restarts_within_5_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Killing the agent should trigger a restart in under 5 seconds."""
    agent_port = _free_port()
    db_path = "test_supervisor.db"
    monkeypatch.setenv("AGENT_PORT", str(agent_port))
    monkeypatch.setenv("DB_PATH", db_path)

    supervisor = AgentSupervisor(agent_port, db_path)
    ui_app.state.supervisor = supervisor
    await supervisor.start()
    try:
        await _wait_for_agent(agent_port, timeout=20.0)
        assert supervisor.process is not None
        supervisor.process.kill()
        await supervisor.process.wait()

        start = time.monotonic()
        await _wait_for_agent(agent_port, timeout=5.0)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0
        assert supervisor.process is not None
        assert supervisor.process.returncode is None
    finally:
        await supervisor.stop()
