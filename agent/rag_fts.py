"""Keyword retrieval over the FTS5 mirror of knowledge-base chunks."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.rag_rank import build_fts_query
from shared.logger import get_logger
from shared.models import KbChunk

logger = get_logger(__name__)

_FTS_SQL = text(
    "SELECT rowid FROM kb_chunk_fts WHERE kb_chunk_fts MATCH :q AND kb_id = :kb "
    "ORDER BY bm25(kb_chunk_fts) LIMIT :n"
)


class FtsQueryError(Exception):
    """SQLite rejected or failed to run the keyword query."""


async def fts_search(
    session: AsyncSession, kb_id: int, question: str, limit: int
) -> list[int]:
    """Return KbChunk ids matching the question's tokens, best bm25 first."""
    match = build_fts_query(question)
    if match is None:
        return []
    try:
        connection = await session.connection()
        rows = await connection.execute(_FTS_SQL, {"q": match, "kb": kb_id, "n": limit})
        ids = [int(row[0]) for row in rows.fetchall()]
    except OperationalError as exc:
        logger.warning("rag_fts_query_failed", kb_id=kb_id, error=type(exc).__name__)
        raise FtsQueryError("keyword search failed") from exc
    unique = list(dict.fromkeys(ids))
    logger.info("rag_fts_search", kb_id=kb_id, hits=len(unique))
    return unique


async def load_chunks_by_ids(
    session: AsyncSession, kb_id: int, row_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Load chunks of this knowledge base by row id, dropping foreign or unknown ids."""
    if not row_ids:
        return {}
    found = await session.exec(
        select(KbChunk).where(KbChunk.id.in_(row_ids), KbChunk.kb_id == kb_id)
    )
    return {
        row.id: {
            "row_id": row.id,
            "chunk_id": row.chunk_id,
            "source": row.source,
            "title": row.title,
            "section": row.section,
            "page": row.page_start,
            "text": row.text,
        }
        for row in found.all()
    }
