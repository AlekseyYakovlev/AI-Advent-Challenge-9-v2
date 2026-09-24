"""Entry point: start the UI server (agent is launched by the supervisor)."""

import asyncio
import logging

import psutil
import uvicorn

from shared.config import settings
from shared.database import bootstrap_admin_if_needed
from shared.logger import get_logger
from shared.runtime import check_python_version

logger = get_logger(__name__)


def cleanup_port(port: int) -> None:
    """Kill any Python process holding the given TCP port."""
    for conn in psutil.net_connections(kind="tcp"):
        if (
            conn.laddr
            and conn.laddr.port == port
            and conn.status == psutil.CONN_LISTEN
        ):
            try:
                proc = psutil.Process(conn.pid)
                if "python" in proc.name().lower():
                    logger.info("orphan_cleanup", pid=conn.pid, port=port)
                    print(f"[orphan-cleanup] Killing PID {conn.pid} on port {port}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass


def main() -> None:
    """Clean orphan ports and start the UI server."""
    check_python_version()
    cleanup_port(settings.UI_PORT)
    cleanup_port(settings.AGENT_PORT)

    from ui.main import app

    credentials = asyncio.run(bootstrap_admin_if_needed())
    if credentials is not None:
        username, password = credentials
        print("=" * 60)
        print("Bootstrap admin account created:")
        print(f"    username: {username}")
        print(f"    password: {password}")
        print("Save this now — it will not be shown again.")
        print("=" * 60)

    print(f"Starting UI on http://127.0.0.1:{settings.UI_PORT}")
    print(f"Agent will be auto-started on port {settings.AGENT_PORT}")
    print("Press Ctrl+C to stop.")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        uvicorn.run(app, host="127.0.0.1", port=settings.UI_PORT, reload=False)
    except KeyboardInterrupt:
        logger.info("ui_shutdown")


if __name__ == "__main__":
    main()
