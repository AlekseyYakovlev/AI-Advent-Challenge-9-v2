"""Thin CRUD layer owning all reads and writes to the task tables."""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import Task, TaskState, TaskTransition

logger = get_logger(__name__)


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
