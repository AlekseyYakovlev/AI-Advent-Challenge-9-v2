"""In-memory process state shared by REST and WebSocket handlers."""

import asyncio
from contextvars import ContextVar
from typing import Any

CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

active_streams: dict[int, Any] = {}
ws_rate_limiter: dict[int, list[float]] = {}
chat_locks: dict[int, asyncio.Lock] = {}
# One in-flight auto-title job per chat (agent/titles.py)
title_tasks: dict[int, asyncio.Task[None]] = {}
# One in-flight indexing job per knowledge base.
kb_jobs: dict[int, asyncio.Task[None]] = {}
# Loaded FAISS indexes keyed by knowledge base id.
kb_index_cache: dict[int, Any] = {}

# Set per chat turn in ws._handle_chat_message so tool handlers know the chat's model;
# it is overwritten on every turn, so nothing needs to clear it.
current_chat_model: ContextVar[str | None] = ContextVar("current_chat_model", default=None)
# Set per chat turn next to current_chat_model so schedule_task stores the chat's provider.
current_chat_provider_id: ContextVar[int | None] = ContextVar(
    "current_chat_provider_id", default=None
)


def cleanup_chat_caches(chat_id: int) -> None:
    """Remove in-memory state keyed by the deleted chat."""
    active_streams.pop(chat_id, None)
    ws_rate_limiter.pop(chat_id, None)
    chat_locks.pop(chat_id, None)
    title_task = title_tasks.pop(chat_id, None)
    if title_task is not None and not title_task.done():
        title_task.cancel()


def cleanup_kb_caches(kb_id: int) -> asyncio.Task[None] | None:
    """Drop in-memory state for a knowledge base and cancel its indexing job.

    Returns the popped job so the caller can await its cancellation.
    """
    kb_index_cache.pop(kb_id, None)
    job = kb_jobs.pop(kb_id, None)
    if job is not None and not job.done():
        job.cancel()
    return job
