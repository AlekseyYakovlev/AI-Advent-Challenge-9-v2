"""Entry point: start the UI server (agent is launched by the supervisor)."""

import logging

import psutil
import uvicorn

from shared.config import settings
from shared.logger import get_logger

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
    cleanup_port(settings.UI_PORT)
    cleanup_port(settings.AGENT_PORT)

    from ui.main import app

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
