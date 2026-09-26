"""Tests for ScheduledTask / TaskRun tables: overlap index, cascades, SET NULL."""

from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from shared.database import async_session_factory
from shared.models import (
    Chat,
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleType,
    TaskRun,
    User,
)

SeedUser = Callable[[str, str], Coroutine[Any, Any, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job(user_id: int, chat_id: int | None = None, title: str = "job") -> ScheduledTask:
    return ScheduledTask(
        user_id=user_id,
        origin_chat_id=chat_id,
        title=title,
        prompt="do something",
        model="test-model",
        schedule_type=ScheduleType.ONCE,
        next_run_at=_now(),
    )


def _run(job_id: int, user_id: int, status: RunStatus) -> TaskRun:
    return TaskRun(
        scheduled_task_id=job_id,
        user_id=user_id,
        status=status,
        trigger=RunTrigger.SCHEDULE,
        started_at=_now(),
        model="test-model",
    )


async def _seed_job(user_id: int, chat_id: int | None = None, title: str = "job") -> int:
    async with async_session_factory() as session:
        job = _job(user_id, chat_id, title)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _seed_chat(user_id: int) -> int:
    async with async_session_factory() as session:
        chat = Chat(title="origin", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


async def test_init_db_creates_tables_and_partial_index() -> None:
    async with async_session_factory() as session:
        rows = await session.execute(
            text("SELECT name FROM sqlite_master WHERE name IN "
                 "('scheduledtask', 'taskrun', 'uq_taskrun_one_running', 'ix_scheduledtask_status_next')")
        )
        names = {row[0] for row in rows}
    assert names == {
        "scheduledtask",
        "taskrun",
        "uq_taskrun_one_running",
        "ix_scheduledtask_status_next",
    }


async def test_scheduled_task_defaults(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    job_id = await _seed_job(user.id)
    async with async_session_factory() as session:
        job = await session.get(ScheduledTask, job_id)
    assert job.status == ScheduledTaskStatus.ACTIVE
    assert job.run_count == 0
    assert job.max_runs is None


async def test_second_running_run_rejected(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    job_id = await _seed_job(user.id)
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.RUNNING))
        await session.commit()
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.RUNNING))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_many_skipped_runs_accepted(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    job_id = await _seed_job(user.id)
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.SKIPPED))
        session.add(_run(job_id, user.id, RunStatus.SKIPPED))
        session.add(_run(job_id, user.id, RunStatus.SUCCESS))
        session.add(_run(job_id, user.id, RunStatus.FAILED))
        await session.commit()
    async with async_session_factory() as session:
        rows = (await session.exec(select(TaskRun))).all()
    assert len(rows) == 4


async def test_running_runs_for_different_jobs_accepted(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    first = await _seed_job(user.id, title="a")
    second = await _seed_job(user.id, title="b")
    async with async_session_factory() as session:
        session.add(_run(first, user.id, RunStatus.RUNNING))
        session.add(_run(second, user.id, RunStatus.RUNNING))
        await session.commit()


async def test_new_running_run_allowed_after_previous_finished(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    job_id = await _seed_job(user.id)
    async with async_session_factory() as session:
        run = _run(job_id, user.id, RunStatus.RUNNING)
        session.add(run)
        await session.commit()
        run.status = RunStatus.SUCCESS
        session.add(run)
        await session.commit()
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.RUNNING))
        await session.commit()


async def test_status_stored_as_lowercase_literal(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    job_id = await _seed_job(user.id)
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.RUNNING))
        await session.commit()
        raw_run = (await session.execute(text("SELECT status, trigger FROM taskrun"))).one()
        raw_job = (await session.execute(text("SELECT status, schedule_type FROM scheduledtask"))).one()
    assert tuple(raw_run) == ("running", "schedule")
    assert tuple(raw_job) == ("active", "once")


async def test_deleting_chat_keeps_job_with_null_origin(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    chat_id = await _seed_chat(user.id)
    job_id = await _seed_job(user.id, chat_id)
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        await session.delete(chat)
        await session.commit()
    async with async_session_factory() as session:
        job = await session.get(ScheduledTask, job_id)
    assert job is not None
    assert job.origin_chat_id is None


async def test_deleting_job_deletes_its_runs(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    job_id = await _seed_job(user.id)
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.SKIPPED))
        session.add(_run(job_id, user.id, RunStatus.SUCCESS))
        await session.commit()
    async with async_session_factory() as session:
        job = await session.get(ScheduledTask, job_id)
        await session.delete(job)
        await session.commit()
    async with async_session_factory() as session:
        remaining = (await session.exec(select(TaskRun))).all()
    assert remaining == []


async def test_deleting_user_deletes_jobs_and_runs(seed_user: SeedUser) -> None:
    user = await seed_user("alice", "pw")
    other = await seed_user("bob", "pw")
    job_id = await _seed_job(user.id)
    other_job_id = await _seed_job(other.id, title="bob-job")
    async with async_session_factory() as session:
        session.add(_run(job_id, user.id, RunStatus.SUCCESS))
        session.add(_run(other_job_id, other.id, RunStatus.SUCCESS))
        await session.commit()
    async with async_session_factory() as session:
        row = await session.get(User, user.id)
        await session.delete(row)
        await session.commit()
    async with async_session_factory() as session:
        jobs = (await session.exec(select(ScheduledTask))).all()
        runs = (await session.exec(select(TaskRun))).all()
    assert [j.id for j in jobs] == [other_job_id]
    assert [r.scheduled_task_id for r in runs] == [other_job_id]
