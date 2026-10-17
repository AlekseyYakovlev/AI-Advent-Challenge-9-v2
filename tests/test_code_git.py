"""Tests for the shell-free git reads behind the code index."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent import code_git
from agent.code_git import GitError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def run_git(cwd: Path, *args: str) -> str:
    """Run git in a test repository and return stdout."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    done = subprocess.run(
        [
            "git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args,
        ],  # fmt: skip
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return done.stdout.decode("utf-8", errors="replace")


class Repos:
    """A bare origin and a working clone of it."""

    def __init__(self, root: Path) -> None:
        self.origin = root / "Aleksey" / "Demo.git"
        self.work = root / "work"
        self.root = root
        self.origin.mkdir(parents=True)
        run_git(self.origin, "init", "--bare")
        self.work.mkdir()
        run_git(self.work, "init")
        run_git(self.work, "remote", "add", "origin", str(self.origin))

    def commit(self, files: dict[str, str | bytes | None], message: str = "c") -> str:
        """Write files (None deletes), commit and push the current branch; return the commit."""
        for name, content in files.items():
            target = self.work / name
            if content is None:
                run_git(self.work, "rm", "-q", "--", name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8") if isinstance(content, str) else content
            target.write_bytes(data)
        run_git(self.work, "add", "-A")
        run_git(self.work, "commit", "-q", "-m", message)
        branch = run_git(self.work, "rev-parse", "--abbrev-ref", "HEAD").strip()
        run_git(self.work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return run_git(self.work, "rev-parse", "HEAD").strip()


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    return Repos(tmp_path)


async def test_validate_repo_path_returns_toplevel(repos: Repos) -> None:
    repos.commit({"a.py": "x = 1\n"})
    sub = repos.work / "pkg"
    sub.mkdir()
    top = await code_git.validate_repo_path(str(sub))
    assert Path(top).resolve() == repos.work.resolve()


@pytest.mark.parametrize("bad", ["", "   ", "relative/path"])
async def test_validate_repo_path_rejects_unusable_text(bad: str) -> None:
    with pytest.raises(GitError):
        await code_git.validate_repo_path(bad)


async def test_validate_repo_path_rejects_missing_and_non_repo(tmp_path: Path) -> None:
    with pytest.raises(GitError) as missing:
        await code_git.validate_repo_path(str(tmp_path / "nope"))
    assert "не найдена" in missing.value.detail
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitError) as plain_error:
        await code_git.validate_repo_path(str(plain))
    assert "не является git" in plain_error.value.detail


@pytest.mark.parametrize(
    "url",
    [
        "git@Alias:Aleksey/Demo.git",
        "https://github.com/Aleksey/Demo.git",
        "https://github.com/Aleksey/Demo",
        "ssh://git@github.com/Aleksey/Demo.git",
    ],
)
async def test_repo_key_parses_origin_urls(repos: Repos, url: str) -> None:
    run_git(repos.work, "remote", "set-url", "origin", url)
    assert await code_git.repo_key(str(repos.work)) == "aleksey/demo"


async def test_repo_key_without_origin(tmp_path: Path) -> None:
    repo = tmp_path / "solo"
    repo.mkdir()
    run_git(repo, "init")
    with pytest.raises(GitError) as exc:
        await code_git.repo_key(str(repo))
    assert "origin" in exc.value.detail


@pytest.mark.parametrize("ref", ["main", "Day32", "feature/x-1", "release/1.2"])
def test_check_base_ref_accepts(ref: str) -> None:
    code_git.check_base_ref(ref)


@pytest.mark.parametrize(
    "ref",
    [
        "", "-x", "--upload-pack=x", "a..b", "a b", "a~1", "a^", "a:b", "a\\b", "a/", "a@{1}",
        "x" * 256, "/a", ".a", "a.lock", "a//b", "a\nb",
    ],
)  # fmt: skip
def test_check_base_ref_rejects(ref: str) -> None:
    with pytest.raises(GitError):
        code_git.check_base_ref(ref)


async def test_fetch_base_commit_ignores_checkout_and_edits(repos: Repos) -> None:
    first = repos.commit({"a.py": "x = 1\n"})
    run_git(repos.work, "checkout", "-q", "-b", "other")
    (repos.work / "a.py").write_text("edited\n")
    assert await code_git.fetch_base_commit(str(repos.work), "main") == first
    assert await code_git.current_branch(str(repos.work)) == "other"
    assert (repos.work / "a.py").read_text() == "edited\n"
    run_git(repos.work, "checkout", "-q", "main", "--", ".")
    run_git(repos.work, "checkout", "-q", "main")
    second = repos.commit({"a.py": "x = 2\n"})
    assert await code_git.fetch_base_commit(str(repos.work), "main") == second


async def test_fetch_base_commit_unknown_branch_hides_origin_url(repos: Repos) -> None:
    repos.commit({"a.py": "x = 1\n"})
    with pytest.raises(GitError) as exc:
        await code_git.fetch_base_commit(str(repos.work), "missing-branch")
    assert "missing-branch" in exc.value.detail
    assert str(repos.origin) not in exc.value.detail
    assert "Demo.git" not in exc.value.detail


async def test_fetch_base_commit_concurrent_branches_do_not_mix(repos: Repos) -> None:
    main_tip = repos.commit({"a.py": "x = 1\n"})
    run_git(repos.work, "checkout", "-q", "-b", "Day32")
    day_tip = repos.commit({"b.py": "y = 1\n"})
    results = await asyncio.gather(
        *[
            code_git.fetch_base_commit(str(repos.work), "main" if i % 2 == 0 else "Day32")
            for i in range(6)
        ]
    )
    assert results == [main_tip, day_tip] * 3


async def test_current_branch_none_on_detached_head(repos: Repos) -> None:
    commit = repos.commit({"a.py": "x = 1\n"})
    run_git(repos.work, "checkout", "-q", "--detach", commit)
    assert await code_git.current_branch(str(repos.work)) is None


async def test_list_tree_regular_files_only(repos: Repos) -> None:
    repos.commit({"agent/ws.py": "x = 1\n", "папка с пробелом/файл тест.py": "y = 2\n"})
    commit = await code_git.fetch_base_commit(str(repos.work), "main")
    # A symlink entry and a submodule entry, added straight into the index.
    link_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repos.work, input=b"agent/ws.py", capture_output=True, check=True,
    ).stdout.decode().strip()  # fmt: skip
    run_git(repos.work, "update-index", "--add", "--cacheinfo", f"120000,{link_blob},link.py")
    run_git(repos.work, "update-index", "--add", "--cacheinfo", f"160000,{commit},sub")
    tree = run_git(repos.work, "write-tree").strip()
    new_commit = run_git(repos.work, "commit-tree", tree, "-m", "m").strip()
    entries = await code_git.list_tree(str(repos.work), new_commit)
    paths = {entry.path: entry for entry in entries}
    assert set(paths) == {"agent/ws.py", "папка с пробелом/файл тест.py"}
    assert paths["agent/ws.py"].size == len("x = 1\n")
    assert len(paths["agent/ws.py"].blob_id) == 40


async def test_read_blobs_exact_bytes(repos: Repos) -> None:
    crlf = b"line one\r\nline two\r\n"
    binary = b"\x00\x01\x02binary\x00\xff\n\n"
    repos.commit({"crlf.txt": crlf, "bin.dat": binary, "empty.txt": b""})
    commit = await code_git.fetch_base_commit(str(repos.work), "main")
    entries = {e.path: e for e in await code_git.list_tree(str(repos.work), commit)}
    wanted = [entries[name].blob_id for name in ("crlf.txt", "bin.dat", "empty.txt")]
    got = await code_git.read_blobs(str(repos.work), wanted)
    assert got[entries["crlf.txt"].blob_id] == crlf
    assert got[entries["bin.dat"].blob_id] == binary
    assert got[entries["empty.txt"].blob_id] == b""


async def test_read_blobs_unknown_id_raises(repos: Repos) -> None:
    repos.commit({"a.py": "x = 1\n"})
    with pytest.raises(GitError):
        await code_git.read_blobs(str(repos.work), ["0" * 39 + "1"])
    with pytest.raises(GitError):
        await code_git.read_blobs(str(repos.work), ["not-an-id"])


async def test_read_blobs_empty_list(repos: Repos) -> None:
    assert await code_git.read_blobs(str(repos.work), []) == {}


async def test_timeout_kills_the_process_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_git, "GIT_TIMEOUT", 0.5)
    monkeypatch.setattr(
        code_git,
        "base_argv",
        lambda git, top: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    started = time.monotonic()
    with pytest.raises(GitError) as exc:
        await code_git._run(["status"], top=None)
    assert "не ответила" in exc.value.detail
    assert time.monotonic() - started < 15
