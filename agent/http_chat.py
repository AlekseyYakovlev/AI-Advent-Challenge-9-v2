"""Plain-HTTP chat endpoint that runs the WebSocket chat turn and returns one JSON body."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import ws
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.events import hub
from agent.headless import RecordingSink
from agent.providers import KIND_LM_STUDIO
from agent.rag_api import _get_owned_chat, apply_chat_kb
from agent.schemas import HttpChatRequest, HttpChatResponse, MessagePayload
from agent.state import chat_locks
from shared.database import async_session_factory, get_session
from shared.logger import get_logger
from shared.models import Message, User

logger = get_logger(__name__)

router = APIRouter(tags=["http-chat"])

CODE_CHAT_BUSY = "CHAT_BUSY"
CODE_LM_STUDIO_UNAVAILABLE = "LM_STUDIO_UNAVAILABLE"
CODE_LLM_UNREACHABLE = "LLM_UNREACHABLE"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
MSG_CHAT_BUSY = "Chat is busy with another request; retry when the current turn has finished"
MSG_LM_STUDIO_DOWN = "LM Studio is not running"
MSG_INTERNAL = "Internal error while processing the message"
EVENT_CHAT_UPDATED = "chat_updated"

# Strong references: the event loop keeps only weak ones to running tasks.
_http_turns: set[asyncio.Task[Any]] = set()


def _error_response(
    status_code: int,
    detail: str,
    code: str,
    user_message_kept: bool | None,
    **extra: Any,
) -> JSONResponse:
    """Build the uniform error body; a JSONResponse keeps `code` that HTTPException drops."""
    content: dict[str, Any] = {
        "detail": detail,
        "code": code,
        "user_message_kept": user_message_kept,
        **extra,
    }
    return JSONResponse(status_code=status_code, content=content)


def _internal_error() -> JSONResponse:
    """Build the fixed 500 response that never leaks exception text."""
    return _error_response(500, MSG_INTERNAL, CODE_INTERNAL_ERROR, None)


def _map_error_frame(frame: dict[str, Any]) -> JSONResponse:
    """Translate the terminal error frame of a failed turn into an HTTP response."""
    code = frame.get("code")
    detail = str(frame.get("detail", ""))
    if code == "CONTEXT_OVERFLOW":
        return _error_response(
            413, detail, "CONTEXT_OVERFLOW", False,
            suggested_strategy=frame.get("suggested_strategy"),
        )
    if code == "PROVIDER_UNAVAILABLE":
        return _error_response(503, detail, "PROVIDER_UNAVAILABLE", False)
    if code == "LLM_ERROR":
        if frame.get("cause") == "unreachable":
            if frame.get("provider_kind") == KIND_LM_STUDIO:
                return _error_response(
                    503, MSG_LM_STUDIO_DOWN, CODE_LM_STUDIO_UNAVAILABLE, False,
                )
            return _error_response(503, detail, CODE_LLM_UNREACHABLE, False)
        return _error_response(502, detail, "LLM_ERROR", False)
    if code == "RAG_GATED_FAILED":
        return _error_response(500, detail, "RAG_GATED_FAILED", False)
    if code is None and detail.endswith("not found"):
        return JSONResponse(status_code=404, content={"detail": detail})
    return _internal_error()


async def _build_success(
    chat_id: int,
    body: HttpChatRequest,
    done: dict[str, Any],
    sink: RecordingSink,
) -> HttpChatResponse | None:
    """Assemble the response from the stored assistant message and the done frame."""
    async with async_session_factory() as session:
        assistant = await session.get(Message, done["message_id"])
    if assistant is None or assistant.parent_id is None:
        return None
    return HttpChatResponse(
        chat_id=chat_id,
        user_message_id=assistant.parent_id,
        assistant_message_id=assistant.id,
        content=assistant.content,
        model=body.model,
        provider_id=body.provider_id,
        stats=done.get("stats") or {},
        tool_calls=[f for f in sink.frames if f.get("type") == "tool_call"],
        memory_writes=done.get("memory_writes") or [],
        task_writes=done.get("task_writes") or [],
        invariant_conflict=done.get("invariant_conflict"),
        rag=done.get("rag"),
    )


async def _run_http_turn(
    chat_id: int,
    user_id: int,
    body: HttpChatRequest,
    kb_given: bool,
    lock: asyncio.Lock,
) -> JSONResponse | HttpChatResponse:
    """Run the turn while owning the already-acquired chat lock and release it at the end."""
    try:
        if kb_given:
            try:
                async with async_session_factory() as session:
                    await apply_chat_kb(session, chat_id, user_id, body.kb_id)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail},
                )
        sink = RecordingSink()
        payload = MessagePayload(
            content=body.content, model=body.model, provider_id=body.provider_id,
        )
        try:
            await ws._run_chat_turn(sink, chat_id, payload)
        except Exception as exc:
            logger.error("http_chat_failed", chat_id=chat_id, error=str(exc))
            return _internal_error()
        done = next((f for f in sink.frames if f.get("type") == "done"), None)
        if done is None:
            errors = [
                f for f in sink.frames
                if f.get("type") == "error" and f.get("code") != "TOOL_ERROR"
            ]
            return _map_error_frame(errors[-1]) if errors else _internal_error()
        try:
            response = await _build_success(chat_id, body, done, sink)
        except Exception as exc:
            logger.error("http_chat_failed", chat_id=chat_id, error=str(exc))
            return _internal_error()
        if response is None:
            return _internal_error()
        hub.publish(user_id, {"type": EVENT_CHAT_UPDATED, "chat_id": chat_id})
        return response
    finally:
        lock.release()


@router.post(
    "/api/v1/chats/{chat_id}/messages",
    response_model=HttpChatResponse,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def post_chat_message(
    chat_id: int,
    body: HttpChatRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Run one chat turn for the key holder and return the whole reply as JSON."""
    await _get_owned_chat(session, chat_id, current_user.id)
    kb_selection = "omitted"
    if "kb_id" in body.model_fields_set:
        kb_selection = "null" if body.kb_id is None else "id"
    logger.info(
        "http_chat_started",
        chat_id=chat_id,
        user_id=current_user.id,
        model=body.model,
        provider_id=body.provider_id,
        kb_selection=kb_selection,
    )
    lock = chat_locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        return _error_response(409, MSG_CHAT_BUSY, CODE_CHAT_BUSY, False)
    await lock.acquire()
    task = asyncio.create_task(
        _run_http_turn(chat_id, current_user.id, body, "kb_id" in body.model_fields_set, lock)
    )
    _http_turns.add(task)
    task.add_done_callback(_http_turns.discard)
    result = await asyncio.shield(task)
    status_code = result.status_code if isinstance(result, JSONResponse) else 200
    logger.info("http_chat_finished", chat_id=chat_id, status=status_code)
    return result
