"""Windows guard: a hard-killed Agent must not leave its MCP server child running."""

import asyncio
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows job-object behavior"
)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
HELPER: str = str(Path(__file__).parent / "fixtures" / "mcp_orphan_helper.py")
FIXTURE: str = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
CHILD_PID_PREFIX: str = "CHILD_PID="


async def _read_child_pid(proc: asyncio.subprocess.Process) -> int:
    """Read helper output until it reports the fixture child's pid; fail on ERROR or EOF."""
    assert proc.stdout is not None
    seen: list[str] = []
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            pytest.fail(f"helper exited before reporting a child pid: {seen[-10:]}")
        line = raw.decode("utf-8", errors="replace").strip()
        seen.append(line)
        if line.startswith(CHILD_PID_PREFIX):
            return int(line[len(CHILD_PID_PREFIX):])
        if line.startswith("ERROR"):
            pytest.fail(f"helper could not open the session: {line}")


def _verify_owned_child(pid: int, helper_pid: int, mode: str) -> float:
    """Prove the pid is the fixture server started by our helper; return its create_time."""
    child = psutil.Process(pid)
    cmdline = child.cmdline()
    assert FIXTURE in cmdline
    assert mode in cmdline
    assert helper_pid in [parent.pid for parent in child.parents()]
    return child.create_time()


def _is_gone(pid: int, create_time: float) -> bool:
    """Return whether that exact process (pid plus start time) no longer exists."""
    if not psutil.pid_exists(pid):
        return True
    try:
        return psutil.Process(pid).create_time() != create_time
    except psutil.NoSuchProcess:
        return True


async def _hard_terminate(proc: asyncio.subprocess.Process) -> None:
    """Stop the helper the way ui/supervisor.py stops the Agent: terminate, then kill."""
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()


@pytest.mark.parametrize("mode", ["ok", "stubborn"])
async def test_hard_terminated_agent_leaves_no_orphaned_mcp_child(
    tmp_path: Path, mode: str
) -> None:
    """The MCP SDK's kill-on-close job object takes the server down with its parent.

    ``ok`` exits by itself when its stdin closes; ``stubborn`` ignores that and would stay
    running for a minute, so only the job object can make it disappear promptly.
    """
    env = {
        **os.environ,
        "DB_PATH": str(tmp_path / "orphan.db"),
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        HELPER,
        mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    child_pid: int | None = None
    child_created: float | None = None
    try:
        child_pid = await asyncio.wait_for(_read_child_pid(proc), 30)
        child_created = _verify_owned_child(child_pid, proc.pid, mode)

        await _hard_terminate(proc)

        deadline = time.monotonic() + 5.0
        while not _is_gone(child_pid, child_created) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert _is_gone(child_pid, child_created), (
            f"fixture child {child_pid} survived the hard termination of its parent"
        )
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        # Only a pid verified above (same pid and start time) may be force-killed here.
        if (
            child_pid is not None
            and child_created is not None
            and not _is_gone(child_pid, child_created)
        ):
            psutil.Process(child_pid).kill()
