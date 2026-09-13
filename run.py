"""Entry point: start the UI server (agent is launched by the supervisor)."""

import asyncio

import psutil
import uvicorn

from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)


async def cleanup_port(port: int) -> None:
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
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass


async def main() -> None:
    """Clean orphan ports and start the UI server."""
    await cleanup_port(settings.UI_PORT)
    await cleanup_port(settings.AGENT_PORT)

    from ui.main import app

    uvicorn.run(app, host="127.0.0.1", port=settings.UI_PORT, reload=False)


if __name__ == "__main__":
    asyncio.run(main())
