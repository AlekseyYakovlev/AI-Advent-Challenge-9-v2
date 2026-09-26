"""Tests for the scheduler LLM tools, the cancel-intent heuristic and the ws exclusions."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from pydantic import ValidationError
from sqlmodel import select
from starlette.testclient import TestClient

from agent import scheduler_ops
from agent.main import app
from agent.schemas import CancelScheduledTaskArgs, ListScheduledTasksArgs, ScheduleTaskArgs
from agent.state import current_chat_model
from agent.tool_guard import user_asked_to_cancel
from agent.tools import TOOL_REGISTRY, build_tool_schemas, dispatch_tool_calls
from agent.ws import SCHEDULER_TOOL_NAMES
from shared.config import settings
from shared.database import async_session_factory
from shared.models import (
    Chat,
    Message,
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    TaskRun,
)
from tests.conftest import login_test_client


@pytest.mark.parametrize(
    "text",
    [
        "отмени задание 3",
        "Останови периодическую задачу",
        "удали это задание пожалуйста",
        "убери напоминание",
        "выключи расписание",
        "прекрати проверку",
        "cancel job 5",
        "please stop the scheduled task",
        "delete it",
        "remove the job",
        "disable that schedule",
        "Отмените задание",
        "Не могли бы вы отменить задание?",
    ],
)
def test_user_asked_to_cancel_true(text: str) -> None:
    """Imperative and infinitive cancel phrasings are recognised."""
    assert user_asked_to_cancel(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "не отменяй задание",
        "не надо удалять",
        "не отменять задание",
        "don't cancel it",
        "do not stop the job",
        "never delete that",
        "покажи мои задания",
        "через минуту прочитай файл",
        "прочитай файл с удалённого сервера",
        "удаленный доступ не работает",
        "покажи удалённые задания",
        "start the stopwatch",
        "",
    ],
)
def test_user_asked_to_cancel_false(text: str) -> None:
    """Negations, unrelated words and stems that merely contain a cancel verb are rejected."""
    assert user_asked_to_cancel(text) is False


def test_schedule_args_once_delay_valid() -> None:
    """A once job with delay_seconds validates."""
    args = ScheduleTaskArgs(schedule_type="once", delay_seconds=60, title="t", prompt="p")
    assert args.delay_seconds == 60


def test_schedule_args_once_run_at_valid() -> None:
    """A once job with run_at validates."""
    args = ScheduleTaskArgs(
        schedule_type="once", run_at="2026-09-26T14:05:00", title="t", prompt="p"
    )
    assert args.run_at is not None


def test_schedule_args_once_both_rejected() -> None:
    """A once job with both delay_seconds and run_at is ambiguous."""
    with pytest.raises(ValidationError):
        ScheduleTaskArgs(
            schedule_type="once",
            delay_seconds=60,
            run_at="2026-09-26T14:05:00",
            title="t",
            prompt="p",
        )


def test_schedule_args_once_neither_rejected() -> None:
    """A once job needs a delay or an absolute time."""
    with pytest.raises(ValidationError):
        ScheduleTaskArgs(schedule_type="once", title="t", prompt="p")


def test_schedule_args_interval_requires_interval_seconds() -> None:
    """An interval job without interval_seconds is rejected; with it, it passes."""
    with pytest.raises(ValidationError):
        ScheduleTaskArgs(schedule_type="interval", title="t", prompt="p")
    args = ScheduleTaskArgs(schedule_type="interval", interval_seconds=30, title="t", prompt="p")
    assert args.interval_seconds == 30


def test_schedule_args_cron_requires_cron() -> None:
    """A cron job without an expression is rejected; with it, it passes."""
    with pytest.raises(ValidationError):
        ScheduleTaskArgs(schedule_type="cron", title="t", prompt="p")
    args = ScheduleTaskArgs(schedule_type="cron", cron="*/5 * * * *", title="t", prompt="p")
    assert args.cron == "*/5 * * * *"


def test_schedule_args_rejects_unknown_type_and_blank_text() -> None:
    """Unknown schedule types and empty title/prompt fail validation."""
    with pytest.raises(ValidationError):
        ScheduleTaskArgs(schedule_type="weekly", delay_seconds=1, title="t", prompt="p")
    with pytest.raises(ValidationError):
        ScheduleTaskArgs(schedule_type="once", delay_seconds=1, title="", prompt="p")


def test_cancel_args_require_flag() -> None:
    """user_requested_cancellation has no default."""
    with pytest.raises(ValidationError):
        CancelScheduledTaskArgs(task_id=1)
    assert CancelScheduledTaskArgs(task_id=1, user_requested_cancellation=False).task_id == 1
    with pytest.raises(ValidationError):
        CancelScheduledTaskArgs(task_id=0, user_requested_cancellation=True)


def test_list_args_has_no_fields() -> None:
    """list_scheduled_tasks takes no arguments."""
    assert ListScheduledTasksArgs.model_fields == {}


def test_no_scheduler_schema_enum_contains_cancelled() -> None:
    """Cancellation is a status, never an enum value the model can pick."""
    for model in (ScheduleTaskArgs, ListScheduledTasksArgs, CancelScheduledTaskArgs):
        for prop in model.model_json_schema().get("properties", {}).values():
            assert "cancelled" not in prop.get("enum", [])


# --- tool dispatch and integration -------------------------------------------------


@pytest.fixture
def chat_model():
    """Set the per-turn chat model ContextVar for the duration of a test."""
    token = current_chat_model.set("m1")
    yield "m1"
    current_chat_model.reset(token)


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


async def _seed_chat(user_id: int, leaf_text: str | None = None, role: str = "user") -> int:
    """Create a chat whose current leaf is a message with the given text."""
    async with async_session_factory() as session:
        chat = Chat(title="c", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        if leaf_text is not None:
            message = Message(chat_id=chat.id, role=role, content=leaf_text)
            session.add(message)
            await session.commit()
            await session.refresh(message)
            chat.current_leaf_message_id = message.id
            session.add(chat)
            await session.commit()
        return chat.id


async def _dispatch(user_id: int, chat_id: int, name: str, arguments: dict[str, Any]) -> dict:
    """Run one tool call through the dispatcher and return its single result."""
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session, user_id, chat_id, [_call("c1", name, arguments)]
        )
    return results[0]


async def _make_job(user_id: int, **overrides: Any) -> ScheduledTask:
    fields: dict[str, Any] = {
        "title": "job",
        "prompt": "do it",
        "model": "m",
        "schedule_type": "interval",
        "interval_seconds": 60,
    }
    fields.update(overrides)
    async with async_session_factory() as session:
        return await scheduler_ops.create_scheduled_task(session, user_id, **fields)


async def _job(task_id: int) -> ScheduledTask | None:
    async with async_session_factory() as session:
        return await session.get(ScheduledTask, task_id)


async def _add_run(task_id: int, user_id: int) -> None:
    async with async_session_factory() as session:
        session.add(
            TaskRun(
                scheduled_task_id=task_id,
                user_id=user_id,
                status=RunStatus.SUCCESS,
                trigger=RunTrigger.MANUAL,
                started_at=datetime.now(timezone.utc),
                model="m",
            )
        )
        await session.commit()


async def test_schedule_task_once_creates_job(
    authenticated_client: AsyncClient, chat_model: str
) -> None:
    """A once job via delay_seconds is stored for the caller with the chat model and origin."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    before = datetime.now(timezone.utc)

    result = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "once", "delay_seconds": 60, "title": "t", "prompt": "p"},
    )

    assert result["ok"] is True
    body = json.loads(result["content"])
    assert body["status"] == "scheduled"
    assert body["next_run_at"].endswith(("Z", "+00:00"))
    assert body["next_run_at_local"]
    row = await _job(body["id"])
    assert row is not None
    assert row.user_id == user_id
    assert row.model == "m1"
    assert row.origin_chat_id == chat_id
    delta = row.next_run_at.replace(tzinfo=timezone.utc) - before
    assert timedelta(seconds=55) <= delta <= timedelta(seconds=70)


async def test_schedule_task_interval_and_cron(
    authenticated_client: AsyncClient, chat_model: str
) -> None:
    """Interval and cron jobs are accepted with their schedule fields."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)

    interval = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {
            "schedule_type": "interval",
            "interval_seconds": 30,
            "title": "i",
            "prompt": "p",
            "max_runs": 3,
        },
    )
    cron = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "cron", "cron": "*/5 * * * *", "title": "c", "prompt": "p"},
    )

    assert interval["ok"] is True and json.loads(interval["content"])["max_runs"] == 3
    assert cron["ok"] is True


async def test_schedule_task_without_model_is_model_unknown(
    authenticated_client: AsyncClient,
) -> None:
    """Without a chat model in context no job is created."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)

    result = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "once", "delay_seconds": 60, "title": "t", "prompt": "p"},
    )

    assert result["ok"] is False
    assert json.loads(result["content"])["code"] == "model_unknown"
    async with async_session_factory() as session:
        assert (await session.exec(select(ScheduledTask))).all() == []


async def test_schedule_task_invalid_schedule(
    authenticated_client: AsyncClient, chat_model: str
) -> None:
    """A 6-field cron and a too-short interval come back as invalid_schedule."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)

    bad_cron = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "cron", "cron": "* * * * * *", "title": "t", "prompt": "p"},
    )
    bad_interval = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "interval", "interval_seconds": 5, "title": "t", "prompt": "p"},
    )

    for result in (bad_cron, bad_interval):
        assert result["ok"] is False
        assert json.loads(result["content"])["code"] == "invalid_schedule"
    assert json.loads(bad_cron["content"])["error"]


_OUT_OF_RANGE_ARGS = [
    {"schedule_type": "once", "delay_seconds": 10**12},
    {"schedule_type": "interval", "interval_seconds": 10**12},
    {"schedule_type": "interval", "interval_seconds": 60, "max_runs": 10**30},
    {"schedule_type": "once", "run_at": "0001-01-01T00:00:00"},
    {"schedule_type": "once", "run_at": "9999-12-31T23:59:59"},
]
_OUT_OF_RANGE_IDS = ["delay", "interval", "max-runs", "run-at-0001", "run-at-9999"]


@pytest.mark.parametrize("args", _OUT_OF_RANGE_ARGS, ids=_OUT_OF_RANGE_IDS)
async def test_schedule_task_handler_reports_out_of_range_as_invalid_schedule(
    authenticated_client: AsyncClient, chat_model: str, args: dict[str, Any]
) -> None:
    """The handler maps range errors to ok=False invalid_schedule instead of raising."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    async with async_session_factory() as session:
        result = await TOOL_REGISTRY["schedule_task"](
            session, user_id, chat_id, {"title": "t", "prompt": "p", **args}
        )
    assert result["status"] == "error"
    assert result["code"] == "invalid_schedule"
    assert await _all_jobs() == []


@pytest.mark.parametrize("args", _OUT_OF_RANGE_ARGS, ids=_OUT_OF_RANGE_IDS)
async def test_dispatch_schedule_task_out_of_range_is_not_ok_and_does_not_raise(
    authenticated_client: AsyncClient, chat_model: str, args: dict[str, Any]
) -> None:
    """Through the dispatcher an out-of-range call is reported as ok=False without raising."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)

    result = await _dispatch(
        user_id, chat_id, "schedule_task", {"title": "t", "prompt": "p", **args}
    )

    assert result["ok"] is False
    assert await _all_jobs() == []


async def test_dispatch_turns_handler_exception_into_failed_result(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected handler exception fails its own call only, never the surrounding turn."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    calls: list[str] = []

    async def _boom(_session: Any, _user_id: int, _chat_id: int, _args: dict[str, Any]) -> dict:
        calls.append("boom")
        raise RuntimeError("secret detail")

    monkeypatch.setitem(TOOL_REGISTRY, "list_scheduled_tasks", _boom)
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session,
            user_id,
            chat_id,
            [_call("c1", "list_scheduled_tasks", {}), _call("c2", "list_scheduled_tasks", {})],
        )

    assert calls == ["boom", "boom"]
    assert [r["ok"] for r in results] == [False, False]
    body = json.loads(results[0]["content"])
    assert body["status"] == "error" and body["code"] == "tool_failed"
    assert "secret detail" not in results[0]["content"]


async def test_dispatch_propagates_cancellation(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CancelledError is not swallowed by the handler guard."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)

    async def _cancelled(*_args: Any) -> dict:
        raise asyncio.CancelledError()

    monkeypatch.setitem(TOOL_REGISTRY, "list_scheduled_tasks", _cancelled)
    async with async_session_factory() as session:
        with pytest.raises(asyncio.CancelledError):
            await dispatch_tool_calls(
                session, user_id, chat_id, [_call("c1", "list_scheduled_tasks", {})]
            )


async def test_schedule_task_cap_reports_too_many(
    authenticated_client: AsyncClient, chat_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reaching the per-user cap is reported as too_many."""
    monkeypatch.setattr(settings, "SCHEDULER_MAX_ACTIVE_TASKS_PER_USER", 1)
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    await _make_job(user_id)

    result = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "once", "delay_seconds": 60, "title": "t", "prompt": "p"},
    )

    assert result["ok"] is False
    assert json.loads(result["content"])["code"] == "too_many"


async def test_list_scheduled_tasks_only_live_jobs_of_caller(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """The list shows the caller's active/paused jobs only, with last run status."""
    user_id = authenticated_client.seeded_user_id
    other_id = second_authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    active = await _make_job(user_id, title="active")
    paused = await _make_job(user_id, title="paused")
    cancelled = await _make_job(user_id, title="cancelled")
    await _make_job(other_id, title="foreign")
    async with async_session_factory() as session:
        await scheduler_ops.pause_task(session, user_id, paused.id)
        await scheduler_ops.cancel_task(session, user_id, cancelled.id)
    await _add_run(active.id, user_id)

    result = await _dispatch(user_id, chat_id, "list_scheduled_tasks", {})

    assert result["ok"] is True
    tasks = json.loads(result["content"])["tasks"]
    by_title = {t["title"]: t for t in tasks}
    assert set(by_title) == {"active", "paused"}
    assert by_title["active"]["last_run_status"] == "success"
    assert by_title["paused"]["last_run_status"] is None
    assert by_title["active"]["schedule"] == "every 60 s"
    assert by_title["active"]["next_run_at_local"]


async def test_cancel_blocked_when_latest_message_has_no_cancel_intent(
    authenticated_client: AsyncClient,
) -> None:
    """The flag alone is not enough: the user's latest message must ask to cancel."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id, "покажи задания")
    job = await _make_job(user_id)

    result = await _dispatch(
        user_id,
        chat_id,
        "cancel_scheduled_task",
        {"task_id": job.id, "user_requested_cancellation": True},
    )

    assert result["ok"] is False
    assert json.loads(result["content"])["code"] == "cancel_not_requested"
    assert (await _job(job.id)).status == ScheduledTaskStatus.ACTIVE


async def test_cancel_blocked_when_flag_is_false(authenticated_client: AsyncClient) -> None:
    """Cancel intent in the message is not enough without the explicit flag."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id, "отмени задание")
    job = await _make_job(user_id)

    result = await _dispatch(
        user_id,
        chat_id,
        "cancel_scheduled_task",
        {"task_id": job.id, "user_requested_cancellation": False},
    )

    assert json.loads(result["content"])["code"] == "cancel_not_requested"
    assert (await _job(job.id)).status == ScheduledTaskStatus.ACTIVE


async def test_cancel_blocked_when_leaf_is_not_a_user_message(
    authenticated_client: AsyncClient,
) -> None:
    """An assistant leaf carrying cancel words does not authorise a cancel."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id, "отмени задание", role="assistant")
    job = await _make_job(user_id)

    result = await _dispatch(
        user_id,
        chat_id,
        "cancel_scheduled_task",
        {"task_id": job.id, "user_requested_cancellation": True},
    )

    assert json.loads(result["content"])["code"] == "cancel_not_requested"


async def test_cancel_succeeds_when_user_asked(authenticated_client: AsyncClient) -> None:
    """A cancel request plus the flag soft-cancels the job and keeps its runs."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id, "отмени задание")
    job = await _make_job(user_id)
    await _add_run(job.id, user_id)

    result = await _dispatch(
        user_id,
        chat_id,
        "cancel_scheduled_task",
        {"task_id": job.id, "user_requested_cancellation": True},
    )

    assert result["ok"] is True
    assert json.loads(result["content"])["status"] == "cancelled"
    assert (await _job(job.id)).status == ScheduledTaskStatus.CANCELLED
    async with async_session_factory() as session:
        assert len((await session.exec(select(TaskRun))).all()) == 1


async def test_cancel_foreign_missing_and_finished_jobs(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Foreign and missing ids are not_found; an already cancelled job is a conflict."""
    user_id = authenticated_client.seeded_user_id
    other_id = second_authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id, "cancel job")
    foreign = await _make_job(other_id)
    mine = await _make_job(user_id)

    foreign_result = await _dispatch(
        user_id,
        chat_id,
        "cancel_scheduled_task",
        {"task_id": foreign.id, "user_requested_cancellation": True},
    )
    missing_result = await _dispatch(
        user_id,
        chat_id,
        "cancel_scheduled_task",
        {"task_id": 9999, "user_requested_cancellation": True},
    )
    cancel_args = {"task_id": mine.id, "user_requested_cancellation": True}
    first = await _dispatch(user_id, chat_id, "cancel_scheduled_task", cancel_args)
    second = await _dispatch(user_id, chat_id, "cancel_scheduled_task", cancel_args)

    assert json.loads(foreign_result["content"])["code"] == "not_found"
    assert json.loads(missing_result["content"])["code"] == "not_found"
    assert (await _job(foreign.id)).status == ScheduledTaskStatus.ACTIVE
    assert first["ok"] is True
    assert json.loads(second["content"])["code"] == "conflict"


async def test_cancel_without_chat_is_blocked(authenticated_client: AsyncClient) -> None:
    """A headless call (chat id 0) has no latest message and cannot cancel."""
    user_id = authenticated_client.seeded_user_id
    job = await _make_job(user_id)

    result = await _dispatch(
        user_id,
        0,
        "cancel_scheduled_task",
        {"task_id": job.id, "user_requested_cancellation": True},
    )

    assert json.loads(result["content"])["code"] == "cancel_not_requested"
    assert (await _job(job.id)).status == ScheduledTaskStatus.ACTIVE


async def test_cancel_uses_only_own_chat(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Another user's chat cannot authorise a cancel."""
    user_id = authenticated_client.seeded_user_id
    other_id = second_authenticated_client.seeded_user_id
    foreign_chat = await _seed_chat(other_id, "cancel job")
    job = await _make_job(user_id)

    result = await _dispatch(
        user_id,
        foreign_chat,
        "cancel_scheduled_task",
        {"task_id": job.id, "user_requested_cancellation": True},
    )

    assert json.loads(result["content"])["code"] == "cancel_not_requested"


async def test_chat_delete_keeps_job_with_null_origin(
    authenticated_client: AsyncClient, chat_model: str
) -> None:
    """Deleting the origin chat leaves the job in place with origin_chat_id NULL."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    result = await _dispatch(
        user_id,
        chat_id,
        "schedule_task",
        {"schedule_type": "once", "delay_seconds": 60, "title": "t", "prompt": "p"},
    )
    task_id = json.loads(result["content"])["id"]
    assert (await _job(task_id)).origin_chat_id == chat_id

    resp = await authenticated_client.delete(f"/api/v1/chats/{chat_id}")

    assert resp.status_code == 204
    row = await _job(task_id)
    assert row is not None
    assert row.origin_chat_id is None


def test_tool_schema_list_has_nine_tools_and_scheduler_tools_registered() -> None:
    """The three scheduler tools are registered and no property enum offers 'cancelled'."""
    schemas = build_tool_schemas()
    assert len(schemas) == 9
    assert set(SCHEDULER_TOOL_NAMES) <= set(TOOL_REGISTRY)
    for schema in schemas:
        for prop in schema["function"]["parameters"].get("properties", {}).values():
            assert "cancelled" not in prop.get("enum", [])


# --- full websocket turn ----------------------------------------------------------

BASE_URL = settings.LM_STUDIO_BASE_URL
WS_ORIGIN = "http://localhost:8000"
WS_MODEL = "ws-test-model"


def _sse(lines: list[str]) -> httpx.Response:
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


def _plain_response(text: str) -> httpx.Response:
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
    return _sse(
        [
            f"data: {json.dumps(chunk)}",
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )


def _tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> httpx.Response:
    delta = [
        {
            "index": 0,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
    ]
    first = {"choices": [{"delta": {"tool_calls": delta}, "finish_reason": None}]}
    second = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    return _sse([f"data: {json.dumps(first)}", f"data: {json.dumps(second)}", "data: [DONE]"])


async def _all_jobs() -> list[ScheduledTask]:
    async with async_session_factory() as session:
        return list((await session.exec(select(ScheduledTask))).all())


@respx.mock
def test_ws_turn_schedule_task_is_not_a_memory_write() -> None:
    """A schedule_task call in a chat turn creates the job but never shows up in memory_writes."""
    queue = [
        _tool_response(
            "call_1",
            "schedule_task",
            {"schedule_type": "once", "delay_seconds": 60, "title": "t", "prompt": "p"},
        ),
        _plain_response("Scheduled it."),
    ]
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=lambda _request: queue.pop(0)
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Sched"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            ws.send_json({"content": "run p in a minute", "model": WS_MODEL})
            frames = []
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame.get("type") == "done":
                    break

        done = frames[-1]
        assert done["memory_writes"] == []
        jobs = client.portal.call(_all_jobs)
        assert len(jobs) == 1
        assert jobs[0].model == WS_MODEL
        assert jobs[0].origin_chat_id == chat_id


def _read_turn(ws: Any) -> list[dict[str, Any]]:
    """Collect the frames of one chat turn up to and including its done frame."""
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done":
            return frames


@respx.mock
def test_ws_turn_with_out_of_range_schedule_task_call_keeps_the_socket_alive() -> None:
    """A failing schedule_task call streams its reply and the socket serves the next message."""
    queue = [
        _tool_response(
            "call_1",
            "schedule_task",
            {"schedule_type": "once", "run_at": "0001-01-01T00:00:00", "title": "t", "prompt": "p"},
        ),
        _plain_response("That time is not valid, please pick another one."),
        _plain_response("Second answer."),
    ]
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=lambda _request: queue.pop(0)
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Sched"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            ws.send_json({"content": "run p at the dawn of time", "model": WS_MODEL})
            first = _read_turn(ws)
            ws.send_json({"content": "thanks", "model": WS_MODEL})
            second = _read_turn(ws)

        tool_frames = [f for f in first if f.get("type") == "tool_call"]
        assert len(tool_frames) == 1 and tool_frames[0]["ok"] is False
        assert json.loads(tool_frames[0]["result"])["code"] == "invalid_schedule"
        assert any(f.get("type") == "token" for f in first)
        assert second[-1]["type"] == "done"
        assert client.portal.call(_all_jobs) == []

