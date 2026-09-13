"""In-memory process state shared by REST and WebSocket handlers."""

import asyncio
from typing import Any

CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

active_streams: dict[int, Any] = {}
ws_rate_limiter: dict[int, list[float]] = {}
chat_locks: dict[int, asyncio.Lock] = {}


def cleanup_chat_caches(chat_id: int) -> None:
    """Remove in-memory state keyed by the deleted chat."""
    active_streams.pop(chat_id, None)
    ws_rate_limiter.pop(chat_id, None)
    chat_locks.pop(chat_id, None)
