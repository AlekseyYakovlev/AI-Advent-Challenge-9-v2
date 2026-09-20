"""Context engine: open-task injection into build_system_prompt (TASK-04)."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent.context_engine import build_system_prompt
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Settings, Task, TaskState

BASE_PROMPT = "Base prompt"


async def _create_chat(user_id: int | None, title: str = "Task chat") -> int:
    """Insert a Chat row (optionally unowned) with a known-constant base system prompt."""
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, system_prompt=BASE_PROMPT))
        await session.commit()
        return chat.id


async def _create_task(
    user_id: int,
    chat_id: int,
    title: str = "Export chat history",
    goal: str = "Produce a markdown export",
    state: TaskState = TaskState.PLANNING,
    is_paused: bool = False,
) -> Task:
    """Insert a Task row directly (this module tests the read/injection path only)."""
    async with async_session_factory() as session:
        task = Task(
            user_id=user_id,
            chat_id=chat_id,
            title=title,
            goal=goal,
            state=state,
            is_paused=is_paused,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


@pytest.mark.asyncio
async def test_open_task_is_injected_into_system_prompt(
    authenticated_client: AsyncClient,
) -> None:
    """A single planning task appears with the header, its id, title, state, and goal."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task = await _create_task(
        user_id, chat_id, title="Export chat history", goal="markdown export",
    )

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "Open tasks in this chat:" in prompt
    assert f"#{task.id}" in prompt
    assert "Export chat history" in prompt
    assert "state=planning" in prompt
    assert "markdown export" in prompt


@pytest.mark.asyncio
async def test_multiple_open_tasks_are_all_injected(
    authenticated_client: AsyncClient,
) -> None:
    """Three tasks in different non-terminal states all appear by id and title."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task1 = await _create_task(user_id, chat_id, title="Task one", state=TaskState.PLANNING)
    task2 = await _create_task(user_id, chat_id, title="Task two", state=TaskState.EXECUTION)
    task3 = await _create_task(user_id, chat_id, title="Task three", state=TaskState.VALIDATION)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    for task in (task1, task2, task3):
        assert f"#{task.id}" in prompt
        assert task.title in prompt


@pytest.mark.asyncio
async def test_paused_task_is_injected_with_pause_marker(
    authenticated_client: AsyncClient,
) -> None:
    """A paused task's line carries [ON PAUSE] and still shows its state (pause != state)."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    await _create_task(user_id, chat_id, state=TaskState.EXECUTION, is_paused=True)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "[ON PAUSE]" in prompt
    assert "state=execution" in prompt


@pytest.mark.asyncio
async def test_done_task_is_excluded_from_injection(
    authenticated_client: AsyncClient,
) -> None:
    """A done task, as the chat's only task, produces no task block at all."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    await _create_task(user_id, chat_id, title="Finished task", state=TaskState.DONE)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "Open tasks in this chat:" not in prompt
    assert "Finished task" not in prompt


@pytest.mark.asyncio
async def test_cancelled_task_is_excluded_from_injection(
    authenticated_client: AsyncClient,
) -> None:
    """A cancelled task, as the chat's only task, produces no task block at all."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    await _create_task(user_id, chat_id, title="Cancelled task", state=TaskState.CANCELLED)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "Open tasks in this chat:" not in prompt
    assert "Cancelled task" not in prompt


@pytest.mark.asyncio
async def test_chat_with_no_tasks_has_no_task_block(
    authenticated_client: AsyncClient,
) -> None:
    """A chat with zero tasks has no Open tasks header in its prompt."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "Open tasks in this chat:" not in prompt


@pytest.mark.asyncio
async def test_task_injection_is_scoped_to_the_chat(
    authenticated_client: AsyncClient,
) -> None:
    """A task in chat B does not appear in chat A's prompt."""
    user_id = authenticated_client.seeded_user_id
    chat_a = await _create_chat(user_id, "Chat A")
    chat_b = await _create_chat(user_id, "Chat B")
    await _create_task(user_id, chat_b, title="Chat B task")

    async with async_session_factory() as session:
        prompt_a = await build_system_prompt(session, chat_a)

    assert "Open tasks in this chat:" not in prompt_a
    assert "Chat B task" not in prompt_a


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", list(ContextStrategy))
async def test_task_injection_survives_every_compression_strategy(
    authenticated_client: AsyncClient,
    strategy: ContextStrategy,
) -> None:
    """The open-task block is present in the system prompt under every ContextStrategy."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    task = await _create_task(user_id, chat_id, title="Strategy task")

    async with async_session_factory() as session:
        result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
        settings_row = result.one()
        settings_row.strategy = strategy
        session.add(settings_row)
        await session.commit()

        prompt = await build_system_prompt(session, chat_id)

    assert "Open tasks in this chat:" in prompt
    assert f"#{task.id}" in prompt
