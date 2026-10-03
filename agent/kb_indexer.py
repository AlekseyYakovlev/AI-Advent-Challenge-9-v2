"""Background knowledge-base indexing: parse, chunk, embed and build the FAISS index."""

import asyncio
import contextlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.embeddings import (
    EmbeddingError,
    embed_passages,
    ensure_embedding_model,
    prefixes_for,
)
from agent.events import hub
from agent.kb_chunking import ChunkDraft, chunk_fixed, chunk_structural
from agent.kb_limits import EMBED_BATCH_SIZE, PROGRESS_THROTTLE_SECONDS
from agent.kb_loaders import MSG_EMPTY, KbLoadError, load_document
from agent.kb_schemas import kb_deleted_frame, kb_progress_frame
from agent.state import cleanup_kb_caches, kb_jobs
from shared.database import async_session_factory
from shared.kb_storage import (
    build_index,
    index_path,
    remove_index_file,
    remove_kb_dir,
    uploads_dir,
    write_index_bytes,
)
from shared.logger import get_logger
from shared.models import (
    KbChunk,
    KbDocument,
    KbStatus,
    KbStrategy,
    KnowledgeBase,
)

logger = get_logger(__name__)

MSG_INTERRUPTED_RESTART = "Индексация прервана перезапуском агента. Удалите базу и создайте её заново."
MSG_INTERRUPTED = "Индексация прервана."
MSG_UNEXPECTED = "Не удалось проиндексировать базу знаний. Подробности в журнале агента."

PHASE_LOADING_MODEL = "loading_model"
PHASE_PARSING = "parsing"
PHASE_EMBEDDING = "embedding"

CHUNK_INSERT_SLICE = 500
DELETE_JOB_TIMEOUT_SECONDS = 10.0

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return the single-slot indexing semaphore, created on first use in the running loop."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(1)
    return _semaphore


def reset_state() -> None:
    """Drop the semaphore so the next job builds one bound to its own event loop."""
    global _semaphore
    _semaphore = None


def spawn_index_job(kb_id: int, user_id: int) -> None:
    """Start run_index_job in the background, keeping a strong reference until it ends."""
    task = asyncio.create_task(run_index_job(kb_id, user_id), name=f"kb-index-{kb_id}")
    kb_jobs[kb_id] = task

    def _forget(done: asyncio.Task[None]) -> None:
        if kb_jobs.get(kb_id) is done:
            kb_jobs.pop(kb_id, None)

    task.add_done_callback(_forget)


async def _update_kb(kb_id: int, *, publish: bool = True, **values: Any) -> bool:
    """Apply column values to a knowledge base row and announce it; False if it is gone."""
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None:
            return False
        for key, value in values.items():
            setattr(kb, key, value)
        kb.updated_at = datetime.now(timezone.utc)
        session.add(kb)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await session.refresh(kb)
        if publish:
            hub.publish(kb.user_id, kb_progress_frame(kb))
    return True


def _chunk_document(
    kb: KnowledgeBase, path: Path, filename: str
) -> tuple[list[ChunkDraft], int | None]:
    """Load one stored file and split it per the KB strategy (runs in a worker thread)."""
    loaded = load_document(path, filename)
    if kb.strategy == KbStrategy.STRUCTURAL:
        drafts = chunk_structural(
            loaded.text,
            doc_title=loaded.title,
            is_markdown=loaded.is_markdown,
            page_offsets=loaded.page_offsets,
        )
    else:
        drafts = chunk_fixed(loaded.text, kb.chunk_size, kb.chunk_overlap, loaded.page_offsets)
    if not drafts:
        raise KbLoadError(MSG_EMPTY.format(name=filename))
    return drafts, loaded.page_count


def _build_and_write_index(
    user_id: int, kb_id: int, vectors: list[list[float]], ids: list[int]
) -> int:
    """Build the FAISS index from vectors keyed by chunk ids and persist it (worker thread)."""
    matrix = np.asarray(vectors, dtype="float32")
    index = build_index(matrix, ids)
    write_index_bytes(index_path(user_id, kb_id), index)
    return int(index.ntotal)


async def _insert_chunks(
    kb_id: int, docs: list[KbDocument], drafts_by_doc: list[list[ChunkDraft]]
) -> list[tuple[int, str]]:
    """Persist every chunk so each has its id before FAISS; returns (id, text) pairs in order."""
    pairs: list[tuple[int, str]] = []
    async with async_session_factory() as session:
        try:
            for doc, drafts in zip(docs, drafts_by_doc):
                rows = [
                    KbChunk(
                        kb_id=kb_id,
                        document_id=doc.id,
                        chunk_index=i,
                        chunk_id=f"{doc.id}-{i}",
                        text=draft.text,
                        section=draft.section,
                        source=doc.filename,
                        title=Path(doc.filename).stem,
                        page_start=draft.page_start,
                        char_start=draft.char_start,
                        char_end=draft.char_end,
                    )
                    for i, draft in enumerate(drafts)
                ]
                for start in range(0, len(rows), CHUNK_INSERT_SLICE):
                    batch = rows[start : start + CHUNK_INSERT_SLICE]
                    session.add_all(batch)
                    await session.flush()
                    pairs.extend((row.id, row.text) for row in batch)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return pairs


async def _parse_documents(
    kb: KnowledgeBase, user_id: int
) -> tuple[list[KbDocument], list[list[ChunkDraft]]]:
    """Parse and chunk every document of the KB, recording page counts."""
    async with async_session_factory() as session:
        docs = list(
            (
                await session.exec(
                    select(KbDocument).where(KbDocument.kb_id == kb.id).order_by(KbDocument.id)
                )
            ).all()
        )
    all_drafts: list[list[ChunkDraft]] = []
    page_counts: dict[int, int | None] = {}
    for doc in docs:
        drafts, page_count = await asyncio.to_thread(
            _chunk_document, kb, uploads_dir(user_id, kb.id) / doc.stored_name, doc.filename
        )
        page_counts[doc.id] = page_count
        all_drafts.append(drafts)
    async with async_session_factory() as session:
        try:
            for doc in docs:
                row = await session.get(KbDocument, doc.id)
                if row is not None:
                    row.page_count = page_counts[doc.id]
                    session.add(row)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return docs, all_drafts


async def _embed_all(kb_id: int, model_id: str, pairs: list[tuple[int, str]]) -> list[list[float]]:
    """Embed chunk texts in sequential batches, publishing throttled progress."""
    vectors: list[list[float]] = []
    last_publish = 0.0
    total = len(pairs)
    for start in range(0, total, EMBED_BATCH_SIZE):
        batch = pairs[start : start + EMBED_BATCH_SIZE]
        vectors.extend(await embed_passages(model_id, [text for _, text in batch]))
        done = len(vectors)
        now = time.monotonic()
        send = done >= total or now - last_publish >= PROGRESS_THROTTLE_SECONDS
        if send:
            last_publish = now
        await _update_kb(kb_id, publish=send, done_chunks=done)
    return vectors


async def run_index_job(kb_id: int, user_id: int) -> None:
    """Index one knowledge base end to end; any failure leaves it failed with no chunks."""
    async with _get_semaphore():
        started = time.monotonic()
        try:
            async with async_session_factory() as session:
                kb = await session.get(KnowledgeBase, kb_id)
                if kb is None:
                    return
                session.expunge(kb)
            logger.info("kb_index_started", kb_id=kb_id, user_id=user_id)
            await _update_kb(kb_id, status=KbStatus.INDEXING, phase=PHASE_LOADING_MODEL, error=None)
            await ensure_embedding_model(kb.embedding_model)
            await _update_kb(kb_id, phase=PHASE_PARSING)
            docs, drafts_by_doc = await _parse_documents(kb, user_id)
            pairs = await _insert_chunks(kb_id, docs, drafts_by_doc)
            await _update_kb(kb_id, phase=PHASE_EMBEDDING, total_chunks=len(pairs), done_chunks=0)
            vectors = await _embed_all(kb_id, kb.embedding_model, pairs)
            ids = [chunk_id for chunk_id, _ in pairs]
            ntotal = await asyncio.to_thread(_build_and_write_index, user_id, kb_id, vectors, ids)
            if ntotal != len(ids):
                raise RuntimeError("index size does not match chunk count")
            query_prefix, doc_prefix = prefixes_for(kb.embedding_model)
            await _update_kb(
                kb_id,
                status=KbStatus.READY,
                phase=None,
                error=None,
                dim=len(vectors[0]),
                chunk_count=len(ids),
                done_chunks=len(ids),
                total_chunks=len(ids),
                query_prefix=query_prefix,
                doc_prefix=doc_prefix,
            )
            logger.info(
                "kb_index_ready",
                kb_id=kb_id,
                user_id=user_id,
                chunk_count=len(ids),
                seconds=round(time.monotonic() - started, 2),
            )
        except (KbLoadError, EmbeddingError) as exc:
            logger.error("kb_index_failed", kb_id=kb_id, user_id=user_id, error=type(exc).__name__)
            await _fail(kb_id, user_id, exc.message)
        except asyncio.CancelledError:
            await _fail(kb_id, user_id, MSG_INTERRUPTED)
            raise
        except Exception as exc:
            logger.error("kb_index_failed", kb_id=kb_id, user_id=user_id, error=type(exc).__name__)
            await _fail(kb_id, user_id, MSG_UNEXPECTED)


async def _fail(kb_id: int, user_id: int, message: str) -> None:
    """Mark a KB failed, discarding all of its chunks and its index file (all-or-nothing)."""
    try:
        async with async_session_factory() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None:
                return
            try:
                await session.exec(delete(KbChunk).where(KbChunk.kb_id == kb_id))
                kb.status = KbStatus.FAILED
                kb.error = message
                kb.phase = None
                kb.done_chunks = 0
                kb.total_chunks = 0
                kb.chunk_count = 0
                kb.updated_at = datetime.now(timezone.utc)
                session.add(kb)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            await session.refresh(kb)
            frame = kb_progress_frame(kb)
        await asyncio.to_thread(remove_index_file, user_id, kb_id)
        hub.publish(user_id, frame)
    except Exception as exc:
        logger.error("kb_fail_record_failed", kb_id=kb_id, error=type(exc).__name__)


async def delete_kb(session: AsyncSession, kb: KnowledgeBase) -> None:
    """Cancel the KB's job, then remove its rows, files and caches and tell the owner."""
    kb_id, user_id = kb.id, kb.user_id
    job = cleanup_kb_caches(kb_id)
    if job is not None:
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(job, timeout=DELETE_JOB_TIMEOUT_SECONDS)
    try:
        await session.exec(delete(KbChunk).where(KbChunk.kb_id == kb_id))
        await session.exec(delete(KbDocument).where(KbDocument.kb_id == kb_id))
        await session.exec(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await asyncio.to_thread(remove_kb_dir, user_id, kb_id)
    if job is not None and not job.done():
        # Job outlived the timeout: sweep again once it really finishes so a late
        # index write cannot resurrect files for the deleted KB.
        job.add_done_callback(lambda _task: remove_kb_dir(user_id, kb_id))
    hub.publish(user_id, kb_deleted_frame(kb_id))
    logger.info("kb_deleted", kb_id=kb_id, user_id=user_id)


async def recover_orphaned_kb_jobs() -> int:
    """Fail knowledge bases left queued/indexing by a dead process."""
    active = [KbStatus.QUEUED, KbStatus.INDEXING]
    async with async_session_factory() as session:
        try:
            rows = list(
                (
                    await session.exec(
                        select(KnowledgeBase.id, KnowledgeBase.user_id).where(
                            KnowledgeBase.status.in_(active)
                        )
                    )
                ).all()
            )
            ids = [row[0] for row in rows]
            if ids:
                await session.exec(delete(KbChunk).where(KbChunk.kb_id.in_(ids)))
                await session.exec(
                    update(KnowledgeBase)
                    .where(KnowledgeBase.id.in_(ids))
                    .values(
                        status=KbStatus.FAILED,
                        error=MSG_INTERRUPTED_RESTART,
                        phase=None,
                        done_chunks=0,
                        total_chunks=0,
                        chunk_count=0,
                    )
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    for kb_id, user_id in rows:
        await asyncio.to_thread(remove_index_file, user_id, kb_id)
    logger.info("kb_recovered_orphans", count=len(rows))
    return len(rows)


async def shutdown_kb_jobs() -> None:
    """Cancel and await every running indexing job."""
    tasks = list(kb_jobs.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    kb_jobs.clear()
