"""Orphan process cleanup on supervisor startup."""

import asyncio
import socket
import sys
import time

import httpx
import psutil
import pytest

from ui.supervisor import AgentSupervisor, find_pids_on_port


def _free_port() -> int:
    """Bind to port 0 and return the assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _start_orphan(port: int) -> asyncio.subprocess.Process:
    """Launch a stub server that occupies the agent port."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "tests._orphan_app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if find_pids_on_port(port):
            return proc
        await asyncio.sleep(0.2)
    proc.kill()
    await proc.wait()
    raise TimeoutError(f"Orphan server did not bind to port {port}")


async def _wait_for_agent(port: int, timeout: float) -> None:
    """Poll /health until the real agent responds."""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=1.0)
                if resp.status_code == 200 and resp.json().get("status") != "orphan":
                    return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.2)
    raise TimeoutError(f"Agent on port {port} did not become healthy")


@pytest.mark.asyncio
async def test_orphan_cleanup_kills_port_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Starting the supervisor should kill any process already on AGENT_PORT."""
    agent_port = _free_port()
    db_path = "test_orphan.db"
    monkeypatch.setenv("AGENT_PORT", str(agent_port))
    monkeypatch.setenv("DB_PATH", db_path)

    orphan = await _start_orphan(agent_port)
    orphan_pid = orphan.pid
    assert orphan_pid in find_pids_on_port(agent_port)

    supervisor = AgentSupervisor(agent_port, db_path)
    await supervisor.start()
    try:
        await _wait_for_agent(agent_port, timeout=20.0)
        assert not psutil.pid_exists(orphan_pid)
        assert orphan.returncode is not None
    finally:
        await supervisor.stop()
