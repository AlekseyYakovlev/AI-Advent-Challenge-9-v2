"""Per-user live event hub and the /ws/events WebSocket endpoint."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from agent.dependencies import get_current_user_ws
from agent.schedule import as_aware_utc

# Deliberate reuse: the events socket must apply the exact same Origin policy as /ws/chat.
from agent.ws import _validate_origin
from shared.auth import SESSION_COOKIE_NAME, hash_session_token
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Session as SessionRow

logger = get_logger(__name__)

EVENTS_QUEUE_MAXSIZE = 100
EVENTS_RECEIVE_TIMEOUT_SECONDS = 60.0
EVENTS_SESSION_RECHECK_SECONDS = 60.0

EventFrame = dict[str, Any]


class EventHub:
    """In-memory fan-out of event frames to the open sockets of each user."""

    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[EventFrame]]] = {}

    def subscribe(self, user_id: int) -> asyncio.Queue[EventFrame]:
        """Register and return a new bounded queue for one of the user's sockets."""
        queue: asyncio.Queue[EventFrame] = asyncio.Queue(maxsize=EVENTS_QUEUE_MAXSIZE)
        self._subs.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue[EventFrame]) -> None:
        """Remove a queue and drop the user key once no queues remain."""
        queues = self._subs.get(user_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            del self._subs[user_id]

    def publish(self, user_id: int, frame: EventFrame) -> None:
        """Deliver a frame to every queue of the user without blocking or raising.

        A full queue loses its oldest frame so a slow browser can never stall the
        scheduler or a REST handler.
        """
        for queue in list(self._subs.get(user_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                logger.warning("events_frame_dropped", user_id=user_id)

    def subscriber_count(self, user_id: int) -> int:
        """Return how many sockets of the user are currently subscribed."""
        return len(self._subs.get(user_id, ()))

    def clear(self) -> None:
        """Drop every subscription (tests only)."""
        self._subs.clear()


hub = EventHub()


async def _pump(websocket: WebSocket, queue: asyncio.Queue[EventFrame]) -> None:
    """Forward queued frames to the socket; the single writer serialises sends."""
    try:
        while True:
            frame = await queue.get()
            await websocket.send_json(frame)
    except (WebSocketDisconnect, RuntimeError):
        logger.debug("events_pump_stopped")


async def _session_still_valid(session_id: str, user_id: int) -> bool:
    """Check the session row still exists, is unexpired and belongs to the user.

    Read-only on purpose: unlike the handshake lookup it never slides expires_at, so an
    idle open tab cannot keep a session alive forever.
    """
    try:
        async with async_session_factory() as db:
            result = await db.exec(
                select(SessionRow).where(SessionRow.token_hash == hash_session_token(session_id)),
            )
            row = result.first()
    except SQLAlchemyError as exc:
        # A transient DB error must not disconnect every open socket.
        logger.warning("events_ws_recheck_failed", user_id=user_id, error=str(exc))
        return True
    return (
        row is not None
        and row.user_id == user_id
        and as_aware_utc(row.expires_at) > datetime.now(timezone.utc)
    )


async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
    """Close the socket, ignoring the case where it is already closed."""
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError:
        logger.debug("events_ws_already_closed")


async def ws_events(websocket: WebSocket) -> None:
    """Serve the user-level events socket after origin and session-cookie checks."""
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
    async with async_session_factory() as db:
        user = await get_current_user_ws(session_id, db)
    if user is None:
        logger.warning("events_ws_auth_rejected", reason="no_session")
        await websocket.close(code=1008, reason="Unauthorized")
        return

    user_id: int = user.id
    assert session_id is not None  # get_current_user_ws returns None without a cookie
    await websocket.accept()
    queue = hub.subscribe(user_id)
    pump = asyncio.create_task(_pump(websocket, queue))
    logger.info("events_ws_connected", user_id=user_id)
    last_check = time.monotonic()
    try:
        while not pump.done():
            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=EVENTS_RECEIVE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                message = None
            if message is not None:
                if message["type"] == "websocket.disconnect":
                    break
                if "text" not in message:
                    logger.warning("events_ws_non_text_frame", user_id=user_id)
                    await _safe_close(websocket, 1003, "Text frames only")
                    break
            if time.monotonic() - last_check >= EVENTS_SESSION_RECHECK_SECONDS:
                if not await _session_still_valid(session_id, user_id):
                    logger.warning("events_ws_session_gone", user_id=user_id)
                    await _safe_close(websocket, 1008, "Unauthorized")
                    break
                last_check = time.monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        # Unsubscribe before any await: when this task is cancelled (server shutdown,
        # test client teardown) the awaits below re-raise CancelledError and would
        # otherwise skip the unsubscribe and leak the queue.
        hub.unsubscribe(user_id, queue)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        logger.info("events_ws_disconnected", user_id=user_id)
