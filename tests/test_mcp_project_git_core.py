"""Unit tests for the project git MCP server core: arguments, startup, runner, gates, truncation."""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from mcp_servers import project_git

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
MODULE_PATH: Path = REPO_ROOT / "mcp_servers" / "project_git.py"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


# ---------------------------------------------------------------- parse_args


def test_parse_args_defaults() -> None:
    args = project_git.parse_args([])
    assert args.root == os.getcwd()
    assert args.max_chars == 18000
    assert args.timeout == 10.0


def test_parse_args_explicit() -> None:
    args = project_git.parse_args(["--root=X", "--max-chars=2000", "--timeout=3"])
    assert (args.root, args.max_chars, args.timeout) == ("X", 2000, 3.0)


@pytest.mark.parametrize(
    "bad",
    [["--max-chars=999"], ["--max-chars=abc"], ["--timeout=0"], ["--timeout=-1"], ["--max=5"]],
)
def test_parse_args_rejects(bad: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        project_git.parse_args(bad)
    assert exc.value.code == 2
    assert capsys.readouterr().err


# ------------------------------------------------------------ env and argv


def test_git_env_strips_inherited_git_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_WORK_TREE", "/elsewhere")
    env = project_git.git_env()
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_PAGER"] == "cat"
    assert env["LC_ALL"] == "C"
    allowed = {"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT", "GIT_PAGER"}
    assert {k for k in env if k.upper().startswith("GIT_")} <= allowed


def test_base_argv_is_fixed() -> None:
    assert project_git.base_argv("G") == [
        "G", "--no-pager", "--literal-pathspecs", "-c", "core.quotepath=off",
        "-c", "core.fsmonitor=false", "-c", "color.ui=false",
    ]  # fmt: skip


# ------------------------------------------------------------------- runner


async def _run(code: str, timeout: float = 10.0) -> project_git.GitResult:
    return await project_git.run_command(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
        timeout=timeout,
        label="child",
    )


async def test_run_command_normalises_crlf() -> None:
    result = await _run("import sys; sys.stdout.buffer.write(b'hi\\r\\n')")
    assert result.returncode == 0
    assert result.text == "hi\n"
    assert result.capped is False


async def test_run_command_keeps_stderr_separate() -> None:
    result = await _run("import sys; sys.stderr.write('oops')")
    assert result.text == ""
    assert "oops" in result.stderr


async def test_run_command_caps_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_git, "HARD_CAP_BYTES", 1000)
    result = await _run("import sys; sys.stdout.write('x' * 500000); sys.stdout.flush()")
    assert result.capped is True
    assert len(result.text.encode()) <= 1000


async def test_run_command_timeout_kills_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(project_git.ToolInputError) as exc:
        await _run(code, timeout=2.0)
    assert time.monotonic() - started < 6
    assert "timed out" in str(exc.value) and "path" in str(exc.value)
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while psutil.pid_exists(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(grandchild)


# --------------------------------------------------------------- startup


def _stderr_lines(capsys: pytest.CaptureFixture[str]) -> tuple[str, list[str]]:
    captured = capsys.readouterr()
    return captured.out, captured.err.strip().splitlines()


def test_main_missing_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["p", f"--root={tmp_path / 'missing'}"])
    with pytest.raises(SystemExit) as exc:
        project_git.main()
    out, lines = _stderr_lines(capsys)
    assert exc.value.code == 1
    assert out == ""
    assert "does not exist" in lines[-1]


def test_main_git_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["p", f"--root={tmp_path}"])
    monkeypatch.setattr(project_git.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        project_git.main()
    out, lines = _stderr_lines(capsys)
    assert exc.value.code == 1
    assert out == ""
    assert "git was not found in PATH" in lines[-1]


@needs_git
def test_main_not_a_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["p", f"--root={tmp_path}"])
    with pytest.raises(SystemExit) as exc:
        project_git.main()
    out, lines = _stderr_lines(capsys)
    assert exc.value.code == 1
    assert out == ""
    assert "is not a git repository" in lines[-1] and "git init" in lines[-1]
    assert not (tmp_path / ".git").exists()


def test_module_source_is_clean() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "print(" not in source
    for line in source.splitlines():
        assert not re.match(r"\s*(import|from)\s+(shared|agent)\b", line)
