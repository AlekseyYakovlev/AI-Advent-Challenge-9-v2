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
MSG_DIM_MISMATCH = "Размерность эмбеддинга не совпадает с индексом базы."


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


class EmbeddingDimMismatchError(KbIndexCorruptError):
    """The query embedding has a different dimension than the stored index."""

    def __init__(self) -> None:
        Exception.__init__(self, MSG_DIM_MISMATCH)
        self.message = MSG_DIM_MISMATCH


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


async def search_kb_vectors(
    session: AsyncSession, kb: KnowledgeBase, query: str, top_k: int
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Return the top_k chunks (with row ids) and the normalised query vector."""
    if kb.status != KbStatus.READY:
        raise KbNotReadyError()
    index = await load_index_cached(kb)
    vector = await embed_query(kb.embedding_model, query, None, kb.query_prefix)
    array = np.asarray([vector], dtype="float32")
    got = int(array.shape[1])
    if got != index.d or (kb.dim is not None and got != kb.dim):
        logger.error("kb_search_dim_mismatch", kb_id=kb.id, expected=index.d, got=got)
        raise EmbeddingDimMismatchError()
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
                "row_id": row.id,
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
    return results, array[0]


async def search_kb(
    session: AsyncSession, kb: KnowledgeBase, query: str, top_k: int
) -> list[dict[str, Any]]:
    """Return the top_k chunks most similar to the query, best first."""
    results, _ = await search_kb_vectors(session, kb, query, top_k)
    return [{key: value for key, value in item.items() if key != "row_id"} for item in results]


def _reconstruct_cosines(
    index: faiss.Index, query_vector: np.ndarray, row_ids: list[int]
) -> dict[int, float]:
    """Dot product of the query with each stored (already normalised) vector."""
    cosines: dict[int, float] = {}
    for row_id in row_ids:
        try:
            stored = index.reconstruct(int(row_id))
        except RuntimeError:
            continue
        cosines[int(row_id)] = round(float(np.dot(stored, query_vector)), 4)
    return cosines


async def cosine_for_ids(
    kb: KnowledgeBase, query_vector: np.ndarray, row_ids: list[int]
) -> dict[int, float]:
    """Cosine of the query against the given chunk row ids, omitting ids absent from the index."""
    if not row_ids:
        return {}
    index = await load_index_cached(kb)
    return await asyncio.to_thread(_reconstruct_cosines, index, query_vector, row_ids)
