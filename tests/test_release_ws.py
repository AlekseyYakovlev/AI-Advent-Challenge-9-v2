"""The /release command over WebSocket against a temp repository with a local bare origin."""

import asyncio
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
from sqlmodel import select
from starlette.testclient import TestClient

from agent import state as agent_state
from agent.main import app
from agent.release_command import MSG_NO_PLAN, MSG_PLAN_STALE, MSG_USAGE
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Message
from tests.conftest import login_test_client

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

COMPLETIONS = f"{settings.LM_STUDIO_BASE_URL}/v1/chat/completions"
MODEL = "release-test-model"
WS_ORIGIN = "http://localhost:8000"
REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = str(REPO_ROOT / "mcp_servers" / "project_git.py")
FRAME_END = ("release_plan", "release_result", "help_notice", "error")
CHANGELOG = "### Добавлено\n- Фаза 90: добавлен alpha\n\n### Исправлено\n- Фаза 90: исправлен alpha"
DECISION = json.dumps(
    {"version": "v2.0", "title": "Тестовый релиз", "rationale": "Есть новая функциональность"},
    ensure_ascii=False,
)


@pytest.fixture(autouse=True)
def _quiet_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence background title and fact jobs."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *a, **k: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *a, **k: None)
    agent_state.release_plans.clear()


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    done = subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "-c", "core.autocrlf=false",
         "-c", "init.defaultBranch=main", *args],
        cwd=repo, env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return done.stdout.decode("utf-8", errors="replace").strip()


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Working repo on main with tag v1.0 pushed to a local bare origin and four new commits."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.name", "T")
    _git(work, "config", "user.email", "t@t")
    _commit(work, "a.txt", "one\n", "initial")
    _git(work, "tag", "-a", "v1.0", "-m", "v1.0")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main", "v1.0")
    _commit(work, "alpha.txt", "a\n", "feat(90-01): add alpha")
    _commit(work, "alpha.txt", "b\n", "fix(90-02): fix alpha")
    _commit(work, "x.txt", "x\n", "chore: bump")
    _commit(work, ".planning/p.md", "plan\n", "docs(90-01): plan")
    return work


def _mock_llm() -> respx.Route:
    """Serve the changelog first, then the decision JSON for every later call."""
    calls: list[int] = []

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        text = CHANGELOG if len(calls) == 1 else DECISION
        return httpx.Response(
            200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        )

    return respx.post(COMPLETIONS).mock(side_effect=_side_effect)


def _add_release_server(client: TestClient, repo: Path) -> None:
    created = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "release-git",
            "command": sys.executable,
            "args": [SERVER, f"--root={repo}", "--allow-release", "--timeout=30"],
        },
    )
    assert created.status_code == 201, created.text
    resp = client.post(f"/api/v1/mcp/servers/{created.json()['id']}/connect")
    assert resp.json()["connection"]["status"] == "connected", resp.text


def _drain(ws: Any, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Send one frame and collect frames up to the first terminal one."""
    ws.send_json(body)
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") in FRAME_END:
            return frames


def _msg(content: str) -> dict[str, Any]:
    return {"content": content, "model": MODEL}


def _tools(frames: list[dict[str, Any]]) -> list[str]:
    return [f["tool"] for f in frames if f.get("type") == "tool_call"]


def _last(frames: list[dict[str, Any]]) -> dict[str, Any]:
    return frames[-1]


def _open(client: TestClient) -> tuple[int, Any]:
    chat_id = client.post("/api/v1/chats", json={"title": "Release"}).json()["id"]
    return chat_id, client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN})


def _confirm_body(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "release_confirm",
        "plan_id": plan["plan_id"],
        "version": plan["version"],
        "title": plan["title"],
        "model": MODEL,
        "provider_id": None,
    }


async def _message_count(chat_id: int) -> int:
    async with async_session_factory() as session:
        rows = await session.exec(select(Message).where(Message.chat_id == chat_id))
        return len(list(rows.all()))


@respx.mock
def test_release_plan_streams_tool_cards_and_stores_nothing(repo: Path) -> None:
    _mock_llm()
    with TestClient(app) as client:
        login_test_client(client)
        _add_release_server(client, repo)
        chat_id, conn = _open(client)
        with conn as ws:
            frames = _drain(ws, _msg("/release"))
        plan = _last(frames)
        assert plan["type"] == "release_plan", plan
        assert "release_context" in _tools(frames)
        assert "release_range_commits" in _tools(frames)
        assert plan["candidates"] == ["v2.0", "v1.1"]
        assert plan["version"] == "v2.0"
        assert plan["counts"]["dropped"] >= 2
        assert chat_id in agent_state.release_plans
        assert asyncio.run(_message_count(chat_id)) == 0


@respx.mock
def test_confirm_frame_commits_tags_pushes_and_marks_release_unavailable(
    repo: Path, tmp_path: Path
) -> None:
    _mock_llm()
    with TestClient(app) as client:
        login_test_client(client)
        _add_release_server(client, repo)
        _, conn = _open(client)
        with conn as ws:
            plan = _last(_drain(ws, _msg("/release")))
            frames = _drain(ws, _confirm_body(plan))
    result = _last(frames)
    assert result["type"] == "release_result"
    assert result["outcome"] == "partial"
    tools = _tools(frames)
    for name in ("release_commit_changelog", "release_create_tag", "release_push"):
        assert name in tools
    assert "github_create_draft_release" not in tools
    steps = {s["key"]: s["status"] for s in result["steps"]}
    assert steps["release"] == "unavailable"
    assert _git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split() == ["CHANGELOG.md"]
    assert _git(repo, "cat-file", "-t", "v2.0") == "tag"
    assert "v2.0 Week 4: Тестовый релиз" in _git(repo, "tag", "-l", "--format=%(contents)", "v2.0")
    origin = tmp_path / "origin.git"
    assert _git(origin, "tag", "-l", "v2.0") == "v2.0"
    assert _git(origin, "rev-parse", "main") == _git(repo, "rev-parse", "HEAD")


@respx.mock
def test_typed_confirm_after_partial_skips_done_steps(repo: Path) -> None:
    _mock_llm()
    with TestClient(app) as client:
        login_test_client(client)
        _add_release_server(client, repo)
        _, conn = _open(client)
        with conn as ws:
            plan = _last(_drain(ws, _msg("/release")))
            first = _drain(ws, _confirm_body(plan))
            assert _last(first)["outcome"] == "partial"
            frames = _drain(ws, _msg("/release confirm"))
    result = _last(frames)
    assert result["type"] == "release_result"
    steps = {s["key"]: s["status"] for s in result["steps"]}
    assert steps["commit"] == steps["tag"] == steps["push"] == "skipped"
    tools = _tools(frames)
    for name in ("release_commit_changelog", "release_create_tag", "release_push"):
        assert name not in tools


@respx.mock
def test_untracked_file_blocks_the_plan(repo: Path) -> None:
    _mock_llm()
    (repo / "scratch_file.txt").write_text("tmp\n", encoding="utf-8")
    with TestClient(app) as client:
        login_test_client(client)
        _add_release_server(client, repo)
        _, conn = _open(client)
        with conn as ws:
            frames = _drain(ws, _msg("/release"))
    plan = _last(frames)
    assert plan["type"] == "release_plan"
    assert "scratch_file.txt" in (plan["blocked"] or "")


def test_confirm_without_plan_gets_notice() -> None:
    with TestClient(app) as client:
        login_test_client(client)
        _, conn = _open(client)
        with conn as ws:
            frame = _last(_drain(ws, _msg("/release confirm")))
    assert frame["type"] == "help_notice"
    assert frame["text"] == MSG_NO_PLAN


def test_release_without_server_names_release_git() -> None:
    with TestClient(app) as client:
        login_test_client(client)
        _, conn = _open(client)
        with conn as ws:
            frame = _last(_drain(ws, _msg("/release")))
    assert frame["type"] == "help_notice"
    assert "release-git" in frame["text"]


@respx.mock
def test_confirm_with_wrong_plan_id_is_stale(repo: Path) -> None:
    _mock_llm()
    with TestClient(app) as client:
        login_test_client(client)
        _add_release_server(client, repo)
        _, conn = _open(client)
        with conn as ws:
            plan = _last(_drain(ws, _msg("/release")))
            body = _confirm_body(plan)
            body["plan_id"] = "0" * 32
            frame = _last(_drain(ws, body))
    assert frame["type"] == "help_notice"
    assert MSG_PLAN_STALE in frame["text"]


def test_release_with_bad_argument_gets_usage() -> None:
    with TestClient(app) as client:
        login_test_client(client)
        _, conn = _open(client)
        with conn as ws:
            frame = _last(_drain(ws, _msg("/release foo")))
    assert frame["type"] == "help_notice"
    assert frame["text"] == MSG_USAGE
