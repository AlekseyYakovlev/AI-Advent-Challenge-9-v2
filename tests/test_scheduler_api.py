"""Scheduler operations and REST tests: validation copy, lifecycle, conflicts, IDOR, live events."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent.events import hub
from agent.schedule import (
    MSG_CRON_INVALID,
    MSG_RUN_AT_PAST,
    MSG_TITLE_PROMPT_REQUIRED,
    ScheduleValidationError,
    as_aware_utc,
    msg_min_interval,
)
from agent.scheduler import scheduler
from agent.scheduler_ops import (
    MSG_TOO_MANY_TASKS,
    SchedulerConflictError,
    SchedulerNotFoundError,
    cancel_task,
    create_scheduled_task,
    delete_task,
    get_owned_run,
    get_owned_task,
    list_scheduled_tasks,
    list_task_runs,
    pause_task,
    resume_task,
    run_task_now,
)
from shared.config import settings
from shared.database import async_session_factory
from shared.models import (
    Chat,
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleType,
    TaskRun,
)

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep manual runs from reaching the LLM: spawning a run is a no-op."""
    monkeypatch.setattr(scheduler, "spawn_run", lambda run_id: None)


async def _create(user_id: int, **overrides: Any) -> ScheduledTask:
    """Create an interval job through the ops layer."""
    fields: dict[str, Any] = {
        "title": "job",
        "prompt": "do it",
        "model": "m",
        "schedule_type": "interval",
        "interval_seconds": 60,
    }
    fields.update(overrides)
    async with async_session_factory() as session:
        return await create_scheduled_task(session, user_id, **fields)


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


async def _db_task(task_id: int) -> ScheduledTask | None:
    async with async_session_factory() as session:
        return await session.get(ScheduledTask, task_id)


async def _set_status(task_id: int, status: ScheduledTaskStatus) -> None:
    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        task.status = status
        session.add(task)
        await session.commit()


def _drain(queue: Any) -> list[dict[str, Any]]:
    """Return every frame currently queued."""
    frames: list[dict[str, Any]] = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


# --------------------------------------------------------------------------- ops: create


async def test_create_once_sets_next_run_and_origin(
    authenticated_client: AsyncClient,
) -> None:
    """A once job with delay_seconds fires at now + delay and keeps its origin chat."""
    user_id = authenticated_client.seeded_user_id
    async with async_session_factory() as session:
        chat = Chat(title="origin", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        task = await create_scheduled_task(
            session,
            user_id,
            title=" t ",
            prompt=" p ",
            model="m",
            schedule_type="once",
            delay_seconds=60,
            origin_chat_id=chat.id,
            now=NOW,
        )
    assert task.status == ScheduledTaskStatus.ACTIVE
    assert task.title == "t" and task.prompt == "p"
    assert task.schedule_type == ScheduleType.ONCE
    assert as_aware_utc(task.next_run_at) == NOW + timedelta(seconds=60)
    assert as_aware_utc(task.run_at) == NOW + timedelta(seconds=60)
    assert task.run_count == 0
    assert task.origin_chat_id == chat.id


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"title": "   "}, MSG_TITLE_PROMPT_REQUIRED),
        ({"prompt": ""}, MSG_TITLE_PROMPT_REQUIRED),
        ({"title": "x" * 201}, "Название не длиннее 200 символов"),
        ({"prompt": "x" * 4001}, "Промпт не длиннее 4000 символов"),
        ({"model": "  "}, "Выберите модель"),
    ],
    ids=["blank-title", "blank-prompt", "long-title", "long-prompt", "blank-model"],
)
async def test_create_rejects_invalid_text_fields(
    authenticated_client: AsyncClient, overrides: dict[str, Any], message: str
) -> None:
    """Blank or oversized title, prompt and model raise a Russian ScheduleValidationError."""
    with pytest.raises(ScheduleValidationError) as info:
        await _create(authenticated_client.seeded_user_id, **overrides)
    assert info.value.message == message


async def test_create_enforces_per_user_cap(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active and paused jobs count toward the cap; finished jobs and other users do not."""
    monkeypatch.setattr(settings, "SCHEDULER_MAX_ACTIVE_TASKS_PER_USER", 2)
    user_id = authenticated_client.seeded_user_id
    first = await _create(user_id)
    second = await _create(user_id)
    await _set_status(second.id, ScheduledTaskStatus.PAUSED)

    with pytest.raises(SchedulerConflictError) as info:
        await _create(user_id)
    assert info.value.message == MSG_TOO_MANY_TASKS.format(n=2)

    await _create(second_authenticated_client.seeded_user_id)
    await _set_status(first.id, ScheduledTaskStatus.CANCELLED)
    await _create(user_id)


# --------------------------------------------------------------------------- ops: lifecycle


async def test_pause_keeps_next_run_and_rejects_second_pause(
    authenticated_client: AsyncClient,
) -> None:
    """Pausing keeps next_run_at; pausing a paused job is a conflict."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    expected_next = task.next_run_at
    async with async_session_factory() as session:
        paused = await pause_task(session, user_id, task.id)
        assert paused.status == ScheduledTaskStatus.PAUSED
        assert paused.next_run_at == expected_next
        with pytest.raises(SchedulerConflictError) as info:
            await pause_task(session, user_id, task.id)
    assert info.value.message == "Задание не активно"


async def test_resume_interval_restarts_from_now(authenticated_client: AsyncClient) -> None:
    """Resuming an interval job schedules now + interval; resuming an active job conflicts."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    resume_at = NOW + timedelta(hours=3)
    async with async_session_factory() as session:
        await pause_task(session, user_id, task.id)
        resumed = await resume_task(session, user_id, task.id, now=resume_at)
        assert resumed.status == ScheduledTaskStatus.ACTIVE
        assert as_aware_utc(resumed.next_run_at) == resume_at + timedelta(seconds=60)
        with pytest.raises(SchedulerConflictError) as info:
            await resume_task(session, user_id, task.id)
    assert info.value.message == "Задание не на паузе"


async def test_resume_once_keeps_run_at(authenticated_client: AsyncClient) -> None:
    """A paused once job resumes on its original run_at (it may then fire late)."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id, schedule_type="once", delay_seconds=300, interval_seconds=None)
    async with async_session_factory() as session:
        await pause_task(session, user_id, task.id)
        resumed = await resume_task(session, user_id, task.id)
    assert resumed.next_run_at == task.run_at


async def test_cancel_is_soft_and_rejects_finished(authenticated_client: AsyncClient) -> None:
    """Cancel clears next_run_at, keeps the runs and conflicts once the job is finished."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    run_id = await _add_run(task.id, user_id, RunStatus.SUCCESS)
    async with async_session_factory() as session:
        cancelled = await cancel_task(session, user_id, task.id)
        assert cancelled.status == ScheduledTaskStatus.CANCELLED
        assert cancelled.next_run_at is None
        with pytest.raises(SchedulerConflictError) as info:
            await cancel_task(session, user_id, task.id)
        assert info.value.message == "Задание уже завершено"
        assert await session.get(TaskRun, run_id) is not None


async def test_cancel_completed_is_conflict(authenticated_client: AsyncClient) -> None:
    """A completed job cannot be cancelled."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    await _set_status(task.id, ScheduledTaskStatus.COMPLETED)
    async with async_session_factory() as session:
        with pytest.raises(SchedulerConflictError):
            await cancel_task(session, user_id, task.id)


async def test_run_now_creates_manual_run_and_rejects_second(
    authenticated_client: AsyncClient,
) -> None:
    """Run now starts a RUNNING manual run; a second call while it runs is a conflict."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    async with async_session_factory() as session:
        run = await run_task_now(session, user_id, task.id)
    assert run.status == RunStatus.RUNNING
    assert run.trigger == RunTrigger.MANUAL
    async with async_session_factory() as session:
        with pytest.raises(SchedulerConflictError) as info:
            await run_task_now(session, user_id, task.id)
    assert info.value.message == "Задание уже выполняется"


async def test_run_now_works_when_paused_and_rejects_finished(
    authenticated_client: AsyncClient,
) -> None:
    """A paused job can be run manually; completed and cancelled jobs cannot."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    await _set_status(task.id, ScheduledTaskStatus.PAUSED)
    async with async_session_factory() as session:
        run = await run_task_now(session, user_id, task.id)
    assert run.status == RunStatus.RUNNING

    for finished in (ScheduledTaskStatus.COMPLETED, ScheduledTaskStatus.CANCELLED):
        await _set_status(task.id, finished)
        async with async_session_factory() as session:
            with pytest.raises(SchedulerConflictError) as info:
                await run_task_now(session, user_id, task.id)
        assert info.value.message == "Задание уже завершено"


async def test_delete_removes_runs_and_aborts_first(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete removes the job with its runs; the in-flight abort happens before the row goes."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    await _add_run(task.id, user_id, RunStatus.SUCCESS)
    await _add_run(task.id, user_id, RunStatus.FAILED)
    seen: list[bool] = []

    async def fake_abort(task_id: int) -> None:
        seen.append(await _db_task(task_id) is not None)

    monkeypatch.setattr(scheduler, "abort_task_runs", fake_abort)
    async with async_session_factory() as session:
        await delete_task(session, user_id, task.id)

    assert seen == [True]
    assert await _db_task(task.id) is None
    async with async_session_factory() as session:
        runs = (
            await session.exec(select(TaskRun).where(TaskRun.scheduled_task_id == task.id))
        ).all()
    assert runs == []


async def test_foreign_task_and_run_are_not_found(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Every operation on another user's job or run raises SchedulerNotFoundError."""
    owner = authenticated_client.seeded_user_id
    other = second_authenticated_client.seeded_user_id
    task = await _create(owner)
    run_id = await _add_run(task.id, owner, RunStatus.SUCCESS)

    async with async_session_factory() as session:
        for operation in (
            get_owned_task,
            pause_task,
            resume_task,
            cancel_task,
            delete_task,
            run_task_now,
            list_task_runs,
        ):
            with pytest.raises(SchedulerNotFoundError):
                await operation(session, other, task.id)
        with pytest.raises(SchedulerNotFoundError):
            await get_owned_run(session, other, run_id)
        assert await list_scheduled_tasks(session, other) == []

    stored = await _db_task(task.id)
    assert stored is not None and stored.status == ScheduledTaskStatus.ACTIVE


async def test_mutations_publish_events_to_owner_only(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Each mutation pushes exactly one frame to the owner and none to another user."""
    owner = authenticated_client.seeded_user_id
    other = second_authenticated_client.seeded_user_id
    owner_queue = hub.subscribe(owner)
    other_queue = hub.subscribe(other)

    task = await _create(owner)
    frames = _drain(owner_queue)
    assert [f["type"] for f in frames] == ["task_updated"]
    assert frames[0]["task"]["id"] == task.id

    async with async_session_factory() as session:
        await pause_task(session, owner, task.id)
        assert [f["type"] for f in _drain(owner_queue)] == ["task_updated"]
        await resume_task(session, owner, task.id)
        assert [f["type"] for f in _drain(owner_queue)] == ["task_updated"]
        await cancel_task(session, owner, task.id)
        assert [f["type"] for f in _drain(owner_queue)] == ["task_updated"]
        await delete_task(session, owner, task.id)
    deleted = _drain(owner_queue)
    assert deleted == [{"type": "task_deleted", "task_id": task.id}]
    assert _drain(other_queue) == []


async def test_list_orders_live_first_then_next_run_then_newest(
    authenticated_client: AsyncClient,
) -> None:
    """Active and paused jobs come first, ordered by next_run_at; finished ones follow."""
    user_id = authenticated_client.seeded_user_id
    later = await _create(user_id, interval_seconds=600, title="later")
    sooner = await _create(user_id, interval_seconds=30, title="sooner")
    done = await _create(user_id, title="done")
    await _set_status(done.id, ScheduledTaskStatus.COMPLETED)

    async with async_session_factory() as session:
        rows = await list_scheduled_tasks(session, user_id)
        only_done = await list_scheduled_tasks(
            session, user_id, statuses={ScheduledTaskStatus.COMPLETED}
        )
    assert [row.id for row in rows] == [sooner.id, later.id, done.id]
    assert [row.id for row in only_done] == [done.id]


async def test_list_task_runs_newest_first_and_limited(
    authenticated_client: AsyncClient,
) -> None:
    """Runs are listed newest first and respect the limit."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    old = await _add_run(task.id, user_id, RunStatus.SUCCESS, started_at=NOW)
    new = await _add_run(task.id, user_id, RunStatus.FAILED, started_at=NOW + timedelta(hours=1))
    async with async_session_factory() as session:
        runs = await list_task_runs(session, user_id, task.id)
        limited = await list_task_runs(session, user_id, task.id, limit=1)
        run, owner_task = await get_owned_run(session, user_id, old)
    assert [r.id for r in runs] == [new, old]
    assert [r.id for r in limited] == [new]
    assert run.id == old and owner_task.id == task.id
