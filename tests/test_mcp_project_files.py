"""Tests for the file read, search and write tools of the project git server."""

import contextlib
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Iterator
from pathlib import Path
from typing import Any

import pytest

from agent.mcp_client import close_handle, open_session
from mcp_servers import project_git
from mcp_servers.project_git import ToolInputError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

USAGE_SOURCE: str = (
    "from m import target\n"
    "\n"
    "\n"
    "def target():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def caller(target):\n"
    "    target()\n"
    "    obj.target()\n"
    "    x = target\n"
    "    # target in a comment\n"
    '    s = "target in a string"\n'
    "    return x, s\n"
)


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


def _make_files_repo(repo: Path) -> Path:
    """Build a repository with tracked, ignored and untracked files."""
    (repo / "pkg").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "notes").mkdir()
    _git(repo, "init")
    (repo / "a.py").write_text("needle = 1\nprint(needle)\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text("def f():\n    return 'needle'\n", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("# Guide\nneedle here\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n*.db\nbuild/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    (repo / ".env").write_text("needle SECRET=1\n", encoding="utf-8")
    (repo / "app.db").write_text("needle db\n", encoding="utf-8")
    (repo / "notes" / "new.md").write_text("untracked needle\n", encoding="utf-8")
    return repo


def _configure(repo: Path, max_chars: int = 18000) -> None:
    project_git.configure(os.path.realpath(repo), shutil.which("git") or "git", max_chars, 10.0)


@pytest.fixture
def files_repo(tmp_path: Path) -> Path:
    repo = _make_files_repo(tmp_path / "repo")
    _configure(repo)
    return repo


async def _ok(call: Awaitable[str]) -> str:
    result = await call
    assert result.strip() != ""
    assert len(result) <= project_git.get_config().max_chars
    return result


async def test_tools_registered_with_local_description(files_repo: Path) -> None:
    tools = {t.name: t for t in await project_git.server.list_tools()}
    for name in ("read_file", "search_text", "find_usages"):
        assert name in tools
        assert (tools[name].description or "").startswith("LOCAL working copy")


async def test_read_file_whole(files_repo: Path) -> None:
    out = await _ok(project_git.read_file("a.py"))
    assert out.splitlines()[0] == "a.py: lines 1-2 of 2"
    assert "     1\tneedle = 1" in out


async def test_read_file_range(files_repo: Path) -> None:
    out = await _ok(project_git.read_file("a.py", 2, 2))
    assert out.splitlines()[0] == "a.py: lines 2-2 of 2"
    assert "print(needle)" in out
    assert "needle = 1" not in out


async def test_read_file_default_window_and_marker(files_repo: Path) -> None:
    (files_repo / "big.txt").write_text("".join(f"l{i}\n" for i in range(400)), encoding="utf-8")
    out = await _ok(project_git.read_file("big.txt"))
    assert out.splitlines()[0] == "big.txt: lines 1-300 of 400"
    assert "start_line=301" in out


async def test_read_file_cut_by_max_chars(files_repo: Path) -> None:
    _configure(files_repo, max_chars=1000)
    (files_repo / "wide.txt").write_text("".join(f"{'x' * 60}{i}\n" for i in range(100)), encoding="utf-8")
    out = await _ok(project_git.read_file("wide.txt"))
    assert "[output truncated" in out
    assert "start_line=" in out


async def test_read_file_untracked_visible(files_repo: Path) -> None:
    out = await _ok(project_git.read_file("notes/new.md"))
    assert "untracked needle" in out


async def test_read_file_crlf_has_no_carriage_return(files_repo: Path) -> None:
    (files_repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    out = await _ok(project_git.read_file("crlf.txt"))
    assert "\r" not in out
    assert "lines 1-2 of 2" in out


async def test_read_file_empty(files_repo: Path) -> None:
    (files_repo / "empty.txt").write_text("", encoding="utf-8")
    assert (await _ok(project_git.read_file("empty.txt"))).startswith("Empty file")


async def test_read_file_ignored_refused(files_repo: Path) -> None:
    for name in (".env", "app.db"):
        with pytest.raises(ToolInputError, match="git ignores it"):
            await project_git.read_file(name)


async def test_read_file_binary_refused(files_repo: Path) -> None:
    (files_repo / "bin.dat").write_bytes(b"ab\x00cd")
    with pytest.raises(ToolInputError, match="binary"):
        await project_git.read_file("bin.dat")


@pytest.mark.parametrize("path", ["missing.py", "pkg", ".", "../x", "", "-x"])
async def test_read_file_bad_path(files_repo: Path, path: str) -> None:
    with pytest.raises(ToolInputError):
        await project_git.read_file(path)


@pytest.mark.parametrize("start,end", [(0, None), (3, None), (2, 1)])
async def test_read_file_bad_range(files_repo: Path, start: int, end: int | None) -> None:
    with pytest.raises(ToolInputError):
        await project_git.read_file("a.py", start, end)


async def test_search_text_substring(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("needle"))
    assert out.splitlines()[0] == "5 matches in 4 files"
    assert "a.py:1: needle = 1" in out
    assert "notes/new.md:1:" in out


async def test_search_text_never_shows_ignored(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("needle"))
    assert "SECRET" not in out
    assert ".env" not in out
    assert "app.db" not in out


async def test_search_text_two_files(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("needle", glob="*.py"))
    assert out.splitlines()[0] == "3 matches in 2 files"


async def test_search_text_regex(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("ne+dle", regex=True, glob="*.py"))
    assert out.splitlines()[0].endswith("for regex")


async def test_search_text_glob_limits(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("needle", glob="*.md"))
    paths = {line.split(":")[0] for line in out.splitlines()[1:]}
    assert paths <= {"docs/guide.md", "notes/new.md"}


async def test_search_text_ignore_case(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("GUIDE", ignore_case=True))
    assert "docs/guide.md" in out


async def test_search_text_no_match(files_repo: Path) -> None:
    out = await _ok(project_git.search_text("zzzqqq"))
    assert out.startswith("No matches for")


async def test_search_text_invalid_regex(files_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="regular expression"):
        await project_git.search_text("fo(", regex=True)


@pytest.mark.parametrize(
    "pattern,glob",
    [
        ("a\nb", None),
        ("x" * 201, None),
        ("", None),
        ("needle", ":(exclude)x"),
        ("needle", "../x"),
        ("needle", "-x"),
        ("needle", "/abs"),
    ],
)
async def test_search_text_bad_arguments(files_repo: Path, pattern: str, glob: str | None) -> None:
    with pytest.raises(ToolInputError):
        await project_git.search_text(pattern, glob=glob)


async def test_search_text_hit_cap(files_repo: Path) -> None:
    (files_repo / "many.txt").write_text("".join("hit\n" for _ in range(250)), encoding="utf-8")
    _configure(files_repo, max_chars=20000)
    out = await _ok(project_git.search_text("hit", glob="many.txt"))
    assert out.splitlines()[0] == "250 matches in 1 file"
    assert sum(1 for line in out.splitlines() if line.startswith("many.txt:")) == 200
    assert "matches" in out.splitlines()[-1]


async def test_find_usages_kinds(files_repo: Path) -> None:
    (files_repo / "u.py").write_text(USAGE_SOURCE, encoding="utf-8")
    out = await _ok(project_git.find_usages("target", "u.py"))
    first = out.splitlines()[0]
    assert first.startswith("6 usages of target in 1 file")
    assert "(def 1, import 1, call 2, ref 1, param 1)" in first
    for kind in ("def", "import", "call", "ref", "param"):
        assert f": {kind}:" in out
    assert "comment" not in out
    assert "string" not in out


async def test_find_usages_assignment(files_repo: Path) -> None:
    (files_repo / "s.py").write_text("target = 1\nself.target = 2\n", encoding="utf-8")
    out = await _ok(project_git.find_usages("target", "s.py"))
    assert out.count(": assign:") == 2


async def test_find_usages_syntax_error_skipped(files_repo: Path) -> None:
    (files_repo / "bad.py").write_text("def (:\n", encoding="utf-8")
    out = await _ok(project_git.find_usages("needle"))
    assert "1 files skipped" in out.splitlines()[0]


async def test_find_usages_unknown_symbol(files_repo: Path) -> None:
    out = await _ok(project_git.find_usages("nothing_here"))
    assert out.startswith("No usages of")


async def test_find_usages_bad_symbol(files_repo: Path) -> None:
    with pytest.raises(ToolInputError):
        await project_git.find_usages("1abc")


async def test_find_usages_ignores_dotted_prefix(files_repo: Path) -> None:
    out = await _ok(project_git.find_usages("mod.needle", "a.py"))
    assert "a.py:1:" in out


# ------------------------------------------------------------- write tools

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SERVER: str = str(REPO_ROOT / "mcp_servers" / "project_git.py")
READ_ONLY_TOOLS: set[str] = {
    "git_current_branch", "git_status", "git_diff", "git_tracked_files",
    "git_recent_commits", "read_file", "search_text", "find_usages",
}  # fmt: skip


def _configure_write(repo: Path, max_chars: int = 18000) -> None:
    project_git.configure(
        os.path.realpath(repo), shutil.which("git") or "git", max_chars, 10.0, allow_write=True
    )


@pytest.fixture
def write_repo(files_repo: Path) -> Iterator[Path]:
    """The files repository with write mode on; read-only mode is restored afterwards."""
    _configure_write(files_repo)
    yield files_repo
    _configure(files_repo)


def _text(result: Any) -> str:
    return "".join(getattr(item, "text", "") for item in result.content)


@contextlib.asynccontextmanager
async def _session(args: list[str]) -> AsyncIterator[Any]:
    handle, result = await open_session(sys.executable, [SERVER, *args], None, None)
    assert handle is not None, (result.error_code, result.error_message, result.stderr_tail)
    try:
        yield handle.session
    finally:
        await close_handle(handle)


def _snapshot(repo: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_config_allow_write_default_and_flag(files_repo: Path) -> None:
    assert project_git.get_config().allow_write is False
    _configure_write(files_repo)
    try:
        assert project_git.get_config().allow_write is True
    finally:
        _configure(files_repo)


def test_parse_args_allow_write() -> None:
    assert project_git.parse_args(["--allow-write"]).allow_write is True
    assert project_git.parse_args([]).allow_write is False


async def test_import_time_registry_has_no_writer() -> None:
    names = {t.name for t in await project_git.server.list_tools()}
    assert names == READ_ONLY_TOOLS


async def test_write_tools_refuse_without_flag(files_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="--allow-write"):
        await project_git.write_file("notes/a.md", "x\n")
    with pytest.raises(ToolInputError, match="--allow-write"):
        await project_git.edit_file("a.py", "needle = 1", "needle = 2")
    assert not (files_repo / "notes" / "a.md").exists()
    assert (files_repo / "a.py").read_text(encoding="utf-8").startswith("needle = 1")


async def test_stdio_tool_list_without_flag(files_repo: Path) -> None:
    async with _session([f"--root={files_repo}"]) as session:
        names = {t.name for t in (await session.list_tools()).tools}
    assert names == READ_ONLY_TOOLS and len(names) == 8


async def test_stdio_tool_list_with_flag(files_repo: Path) -> None:
    async with _session([f"--root={files_repo}", "--allow-write"]) as session:
        names = {t.name for t in (await session.list_tools()).tools}
    assert names == READ_ONLY_TOOLS | {"write_file", "edit_file"} and len(names) == 10


async def test_stdio_write_creates_file(files_repo: Path) -> None:
    async with _session([f"--root={files_repo}", "--allow-write"]) as session:
        result = await session.call_tool(
            "write_file", {"path": "docs/adr/0001-x.md", "content": "# T\n\nbody\n"}
        )
    assert not result.isError
    assert _text(result).startswith("Created docs/adr/0001-x.md")
    created = files_repo / "docs" / "adr" / "0001-x.md"
    assert created.read_text(encoding="utf-8") == "# T\n\nbody\n"


async def test_stdio_write_env_refused(files_repo: Path) -> None:
    before = (files_repo / ".env").read_bytes()
    async with _session([f"--root={files_repo}", "--allow-write"]) as session:
        result = await session.call_tool("write_file", {"path": ".env", "content": "X=1\n"})
    assert result.isError and ".env files hold secrets" in _text(result)
    assert (files_repo / ".env").read_bytes() == before


async def test_write_file_new(write_repo: Path) -> None:
    text = await _ok(project_git.write_file("notes/a.md", "# T\n\nbody\n"))
    assert text.splitlines()[0] == "Created notes/a.md (+3 -0)"
    assert "--- /dev/null" in text and "+++ b/notes/a.md" in text
    assert "@@ -0,0 +1,3 @@" in text
    assert (write_repo / "notes" / "a.md").read_text(encoding="utf-8") == "# T\n\nbody\n"


async def test_write_file_new_in_missing_folders(write_repo: Path) -> None:
    text = await _ok(project_git.write_file("x/y/z.txt", "hi\n"))
    assert text.startswith("Created x/y/z.txt (+1 -0)")
    assert (write_repo / "x" / "y" / "z.txt").is_file()


async def test_write_file_overwrite(write_repo: Path) -> None:
    text = await _ok(project_git.write_file("a.py", "needle = 2\nprint(needle)\n"))
    assert text.splitlines()[0] == "Updated a.py (+1 -1)"
    assert "--- a/a.py" in text and "+++ b/a.py" in text
    assert "-needle = 1" in text and "+needle = 2" in text


async def test_write_file_identical_is_no_change(write_repo: Path) -> None:
    text = await _ok(project_git.write_file("a.py", "needle = 1\nprint(needle)\n"))
    assert text.startswith("No changes:")
    assert (write_repo / "a.py").read_text(encoding="utf-8") == "needle = 1\nprint(needle)\n"


async def test_write_file_keeps_crlf_of_existing(write_repo: Path) -> None:
    (write_repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    await _ok(project_git.write_file("crlf.txt", "one\nthree\n"))
    assert (write_repo / "crlf.txt").read_bytes() == b"one\r\nthree\r\n"


async def test_edit_file_single_match(write_repo: Path) -> None:
    text = await _ok(project_git.edit_file("a.py", "needle = 1", "needle = 5"))
    assert text.splitlines()[0] == "Updated a.py (+1 -1)"
    assert (write_repo / "a.py").read_text(encoding="utf-8") == "needle = 5\nprint(needle)\n"


async def test_edit_file_not_found_with_line_hint(write_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="first line appears at line 2"):
        await project_git.edit_file("pkg/b.py", "  return 'needle'\n  x", "return 'x'")
    with pytest.raises(ToolInputError, match="was not found.*call read_file"):
        await project_git.edit_file("a.py", "absent text", "x")


async def test_edit_file_ambiguous(write_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="occurs 2 times"):
        await project_git.edit_file("a.py", "needle", "pin")
    assert (write_repo / "a.py").read_text(encoding="utf-8") == "needle = 1\nprint(needle)\n"


async def test_edit_file_bad_arguments(write_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="must not be empty"):
        await project_git.edit_file("a.py", "", "x")
    with pytest.raises(ToolInputError, match="identical"):
        await project_git.edit_file("a.py", "needle = 1", "needle = 1")
    with pytest.raises(ToolInputError, match="does not exist"):
        await project_git.edit_file("missing.py", "a", "b")


async def test_edit_file_crlf_roundtrip(write_repo: Path) -> None:
    (write_repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    text = await _ok(project_git.edit_file("crlf.txt", "two\n", "TWO\n"))
    assert text.startswith("Updated crlf.txt (+1 -1)")
    assert (write_repo / "crlf.txt").read_bytes() == b"one\r\nTWO\r\n"


@pytest.mark.parametrize(
    "target",
    [
        "../outside.md", ".git/hooks/pre-commit", ".GIT/config", ".git./x", "GIT~1/x",
        "sub/.git/x", ".env", "config/.env.local", "app.db", "build/out.txt", "con.txt",
        "nul", "docs", ".", "",
    ],
    ids=[
        "traversal", "git-hook", "git-upper", "git-trailing-dot", "git-short-name",
        "nested-git", "env", "env-local", "ignored-db", "ignored-dir", "device-con",
        "device-nul", "folder", "dot", "empty",
    ],
)  # fmt: skip
async def test_write_forbidden_targets(write_repo: Path, target: str) -> None:
    before = _snapshot(write_repo)
    with pytest.raises(ToolInputError):
        await project_git.write_file(target, "payload\n")
    assert _snapshot(write_repo) == before
    assert not (write_repo.parent / "outside.md").exists()


async def test_edit_forbidden_target(write_repo: Path) -> None:
    before = (write_repo / ".env").read_bytes()
    with pytest.raises(ToolInputError, match=".env files hold secrets"):
        await project_git.edit_file(".env", "needle", "x")
    assert (write_repo / ".env").read_bytes() == before


async def test_write_content_with_nul(write_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="NUL"):
        await project_git.write_file("bin.txt", "a\x00b")
    assert not (write_repo / "bin.txt").exists()


async def test_edit_binary_existing_file(write_repo: Path) -> None:
    (write_repo / "blob.dat").write_bytes(b"ab\x00cd")
    with pytest.raises(ToolInputError, match="binary"):
        await project_git.edit_file("blob.dat", "ab", "x")
    with pytest.raises(ToolInputError, match="binary"):
        await project_git.write_file("blob.dat", "text\n")
    assert (write_repo / "blob.dat").read_bytes() == b"ab\x00cd"


async def test_write_content_too_large(write_repo: Path) -> None:
    with pytest.raises(ToolInputError, match="larger than 200000"):
        await project_git.write_file("big.txt", "a" * 200_001)
    assert not (write_repo / "big.txt").exists()


async def test_write_result_cut_but_file_complete(write_repo: Path) -> None:
    _configure_write(write_repo, max_chars=1000)
    content = "".join(f"line {i:04d} padding padding\n" for i in range(300))
    text = await _ok(project_git.write_file("long.txt", content))
    assert "The file was saved in full" in text
    assert (write_repo / "long.txt").read_text(encoding="utf-8") == content


async def test_write_through_symlink_into_git_refused(write_repo: Path) -> None:
    link = write_repo / "lnk"
    try:
        os.symlink(write_repo / ".git", link, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks are not available")
    with pytest.raises(ToolInputError):
        await project_git.write_file("lnk/x", "payload\n")
    assert not (write_repo / ".git" / "x").exists()
