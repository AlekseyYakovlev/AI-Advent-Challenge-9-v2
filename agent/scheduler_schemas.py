"""Pydantic models, mappers and WS frames for the scheduler."""

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from agent.schedule import as_aware_utc
from shared.models import ScheduledTask, TaskRun


def _enum_value(value: Any) -> str:
    """Return the plain string of an enum member (or of an already-plain string)."""
    return value.value if isinstance(value, Enum) else str(value)


def _utc_or_none(value: datetime | None) -> datetime | None:
    """Return value as UTC-aware, keeping None."""
    return as_aware_utc(value) if value is not None else None


class RunSummary(BaseModel):
    """List-sized view of a run; never carries the result text."""

    id: int
    task_id: int
    status: str
    trigger: str
    scheduled_for: datetime | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    is_late: bool
    model: str


class RunDetail(RunSummary):
    """Full view of one run including its answer, error and tool trace."""

    task_title: str
    result_text: str | None
    error: str | None
    tool_trace: list[dict[str, Any]]


class ScheduledTaskOut(BaseModel):
    """A scheduled job with its newest run and live running flag."""

    id: int
    title: str
    prompt: str
    model: str
    schedule_type: str
    run_at: datetime | None
    interval_seconds: int | None
    cron: str | None
    max_runs: int | None
    run_count: int
    next_run_at: datetime | None
    status: str
    origin_chat_id: int | None
    created_at: datetime
    updated_at: datetime
    last_run: RunSummary | None
    is_running: bool


class ScheduledTaskCreate(BaseModel):
    """REST body for creating a job; semantic validation happens server-side."""

    title: str = ""
    prompt: str = ""
    model: str = ""
    schedule_type: str = "once"
    delay_seconds: int | None = None
    run_at: str | None = None
    interval_seconds: int | None = None
    cron: str | None = None
    max_runs: int | None = None


def run_to_summary(run: TaskRun) -> RunSummary:
    """Map a run row to its summary, converting timestamps to UTC-aware."""
    started_at = as_aware_utc(run.started_at)
    finished_at = _utc_or_none(run.finished_at)
    duration_ms = (
        int((finished_at - started_at).total_seconds() * 1000)
        if finished_at is not None
        else None
    )
    return RunSummary(
        id=run.id,
        task_id=run.scheduled_task_id,
        status=_enum_value(run.status),
        trigger=_enum_value(run.trigger),
        scheduled_for=_utc_or_none(run.scheduled_for),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        is_late=run.is_late,
        model=run.model,
    )


def _parse_tool_trace(raw: str | None) -> list[dict[str, Any]]:
    """Parse the stored JSON tool trace; anything unusable becomes an empty list."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def run_to_detail(run: TaskRun, task_title: str) -> RunDetail:
    """Map a run row to its full detail view."""
    summary = run_to_summary(run)
    return RunDetail(
        **summary.model_dump(),
        task_title=task_title,
        result_text=run.result_text,
        error=run.error,
        tool_trace=_parse_tool_trace(run.tool_trace),
    )


def task_to_out(
    task: ScheduledTask,
    last_run: TaskRun | None,
    is_running: bool,
) -> ScheduledTaskOut:
    """Map a job row (plus its newest run and running flag) to the API shape."""
    return ScheduledTaskOut(
        id=task.id,
        title=task.title,
        prompt=task.prompt,
        model=task.model,
        schedule_type=_enum_value(task.schedule_type),
        run_at=_utc_or_none(task.run_at),
        interval_seconds=task.interval_seconds,
        cron=task.cron_expr,
        max_runs=task.max_runs,
        run_count=task.run_count,
        next_run_at=_utc_or_none(task.next_run_at),
        status=_enum_value(task.status),
        origin_chat_id=task.origin_chat_id,
        created_at=as_aware_utc(task.created_at),
        updated_at=as_aware_utc(task.updated_at),
        last_run=run_to_summary(last_run) if last_run is not None else None,
        is_running=is_running,
    )


def _run_frame(frame_type: str, run: TaskRun, task_out: ScheduledTaskOut) -> dict[str, Any]:
    """Build a run lifecycle frame carrying a post-commit task snapshot."""
    return {
        "type": frame_type,
        "task_id": run.scheduled_task_id,
        "run": run_to_summary(run).model_dump(mode="json"),
        "task": task_out.model_dump(mode="json"),
    }


def run_started_frame(run: TaskRun, task_out: ScheduledTaskOut) -> dict[str, Any]:
    """Frame announcing a run that has just begun."""
    return _run_frame("run_started", run, task_out)


def run_finished_frame(run: TaskRun, task_out: ScheduledTaskOut) -> dict[str, Any]:
    """Frame announcing a run that has ended (success, failed or skipped)."""
    return _run_frame("run_finished", run, task_out)


def task_updated_frame(task_out: ScheduledTaskOut) -> dict[str, Any]:
    """Frame announcing that a job changed (created, paused, resumed, cancelled, completed)."""
    return {"type": "task_updated", "task": task_out.model_dump(mode="json")}


def task_deleted_frame(task_id: int) -> dict[str, Any]:
    """Frame announcing that a job and its runs were removed."""
    return {"type": "task_deleted", "task_id": task_id}


__all__ = [
    "RunDetail",
    "RunSummary",
    "ScheduledTaskCreate",
    "ScheduledTaskOut",
    "run_finished_frame",
    "run_started_frame",
    "run_to_detail",
    "run_to_summary",
    "task_deleted_frame",
    "task_to_out",
    "task_updated_frame",
]
