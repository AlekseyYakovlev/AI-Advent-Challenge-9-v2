"""Background job scheduler: poll loop, atomic claim, recovery and run execution."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import and_, case, exists, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.events import hub
from agent.headless import HeadlessRunError, run_headless_turn
from agent.schedule import as_aware_utc, next_run_after_claim
from agent.scheduler_schemas import (
    ScheduledTaskOut,
    run_finished_frame,
    run_started_frame,
    task_to_out,
)
from shared.config import settings
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import (
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleType,
    TaskRun,
)

logger = get_logger(__name__)

MSG_SKIPPED_OVERLAP = "Предыдущий запуск ещё выполнялся"
MSG_INTERRUPTED_RESTART = "Прервано: Agent был перезапущен"
MSG_INTERRUPTED_STOP = "Прервано: Agent остановлен"
MSG_ABORTED_DELETE = "Прервано: задание удалено"
MSG_ALREADY_FINISHED = "Задание уже завершено"

TICK_BATCH_LIMIT = 20


def msg_timeout(seconds: float) -> str:
    """Russian failure text for a run that exceeded its overall deadline."""
    return f"Превышено время выполнения ({int(seconds)} с)"


def msg_unexpected(exc: BaseException) -> str:
    """Russian failure text for an unmapped exception (type name only, no details)."""
    return f"Непредвиденная ошибка ({type(exc).__name__})"


class RunAlreadyActiveError(Exception):
    """A run of this job is already in progress."""


class SchedulerConflictError(Exception):
    """The requested change is illegal in the job's current state; `message` is Russian text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


async def build_task_out(session: AsyncSession, task: ScheduledTask) -> ScheduledTaskOut:
    """Load the newest run and running flag of a job and map it to the API shape."""
    last_run = (
        await session.exec(
            select(TaskRun)
            .where(TaskRun.scheduled_task_id == task.id)
            .order_by(TaskRun.id.desc())
            .limit(1)
        )
    ).first()
    running = (
        await session.exec(
            select(TaskRun.id).where(
                TaskRun.scheduled_task_id == task.id,
                TaskRun.status == RunStatus.RUNNING,
            )
        )
    ).first()
    return task_to_out(task, last_run, running is not None)


class SchedulerService:
    """Claims due jobs exactly once per slot and runs them in the background."""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(settings.SCHEDULER_MAX_CONCURRENT_RUNS)
        self._runs: dict[int, asyncio.Task[None]] = {}
        self._run_task_ids: dict[int, int] = {}
        self._cancel_reason: dict[int, str] = {}
        self._loop_task: asyncio.Task[None] | None = None

    async def _has_running_run(self, session: AsyncSession, task_id: int) -> bool:
        """Return True when the job already has a run in progress."""
        found = (
            await session.exec(
                select(TaskRun.id).where(
                    TaskRun.scheduled_task_id == task_id,
                    TaskRun.status == RunStatus.RUNNING,
                )
            )
        ).first()
        return found is not None

    async def claim_slot(
        self,
        session: AsyncSession,
        task: ScheduledTask,
        now: datetime,
    ) -> TaskRun | None:
        """Consume the job's due slot and record its run in one commit.

        The guarded UPDATE (status active and next_run_at unchanged) is the optimistic
        claim: only one caller sees rowcount == 1 for a given slot. The partial unique
        index on running runs backstops a racing manual run; in that case the whole
        transaction (including the UPDATE) is rolled back and the slot stays due, so the
        next tick records it as skipped.
        """
        now = as_aware_utc(now)
        old_next = task.next_run_at
        if old_next is None:
            return None
        # Read before any rollback: rollback expires the instance and would trigger a lazy load.
        task_id = task.id
        overlap = await self._has_running_run(session, task_id)
        counts = not overlap
        new_next = next_run_after_claim(
            task.schedule_type,
            old_next=as_aware_utc(old_next),
            now=now,
            interval_seconds=task.interval_seconds,
            cron_expr=task.cron_expr,
        )
        if counts and task.max_runs is not None and task.run_count + 1 >= task.max_runs:
            new_next = None
        try:
            result = await session.exec(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task.id,
                    ScheduledTask.status == ScheduledTaskStatus.ACTIVE,
                    ScheduledTask.next_run_at == old_next,
                )
                .values(
                    next_run_at=new_next,
                    run_count=ScheduledTask.run_count + (1 if counts else 0),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            lag = (now - as_aware_utc(old_next)).total_seconds()
            run = TaskRun(
                scheduled_task_id=task.id,
                user_id=task.user_id,
                trigger=RunTrigger.SCHEDULE,
                status=RunStatus.SKIPPED if overlap else RunStatus.RUNNING,
                scheduled_for=old_next,
                started_at=now,
                finished_at=now if overlap else None,
                is_late=lag > settings.SCHEDULER_LATE_THRESHOLD_SECONDS,
                error=MSG_SKIPPED_OVERLAP if overlap else None,
                model=task.model,
            )
            session.add(run)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.warning("scheduler_claim_conflict", task_id=task_id)
            return None
        except Exception:
            await session.rollback()
            raise
        return run

    async def tick(self, now: datetime | None = None, *, spawn: bool = True) -> list[int]:
        """Claim every due job once and start their runs; returns ids of RUNNING runs."""
        now = as_aware_utc(now or datetime.now(timezone.utc))
        async with async_session_factory() as session:
            due_ids = list(
                (
                    await session.exec(
                        select(ScheduledTask.id)
                        .where(
                            ScheduledTask.status == ScheduledTaskStatus.ACTIVE,
                            ScheduledTask.next_run_at.is_not(None),
                            ScheduledTask.next_run_at <= now,
                        )
                        .order_by(ScheduledTask.next_run_at)
                        .limit(TICK_BATCH_LIMIT)
                    )
                ).all()
            )
        started: list[int] = []
        for task_id in due_ids:
            run_id = await self._claim_and_announce(task_id, now, spawn)
            if run_id is not None:
                started.append(run_id)
        return started

    async def _claim_and_announce(self, task_id: int, now: datetime, spawn: bool) -> int | None:
        """Claim one due job in a fresh session, publish its event and optionally spawn it."""
        async with async_session_factory() as session:
            task = await session.get(ScheduledTask, task_id)
            if task is None:
                return None
            run = await self.claim_slot(session, task, now)
            if run is None:
                return None
            logger.info(
                "scheduler_run_claimed",
                task_id=task.id,
                run_id=run.id,
                user_id=task.user_id,
                status=run.status.value,
                is_late=run.is_late,
            )
            if run.status == RunStatus.SKIPPED:
                await self.finalize_task(session, task.id)
                await session.refresh(task)
                hub.publish(
                    task.user_id,
                    run_finished_frame(run, await build_task_out(session, task)),
                )
                return None
            await session.refresh(task)
            hub.publish(
                task.user_id,
                run_started_frame(run, await build_task_out(session, task)),
            )
        if spawn:
            self._run_task_ids[run.id] = task_id
            self.spawn_run(run.id)
        return run.id

    async def finalize_task(self, session: AsyncSession, task_id: int) -> bool:
        """Mark a job completed when it has no next slot and no run in progress."""
        now = datetime.now(timezone.utc)
        running = exists().where(
            TaskRun.scheduled_task_id == task_id,
            TaskRun.status == RunStatus.RUNNING,
        )
        try:
            result = await session.exec(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task_id,
                    ScheduledTask.status.in_(
                        [ScheduledTaskStatus.ACTIVE, ScheduledTaskStatus.PAUSED]
                    ),
                    ScheduledTask.next_run_at.is_(None),
                    ~running,
                )
                .values(status=ScheduledTaskStatus.COMPLETED, updated_at=now)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return result.rowcount == 1

    async def recover_orphaned_runs(self, now: datetime | None = None) -> int:
        """Fail runs left RUNNING by a dead process and finalize exhausted jobs."""
        now = as_aware_utc(now or datetime.now(timezone.utc))
        async with async_session_factory() as session:
            try:
                result = await session.exec(
                    update(TaskRun)
                    .where(TaskRun.status == RunStatus.RUNNING)
                    .values(
                        status=RunStatus.FAILED,
                        finished_at=now,
                        error=MSG_INTERRUPTED_RESTART,
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            recovered = int(result.rowcount)
            exhausted = list(
                (
                    await session.exec(
                        select(ScheduledTask.id).where(
                            ScheduledTask.status.in_(
                                [ScheduledTaskStatus.ACTIVE, ScheduledTaskStatus.PAUSED]
                            ),
                            ScheduledTask.next_run_at.is_(None),
                        )
                    )
                ).all()
            )
            for task_id in exhausted:
                await self.finalize_task(session, task_id)
        logger.info("scheduler_recovered_orphans", count=recovered)
        return recovered

    def spawn_run(self, run_id: int) -> None:
        """Start execute_run in the background, keeping a strong reference until it ends."""
        task = asyncio.create_task(self.execute_run(run_id), name=f"scheduler-run-{run_id}")
        self._runs[run_id] = task
        task.add_done_callback(lambda _done: self._forget_run(run_id))

    def _forget_run(self, run_id: int) -> None:
        """Drop bookkeeping for a run whose asyncio task has ended."""
        self._runs.pop(run_id, None)
        self._run_task_ids.pop(run_id, None)
        self._cancel_reason.pop(run_id, None)

    async def execute_run(self, run_id: int) -> None:
        """Run the headless turn for a claimed run and persist its outcome."""
        async with async_session_factory() as session:
            run = await session.get(TaskRun, run_id)
            task = await session.get(ScheduledTask, run.scheduled_task_id) if run else None
            if run is None or task is None:
                return
            task_id, user_id, prompt, model = task.id, task.user_id, task.prompt, task.model
        self._run_task_ids[run_id] = task_id
        try:
            # The semaphore is taken outside the deadline so queueing does not eat the budget.
            async with self._semaphore:
                async with asyncio.timeout(settings.SCHEDULER_RUN_TIMEOUT):
                    async with async_session_factory() as run_session:
                        result = await run_headless_turn(run_session, user_id, prompt, model)
        except HeadlessRunError as exc:
            await self._finish_run(run_id, RunStatus.FAILED, error=exc.message)
        except TimeoutError:
            await self._finish_run(
                run_id, RunStatus.FAILED, error=msg_timeout(settings.SCHEDULER_RUN_TIMEOUT)
            )
        except asyncio.CancelledError:
            await self._finish_cancelled(run_id)
            raise
        except Exception as exc:
            logger.error("scheduler_run_failed", run_id=run_id, error=type(exc).__name__)
            await self._finish_run(run_id, RunStatus.FAILED, error=msg_unexpected(exc))
        else:
            if result.mcp_tool_count == 0:
                logger.info("scheduler_run_no_mcp_tools", run_id=run_id)
            await self._finish_run(
                run_id,
                RunStatus.SUCCESS,
                result_text=result.text,
                tool_trace=result.tool_trace,
            )

    async def _finish_cancelled(self, run_id: int) -> None:
        """Best-effort failure record for a run whose task was cancelled."""
        reason = self._cancel_reason.get(run_id, MSG_INTERRUPTED_STOP)
        try:
            await self._finish_run(run_id, RunStatus.FAILED, error=reason)
        except Exception as exc:
            logger.error("scheduler_cancel_record_failed", run_id=run_id, error=type(exc).__name__)

    async def _finish_run(
        self,
        run_id: int,
        status: RunStatus,
        *,
        result_text: str | None = None,
        error: str | None = None,
        tool_trace: str | None = None,
    ) -> None:
        """Persist a run outcome, finalize its job and tell the owner."""
        async with async_session_factory() as session:
            run = await session.get(TaskRun, run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return
            run.status = status
            run.finished_at = datetime.now(timezone.utc)
            run.result_text = result_text
            run.error = error
            run.tool_trace = tool_trace
            session.add(run)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            task = await session.get(ScheduledTask, run.scheduled_task_id)
            if task is None:
                return
            await self.finalize_task(session, task.id)
            await session.refresh(task)
            task_out = await build_task_out(session, task)
            hub.publish(task.user_id, run_finished_frame(run, task_out))
            logger.info(
                "scheduler_run_finished",
                run_id=run_id,
                task_id=task.id,
                status=status.value,
                duration_ms=task_out.last_run.duration_ms if task_out.last_run else None,
            )

    async def start_manual_run(
        self,
        session: AsyncSession,
        task: ScheduledTask,
        now: datetime | None = None,
    ) -> TaskRun:
        """Start an off-schedule run that counts toward run_count and max_runs.

        A periodic job keeps its next_run_at; a one-shot (or exhausted) job clears it so
        the job completes once this run finishes.
        """
        now = as_aware_utc(now or datetime.now(timezone.utc))
        task_id = task.id
        # Evaluated by the database against the row being updated, not the caller's ORM copy.
        exhausts = or_(
            ScheduledTask.schedule_type == ScheduleType.ONCE,
            and_(
                ScheduledTask.max_runs.is_not(None),
                ScheduledTask.run_count + 1 >= ScheduledTask.max_runs,
            ),
        )
        run = TaskRun(
            scheduled_task_id=task_id,
            user_id=task.user_id,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.RUNNING,
            scheduled_for=None,
            started_at=now,
            is_late=False,
            model=task.model,
        )
        try:
            result = await session.exec(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task_id,
                    ScheduledTask.status.in_(
                        [ScheduledTaskStatus.ACTIVE, ScheduledTaskStatus.PAUSED]
                    ),
                )
                .values(
                    run_count=ScheduledTask.run_count + 1,
                    next_run_at=case((exhausts, None), else_=ScheduledTask.next_run_at),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise SchedulerConflictError(MSG_ALREADY_FINISHED)
            session.add(run)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise RunAlreadyActiveError() from exc
        except SchedulerConflictError:
            raise
        except Exception:
            await session.rollback()
            raise
        await session.refresh(task)
        hub.publish(
            task.user_id,
            run_started_frame(run, await build_task_out(session, task)),
        )
        self._run_task_ids[run.id] = task_id
        self.spawn_run(run.id)
        return run

    async def abort_task_runs(self, task_id: int) -> None:
        """Cancel and await every in-flight run of a job (used before deleting it)."""
        pending: list[asyncio.Task[None]] = []
        for run_id, owner_task_id in list(self._run_task_ids.items()):
            running = self._runs.get(run_id)
            if owner_task_id != task_id or running is None:
                continue
            self._cancel_reason[run_id] = MSG_ABORTED_DELETE
            running.cancel()
            pending.append(running)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    @property
    def is_running_loop(self) -> bool:
        """Whether the poll loop task is alive."""
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        """Start the poll loop (no-op when it is already running)."""
        if self.is_running_loop:
            return
        # Rebuilt here so the semaphore belongs to the loop the service actually runs on.
        self._semaphore = asyncio.Semaphore(settings.SCHEDULER_MAX_CONCURRENT_RUNS)
        self._loop_task = asyncio.create_task(self._run_loop(), name="scheduler-loop")

    async def _run_loop(self) -> None:
        """Tick forever; a failing tick is logged and never stops the loop."""
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("scheduler_tick_failed", error=type(exc).__name__)
            await asyncio.sleep(settings.SCHEDULER_POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop the loop, then cancel in-flight runs (their handlers record the interruption)."""
        loop_task, self._loop_task = self._loop_task, None
        if loop_task is not None:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        in_flight = list(self._runs.values())
        for running in in_flight:
            running.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)


scheduler = SchedulerService()
