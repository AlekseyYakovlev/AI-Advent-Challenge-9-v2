"""WebSocket chat endpoint and multi-tab broadcasting."""

import asyncio
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.context_engine import (
    build_system_prompt,
    extract_and_update_facts,
    get_effective_settings,
    summarize_if_needed,
)
from agent.llm_client import llm_client
from agent.state import (
    CORS_ORIGINS,
    active_streams,
    chat_locks,
    ws_rate_limiter,
)
from agent.schemas import MessagePayload
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Chat, Message

logger = get_logger(__name__)

IDLE_TIMEOUT_SECONDS = 300.0
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60.0

active_connections: set[WebSocket] = set()


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


async def _build_chat_messages(
    session: AsyncSession,
    chat: Chat,
) -> list[dict[str, str]]:
    """Walk the active branch and return role/content dicts oldest-first."""
    if chat.current_leaf_message_id is None:
        return []
    path: list[Message] = []
    current_id: int | None = chat.current_leaf_message_id
    while current_id is not None:
        message = await session.get(Message, current_id)
        if message is None or message.chat_id != chat.id:
            break
        path.append(message)
        current_id = message.parent_id
    return [
        {"role": msg.role, "content": msg.content}
        for msg in reversed(path)
    ]


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
            history = await _build_chat_messages(session, chat)
            history = await summarize_if_needed(
                session,
                chat_id,
                history,
                payload.model,
            )
            system_prompt = await build_system_prompt(session, chat_id)
            llm_messages = [
                {"role": "system", "content": system_prompt},
                *history,
            ]
            effective = await get_effective_settings(session, chat_id)
            temperature = effective.temperature
            max_tokens = effective.max_tokens

            assistant_text = ""
            stream_task = asyncio.current_task()
            active_streams[chat_id] = stream_task
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
            finally:
                active_streams.pop(chat_id, None)

            assistant_msg = await _persist_assistant_message(
                session,
                chat,
                user_msg.id,
                assistant_text,
            )
            extract_and_update_facts(
                session,
                chat_id,
                payload.content,
                payload.model,
            )
            await websocket.send_json(
                {
                    "type": "done",
                    "message_id": assistant_msg.id,
                    "user_tokens": user_msg.token_count,
                    "assistant_tokens": assistant_msg.token_count,
                    "total_tokens": user_msg.token_count + assistant_msg.token_count,
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
