"""Check of the real github-mcp-server binary in read-only mode, plus token masking in the API."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import psutil
import pytest
from httpx import AsyncClient

from agent import mcp_client, mcp_config
from agent.mcp_tools import build_mcp_toolset
from agent.schemas import McpConnectionStatus
from agent.tools import TOOL_REGISTRY, dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat
from tests.conftest import _create_user

GITHUB_MCP_EXE: str = os.environ.get(
    "GITHUB_MCP_SERVER_EXE", r"C:\Users\Aleksey\go\bin\github-mcp-server.exe",
)
TOKEN_ENV: str = "GITHUB_PERSONAL_ACCESS_TOKEN"
GITHUB_ARGS: list[str] = ["stdio", "--read-only", "--toolsets=repos,pull_requests"]
OWNER: str = "AlekseyYakovlev"
REPO: str = "AI-Advent-Challenge-9-v2"
EXPOSED_PREFIX: str = "mcp__github__"
EXPOSED_LIST_BRANCHES: str = "mcp__github__list_branches"
READ_TOOLS: frozenset[str] = frozenset({
    "list_branches", "pull_request_read", "list_pull_requests",
    "get_file_contents", "list_commits",
})
WRITE_TOOLS: frozenset[str] = frozenset({
    "create_pull_request", "merge_pull_request", "update_pull_request",
    "create_branch", "create_or_update_file", "push_files",
})
CLEANUP_WAIT_SECONDS: float = 5.0
FAKE_TOKEN: str = "not-a-real-token-value"
BASE: str = "/api/v1/mcp/servers"

requires_real_github = pytest.mark.skipif(
    not Path(GITHUB_MCP_EXE).exists() or not os.environ.get(TOKEN_ENV),
    reason="github-mcp-server.exe or GITHUB_PERSONAL_ACCESS_TOKEN is missing",
)


def _github_pids() -> set[int]:
    """Return the PIDs of every running github-mcp-server.exe process."""
    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name") or ""
        if name.lower() == "github-mcp-server.exe":
            pids.add(proc.pid)
    return pids


def _token() -> str:
    """Return the token from the process environment only."""
    return os.environ[TOKEN_ENV]


async def _wait_for_no_leftovers(protected: set[int]) -> None:
    """Assert that no github-mcp-server.exe started by this run is still alive."""
    deadline = asyncio.get_running_loop().time() + CLEANUP_WAIT_SECONDS
    leftover = _github_pids() - protected
    while leftover and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.2)
        leftover = _github_pids() - protected
    assert not leftover, f"github-mcp-server.exe from this run still alive: {sorted(leftover)}"
    assert all(psutil.pid_exists(pid) for pid in protected)


async def _create_github_row(username: str) -> tuple[int, int, int]:
    """Create a user, a github server row and a chat; return (user_id, server_id, chat_id)."""
    user_id = await _create_user(username, "pw")
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, "github", GITHUB_MCP_EXE, GITHUB_ARGS,
            {TOKEN_ENV: _token()}, None, True,
        )
        server_id = row.id
        chat = Chat(title="Real github", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id
    return user_id, server_id, chat_id


@requires_real_github
async def test_github_read_only_toolset_via_app_connect() -> None:
    """Read-only mode exposes the read tools and none of the write tools."""
    protected = _github_pids()
    user_id, server_id, _chat_id = await _create_github_row("realgh")

    try:
        connection = await mcp_client.connect_server(
            user_id, server_id, GITHUB_MCP_EXE, GITHUB_ARGS, {TOKEN_ENV: _token()}, None,
        )
        assert connection.status == McpConnectionStatus.CONNECTED, (
            f"connect failed: {connection.error_code}"
        )

        async with async_session_factory() as session:
            toolset = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
        names = {
            exposed[len(EXPOSED_PREFIX):]
            for exposed in toolset.bindings
            if exposed.startswith(EXPOSED_PREFIX)
        }
        assert READ_TOOLS <= names, f"missing read tools: {sorted(READ_TOOLS - names)}"
        assert not (WRITE_TOOLS & names), f"write tools exposed: {sorted(WRITE_TOOLS & names)}"
    finally:
        await mcp_client.disconnect_server(user_id, server_id)

    await _wait_for_no_leftovers(protected)


@requires_real_github
async def test_github_list_branches_read_call_succeeds() -> None:
    """A real read call succeeds, which a handshake alone cannot prove for a bad token."""
    protected = _github_pids()
    user_id, server_id, chat_id = await _create_github_row("realghread")

    try:
        async with async_session_factory() as session:
            toolset = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
            assert EXPOSED_LIST_BRANCHES in toolset.bindings

            call: dict[str, Any] = {
                "id": "call_gh_1",
                "type": "function",
                "function": {
                    "name": EXPOSED_LIST_BRANCHES,
                    "arguments": json.dumps({"owner": OWNER, "repo": REPO}),
                },
            }
            results = await dispatch_tool_calls(
                session, user_id, chat_id, [call], mcp_bindings=toolset.bindings,
            )

        (result,) = results
        assert result["ok"] is True, (
            "list_branches failed; check that the token is valid and has access to the repository"
        )
        content = json.loads(result["content"])["content"]
        assert isinstance(content, str) and content.strip()
    finally:
        await mcp_client.disconnect_server(user_id, server_id)

    await _wait_for_no_leftovers(protected)


async def test_github_token_value_is_never_returned_by_api(
    authenticated_client: AsyncClient,
) -> None:
    """A saved token value is masked: create and list responses carry only env_keys."""
    resp = await authenticated_client.post(BASE, json={
        "name": "github",
        "command": GITHUB_MCP_EXE,
        "args": GITHUB_ARGS,
        "env": {TOKEN_ENV: FAKE_TOKEN},
        "cwd": None,
        "enabled": False,
    })
    assert resp.status_code == 201, resp.status_code
    assert FAKE_TOKEN not in resp.text
    assert resp.json()["env_keys"] == [TOKEN_ENV]

    listed = await authenticated_client.get(BASE)
    assert listed.status_code == 200
    assert FAKE_TOKEN not in listed.text
    assert listed.json()[0]["env_keys"] == [TOKEN_ENV]
