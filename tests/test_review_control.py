"""Offline tests for scripts/review_control.py: branch building, parsing, waiting, honest exit codes."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("review_control", SCRIPTS / "review_control.py")
assert _spec is not None and _spec.loader is not None
rc = importlib.util.module_from_spec(_spec)
sys.modules["review_control"] = rc
_spec.loader.exec_module(rc)

import pr_review_ci  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

TOKEN = "ghp_test_token_value_123"
API_KEY = "aia_test_key_value_456"
REPO = "owner/repo"


def run_git(cwd: Path, *args: str) -> str:
    """Run git in a test repository and return stdout."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    done = subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "-c", "core.autocrlf=false",
         "-c", "init.defaultBranch=main", *args],
        cwd=cwd, env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return done.stdout.decode("utf-8", errors="replace")


class Env:
    """A bare origin, a dirty clone, a manifest and an output folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.work = root / "work"
        self.out = root / "out"
        self.origin.mkdir()
        run_git(self.origin, "init", "--bare")
        self.work.mkdir()
        run_git(self.work, "init")
        run_git(self.work, "remote", "add", "origin", str(self.origin))
        (self.work / "agent").mkdir()
        (self.work / "agent" / "mod.py").write_text("A = 1\nB = 2\n", encoding="utf-8", newline="\n")
        (self.work / "a.txt").write_text("base\n", encoding="utf-8", newline="\n")
        run_git(self.work, "add", "-A")
        run_git(self.work, "commit", "-q", "-m", "base")
        run_git(self.work, "push", "-q", "origin", "HEAD:refs/heads/main")
        run_git(self.work, "fetch", "-q", "origin")
        run_git(self.work, "branch", "-q", "--set-upstream-to=origin/main")
        fixture = root / "fixture.txt"
        fixture.write_text('"""Demo."""\n\nVALUE: int = 1\n', encoding="utf-8", newline="\n")
        self.manifest_path = root / "manifest.json"
        self.manifest = {
            "repo": REPO, "base": "main", "branch_prefix": "review-test/",
            "prs": [
                {"id": "bug", "control": True, "kind": "obvious_bug", "title": "Add demo",
                 "body": "Body", "files": [{"path": "agent/review_demo_x.py", "source": str(fixture)}],
                 "copies": [{"path": "agent/review_demo_big/mod.py", "from": "agent/mod.py",
                             "edits": [{"find": "B = 2\n", "replace": "B = 3\n"}]}],
                 "expected": [], "tolerated": []},
                {"id": "clean", "control": True, "kind": "clean", "title": "Add clean",
                 "body": "Body", "files": [{"path": "agent/review_demo_y.py", "source": str(fixture)}],
                 "copies": [], "expected": [], "tolerated": []},
            ],
        }
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.work / "agent" / "mod.py").write_text("DIRTY = 1\n", encoding="utf-8", newline="\n")
        (self.work / "untracked.txt").write_text("keep me\n", encoding="utf-8", newline="\n")

    def args(self, command: str, *extra: str) -> list[str]:
        """Argument list of a command."""
        return [command, "--manifest", str(self.manifest_path), "--out", str(self.out),
                "--repo-dir", str(self.work), *extra]

    def snapshot(self) -> tuple[str, dict[str, bytes]]:
        """Porcelain status and file bytes of the clone."""
        status = run_git(self.work, "status", "--porcelain")
        files = {p.name: p.read_bytes() for p in self.work.rglob("*")
                 if p.is_file() and ".git" not in p.parts}
        return status, files

    def tree_of(self, ref: str) -> list[str]:
        """Sorted file list of a commit in the origin."""
        return sorted(run_git(self.origin, "ls-tree", "-r", "--name-only", ref).split())


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    for name in ("GITHUB_PERSONAL_ACCESS_TOKEN", "PR_REVIEW_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rc, "sleep", lambda seconds: None)
    monkeypatch.setattr(rc, "_SECRETS", [])
    return Env(tmp_path)


def use_http(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Route the script's HTTP through a fake handler."""
    monkeypatch.setattr(
        rc, "client_factory", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_build_pr_tree_applies_edits(env: Env) -> None:
    entry = env.manifest["prs"][0]
    tree = rc.build_pr_tree(entry, lambda path: "A = 1\nB = 2\n")
    assert tree["agent/review_demo_big/mod.py"] == "A = 1\nB = 3\n"
    assert tree["agent/review_demo_x.py"].startswith('"""Demo."""')


def test_build_pr_tree_rejects_ambiguous_edit(env: Env) -> None:
    entry = env.manifest["prs"][0]
    with pytest.raises(rc.ControlError):
        rc.build_pr_tree(entry, lambda path: "B = 2\nB = 2\n")
    with pytest.raises(rc.ControlError):
        rc.build_pr_tree(entry, lambda path: "nothing\n")


def test_open_without_token_pushes_branches_and_exits_4(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    before = env.snapshot()
    code = rc.main(env.args("open"))
    assert code == rc.EXIT_MANUAL
    assert env.snapshot() == before
    out = capsys.readouterr().out
    assert f"https://github.com/{REPO}/compare/main...review-test/bug?expand=1" in out
    assert "Add demo" in out
    base = run_git(env.origin, "rev-parse", "refs/heads/main").strip()
    head = run_git(env.origin, "rev-parse", "refs/heads/review-test/bug").strip()
    assert run_git(env.origin, "rev-parse", f"{head}^").strip() == base
    assert env.tree_of(head) == sorted(
        ["a.txt", "agent/mod.py", "agent/review_demo_x.py", "agent/review_demo_big/mod.py"]
    )
    shown = run_git(env.origin, "show", f"{head}:agent/review_demo_big/mod.py")
    assert shown == "A = 1\nB = 3\n"
    assert run_git(env.work, "worktree", "list").strip().count("\n") == 0
    prs = json.loads((env.out / "prs.json").read_text(encoding="utf-8"))
    assert prs["bug"]["head_sha"] == head


def test_open_twice_is_idempotent(env: Env) -> None:
    rc.main(env.args("open"))
    first = run_git(env.origin, "rev-parse", "refs/heads/review-test/bug").strip()
    rc.main(env.args("open"))
    assert run_git(env.origin, "rev-parse", "refs/heads/review-test/bug").strip() == first
    assert run_git(env.origin, "rev-list", "--count", "refs/heads/review-test/bug").strip() == "2"


def test_open_with_token_creates_and_reuses_pull_requests(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    rc.register_secrets()
    created: list[dict[str, Any]] = []
    existing: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        if request.method == "GET" and request.url.path == f"/repos/{REPO}/pulls":
            head = request.url.params["head"]
            if head in existing:
                return httpx.Response(200, json=[{"number": existing[head], "html_url": "u"}])
            return httpx.Response(200, json=[])
        assert request.method == "POST"
        body = json.loads(request.content)
        created.append(body)
        number = 10 + len(created)
        existing[f"owner:{body['head']}"] = number
        return httpx.Response(201, json={"number": number, "html_url": f"https://x/{number}"})

    use_http(monkeypatch, handler)
    assert rc.main(env.args("open")) == rc.EXIT_OK
    assert [c["base"] for c in created] == ["main", "main"]
    assert created[0]["title"] == "Add demo" and created[0]["body"] == "Body"
    assert rc.main(env.args("open")) == rc.EXIT_OK
    assert len(created) == 2
    prs = json.loads((env.out / "prs.json").read_text(encoding="utf-8"))
    assert prs["bug"]["number"] == 11 and prs["clean"]["number"] == 12


def comment_body(sha: str) -> str:
    """A review comment as the Action writes it."""
    review = {"head_sha": sha, "provider": "prov", "model": "mdl",
              "sections": {"bugs": ["Ошибка в строке 3"], "architecture": [], "recommendations": []},
              "sources": {"docs": [], "code": []}}
    return pr_review_ci.build_comment(review)


def test_parse_review_comment() -> None:
    parsed = rc.parse_review_comment(comment_body("abcdef1234567890"))
    assert parsed is not None
    assert parsed["commit"] == "abcdef1"
    assert parsed["provider_model"] == "prov/mdl"
    assert "Ошибка в строке 3" in parsed["sections"]["bugs"]
    assert set(parsed["sections"]) == {"bugs", "architecture", "recommendations"}
    assert rc.parse_review_comment("обычный комментарий") is None


def write_prs(env: Env, head: str = "a" * 40) -> None:
    rc.write_json(env.out / "prs.json", {
        "bug": {"number": 11, "branch": "review-test/bug", "head_sha": head, "url": "u"},
    })


def github_fake(comments: list[dict[str, Any]], runs: list[dict[str, Any]]) -> Callable[..., httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/comments"):
            return httpx.Response(200, json=comments)
        if path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": runs})
        if path == "/api/v1/reviews/config":
            return httpx.Response(200, json={"effective": {"provider_name": "P", "model": "M",
                                                          "docs_kb_id": None}})
        return httpx.Response(404)
    return handler


def test_wait_ok_when_comment_names_head(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    write_prs(env)
    comment = {"id": 5, "user": {"login": rc.BOT_LOGIN}, "body": comment_body("a" * 40),
               "updated_at": "2026-01-01T00:10:00Z"}
    use_http(monkeypatch, github_fake([comment], []))
    assert rc.main(env.args("wait")) == rc.EXIT_OK


def test_wait_ignores_comment_of_other_commit_and_other_author(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    write_prs(env)
    comments = [
        {"id": 5, "user": {"login": rc.BOT_LOGIN}, "body": comment_body("b" * 40)},
        {"id": 6, "user": {"login": "someone"}, "body": comment_body("a" * 40)},
    ]
    runs = [{"status": "completed", "conclusion": "failure"}]
    use_http(monkeypatch, github_fake(comments, runs))
    assert rc.main(env.args("wait")) == rc.EXIT_FAILED


def test_wait_not_run_when_no_workflow_run(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    write_prs(env)
    ticks = iter(range(0, 100000, 400))
    monkeypatch.setattr(rc, "clock", lambda: float(next(ticks)))
    use_http(monkeypatch, github_fake([], []))
    assert rc.main(env.args("wait", "--timeout-minutes", "1")) == rc.EXIT_NOT_RUN
    assert rc.RUNNER_OFFLINE_REASON in capsys.readouterr().out


def test_wait_queued_until_timeout_is_not_run(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    write_prs(env)
    ticks = iter(range(0, 100000, 400))
    monkeypatch.setattr(rc, "clock", lambda: float(next(ticks)))
    use_http(monkeypatch, github_fake([], [{"status": "queued", "conclusion": None}]))
    assert rc.main(env.args("wait", "--timeout-minutes", "1")) == rc.EXIT_NOT_RUN


def test_collect_writes_files_for_ok_and_not_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("PR_REVIEW_API_KEY", API_KEY)
    head_ok, head_none = "a" * 40, "c" * 40
    rc.write_json(env.out / "prs.json", {
        "bug": {"number": 11, "branch": "review-test/bug", "head_sha": head_ok, "url": "u"},
        "clean": {"number": 12, "branch": "review-test/clean", "head_sha": head_none, "url": "u"},
    })
    comment = {"id": 5, "user": {"login": rc.BOT_LOGIN}, "body": comment_body(head_ok),
               "updated_at": "2026-01-01T00:10:00Z"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/reviews/config":
            assert request.headers["authorization"] == f"Bearer {API_KEY}"
        if "/issues/11/" in request.url.path:
            return httpx.Response(200, json=[comment])
        if "/issues/12/" in request.url.path:
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        return github_fake([], [])(request)

    use_http(monkeypatch, handler)
    assert rc.main(env.args("collect", "--run", "local")) == rc.EXIT_NOT_RUN
    run = env.out / "local"
    ok = json.loads((run / "bug.json").read_text(encoding="utf-8"))
    assert ok["status"] == "ok" and ok["provider_model"] == "prov/mdl" and ok["comment_id"] == 5
    assert (run / "bug.md").read_text(encoding="utf-8").startswith(pr_review_ci.MARKER)
    none = json.loads((run / "clean.json").read_text(encoding="utf-8"))
    assert none["status"] == rc.NOT_RUN and none["reason"] == rc.RUNNER_OFFLINE_REASON
    assert (run / "clean.md").read_text(encoding="utf-8") == ""
    info = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert info["provider"] == "P" and info["model"] == "M"
    for path in run.iterdir():
        text = path.read_text(encoding="utf-8")
        assert TOKEN not in text and API_KEY not in text


def test_rerun_requires_collected_run_and_keeps_tree(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc.main(env.args("open"))
    old = json.loads((env.out / "prs.json").read_text(encoding="utf-8"))["bug"]["head_sha"]
    assert rc.main(env.args("rerun", "--only", "bug", "--after-run", "local")) == rc.EXIT_NOT_RUN
    assert run_git(env.origin, "rev-parse", "refs/heads/review-test/bug").strip() == old
    rc.write_json(env.out / "local" / "bug.json", {"status": "ok"})
    before = env.snapshot()
    assert rc.main(env.args("rerun", "--only", "bug", "--after-run", "local")) == rc.EXIT_OK
    assert env.snapshot() == before
    new = run_git(env.origin, "rev-parse", "refs/heads/review-test/bug").strip()
    assert new != old
    assert run_git(env.origin, "rev-parse", f"{new}^").strip() == old
    assert (run_git(env.origin, "rev-parse", f"{new}^{{tree}}").strip()
            == run_git(env.origin, "rev-parse", f"{old}^{{tree}}").strip())
    prs = json.loads((env.out / "prs.json").read_text(encoding="utf-8"))
    assert prs["bug"]["head_sha"] == new


def test_close_only_patches_state(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    write_prs(env)
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, json.loads(request.content or b"null")))
        return httpx.Response(200, json={})

    use_http(monkeypatch, handler)
    assert rc.main(env.args("close")) == rc.EXIT_OK
    assert calls == [("PATCH", f"/repos/{REPO}/pulls/11", {"state": "closed"})]


def test_close_without_token_is_manual(env: Env) -> None:
    write_prs(env)
    assert rc.main(env.args("close")) == rc.EXIT_MANUAL


def test_git_refuses_foreign_refs_and_commands(env: Env) -> None:
    with pytest.raises(rc.ControlError):
        rc.git(["push", "origin", "HEAD:refs/heads/Day32"], env.work)
    with pytest.raises(rc.ControlError):
        rc.git(["push", "origin", "--force", "HEAD:refs/heads/review-test/x"], env.work)
    with pytest.raises(rc.ControlError):
        rc.git(["gc"], env.work)


def test_prepare_docs_without_selected_base_is_not_run(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PR_REVIEW_API_KEY", API_KEY)
    use_http(monkeypatch, github_fake([], []))
    assert rc.main(env.args("prepare-docs")) == rc.EXIT_NOT_RUN
    assert "не запускалось: база документации не выбрана" in capsys.readouterr().out


def test_prepare_docs_uploads_and_lists(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PR_REVIEW_API_KEY", API_KEY)
    (env.work / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    uploaded: list[bytes] = []
    states = iter([{"status": "indexing", "phase": "embedding"}, {"status": "ready", "phase": None}])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/reviews/config":
            return httpx.Response(200, json={"effective": {"docs_kb_id": 7}})
        if path == "/api/v1/kb/7/files" and request.method == "POST":
            uploaded.append(request.content)
            return httpx.Response(202, json={})
        if path == "/api/v1/kb/7/files":
            return httpx.Response(200, json=[{"id": 1, "filename": "CLAUDE.md", "size_bytes": 8}])
        if path == "/api/v1/kb/7":
            return httpx.Response(200, json=next(states))
        return httpx.Response(404)

    use_http(monkeypatch, handler)
    assert rc.main(env.args("prepare-docs")) == rc.EXIT_OK
    assert b'filename="CLAUDE.md"' in uploaded[0]
    listing = json.loads((env.out / "docs_kb_files.json").read_text(encoding="utf-8"))
    assert listing[0]["filename"] == "CLAUDE.md"


def test_missing_api_key_and_dead_app_end_as_not_run(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert rc.main(env.args("prepare-docs")) == rc.EXIT_NOT_RUN
    assert rc.main(env.args("collect")) == rc.EXIT_NOT_RUN
    monkeypatch.setenv("PR_REVIEW_API_KEY", API_KEY)
    write_prs(env)

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    use_http(monkeypatch, dead)
    assert rc.main(env.args("prepare-docs")) == rc.EXIT_NOT_RUN
    assert "не запускалось: приложение недоступно" in capsys.readouterr().out


def test_status_without_prs_does_not_raise(env: Env) -> None:
    assert rc.main(env.args("status")) == rc.EXIT_NOT_RUN
    write_prs(env)
    assert rc.main(env.args("status")) == rc.EXIT_OK


def test_secrets_never_reach_output(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("PR_REVIEW_API_KEY", API_KEY)
    rc.register_secrets()
    rc.say(f"token {TOKEN} key {API_KEY}")
    rc.write_file(env.out / "x.txt", f"{TOKEN} {API_KEY}")
    out = capsys.readouterr().out
    assert TOKEN not in out and API_KEY not in out
    written = (env.out / "x.txt").read_text(encoding="utf-8")
    assert TOKEN not in written and API_KEY not in written
