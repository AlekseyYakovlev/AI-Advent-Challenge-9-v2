"""Scheduler engine tests: atomic claim, catch-up, overlap, max_runs, recovery and execution."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlmodel import select

from agent.events import EventHub
from agent.schedule import as_aware_utc
from agent.headless import HeadlessResult, HeadlessRunError
from agent.scheduler import (
    MSG_ABORTED_DELETE,
    MSG_ALREADY_FINISHED,
    MSG_INTERRUPTED_RESTART,
    MSG_SKIPPED_OVERLAP,
    RunAlreadyActiveError,
    SchedulerService,
    build_task_out,
    msg_timeout,
)
from agent.scheduler_ops import (
    MSG_FINAL_RUN_IN_PROGRESS,
    SchedulerConflictError,
    delete_task,
    pause_task,
    resume_task,
)
from agent.scheduler_schemas import run_to_summary, task_to_out
from shared.database import async_session_factory
from shared.models import (
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleType,
    TaskRun,
)

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fresh_hub(monkeypatch: pytest.MonkeyPatch) -> EventHub:
    """Replace the scheduler's event hub with an empty one."""
    hub = EventHub()
    monkeypatch.setattr("agent.scheduler.hub", hub)
    return hub


async def _add_task(user_id: int, **overrides: Any) -> int:
    """Insert a scheduled job and return its id."""
    fields: dict[str, Any] = {
        "user_id": user_id,
        "title": "job",
        "prompt": "do it",
        "model": "m",
        "schedule_type": ScheduleType.INTERVAL,
        "interval_seconds": 60,
        "next_run_at": NOW - timedelta(seconds=10),
        "status": ScheduledTaskStatus.ACTIVE,
    }
    fields.update(overrides)
    async with async_session_factory() as session:
        task = ScheduledTask(**fields)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def _add_run(task_id: int, user_id: int, status: RunStatus, **overrides: Any) -> int:
    """Insert a run row directly and return its id."""
    fields: dict[str, Any] = {
        "scheduled_task_id": task_id,
        "user_id": user_id,
        "status": status,
        "trigger": RunTrigger.MANUAL,
        "started_at": NOW,
        "model": "m",
    }
    fields.update(overrides)
    async with async_session_factory() as session:
        run = TaskRun(**fields)
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run.id


async def _task(task_id: int) -> ScheduledTask:
    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        return task


async def _runs(task_id: int) -> list[TaskRun]:
    async with async_session_factory() as session:
        rows = await session.exec(
            select(TaskRun).where(TaskRun.scheduled_task_id == task_id).order_by(TaskRun.id)
        )
        return list(rows.all())


async def test_once_job_due_creates_single_running_run(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    slot = NOW - timedelta(seconds=5)
    task_id = await _add_task(user.id, schedule_type=ScheduleType.ONCE, next_run_at=slot)
    svc = SchedulerService()

    started = await svc.tick(NOW, spawn=False)

    runs = await _runs(task_id)
    assert started == [runs[0].id]
    assert len(runs) == 1
    assert runs[0].status == RunStatus.RUNNING
    assert runs[0].trigger == RunTrigger.SCHEDULE
    assert as_aware_utc(runs[0].scheduled_for) == slot
    assert runs[0].is_late is False
    task = await _task(task_id)
    assert task.run_count == 1
    assert task.next_run_at is None
    assert await svc.tick(NOW, spawn=False) == []
    assert len(await _runs(task_id)) == 1


async def test_not_yet_due_job_is_not_claimed(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, next_run_at=NOW + timedelta(seconds=30))

    assert await SchedulerService().tick(NOW, spawn=False) == []
    assert await _runs(task_id) == []


async def test_interval_job_advances_by_one_slot(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    old = NOW - timedelta(seconds=10)
    task_id = await _add_task(user.id, next_run_at=old)

    await SchedulerService().tick(NOW, spawn=False)

    runs = await _runs(task_id)
    assert len(runs) == 1
    assert runs[0].is_late is False
    task = await _task(task_id)
    assert as_aware_utc(task.next_run_at) == old + timedelta(seconds=60)
    assert task.run_count == 1
    assert task.status == ScheduledTaskStatus.ACTIVE


async def test_interval_catch_up_after_downtime_runs_once_flagged_late(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, next_run_at=NOW - timedelta(hours=2))
    svc = SchedulerService()

    await svc.tick(NOW, spawn=False)
    await svc.tick(NOW, spawn=False)

    runs = await _runs(task_id)
    assert len(runs) == 1
    assert runs[0].is_late is True
    next_run = as_aware_utc((await _task(task_id)).next_run_at)
    assert NOW < next_run <= NOW + timedelta(seconds=60)


async def test_cron_catch_up_runs_once_and_lands_on_next_boundary(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(
        user.id,
        schedule_type=ScheduleType.CRON,
        interval_seconds=None,
        cron_expr="*/5 * * * *",
        next_run_at=NOW - timedelta(days=1),
    )

    await SchedulerService().tick(NOW, spawn=False)

    runs = await _runs(task_id)
    assert len(runs) == 1
    assert runs[0].is_late is True
    next_run = as_aware_utc((await _task(task_id)).next_run_at)
    assert NOW < next_run <= NOW + timedelta(minutes=5)
    assert next_run.astimezone().minute % 5 == 0


async def test_concurrent_claim_of_same_slot_yields_one_run(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()

    async with async_session_factory() as s1, async_session_factory() as s2:
        t1 = await s1.get(ScheduledTask, task_id)
        t2 = await s2.get(ScheduledTask, task_id)
        results = await asyncio.gather(
            svc.claim_slot(s1, t1, NOW),
            svc.claim_slot(s2, t2, NOW),
            return_exceptions=True,
        )

    claimed = [r for r in results if isinstance(r, TaskRun)]
    assert len(claimed) == 1
    assert len(await _runs(task_id)) == 1
    assert (await _task(task_id)).run_count == 1


async def test_stale_claim_of_consumed_slot_returns_none(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()

    async with async_session_factory() as s1, async_session_factory() as s2:
        t1 = await s1.get(ScheduledTask, task_id)
        t2 = await s2.get(ScheduledTask, task_id)
        first = await svc.claim_slot(s1, t1, NOW)
        second = await svc.claim_slot(s2, t2, NOW)

    assert first is not None
    assert second is None
    assert len(await _runs(task_id)) == 1


async def test_overlap_records_skipped_run_without_counting(
    seed_user: Any, fresh_hub: EventHub
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, run_count=1)
    await _add_run(task_id, user.id, RunStatus.RUNNING)
    queue = fresh_hub.subscribe(user.id)
    old = NOW - timedelta(seconds=10)

    assert await SchedulerService().tick(NOW, spawn=False) == []

    runs = await _runs(task_id)
    skipped = [r for r in runs if r.status == RunStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].error == MSG_SKIPPED_OVERLAP
    assert skipped[0].finished_at is not None
    assert skipped[0].trigger == RunTrigger.SCHEDULE
    task = await _task(task_id)
    assert task.run_count == 1
    assert as_aware_utc(task.next_run_at) == old + timedelta(seconds=60)
    frame = queue.get_nowait()
    assert frame["type"] == "run_finished"
    assert frame["run"]["status"] == "skipped"
    assert queue.empty()


@pytest.mark.parametrize("status", [ScheduledTaskStatus.PAUSED, ScheduledTaskStatus.CANCELLED])
async def test_inactive_jobs_are_never_claimed(seed_user: Any, status: ScheduledTaskStatus) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, status=status)
    svc = SchedulerService()

    assert await svc.tick(NOW, spawn=False) == []
    async with async_session_factory() as session:
        stale = await session.get(ScheduledTask, task_id)
        assert await svc.claim_slot(session, stale, NOW) is None
    assert await _runs(task_id) == []


async def test_max_runs_exhausts_job_and_skips_do_not_count(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, max_runs=2)
    svc = SchedulerService()

    await svc.tick(NOW, spawn=False)
    first = await _task(task_id)
    assert first.run_count == 1
    assert first.next_run_at is not None

    later = NOW + timedelta(seconds=120)
    await svc.tick(later, spawn=False)
    assert (await _runs(task_id))[-1].status == RunStatus.SKIPPED
    still = await _task(task_id)
    assert still.run_count == 1
    assert still.next_run_at is not None

    runs = await _runs(task_id)
    async with async_session_factory() as session:
        running = await session.get(TaskRun, runs[0].id)
        running.status = RunStatus.SUCCESS
        running.finished_at = later
        session.add(running)
        await session.commit()
    await svc.tick(later + timedelta(seconds=120), spawn=False)
    exhausted = await _task(task_id)
    assert exhausted.run_count == 2
    assert exhausted.next_run_at is None
    assert exhausted.status == ScheduledTaskStatus.ACTIVE

    running_id = (await _runs(task_id))[-1].id
    async with async_session_factory() as session:
        assert await svc.finalize_task(session, task_id) is False
        row = await session.get(TaskRun, running_id)
        row.status = RunStatus.SUCCESS
        row.finished_at = later
        session.add(row)
        await session.commit()
        assert await svc.finalize_task(session, task_id) is True
    assert (await _task(task_id)).status == ScheduledTaskStatus.COMPLETED


async def test_finalize_ignores_paused_job_with_pending_slot(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    paused_id = await _add_task(
        user.id, status=ScheduledTaskStatus.PAUSED, next_run_at=NOW + timedelta(hours=1)
    )
    svc = SchedulerService()

    async with async_session_factory() as session:
        assert await svc.finalize_task(session, paused_id) is False
    assert (await _task(paused_id)).status == ScheduledTaskStatus.PAUSED


async def test_recover_orphaned_runs_fails_running_and_finalizes_once_job(
    seed_user: Any,
) -> None:
    user = await seed_user("u1", "pw")
    once_id = await _add_task(user.id, schedule_type=ScheduleType.ONCE)
    svc = SchedulerService()
    await svc.tick(NOW, spawn=False)
    periodic_id = await _add_task(user.id, next_run_at=NOW + timedelta(hours=1))
    other_run = await _add_run(periodic_id, user.id, RunStatus.RUNNING)

    recovered = await SchedulerService().recover_orphaned_runs(NOW + timedelta(minutes=1))

    assert recovered == 2
    once_run = (await _runs(once_id))[0]
    assert once_run.status == RunStatus.FAILED
    assert once_run.error == MSG_INTERRUPTED_RESTART
    assert once_run.finished_at is not None
    async with async_session_factory() as session:
        other = await session.get(TaskRun, other_run)
        assert other.status == RunStatus.FAILED
    assert (await _task(once_id)).status == ScheduledTaskStatus.COMPLETED
    assert (await _task(periodic_id)).status == ScheduledTaskStatus.ACTIVE
    assert await SchedulerService().recover_orphaned_runs() == 0


async def test_serialized_datetimes_are_utc_aware(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    await SchedulerService().tick(NOW, spawn=False)

    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        out = await build_task_out(session, task)
    payload = out.model_dump(mode="json")

    for value in (
        payload["next_run_at"],
        payload["created_at"],
        payload["updated_at"],
        payload["last_run"]["started_at"],
        payload["last_run"]["scheduled_for"],
    ):
        assert value.endswith("Z") or value.endswith("+00:00")
    assert payload["is_running"] is True
    assert payload["cron"] is None
    assert '"started_at":"' in out.last_run.model_dump_json().replace(" ", "")


async def test_run_started_is_published_to_owner_only_with_fresh_snapshot(
    seed_user: Any, fresh_hub: EventHub
) -> None:
    owner = await seed_user("owner", "pw")
    other = await seed_user("other", "pw")
    owner_queue = fresh_hub.subscribe(owner.id)
    other_queue = fresh_hub.subscribe(other.id)
    old = NOW - timedelta(seconds=10)
    task_id = await _add_task(owner.id, next_run_at=old)

    await SchedulerService().tick(NOW, spawn=False)

    frame = owner_queue.get_nowait()
    assert frame["type"] == "run_started"
    assert frame["task_id"] == task_id
    assert frame["run"]["status"] == "running"
    assert "result_text" not in frame["run"]
    task_snapshot = frame["task"]
    assert task_snapshot["is_running"] is True
    assert task_snapshot["run_count"] == 1
    assert as_aware_utc(datetime.fromisoformat(task_snapshot["next_run_at"])) == old + timedelta(
        seconds=60
    )
    assert other_queue.empty()


async def test_racing_manual_run_rolls_back_claim_and_next_tick_skips(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    old = NOW - timedelta(seconds=10)
    task_id = await _add_task(user.id, next_run_at=old)
    svc = SchedulerService()

    async with async_session_factory() as session:
        stale = await session.get(ScheduledTask, task_id)
        await _add_run(task_id, user.id, RunStatus.RUNNING)

        async def _no_overlap_seen(_session: Any, _task_id: int) -> bool:
            return False

        with monkeypatch.context() as patch:
            patch.setattr(svc, "_has_running_run", _no_overlap_seen)
            assert await svc.claim_slot(session, stale, NOW) is None

    task = await _task(task_id)
    assert as_aware_utc(task.next_run_at) == old
    assert task.run_count == 0

    assert await svc.tick(NOW, spawn=False) == []
    runs = await _runs(task_id)
    skipped = [r for r in runs if r.status == RunStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].error == MSG_SKIPPED_OVERLAP
    assert (await _task(task_id)).run_count == 0


async def test_mapper_duration_and_running_summary(seed_user: Any) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    run_id = await _add_run(
        task_id,
        user.id,
        RunStatus.SUCCESS,
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=1500),
    )
    running_id = await _add_run(task_id, user.id, RunStatus.RUNNING)
    async with async_session_factory() as session:
        done = run_to_summary(await session.get(TaskRun, run_id))
        live = run_to_summary(await session.get(TaskRun, running_id))
        task = await session.get(ScheduledTask, task_id)
        out = task_to_out(task, None, False)
    assert done.duration_ms == 1500
    assert live.duration_ms is None
    assert out.last_run is None


def _fake_turn(text: str = "ok") -> Any:
    """Build a run_headless_turn stand-in returning a fixed successful result."""

    async def _run(_session: Any, _user_id: int, _prompt: str, _model: str) -> HeadlessResult:
        return HeadlessResult(text=text, results=[], tool_trace=None, mcp_tool_count=0)

    return _run


def _raising_turn(exc: BaseException) -> Any:
    """Build a run_headless_turn stand-in that raises exc."""

    async def _run(_session: Any, _user_id: int, _prompt: str, _model: str) -> HeadlessResult:
        raise exc

    return _run


async def _claim_running_run(svc: SchedulerService) -> int:
    """Tick once (no spawn) and return the id of the RUNNING run it created."""
    started = await svc.tick(NOW, spawn=False)
    assert len(started) == 1
    return started[0]


async def test_execute_run_success_persists_result_and_publishes_finished(
    seed_user: Any, fresh_hub: EventHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()
    run_id = await _claim_running_run(svc)
    queue = fresh_hub.subscribe(user.id)
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _fake_turn("ok"))

    await svc.execute_run(run_id)

    run = (await _runs(task_id))[0]
    assert run.status == RunStatus.SUCCESS
    assert run.result_text == "ok"
    assert run.finished_at is not None
    frame = queue.get_nowait()
    assert frame["type"] == "run_finished"
    assert frame["run"]["status"] == "success"
    assert frame["task"]["is_running"] is False
    assert frame["task"]["run_count"] == 1
    assert queue.empty()


async def test_execute_run_maps_headless_error_and_next_slot_still_fires(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()
    run_id = await _claim_running_run(svc)
    message = "Модель недоступна: LM Studio не запущен"
    monkeypatch.setattr(
        "agent.scheduler.run_headless_turn", _raising_turn(HeadlessRunError(message))
    )

    await svc.execute_run(run_id)

    run = (await _runs(task_id))[0]
    assert run.status == RunStatus.FAILED
    assert run.error == message
    assert (await _task(task_id)).status == ScheduledTaskStatus.ACTIVE
    assert len(await svc.tick(NOW + timedelta(seconds=120), spawn=False)) == 1
    assert len(await _runs(task_id)) == 2


async def test_execute_run_times_out_with_russian_message(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()
    run_id = await _claim_running_run(svc)

    async def _slow(*_args: Any) -> HeadlessResult:
        await asyncio.sleep(1)
        raise AssertionError("deadline should have fired")

    monkeypatch.setattr("agent.scheduler.run_headless_turn", _slow)
    monkeypatch.setattr("agent.scheduler.settings.SCHEDULER_RUN_TIMEOUT", 0.05)

    await svc.execute_run(run_id)

    run = (await _runs(task_id))[0]
    assert run.status == RunStatus.FAILED
    assert run.error == msg_timeout(0.05)


def test_default_timeout_message_reads_120_seconds() -> None:
    assert msg_timeout(120.0) == "Превышено время выполнения (120 с)"


async def test_execute_run_maps_unexpected_exception(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()
    run_id = await _claim_running_run(svc)
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _raising_turn(RuntimeError("secret")))

    await svc.execute_run(run_id)

    run = (await _runs(task_id))[0]
    assert run.status == RunStatus.FAILED
    assert run.error == "Непредвиденная ошибка (RuntimeError)"


async def test_execute_run_for_missing_run_returns_quietly() -> None:
    await SchedulerService().execute_run(987654)


async def test_once_job_completes_after_its_run(
    seed_user: Any, fresh_hub: EventHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, schedule_type=ScheduleType.ONCE)
    svc = SchedulerService()
    run_id = await _claim_running_run(svc)
    queue = fresh_hub.subscribe(user.id)
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _fake_turn())

    await svc.execute_run(run_id)

    assert (await _task(task_id)).status == ScheduledTaskStatus.COMPLETED
    frame = queue.get_nowait()
    assert frame["task"]["status"] == "completed"


async def test_manual_run_on_interval_job_keeps_next_slot(
    seed_user: Any, fresh_hub: EventHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    slot = NOW + timedelta(minutes=30)
    task_id = await _add_task(user.id, next_run_at=slot, run_count=3)
    queue = fresh_hub.subscribe(user.id)
    svc = SchedulerService()
    spawned: list[int] = []
    monkeypatch.setattr(svc, "spawn_run", spawned.append)

    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        run = await svc.start_manual_run(session, task, NOW)

        assert run.trigger == RunTrigger.MANUAL
        assert run.status == RunStatus.RUNNING
        assert run.scheduled_for is None
        assert spawned == [run.id]
        with pytest.raises(RunAlreadyActiveError):
            await svc.start_manual_run(session, task, NOW)

    stored = await _task(task_id)
    assert stored.run_count == 4
    assert as_aware_utc(stored.next_run_at) == slot
    frame = queue.get_nowait()
    assert frame["type"] == "run_started"
    assert frame["task"]["run_count"] == 4
    assert frame["task"]["is_running"] is True
    assert len(await _runs(task_id)) == 1


async def test_manual_run_on_once_job_clears_slot_and_completes_after_run(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(
        user.id, schedule_type=ScheduleType.ONCE, next_run_at=NOW + timedelta(hours=1)
    )
    svc = SchedulerService()
    monkeypatch.setattr(svc, "spawn_run", lambda _run_id: None)
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _fake_turn())

    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        run = await svc.start_manual_run(session, task, NOW)
    assert (await _task(task_id)).next_run_at is None

    await svc.execute_run(run.id)
    assert (await _task(task_id)).status == ScheduledTaskStatus.COMPLETED


async def test_abort_task_runs_cancels_in_flight_run(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()

    async def _forever(*_args: Any) -> HeadlessResult:
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr("agent.scheduler.run_headless_turn", _forever)
    started = await svc.tick(NOW)
    await asyncio.sleep(0.1)
    assert svc._runs

    await svc.abort_task_runs(task_id)

    run = (await _runs(task_id))[0]
    assert run.id == started[0]
    assert run.status == RunStatus.FAILED
    assert run.error == MSG_ABORTED_DELETE
    assert not svc._runs


async def test_loop_runs_due_job_and_stop_ends_it(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(
        user.id,
        interval_seconds=3600,
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    monkeypatch.setattr("agent.scheduler.settings.SCHEDULER_POLL_INTERVAL", 0.01)
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _fake_turn("loop"))
    svc = SchedulerService()

    await svc.start()
    assert svc.is_running_loop
    # Wait for the run task itself to end, not just for the SUCCESS row: _finish_run keeps
    # querying after that commit, and cancelling it there orphans a DB connection.
    for _ in range(200):
        runs = await _runs(task_id)
        if runs and runs[0].status == RunStatus.SUCCESS and not svc._runs:
            break
        await asyncio.sleep(0.02)
    await svc.stop()

    assert not svc.is_running_loop
    runs = await _runs(task_id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCESS
    assert runs[0].result_text == "loop"


# --------------------------------------------------------------------------- final run in flight


async def _assert_final_run_cannot_be_rearmed(
    user_id: int, task_id: int, svc: SchedulerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pause/resume are refused while the final run is in flight; the job then completes."""
    run_id = await _claim_running_run(svc)
    assert (await _task(task_id)).next_run_at is None

    async with async_session_factory() as session:
        with pytest.raises(SchedulerConflictError) as info:
            await pause_task(session, user_id, task_id)
    assert info.value.message == MSG_FINAL_RUN_IN_PROGRESS
    assert (await _task(task_id)).status == ScheduledTaskStatus.ACTIVE

    # A job that was paused by a racing request must not be re-armed by resume either.
    async with async_session_factory() as session:
        stored = await session.get(ScheduledTask, task_id)
        stored.status = ScheduledTaskStatus.PAUSED
        session.add(stored)
        await session.commit()
    async with async_session_factory() as session:
        with pytest.raises(SchedulerConflictError) as info:
            await resume_task(session, user_id, task_id, now=NOW)
    assert info.value.message == MSG_FINAL_RUN_IN_PROGRESS
    stored = await _task(task_id)
    assert stored.status == ScheduledTaskStatus.PAUSED
    assert stored.next_run_at is None
    async with async_session_factory() as session:
        stored = await session.get(ScheduledTask, task_id)
        stored.status = ScheduledTaskStatus.ACTIVE
        session.add(stored)
        await session.commit()

    assert await svc.tick(NOW + timedelta(hours=1), spawn=False) == []
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _fake_turn())
    await svc.execute_run(run_id)

    stored = await _task(task_id)
    assert stored.status == ScheduledTaskStatus.COMPLETED
    assert stored.run_count == 1
    assert await svc.tick(NOW + timedelta(hours=2), spawn=False) == []
    assert len(await _runs(task_id)) == 1


async def test_once_job_final_run_cannot_be_rearmed_by_pause_resume(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, schedule_type=ScheduleType.ONCE)
    await _assert_final_run_cannot_be_rearmed(user.id, task_id, SchedulerService(), monkeypatch)


async def test_max_runs_final_run_cannot_be_rearmed_by_pause_resume(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, max_runs=1)
    await _assert_final_run_cannot_be_rearmed(user.id, task_id, SchedulerService(), monkeypatch)


async def test_start_manual_run_refuses_job_cancelled_after_status_check(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, next_run_at=NOW + timedelta(hours=1))
    svc = SchedulerService()
    spawned: list[int] = []
    monkeypatch.setattr(svc, "spawn_run", spawned.append)

    async with async_session_factory() as session:
        stale = await session.get(ScheduledTask, task_id)
        assert stale is not None and stale.status == ScheduledTaskStatus.ACTIVE
        async with async_session_factory() as other:
            fresh = await other.get(ScheduledTask, task_id)
            fresh.status = ScheduledTaskStatus.CANCELLED
            fresh.next_run_at = None
            other.add(fresh)
            await other.commit()

        with pytest.raises(SchedulerConflictError) as info:
            await svc.start_manual_run(session, stale, NOW)

    assert info.value.message == MSG_ALREADY_FINISHED
    assert spawned == []
    assert await _runs(task_id) == []
    stored = await _task(task_id)
    assert stored.run_count == 0
    assert stored.status == ScheduledTaskStatus.CANCELLED


async def test_start_manual_run_exhaustion_uses_stored_run_count(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, max_runs=2, run_count=0, next_run_at=NOW + timedelta(hours=1))
    svc = SchedulerService()
    monkeypatch.setattr(svc, "spawn_run", lambda _run_id: None)

    async with async_session_factory() as session:
        stale = await session.get(ScheduledTask, task_id)
        async with async_session_factory() as other:
            bumped = await other.get(ScheduledTask, task_id)
            bumped.run_count = 1
            other.add(bumped)
            await other.commit()
        await svc.start_manual_run(session, stale, NOW)

    stored = await _task(task_id)
    assert stored.run_count == 2
    assert stored.next_run_at is None


# --------------------------------------------------------------------------- announce failures


async def _drain_runs(svc: SchedulerService) -> None:
    """Wait until every spawned run task has ended (never stop the service mid-run)."""
    for _ in range(500):
        if not svc._runs:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("runs did not drain")


async def test_failed_announce_still_runs_the_claimed_run_and_next_slot_is_claimable(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()
    monkeypatch.setattr("agent.scheduler.run_headless_turn", _fake_turn("done"))
    real_build = build_task_out
    calls: list[int] = []

    async def _flaky_build(session: Any, task: ScheduledTask) -> Any:
        calls.append(task.id)
        if len(calls) == 1:
            raise RuntimeError("announce broke")
        return await real_build(session, task)

    monkeypatch.setattr("agent.scheduler.build_task_out", _flaky_build)

    started = await svc.tick(NOW)
    await _drain_runs(svc)

    runs = await _runs(task_id)
    assert started == [runs[0].id]
    assert runs[0].status == RunStatus.SUCCESS
    assert runs[0].result_text == "done"

    later = await svc.tick(NOW + timedelta(seconds=120))
    await _drain_runs(svc)
    runs = await _runs(task_id)
    assert len(later) == 1 and len(runs) == 2
    assert [r.status for r in runs] == [RunStatus.SUCCESS, RunStatus.SUCCESS]


async def test_manual_run_is_spawned_even_when_announce_fails(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id, next_run_at=NOW + timedelta(hours=1))
    svc = SchedulerService()
    spawned: list[int] = []
    monkeypatch.setattr(svc, "spawn_run", spawned.append)

    async def _broken(_session: Any, _task: ScheduledTask) -> Any:
        raise RuntimeError("announce broke")

    monkeypatch.setattr("agent.scheduler.build_task_out", _broken)
    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        run = await svc.start_manual_run(session, task, NOW)

    assert spawned == [run.id]


async def test_one_failing_job_does_not_abort_the_tick(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    first = await _add_task(user.id, next_run_at=NOW - timedelta(seconds=30))
    second = await _add_task(user.id, next_run_at=NOW - timedelta(seconds=20))
    svc = SchedulerService()
    real_claim = svc.claim_slot

    async def _claim(session: Any, task: ScheduledTask, now: datetime) -> Any:
        if task.id == first:
            raise RuntimeError("database is locked")
        return await real_claim(session, task, now)

    monkeypatch.setattr(svc, "claim_slot", _claim)

    started = await svc.tick(NOW, spawn=False)

    assert len(started) == 1
    assert await _runs(first) == []
    assert [r.id for r in await _runs(second)] == started


async def test_delete_takes_job_out_of_scheduling_before_aborting_runs(
    seed_user: Any, fresh_hub: EventHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user("u1", "pw")
    task_id = await _add_task(user.id)
    svc = SchedulerService()
    monkeypatch.setattr("agent.scheduler_ops.hub", fresh_hub)
    monkeypatch.setattr("agent.scheduler_ops.scheduler", svc)
    queue = fresh_hub.subscribe(user.id)

    async def _forever(*_args: Any) -> HeadlessResult:
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr("agent.scheduler.run_headless_turn", _forever)
    assert len(await svc.tick(NOW)) == 1
    await asyncio.sleep(0.1)
    assert svc._runs

    real_abort = svc.abort_task_runs
    claimed_during_abort: list[list[int]] = []

    async def _abort_then_poll(aborted_task_id: int) -> None:
        await real_abort(aborted_task_id)
        # The poll loop ticks while the runs are being aborted.
        claimed_during_abort.append(await svc.tick(NOW + timedelta(seconds=120)))

    monkeypatch.setattr(svc, "abort_task_runs", _abort_then_poll)

    async with async_session_factory() as session:
        await delete_task(session, user.id, task_id)
    await _drain_runs(svc)

    assert claimed_during_abort and all(ids == [] for ids in claimed_during_abort)
    assert not svc._runs
    async with async_session_factory() as session:
        assert await session.get(ScheduledTask, task_id) is None
    types: list[str] = []
    while not queue.empty():
        types.append(queue.get_nowait()["type"])
    assert types.count("run_started") == 1
    assert types[-1] == "task_deleted"
