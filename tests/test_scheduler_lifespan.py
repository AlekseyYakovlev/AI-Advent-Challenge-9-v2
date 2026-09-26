"""Agent lifespan wiring for the scheduler: startup recovery, gated loop, stop ordering."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from agent.main import app
from agent.scheduler import MSG_INTERRUPTED_RESTART, scheduler
from shared.config import settings
from shared.database import async_session_factory, engine
from shared.models import (
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleType,
    TaskRun,
    User,
)


async def _seed_orphan_run() -> int:
    """Insert a user, a job and a RUNNING run left behind by a dead process."""
    async with async_session_factory() as session:
        user = User(username="orphan-owner", password_hash="x")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        task = ScheduledTask(
            user_id=user.id,
            title="job",
            prompt="p",
            model="m",
            schedule_type=ScheduleType.ONCE,
            next_run_at=None,
            status=ScheduledTaskStatus.ACTIVE,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        run = TaskRun(
            scheduled_task_id=task.id,
            user_id=user.id,
            status=RunStatus.RUNNING,
            trigger=RunTrigger.SCHEDULE,
            started_at=datetime.now(timezone.utc),
            model="m",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id
    await engine.dispose()
    return run_id


async def _read_run_and_task(run_id: int) -> tuple[TaskRun, ScheduledTask]:
    async with async_session_factory() as session:
        run = await session.get(TaskRun, run_id)
        task = await session.get(ScheduledTask, run.scheduled_task_id)
        return run, task


def _assert_orphan_recovered(client: TestClient, run_id: int) -> None:
    run, task = client.portal.call(_read_run_and_task, run_id)
    assert run.status == RunStatus.FAILED
    assert run.error == MSG_INTERRUPTED_RESTART
    assert run.finished_at is not None
    assert task.status == ScheduledTaskStatus.COMPLETED


def test_loop_stays_off_when_scheduler_disabled_but_orphans_are_recovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
    run_id = asyncio.run(_seed_orphan_run())

    with TestClient(app) as client:
        assert scheduler.is_running_loop is False
        _assert_orphan_recovered(client, run_id)

    assert scheduler.is_running_loop is False


def test_loop_runs_inside_lifespan_when_enabled_and_stops_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    run_id = asyncio.run(_seed_orphan_run())

    with TestClient(app) as client:
        assert scheduler.is_running_loop is True
        _assert_orphan_recovered(client, run_id)

    assert scheduler.is_running_loop is False


def test_scheduler_stops_before_mcp_cleanup_and_engine_dispose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    order: list[str] = []
    original_stop = scheduler.stop
    original_dispose = engine.dispose

    async def _stop() -> None:
        order.append("scheduler.stop")
        await original_stop()

    async def _cleanup() -> None:
        order.append("mcp_cleanup")

    async def _dispose(*args: Any, **kwargs: Any) -> None:
        order.append("engine.dispose")
        await original_dispose(*args, **kwargs)

    monkeypatch.setattr(scheduler, "stop", _stop)
    monkeypatch.setattr("agent.main.mcp_client.cleanup_all_sessions", _cleanup)
    monkeypatch.setattr("agent.main.engine", SimpleNamespace(dispose=_dispose))

    with TestClient(app):
        pass

    assert order[:3] == ["scheduler.stop", "mcp_cleanup", "engine.dispose"]
