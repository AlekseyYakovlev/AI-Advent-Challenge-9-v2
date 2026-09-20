"""Tests for agent/tools.py's dispatcher: ordering, error tolerance, and scope safety."""

import json

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import memory
from agent.tools import dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat, LongTermMemory, WorkingMemory


async def _create_chat(user_id: int, title: str = "Tools chat") -> int:
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


def _call(
    call_id: str,
    name: str,
    arguments: str,
) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.mark.asyncio
async def test_working_memory_tool_call_writes_row(authenticated_client: AsyncClient) -> None:
    """One save_working_memory call writes exactly one WorkingMemory row (content -> value)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "save_working_memory",
            json.dumps({"key": "current_task_step", "content": "drafting the intro"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["write"]["layer"] == "working"

    async with async_session_factory() as session:
        result = await session.exec(select(WorkingMemory).where(WorkingMemory.chat_id == chat_id))
        rows = result.all()
        assert len(rows) == 1
        assert rows[0].key == "current_task_step"
        assert rows[0].value == "drafting the intro"


@pytest.mark.asyncio
async def test_long_term_memory_tool_call_is_user_scoped(
    authenticated_client: AsyncClient,
) -> None:
    """One save_long_term_memory call writes a user-scoped row, visible via list_long_term_memory."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "save_long_term_memory",
            json.dumps({"key": "favorite_color", "content": "Blue"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is True
    assert results[0]["write"]["layer"] == "long_term"

    async with async_session_factory() as session:
        rows = await memory.list_long_term_memory(session, user_id)
        assert len(rows) == 1
        assert rows[0].key == "favorite_color"
        assert rows[0].value == "Blue"


@pytest.mark.asyncio
async def test_multiple_tool_calls_execute_sequentially_in_order(
    authenticated_client: AsyncClient,
) -> None:
    """Two calls in one turn (long-term then working) both write, in the returned order."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_lt",
            "save_long_term_memory",
            json.dumps({"key": "user_name", "content": "Alex"}),
        ),
        _call(
            "call_wk",
            "save_working_memory",
            json.dumps({"key": "current_task_step", "content": "drafting the intro paragraph"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert [r["tool_call_id"] for r in results] == ["call_lt", "call_wk"]
    assert results[0]["write"]["layer"] == "long_term"
    assert results[1]["write"]["layer"] == "working"

    async with async_session_factory() as session:
        long_term = await memory.list_long_term_memory(session, user_id)
        working = await memory.list_working_memory(session, chat_id)
        assert len(long_term) == 1 and long_term[0].key == "user_name"
        assert len(working) == 1 and working[0].key == "current_task_step"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_entry(authenticated_client: AsyncClient) -> None:
    """An unregistered tool name yields ok=False with an unknown-tool error, raises nothing."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [_call("call_1", "delete_everything", json.dumps({"key": "x", "content": "y"}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False
    assert "unknown tool" in json.loads(results[0]["content"])["error"]


@pytest.mark.asyncio
async def test_malformed_arguments_return_error_entry(authenticated_client: AsyncClient) -> None:
    """Malformed JSON arguments yield ok=False with a malformed-arguments error, no row written."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [_call("call_1", "save_working_memory", "{not json")]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False
    assert "malformed arguments" in json.loads(results[0]["content"])["error"]

    async with async_session_factory() as session:
        rows = await memory.list_working_memory(session, chat_id)
        assert rows == []


@pytest.mark.asyncio
async def test_llm_supplied_scope_is_ignored(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """chat_id/user_id inside LLM arguments cannot redirect a write to another user's chat."""
    victim_user_id = second_authenticated_client.seeded_user_id
    victim_chat_id = await _create_chat(victim_user_id, "Victim chat")

    attacker_user_id = authenticated_client.seeded_user_id
    attacker_chat_id = await _create_chat(attacker_user_id, "Attacker chat")

    calls = [
        _call(
            "call_1",
            "save_working_memory",
            json.dumps(
                {
                    "key": "note",
                    "content": "escalated write",
                    "chat_id": victim_chat_id,
                    "user_id": victim_user_id,
                },
            ),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, attacker_user_id, attacker_chat_id, calls)

    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        attacker_rows = await memory.list_working_memory(session, attacker_chat_id)
        victim_rows = await memory.list_working_memory(session, victim_chat_id)
        assert len(attacker_rows) == 1
        assert attacker_rows[0].value == "escalated write"
        assert victim_rows == []


@pytest.mark.asyncio
async def test_no_write_without_tool_call(authenticated_client: AsyncClient) -> None:
    """Dispatching an empty tool_calls list writes zero rows."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, [])

    assert results == []

    async with async_session_factory() as session:
        working = await memory.list_working_memory(session, chat_id)
        long_term = await memory.list_long_term_memory(session, user_id)
        assert working == []
        assert long_term == []
