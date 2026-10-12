"""Agent process supervisor: orphan cleanup, healthcheck, restart."""

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from shared.config import BIND_LOOPBACK
from shared.logger import get_logger

logger = get_logger(__name__)

HEALTHCHECK_INTERVAL = 3.0
PROCESS_POLL_INTERVAL = 0.5
SHUTDOWN_WAIT_SECONDS = 3.0
AGENT_STARTUP_RETRIES = 20
AGENT_STARTUP_DELAY = 0.5
AGENT_STARTUP_CYCLES = 3
STARTUP_GRACE_SECONDS = 10.0


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

    def __init__(self, agent_port: int, db_path: str, host: str = BIND_LOOPBACK) -> None:
        self.agent_port = agent_port
        self.db_path = db_path
        self.host = host
        self.process: asyncio.subprocess.Process | None = None
        self._healthcheck_task: asyncio.Task[None] | None = None
        self._last_launch_time: float = 0.0

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

        log_dir = Path("logs")
        try:
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / "agent.log"
            log_fd = os.open(
                str(log_file),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
        except OSError as exc:
            logger.warning("failed_to_open_log_file", error=str(exc))
            log_fd = asyncio.subprocess.DEVNULL

        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "agent.main:app",
            "--host",
            self.host,
            "--port",
            str(self.agent_port),
            env=env,
            stdout=log_fd,
            stderr=log_fd,
        )
        self._log_fd = log_fd
        self._last_launch_time = time.monotonic()
        logger.info(
            "agent_started", pid=self.process.pid, port=self.agent_port, host=self.host
        )

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

        if hasattr(self, "_log_fd"):
            if isinstance(self._log_fd, int):
                try:
                    os.close(self._log_fd)
                except OSError:
                    pass
            self._log_fd = None

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

    async def _agent_routes_ready(self) -> bool:
        """Return True when the Agent answers the unauthenticated route probe with 401."""
        # The route table, including the chat WebSocket route, is registered when
        # agent.main is imported, and the 401 comes from the auth dependency of the
        # guarded route, so a 401 proves the app and its auth layer are mounted. The
        # probe sends no credentials and never reads the body.
        url = f"http://127.0.0.1:{self.agent_port}/debug/routes"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=2.0)
                return resp.status_code == 401
        except httpx.HTTPError:
            return False

    async def _wait_for_agent_ready(self) -> None:
        """Wait until /health is 200 and the guarded route probe is answered 401."""
        for cycle in range(AGENT_STARTUP_CYCLES):
            for attempt in range(AGENT_STARTUP_RETRIES):
                if await self._ping_health() and await self._agent_routes_ready():
                    logger.info(
                        "agent_ready",
                        port=self.agent_port,
                        attempt=attempt + 1,
                        cycle=cycle + 1,
                    )
                    return
                await asyncio.sleep(AGENT_STARTUP_DELAY)
            logger.warning(
                "agent_startup_incomplete",
                port=self.agent_port,
                cycle=cycle + 1,
            )
            await self._restart_agent()
        logger.error("agent_not_ready", port=self.agent_port, cycles=AGENT_STARTUP_CYCLES)

    async def _restart_agent(self) -> None:
        """Kill stale process state and launch a fresh agent."""
        logger.warning("agent_restarting", port=self.agent_port)
        await self._shutdown_agent()
        await asyncio.to_thread(kill_processes_on_port, self.agent_port)
        await self._launch_agent()

    async def _healthcheck_loop(self) -> None:
        """Restart a dead agent within half a second; ping /health every 3 seconds."""
        last_ping = time.monotonic()
        while True:
            try:
                await asyncio.sleep(PROCESS_POLL_INTERVAL)
                process_dead = (
                    self.process is None or self.process.returncode is not None
                )
                if not process_dead:
                    if time.monotonic() - last_ping < HEALTHCHECK_INTERVAL:
                        continue
                    last_ping = time.monotonic()
                    elapsed = time.monotonic() - self._last_launch_time
                    if elapsed < STARTUP_GRACE_SECONDS:
                        continue
                if process_dead or not await self._ping_health():
                    logger.warning("agent_unhealthy", port=self.agent_port)
                    await self._restart_agent()
                    await self._wait_for_agent_ready()
                    last_ping = time.monotonic()
            except asyncio.CancelledError:
                break
