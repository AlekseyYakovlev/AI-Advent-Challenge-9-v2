"""Semantic search over a ready knowledge base using a cached FAISS index."""

import asyncio
from typing import Any

import faiss
import numpy as np
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.embeddings import embed_query
from agent.state import kb_index_cache
from shared.kb_storage import index_path, read_index_bytes
from shared.logger import get_logger
from shared.models import KbChunk, KbStatus, KnowledgeBase

logger = get_logger(__name__)

MSG_NOT_READY = "Поиск доступен после завершения индексации"
MSG_INDEX_CORRUPT = "Индекс базы знаний повреждён. Удалите её и создайте заново."


class KbNotReadyError(Exception):
    """Search was requested before the knowledge base finished indexing."""

    def __init__(self) -> None:
        super().__init__(MSG_NOT_READY)
        self.message = MSG_NOT_READY


class KbIndexCorruptError(Exception):
    """The stored index is missing or disagrees with the database."""

    def __init__(self) -> None:
        super().__init__(MSG_INDEX_CORRUPT)
        self.message = MSG_INDEX_CORRUPT


async def load_index_cached(kb: KnowledgeBase) -> faiss.Index:
    """Return the KB's FAISS index, loading and validating it on first use."""
    cached = kb_index_cache.get(kb.id)
    if cached is not None:
        return cached
    try:
        index = await asyncio.to_thread(read_index_bytes, index_path(kb.user_id, kb.id))
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        logger.error("kb_index_load_failed", kb_id=kb.id, error=type(exc).__name__)
        raise KbIndexCorruptError() from exc
    if index.ntotal != kb.chunk_count or index.d != kb.dim:
        logger.error("kb_index_inconsistent", kb_id=kb.id)
        raise KbIndexCorruptError()
    kb_index_cache[kb.id] = index
    return index


async def search_kb(
    session: AsyncSession, kb: KnowledgeBase, query: str, top_k: int
) -> list[dict[str, Any]]:
    """Return the top_k chunks most similar to the query, best first."""
    if kb.status != KbStatus.READY:
        raise KbNotReadyError()
    index = await load_index_cached(kb)
    vector = await embed_query(kb.embedding_model, query, None, kb.query_prefix)
    array = np.asarray([vector], dtype="float32")
    if array.shape[1] != index.d:
        raise KbIndexCorruptError()
    faiss.normalize_L2(array)
    scores, ids = await asyncio.to_thread(index.search, array, top_k)
    ranked = [
        (int(chunk_id), float(score))
        for chunk_id, score in zip(ids[0], scores[0])
        if int(chunk_id) >= 0
    ]
    rows: dict[int, KbChunk] = {}
    if ranked:
        found = await session.exec(
            select(KbChunk).where(
                KbChunk.id.in_([chunk_id for chunk_id, _ in ranked]),
                KbChunk.kb_id == kb.id,
            )
        )
        rows = {row.id: row for row in found.all()}
    results: list[dict[str, Any]] = []
    for chunk_id, score in ranked:
        row = rows.get(chunk_id)
        if row is None:
            continue
        results.append(
            {
                "rank": len(results) + 1,
                "score": round(score, 4),
                "chunk_id": row.chunk_id,
                "source": row.source,
                "title": row.title,
                "section": row.section,
                "page": row.page_start,
                "text": row.text,
            }
        )
    logger.info("kb_search", kb_id=kb.id, top_k=top_k, results=len(results))
    return results
