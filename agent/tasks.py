"""Thin CRUD layer owning all reads and writes to the task tables."""

from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import Task, TaskState, TaskTransition

logger = get_logger(__name__)


class TaskNotFoundError(Exception):
    """Raised when a task_id does not resolve to a row owned by the calling chat/user."""


async def create_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    title: str,
    description: str,
    goal: str,
) -> Task:
    """Create a task in the planning state and write its creation transition row."""
    task = Task(
        user_id=user_id,
        chat_id=chat_id,
        title=title,
        description=description,
        goal=goal,
        state=TaskState.PLANNING,
    )
    session.add(task)
    await session.flush()
    session.add(
        TaskTransition(task_id=task.id, from_state=None, to_state=TaskState.PLANNING, note=""),
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(task)
    logger.info("task_created", chat_id=chat_id, task_id=task.id)
    return task


async def list_tasks_for_chat(session: AsyncSession, chat_id: int) -> list[Task]:
    """Return this chat's tasks, oldest first, as a flat list (no "current" pointer, D-08)."""
    result = await session.exec(
        select(Task).where(Task.chat_id == chat_id).order_by(Task.created_at, Task.id),
    )
    return list(result.all())


async def list_transitions(session: AsyncSession, task_id: int) -> list[TaskTransition]:
    """Return a task's state-change history, oldest first (D-11)."""
    result = await session.exec(
        select(TaskTransition)
        .where(TaskTransition.task_id == task_id)
        .order_by(TaskTransition.created_at, TaskTransition.id),
    )
    return list(result.all())


async def _get_owned_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    task_id: int,
) -> Task:
    """Load a task owned by user_id/chat_id or raise TaskNotFoundError.

    Mitigates the task_id IDOR gap: agent/tools.py::_SCOPE_KEYS only guards
    chat_id/user_id keys in the raw tool arguments and does nothing for a
    legitimate-looking task_id pointing at another chat's row.
    """
    task = await session.get(Task, task_id)
    if task is None or task.chat_id != chat_id or task.user_id != user_id:
        logger.warning("task_access_denied", task_id=task_id, chat_id=chat_id, user_id=user_id)
        raise TaskNotFoundError(f"Task {task_id} not found")
    return task


async def transition_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    task_id: int,
    new_state: TaskState,
    note: str = "",
) -> Task:
    """Move an owned task to a new lifecycle state and append one history row.

    Does not validate that previous_state -> new_state is a legal edge --
    transition-graph legality enforcement is Phase 6's job (TRANS-01).
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)
    previous_state = task.state
    task.state = new_state
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.add(
        TaskTransition(task_id=task.id, from_state=previous_state, to_state=new_state, note=note),
    )
    try:
        await session.commit()
        await session.refresh(task)
    except Exception:
        await session.rollback()
        raise
    logger.info(
        "task_transitioned",
        task_id=task.id,
        chat_id=chat_id,
        from_state=previous_state.value,
        to_state=new_state.value,
    )
    return task


async def set_paused(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    task_id: int,
    is_paused: bool,
) -> Task:
    """Toggle an owned task's is_paused flag without touching its lifecycle state.

    Writes no TaskTransition row: is_paused is orthogonal to state (D-04),
    not a fifth enum value, so pausing/resuming is not a history event.
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)
    task.is_paused = is_paused
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    try:
        await session.commit()
        await session.refresh(task)
    except Exception:
        await session.rollback()
        raise
    logger.info("task_pause_toggled", task_id=task.id, chat_id=chat_id, is_paused=is_paused)
    return task


async def cancel_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    task_id: int,
) -> Task:
    """Move an owned task to the terminal cancelled state and append one history row.

    There is deliberately no legality check on the previous state -- transition-
    graph legality enforcement is Phase 6's job (TRANS-01).
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)
    previous_state = task.state
    task.state = TaskState.CANCELLED
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.add(
        TaskTransition(
            task_id=task.id,
            from_state=previous_state,
            to_state=TaskState.CANCELLED,
            note="",
        ),
    )
    try:
        await session.commit()
        await session.refresh(task)
    except Exception:
        await session.rollback()
        raise
    logger.info(
        "task_cancelled",
        task_id=task.id,
        chat_id=chat_id,
        from_state=previous_state.value,
    )
    return task
