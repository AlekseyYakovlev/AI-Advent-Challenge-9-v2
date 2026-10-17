"""Tests for the code-initiated MCP caller of the release pipeline."""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent import ws
from agent.mcp_client import cleanup_server
from agent.release_command import MSG_SERVER_NOT_CONFIGURED
from agent.release_mcp import ReleaseMcp, ReleaseRows
from agent.tool_frames import tool_call_frame
from shared.config import settings
from test_mcp_release_tools import SERVER, release_repo  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

USER_ID = 9101
SERVER_ID = 9101


def _row(repo: Path, command: str | None = None, server_id: int = SERVER_ID) -> Any:
    return SimpleNamespace(
        id=server_id,
        name="release-git",
        command=command or sys.executable,
        args_json=json.dumps([SERVER, f"--root={repo}", "--allow-release"]),
        env_json="{}",
        cwd=None,
        enabled=True,
    )


class Collector:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


def test_tool_call_frame_matches_ws_copy() -> None:
    result = {
        "tool_call_id": "t1",
        "name": "mcp__s__t",
        "mcp": {"server_name": "s", "tool": "t"},
        "arguments": "{}",
        "ok": True,
        "result_text": "x" * 5000,
        "content": "ignored",
    }
    assert tool_call_frame(result) == ws._tool_call_frame(result)
    assert tool_call_frame(result)["truncated"] is True


async def test_release_context_sends_tool_card(release_repo: Path) -> None:  # noqa: F811
    sink = Collector()
    call = ReleaseMcp(USER_ID, ReleaseRows(release=_row(release_repo), github=None), sink)
    try:
        outcome = await call("release", "release_context", {})
        assert outcome.ok is True
        json.loads(outcome.text.splitlines()[1])
        assert len(sink.frames) == 1
        frame = sink.frames[0]
        assert frame["type"] == "tool_call"
        assert frame["server"] == "release-git"
        assert frame["tool"] == "release_context"
        assert frame["ok"] is True
        assert frame["name"].startswith("mcp__release-git")
    finally:
        await cleanup_server(USER_ID, SERVER_ID)


async def test_tool_error_returns_reason_and_failed_card(release_repo: Path) -> None:  # noqa: F811
    sink = Collector()
    call = ReleaseMcp(USER_ID, ReleaseRows(release=_row(release_repo), github=None), sink)
    try:
        outcome = await call(
            "release", "release_create_tag", {"version": "bad", "message": "m", "commit": "x"}
        )
        assert outcome.ok is False
        assert outcome.text
        assert sink.frames[-1]["ok"] is False
    finally:
        await cleanup_server(USER_ID, SERVER_ID)


async def test_missing_github_row_is_reported_with_card(release_repo: Path) -> None:  # noqa: F811
    sink = Collector()
    call = ReleaseMcp(USER_ID, ReleaseRows(release=_row(release_repo), github=None), sink)
    assert call.has_github is False
    outcome = await call("github", "github_create_draft_release", {})
    assert outcome.ok is False
    assert outcome.text == "MCP server 'github' is not configured"
    assert len(sink.frames) == 1


async def test_unspawnable_server_is_not_connected(tmp_path: Path) -> None:
    sink = Collector()
    row = _row(tmp_path, command=str(tmp_path / "no-such-binary"), server_id=SERVER_ID + 1)
    call = ReleaseMcp(USER_ID, ReleaseRows(release=row, github=None), sink)
    try:
        outcome = await call("release", "release_context", {})
        assert outcome.ok is False
        assert outcome.text == "MCP server 'release-git' is not connected"
        assert sink.frames[0]["ok"] is False
    finally:
        await cleanup_server(USER_ID, SERVER_ID + 1)


async def test_long_result_is_cut(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(settings, "MCP_TOOL_RESULT_MAX_CHARS", 50)
    sink = Collector()
    call = ReleaseMcp(USER_ID, ReleaseRows(release=_row(release_repo), github=None), sink)
    try:
        outcome = await call("release", "release_context", {})
        assert outcome.truncated is True
        assert "…[truncated" in outcome.text
    finally:
        await cleanup_server(USER_ID, SERVER_ID)


def test_has_github_true_when_row_present(tmp_path: Path) -> None:
    gh = _row(tmp_path)
    call = ReleaseMcp(USER_ID, ReleaseRows(release=_row(tmp_path), github=gh), Collector())
    assert call.has_github is True


def test_server_not_configured_message_mentions_server() -> None:
    assert "release-git" in MSG_SERVER_NOT_CONFIGURED
