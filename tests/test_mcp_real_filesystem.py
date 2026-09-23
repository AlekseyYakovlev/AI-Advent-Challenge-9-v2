"""Port-free check of the chat tool path against the real filesystem MCP server binary."""

import asyncio
import json
import os
from pathlib import Path

import psutil
import pytest

from agent import mcp_client, mcp_config
from agent.mcp_tools import build_mcp_toolset
from agent.schemas import McpConnectionStatus
from agent.tools import TOOL_REGISTRY, dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat
from tests.conftest import _create_user

FILESYSTEM_EXE: str = os.environ.get(
    "MCP_FILESYSTEM_EXE", r"C:\Users\Aleksey\go\bin\filesystem.exe",
)
ALLOWED_DIR: str = r"C:\Projects\AiAdventAgentV2"
EXPOSED_NAME: str = "mcp__filesystem__list_allowed_directories"
CLEANUP_WAIT_SECONDS: float = 5.0

pytestmark = pytest.mark.skipif(
    not Path(FILESYSTEM_EXE).exists(),
    reason="filesystem.exe not installed",
)


def _filesystem_pids() -> set[int]:
    """Return the PIDs of every running filesystem.exe process."""
    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name") or ""
        if name.lower() == "filesystem.exe":
            pids.add(proc.pid)
    return pids


async def test_list_allowed_directories_with_empty_arguments_via_chat_dispatch() -> None:
    """The real server answers a no-argument call routed through dispatch_tool_calls."""
    protected = _filesystem_pids()
    user_id = await _create_user("realfs", "pw")
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, "Filesystem", FILESYSTEM_EXE, [ALLOWED_DIR], {}, None, True,
        )
        server_id = row.id
        chat = Chat(title="Real filesystem", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id

    try:
        connection = await mcp_client.connect_server(
            user_id, server_id, FILESYSTEM_EXE, [ALLOWED_DIR], None, None,
        )
        assert connection.status == McpConnectionStatus.CONNECTED
        assert "list_allowed_directories" in {tool.name for tool in connection.tools}

        async with async_session_factory() as session:
            toolset = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
            assert EXPOSED_NAME in toolset.bindings

            call = {
                "id": "call_real_1",
                "type": "function",
                "function": {"name": EXPOSED_NAME, "arguments": ""},
            }
            results = await dispatch_tool_calls(
                session, user_id, chat_id, [call], mcp_bindings=toolset.bindings,
            )

        (result,) = results
        assert result["ok"] is True, result["content"]
        assert "aiadventagentv2" in json.loads(result["content"])["content"].lower()
    finally:
        await mcp_client.disconnect_server(user_id, server_id)

    deadline = asyncio.get_running_loop().time() + CLEANUP_WAIT_SECONDS
    leftover = _filesystem_pids() - protected
    while leftover and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.2)
        leftover = _filesystem_pids() - protected
    assert not leftover, f"filesystem.exe from this run still alive: {sorted(leftover)}"
    assert all(psutil.pid_exists(pid) for pid in protected)
