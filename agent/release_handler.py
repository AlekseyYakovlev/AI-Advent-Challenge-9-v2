"""WebSocket entry of the /release command: plan and confirm under the chat lock."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from agent.command_turn import notice_frame
from agent.llm_client import LLMClient
from agent.providers import ProviderUnavailableError, resolve_client
from agent.release_command import (
    MSG_NO_PLAN,
    MSG_PLAN_STALE,
    MSG_USAGE,
    FrameSender,
    ReleaseCommand,
    ReleasePlan,
    parse_release,
    parse_release_frame,
    plan_frame,
)
from agent.release_executor import confirm_plan
from agent.release_llm import ReleaseLlm
from agent.release_mcp import ReleaseMcp, ReleaseMcpError, ReleaseRows, resolve_release_rows
from agent.release_pipeline import tag_message
from agent.release_planner import build_plan
from agent.schemas import MessagePayload
from agent.state import chat_locks, release_plans
from shared.database import async_session_factory
from shared.logger import get_logger

logger = get_logger(__name__)

RELEASE_ERROR_CODE = "RELEASE_FAILED"
MSG_RELEASE_FAILED = "Не удалось выполнить /release."


def _parse(raw: Any) -> ReleaseCommand | None:
    """Parse a release_confirm frame or a /release chat message."""
    cmd = parse_release_frame(raw)
    if cmd is not None:
        return cmd
    content = raw.get("content") if isinstance(raw, dict) else None
    return parse_release(content) if isinstance(content, str) else None


async def _load_rows(user_id: int) -> ReleaseRows:
    """Resolve the user's release-git and github server rows."""
    async with async_session_factory() as db:
        return await resolve_release_rows(db, user_id)


async def _run_plan(
    send: FrameSender, chat_id: int, user_id: int, cmd: ReleaseCommand, raw: Any
) -> None:
    """Plan phase: resolve the chat's client, read the repo and send the plan frame."""
    try:
        payload = MessagePayload.model_validate(raw)
    except ValidationError as exc:
        await send({"type": "error", "detail": exc.errors()})
        return
    try:
        client: LLMClient
        client, _ = await resolve_client(user_id, payload.provider_id)
    except ProviderUnavailableError as exc:
        await send({"type": "error", "code": "PROVIDER_UNAVAILABLE", "detail": exc.message})
        return
    try:
        rows = await _load_rows(user_id)
    except ReleaseMcpError as exc:
        await send(notice_frame("release", exc.detail))
        return
    async with chat_locks.setdefault(chat_id, asyncio.Lock()):
        build = await build_plan(
            ReleaseMcp(user_id, rows, send),
            ReleaseLlm(client, payload.model),
            chat_id=chat_id,
            user_id=user_id,
            range_spec=cmd.range_spec,
            has_github=rows.github is not None,
        )
        if build.plan is None:
            await send(notice_frame("release", build.notice or MSG_RELEASE_FAILED))
            return
        plan: ReleasePlan = build.plan
        previous = release_plans.get(chat_id)
        if previous is not None:
            previous.stale = True
        release_plans[chat_id] = plan
        message = tag_message(plan.version, plan.title) if plan.version else ""
        await send(plan_frame(plan, message))


async def _run_confirm(send: FrameSender, chat_id: int, user_id: int, cmd: ReleaseCommand) -> None:
    """Confirm phase: run the chat's latest plan."""
    plan = release_plans.get(chat_id)
    if plan is None or plan.user_id != user_id:
        await send(notice_frame("release", MSG_NO_PLAN))
        return
    if cmd.plan_id is not None and cmd.plan_id != plan.plan_id:
        await send(notice_frame("release", MSG_PLAN_STALE))
        return
    try:
        rows = await _load_rows(user_id)
    except ReleaseMcpError as exc:
        await send(notice_frame("release", exc.detail))
        return
    async with chat_locks.setdefault(chat_id, asyncio.Lock()):
        outcome = await confirm_plan(
            plan,
            ReleaseMcp(user_id, rows, send),
            send,
            version=cmd.version,
            title=cmd.title,
            today_iso=datetime.now(timezone.utc).date().isoformat(),
        )
        if outcome == "done" and release_plans.get(chat_id) is plan:
            release_plans.pop(chat_id, None)


async def handle_release(websocket: WebSocket, chat_id: int, user_id: int, raw: Any) -> None:
    """Run /release (plan or confirm); nothing is stored and the model chat flow is not used."""
    cmd = _parse(raw)
    if cmd is None:
        return
    send: FrameSender = websocket.send_json
    try:
        if cmd.kind == "usage":
            await send(notice_frame("release", MSG_USAGE))
        elif cmd.kind == "plan":
            await _run_plan(send, chat_id, user_id, cmd, raw)
        else:
            await _run_confirm(send, chat_id, user_id, cmd)
    except (asyncio.CancelledError, WebSocketDisconnect):
        raise
    except Exception as exc:
        logger.error("release_command_failed", chat_id=chat_id, error=type(exc).__name__)
        await send({"type": "error", "code": RELEASE_ERROR_CODE, "detail": MSG_RELEASE_FAILED})
