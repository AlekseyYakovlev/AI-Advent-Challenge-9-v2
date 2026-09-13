"""Port cleanup before UI startup (run.py)."""

import inspect
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from run import cleanup_port, main
from ui.supervisor import find_pids_on_port

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    """Bind to port 0 and return the assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_dummy_server(port: int) -> subprocess.Popen[bytes]:
    """Start a dummy TCP server that occupies the given port."""
    script = (
        "import socket, time\n"
        "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"sock.bind(('127.0.0.1', {port}))\n"
        "sock.listen(1)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.pid in find_pids_on_port(port):
            return proc
        time.sleep(0.1)
    proc.kill()
    proc.wait()
    raise TimeoutError(f"Dummy server did not bind to port {port}")


def test_cleanup_port_kills_listener() -> None:
    """cleanup_port should terminate a Python process listening on the port."""
    port = _free_port()
    proc = _start_dummy_server(port)
    try:
        assert proc.pid in find_pids_on_port(port)
        cleanup_port(port)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.pid not in find_pids_on_port(port):
                break
            time.sleep(0.1)
        assert proc.pid not in find_pids_on_port(port)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cleanup_port_ignores_non_python() -> None:
    """cleanup_port should not terminate non-Python processes."""
    mock_proc = MagicMock()
    mock_proc.name.return_value = "node.exe"
    mock_conn = MagicMock()
    mock_conn.laddr.port = 9999
    mock_conn.status = psutil.CONN_LISTEN
    mock_conn.pid = 12345

    with patch("run.psutil.net_connections", return_value=[mock_conn]):
        with patch("run.psutil.Process", return_value=mock_proc):
            cleanup_port(9999)
            mock_proc.terminate.assert_not_called()
            mock_proc.kill.assert_not_called()


def test_run_py_no_asyncio_run_error() -> None:
    """run.main must not wrap uvicorn in asyncio.run()."""
    source = (REPO_ROOT / "run.py").read_text(encoding="utf-8")
    assert "asyncio.run(" not in source
    assert not inspect.iscoroutinefunction(main)

    with patch("run.cleanup_port"), patch("run.uvicorn.run") as mock_run:
        main()
    mock_run.assert_called_once()


def test_keyboard_interrupt_handling() -> None:
    """run.py should catch KeyboardInterrupt for a clean Ctrl+C shutdown."""
    source = inspect.getsource(main)
    assert "try:" in source
    assert "KeyboardInterrupt" in source

    with patch("run.cleanup_port"), patch(
        "run.uvicorn.run", side_effect=KeyboardInterrupt
    ):
        main()
