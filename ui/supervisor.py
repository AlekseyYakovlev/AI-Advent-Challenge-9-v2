"""Agent process supervisor: orphan cleanup, healthcheck, restart."""

import asyncio
import os
import sys
from typing import Any

import httpx
import psutil

from shared.logger import get_logger

logger = get_logger(__name__)

HEALTHCHECK_INTERVAL = 3.0
SHUTDOWN_WAIT_SECONDS = 3.0
AGENT_STARTUP_RETRIES = 10
AGENT_STARTUP_DELAY = 0.5


def find_pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on the given TCP port."""
    pids: list[int] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                    pids.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return list(dict.fromkeys(pids))


def kill_processes_on_port(port: int) -> None:
    """Terminate any process bound to the given port."""
    for pid in find_pids_on_port(port):
        try:
            proc = psutil.Process(pid)
            logger.info("killing_orphan", pid=pid, port=port)
            proc.kill()
            proc.wait(timeout=SHUTDOWN_WAIT_SECONDS)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            continue


class AgentSupervisor:
    """Manage the agent subprocess lifecycle and health monitoring."""

    def __init__(self, agent_port: int, db_path: str) -> None:
        self.agent_port = agent_port
        self.db_path = db_path
        self.process: asyncio.subprocess.Process | None = None
        self._healthcheck_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Clean orphans, launch the agent, and begin health monitoring."""
        await asyncio.to_thread(kill_processes_on_port, self.agent_port)
        await self._launch_agent()
        await self._wait_for_agent_ready()
        self._healthcheck_task = asyncio.create_task(self._healthcheck_loop())

    async def stop(self) -> None:
        """Stop health monitoring and shut down the agent process."""
        if self._healthcheck_task is not None:
            self._healthcheck_task.cancel()
            try:
                await self._healthcheck_task
            except asyncio.CancelledError:
                pass
            self._healthcheck_task = None
        await self._shutdown_agent()

    async def _launch_agent(self) -> None:
        """Spawn the agent via uvicorn if not already running."""
        if self.process is not None and self.process.returncode is None:
            return
        env = {**os.environ, "DB_PATH": self.db_path}
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "agent.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.agent_port),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("agent_started", pid=self.process.pid, port=self.agent_port)

    async def _shutdown_agent(self) -> None:
        """Gracefully stop the agent: terminate, wait, then kill."""
        if self.process is None or self.process.returncode is not None:
            self.process = None
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=SHUTDOWN_WAIT_SECONDS)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        logger.info("agent_stopped", port=self.agent_port)
        self.process = None

    async def _ping_health(self) -> bool:
        """Return True when the agent /health endpoint responds OK."""
        if self.process is None or self.process.returncode is not None:
            return False
        url = f"http://127.0.0.1:{self.agent_port}/health"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=2.0)
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def _has_websocket_route(self) -> bool:
        """Return True when the agent exposes the chat WebSocket route."""
        url = f"http://127.0.0.1:{self.agent_port}/debug/routes"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code != 200:
                    return False
                routes = resp.json().get("routes", [])
                return any(
                    route.get("path") == "/ws/chat/{chat_id}"
                    for route in routes
                )
        except httpx.HTTPError:
            return False

    async def _wait_for_agent_ready(self) -> None:
        """Wait until health and WebSocket routes are available."""
        for attempt in range(AGENT_STARTUP_RETRIES):
            if await self._ping_health() and await self._has_websocket_route():
                logger.info("agent_ready", port=self.agent_port, attempt=attempt + 1)
                return
            await asyncio.sleep(AGENT_STARTUP_DELAY)
        logger.warning("agent_startup_incomplete", port=self.agent_port)
        await self._restart_agent()

    async def _restart_agent(self) -> None:
        """Kill stale process state and launch a fresh agent."""
        logger.warning("agent_restarting", port=self.agent_port)
        await self._shutdown_agent()
        await asyncio.to_thread(kill_processes_on_port, self.agent_port)
        await self._launch_agent()

    async def _healthcheck_loop(self) -> None:
        """Poll agent health every 3 seconds and restart on failure."""
        while True:
            try:
                await asyncio.sleep(HEALTHCHECK_INTERVAL)
                if not await self._ping_health():
                    await self._restart_agent()
            except asyncio.CancelledError:
                break
