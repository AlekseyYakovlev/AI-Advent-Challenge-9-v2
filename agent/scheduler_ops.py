"""User-scoped scheduler operations shared by REST routes and LLM tools."""

from datetime import datetime, timezone

from sqlalchemy import func, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.events import hub
from agent.schedule import (
    MSG_TITLE_PROMPT_REQUIRED,
    PROMPT_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    ScheduleValidationError,
    as_aware_utc,
    build_schedule_spec,
    initial_next_run,
    next_run_on_resume,
)
from agent.scheduler import (
    MSG_ALREADY_FINISHED,
    RunAlreadyActiveError,
    SchedulerConflictError,
    build_task_out,
    scheduler,
)
from agent.scheduler_schemas import ScheduledTaskOut, task_deleted_frame, task_updated_frame
from shared.config import settings
from shared.logger import get_logger
from shared.models import ScheduledTask, ScheduledTaskStatus, TaskRun

logger = get_logger(__name__)

MODEL_MAX_LENGTH = 200

MSG_TOO_MANY_TASKS = "Слишком много активных заданий (максимум {n})"
MSG_TITLE_TOO_LONG = f"Название не длиннее {TITLE_MAX_LENGTH} символов"
MSG_PROMPT_TOO_LONG = f"Промпт не длиннее {PROMPT_MAX_LENGTH} символов"
MSG_MODEL_REQUIRED = "Выберите модель"
MSG_MODEL_TOO_LONG = f"Название модели не длиннее {MODEL_MAX_LENGTH} символов"
MSG_NOT_ACTIVE = "Задание не активно"
MSG_NOT_PAUSED = "Задание не на паузе"
MSG_ALREADY_RUNNING = "Задание уже выполняется"
MSG_FINAL_RUN_IN_PROGRESS = "Задание выполняет последний запуск"

_LIVE_STATUSES = (ScheduledTaskStatus.ACTIVE, ScheduledTaskStatus.PAUSED)


class SchedulerNotFoundError(Exception):
    """The job or run does not exist or belongs to another user."""


def _validate_text_fields(title: str, prompt: str, model: str) -> tuple[str, str, str]:
    """Strip and validate the free-text fields of a new job."""
    title, prompt, model = title.strip(), prompt.strip(), model.strip()
    if not title or not prompt:
        raise ScheduleValidationError(MSG_TITLE_PROMPT_REQUIRED)
    if len(title) > TITLE_MAX_LENGTH:
        raise ScheduleValidationError(MSG_TITLE_TOO_LONG)
    if len(prompt) > PROMPT_MAX_LENGTH:
        raise ScheduleValidationError(MSG_PROMPT_TOO_LONG)
    if not model:
        raise ScheduleValidationError(MSG_MODEL_REQUIRED)
    if len(model) > MODEL_MAX_LENGTH:
        raise ScheduleValidationError(MSG_MODEL_TOO_LONG)
    return title, prompt, model


async def _count_live_tasks(session: AsyncSession, user_id: int) -> int:
    """Count the user's active and paused jobs."""
    count = (
        await session.exec(
            select(func.count())
            .select_from(ScheduledTask)
            .where(
                ScheduledTask.user_id == user_id,
                ScheduledTask.status.in_(_LIVE_STATUSES),
            )
        )
    ).one()
    return int(count)


async def publish_task_updated(session: AsyncSession, task: ScheduledTask) -> ScheduledTaskOut:
    """Build the API view of a job and push it to the owner's event sockets."""
    task_out = await build_task_out(session, task)
    hub.publish(task.user_id, task_updated_frame(task_out))
    return task_out


async def create_scheduled_task(
    session: AsyncSession,
    user_id: int,
    *,
    title: str,
    prompt: str,
    model: str,
    schedule_type: str,
    delay_seconds: int | None = None,
    run_at: str | None = None,
    interval_seconds: int | None = None,
    cron: str | None = None,
    max_runs: int | None = None,
    origin_chat_id: int | None = None,
    now: datetime | None = None,
) -> ScheduledTask:
    """Validate and persist a new job; raises ScheduleValidationError or SchedulerConflictError."""
    now = as_aware_utc(now or datetime.now(timezone.utc))
    title, prompt, model = _validate_text_fields(title, prompt, model)
    spec = build_schedule_spec(
        schedule_type,
        delay_seconds=delay_seconds,
        run_at=run_at,
        interval_seconds=interval_seconds,
        cron=cron,
        max_runs=max_runs,
        now=now,
    )
    next_run_at = initial_next_run(spec, now)
    limit = settings.SCHEDULER_MAX_ACTIVE_TASKS_PER_USER
    if await _count_live_tasks(session, user_id) >= limit:
        raise SchedulerConflictError(MSG_TOO_MANY_TASKS.format(n=limit))
    row = ScheduledTask(
        user_id=user_id,
        origin_chat_id=origin_chat_id,
        title=title,
        prompt=prompt,
        model=model,
        schedule_type=spec.schedule_type,
        run_at=spec.run_at,
        interval_seconds=spec.interval_seconds,
        cron_expr=spec.cron_expr,
        max_runs=spec.max_runs,
        next_run_at=next_run_at,
        status=ScheduledTaskStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    await publish_task_updated(session, row)
    logger.info(
        "scheduled_task_created",
        user_id=user_id,
        task_id=row.id,
        schedule_type=spec.schedule_type.value,
    )
    return row


async def get_owned_task(session: AsyncSession, user_id: int, task_id: int) -> ScheduledTask:
    """Return the user's job; another user's job is reported as missing."""
    row = await session.get(ScheduledTask, task_id)
    if row is None or row.user_id != user_id:
        logger.warning("scheduled_task_access_denied", user_id=user_id, task_id=task_id)
        raise SchedulerNotFoundError()
    return row


async def get_owned_run(
    session: AsyncSession, user_id: int, run_id: int
) -> tuple[TaskRun, ScheduledTask]:
    """Return the user's run together with its job; another user's run is reported as missing."""
    run = await session.get(TaskRun, run_id)
    if run is None or run.user_id != user_id:
        logger.warning("task_run_access_denied", user_id=user_id, run_id=run_id)
        raise SchedulerNotFoundError()
    task = await session.get(ScheduledTask, run.scheduled_task_id)
    if task is None or task.user_id != user_id:
        raise SchedulerNotFoundError()
    return run, task


async def list_scheduled_tasks(
    session: AsyncSession,
    user_id: int,
    *,
    statuses: set[ScheduledTaskStatus] | None = None,
) -> list[ScheduledTask]:
    """List the user's jobs: live ones first, then by next fire time, then newest."""
    query = select(ScheduledTask).where(ScheduledTask.user_id == user_id)
    if statuses:
        query = query.where(ScheduledTask.status.in_(statuses))
    rows = list((await session.exec(query)).all())
    # Sorted in Python: SQLite has no NULLS LAST and the rows are few per user.
    rows.sort(key=lambda row: row.created_at.timestamp(), reverse=True)
    rows.sort(
        key=lambda row: (
            row.status not in _LIVE_STATUSES,
            row.next_run_at is None,
            as_aware_utc(row.next_run_at).timestamp() if row.next_run_at else 0.0,
        )
    )
    return rows


async def _guarded_transition(
    session: AsyncSession,
    task_id: int,
    *,
    expected: ScheduledTaskStatus,
    values: dict[str, object],
) -> bool:
    """Apply values only while the job still has the expected status and a pending slot.

    next_run_at is NULL exactly while the job's final run is in flight; such a job must
    stay untouched so it can neither be re-armed nor paused, and finalize_task completes
    it when the run ends.
    """
    try:
        result = await session.exec(
            update(ScheduledTask)
            .where(
                ScheduledTask.id == task_id,
                ScheduledTask.status == expected,
                ScheduledTask.next_run_at.is_not(None),
            )
            .values(**values)
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return result.rowcount == 1


async def pause_task(
    session: AsyncSession, user_id: int, task_id: int, now: datetime | None = None
) -> ScheduledTask:
    """Pause an active job; its next_run_at is kept so it is never completed while paused."""
    task = await get_owned_task(session, user_id, task_id)
    if task.status != ScheduledTaskStatus.ACTIVE:
        raise SchedulerConflictError(MSG_NOT_ACTIVE)
    if task.next_run_at is None:
        raise SchedulerConflictError(MSG_FINAL_RUN_IN_PROGRESS)
    updated_at = as_aware_utc(now or datetime.now(timezone.utc))
    if not await _guarded_transition(
        session,
        task_id,
        expected=ScheduledTaskStatus.ACTIVE,
        values={"status": ScheduledTaskStatus.PAUSED, "updated_at": updated_at},
    ):
        raise SchedulerConflictError(MSG_FINAL_RUN_IN_PROGRESS)
    await session.refresh(task)
    await publish_task_updated(session, task)
    logger.info("scheduled_task_paused", user_id=user_id, task_id=task_id)
    return task


async def resume_task(
    session: AsyncSession, user_id: int, task_id: int, now: datetime | None = None
) -> ScheduledTask:
    """Resume a paused job; periodic jobs restart from now without catching up paused time."""
    task = await get_owned_task(session, user_id, task_id)
    if task.status != ScheduledTaskStatus.PAUSED:
        raise SchedulerConflictError(MSG_NOT_PAUSED)
    if task.next_run_at is None:
        raise SchedulerConflictError(MSG_FINAL_RUN_IN_PROGRESS)
    now = as_aware_utc(now or datetime.now(timezone.utc))
    next_run_at = next_run_on_resume(
        task.schedule_type,
        run_at=task.run_at,
        interval_seconds=task.interval_seconds,
        cron_expr=task.cron_expr,
        now=now,
    )
    if not await _guarded_transition(
        session,
        task_id,
        expected=ScheduledTaskStatus.PAUSED,
        values={
            "status": ScheduledTaskStatus.ACTIVE,
            "next_run_at": next_run_at,
            "updated_at": now,
        },
    ):
        raise SchedulerConflictError(MSG_FINAL_RUN_IN_PROGRESS)
    await session.refresh(task)
    await publish_task_updated(session, task)
    logger.info("scheduled_task_resumed", user_id=user_id, task_id=task_id)
    return task


async def cancel_task(
    session: AsyncSession, user_id: int, task_id: int, now: datetime | None = None
) -> ScheduledTask:
    """Cancel a job softly: runs are kept and an in-flight run still records its result."""
    task = await get_owned_task(session, user_id, task_id)
    if task.status not in _LIVE_STATUSES:
        raise SchedulerConflictError(MSG_ALREADY_FINISHED)
    task.status = ScheduledTaskStatus.CANCELLED
    task.next_run_at = None
    task.updated_at = as_aware_utc(now or datetime.now(timezone.utc))
    session.add(task)
    try:
        await session.commit()
        await session.refresh(task)
    except Exception:
        await session.rollback()
        raise
    await publish_task_updated(session, task)
    logger.info("scheduled_task_cancelled", user_id=user_id, task_id=task_id)
    return task


async def delete_task(session: AsyncSession, user_id: int, task_id: int) -> None:
    """Delete a job and its runs, taking it out of scheduling before aborting in-flight runs."""
    task = await get_owned_task(session, user_id, task_id)
    try:
        # Committed first so the poll loop cannot claim another slot while the runs are aborted.
        await session.exec(
            update(ScheduledTask)
            .where(ScheduledTask.id == task_id)
            .values(
                status=ScheduledTaskStatus.CANCELLED,
                next_run_at=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await scheduler.abort_task_runs(task_id)
    try:
        await session.delete(task)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    # A run claimed just before the job left scheduling may only have been spawned by now.
    await scheduler.abort_task_runs(task_id)
    hub.publish(user_id, task_deleted_frame(task_id))
    logger.info("scheduled_task_deleted", user_id=user_id, task_id=task_id)


async def run_task_now(session: AsyncSession, user_id: int, task_id: int) -> TaskRun:
    """Start an off-schedule run of an active or paused job."""
    task = await get_owned_task(session, user_id, task_id)
    if task.status not in _LIVE_STATUSES:
        raise SchedulerConflictError(MSG_ALREADY_FINISHED)
    try:
        run = await scheduler.start_manual_run(session, task)
    except RunAlreadyActiveError as exc:
        raise SchedulerConflictError(MSG_ALREADY_RUNNING) from exc
    await publish_task_updated(session, task)
    logger.info("scheduled_task_run_now", user_id=user_id, task_id=task_id, run_id=run.id)
    return run


async def list_task_runs(
    session: AsyncSession, user_id: int, task_id: int, limit: int = 20
) -> list[TaskRun]:
    """List a job's runs newest first."""
    await get_owned_task(session, user_id, task_id)
    result = await session.exec(
        select(TaskRun)
        .where(TaskRun.scheduled_task_id == task_id, TaskRun.user_id == user_id)
        .order_by(TaskRun.started_at.desc(), TaskRun.id.desc())
        .limit(limit)
    )
    return list(result.all())


__all__ = [
    "MSG_TOO_MANY_TASKS",
    "SchedulerConflictError",
    "SchedulerNotFoundError",
    "cancel_task",
    "create_scheduled_task",
    "delete_task",
    "get_owned_run",
    "get_owned_task",
    "list_scheduled_tasks",
    "list_task_runs",
    "pause_task",
    "publish_task_updated",
    "resume_task",
    "run_task_now",
]
