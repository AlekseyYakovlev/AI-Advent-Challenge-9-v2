"""Tests for the create_task tool path via agent.tools.dispatch_tool_calls."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import select

from agent.tools import dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat, Task, TaskState, TaskTransition


async def _create_chat(user_id: int, title: str = "Tasks chat") -> int:
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


def _call(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.mark.asyncio
async def test_create_task_tool_persists_task_and_first_transition(
    authenticated_client: AsyncClient,
) -> None:
    """One create_task call persists a Task row in the planning state, correctly scoped."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "create_task",
            json.dumps(
                {
                    "title": "Ship Day 13",
                    "description": "Build the task state machine",
                    "goal": "All five TASK reqs pass",
                },
            ),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        result = await session.exec(select(Task).where(Task.chat_id == chat_id))
        rows = result.all()
        assert len(rows) == 1
        row = rows[0]
        assert row.state is TaskState.PLANNING
        assert row.is_paused is False
        assert row.delegate_to is None
        assert row.title == "Ship Day 13"
        assert row.description == "Build the task state machine"
        assert row.goal == "All five TASK reqs pass"
        assert row.user_id == user_id
        assert row.chat_id == chat_id


@pytest.mark.asyncio
async def test_create_task_writes_creation_transition_row(
    authenticated_client: AsyncClient,
) -> None:
    """The created task has exactly one TaskTransition row: from_state=None, to_state=planning."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "create_task",
            json.dumps(
                {"title": "Task A", "description": "Desc A", "goal": "Goal A"},
            ),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    task_id = json.loads(results[0]["content"])["id"]

    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        rows = result.all()
        assert len(rows) == 1
        assert rows[0].from_state is None
        assert rows[0].to_state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_chat_holds_multiple_concurrent_tasks(authenticated_client: AsyncClient) -> None:
    """Two create_task calls in one dispatch produce two distinct Task rows for the same chat."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "create_task",
            json.dumps({"title": "Task One", "description": "D1", "goal": "G1"}),
        ),
        _call(
            "call_2",
            "create_task",
            json.dumps({"title": "Task Two", "description": "D2", "goal": "G2"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is True
    assert results[1]["ok"] is True

    async with async_session_factory() as session:
        result = await session.exec(select(Task).where(Task.chat_id == chat_id))
        rows = result.all()
        assert len(rows) == 2
        assert rows[0].id != rows[1].id


@pytest.mark.asyncio
async def test_create_task_rejects_delegate_to_argument(authenticated_client: AsyncClient) -> None:
    """An extra delegate_to argument from the LLM is dropped; delegate_to stays None (D-03)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "create_task",
            json.dumps(
                {
                    "title": "Task with delegate",
                    "description": "Desc",
                    "goal": "Goal",
                    "delegate_to": "subagent-x",
                },
            ),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        result = await session.exec(select(Task).where(Task.chat_id == chat_id))
        row = result.first()
        assert row.delegate_to is None


@pytest.mark.asyncio
async def test_task_state_enum_stores_lowercase_values(authenticated_client: AsyncClient) -> None:
    """The raw state column value is the string 'planning', never 'PLANNING'."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    calls = [
        _call(
            "call_1",
            "create_task",
            json.dumps({"title": "Task raw", "description": "D", "goal": "G"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    task_id = json.loads(results[0]["content"])["id"]

    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT state FROM task WHERE id = :task_id"),
            {"task_id": task_id},
        )
        raw_state = result.first()[0]
        assert raw_state == "planning"
