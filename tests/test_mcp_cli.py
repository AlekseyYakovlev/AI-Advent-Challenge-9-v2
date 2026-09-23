"""Tests for the standalone MCP list-tools CLI script."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPT: Path = REPO_ROOT / "scripts" / "mcp_list_tools.py"
FIXTURE: str = str(REPO_ROOT / "tests" / "fixtures" / "mcp_stdio_server.py")


def _load_script() -> ModuleType:
    """Load the CLI script as a module without executing main()."""
    spec = importlib.util.spec_from_file_location("mcp_list_tools", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli: ModuleType = _load_script()


async def test_run_success_prints_server_and_tools(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A healthy server prints serverInfo header and each tool with parameters."""
    code = await cli.run([sys.executable, FIXTURE, "ok"])
    captured = capsys.readouterr()

    assert code == 0
    assert "Server: fixture-server" in captured.out
    assert "Protocol: " in captured.out
    assert "Tools (2):" in captured.out
    assert "- echo: Echo the given text back." in captured.out
    param_lines = [ln for ln in captured.out.splitlines() if "text" in ln and "string" in ln]
    assert any("*" in ln for ln in param_lines)


async def test_run_bad_command_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing executable exits 1 with the code and Russian message on stderr."""
    code = await cli.run(["C:/definitely/not/here/nope.exe"])
    captured = capsys.readouterr()

    assert code == 1
    assert "COMMAND_NOT_FOUND" in captured.err
    assert "Команда не найдена" in captured.err
    assert captured.out == ""


async def test_run_process_exited_shows_stderr_tail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A server that dies at startup surfaces its stderr tail."""
    code = await cli.run([sys.executable, FIXTURE, "stderr_exit"])
    captured = capsys.readouterr()

    assert code == 1
    assert "PROCESS_EXITED" in captured.err
    assert "stderr line 25" in captured.err


async def test_run_without_args_is_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No command argument yields a usage message and exit code 2."""
    code = await cli.run([])
    captured = capsys.readouterr()

    assert code == 2
    assert "Usage" in captured.err


def test_describe_params_handles_types() -> None:
    """Parameter descriptions cover integer, union and untyped properties."""
    schema = {
        "properties": {
            "a": {"type": "integer", "description": "first"},
            "b": {"type": ["string", "null"]},
            "c": {},
        },
        "required": ["a"],
    }

    assert cli.describe_params(schema) == [
        ("a", "integer", True, "first"),
        ("b", "string|null", False, ""),
        ("c", "any", False, ""),
    ]


async def test_script_runs_as_subprocess() -> None:
    """The script runs from the repo root and stdout carries no JSON log lines."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/mcp_list_tools.py",
        sys.executable,
        FIXTURE,
        "ok",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    text = stdout.decode("utf-8")

    assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert text.splitlines()[0].startswith("Server:")
    assert "echo" in text
