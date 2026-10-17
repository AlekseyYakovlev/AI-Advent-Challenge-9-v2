"""Tests for the read_file, search_text and find_usages tools of the project git server."""

import os
import shutil
import subprocess
from collections.abc import Awaitable
from pathlib import Path

import pytest

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
    (repo / ".gitignore").write_text(".env\n*.db\n", encoding="utf-8")
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
