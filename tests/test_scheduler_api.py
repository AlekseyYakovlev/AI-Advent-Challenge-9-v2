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


async def _clear_next_run(task_id: int, status: ScheduledTaskStatus) -> None:
    """Put a job in the state of a final run in flight: slot consumed, status still live."""
    async with async_session_factory() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        task.next_run_at = None
        task.status = status
        session.add(task)
        await session.commit()


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
    """Delete cancels scheduling first, aborts runs, then removes the job with its runs."""
    user_id = authenticated_client.seeded_user_id
    task = await _create(user_id)
    await _add_run(task.id, user_id, RunStatus.SUCCESS)
    await _add_run(task.id, user_id, RunStatus.FAILED)
    seen: list[tuple[ScheduledTaskStatus, bool] | None] = []

    async def fake_abort(task_id: int) -> None:
        row = await _db_task(task_id)
        seen.append(None if row is None else (row.status, row.next_run_at is None))

    monkeypatch.setattr(scheduler, "abort_task_runs", fake_abort)
    async with async_session_factory() as session:
        await delete_task(session, user_id, task.id)

    # First abort: the job is already out of scheduling but still stored; the second one
    # (after the delete) catches a run spawned in between.
    assert seen == [(ScheduledTaskStatus.CANCELLED, True), None]
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


# --------------------------------------------------------------------------- REST


async def _post_task(client: AsyncClient, **overrides: Any) -> Any:
    """POST a valid interval job over REST and return the response."""
    body: dict[str, Any] = {
        "title": "Digest",
        "prompt": "summarize",
        "model": "deepseek-chat",
        "schedule_type": "interval",
        "interval_seconds": 60,
    }
    body.update(overrides)
    return await client.post("/api/v1/scheduler/tasks", json=body, headers=ORIGIN)


async def _api_create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    resp = await _post_task(client, **overrides)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_rest_create_returns_201_with_utc_iso(authenticated_client: AsyncClient) -> None:
    """POST /tasks answers 201 with UTC-aware ISO timestamps and the stored schedule."""
    resp = await _post_task(authenticated_client, max_runs=3)
    assert resp.status_code == 201
    data = resp.json()
    assert data["schedule_type"] == "interval"
    assert data["max_runs"] == 3
    assert data["status"] == "active"
    for key in ("next_run_at", "created_at"):
        assert data[key].endswith("Z") or data[key].endswith("+00:00"), data[key]
    assert data["last_run"] is None and data["is_running"] is False


@pytest.mark.parametrize(
    "overrides,detail",
    [
        ({"schedule_type": "cron", "cron": "*/5 * * * * *"}, MSG_CRON_INVALID),
        (
            {"schedule_type": "once", "interval_seconds": None, "run_at": "2000-01-01T00:00:00Z"},
            MSG_RUN_AT_PAST,
        ),
        ({"title": "  "}, MSG_TITLE_PROMPT_REQUIRED),
        ({"interval_seconds": 5}, msg_min_interval(10)),
        ({"model": ""}, "Выберите модель"),
    ],
    ids=["cron-6-fields", "run-at-past", "blank-title", "interval-too-short", "blank-model"],
)
async def test_rest_create_validation_returns_russian_422(
    authenticated_client: AsyncClient, overrides: dict[str, Any], detail: str
) -> None:
    """Invalid input answers 422 with a plain Russian string detail."""
    resp = await _post_task(authenticated_client, **overrides)
    assert resp.status_code == 422
    assert resp.json()["detail"] == detail


async def test_rest_create_rejects_non_json_and_foreign_origin(
    authenticated_client: AsyncClient,
) -> None:
    """The CSRF guards apply to creation: 415 without JSON, 403 for a foreign Origin."""
    resp = await authenticated_client.post(
        "/api/v1/scheduler/tasks",
        content='{"title": "x"}',
        headers={**ORIGIN, "Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    resp = await authenticated_client.post(
        "/api/v1/scheduler/tasks",
        json={"title": "x"},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403


async def test_rest_create_over_cap_is_409(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exceeding the per-user cap answers 409 with the Russian cap message."""
    monkeypatch.setattr(settings, "SCHEDULER_MAX_ACTIVE_TASKS_PER_USER", 1)
    await _api_create(authenticated_client)
    resp = await _post_task(authenticated_client)
    assert resp.status_code == 409
    assert resp.json()["detail"] == MSG_TOO_MANY_TASKS.format(n=1)


async def test_rest_list_and_get_are_per_user(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """The list holds only the caller's jobs and a foreign id answers 404."""
    created = await _api_create(authenticated_client)
    listed = await authenticated_client.get("/api/v1/scheduler/tasks")
    assert [item["id"] for item in listed.json()] == [created["id"]]
    other_list = await second_authenticated_client.get("/api/v1/scheduler/tasks")
    assert other_list.json() == []
    foreign = await second_authenticated_client.get(f"/api/v1/scheduler/tasks/{created['id']}")
    assert foreign.status_code == 404
    own = await authenticated_client.get(f"/api/v1/scheduler/tasks/{created['id']}")
    assert own.status_code == 200 and own.json()["title"] == "Digest"


async def test_rest_pause_resume_cancel_lifecycle(authenticated_client: AsyncClient) -> None:
    """Pause, resume and cancel change the status; repeating an illegal step is a 409."""
    base = f"/api/v1/scheduler/tasks/{(await _api_create(authenticated_client))['id']}"

    resp = await authenticated_client.post(f"{base}/pause", headers=ORIGIN)
    assert resp.status_code == 200 and resp.json()["status"] == "paused"
    resp = await authenticated_client.post(f"{base}/pause", headers=ORIGIN)
    assert resp.status_code == 409 and resp.json()["detail"] == "Задание не активно"

    resp = await authenticated_client.post(f"{base}/resume", headers=ORIGIN)
    assert resp.status_code == 200 and resp.json()["status"] == "active"
    resp = await authenticated_client.post(f"{base}/resume", headers=ORIGIN)
    assert resp.status_code == 409

    resp = await authenticated_client.post(f"{base}/cancel", headers=ORIGIN)
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    resp = await authenticated_client.post(f"{base}/cancel", headers=ORIGIN)
    assert resp.status_code == 409 and resp.json()["detail"] == "Задание уже завершено"


async def test_rest_pause_and_resume_of_job_with_final_run_in_flight_are_409(
    authenticated_client: AsyncClient,
) -> None:
    """A job whose slot is consumed (final run in flight) can be neither paused nor resumed."""
    task_id = (await _api_create(authenticated_client))["id"]
    base = f"/api/v1/scheduler/tasks/{task_id}"

    await _clear_next_run(task_id, ScheduledTaskStatus.ACTIVE)
    resp = await authenticated_client.post(f"{base}/pause", headers=ORIGIN)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Задание выполняет последний запуск"

    await _clear_next_run(task_id, ScheduledTaskStatus.PAUSED)
    resp = await authenticated_client.post(f"{base}/resume", headers=ORIGIN)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Задание выполняет последний запуск"
    stored = await _db_task(task_id)
    assert stored is not None
    assert stored.status == ScheduledTaskStatus.PAUSED and stored.next_run_at is None


async def test_rest_run_now_then_conflict(authenticated_client: AsyncClient) -> None:
    """Run now answers 202 with a running manual run; a second call answers 409."""
    task_id = (await _api_create(authenticated_client))["id"]
    url = f"/api/v1/scheduler/tasks/{task_id}/run"
    resp = await authenticated_client.post(url, headers=ORIGIN)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "running" and data["trigger"] == "manual"
    assert data["task_id"] == task_id
    resp = await authenticated_client.post(url, headers=ORIGIN)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Задание уже выполняется"


async def test_rest_runs_and_run_detail(authenticated_client: AsyncClient) -> None:
    """The run list is newest first without result_text; the detail carries the full result."""
    user_id = authenticated_client.seeded_user_id
    task_id = (await _api_create(authenticated_client, title="Report"))["id"]
    old = await _add_run(task_id, user_id, RunStatus.FAILED, error="boom")
    new = await _add_run(
        task_id,
        user_id,
        RunStatus.SUCCESS,
        started_at=NOW + timedelta(hours=1),
        finished_at=NOW + timedelta(hours=1, seconds=2),
        result_text="all good",
        tool_trace='[{"tool": "search", "ok": true}]',
    )

    listed = await authenticated_client.get(f"/api/v1/scheduler/tasks/{task_id}/runs")
    assert listed.status_code == 200
    items = listed.json()
    assert [item["id"] for item in items] == [new, old]
    assert all("result_text" not in item for item in items)
    assert items[0]["duration_ms"] == 2000

    detail = await authenticated_client.get(f"/api/v1/scheduler/runs/{new}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["result_text"] == "all good"
    assert body["task_title"] == "Report"
    assert body["tool_trace"] == [{"tool": "search", "ok": True}]
    failed = (await authenticated_client.get(f"/api/v1/scheduler/runs/{old}")).json()
    assert failed["error"] == "boom" and failed["tool_trace"] == []

    too_many = await authenticated_client.get(f"/api/v1/scheduler/tasks/{task_id}/runs?limit=101")
    assert too_many.status_code == 422


async def test_rest_delete_removes_job_and_runs(authenticated_client: AsyncClient) -> None:
    """DELETE answers 204; afterwards the job is 404 and no run rows remain."""
    user_id = authenticated_client.seeded_user_id
    task_id = (await _api_create(authenticated_client))["id"]
    await _add_run(task_id, user_id, RunStatus.SUCCESS)

    resp = await authenticated_client.delete(f"/api/v1/scheduler/tasks/{task_id}", headers=ORIGIN)
    assert resp.status_code == 204
    again = await authenticated_client.get(f"/api/v1/scheduler/tasks/{task_id}")
    assert again.status_code == 404
    async with async_session_factory() as session:
        runs = (
            await session.exec(select(TaskRun).where(TaskRun.scheduled_task_id == task_id))
        ).all()
    assert runs == []


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", ""),
        ("POST", "/pause"),
        ("POST", "/resume"),
        ("POST", "/cancel"),
        ("POST", "/run"),
        ("DELETE", ""),
        ("GET", "/runs"),
    ],
)
async def test_rest_foreign_task_routes_return_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    method: str,
    suffix: str,
) -> None:
    """Another user's job id answers 404 on every route and leaves the job untouched."""
    task_id = (await _api_create(authenticated_client))["id"]
    resp = await second_authenticated_client.request(
        method, f"/api/v1/scheduler/tasks/{task_id}{suffix}", headers=ORIGIN
    )
    assert resp.status_code == 404
    stored = await _db_task(task_id)
    assert stored is not None
    assert stored.status == ScheduledTaskStatus.ACTIVE and stored.run_count == 0
    async with async_session_factory() as session:
        runs = (await session.exec(select(TaskRun))).all()
    assert runs == []


async def test_rest_foreign_run_detail_returns_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Another user's run id answers 404 (never 403) and its own owner still reads it."""
    task_id = (await _api_create(authenticated_client))["id"]
    run_id = await _add_run(task_id, authenticated_client.seeded_user_id, RunStatus.SUCCESS)
    resp = await second_authenticated_client.get(f"/api/v1/scheduler/runs/{run_id}")
    assert resp.status_code == 404
    own = await authenticated_client.get(f"/api/v1/scheduler/runs/{run_id}")
    assert own.status_code == 200


async def test_rest_mutations_require_allowed_origin(authenticated_client: AsyncClient) -> None:
    """Mutating routes reject a foreign Origin with 403 before touching the job."""
    task_id = (await _api_create(authenticated_client))["id"]
    resp = await authenticated_client.post(
        f"/api/v1/scheduler/tasks/{task_id}/pause", headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
    resp = await authenticated_client.delete(
        f"/api/v1/scheduler/tasks/{task_id}", headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
    stored = await _db_task(task_id)
    assert stored is not None and stored.status == ScheduledTaskStatus.ACTIVE


async def test_rest_mutation_publishes_event_to_owner(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """A REST pause pushes task_updated to the owner's sockets and nothing to another user."""
    owner_queue = hub.subscribe(authenticated_client.seeded_user_id)
    other_queue = hub.subscribe(second_authenticated_client.seeded_user_id)
    task_id = (await _api_create(authenticated_client))["id"]
    _drain(owner_queue)
    await authenticated_client.post(f"/api/v1/scheduler/tasks/{task_id}/pause", headers=ORIGIN)
    frames = _drain(owner_queue)
    assert [f["type"] for f in frames] == ["task_updated"]
    assert frames[0]["task"]["status"] == "paused"
    assert _drain(other_queue) == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/scheduler/tasks"),
        ("POST", "/api/v1/scheduler/tasks"),
        ("GET", "/api/v1/scheduler/tasks/1"),
        ("POST", "/api/v1/scheduler/tasks/1/pause"),
        ("POST", "/api/v1/scheduler/tasks/1/resume"),
        ("POST", "/api/v1/scheduler/tasks/1/cancel"),
        ("POST", "/api/v1/scheduler/tasks/1/run"),
        ("DELETE", "/api/v1/scheduler/tasks/1"),
        ("GET", "/api/v1/scheduler/tasks/1/runs"),
        ("GET", "/api/v1/scheduler/runs/1"),
    ],
)
async def test_rest_routes_require_a_session(client: AsyncClient, method: str, path: str) -> None:
    """Every scheduler route answers 401 without a session cookie."""
    resp = await client.request(method, path, headers=ORIGIN, json={} if method == "POST" else None)
    assert resp.status_code == 401
