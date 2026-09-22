"""Tests for the create_task tool path via agent.tools.dispatch_tool_calls."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import select

from agent import tasks
from agent.tools import TOOL_REGISTRY, build_tool_schemas, dispatch_tool_calls
from shared.database import async_session_factory
from shared.models import Chat, Task, TaskState, TaskTransition


async def _create_chat(user_id: int, title: str = "Tasks chat") -> int:
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


async def _seed_chat(user_id: int, title: str = "Tasks chat") -> int:
    """Create and return the id of a chat owned by user_id."""
    return await _create_chat(user_id, title)


def _call(call_id: str, name: str, arguments: str) -> dict:
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


@pytest.mark.asyncio
async def test_transition_task_moves_state_and_appends_history(
    authenticated_client: AsyncClient,
) -> None:
    """A single transition_task call moves the state and appends one history row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "execution", "note": "plan approved"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.EXECUTION

        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        rows = list(result.all())
        assert len(rows) == 2
        newest = max(rows, key=lambda row: row.id)
        assert newest.from_state is TaskState.PLANNING
        assert newest.to_state is TaskState.EXECUTION
        assert newest.note == "plan approved"


@pytest.mark.asyncio
async def test_transition_task_note_is_optional(authenticated_client: AsyncClient) -> None:
    """Dispatching transition_task without a note key succeeds and defaults note to "" ."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "execution"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition)
            .where(TaskTransition.task_id == task_id)
            .order_by(TaskTransition.id.desc()),
        )
        newest = result.first()
        assert newest.note == ""


@pytest.mark.asyncio
async def test_transition_task_bumps_updated_at(authenticated_client: AsyncClient) -> None:
    """A transition strictly increases the task's updated_at timestamp."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        task_before = await session.get(Task, task_id)
        updated_at_before = task_before.updated_at

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "execution"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        task_after = await session.get(Task, task_id)
        assert task_after.updated_at > updated_at_before


@pytest.mark.asyncio
async def test_transition_task_full_lifecycle(authenticated_client: AsyncClient) -> None:
    """planning -> execution -> validation -> done across three sequential dispatch calls."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    for new_state in ("execution", "validation", "done"):
        calls = [
            _call(
                "call_transition",
                "transition_task",
                json.dumps({"task_id": task_id, "new_state": new_state}),
            ),
        ]
        async with async_session_factory() as session:
            results = await dispatch_tool_calls(session, user_id, chat_id, calls)
        assert results[0]["ok"] is True

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.DONE

        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        rows = list(result.all())
        assert len(rows) == 4


@pytest.mark.asyncio
async def test_transition_task_rejects_cancelled_state(authenticated_client: AsyncClient) -> None:
    """transition_task cannot set a task to cancelled (D-07); Pydantic rejects it up front."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "cancelled"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_transition_task_rejects_unknown_state(authenticated_client: AsyncClient) -> None:
    """An unrecognized new_state value is rejected without mutating the task."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "archived"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_transition_task_other_chats_task_is_rejected(
    authenticated_client: AsyncClient,
) -> None:
    """A task_id belonging to a different chat (same user) is rejected without mutation."""
    user_id = authenticated_client.seeded_user_id
    chat_a = await _seed_chat(user_id, "Chat A")
    chat_b = await _seed_chat(user_id, "Chat B")
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

    result_payload = json.loads(results[0]["content"])
    assert result_payload.get("status") == "error" or "error" in result_payload

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING

        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        rows = list(result.all())
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_transition_task_other_users_task_is_rejected(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A task_id belonging to a different user is rejected without mutation."""
    owner_id = authenticated_client.seeded_user_id
    other_user_id = second_authenticated_client.seeded_user_id
    chat_id = await _seed_chat(owner_id)
    task_id = await _create_task_via_tool(owner_id, chat_id)

    other_chat_id = await _seed_chat(other_user_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "execution"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, other_user_id, other_chat_id, calls)

    result_payload = json.loads(results[0]["content"])
    assert result_payload.get("status") == "error" or "error" in result_payload

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING

        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        rows = list(result.all())
        assert len(rows) == 1


async def _count_transitions(task_id: int) -> int:
    """Return the number of TaskTransition rows for a task."""
    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        return len(list(result.all()))


@pytest.mark.asyncio
async def test_pause_task_tool_sets_flag_without_changing_state(
    authenticated_client: AsyncClient,
) -> None:
    """pause_task sets is_paused True and leaves state/history untouched (D-04)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": task_id, "new_state": "execution"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    assert results[0]["ok"] is True

    transitions_before = await _count_transitions(task_id)

    calls = [_call("call_pause", "pause_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is True
        assert task.state is TaskState.EXECUTION

    assert await _count_transitions(task_id) == transitions_before


@pytest.mark.asyncio
async def test_resume_task_tool_clears_flag_without_changing_state(
    authenticated_client: AsyncClient,
) -> None:
    """resume_task clears is_paused and leaves state/history untouched (D-04)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [_call("call_pause", "pause_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    assert results[0]["ok"] is True

    transitions_before = await _count_transitions(task_id)

    calls = [_call("call_resume", "resume_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False
        assert task.state is TaskState.PLANNING

    assert await _count_transitions(task_id) == transitions_before


@pytest.mark.asyncio
async def test_pause_task_bumps_updated_at(authenticated_client: AsyncClient) -> None:
    """A pause strictly increases the task's updated_at timestamp."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        task_before = await session.get(Task, task_id)
        updated_at_before = task_before.updated_at

    calls = [_call("call_pause", "pause_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    assert results[0]["ok"] is True

    async with async_session_factory() as session:
        task_after = await session.get(Task, task_id)
        assert task_after.updated_at > updated_at_before


@pytest.mark.asyncio
async def test_pause_task_other_users_task_is_rejected(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A task_id belonging to a different user is rejected without mutation (IDOR)."""
    owner_id = authenticated_client.seeded_user_id
    other_user_id = second_authenticated_client.seeded_user_id
    chat_id = await _seed_chat(owner_id)
    task_id = await _create_task_via_tool(owner_id, chat_id)

    other_chat_id = await _seed_chat(other_user_id)

    calls = [_call("call_pause", "pause_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, other_user_id, other_chat_id, calls)

    result_payload = json.loads(results[0]["content"])
    assert result_payload.get("status") == "error" or "error" in result_payload

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False


@pytest.mark.asyncio
async def test_resume_task_other_chats_task_is_rejected(
    authenticated_client: AsyncClient,
) -> None:
    """A task_id belonging to a different chat (same user) is rejected without mutation (IDOR)."""
    user_id = authenticated_client.seeded_user_id
    chat_a = await _seed_chat(user_id, "Chat A")
    chat_b = await _seed_chat(user_id, "Chat B")
    task_id = await _create_task_via_tool(user_id, chat_a)

    calls = [_call("call_pause", "pause_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_a, calls)
    assert results[0]["ok"] is True

    calls = [_call("call_resume", "resume_task", json.dumps({"task_id": task_id}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_b, calls)

    result_payload = json.loads(results[0]["content"])
    assert result_payload.get("status") == "error" or "error" in result_payload

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is True


@pytest.mark.asyncio
async def test_pause_and_resume_are_not_llm_state_changes(
    authenticated_client: AsyncClient,
) -> None:
    """pause_task/resume_task are registered tools with no new_state/state/delegate_to field."""
    schemas = build_tool_schemas()
    by_name = {s["function"]["name"]: s for s in schemas}
    assert "pause_task" in by_name
    assert "resume_task" in by_name
    for name in ("pause_task", "resume_task"):
        properties = by_name[name]["function"]["parameters"].get("properties", {})
        assert "new_state" not in properties
        assert "state" not in properties
        assert "delegate_to" not in properties


@pytest.mark.asyncio
async def test_cancel_is_not_an_llm_tool(authenticated_client: AsyncClient) -> None:
    """cancel_task is never a registered LLM tool and no tool schema exposes 'cancelled' (D-07/D-12)."""
    assert "cancel_task" not in TOOL_REGISTRY
    for schema in build_tool_schemas():
        for prop in schema["function"]["parameters"].get("properties", {}).values():
            assert "cancelled" not in prop.get("enum", [])


@pytest.mark.asyncio
async def test_transition_task_unknown_id_is_rejected(authenticated_client: AsyncClient) -> None:
    """An unknown task_id produces an error payload without raising an exception."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)

    calls = [
        _call(
            "call_transition",
            "transition_task",
            json.dumps({"task_id": 999999, "new_state": "execution"}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    result_payload = json.loads(results[0]["content"])
    assert result_payload.get("status") == "error" or "error" in result_payload

    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == 999999),
        )
        rows = list(result.all())
        assert len(rows) == 0


@pytest.mark.asyncio
async def test_transition_task_execution_to_planning_succeeds(
    authenticated_client: AsyncClient,
) -> None:
    """A one-step-back rework move (execution -> planning) is legal (D-01)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)

    async with async_session_factory() as session:
        task = await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.PLANNING)

    assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_transition_task_validation_to_execution_succeeds(
    authenticated_client: AsyncClient,
) -> None:
    """A one-step-back rework move (validation -> execution) is legal (D-01)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)
    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.VALIDATION)

    async with async_session_factory() as session:
        task = await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)

    assert task.state is TaskState.EXECUTION


@pytest.mark.asyncio
async def test_transition_task_rejects_skip_ahead(authenticated_client: AsyncClient) -> None:
    """planning -> done directly (skipping execution/validation) raises IllegalTransitionError."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        task_before = await session.get(Task, task_id)
        updated_at_before = task_before.updated_at

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.DONE)

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING
        assert task.updated_at == updated_at_before

        result = await session.exec(
            select(TaskTransition)
            .where(TaskTransition.task_id == task_id)
            .order_by(TaskTransition.id),
        )
        rows = list(result.all())
        assert len(rows) == 2
        newest = rows[-1]
        assert newest.rejected is True
        assert newest.from_state is TaskState.PLANNING
        assert newest.to_state is TaskState.DONE
        assert newest.rejection_reason


@pytest.mark.asyncio
async def test_transition_task_rejects_two_steps_back(authenticated_client: AsyncClient) -> None:
    """validation -> planning (two steps back) raises IllegalTransitionError and writes a rejected row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)
    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.VALIDATION)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.PLANNING)

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.VALIDATION

        result = await session.exec(
            select(TaskTransition)
            .where(TaskTransition.task_id == task_id)
            .order_by(TaskTransition.id),
        )
        newest = list(result.all())[-1]
        assert newest.rejected is True


@pytest.mark.asyncio
async def test_transition_task_rejects_self_loop(authenticated_client: AsyncClient) -> None:
    """planning -> planning (self-loop) raises IllegalTransitionError."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.PLANNING)


@pytest.mark.asyncio
async def test_transition_task_rejects_done_terminal_exit(
    authenticated_client: AsyncClient,
) -> None:
    """done has no exit -- moving to execution raises IllegalTransitionError (D-01)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    for state in (TaskState.EXECUTION, TaskState.VALIDATION, TaskState.DONE):
        async with async_session_factory() as session:
            await tasks.transition_task(session, user_id, chat_id, task_id, state)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.DONE


@pytest.mark.asyncio
async def test_transition_task_rejects_cancelled_terminal_exit(
    authenticated_client: AsyncClient,
) -> None:
    """cancelled has no exit -- moving to planning raises IllegalTransitionError (D-01)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.cancel_task(session, user_id, chat_id, task_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.PLANNING)

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.CANCELLED


@pytest.mark.asyncio
async def test_transition_task_other_users_task_raises_not_found_not_illegal(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Ownership resolves before legality -- another user's task_id raises TaskNotFoundError."""
    owner_id = authenticated_client.seeded_user_id
    other_user_id = second_authenticated_client.seeded_user_id
    chat_id = await _seed_chat(owner_id)
    task_id = await _create_task_via_tool(owner_id, chat_id)

    other_chat_id = await _seed_chat(other_user_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.TaskNotFoundError):
            await tasks.transition_task(
                session, other_user_id, other_chat_id, task_id, TaskState.DONE,
            )


def test_build_transition_illegal_prompt_includes_task_details() -> None:
    """The prompt names the task id and both the from-state and to-state values."""
    rejected = [
        {
            "task_id": 4,
            "from_state": "planning",
            "to_state": "done",
            "error": "validation must come first",
        },
    ]
    prompt = tasks.build_transition_illegal_prompt(rejected)
    assert "4" in prompt
    assert "planning" in prompt
    assert "done" in prompt


@pytest.mark.asyncio
async def test_set_paused_true_succeeds_on_non_terminal_task(
    authenticated_client: AsyncClient,
) -> None:
    """Pausing a task in planning/execution/validation succeeds and sets is_paused."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        task = await tasks.set_paused(session, user_id, chat_id, task_id, True)

    assert task.is_paused is True
    assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_set_paused_true_rejects_done_task(authenticated_client: AsyncClient) -> None:
    """Pausing a DONE task raises IllegalTransitionError; is_paused stays unchanged."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    for state in (TaskState.EXECUTION, TaskState.VALIDATION, TaskState.DONE):
        async with async_session_factory() as session:
            await tasks.transition_task(session, user_id, chat_id, task_id, state)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.set_paused(session, user_id, chat_id, task_id, True)

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False


@pytest.mark.asyncio
async def test_set_paused_true_rejects_cancelled_task(authenticated_client: AsyncClient) -> None:
    """Pausing a CANCELLED task raises IllegalTransitionError."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.cancel_task(session, user_id, chat_id, task_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.set_paused(session, user_id, chat_id, task_id, True)


@pytest.mark.asyncio
async def test_set_paused_false_succeeds_on_paused_task(
    authenticated_client: AsyncClient,
) -> None:
    """Resuming a paused, non-terminal task clears is_paused and leaves state unchanged."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.set_paused(session, user_id, chat_id, task_id, True)

    async with async_session_factory() as session:
        task = await tasks.set_paused(session, user_id, chat_id, task_id, False)

    assert task.is_paused is False
    assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_set_paused_false_rejects_when_not_paused(
    authenticated_client: AsyncClient,
) -> None:
    """Resuming a task whose is_paused is already False raises IllegalTransitionError."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    rows_before = await _count_transitions(task_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.set_paused(session, user_id, chat_id, task_id, False)

    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition)
            .where(TaskTransition.task_id == task_id)
            .order_by(TaskTransition.id),
        )
        rows = list(result.all())
        assert len(rows) == rows_before + 1
        newest = rows[-1]
        assert newest.rejected is True
        assert newest.from_state is TaskState.PLANNING
        assert newest.to_state is TaskState.PLANNING
        assert newest.rejection_reason


@pytest.mark.asyncio
async def test_set_paused_success_paths_write_no_transition_row(
    authenticated_client: AsyncClient,
) -> None:
    """A successful pause and a successful resume each add no TaskTransition row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    rows_before_pause = await _count_transitions(task_id)
    async with async_session_factory() as session:
        await tasks.set_paused(session, user_id, chat_id, task_id, True)
    assert await _count_transitions(task_id) == rows_before_pause

    async with async_session_factory() as session:
        await tasks.set_paused(session, user_id, chat_id, task_id, False)
    assert await _count_transitions(task_id) == rows_before_pause


@pytest.mark.asyncio
async def test_set_paused_rejection_paths_write_one_self_loop_row_each(
    authenticated_client: AsyncClient,
) -> None:
    """Pause-on-terminal, resume-on-terminal, and resume-no-op each append one self-loop row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    for state in (TaskState.EXECUTION, TaskState.VALIDATION, TaskState.DONE):
        async with async_session_factory() as session:
            await tasks.transition_task(session, user_id, chat_id, task_id, state)

    rows_before = await _count_transitions(task_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.set_paused(session, user_id, chat_id, task_id, True)
    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.set_paused(session, user_id, chat_id, task_id, False)

    rows_after = await _count_transitions(task_id)
    assert rows_after == rows_before + 2

    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition)
            .where(TaskTransition.task_id == task_id)
            .order_by(TaskTransition.id),
        )
        rows = list(result.all())[-2:]
        for row in rows:
            assert row.rejected is True
            assert row.from_state is TaskState.DONE
            assert row.to_state is TaskState.DONE
            assert row.note == ""
            assert row.rejection_reason


@pytest.mark.asyncio
async def test_cancel_task_succeeds_on_non_terminal_task(
    authenticated_client: AsyncClient,
) -> None:
    """Cancelling a task in planning/execution/validation succeeds and appends a history row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        task = await tasks.cancel_task(session, user_id, chat_id, task_id)

    assert task.state is TaskState.CANCELLED


@pytest.mark.asyncio
async def test_cancel_task_rejects_already_done(authenticated_client: AsyncClient) -> None:
    """Cancelling a DONE task raises IllegalTransitionError and appends a rejected row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    for state in (TaskState.EXECUTION, TaskState.VALIDATION, TaskState.DONE):
        async with async_session_factory() as session:
            await tasks.transition_task(session, user_id, chat_id, task_id, state)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.cancel_task(session, user_id, chat_id, task_id)

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.DONE

        result = await session.exec(
            select(TaskTransition)
            .where(TaskTransition.task_id == task_id)
            .order_by(TaskTransition.id),
        )
        newest = list(result.all())[-1]
        assert newest.rejected is True
        assert newest.from_state is TaskState.DONE
        assert newest.to_state is TaskState.CANCELLED


@pytest.mark.asyncio
async def test_cancel_task_rejects_already_cancelled(authenticated_client: AsyncClient) -> None:
    """Cancelling an already-CANCELLED task raises IllegalTransitionError and appends a row."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.cancel_task(session, user_id, chat_id, task_id)

    async with async_session_factory() as session:
        with pytest.raises(tasks.IllegalTransitionError):
            await tasks.cancel_task(session, user_id, chat_id, task_id)


@pytest.mark.asyncio
async def test_resume_then_legal_transition_still_succeeds(
    authenticated_client: AsyncClient,
) -> None:
    """After resume, a legal transition_task on that task still succeeds (TRANS-03)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    async with async_session_factory() as session:
        await tasks.set_paused(session, user_id, chat_id, task_id, True)
    async with async_session_factory() as session:
        await tasks.set_paused(session, user_id, chat_id, task_id, False)

    async with async_session_factory() as session:
        task = await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)

    assert task.state is TaskState.EXECUTION
