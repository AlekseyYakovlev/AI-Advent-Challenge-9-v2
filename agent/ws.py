"""WebSocket chat endpoint and multi-tab broadcasting."""

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.context_engine import (
    ContextOverflowError,
    build_llm_context,
    compute_chat_stats,
    extract_and_update_facts,
    get_effective_settings,
)
from agent.dependencies import get_current_user_ws
from agent import invariants, tasks
from agent.llm_client import llm_client
from agent.mcp_tools import McpToolset, build_mcp_toolset
from agent.state import (
    CORS_ORIGINS,
    active_streams,
    chat_locks,
    ws_rate_limiter,
)
from agent.schemas import (
    TOOL_EVENT_ARGS_PREVIEW_CHARS,
    TOOL_EVENT_PREVIEW_CHARS,
    MessagePayload,
    ToolCallEvent,
)
from agent.tools import TOOL_REGISTRY, build_tool_schemas, dispatch_tool_calls
from shared.auth import SESSION_COOKIE_NAME
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Chat, Message

logger = get_logger(__name__)

IDLE_TIMEOUT_SECONDS = 300.0
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60.0
TASK_TOOL_NAMES = ("create_task", "transition_task", "pause_task", "resume_task")

active_connections: set[WebSocket] = set()


def _normalize_tool_calls_for_echo(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy tool calls for the follow-up request, replacing empty arguments with "{}".

    Providers reject an empty string as function arguments, and a no-argument call
    can legitimately arrive that way. The originals are left untouched.
    """
    normalized: list[dict[str, Any]] = []
    for call in tool_calls:
        call_copy = dict(call)
        function = dict(call_copy.get("function") or {})
        arguments = function.get("arguments")
        if not isinstance(arguments, str) or not arguments.strip():
            function["arguments"] = "{}"
        call_copy["function"] = function
        normalized.append(call_copy)
    return normalized


def _preview(text: str, limit: int) -> tuple[str, bool]:
    """Cut text to the limit and report whether it was cut."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _tool_call_frame(result: dict[str, Any]) -> dict[str, Any]:
    """Build the tool_call WebSocket frame for one dispatch result."""
    mcp_info = result.get("mcp")
    arguments, _ = _preview(result.get("arguments", ""), TOOL_EVENT_ARGS_PREVIEW_CHARS)
    preview, cut = _preview(
        result.get("result_text", result["content"]),
        TOOL_EVENT_PREVIEW_CHARS,
    )
    return ToolCallEvent(
        tool_call_id=result.get("tool_call_id"),
        name=result.get("name"),
        server=mcp_info["server_name"] if mcp_info else None,
        tool=mcp_info["tool"] if mcp_info else result.get("name"),
        arguments=arguments,
        ok=result["ok"],
        result=preview,
        truncated=bool(result.get("truncated")) or cut,
    ).model_dump()


async def _load_mcp_toolset(session: AsyncSession, user_id: int, chat_id: int) -> McpToolset:
    """Build the per-turn MCP toolset; an MCP problem must never break the chat turn."""
    try:
        return await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
    except Exception as exc:
        logger.warning("mcp_toolset_failed", chat_id=chat_id, error=str(exc))
        return McpToolset.empty()


def _validate_origin(websocket: WebSocket) -> bool:
    """Return True when the request Origin header is allowed or missing (single-user mode)."""
    origin = websocket.headers.get("origin")
    logger.info("ws_origin_check", origin=origin or "None")

    if origin is None:
        logger.info("ws_origin_allowed", reason="no_origin")
        return True

    if origin in CORS_ORIGINS:
        logger.info("ws_origin_allowed", reason="cors_origins", origin=origin)
        return True

    if origin == "null":
        logger.info("ws_origin_allowed", reason="null_origin")
        return True

    allowed_patterns = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]
    if origin in allowed_patterns:
        logger.info("ws_origin_allowed", reason="allowed_pattern", origin=origin)
        return True

    logger.warning("ws_origin_rejected", origin=origin)
    return False


async def _user_owns_chat(db: AsyncSession, user_id: int, chat_id: int) -> bool:
    """Return True when chat_id exists and belongs to user_id."""
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        logger.warning("ws_auth_rejected", chat_id=chat_id, reason="not_owner")
        return False
    return True


def _check_rate_limit(chat_id: int) -> bool:
    """Allow up to RATE_LIMIT_MAX messages per chat per minute."""
    now = time.monotonic()
    timestamps = ws_rate_limiter.setdefault(chat_id, [])
    ws_rate_limiter[chat_id] = [
        ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW
    ]
    if len(ws_rate_limiter[chat_id]) >= RATE_LIMIT_MAX:
        return False
    ws_rate_limiter[chat_id].append(now)
    return True


async def _persist_user_message(
    session: AsyncSession,
    chat: Chat,
    content: str,
) -> Message:
    """Insert a user message and advance the chat leaf."""
    user_msg = Message(
        chat_id=chat.id,
        parent_id=chat.current_leaf_message_id,
        role="user",
        content=content,
        token_count=llm_client.count_tokens(content),
    )
    session.add(user_msg)
    await session.flush()
    chat.current_leaf_message_id = user_msg.id
    session.add(chat)
    await session.commit()
    await session.refresh(user_msg)
    return user_msg


async def _persist_assistant_message(
    session: AsyncSession,
    chat: Chat,
    parent_id: int,
    content: str,
) -> Message:
    """Insert an assistant message and advance the chat leaf."""
    assistant_msg = Message(
        chat_id=chat.id,
        parent_id=parent_id,
        role="assistant",
        content=content,
        token_count=llm_client.count_tokens(content),
    )
    session.add(assistant_msg)
    await session.flush()
    chat.current_leaf_message_id = assistant_msg.id
    session.add(chat)
    await session.commit()
    await session.refresh(assistant_msg)
    return assistant_msg


async def _handle_chat_message(
    websocket: WebSocket,
    chat_id: int,
    payload: MessagePayload,
) -> None:
    """Process one inbound chat message under the per-chat lock."""
    if chat_id not in chat_locks:
        chat_locks[chat_id] = asyncio.Lock()
    async with chat_locks[chat_id]:
        async with async_session_factory() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None:
                await websocket.send_json(
                    {"type": "error", "detail": f"Chat {chat_id} not found"},
                )
                return

            user_msg = await _persist_user_message(
                session,
                chat,
                payload.content,
            )

            effective = await get_effective_settings(session, chat_id)
            logger.info(
                "strategy_selected",
                chat_id=chat_id,
                strategy=effective.strategy,
            )

            try:
                llm_messages = await build_llm_context(
                    session,
                    chat_id,
                    payload.model,
                )
            except ContextOverflowError as exc:
                logger.error(
                    "context_overflow_blocked",
                    chat_id=chat_id,
                    error=str(exc),
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": str(exc),
                        "code": "CONTEXT_OVERFLOW",
                        "suggested_strategy": "sliding",
                    },
                )
                await session.delete(user_msg)
                await session.commit()
                return

            temperature = effective.temperature
            max_tokens = effective.max_tokens

            toolset = McpToolset.empty()
            if chat.user_id is not None:
                toolset = await _load_mcp_toolset(session, chat.user_id, chat_id)
                tool_schemas = build_tool_schemas() + toolset.schemas
            else:
                tool_schemas = None
                logger.warning("tools_disabled_unowned_chat", chat_id=chat_id)

            assistant_text = ""
            pending_tool_calls: list[dict[str, Any]] = []
            stream_task = asyncio.current_task()
            active_streams[chat_id] = stream_task

            try:
                if tool_schemas:
                    async for event in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                        tools=tool_schemas,
                    ):
                        if event["type"] == "content":
                            assistant_text += event["content"]
                            await websocket.send_json(
                                {"type": "token", "content": event["content"]},
                            )
                        elif event["type"] == "tool_calls":
                            pending_tool_calls = event["tool_calls"]
                else:
                    async for token in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                    ):
                        assistant_text += token
                        await websocket.send_json(
                            {"type": "token", "content": token},
                        )
            except Exception as exc:
                logger.error(
                    "llm_stream_failed",
                    chat_id=chat_id,
                    error=str(exc),
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": f"LLM error: {str(exc)}",
                        "code": "LLM_ERROR",
                    },
                )
                await session.delete(user_msg)
                await session.commit()
                return
            finally:
                active_streams.pop(chat_id, None)

            memory_writes: list[dict[str, Any]] = []
            task_writes: list[dict[str, Any]] = []
            if pending_tool_calls:
                tool_results = await dispatch_tool_calls(
                    session,
                    chat.user_id,
                    chat_id,
                    pending_tool_calls,
                    mcp_bindings=toolset.bindings,
                )
                for result in tool_results:
                    await websocket.send_json(_tool_call_frame(result))

                llm_messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_text,
                        "tool_calls": _normalize_tool_calls_for_echo(pending_tool_calls),
                    },
                )
                for result in tool_results:
                    llm_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result["tool_call_id"],
                            "content": result["content"],
                        },
                    )

                try:
                    async for token in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                    ):
                        assistant_text += token
                        await websocket.send_json(
                            {"type": "token", "content": token},
                        )
                except Exception as exc:
                    logger.error(
                        "llm_stream_failed",
                        chat_id=chat_id,
                        error=str(exc),
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": f"LLM error: {str(exc)}",
                            "code": "LLM_ERROR",
                        },
                    )
                    await session.delete(user_msg)
                    await session.commit()
                    return

                memory_writes = [
                    r["write"]
                    for r in tool_results
                    if r["ok"] and r["write"] is not None and r["name"] not in TASK_TOOL_NAMES
                ]

                for result in tool_results:
                    if result["ok"] and result["name"] in TASK_TOOL_NAMES:
                        try:
                            task_result = json.loads(result["content"])
                        except json.JSONDecodeError:
                            continue
                        task_writes.append(
                            {
                                "id": task_result.get("id"),
                                "title": task_result.get("title"),
                                "state": task_result.get("state"),
                            },
                        )

                for result in tool_results:
                    # MCP failures are shown on their tool_call card; an error frame would
                    # make the client stop streaming mid-turn.
                    if not result["ok"] and result.get("mcp") is None:
                        try:
                            error_detail = json.loads(result["content"]).get(
                                "error", result["content"],
                            )
                        except json.JSONDecodeError:
                            error_detail = result["content"]
                        await websocket.send_json(
                            {
                                "type": "error",
                                "detail": error_detail,
                                "code": "TOOL_ERROR",
                            },
                        )

                rejected_transitions: list[dict[str, Any]] = []
                for result in tool_results:
                    if result["ok"] or result.get("name") != "transition_task":
                        continue
                    try:
                        parsed = json.loads(result["content"])
                    except json.JSONDecodeError:
                        continue
                    if parsed.get("code") != "illegal_transition":
                        continue
                    rejected_transitions.append(parsed)

                if rejected_transitions:
                    llm_messages.append(
                        {
                            "role": "user",
                            "content": tasks.build_transition_illegal_prompt(
                                rejected_transitions,
                            ),
                        },
                    )
                    try:
                        async for token in llm_client.stream_chat(
                            llm_messages,
                            payload.model,
                            temperature,
                            max_tokens,
                        ):
                            assistant_text += token
                            await websocket.send_json(
                                {"type": "token", "content": token},
                            )
                    except Exception as exc:
                        logger.warning(
                            "transition_illegal_reprompt_failed",
                            chat_id=chat_id,
                            error=str(exc),
                        )

            active_invariants = await invariants.resolve_active_invariants(session, chat_id)
            flagged: dict[str, Any] | None = None
            justification_text = ""
            critique: dict[str, Any] = {"conflict": False}
            if active_invariants:
                critique = await invariants.run_self_critique(
                    active_invariants,
                    assistant_text,
                    pending_tool_calls,
                    payload.model,
                )
                flagged = invariants.match_flagged_invariant(active_invariants, critique)

            if flagged is not None:
                llm_messages.append(
                    {
                        "role": "user",
                        "content": invariants.build_justify_retract_prompt(flagged, critique),
                    },
                )
                try:
                    async for token in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                    ):
                        justification_text += token
                        assistant_text += token
                        await websocket.send_json(
                            {"type": "token", "content": token},
                        )
                except Exception as exc:
                    logger.warning(
                        "invariant_justify_retract_failed",
                        chat_id=chat_id,
                        error=str(exc),
                    )

            assistant_msg = await _persist_assistant_message(
                session,
                chat,
                user_msg.id,
                assistant_text,
            )

            conflict_payload: dict[str, Any] | None = None
            if flagged is not None:
                conflict_row = await invariants.record_conflict(
                    session,
                    chat_id,
                    assistant_msg.id,
                    flagged["scope"],
                    flagged["id"],
                    flagged["title"],
                    justification_text or critique.get("explanation", ""),
                )
                conflict_payload = {
                    "id": conflict_row.id,
                    "chat_id": conflict_row.chat_id,
                    "message_id": conflict_row.message_id,
                    "invariant_scope": conflict_row.invariant_scope,
                    "invariant_id": conflict_row.invariant_id,
                    "invariant_title": conflict_row.invariant_title,
                    "note": conflict_row.note,
                    "created_at": conflict_row.created_at.isoformat(),
                }

            extract_and_update_facts(
                session,
                chat_id,
                payload.content,
                payload.model,
            )
            stats = await compute_chat_stats(session, chat_id, payload.model)
            await websocket.send_json(
                {
                    "type": "done",
                    "message_id": assistant_msg.id,
                    "stats": stats,
                    "memory_writes": memory_writes,
                    "task_writes": task_writes,
                    "invariant_conflict": conflict_payload,
                },
            )


async def broadcast_model_event(model_id: str, event_type: str) -> None:
    """Broadcast a model lifecycle event to every active WebSocket."""
    payload = {
        "type": "model_event",
        "model_id": model_id,
        "event": event_type,
    }
    for connection in list(active_connections):
        try:
            await connection.send_json(payload)
        except WebSocketDisconnect:
            active_connections.discard(connection)
        except Exception as exc:
            logger.warning("broadcast_failed", error=str(exc))
            active_connections.discard(connection)


async def ws_chat(websocket: WebSocket, chat_id: int) -> None:
    """Handle streaming chat over WebSocket with security guards."""
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
    async with async_session_factory() as db:
        user = await get_current_user_ws(session_id, db)
        if user is None:
            logger.warning("ws_auth_rejected", chat_id=chat_id, reason="no_session")
            await websocket.close(code=1008, reason="Unauthorized")
            return

        if not await _user_owns_chat(db, user.id, chat_id):
            await websocket.close(code=1008, reason="Unauthorized")
            return

    await websocket.accept()
    active_connections.add(websocket)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="Idle timeout")
                break

            if not _check_rate_limit(chat_id):
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": "Rate limit exceeded",
                    },
                )
                continue

            try:
                payload = MessagePayload.model_validate(raw)
            except ValidationError as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": exc.errors(),
                    },
                )
                continue

            await _handle_chat_message(websocket, chat_id, payload)
    except WebSocketDisconnect:
        logger.info("ws_disconnected", chat_id=chat_id)
    finally:
        active_connections.discard(websocket)


def register_websocket_routes(app: Any) -> None:
    """Attach the chat WebSocket route to the FastAPI app."""
    app.websocket("/ws/chat/{chat_id}")(ws_chat)
