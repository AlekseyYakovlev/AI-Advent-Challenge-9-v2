"""Thin CRUD layer owning all reads and writes to the task tables."""

from datetime import datetime, timezone
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import Task, TaskState, TaskTransition

logger = get_logger(__name__)


class TaskNotFoundError(Exception):
    """Raised when a task_id does not resolve to a row owned by the calling chat/user."""


_FORWARD_EDGES: dict[TaskState, TaskState] = {
    TaskState.PLANNING: TaskState.EXECUTION,
    TaskState.EXECUTION: TaskState.VALIDATION,
    TaskState.VALIDATION: TaskState.DONE,
}

_BACKWARD_EDGES: dict[TaskState, TaskState] = {
    TaskState.EXECUTION: TaskState.PLANNING,
    TaskState.VALIDATION: TaskState.EXECUTION,
}

_TERMINAL_STATES: set[TaskState] = {TaskState.DONE, TaskState.CANCELLED}


class IllegalTransitionError(Exception):
    """Raised when a transition/pause/resume/cancel attempt violates the transition graph."""

    def __init__(
        self,
        task_id: int,
        from_state: TaskState,
        to_state: TaskState,
        reason: str,
    ) -> None:
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(reason)


def _legal_targets(from_state: TaskState) -> list[TaskState]:
    """Return the states reachable in exactly one edge from from_state ([] if terminal)."""
    if from_state in _TERMINAL_STATES:
        return []
    targets = []
    if from_state in _FORWARD_EDGES:
        targets.append(_FORWARD_EDGES[from_state])
    if from_state in _BACKWARD_EDGES:
        targets.append(_BACKWARD_EDGES[from_state])
    return targets


def _is_legal_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """Return whether to_state is reachable from from_state in exactly one edge."""
    return to_state in _legal_targets(from_state)


def build_transition_illegal_prompt(rejected: list[dict[str, Any]]) -> str:
    """Build the re-prompt asking the model to retry legally or explain the illegal attempt (D-08)."""
    lines = [
        f'Task #{entry["task_id"]}: the move from "{entry["from_state"]}" to '
        f'"{entry["to_state"]}" was not legal. Reason: {entry["error"]}.'
        for entry in rejected
    ]
    return (
        "\n".join(lines)
        + "\nEither call transition_task again with a legal next state, or briefly explain to "
        "the user why you attempted that move, in the user's language, without repeating your "
        "whole previous answer."
    )


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


async def list_open_tasks(session: AsyncSession, chat_id: int) -> list[Task]:
    """Return this chat's non-terminal tasks (paused ones included), oldest first."""
    result = await session.exec(
        select(Task)
        .where(Task.chat_id == chat_id)
        .where(Task.state.not_in([TaskState.DONE, TaskState.CANCELLED]))
        .order_by(Task.created_at, Task.id),
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

    Only single-edge moves along the transition graph are accepted -- one step
    forward or one step back; skipping states or exiting a terminal state raises
    IllegalTransitionError (TRANS-01).
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)
    previous_state = task.state

    if not _is_legal_transition(previous_state, new_state):
        legal = _legal_targets(previous_state)
        legal_desc = ", ".join(state.value for state in legal) if legal else "terminal state"
        reason = (
            f"Task {task.id} is in {previous_state.value} and cannot move to "
            f"{new_state.value}; legal next states are: {legal_desc}."
        )
        session.add(
            TaskTransition(
                task_id=task.id,
                from_state=previous_state,
                to_state=new_state,
                note=note,
                rejected=True,
                rejection_reason=reason,
            ),
        )
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        logger.warning(
            "task_transition_rejected",
            task_id=task.id,
            chat_id=chat_id,
            from_state=previous_state.value,
            to_state=new_state.value,
        )
        raise IllegalTransitionError(task.id, previous_state, new_state, reason)

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

    On success, writes no TaskTransition row: is_paused is orthogonal to state
    (D-04), not a fifth enum value, so pausing/resuming is not a history event.
    On refusal (terminal-state pause/resume, or resuming a non-paused task),
    raises IllegalTransitionError and persists one self-loop
    (from_state == to_state == task.state) rejected=True row instead.
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)

    rejection_reason: str | None = None
    if task.state in _TERMINAL_STATES:
        operation = "pause" if is_paused else "resume"
        rejection_reason = (
            f"Task {task.id} is in the terminal state {task.state.value} and cannot be "
            f"{operation}d."
        )
    elif not is_paused and not task.is_paused:
        rejection_reason = f"Task {task.id} is not paused, so there is nothing to resume."

    if rejection_reason is not None:
        session.add(
            TaskTransition(
                task_id=task.id,
                from_state=task.state,
                to_state=task.state,
                note="",
                rejected=True,
                rejection_reason=rejection_reason,
            ),
        )
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        logger.warning(
            "task_pause_toggle_rejected",
            task_id=task.id,
            chat_id=chat_id,
            state=task.state.value,
            is_paused=task.is_paused,
            requested=is_paused,
        )
        raise IllegalTransitionError(task.id, task.state, task.state, rejection_reason)

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

    Rejects once the task is already done or cancelled -- a terminal task's
    outcome cannot be overwritten by a later cancel (TRANS-01).
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)
    previous_state = task.state

    if previous_state in _TERMINAL_STATES:
        reason = f"Task {task.id} is already in the terminal state {previous_state.value}."
        session.add(
            TaskTransition(
                task_id=task.id,
                from_state=previous_state,
                to_state=TaskState.CANCELLED,
                note="",
                rejected=True,
                rejection_reason=reason,
            ),
        )
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        logger.warning(
            "task_cancel_rejected",
            task_id=task.id,
            chat_id=chat_id,
            from_state=previous_state.value,
        )
        raise IllegalTransitionError(task.id, previous_state, TaskState.CANCELLED, reason)

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
