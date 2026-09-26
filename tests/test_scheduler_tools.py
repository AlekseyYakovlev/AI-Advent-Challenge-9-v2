"""Tests for the scheduler LLM tools, the cancel-intent heuristic and the ws exclusions."""

import pytest
from pydantic import ValidationError

from agent.schemas import CancelScheduledTaskArgs, ListScheduledTasksArgs, ScheduleTaskArgs
from agent.tool_guard import user_asked_to_cancel


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
