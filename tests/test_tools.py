"""Tests for agent/tools.py's dispatcher: ordering, error tolerance, and scope safety."""

import json

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import memory, tasks
from agent.tools import dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat, LongTermMemory, TaskState, WorkingMemory


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


async def _create_task_via_tool(
    user_id: int,
    chat_id: int,
    title: str = "Task",
    description: str = "Description",
    goal: str = "Goal",
) -> int:
    """Dispatch a create_task tool call and return the created task's id."""
    calls = [
        _call(
            "call_create",
            "create_task",
            json.dumps({"title": title, "description": description, "goal": goal}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    return json.loads(results[0]["content"])["id"]


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


@pytest.mark.asyncio
async def test_illegal_transition_task_call_is_ok_false_with_code(
    authenticated_client: AsyncClient,
) -> None:
    """An illegal transition_task call returns ok=False, write=None, error payload (D-05)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "done"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False
    assert results[0]["write"] is None
    assert results[0]["name"] == "transition_task"
    payload = json.loads(results[0]["content"])
    assert payload["status"] == "error"
    assert payload["code"] == "illegal_transition"
    assert payload["task_id"] == task_id
    assert payload["from_state"] == "planning"
    assert payload["to_state"] == "done"
    assert payload["error"]


@pytest.mark.asyncio
async def test_transition_task_other_chats_task_is_not_found(
    authenticated_client: AsyncClient,
) -> None:
    """A transition_task call for another chat's task_id returns ok=False, code=not_found (D-06)."""
    user_id = authenticated_client.seeded_user_id
    chat_a = await _create_chat(user_id, "Chat A")
    chat_b = await _create_chat(user_id, "Chat B")
    task_id = await _create_task_via_tool(user_id, chat_a)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "execution"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_b, calls)

    assert results[0]["ok"] is False
    payload = json.loads(results[0]["content"])
    assert payload["code"] == "not_found"


@pytest.mark.asyncio
async def test_pause_task_on_done_task_is_illegal_transition(
    authenticated_client: AsyncClient,
) -> None:
    """Pausing a DONE task returns ok=False, code=illegal_transition, self-loop states."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)
    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.VALIDATION)
    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.DONE)

    calls = [_call("call_pause", "pause_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False
    payload = json.loads(results[0]["content"])
    assert payload["code"] == "illegal_transition"
    assert payload["from_state"] == payload["to_state"] == "done"


@pytest.mark.asyncio
async def test_resume_task_not_paused_is_illegal_transition(
    authenticated_client: AsyncClient,
) -> None:
    """Resuming a task that is not paused returns ok=False with name and matching states."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [_call("call_resume", "resume_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False
    assert results[0]["name"] == "resume_task"
    payload = json.loads(results[0]["content"])
    assert payload["code"] == "illegal_transition"
    assert payload["from_state"] == payload["to_state"]


@pytest.mark.asyncio
async def test_rejected_call_does_not_abort_later_calls_in_same_turn(
    authenticated_client: AsyncClient,
) -> None:
    """A rejected call in a multi-call turn does not stop later calls from dispatching."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_illegal",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "done"}),
        ),
        _call(
            "call_memory",
            "save_working_memory",
            json.dumps({"key": "note", "content": "still runs"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False
    assert results[1]["ok"] is True
    assert results[1]["write"]["layer"] == "working"
