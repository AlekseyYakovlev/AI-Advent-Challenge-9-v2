"""Tests for the headless scheduler runner and the dispatcher tool allowlist."""

import json

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent.tools import dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat, Task

ALLOWLIST = frozenset({"save_long_term_memory"})


async def _create_chat(user_id: int) -> int:
    async with async_session_factory() as session:
        chat = Chat(title="Allowlist chat", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


def _create_task_call() -> dict:
    return {
        "id": "call_create",
        "type": "function",
        "function": {
            "name": "create_task",
            "arguments": json.dumps({"title": "T", "description": "D", "goal": "G"}),
        },
    }


async def _count_tasks() -> int:
    async with async_session_factory() as session:
        return len((await session.exec(select(Task))).all())


@pytest.mark.asyncio
async def test_allowlist_rejects_non_allowed_builtin_tool(
    authenticated_client: AsyncClient,
) -> None:
    """A create_task call outside the allowlist is an unknown tool and writes nothing."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session, user_id, chat_id, [_create_task_call()], allowed_tools=ALLOWLIST,
        )

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert json.loads(results[0]["content"]) == {"error": "unknown tool create_task"}
    assert results[0]["write"] is None
    assert await _count_tasks() == 0


@pytest.mark.asyncio
async def test_no_allowlist_keeps_dispatching_every_tool(
    authenticated_client: AsyncClient,
) -> None:
    """With allowed_tools=None the same call is dispatched as before."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session, user_id, chat_id, [_create_task_call()], allowed_tools=None,
        )

    assert results[0]["ok"] is True
    assert await _count_tasks() == 1


@pytest.mark.asyncio
async def test_allowlist_does_not_restrict_mcp_bindings(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP-bound names are routed to MCP first, even when an allowlist is set."""
    from agent import tools
    from agent.mcp_tools import McpToolBinding

    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    binding = McpToolBinding(
        exposed_name="mcp_files_read",
        user_id=user_id,
        server_id=1,
        server_name="files",
        tool_name="read",
    )
    seen: list[str] = []

    async def fake_dispatch_mcp(bound: McpToolBinding, call_id: str, raw: str) -> dict:
        seen.append(bound.exposed_name)
        return {
            "tool_call_id": call_id,
            "name": bound.exposed_name,
            "ok": True,
            "content": "{}",
            "write": None,
            "arguments": raw,
            "mcp": {"server_name": bound.server_name, "tool": bound.tool_name},
        }

    monkeypatch.setattr(tools, "_dispatch_mcp_call", fake_dispatch_mcp)
    call = {
        "id": "call_mcp",
        "type": "function",
        "function": {"name": "mcp_files_read", "arguments": "{}"},
    }

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session,
            user_id,
            chat_id,
            [call],
            mcp_bindings={"mcp_files_read": binding},
            allowed_tools=ALLOWLIST,
        )

    assert seen == ["mcp_files_read"]
    assert results[0]["ok"] is True
