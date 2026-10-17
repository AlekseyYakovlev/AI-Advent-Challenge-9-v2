"""Tests for the five project git MCP tools: direct calls, stdio protocol and chat dispatch."""

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from types import ModuleType
from typing import Any

import psutil
import pytest

from agent import mcp_client, mcp_config
from agent.mcp_client import close_handle, connect_once_and_list, open_session
from agent.mcp_tools import build_mcp_toolset, call_mcp_tool, server_slug
from agent.schemas import McpConnectionStatus, McpErrorCode
from agent.tools import TOOL_REGISTRY, dispatch_tool_calls
from mcp_servers import project_git
from shared.database import async_session_factory
from shared.models import Chat
from tests.conftest import _create_user

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SERVER: str = str(REPO_ROOT / "mcp_servers" / "project_git.py")
EXPOSED_BRANCH: str = "mcp__local-git__git_current_branch"
EXPOSED_FILES: str = "mcp__local-git__git_tracked_files"
FIVE_TOOLS: list[str] = [
    "git_current_branch",
    "git_diff",
    "git_recent_commits",
    "git_status",
    "git_tracked_files",
]
READ_TOOLS: list[str] = ["find_usages", "read_file", "search_text"]


def _load_cli() -> ModuleType:
    """Load the list-tools CLI script as a module."""
    spec = importlib.util.spec_from_file_location(
        "mcp_list_tools", REPO_ROOT / "scripts" / "mcp_list_tools.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli: ModuleType = _load_cli()


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


def _make_repo(repo: Path, *, dirty: bool) -> Path:
    """Build a repository with branch main, a feature branch and optional local changes."""
    (repo / "agent").mkdir(parents=True)
    (repo / "папка").mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    (repo / "agent" / "ws.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "папка" / "файл.txt").write_text("привет\n", encoding="utf-8")
    (repo / "with space.txt").write_text("s\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-am", "second")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feat.txt").write_text("feature work\n", encoding="utf-8")
    _git(repo, "add", "feat.txt")
    _git(repo, "commit", "-m", "feature commit")
    _git(repo, "checkout", "main")
    if dirty:
        (repo / "a.txt").write_text("three\n", encoding="utf-8")
        (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        _git(repo, "add", "staged.txt")
        (repo / "untracked.txt").write_text("u\n", encoding="utf-8")
    return repo


def _configure(repo: Path, max_chars: int = 18000) -> None:
    project_git.configure(os.path.realpath(repo), shutil.which("git") or "git", max_chars, 10.0)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path / "repo", dirty=True)
    _configure(repo)
    return repo


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path / "clean", dirty=False)
    _configure(repo)
    return repo


@pytest.fixture
def empty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init")
    _configure(repo)
    return repo


async def _ok(call: Awaitable[str]) -> str:
    """Await a tool call and check it stays within the configured character budget."""
    result = await call
    assert result.strip() != ""
    assert len(result) <= project_git.get_config().max_chars
    return result


def _first_line(text: str) -> str:
    return text.splitlines()[0]


# ------------------------------------------------------------ registration


async def test_exactly_five_tools_with_local_description() -> None:
    tools = await project_git.server.list_tools()
    assert sorted(t.name for t in tools) == sorted(FIVE_TOOLS + READ_TOOLS)
    for tool in tools:
        assert (tool.description or "").startswith(
            "LOCAL working copy (not the GitHub remote):"
        )


# ------------------------------------------------------- git_current_branch


async def test_branch_main(git_repo: Path) -> None:
    assert await _ok(project_git.git_current_branch()) == "Branch: main"


async def test_branch_follows_checkout(git_repo: Path) -> None:
    _git(git_repo, "stash", "-u")
    _git(git_repo, "checkout", "feature")
    assert await _ok(project_git.git_current_branch()) == "Branch: feature"


async def test_branch_detached(git_repo: Path) -> None:
    _git(git_repo, "checkout", "--detach")
    text = await _ok(project_git.git_current_branch())
    assert text.startswith("Detached HEAD at ")
    assert len(text.split()[-1]) >= 4


async def test_branch_no_commits(empty_repo: Path) -> None:
    assert await _ok(project_git.git_current_branch()) == "Branch: main (no commits yet)"


# --------------------------------------------------------------- git_status


async def test_status_dirty(git_repo: Path) -> None:
    text = await _ok(project_git.git_status())
    assert _first_line(text) == "3 changed files"
    assert "## main" in text
    assert " M a.txt" in text
    assert "A  staged.txt" in text
    assert "?? untracked.txt" in text


async def test_status_clean(clean_repo: Path) -> None:
    text = await _ok(project_git.git_status())
    assert _first_line(text) == "Working tree clean"


async def test_status_keeps_cyrillic_name(clean_repo: Path) -> None:
    (clean_repo / "папка" / "файл.txt").write_text("изменено\n", encoding="utf-8")
    text = await _ok(project_git.git_status())
    assert _first_line(text) == "1 changed file"
    assert "папка/файл.txt" in text


async def test_status_in_empty_repo(empty_repo: Path) -> None:
    text = await _ok(project_git.git_status())
    assert _first_line(text) == "Working tree clean"


# ----------------------------------------------------------------- git_diff


async def test_diff_unstaged(git_repo: Path) -> None:
    text = await _ok(project_git.git_diff())
    assert _first_line(text) == "1 changed file"
    assert "a.txt" in text and "+three" in text
    assert "staged.txt" not in text


async def test_diff_staged(git_repo: Path) -> None:
    text = await _ok(project_git.git_diff(staged=True))
    assert _first_line(text) == "1 changed file"
    assert "staged.txt" in text and "a.txt" not in text


async def test_diff_against_head_shows_both(git_repo: Path) -> None:
    text = await _ok(project_git.git_diff(ref="HEAD"))
    assert _first_line(text) == "2 changed files"
    assert "a.txt" in text and "staged.txt" in text


async def test_diff_path_filter(git_repo: Path) -> None:
    text = await _ok(project_git.git_diff(ref="HEAD", path="a.txt"))
    assert _first_line(text) == "1 changed file"
    assert "staged.txt" not in text


async def test_diff_ref_range(git_repo: Path) -> None:
    text = await _ok(project_git.git_diff(ref="main...feature"))
    assert "feat.txt" in text and "+feature work" in text
    assert "a.txt" not in text


async def test_diff_clean_is_no_differences(clean_repo: Path) -> None:
    assert await _ok(project_git.git_diff()) == "No differences"


async def test_diff_staged_with_range_rejected(git_repo: Path) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        await project_git.git_diff(staged=True, ref="main...feature")
    assert "staged" in str(exc.value)


async def test_diff_truncates_large_patch(clean_repo: Path) -> None:
    _configure(clean_repo, 2000)
    big = "\n".join(f"line {i:05d} padding padding" for i in range(2500)) + "\n"
    assert len(big) > 60000
    (clean_repo / "a.txt").write_text(big, encoding="utf-8")
    text = await _ok(project_git.git_diff())
    lines = text.splitlines()
    assert len(text) <= 2000
    assert "changed" in lines[0]
    assert "a.txt" in text
    assert "+line 00000" in text
    assert "output truncated" in text
    assert "path" in text.splitlines()[-1]
    assert "showing" in lines[-1] and " of " in lines[-1]


async def test_diff_runs_at_most_two_commands(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(clean_repo, 2000)
    (clean_repo / "a.txt").write_text("y\n" * 5000, encoding="utf-8")
    seen: list[list[str]] = []
    real = project_git.run_git

    async def spy(args: list[str]) -> project_git.GitResult:
        seen.append(args)
        return await real(args)

    monkeypatch.setattr(project_git, "run_git", spy)
    await _ok(project_git.git_diff())
    assert len(seen) == 2
    assert all(cmd[0] == "diff" for cmd in seen)
    assert all("--no-ext-diff" in cmd and "--no-textconv" in cmd for cmd in seen)


# ------------------------------------------------------------ git_tracked_files


async def test_tracked_files_lists_all(git_repo: Path) -> None:
    text = await _ok(project_git.git_tracked_files())
    assert _first_line(text) == "5 tracked files"
    for name in ("a.txt", "agent/ws.py", "папка/файл.txt", "with space.txt", "staged.txt"):
        assert name in text
    assert "untracked.txt" not in text


async def test_tracked_files_counts_match_git(git_repo: Path) -> None:
    out = subprocess.run(
        ["git", "ls-files"], cwd=git_repo, env=_clean_env(), capture_output=True, check=True
    ).stdout.decode("utf-8")
    expected = len([line for line in out.splitlines() if line])
    text = await _ok(project_git.git_tracked_files())
    assert _first_line(text).startswith(f"{expected} tracked file")


async def test_tracked_files_with_path(git_repo: Path) -> None:
    text = await _ok(project_git.git_tracked_files(path="папка"))
    assert _first_line(text) == "1 tracked file under папка"
    assert "папка/файл.txt" in text
    assert "a.txt" not in text


async def test_tracked_files_missing_path(git_repo: Path) -> None:
    text = await _ok(project_git.git_tracked_files(path="docs-that-do-not-exist"))
    assert text == "No tracked files under docs-that-do-not-exist"


async def test_tracked_files_glob_not_expanded(git_repo: Path) -> None:
    text = await _ok(project_git.git_tracked_files(path="*.txt"))
    assert text == "No tracked files under *.txt"


async def test_tracked_files_empty_repo(empty_repo: Path) -> None:
    assert await _ok(project_git.git_tracked_files()) == "No tracked files"


def _add_bulk_files(repo: Path, count: int = 400) -> None:
    bulk = repo / "bulk"
    bulk.mkdir()
    for index in range(count):
        (bulk / f"file_{index:04d}.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "bulk")


async def test_tracked_files_truncated_with_table(clean_repo: Path) -> None:
    _add_bulk_files(clean_repo)
    _configure(clean_repo, 2000)
    text = await _ok(project_git.git_tracked_files())
    lines = text.splitlines()
    assert len(text) <= 2000
    assert "404" in lines[0]
    assert "Files per top-level folder:" in text
    assert "bulk/ 400" in text
    assert text.index("Files per top-level folder:") < text.index("bulk/file_0000.txt")
    assert "output truncated" in lines[-1]
    assert " of 404 files" in lines[-1]
    assert "showing" in lines[-1]


async def test_tracked_files_path_gets_under_limit(clean_repo: Path) -> None:
    _add_bulk_files(clean_repo, 30)
    _configure(clean_repo, 2000)
    text = await _ok(project_git.git_tracked_files(path="bulk"))
    assert _first_line(text) == "30 tracked files under bulk"
    assert "output truncated" not in text


# ------------------------------------------------------ git_recent_commits


async def test_commits_default(git_repo: Path) -> None:
    text = await _ok(project_git.git_recent_commits())
    lines = text.splitlines()
    assert lines[0] == "2 commits"
    for line in lines[1:]:
        fields = line.split("\t")
        assert len(fields) == 4
        assert len(fields[0]) >= 4
        assert len(fields[1]) == 10 and fields[1][4] == "-"
        assert fields[2] == "T"
    assert lines[1].endswith("second") and lines[2].endswith("first")


async def test_commits_count_one(git_repo: Path) -> None:
    text = await _ok(project_git.git_recent_commits(count=1))
    assert text.splitlines()[0] == "1 commit"
    assert len(text.splitlines()) == 2


async def test_commits_count_clamped(git_repo: Path) -> None:
    text = await _ok(project_git.git_recent_commits(count=500))
    assert "count limited to 50" in _first_line(text)


async def test_commits_count_zero_is_one(git_repo: Path) -> None:
    text = await _ok(project_git.git_recent_commits(count=0))
    assert _first_line(text) == "1 commit"


async def test_commits_ref(git_repo: Path) -> None:
    text = await _ok(project_git.git_recent_commits(ref="feature"))
    assert "feature commit" in text
    assert _first_line(text) == "3 commits"


async def test_commits_path(git_repo: Path) -> None:
    text = await _ok(project_git.git_recent_commits(path="a.txt"))
    assert _first_line(text) == "2 commits"
    text = await _ok(project_git.git_recent_commits(path="agent/ws.py"))
    assert _first_line(text) == "1 commit"


async def test_commits_no_history(empty_repo: Path) -> None:
    assert await _ok(project_git.git_recent_commits()) == "No commits yet"


async def test_commits_unmatched_path(git_repo: Path) -> None:
    assert await _ok(project_git.git_recent_commits(path="nothing-here")) == "No commits found"


# -------------------------------------------------------------- rejections


@pytest.mark.parametrize(
    "call",
    [
        lambda: project_git.git_diff(path="../x"),
        lambda: project_git.git_tracked_files(path="--output=x"),
        lambda: project_git.git_recent_commits(ref="-p"),
        lambda: project_git.git_diff(ref="no-such-ref"),
    ],
)
async def test_bad_arguments_raise(git_repo: Path, call: Any) -> None:
    with pytest.raises(project_git.ToolInputError):
        await call()


async def test_unknown_ref_message(git_repo: Path) -> None:
    with pytest.raises(project_git.ToolInputError) as exc:
        await project_git.git_diff(ref="no-such-ref")
    assert "unknown ref" in str(exc.value)


@pytest.mark.skipif(sys.platform != "win32", reason="drive paths are Windows-only")
async def test_drive_path_rejected(git_repo: Path) -> None:
    with pytest.raises(project_git.ToolInputError):
        await project_git.git_recent_commits(path="C:\\Windows")


async def test_option_smuggling_never_starts_git(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(args: list[str]) -> project_git.GitResult:
        raise AssertionError("git must not start")

    monkeypatch.setattr(project_git, "run_git", boom)
    with pytest.raises(project_git.ToolInputError):
        await project_git.git_diff(path="--output=x")
    with pytest.raises(project_git.ToolInputError):
        await project_git.git_recent_commits(ref="--upload-pack=x")
    with pytest.raises(project_git.ToolInputError):
        await project_git.git_tracked_files(path="--output=x")


# ---------------------------------------------------------- stdio protocol


def _text(result: Any) -> str:
    return "".join(getattr(item, "text", "") for item in result.content)


@contextlib.asynccontextmanager
async def _session(
    args: list[str], env: dict[str, str] | None = None, cwd: str | None = None
) -> AsyncIterator[Any]:
    handle, result = await open_session(sys.executable, [SERVER, *args], env, cwd)
    assert handle is not None, (result.error_code, result.error_message, result.stderr_tail)
    try:
        yield handle.session
    finally:
        await close_handle(handle)


def _project_git_pids() -> set[int]:
    pids: set[int] = set()
    for proc in psutil.process_iter(["cmdline"]):
        cmd = proc.info.get("cmdline") or []
        if any(part.endswith("project_git.py") for part in cmd):
            pids.add(proc.pid)
    return pids


async def test_cli_lists_five_tools(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli.run([sys.executable, SERVER, f"--root={git_repo}"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Server: local-git" in out
    assert "Tools (8):" in out
    for name in FIVE_TOOLS:
        assert name in out


async def test_stdio_branch_is_live(git_repo: Path) -> None:
    async with _session([f"--root={git_repo}"]) as session:
        first = await session.call_tool("git_current_branch", {})
        assert not first.isError and _text(first) == "Branch: main"
        _git(git_repo, "stash", "-u")
        _git(git_repo, "checkout", "feature")
        second = await session.call_tool("git_current_branch", {})
        assert _text(second) == "Branch: feature"


async def test_stdio_path_outside_root(git_repo: Path) -> None:
    async with _session([f"--root={git_repo}"]) as session:
        result = await session.call_tool("git_diff", {"path": "../x"})
    text = _text(result)
    assert result.isError
    assert "Error executing tool git_diff" in text
    assert "inside the repository root" in text and "relative path" in text


async def test_stdio_ref_starting_with_dash(git_repo: Path) -> None:
    async with _session([f"--root={git_repo}"]) as session:
        result = await session.call_tool("git_recent_commits", {"ref": "-p"})
    assert result.isError and "must not start with" in _text(result)


async def test_stdio_unknown_ref(git_repo: Path) -> None:
    async with _session([f"--root={git_repo}"]) as session:
        result = await session.call_tool("git_diff", {"ref": "HEAD:a.txt"})
    assert result.isError and "unknown ref" in _text(result)


async def test_stdio_wrong_argument_type(git_repo: Path) -> None:
    async with _session([f"--root={git_repo}"]) as session:
        result = await session.call_tool("git_recent_commits", {"count": "many"})
    assert result.isError


async def test_stdio_truncated_diff(clean_repo: Path) -> None:
    big = "\n".join(f"line {i:05d} padding padding" for i in range(2500)) + "\n"
    (clean_repo / "a.txt").write_text(big, encoding="utf-8")
    async with _session([f"--root={clean_repo}", "--max-chars=2000"]) as session:
        result = await session.call_tool("git_diff", {})
    text = _text(result)
    assert not result.isError
    assert len(text) <= 2000
    assert "output truncated" in text


async def test_stdio_root_from_working_directory(git_repo: Path) -> None:
    async with _session([], cwd=str(git_repo)) as session:
        result = await session.call_tool("git_current_branch", {})
    assert _text(result) == "Branch: main"


async def test_stdio_ignores_inherited_git_dir(git_repo: Path, tmp_path: Path) -> None:
    other = _make_repo(tmp_path / "other", dirty=False)
    _git(other, "checkout", "feature")
    env = {"GIT_DIR": str(other / ".git")}
    async with _session([f"--root={git_repo}"], env=env) as session:
        result = await session.call_tool("git_current_branch", {})
    assert _text(result) == "Branch: main"


async def test_startup_fails_for_missing_root(tmp_path: Path) -> None:
    result = await connect_once_and_list(
        sys.executable, [SERVER, f"--root={tmp_path / 'missing'}"], None, None, 15
    )
    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.PROCESS_EXITED
    assert "does not exist" in (result.stderr_tail or "")


async def test_startup_fails_outside_repository(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = await connect_once_and_list(
        sys.executable, [SERVER, f"--root={plain}"], None, None, 15
    )
    assert result.status == McpConnectionStatus.ERROR
    assert result.error_code == McpErrorCode.PROCESS_EXITED
    tail = result.stderr_tail or ""
    assert "is not a git repository" in tail and "git init" in tail


def test_server_slug_is_unchanged() -> None:
    assert server_slug("local-git", 1) == "local-git"


# --------------------------------------------------------------- app path


async def _create_connected_server(
    repo: Path, username: str, extra: list[str] | None = None
) -> tuple[int, int, int]:
    server_args = [SERVER, f"--root={repo}", *(extra or [])]
    user_id = await _create_user(username, "pw")
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, "local-git", sys.executable, server_args, {}, None, True
        )
        server_id = row.id
        chat = Chat(title="Local git", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id
    connection = await mcp_client.connect_server(
        user_id, server_id, sys.executable, server_args, None, None
    )
    assert connection.status == McpConnectionStatus.CONNECTED, connection.error_message
    return user_id, server_id, chat_id


async def test_chat_dispatch_returns_branch(git_repo: Path) -> None:
    protected = _project_git_pids()
    user_id, server_id, chat_id = await _create_connected_server(git_repo, "gitdispatch")
    try:
        async with async_session_factory() as session:
            toolset = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
            assert EXPOSED_BRANCH in toolset.bindings
            call = {
                "id": "call_git_1",
                "type": "function",
                "function": {"name": EXPOSED_BRANCH, "arguments": ""},
            }
            results = await dispatch_tool_calls(
                session, user_id, chat_id, [call], mcp_bindings=toolset.bindings
            )
        (result,) = results
        assert result["ok"] is True, result["content"]
        assert "Branch: main" in json.loads(result["content"])["content"]
    finally:
        await mcp_client.disconnect_server(user_id, server_id)
    assert not (_project_git_pids() - protected)


async def test_app_cap_not_reached_for_long_listing(clean_repo: Path) -> None:
    _add_bulk_files(clean_repo)
    user_id, server_id, _chat_id = await _create_connected_server(
        clean_repo, "gitcap", ["--max-chars=2000"]
    )
    try:
        async with async_session_factory() as session:
            toolset = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
        binding = toolset.bindings[EXPOSED_FILES]
        outcome = await call_mcp_tool(binding, {})
    finally:
        await mcp_client.disconnect_server(user_id, server_id)
    assert outcome["ok"] is True
    assert outcome["truncated"] is False
    assert len(outcome["text"]) <= 2000
    assert "output truncated" in outcome["text"]
