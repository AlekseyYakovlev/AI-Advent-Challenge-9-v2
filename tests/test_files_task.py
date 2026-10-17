"""Tests for scripts/files_task.py against a mocked endpoint and real temporary git repositories."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "files_task.py"
API_KEY = "aia_test_secret_key"
AGENT = "http://test-agent"

if shutil.which("git") is None:
    pytest.skip("git is not installed", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("files_task", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
files_task = importlib.util.module_from_spec(_spec)
sys.modules["files_task"] = files_task
_spec.loader.exec_module(files_task)


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
def clean_repo(tmp_path: Path) -> Path:
    """A clean repository on branch main with one committed README.md."""
    repo = tmp_path / "files-root"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    _git(repo, "branch", "-M", "main")
    return repo


def test_check_root_not_a_work_tree(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    reason = files_task.check_root(folder, allow_own_repo=False)
    assert reason is not None and "not a git work tree" in reason


def test_check_root_dirty(clean_repo: Path) -> None:
    (clean_repo / "stray.txt").write_text("x\n", encoding="utf-8")
    reason = files_task.check_root(clean_repo, allow_own_repo=True)
    assert reason is not None and "not clean" in reason


def test_check_root_own_repo_refused() -> None:
    reason = files_task.check_root(REPO_ROOT, allow_own_repo=False)
    assert reason is not None and "--allow-own-repo" in reason


def test_check_root_clean_ok(clean_repo: Path) -> None:
    assert files_task.check_root(clean_repo, allow_own_repo=True) is None
    assert files_task.check_root(clean_repo, allow_own_repo=False) is None


async def test_amain_dirty_root_refused_without_http(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_repo / "stray.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("FILES_TASK_API_KEY", API_KEY)
    with respx.mock(base_url=AGENT, assert_all_called=False) as router:
        code = await files_task.amain(
            ["--root", str(clean_repo), "--goal", "g", "--out", str(tmp_path / "out"),
             "--agent-url", AGENT]  # fmt: skip
        )
        assert not router.calls
    assert code == 3


def _make_patch_repo(repo: Path) -> None:
    (repo / "docs").mkdir()
    (repo / "docs" / "new.md").write_text("a\nb\n", encoding="utf-8")
    (repo / "README.md").write_text("hello\nworld\n", encoding="utf-8")


def test_collect_patch_includes_new_file_and_applies(clean_repo: Path, tmp_path: Path) -> None:
    _make_patch_repo(clean_repo)
    patch = files_task.collect_patch(clean_repo)
    assert "new file mode" in patch
    assert "+++ b/docs/new.md" in patch
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(clean_repo), str(clone)], check=True, capture_output=True)
    patch_file = tmp_path / "run.patch"
    patch_file.write_bytes(patch.encode("utf-8"))
    result = subprocess.run(
        ["git", "apply", "--check", str(patch_file)], cwd=clone, capture_output=True
    )
    assert result.returncode == 0


def test_patch_stats_counts(clean_repo: Path) -> None:
    _make_patch_repo(clean_repo)
    stats = files_task.patch_stats(files_task.collect_patch(clean_repo))
    by_path = {item["path"]: item for item in stats["files"]}
    assert by_path["docs/new.md"]["new"] is True
    assert by_path["docs/new.md"]["added"] == 2
    assert by_path["README.md"]["new"] is False
    assert by_path["README.md"]["added"] == 1
    assert by_path["README.md"]["removed"] == 0
    assert stats["total_added"] == 3
    assert stats["total_removed"] == 0


def _mock_routes(router: respx.MockRouter, messages_side_effect: Any) -> None:
    router.get("/api/v1/llm-providers/models").respond(
        200,
        json=[
            {
                "provider_id": 3,
                "name": "LM",
                "kind": "lm_studio",
                "models": [{"id": "other/model"}, {"id": "qwen/qwen3.5-9b"}],
            }
        ],
    )
    router.get("/api/v1/mcp/servers").respond(
        200,
        json=[
            {
                "id": 1, "name": "local-git", "enabled": True,
                "args": ["scripts/project_git.py"],
                "connection": {"status": "connected", "tools": [{"name": "git_status"}]},
            },
            {
                "id": 2, "name": "project-files", "enabled": True,
                "args": ["x/project_git.py", "--root"],
                "connection": {"status": "connected", "tools": [{"name": "read_file"}]},
            },
        ],
    )  # fmt: skip
    router.post("/api/v1/chats").respond(200, json={"id": 7})
    router.post("/api/v1/chats/7/messages").mock(side_effect=messages_side_effect)


def _settings_check(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["temperature"] == 0.0
    assert body["chat_id"] == 7
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    return httpx.Response(200, json=body)


async def _run(
    root: Path, out: Path, router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setenv("FILES_TASK_API_KEY", API_KEY)
    router.put("/api/v1/settings").mock(side_effect=_settings_check)
    return await files_task.amain(
        ["--root", str(root), "--goal", "создай ADR", "--out", str(out), "--agent-url", AGENT]
    )


async def test_full_run_done(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def messages(request: httpx.Request) -> httpx.Response:
        target = clean_repo / "docs" / "adr"
        target.mkdir(parents=True)
        (target / "0001-x.md").write_text("# ADR\n", encoding="utf-8")
        answer = "Создан файл docs/adr/0001-x.md"
        calls = [
            {"tool_call_id": "1", "name": "a", "server": "project-files", "tool": "list_dir",
             "arguments": f"{{\"path\": \"{clean_repo}\"}}", "ok": True, "result": "README.md\n",
             "truncated": False},
            {"tool_call_id": "2", "name": "b", "server": "project-files", "tool": "write_file",
             "arguments": "{}", "ok": True, "result": f"ok {API_KEY}", "truncated": False},
        ]  # fmt: skip
        return httpx.Response(
            200,
            json={"chat_id": 7, "user_message_id": 1, "assistant_message_id": 2,
                  "content": answer, "model": "qwen/qwen3.5-9b", "provider_id": 3,
                  "tool_calls": calls},
        )  # fmt: skip

    out = tmp_path / "out"
    with respx.mock(base_url=AGENT) as router:
        _mock_routes(router, messages)
        code = await _run(clean_repo, out, router, monkeypatch)
    assert code == 0
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "done"
    assert record["goal"] == "создай ADR"
    assert record["commit"]
    assert record["model"] == "qwen/qwen3.5-9b"
    assert record["settings"]["temperature"] == 0.0
    assert record["tool_call_count"] == 2
    assert "local-git" in "".join(record["warnings"])
    assert "project-files" in "".join(record["warnings"])
    assert record["root"] == "files-root"
    assert "docs/adr/0001-x.md" in [f["path"] for f in record["patch"]["files"]]
    patch_text = (out / "run.patch").read_text(encoding="utf-8")
    assert "docs/adr/0001-x.md" in patch_text
    answer_text = (out / "answer.md").read_text(encoding="utf-8")
    assert "Создан файл docs/adr/0001-x.md" in answer_text
    for name in ("run.json", "run.patch", "answer.md"):
        text = (out / name).read_text(encoding="utf-8")
        assert API_KEY not in text
        assert str(clean_repo.resolve()) not in text
        assert str(clean_repo.resolve()).replace("\\", "/") not in text


@pytest.mark.parametrize("status", [409, 429, 503])
async def test_blocked_statuses(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    out = tmp_path / "out"
    with respx.mock(base_url=AGENT) as router:
        _mock_routes(router, lambda request: httpx.Response(status, json={"detail": "busy"}))
        code = await _run(clean_repo, out, router, monkeypatch)
    assert code == 2
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "blocked"
    assert record["http_status"] == status


async def test_connect_error_blocked(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    out = tmp_path / "out"
    with respx.mock(base_url=AGENT) as router:
        _mock_routes(router, boom)
        code = await _run(clean_repo, out, router, monkeypatch)
    assert code == 2
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "blocked"


async def test_empty_content_failed(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    with respx.mock(base_url=AGENT) as router:
        _mock_routes(
            router,
            lambda request: httpx.Response(
                200, json={"chat_id": 7, "content": "", "tool_calls": []}
            ),
        )
        code = await _run(clean_repo, out, router, monkeypatch)
    assert code == 1
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed"
