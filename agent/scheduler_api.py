"""REST routes for the scheduler (/api/v1/scheduler)."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import scheduler_ops
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.schedule import ScheduleValidationError
from agent.scheduler import build_task_out
from agent.scheduler_schemas import (
    RunDetail,
    RunSummary,
    ScheduledTaskCreate,
    ScheduledTaskOut,
    run_to_detail,
    run_to_summary,
)
from shared.database import get_session
from shared.models import ScheduledTask, User

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])

MSG_TASK_NOT_FOUND = "Задание не найдено"
MSG_RUN_NOT_FOUND = "Запуск не найден"


def _not_found(detail: str) -> HTTPException:
    """Build the 404 used for missing and foreign ids alike (never 403)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _validation_error(exc: ScheduleValidationError) -> HTTPException:
    """Build the 422 carrying the Russian message the UI shows verbatim."""
    return HTTPException(
        status_code=422,  # the status constant was renamed across Starlette versions
        detail=exc.message,
    )


def _conflict(exc: scheduler_ops.SchedulerConflictError) -> HTTPException:
    """Build the 409 carrying the Russian conflict message."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message)


@router.get("/tasks", response_model=list[ScheduledTaskOut])
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ScheduledTaskOut]:
    """List the current user's scheduled jobs."""
    rows = await scheduler_ops.list_scheduled_tasks(session, current_user.id)
    return [await build_task_out(session, row) for row in rows]


@router.post(
    "/tasks",
    response_model=ScheduledTaskOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_task(
    body: ScheduledTaskCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskOut:
    """Create a once, interval or cron job."""
    try:
        row = await scheduler_ops.create_scheduled_task(
            session, current_user.id, **body.model_dump()
        )
    except ScheduleValidationError as exc:
        raise _validation_error(exc) from exc
    except scheduler_ops.SchedulerConflictError as exc:
        raise _conflict(exc) from exc
    return await build_task_out(session, row)


@router.get("/tasks/{task_id}", response_model=ScheduledTaskOut)
async def get_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskOut:
    """Return one of the current user's jobs."""
    try:
        row = await scheduler_ops.get_owned_task(session, current_user.id, task_id)
    except scheduler_ops.SchedulerNotFoundError as exc:
        raise _not_found(MSG_TASK_NOT_FOUND) from exc
    return await build_task_out(session, row)


async def _change_state(
    operation: Callable[[AsyncSession, int, int], Awaitable[ScheduledTask]],
    task_id: int,
    session: AsyncSession,
    current_user: User,
) -> ScheduledTaskOut:
    """Run a pause, resume or cancel operation and map its errors to HTTP status codes."""
    try:
        row = await operation(session, current_user.id, task_id)
    except scheduler_ops.SchedulerNotFoundError as exc:
        raise _not_found(MSG_TASK_NOT_FOUND) from exc
    except scheduler_ops.SchedulerConflictError as exc:
        raise _conflict(exc) from exc
    return await build_task_out(session, row)


@router.post(
    "/tasks/{task_id}/pause",
    response_model=ScheduledTaskOut,
    dependencies=[Depends(require_allowed_origin)],
)
async def pause_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskOut:
    """Pause an active job."""
    return await _change_state(scheduler_ops.pause_task, task_id, session, current_user)


@router.post(
    "/tasks/{task_id}/resume",
    response_model=ScheduledTaskOut,
    dependencies=[Depends(require_allowed_origin)],
)
async def resume_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskOut:
    """Resume a paused job."""
    return await _change_state(scheduler_ops.resume_task, task_id, session, current_user)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=ScheduledTaskOut,
    dependencies=[Depends(require_allowed_origin)],
)
async def cancel_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskOut:
    """Cancel a job softly, keeping its run history."""
    return await _change_state(scheduler_ops.cancel_task, task_id, session, current_user)


@router.post(
    "/tasks/{task_id}/run",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_allowed_origin)],
)
async def run_task_now(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> RunSummary:
    """Start an off-schedule run of a job."""
    try:
        run = await scheduler_ops.run_task_now(session, current_user.id, task_id)
    except scheduler_ops.SchedulerNotFoundError as exc:
        raise _not_found(MSG_TASK_NOT_FOUND) from exc
    except scheduler_ops.SchedulerConflictError as exc:
        raise _conflict(exc) from exc
    return run_to_summary(run)


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Delete a job and its run history."""
    try:
        await scheduler_ops.delete_task(session, current_user.id, task_id)
    except scheduler_ops.SchedulerNotFoundError as exc:
        raise _not_found(MSG_TASK_NOT_FOUND) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tasks/{task_id}/runs", response_model=list[RunSummary])
async def list_task_runs(
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[RunSummary]:
    """List a job's runs, newest first, without their result text."""
    try:
        runs = await scheduler_ops.list_task_runs(session, current_user.id, task_id, limit)
    except scheduler_ops.SchedulerNotFoundError as exc:
        raise _not_found(MSG_TASK_NOT_FOUND) from exc
    return [run_to_summary(run) for run in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> RunDetail:
    """Return one run with its full result, error and tool trace."""
    try:
        run, task = await scheduler_ops.get_owned_run(session, current_user.id, run_id)
    except scheduler_ops.SchedulerNotFoundError as exc:
        raise _not_found(MSG_RUN_NOT_FOUND) from exc
    return run_to_detail(run, task.title)
