"""LLM tools for creating, listing and cancelling scheduled jobs."""

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from agent import scheduler_ops
from agent.schedule import ScheduleValidationError, as_aware_utc
from agent.schemas import CancelScheduledTaskArgs, ListScheduledTasksArgs, ScheduleTaskArgs
from agent.scheduler import build_task_out
from agent.state import current_chat_model
from agent.tool_guard import user_asked_to_cancel
from agent.tools import register_tool
from shared.logger import get_logger
from shared.models import Chat, Message, ScheduledTask, ScheduledTaskStatus, ScheduleType

logger = get_logger(__name__)

_LIVE_STATUSES = {ScheduledTaskStatus.ACTIVE, ScheduledTaskStatus.PAUSED}

_MSG_MODEL_UNKNOWN = (
    "The chat model is unknown; ask the user to create the job from the scheduler panel."
)
_MSG_CANCEL_NOT_REQUESTED = (
    "The user did not ask to cancel a job in their latest message. "
    "Do not cancel; ask the user to confirm explicitly."
)


def _error(code: str, message: str) -> dict[str, Any]:
    """Build the error result the dispatcher reports as ok=False."""
    return {"status": "error", "code": code, "error": message}


def _local_iso(value: Any) -> str:
    """Format a stored UTC timestamp as local time to whole seconds."""
    return as_aware_utc(value).astimezone().isoformat(timespec="seconds")


def _schedule_summary(task: ScheduledTask) -> str:
    """Return a short English description of when a job fires."""
    if task.schedule_type == ScheduleType.INTERVAL:
        return f"every {task.interval_seconds} s"
    if task.schedule_type == ScheduleType.CRON:
        return f"cron {task.cron_expr}"
    fire_time = task.run_at or task.next_run_at
    return f"once at {_local_iso(fire_time)}" if fire_time else "once"


@register_tool(
    "schedule_task",
    ScheduleTaskArgs,
    "Schedule a background job that the agent will run later WITHOUT the user present: once "
    "(after delay_seconds or at run_at local time), every interval_seconds, or on a 5-field "
    "cron in the machine's local time. prompt is the full instruction to execute at fire time. "
    "Use only when the user asks to do something later or periodically.",
)
async def _schedule_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create a job for the chatting user with the chat's model, remembering the origin chat."""
    model = current_chat_model.get()
    if model is None:
        return _error("model_unknown", _MSG_MODEL_UNKNOWN)
    try:
        row = await scheduler_ops.create_scheduled_task(
            session,
            user_id,
            title=args["title"],
            prompt=args["prompt"],
            model=model,
            schedule_type=args["schedule_type"],
            delay_seconds=args.get("delay_seconds"),
            run_at=args.get("run_at"),
            interval_seconds=args.get("interval_seconds"),
            cron=args.get("cron"),
            max_runs=args.get("max_runs"),
            origin_chat_id=chat_id,
        )
    except ScheduleValidationError as exc:
        return _error("invalid_schedule", exc.message)
    except scheduler_ops.SchedulerConflictError as exc:
        return _error("too_many", exc.message)
    return {
        "status": "scheduled",
        "id": row.id,
        "title": row.title,
        "schedule_type": row.schedule_type.value,
        "next_run_at": as_aware_utc(row.next_run_at).isoformat() if row.next_run_at else None,
        "next_run_at_local": _local_iso(row.next_run_at) if row.next_run_at else None,
        "max_runs": row.max_runs,
    }


@register_tool(
    "list_scheduled_tasks",
    ListScheduledTasksArgs,
    "List the user's active and paused scheduled jobs (id, title, schedule, next run, "
    "last run status).",
)
async def _list_scheduled_tasks(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Return the calling user's live jobs with their newest run status."""
    rows = await scheduler_ops.list_scheduled_tasks(session, user_id, statuses=_LIVE_STATUSES)
    tasks: list[dict[str, Any]] = []
    for row in rows:
        task_out = await build_task_out(session, row)
        tasks.append(
            {
                "id": row.id,
                "title": row.title,
                "schedule_type": row.schedule_type.value,
                "schedule": _schedule_summary(row),
                "next_run_at": as_aware_utc(row.next_run_at).isoformat()
                if row.next_run_at
                else None,
                "next_run_at_local": _local_iso(row.next_run_at) if row.next_run_at else None,
                "status": row.status.value,
                "last_run_status": task_out.last_run.status if task_out.last_run else None,
            }
        )
    return {"status": "ok", "tasks": tasks}


async def _latest_message_asks_to_cancel(
    session: AsyncSession, user_id: int, chat_id: int
) -> bool:
    """Check the chat's current leaf is the user's own message and asks to cancel a job."""
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id or chat.current_leaf_message_id is None:
        return False
    message = await session.get(Message, chat.current_leaf_message_id)
    if message is None or message.role != "user":
        return False
    return user_asked_to_cancel(message.content)


@register_tool(
    "cancel_scheduled_task",
    CancelScheduledTaskArgs,
    "Cancel a scheduled job ONLY when the user explicitly asked to cancel/stop/delete it in "
    "their LATEST message; never cancel on your own initiative. Requires the explicit numeric "
    "task_id (call list_scheduled_tasks first if unknown) - there is no implicit 'current job'. "
    "Set user_requested_cancellation=true only in that case.",
)
async def _cancel_scheduled_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Soft-cancel a job the user owns, but only after the code-level intent check passes."""
    task_id: int = args["task_id"]
    if not args["user_requested_cancellation"] or not await _latest_message_asks_to_cancel(
        session, user_id, chat_id
    ):
        logger.warning("scheduler_cancel_gate_blocked", user_id=user_id, task_id=task_id)
        return _error("cancel_not_requested", _MSG_CANCEL_NOT_REQUESTED)
    try:
        row = await scheduler_ops.cancel_task(session, user_id, task_id)
    except scheduler_ops.SchedulerNotFoundError:
        return _error("not_found", f"scheduled job {task_id} not found")
    except scheduler_ops.SchedulerConflictError as exc:
        return _error("conflict", exc.message)
    return {"status": "cancelled", "id": row.id, "title": row.title}
