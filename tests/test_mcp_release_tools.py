"""Tests for the release tools of the git MCP server: flag gating, read tools and git writes."""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from agent.mcp_client import close_handle, open_session
from mcp_servers import project_git
from mcp_servers.project_git import ToolInputError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SERVER: str = str(REPO_ROOT / "mcp_servers" / "project_git.py")
MODULE_PATH: Path = REPO_ROOT / "mcp_servers" / "project_git.py"
RELEASE_TOOLS: list[str] = [
    "release_context",
    "release_range_commits",
    "release_commit_changelog",
    "release_create_tag",
    "release_push",
    "github_create_draft_release",
]


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


def _run(repo: Path, *args: str) -> str:
    done = subprocess.run(
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
    return done.stdout.decode("utf-8", errors="replace").strip()


def _git(repo: Path, *args: str) -> None:
    _run(repo, *args)


def _commit_file(repo: Path, name: str, text: str, message: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", message)


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    """Working repo with tag v1.0 pushed to a bare origin, configured with allow_release."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@t")
    _commit_file(repo, "a.txt", "one\n", "initial")
    _git(repo, "tag", "-a", "v1.0", "-m", "v1.0")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main", "v1.0")
    project_git.configure(
        os.path.realpath(repo), shutil.which("git") or "git", 18000, 10.0, allow_release=True
    )
    return repo


def _json_line(text: str, index: int = 1) -> Any:
    return json.loads(text.splitlines()[index])


# ----------------------------------------------------------- flag and registration


def test_parse_args_allow_release_flag() -> None:
    assert project_git.parse_args([]).allow_release is False
    assert project_git.parse_args(["--allow-release"]).allow_release is True
    with pytest.raises(SystemExit):
        project_git.parse_args(["--allow-releas"])


def test_config_allow_release_default(tmp_path: Path) -> None:
    project_git.configure(str(tmp_path), "git", 18000, 10.0)
    assert project_git.get_config().allow_release is False


def test_release_tools_are_not_decorated_at_import() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for name in RELEASE_TOOLS:
        assert not re.search(rf"@server\.tool[^\n]*\n(async )?def {name}\b", source)


@contextlib.asynccontextmanager
async def _session(args: list[str]) -> AsyncIterator[Any]:
    handle, result = await open_session(sys.executable, [SERVER, *args], None, None)
    assert handle is not None, (result.error_code, result.error_message, result.stderr_tail)
    try:
        yield handle.session
    finally:
        await close_handle(handle)


async def _listed(args: list[str]) -> set[str]:
    async with _session(args) as session:
        listing = await session.list_tools()
    return {tool.name for tool in listing.tools}


async def test_stdio_listing_without_flag_has_no_release_tools(release_repo: Path) -> None:
    names = await _listed([f"--root={release_repo}"])
    assert not names & set(RELEASE_TOOLS)
    assert "git_status" in names


async def test_stdio_listing_with_flag_adds_six_release_tools(release_repo: Path) -> None:
    plain = await _listed([f"--root={release_repo}"])
    flagged = await _listed([f"--root={release_repo}", "--allow-release"])
    assert flagged - plain <= set(RELEASE_TOOLS)
    assert {"release_context", "release_range_commits"} <= flagged - plain
    assert plain <= flagged


# ------------------------------------------------------------------ release_context


async def test_context_reports_branch_tags_and_remote(release_repo: Path) -> None:
    _git(release_repo, "tag", "v1.1")
    _git(release_repo, "tag", "backup/x")
    text = await project_git.release_context()
    assert text.splitlines()[0] == "Release context for main"
    info = _json_line(text)
    assert info["branch"] == "main"
    assert re.fullmatch(r"[0-9a-f]{40}", info["head"])
    assert {"v1.0", "v1.1", "backup/x"} <= set(info["local_tags"])
    assert {"v1.0", "v1.1"} <= set(info["merged_tags"])
    assert info["remote_tags"] == ["v1.0"]
    assert info["remote_error"] is None
    assert info["origin_url"]
    assert info["dirty"] == []


async def test_context_dirty_includes_untracked_and_modified(release_repo: Path) -> None:
    (release_repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (release_repo / "новый.txt").write_text("x\n", encoding="utf-8")
    info = _json_line(await project_git.release_context())
    joined = "\n".join(info["dirty"])
    assert " M a.txt" in joined
    assert "?? новый.txt" in joined


async def test_context_without_origin(tmp_path: Path) -> None:
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init")
    _commit_file(repo, "a.txt", "x\n", "initial")
    project_git.configure(
        os.path.realpath(repo), shutil.which("git") or "git", 18000, 10.0, allow_release=True
    )
    info = _json_line(await project_git.release_context())
    assert info["origin_url"] is None
    assert info["remote_tags"] is None
    assert isinstance(info["remote_error"], str) and info["remote_error"]


async def test_context_detached_head(release_repo: Path) -> None:
    _git(release_repo, "checkout", "--detach")
    text = await project_git.release_context()
    assert text.splitlines()[0] == "Release context for detached HEAD"
    assert _json_line(text)["branch"] is None


# ------------------------------------------------------------ release_range_commits


def _bulk_commits(repo: Path, count: int) -> None:
    """Add many empty commits on main in one fast-import run."""
    lines: list[str] = []
    for i in range(count):
        message = f"c{i}"
        lines += [
            "commit refs/heads/main",
            f"committer T <t@t> {1700000000 + i} +0000",
            f"data {len(message)}",
            message,
        ]
        if i == 0:
            lines.append("from refs/heads/main^0")
        lines.append("")
    subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=repo,
        env=_clean_env(),
        input=("\n".join(lines) + "\n").encode("utf-8"),
        check=True,
        capture_output=True,
    )
    _git(repo, "reset", "--hard", "main")


async def test_range_commits_pages_of_fifty(release_repo: Path) -> None:
    _bulk_commits(release_repo, 120)
    first = await project_git.release_range_commits("v1.0..HEAD")
    lines = first.splitlines()
    assert lines[0] == "Commits 1-50 of 120 in v1.0..HEAD"
    assert len(lines) == 1 + 50 + 1
    assert json.loads(lines[-1]) == {"next_page": 2, "total": 120}
    commit = json.loads(lines[1])
    assert set(commit) == {"sha", "parents", "subject", "dirs", "planning_only", "files_n"}
    assert re.fullmatch(r"[0-9a-f]{40}", commit["sha"])
    third = (await project_git.release_range_commits("v1.0..HEAD", page=3)).splitlines()
    assert len(third) == 1 + 20 + 1
    assert json.loads(third[-1]) == {"next_page": None, "total": 120}


async def test_range_commits_planning_root_and_merge(release_repo: Path) -> None:
    _commit_file(release_repo, ".planning/x.md", "p\n", "plan")
    _commit_file(release_repo, "root.txt", "r\n", "root file")
    _commit_file(release_repo, "agent/ws.py", "w\n", "agent file")
    _git(release_repo, "checkout", "-b", "side", "v1.0")
    _commit_file(release_repo, "side.txt", "s\n", "side work")
    _git(release_repo, "checkout", "main")
    _git(release_repo, "merge", "--no-ff", "-m", "merge side", "side")
    rows = {
        row["subject"]: row
        for row in (
            json.loads(line)
            for line in (await project_git.release_range_commits("v1.0..HEAD")).splitlines()[1:-1]
        )
    }
    assert rows["plan"]["planning_only"] is True and rows["plan"]["dirs"] == [".planning"]
    assert rows["root file"]["dirs"] == ["."] and rows["root file"]["planning_only"] is False
    assert rows["agent file"]["dirs"] == ["agent"] and rows["agent file"]["files_n"] == 1
    assert rows["merge side"]["parents"] == 2


@pytest.mark.parametrize(
    ("range_text", "page"),
    [
        ("v1.0...HEAD", 1),
        ("HEAD", 1),
        ("--upload-pack=x..HEAD", 1),
        ("v1.0..HEAD", 0),
        ("v1.0..HEAD", 41),
        ("v1.0..nope", 1),
    ],
)
async def test_range_commits_rejects_bad_input(
    release_repo: Path, range_text: str, page: int
) -> None:
    with pytest.raises(ToolInputError):
        await project_git.release_range_commits(range_text, page)


# ------------------------------------------------------- release_commit_changelog

SECTION_11: str = "## [v1.1] - 2026-10-17\n\n### Добавлено\n- x\n"
SECTION_12: str = "## [v1.2] - 2026-10-18\n\n### Исправлено\n- y\n"


async def test_changelog_created_and_committed(release_repo: Path) -> None:
    text = await project_git.release_commit_changelog("v1.1", SECTION_11)
    assert text.splitlines()[0] == "Committed CHANGELOG.md for v1.1"
    commit = _json_line(text)["commit"]
    assert commit == _run(release_repo, "rev-parse", "HEAD")
    assert _run(release_repo, "show", "--name-only", "--format=%s", "HEAD").splitlines() == [
        "docs(release): v1.1 changelog",
        "",
        "CHANGELOG.md",
    ]
    body = (release_repo / "CHANGELOG.md").read_bytes().decode("utf-8")
    assert body.startswith("# Changelog")
    assert "### Добавлено" in body and "\r" not in body


async def test_changelog_second_section_goes_above_first(release_repo: Path) -> None:
    await project_git.release_commit_changelog("v1.1", SECTION_11)
    await project_git.release_commit_changelog("v1.2", SECTION_12)
    body = (release_repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert body.index("# Changelog") < body.index("## [v1.2]") < body.index("## [v1.1]")
    assert "Исправлено" in body


@pytest.mark.parametrize(
    ("version", "section"),
    [
        ("1.1", "## [1.1] - d\n"),
        ("v1", "## [v1] - d\n"),
        ("v1.1", "## [v1.2] - d\n"),
        ("v1.1", "no heading\n"),
    ],
)
async def test_changelog_rejects_bad_version_or_section(
    release_repo: Path, version: str, section: str
) -> None:
    with pytest.raises(ToolInputError):
        await project_git.release_commit_changelog(version, section)


async def test_changelog_refuses_duplicate_version(release_repo: Path) -> None:
    await project_git.release_commit_changelog("v1.1", SECTION_11)
    with pytest.raises(ToolInputError, match="already has a section"):
        await project_git.release_commit_changelog("v1.1", SECTION_11)


async def test_changelog_refuses_when_other_file_staged(release_repo: Path) -> None:
    (release_repo / "other.txt").write_text("o\n", encoding="utf-8")
    _git(release_repo, "add", "other.txt")
    with pytest.raises(ToolInputError, match="staged"):
        await project_git.release_commit_changelog("v1.1", SECTION_11)


async def test_changelog_commit_leaves_unstaged_changes_out(release_repo: Path) -> None:
    (release_repo / "a.txt").write_text("edited\n", encoding="utf-8")
    await project_git.release_commit_changelog("v1.1", SECTION_11)
    names = _run(release_repo, "show", "--name-only", "--format=", "HEAD").split()
    assert names == ["CHANGELOG.md"]
    assert _run(release_repo, "status", "--porcelain").strip() == "M a.txt"


# ------------------------------------------------------------- release_create_tag


async def test_create_tag_annotated(release_repo: Path) -> None:
    sha = _run(release_repo, "rev-parse", "HEAD")
    text = await project_git.release_create_tag("v1.1", "v1.1: Title", sha)
    assert text.splitlines()[0] == f"Created annotated tag v1.1 at {sha[:7]}"
    assert _json_line(text) == {"tag": "v1.1", "commit": sha}
    assert _run(release_repo, "cat-file", "-t", "v1.1") == "tag"
    assert _run(release_repo, "rev-parse", "v1.1^{commit}") == sha


async def test_create_tag_refuses_existing(release_repo: Path) -> None:
    sha = _run(release_repo, "rev-parse", "HEAD")
    with pytest.raises(ToolInputError, match="v1.0"):
        await project_git.release_create_tag("v1.0", "again", sha)


@pytest.mark.parametrize("version", ["v1.1 ", "--x", "refs/tags/v1.1", "1.1", "v1.1.0"])
async def test_create_tag_rejects_bad_version(release_repo: Path, version: str) -> None:
    sha = _run(release_repo, "rev-parse", "HEAD")
    with pytest.raises(ToolInputError):
        await project_git.release_create_tag(version, "m", sha)


@pytest.mark.parametrize("message", ["", "two\nlines", "x" * 201])
async def test_create_tag_rejects_bad_message(release_repo: Path, message: str) -> None:
    sha = _run(release_repo, "rev-parse", "HEAD")
    with pytest.raises(ToolInputError):
        await project_git.release_create_tag("v1.1", message, sha)


# ------------------------------------------------------------------ release_push


def _origin_refs(repo: Path) -> str:
    return _run(repo, "ls-remote", "origin")


async def test_push_sends_branch_and_tag(release_repo: Path) -> None:
    await project_git.release_commit_changelog("v1.1", SECTION_11)
    sha = _run(release_repo, "rev-parse", "HEAD")
    await project_git.release_create_tag("v1.1", "v1.1: T", sha)
    text = await project_git.release_push("main", "v1.1")
    assert text == "Pushed main and v1.1 to origin"
    refs = _origin_refs(release_repo)
    assert f"{sha}\trefs/heads/main" in refs
    assert "refs/tags/v1.1" in refs


async def test_push_rejects_missing_tag(release_repo: Path) -> None:
    with pytest.raises(ToolInputError):
        await project_git.release_push("main", "v9.9")


@pytest.mark.parametrize(
    "branch", ["-x", "a:b", "+main", "ma*", "refs/heads/main", "a..b", "", "bad name"]
)
async def test_push_rejects_bad_branch(release_repo: Path, branch: str) -> None:
    with pytest.raises(ToolInputError):
        await project_git.release_push(branch, "v1.0")


async def test_push_non_fast_forward_fails_without_retry(
    release_repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(tmp_path / "origin.git"), str(other))
    _commit_file(other, "o.txt", "o\n", "other work")
    _git(other, "push", "origin", "main")
    remote_head = _run(release_repo, "ls-remote", "origin", "refs/heads/main").split()[0]
    _commit_file(release_repo, "b.txt", "b\n", "local work")
    await project_git.release_create_tag("v1.1", "v1.1", _run(release_repo, "rev-parse", "HEAD"))
    with pytest.raises(ToolInputError, match="push failed"):
        await project_git.release_push("main", "v1.1")
    assert _run(release_repo, "ls-remote", "origin", "refs/heads/main").split()[0] == remote_head


# ------------------------------------------------------------ source cleanliness


def test_release_source_has_no_force_or_wide_staging() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for needle in (
        "--force", "--force-with-lease", "--mirror", "--delete", '--tags"',
        'add", "-A', 'commit", "-a',
    ):  # fmt: skip
        assert needle not in source, needle
    assert "print(" not in source
    for line in source.splitlines():
        assert not re.match(r"\s*(import|from)\s+(shared|agent)\b", line)
