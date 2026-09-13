"""Port cleanup before UI startup (run.py)."""

import asyncio
import socket
import sys
import time
from unittest.mock import MagicMock, patch

import psutil
import pytest

from run import cleanup_port
from ui.supervisor import find_pids_on_port


def _free_port() -> int:
    """Bind to port 0 and return the assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _start_listener(port: int) -> asyncio.subprocess.Process:
    """Launch a stub server that occupies the given port."""
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
    raise TimeoutError(f"Listener did not bind to port {port}")


@pytest.mark.asyncio
async def test_cleanup_port_kills_listener() -> None:
    """cleanup_port should terminate a Python process listening on the port."""
    port = _free_port()
    proc = await _start_listener(port)
    try:
        assert proc.pid in find_pids_on_port(port)
        await cleanup_port(port)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.pid not in find_pids_on_port(port):
                break
            await asyncio.sleep(0.2)
        assert proc.pid not in find_pids_on_port(port)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.asyncio
async def test_cleanup_port_ignores_non_python() -> None:
    """cleanup_port should not terminate non-Python processes."""
    mock_proc = MagicMock()
    mock_proc.name.return_value = "node.exe"
    mock_conn = MagicMock()
    mock_conn.laddr.port = 9999
    mock_conn.status = psutil.CONN_LISTEN
    mock_conn.pid = 12345

    with patch("run.psutil.net_connections", return_value=[mock_conn]):
        with patch("run.psutil.Process", return_value=mock_proc):
            await cleanup_port(9999)
            mock_proc.terminate.assert_not_called()
            mock_proc.kill.assert_not_called()
