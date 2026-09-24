"""Helper process that opens an MCP session and then idles, so a test can hard-kill it.

Usage: ``mcp_orphan_helper.py [fixture_mode]`` (default ``ok``). It prints
``CHILD_PID=<pid>`` for the fixture server it spawned and never closes the session
gracefully. Its stdout line is the protocol with the launching test.
"""

import asyncio
import sys
from pathlib import Path

import psutil

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent import mcp_client  # noqa: E402

FIXTURE: str = str(Path(__file__).parent / "mcp_stdio_server.py")
HANDSHAKE_TIMEOUT_SECONDS: float = 15.0


def _fixture_child_pids() -> set[int]:
    """Return the pids of this process's descendants whose command line runs the fixture."""
    pids: set[int] = set()
    for child in psutil.Process().children(recursive=True):
        try:
            if FIXTURE in child.cmdline():
                pids.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


async def main() -> int:
    """Open one session, report the child's pid, then wait forever."""
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    before = _fixture_child_pids()
    handle, result = await mcp_client.open_session(
        sys.executable, [FIXTURE, mode], None, None, HANDSHAKE_TIMEOUT_SECONDS
    )
    if handle is None:
        sys.stdout.write(f"ERROR {result.error_code}\n")
        sys.stdout.flush()
        return 1

    spawned = _fixture_child_pids() - before
    if len(spawned) != 1:
        sys.stdout.write(f"ERROR expected one new fixture child, found {sorted(spawned)}\n")
        sys.stdout.flush()
        return 1

    sys.stdout.write(f"CHILD_PID={spawned.pop()}\n")
    sys.stdout.flush()
    await asyncio.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
