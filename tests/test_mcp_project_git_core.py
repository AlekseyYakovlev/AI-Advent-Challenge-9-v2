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


# ------------------------------------------------------------- fixtures


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args,
        ],  # fmt: skip
        cwd=repo,
        env=_clean_env(),
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "agent").mkdir(parents=True)
    _git(repo, "init")
    (repo / "a.txt").write_text("one\n")
    (repo / "agent" / "ws.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    _git(repo, "branch", "feature")
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-am", "second")
    return repo


@pytest.fixture
def configured(git_repo: Path) -> Path:
    project_git.configure(os.path.realpath(git_repo), shutil.which("git") or "git", 18000, 10.0)
    return git_repo


# ------------------------------------------------------------- path gate


@pytest.fixture
def path_root(tmp_path: Path) -> str:
    root = tmp_path / "root"
    (root / "agent").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "agent" / "ws.py").write_text("x\n")
    return os.path.realpath(root)


def test_path_gate_accepts_variants(path_root: str) -> None:
    gate = project_git.resolve_inside_root
    assert gate(path_root, "agent/ws.py") == "agent/ws.py"
    if sys.platform == "win32":
        assert gate(path_root, "agent\\ws.py") == "agent/ws.py"
    assert gate(path_root, os.path.join(path_root, "agent", "ws.py")) == "agent/ws.py"
    assert gate(path_root, "agent/../shared") == "shared"
    assert gate(path_root, ".") == "."
    assert gate(path_root, "gone/deleted.py") == "gone/deleted.py"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_path_gate_is_case_insensitive_on_windows(path_root: str) -> None:
    assert project_git.resolve_inside_root(path_root, "AGENT/WS.PY") == "agent/ws.py"


@pytest.mark.parametrize("raw", ["../x", "..\\..\\Windows", "../../../etc/passwd"])
def test_path_gate_rejects_traversal(path_root: str, raw: str) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        project_git.resolve_inside_root(path_root, raw)
    assert "inside the repository root" in str(exc.value)
    assert "relative path" in str(exc.value)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
@pytest.mark.parametrize(
    "raw", ["C:\\Windows", "\\\\server\\share\\x", "//server/share/x", "\\Windows", "NUL"]
)
def test_path_gate_rejects_windows_escapes(path_root: str, raw: str) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        project_git.resolve_inside_root(path_root, raw)
    assert "inside the repository root" in str(exc.value)


def test_path_gate_rejects_absolute_outside(path_root: str, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x")
    with pytest.raises(project_git.ToolInputError) as exc:
        project_git.resolve_inside_root(path_root, str(outside))
    assert "inside the repository root" in str(exc.value)


@pytest.mark.parametrize("raw", ["--output=x", "-p"])
def test_path_gate_rejects_leading_dash(path_root: str, raw: str) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        project_git.resolve_inside_root(path_root, raw)
    assert 'must not start with "-"' in str(exc.value)


@pytest.mark.parametrize("raw", ["D:foo", ":(top)x", "file.txt:stream"])
def test_path_gate_rejects_colon_forms(path_root: str, raw: str) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        project_git.resolve_inside_root(path_root, raw)
    assert ":" in str(exc.value)


@pytest.mark.parametrize("raw", ["a\nb", "a\x00b", "a\rb", ""])
def test_path_gate_rejects_control_and_empty(path_root: str, raw: str) -> None:
    with pytest.raises(project_git.ToolInputError):
        project_git.resolve_inside_root(path_root, raw)


def test_path_gate_rejects_link_leaving_root(path_root: str, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = Path(path_root) / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("cannot create a symlink")
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        )
        if made.returncode != 0:
            pytest.skip("cannot create a symlink or junction")
    with pytest.raises(project_git.ToolInputError) as exc:
        project_git.resolve_inside_root(path_root, "link/x.txt")
    assert "inside the repository root" in str(exc.value)


# -------------------------------------------------------------- ref gate


@pytest.mark.parametrize(
    "raw",
    ["", "-p", "--upload-pack=x", "a" * 256, "a b", "a\tb", "a\nb", "a\\b"],
)
def test_ref_syntax_rejects(raw: str) -> None:
    with pytest.raises(project_git.ToolInputError):
        project_git.check_ref_syntax(raw)


@pytest.mark.parametrize("raw", ["HEAD~2", "origin/main", "v1.0", "@{-1}", "abc1234"])
def test_ref_syntax_accepts(raw: str) -> None:
    project_git.check_ref_syntax(raw)


async def test_resolve_ref_head_and_branch(configured: Path) -> None:
    head = await project_git.resolve_ref("HEAD")
    feature = await project_git.resolve_ref("feature")
    assert re.fullmatch(r"[0-9a-f]{40}", head)
    assert re.fullmatch(r"[0-9a-f]{40}", feature)
    assert head != feature


@pytest.mark.parametrize("raw", ["no-such-branch", "HEAD:a.txt"])
async def test_resolve_ref_unknown(configured: Path, raw: str) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        await project_git.resolve_ref(raw)
    assert "unknown ref" in str(exc.value) and "HEAD~N" in str(exc.value)


@pytest.mark.parametrize("raw", ["--upload-pack=x", "-p"])
async def test_resolve_ref_never_starts_git_for_option_shape(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    async def boom(args: list[str]) -> project_git.GitResult:
        raise AssertionError("git must not be started")

    monkeypatch.setattr(project_git, "run_git", boom)
    with pytest.raises(project_git.ToolInputError):
        await project_git.resolve_ref(raw)


async def test_resolve_ref_or_range(configured: Path) -> None:
    triple = await project_git.resolve_ref_or_range("main...feature")
    double = await project_git.resolve_ref_or_range("main..feature")
    single = await project_git.resolve_ref_or_range("HEAD")
    assert re.fullmatch(r"[0-9a-f]{40}\.\.\.[0-9a-f]{40}", triple)
    assert re.fullmatch(r"[0-9a-f]{40}\.\.[0-9a-f]{40}", double)
    assert re.fullmatch(r"[0-9a-f]{40}", single)
    for bad in ("main...", "..feature"):
        with pytest.raises(project_git.ToolInputError):
            await project_git.resolve_ref_or_range(bad)


# ------------------------------------------------------------ truncation


def test_fit_text_fits_unchanged() -> None:
    assert project_git.fit_text("S", "short", 18000) == "S\nshort"
    assert project_git.fit_text("S", "", 18000) == "S"


def test_fit_text_truncates_with_marker() -> None:
    body = "\n".join(f"line {i:05d}" for i in range(8000))
    result = project_git.fit_text("S", body, 2000)
    assert len(result) <= 2000
    assert result.startswith("S\n")
    marker = result.rsplit("\n", 1)[1]
    assert marker.startswith("[output truncated:") and marker.endswith("]")
    assert f"of {len(body):,} characters" in marker and "path" in marker
    kept = result[len("S\n") : -len(marker) - 1]
    assert marker.startswith(f"[output truncated: showing {len(kept):,} of")
    assert kept == body[: len(kept)]
    assert body[len(kept)] == "\n"


def test_fit_text_long_body_exact_numbers() -> None:
    result = project_git.fit_text("S", "x" * 60000, 2000)
    assert len(result) <= 2000
    assert "of 60,000 characters" in result


def test_fit_text_lower_bound() -> None:
    result = project_git.fit_text("S", "x" * 5000, 2000, lower_bound=True)
    assert "of more than 5,000" in result


@pytest.mark.parametrize("max_chars", [1000, 1500, 18000])
def test_fit_text_invariant_for_long_summary(max_chars: int) -> None:
    result = project_git.fit_text("s" * 5000, "b" * 70000, max_chars)
    assert len(result) <= max_chars


def test_compose_truncated_diff_order_and_size() -> None:
    stat = " alpha_stat.py | 3 +++\n 1 file changed, 3 insertions(+)"
    patch = "\n".join(f"+patch_line_{i:05d}" for i in range(6000))
    result = project_git.compose_truncated_diff("SUMMARYLINE", stat, patch, 2000)
    assert len(result) <= 2000
    positions = [
        result.index("SUMMARYLINE"),
        result.index("alpha_stat.py"),
        result.index("+patch_line_00000"),
        result.index("[output truncated"),
    ]
    assert positions == sorted(positions)
    assert f"of {len(patch):,} characters" in result


def test_compose_truncated_diff_shrinks_long_stat() -> None:
    files = [f" file{i:03d}.py | 1 +" for i in range(100)]
    totals = " 100 files changed, 100 insertions(+)"
    stat = "\n".join(files + [totals])
    result = project_git.compose_truncated_diff("S", stat, "p\n" * 9000, 2000)
    assert len(result) <= 2000
    assert "more files not shown" in result
    assert totals in result
    assert "file000.py" in result and "file099.py" not in result
