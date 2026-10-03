"""Response schema and live-event frames for knowledge bases."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from shared.models import KnowledgeBase


class KbOut(BaseModel):
    """A knowledge base as shown to the client, including live indexing progress."""

    id: int
    name: str
    status: str
    error: str | None
    strategy: str
    chunk_size: int
    chunk_overlap: int
    embedding_model: str
    dim: int | None
    file_count: int
    chunk_count: int
    done: int
    total: int
    phase: str | None
    created_at: datetime


def kb_out(kb: KnowledgeBase) -> KbOut:
    """Build the client view of a knowledge base row."""
    return KbOut(
        id=kb.id,
        name=kb.name,
        status=kb.status.value,
        error=kb.error,
        strategy=kb.strategy.value,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        embedding_model=kb.embedding_model,
        dim=kb.dim,
        file_count=kb.file_count,
        chunk_count=kb.chunk_count,
        done=kb.done_chunks,
        total=kb.total_chunks,
        phase=kb.phase,
        created_at=kb.created_at,
    )


def kb_progress_frame(kb: KnowledgeBase) -> dict[str, Any]:
    """Event frame announcing a knowledge base status or progress change."""
    return {"type": "kb_progress", "kb": kb_out(kb).model_dump(mode="json")}


def kb_deleted_frame(kb_id: int) -> dict[str, Any]:
    """Event frame announcing that a knowledge base was deleted."""
    return {"type": "kb_deleted", "kb_id": kb_id}
