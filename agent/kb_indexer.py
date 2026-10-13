"""Background knowledge-base indexing: parse, chunk, embed and build the FAISS index."""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, func, update
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
from agent.kb_limits import EMBED_BATCH_SIZE, PHASE_ADDING, PROGRESS_THROTTLE_SECONDS
from agent.kb_loaders import MSG_EMPTY, KbLoadError, load_document
from agent.kb_schemas import kb_deleted_frame, kb_progress_frame
from agent.state import cleanup_kb_caches, kb_index_cache, kb_jobs
from shared.database import async_session_factory
from shared.kb_storage import (
    build_index,
    index_path,
    patch_index,
    promote_index,
    remove_index_file,
    remove_kb_dir,
    remove_tree,
    staging_root,
    uploads_dir,
    write_index_bytes,
    write_index_pending,
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

MSG_ADD_UNEXPECTED = "Не удалось добавить файлы. Подробности в журнале агента."
MSG_ADD_INTERRUPTED = "Добавление файлов прервано."
MSG_ADD_INTERRUPTED_RESTART = (
    "Добавление файлов прервано перезапуском агента. Повторите добавление."
)
MSG_ADD_DIM_MISMATCH = "Модель эмбеддингов вернула векторы другой размерности, чем у базы."

PHASE_LOADING_MODEL = "loading_model"
PHASE_PARSING = "parsing"
PHASE_EMBEDDING = "embedding"

CHUNK_INSERT_SLICE = 500
DELETE_JOB_TIMEOUT_SECONDS = 10.0
ADD_COMMIT_WARN_SECONDS: float = 2.0

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


class KbAddError(Exception):
    """An addition cannot be completed; carries a user-facing message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _PostCommitError(Exception):
    """The addition was committed to the database but the index swap failed."""


@dataclass(frozen=True)
class AddItem:
    """One staged file of an addition and the existing documents it supersedes."""

    source: str
    stored_name: str
    sha256: str
    size_bytes: int
    replaces: tuple[int, ...]


@dataclass(frozen=True)
class _CommitResult:
    """Outcome of the finalising transaction of an addition."""

    superseded_names: list[str]
    commit_seconds: float


def spawn_add_job(
    kb_id: int, user_id: int, staging: Path, items: list[AddItem], unchanged: int
) -> None:
    """Start run_add_job in the background, keeping a strong reference until it ends."""
    task = asyncio.create_task(
        run_add_job(kb_id, user_id, staging, items, unchanged), name=f"kb-add-{kb_id}"
    )
    kb_jobs[kb_id] = task

    def _forget(done: asyncio.Task[None]) -> None:
        if kb_jobs.get(kb_id) is done:
            kb_jobs.pop(kb_id, None)

    task.add_done_callback(_forget)


def _move_staged(staging: Path, target: Path, names: list[str], moved: list[Path]) -> None:
    """Move staged files into the uploads directory, recording each for cleanup (worker thread)."""
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        destination = target / name
        (staging / name).replace(destination)
        moved.append(destination)


def _unlink_files(paths: list[Path]) -> None:
    """Delete files, ignoring missing ones (worker thread)."""
    for path in paths:
        path.unlink(missing_ok=True)


async def _commit_addition(
    kb_id: int,
    user_id: int,
    items: list[AddItem],
    drafts_by_item: list[list[ChunkDraft]],
    page_counts: list[int | None],
    vectors: list[list[float]],
) -> _CommitResult:
    """Apply an addition in one transaction and swap the patched index in atomically."""
    path = index_path(user_id, kb_id)
    replaced_ids = [doc_id for item in items for doc_id in item.replaces]
    matrix = np.asarray(vectors, dtype="float32")
    async with async_session_factory() as session:
        started = time.perf_counter()
        pending: Path | None = None
        committed = False
        try:
            # Inserts come first so every new chunk id exceeds every existing id
            # (SQLite reuses rowids after deletes).
            docs: list[KbDocument] = []
            for item, page_count in zip(items, page_counts):
                doc = KbDocument(
                    kb_id=kb_id,
                    filename=item.source,
                    sha256=item.sha256,
                    size_bytes=item.size_bytes,
                    page_count=page_count,
                    stored_name=item.stored_name,
                )
                session.add(doc)
                docs.append(doc)
            await session.flush()
            new_ids: list[int] = []
            for doc, drafts in zip(docs, drafts_by_item):
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
                    new_ids.extend(row.id for row in batch)
            old_chunk_ids: list[int] = []
            superseded: list[str] = []
            if replaced_ids:
                old_chunk_ids = list(
                    (
                        await session.exec(
                            select(KbChunk.id).where(
                                KbChunk.kb_id == kb_id, KbChunk.document_id.in_(replaced_ids)
                            )
                        )
                    ).all()
                )
                superseded = list(
                    (
                        await session.exec(
                            select(KbDocument.stored_name).where(
                                KbDocument.kb_id == kb_id, KbDocument.id.in_(replaced_ids)
                            )
                        )
                    ).all()
                )
                await session.exec(
                    delete(KbChunk).where(
                        KbChunk.kb_id == kb_id, KbChunk.document_id.in_(replaced_ids)
                    )
                )
                await session.exec(
                    delete(KbDocument).where(
                        KbDocument.kb_id == kb_id, KbDocument.id.in_(replaced_ids)
                    )
                )
            file_count = (
                await session.exec(
                    select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
                )
            ).one()
            chunk_count = (
                await session.exec(
                    select(func.count()).select_from(KbChunk).where(KbChunk.kb_id == kb_id)
                )
            ).one()
            index = await asyncio.to_thread(patch_index, path, old_chunk_ids, matrix, new_ids)
            if int(index.ntotal) != chunk_count:
                raise RuntimeError("index size does not match chunk count")
            pending = await asyncio.to_thread(write_index_pending, path, index)
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None:
                raise RuntimeError("knowledge base disappeared during addition")
            kb.file_count = file_count
            kb.chunk_count = chunk_count
            kb.done_chunks = chunk_count
            kb.total_chunks = chunk_count
            kb.phase = None
            kb.error = None
            kb.updated_at = datetime.now(timezone.utc)
            session.add(kb)
            await session.commit()
            committed = True
            # No await between the commit and the swap: a cold index load must
            # never see the new counts next to the old file.
            promote_index(pending, path)
            kb_index_cache[kb_id] = index
            elapsed = time.perf_counter() - started
        except Exception as exc:
            if committed:
                # The rows are durable and reference the moved files, so the
                # caller must not delete them; the pending index is kept for repair.
                kb_index_cache.pop(kb_id, None)
                raise _PostCommitError(type(exc).__name__) from exc
            await session.rollback()
            if pending is not None:
                pending.unlink(missing_ok=True)
            raise
    if elapsed > ADD_COMMIT_WARN_SECONDS:
        logger.warning(
            "kb_add_commit_slow",
            kb_id=kb_id,
            user_id=user_id,
            commit_seconds=round(elapsed, 3),
            chunk_count=chunk_count,
        )
    return _CommitResult(superseded_names=superseded, commit_seconds=elapsed)


async def _finish_addition(
    kb_id: int,
    user_id: int,
    staging: Path,
    items: list[AddItem],
    unchanged: int,
    result: _CommitResult,
    started: float,
) -> None:
    """Clean up after a committed addition and announce the summary; never raises."""
    try:
        stale = [uploads_dir(user_id, kb_id) / name for name in result.superseded_names]
        await asyncio.to_thread(_unlink_files, stale)
        await asyncio.to_thread(remove_tree, staging)
        summary = {
            "added": sum(1 for item in items if not item.replaces),
            "replaced": sum(1 for item in items if item.replaces),
            "unchanged": unchanged,
        }
        async with async_session_factory() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None:
                return
            hub.publish(user_id, kb_progress_frame(kb, add_summary=summary))
            chunk_count = kb.chunk_count
        logger.info(
            "kb_add_ready",
            kb_id=kb_id,
            user_id=user_id,
            added=summary["added"],
            replaced=summary["replaced"],
            unchanged=unchanged,
            chunk_count=chunk_count,
            seconds=round(time.monotonic() - started, 2),
            commit_seconds=round(result.commit_seconds, 3),
        )
    except Exception as exc:
        logger.error("kb_add_finish_failed", kb_id=kb_id, error=type(exc).__name__)


async def _finish_without_new_files(
    kb_id: int, user_id: int, staging: Path, kb: KnowledgeBase, unchanged: int
) -> None:
    """Complete an addition whose files were all unchanged: nothing to embed or store."""
    await _update_kb(
        kb_id,
        publish=False,
        phase=None,
        error=None,
        done_chunks=kb.chunk_count,
        total_chunks=kb.chunk_count,
    )
    await asyncio.to_thread(remove_tree, staging)
    async with async_session_factory() as session:
        row = await session.get(KnowledgeBase, kb_id)
        if row is not None:
            summary = {"added": 0, "replaced": 0, "unchanged": unchanged}
            hub.publish(user_id, kb_progress_frame(row, add_summary=summary))
    logger.info(
        "kb_add_ready", kb_id=kb_id, user_id=user_id, added=0, replaced=0, unchanged=unchanged
    )


async def _settle(task: asyncio.Task[_CommitResult]) -> _CommitResult | None:
    """Wait for the finalising task to end; None when it failed."""
    try:
        return await task
    except Exception:
        return None


def _is_post_commit_failure(task: asyncio.Task[_CommitResult] | None) -> bool:
    """True when the finalising task failed after its database commit."""
    if task is None or not task.done() or task.cancelled():
        return False
    return isinstance(task.exception(), _PostCommitError)


async def run_add_job(
    kb_id: int, user_id: int, staging: Path, items: list[AddItem], unchanged: int
) -> None:
    """Add files to a ready knowledge base; any failure leaves the base exactly as it was."""
    started = time.monotonic()
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None:
            await asyncio.to_thread(remove_tree, staging)
            return
        session.expunge(kb)
    logger.info("kb_add_started", kb_id=kb_id, user_id=user_id, files=len(items))
    if not items:
        await _finish_without_new_files(kb_id, user_id, staging, kb, unchanged)
        return
    moved: list[Path] = []
    commit_task: asyncio.Task[_CommitResult] | None = None
    move_task: asyncio.Future[None] | None = None
    failure: str | None = None
    cancelled = False
    try:
        async with _get_semaphore():
            await ensure_embedding_model(kb.embedding_model)
            drafts_by_item: list[list[ChunkDraft]] = []
            page_counts: list[int | None] = []
            for item in items:
                drafts, page_count = await asyncio.to_thread(
                    _chunk_document, kb, staging / item.stored_name, item.source
                )
                drafts_by_item.append(drafts)
                page_counts.append(page_count)
            texts = [draft.text for drafts in drafts_by_item for draft in drafts]
            await _update_kb(kb_id, phase=PHASE_ADDING, total_chunks=len(texts), done_chunks=0)
            vectors = await _embed_all(kb_id, kb.embedding_model, [(0, text) for text in texts])
            if any(len(vector) != kb.dim for vector in vectors):
                raise KbAddError(MSG_ADD_DIM_MISMATCH)
            move_task = asyncio.ensure_future(
                asyncio.to_thread(
                    _move_staged,
                    staging,
                    uploads_dir(user_id, kb_id),
                    [item.stored_name for item in items],
                    moved,
                )
            )
            await asyncio.shield(move_task)
            commit_task = asyncio.create_task(
                _commit_addition(kb_id, user_id, items, drafts_by_item, page_counts, vectors)
            )
            result = await asyncio.shield(commit_task)
        await _finish_addition(kb_id, user_id, staging, items, unchanged, result, started)
    except (KbLoadError, EmbeddingError, KbAddError) as exc:
        logger.error("kb_add_failed", kb_id=kb_id, user_id=user_id, error=type(exc).__name__)
        failure = exc.message
    except asyncio.CancelledError:
        cancelled = True
    except _PostCommitError as exc:
        logger.error("kb_add_failed", kb_id=kb_id, user_id=user_id, error=str(exc))
        failure = MSG_ADD_UNEXPECTED
        moved = []  # committed documents reference these files
    except Exception as exc:
        logger.error("kb_add_failed", kb_id=kb_id, user_id=user_id, error=type(exc).__name__)
        failure = MSG_ADD_UNEXPECTED
    # Cleanup runs after the handlers so a traceback no longer pins an open file
    # handle inside the staging directory (Windows cannot delete open files).
    if cancelled:
        if move_task is not None and not move_task.done():
            # The move thread keeps running after cancellation; let it finish so
            # every moved file is in `moved` before cleanup.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await move_task
        settled = await _settle(commit_task) if commit_task is not None else None
        if settled is not None:
            await _finish_addition(kb_id, user_id, staging, items, unchanged, settled, started)
        else:
            if _is_post_commit_failure(commit_task):
                moved = []
            await _fail_add(kb_id, user_id, MSG_ADD_INTERRUPTED, staging, moved)
        raise asyncio.CancelledError
    if failure is not None:
        await _fail_add(kb_id, user_id, failure, staging, moved)


async def _fail_add(
    kb_id: int, user_id: int, message: str, staging: Path, moved: list[Path]
) -> None:
    """An addition failed; the base stays ready and untouched, only the error is recorded."""
    try:
        await asyncio.to_thread(_unlink_files, moved)
        await asyncio.to_thread(remove_tree, staging)
        async with async_session_factory() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None:
                return
            try:
                kb.phase = None
                kb.error = message
                kb.done_chunks = kb.chunk_count
                kb.total_chunks = kb.chunk_count
                kb.updated_at = datetime.now(timezone.utc)
                session.add(kb)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            await session.refresh(kb)
            hub.publish(user_id, kb_progress_frame(kb))
    except Exception as exc:
        logger.error("kb_add_fail_record_failed", kb_id=kb_id, error=type(exc).__name__)


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


def _sweep_orphan_uploads(user_id: int, kb_id: int, keep: set[str]) -> None:
    """Remove the staging root and uploaded files no document refers to (worker thread)."""
    remove_tree(staging_root(user_id, kb_id))
    target = uploads_dir(user_id, kb_id)
    if not target.is_dir():
        return
    for entry in target.iterdir():
        if entry.is_file() and entry.name not in keep:
            entry.unlink(missing_ok=True)


async def recover_orphaned_kb_jobs() -> int:
    """Fail knowledge bases left queued/indexing by a dead process and clear stale additions."""
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
            adding = list(
                (
                    await session.exec(
                        select(KnowledgeBase.id, KnowledgeBase.user_id).where(
                            KnowledgeBase.status == KbStatus.READY,
                            KnowledgeBase.phase.is_not(None),
                        )
                    )
                ).all()
            )
            keep: dict[int, set[str]] = {}
            adding_ids = [row[0] for row in adding]
            if adding_ids:
                await session.exec(
                    update(KnowledgeBase)
                    .where(KnowledgeBase.id.in_(adding_ids))
                    .values(
                        phase=None,
                        error=MSG_ADD_INTERRUPTED_RESTART,
                        done_chunks=KnowledgeBase.chunk_count,
                        total_chunks=KnowledgeBase.chunk_count,
                    )
                )
                for kb_id, stored in (
                    await session.exec(
                        select(KbDocument.kb_id, KbDocument.stored_name).where(
                            KbDocument.kb_id.in_(adding_ids)
                        )
                    )
                ).all():
                    keep.setdefault(kb_id, set()).add(stored)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    for kb_id, user_id in rows:
        await asyncio.to_thread(remove_index_file, user_id, kb_id)
    for kb_id, user_id in adding:
        await asyncio.to_thread(_sweep_orphan_uploads, user_id, kb_id, keep.get(kb_id, set()))
    logger.info("kb_recovered_orphans", count=len(rows))
    logger.info("kb_recovered_additions", count=len(adding))
    return len(rows)


async def shutdown_kb_jobs() -> None:
    """Cancel and await every running indexing job."""
    tasks = list(kb_jobs.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    kb_jobs.clear()
