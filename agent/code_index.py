"""Code knowledge base built from a git base branch, refreshed incrementally by blob id."""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import numpy as np
from sqlalchemy import delete, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import kb_indexer
from agent.code_chunking import MAX_CHUNK_CHARS, chunk_code
from agent.code_git import (
    GitError,
    TreeEntry,
    fetch_base_commit,
    list_tree,
    read_blobs,
    repo_key,
    validate_repo_path,
)
from agent.embeddings import (
    EmbeddingError,
    embed_passages,
    ensure_embedding_model,
    prefixes_for,
)
from agent.kb_chunking import ChunkDraft
from agent.kb_limits import EMBED_BATCH_SIZE
from agent.state import kb_index_cache
from shared.database import async_session_factory
from shared.kb_storage import (
    build_index,
    index_path,
    patch_index,
    promote_index,
    remove_kb_dir,
    write_index_pending,
)
from shared.logger import get_logger
from shared.models import (
    KbChunk,
    KbDocument,
    KbStatus,
    KbStrategy,
    KnowledgeBase,
    ReviewCodeIndex,
)

logger = get_logger(__name__)

CODE_BASE_UNAVAILABLE = "BASE_UNAVAILABLE"
CODE_EMBEDDER_UNAVAILABLE = "EMBEDDER_UNAVAILABLE"

DEFAULT_EMBEDDING_MODEL = "text-embedding-bge-m3"
MAX_FILE_BYTES = 200_000
MAX_JSON_BYTES = 20_000
MAX_BASE_REFS = 5
CHUNK_INSERT_SLICE = 500

MSG_REPO_MISMATCH = (
    "Репозиторий по указанному пути ({local}) не совпадает "
    "с репозиторием Pull Request ({expected})."
)
MSG_NOTHING_TO_INDEX = "В ветке {ref} нет файлов, пригодных для индексации."
MSG_DIM_MISMATCH = "Модель эмбеддингов вернула векторы другой размерности, чем у индекса кода."
MSG_UNEXPECTED = "Не удалось обновить индекс кода."

PHASE_FETCH = "fetch"
PHASE_DIFF = "diff"
PHASE_EMBED = "embed"
PHASE_COMMIT = "commit"

_ALLOWED_SUFFIXES = frozenset(
    {".py", ".js", ".html", ".css", ".ini", ".toml", ".cfg", ".yml", ".yaml", ".json", ".txt"}
)
_EXCLUDED_PREFIXES = (".planning/", "docs/", "eval_out/", "tests/fixtures/", "node_modules/")
_EXCLUDED_SUFFIXES = frozenset(
    {".md", ".db", ".pem", ".key", ".lock", ".png", ".jpg", ".gif", ".ico", ".pdf", ".faiss"}
)


class CodeIndexError(Exception):
    """The code index cannot be refreshed; `detail` is a readable Russian reason."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CodeIndexState:
    """What a refresh produced: where the base stands and how much it changed."""

    kb_id: int
    repo_key: str
    base_ref: str
    commit: str
    file_count: int
    chunk_count: int
    changed: int
    removed: int
    updated_at: datetime


@dataclass
class RefreshProgress:
    """Progress of the refresh of one user, for the settings section."""

    running: bool = False
    phase: str | None = None
    done: int = 0
    total: int = 0
    error: str | None = None


@dataclass(frozen=True)
class _Existing:
    """The stored state of one code base: its row ids and the blob id of each file."""

    kb_id: int
    dim: int | None
    file_count: int
    chunk_count: int
    documents: dict[str, tuple[int, str]]


@dataclass(frozen=True)
class _Fragments:
    """The fragments of one changed file."""

    entry: TreeEntry
    drafts: list[ChunkDraft]


@dataclass(frozen=True)
class _Committed:
    """Counts after the commit of a refresh."""

    kb_id: int
    file_count: int
    chunk_count: int
    updated_at: datetime


_progress: dict[int, RefreshProgress] = {}
_jobs: dict[int, asyncio.Task[None]] = {}
_locks: dict[tuple[int, str, str], asyncio.Lock] = {}


def reset_state() -> None:
    """Forget progress, jobs and locks (tests; locks belong to one event loop)."""
    _progress.clear()
    _jobs.clear()
    _locks.clear()


def is_indexable(path: str, size: int) -> bool:
    """Whether a tracked file belongs in the code index."""
    normalised = path.replace("\\", "/")
    name = PurePosixPath(normalised).name
    suffix = PurePosixPath(normalised).suffix.lower()
    if name.startswith(".env") or name == "package-lock.json" or name.lower().endswith(".min.js"):
        return False
    if normalised.startswith(_EXCLUDED_PREFIXES) or "/node_modules/" in f"/{normalised}":
        return False
    if suffix in _EXCLUDED_SUFFIXES or suffix not in _ALLOWED_SUFFIXES:
        return False
    if size > MAX_FILE_BYTES:
        return False
    return not (suffix == ".json" and size > MAX_JSON_BYTES)


def get_refresh_progress(user_id: int) -> RefreshProgress:
    """Return the progress record of a user (a fresh idle one when there is none)."""
    return _progress.setdefault(user_id, RefreshProgress())


def _report(user_id: int, phase: str | None, done: int = 0, total: int = 0) -> None:
    """Record the phase and counters of the user's refresh."""
    record = get_refresh_progress(user_id)
    record.phase = phase
    record.done = done
    record.total = total


async def latest_index(
    session: AsyncSession, user_id: int, repo_key: str | None = None
) -> ReviewCodeIndex | None:
    """Return the most recently updated code base link of the user."""
    statement = select(ReviewCodeIndex).where(ReviewCodeIndex.user_id == user_id)
    if repo_key is not None:
        statement = statement.where(ReviewCodeIndex.repo_key == repo_key)
    statement = statement.order_by(ReviewCodeIndex.updated_at.desc(), ReviewCodeIndex.id.desc())
    return (await session.exec(statement.limit(1))).first()


async def refresh_code_index(
    user_id: int,
    repo_path: str,
    base_ref: str,
    expected_repo: str | None,
    embedding_model: str,
) -> CodeIndexState:
    """Bring the code base of `base_ref` up to the tip of that branch on origin."""
    _report(user_id, PHASE_FETCH)
    try:
        top = await validate_repo_path(repo_path)
        key = await repo_key(top)
    except GitError as exc:
        raise CodeIndexError(CODE_BASE_UNAVAILABLE, exc.detail) from exc
    if expected_repo is not None and expected_repo.strip().lower() != key:
        raise CodeIndexError(
            CODE_BASE_UNAVAILABLE, MSG_REPO_MISMATCH.format(local=key, expected=expected_repo)
        )
    lock = _locks.setdefault((user_id, key, base_ref), asyncio.Lock())
    # The fetch sits inside the lock so an older tip can never overwrite a newer one.
    async with lock:
        try:
            commit = await fetch_base_commit(top, base_ref)
        except GitError as exc:
            raise CodeIndexError(CODE_BASE_UNAVAILABLE, exc.detail) from exc
        return await _refresh_locked(user_id, top, key, base_ref, commit, embedding_model)


async def _refresh_locked(
    user_id: int, top: str, key: str, base_ref: str, commit: str, model: str
) -> CodeIndexState:
    """Compare the stored base with the commit and commit only the difference."""
    started = time.monotonic()
    _report(user_id, PHASE_DIFF)
    try:
        tree = [e for e in await list_tree(top, commit) if is_indexable(e.path, e.size)]
    except GitError as exc:
        raise CodeIndexError(CODE_BASE_UNAVAILABLE, exc.detail) from exc
    existing = await _load_existing(user_id, key, base_ref, model)
    stored = existing.documents if existing is not None else {}
    wanted = {entry.path: entry for entry in tree}
    changed = [e for e in tree if stored.get(e.path, (0, ""))[1] != e.blob_id]
    removed = [name for name in stored if name not in wanted]
    if existing is not None and not changed and not removed:
        return await _touch(user_id, key, base_ref, commit, existing)
    if existing is None and not tree:
        raise CodeIndexError(CODE_BASE_UNAVAILABLE, MSG_NOTHING_TO_INDEX.format(ref=base_ref))
    async with kb_indexer._get_semaphore():
        try:
            fragments = await _read_fragments(top, changed)
            texts = [draft.text for item in fragments for draft in item.drafts]
            vectors = await _embed(user_id, model, texts)
            dim = len(vectors[0]) if vectors else (existing.dim if existing else None)
            if existing is None and not vectors:
                raise CodeIndexError(
                    CODE_BASE_UNAVAILABLE, MSG_NOTHING_TO_INDEX.format(ref=base_ref)
                )
            if existing is not None and vectors and existing.dim not in (None, dim):
                raise CodeIndexError(CODE_EMBEDDER_UNAVAILABLE, MSG_DIM_MISMATCH)
            _report(user_id, PHASE_COMMIT, len(texts), len(texts))
            done = await _commit(
                user_id, key, base_ref, commit, model, existing, fragments, removed, vectors, dim
            )
        finally:
            get_refresh_progress(user_id).phase = None
    if existing is None:
        await _evict_old(user_id, key, done.kb_id)
    logger.info(
        "code_index_refreshed",
        user_id=user_id,
        kb_id=done.kb_id,
        base_ref=base_ref,
        commit=commit,
        changed=len(changed),
        removed=len(removed),
        chunks=done.chunk_count,
        seconds=round(time.monotonic() - started, 2),
    )
    return CodeIndexState(
        kb_id=done.kb_id,
        repo_key=key,
        base_ref=base_ref,
        commit=commit,
        file_count=done.file_count,
        chunk_count=done.chunk_count,
        changed=len(changed),
        removed=len(removed),
        updated_at=done.updated_at,
    )


async def _load_existing(user_id: int, key: str, base_ref: str, model: str) -> _Existing | None:
    """Read the stored base of this branch; a base built with another model is dropped."""
    async with async_session_factory() as session:
        link = (
            await session.exec(
                select(ReviewCodeIndex).where(
                    ReviewCodeIndex.user_id == user_id,
                    ReviewCodeIndex.repo_key == key,
                    ReviewCodeIndex.base_ref == base_ref,
                )
            )
        ).first()
        if link is None:
            return None
        kb = await session.get(KnowledgeBase, link.kb_id)
        if kb is None or kb.user_id != user_id or kb.embedding_model != model:
            await _drop_base(session, link, kb)
            return None
        rows = (
            await session.exec(
                select(KbDocument.filename, KbDocument.id, KbDocument.sha256).where(
                    KbDocument.kb_id == kb.id
                )
            )
        ).all()
        return _Existing(
            kb_id=kb.id,
            dim=kb.dim,
            file_count=kb.file_count,
            chunk_count=kb.chunk_count,
            documents={name: (doc_id, sha) for name, doc_id, sha in rows},
        )


async def _drop_base(
    session: AsyncSession, link: ReviewCodeIndex, kb: KnowledgeBase | None
) -> None:
    """Remove a code base link together with its knowledge base."""
    try:
        await session.exec(delete(ReviewCodeIndex).where(ReviewCodeIndex.id == link.id))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if kb is not None:
        await kb_indexer.delete_kb(session, kb)


async def _touch(
    user_id: int, key: str, base_ref: str, commit: str, existing: _Existing
) -> CodeIndexState:
    """Nothing changed: only record the commit and the time of the check."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        try:
            link = (
                await session.exec(
                    select(ReviewCodeIndex).where(
                        ReviewCodeIndex.user_id == user_id,
                        ReviewCodeIndex.repo_key == key,
                        ReviewCodeIndex.base_ref == base_ref,
                    )
                )
            ).one()
            link.indexed_commit = commit
            link.updated_at = now
            session.add(link)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    _report(user_id, None)
    logger.info(
        "code_index_refreshed",
        user_id=user_id,
        kb_id=existing.kb_id,
        base_ref=base_ref,
        commit=commit,
        changed=0,
        removed=0,
        chunks=existing.chunk_count,
        seconds=0.0,
    )
    return CodeIndexState(
        kb_id=existing.kb_id,
        repo_key=key,
        base_ref=base_ref,
        commit=commit,
        file_count=existing.file_count,
        chunk_count=existing.chunk_count,
        changed=0,
        removed=0,
        updated_at=now,
    )


def _split_blobs(entries: list[TreeEntry], blobs: dict[str, bytes]) -> list[_Fragments]:
    """Chunk every changed file; binary files get no fragments but are still recorded."""
    result: list[_Fragments] = []
    for entry in entries:
        data = blobs[entry.blob_id]
        drafts: list[ChunkDraft] = []
        if b"\x00" not in data:
            drafts = chunk_code(entry.path, data.decode("utf-8", errors="replace"))
        result.append(_Fragments(entry=entry, drafts=drafts))
    return result


async def _read_fragments(top: str, changed: list[TreeEntry]) -> list[_Fragments]:
    """Read the changed blobs from git and split them into fragments."""
    if not changed:
        return []
    try:
        blobs = await read_blobs(top, [entry.blob_id for entry in changed])
    except GitError as exc:
        raise CodeIndexError(CODE_BASE_UNAVAILABLE, exc.detail) from exc
    return await asyncio.to_thread(_split_blobs, changed, blobs)


async def _embed(user_id: int, model: str, texts: list[str]) -> list[list[float]]:
    """Embed fragment texts in batches, reporting progress."""
    _report(user_id, PHASE_EMBED, 0, len(texts))
    if not texts:
        return []
    vectors: list[list[float]] = []
    try:
        await ensure_embedding_model(model)
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            vectors.extend(await embed_passages(model, texts[start : start + EMBED_BATCH_SIZE]))
            _report(user_id, PHASE_EMBED, len(vectors), len(texts))
    except EmbeddingError as exc:
        logger.error("code_index_embed_failed", user_id=user_id, error=type(exc).__name__)
        raise CodeIndexError(CODE_EMBEDDER_UNAVAILABLE, exc.message) from exc
    if len({len(vector) for vector in vectors}) > 1:
        raise CodeIndexError(CODE_EMBEDDER_UNAVAILABLE, MSG_DIM_MISMATCH)
    return vectors


async def _commit(
    user_id: int,
    key: str,
    base_ref: str,
    commit: str,
    model: str,
    existing: _Existing | None,
    fragments: list[_Fragments],
    removed: list[str],
    vectors: list[list[float]],
    dim: int | None,
) -> _Committed:
    """Apply the difference in one transaction and swap the new index file in after it."""
    now = datetime.now(timezone.utc)
    kb_id = existing.kb_id if existing is not None else 0
    created = existing is None
    pending: Path | None = None
    committed = False
    async with async_session_factory() as session:
        try:
            if existing is None:
                query_prefix, doc_prefix = prefixes_for(model)
                kb = KnowledgeBase(
                    user_id=user_id,
                    name=f"Код: {key}@{base_ref}"[:200],
                    status=KbStatus.READY,
                    strategy=KbStrategy.FIXED,
                    chunk_size=MAX_CHUNK_CHARS,
                    chunk_overlap=0,
                    embedding_model=model,
                    dim=dim,
                    query_prefix=query_prefix,
                    doc_prefix=doc_prefix,
                    created_at=now,
                    updated_at=now,
                )
                session.add(kb)
                await session.flush()
                kb_id = kb.id
                session.add(
                    ReviewCodeIndex(
                        user_id=user_id,
                        repo_key=key,
                        base_ref=base_ref,
                        kb_id=kb_id,
                        indexed_commit=None,
                        updated_at=now,
                    )
                )
            stored = existing.documents if existing is not None else {}
            # Inserts come first so every new chunk id exceeds every existing id
            # (SQLite reuses rowids after deletes).
            docs: list[KbDocument] = []
            for item in fragments:
                doc = KbDocument(
                    kb_id=kb_id,
                    filename=item.entry.path,
                    sha256=item.entry.blob_id,
                    size_bytes=item.entry.size,
                    stored_name=item.entry.blob_id,
                )
                session.add(doc)
                docs.append(doc)
            await session.flush()
            new_ids: list[int] = []
            for doc, item in zip(docs, fragments):
                rows = kb_indexer._chunk_rows(kb_id, doc, item.drafts)
                for start in range(0, len(rows), CHUNK_INSERT_SLICE):
                    batch = rows[start : start + CHUNK_INSERT_SLICE]
                    session.add_all(batch)
                    await session.flush()
                    new_ids.extend(row.id for row in batch)
            superseded = [name for name in removed if name in stored]
            superseded += [item.entry.path for item in fragments if item.entry.path in stored]
            old_doc_ids = [stored[name][0] for name in dict.fromkeys(superseded)]
            old_chunk_ids: list[int] = []
            if old_doc_ids:
                old_chunk_ids = list(
                    (
                        await session.exec(
                            select(KbChunk.id).where(
                                KbChunk.kb_id == kb_id, KbChunk.document_id.in_(old_doc_ids)
                            )
                        )
                    ).all()
                )
                await session.exec(
                    delete(KbChunk).where(
                        KbChunk.kb_id == kb_id, KbChunk.document_id.in_(old_doc_ids)
                    )
                )
                await session.exec(
                    delete(KbDocument).where(
                        KbDocument.kb_id == kb_id, KbDocument.id.in_(old_doc_ids)
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
            matrix = np.asarray(vectors, dtype="float32") if vectors else None
            path = index_path(user_id, kb_id)
            if created:
                index = await asyncio.to_thread(build_index, matrix, new_ids)
                await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            else:
                index = await asyncio.to_thread(patch_index, path, old_chunk_ids, matrix, new_ids)
            if int(index.ntotal) != chunk_count:
                raise RuntimeError("index size does not match chunk count")
            pending = await asyncio.to_thread(write_index_pending, path, index)
            kb_row = await session.get(KnowledgeBase, kb_id)
            link = (
                await session.exec(select(ReviewCodeIndex).where(ReviewCodeIndex.kb_id == kb_id))
            ).one()
            if kb_row is None:
                raise RuntimeError("code knowledge base disappeared during refresh")
            kb_row.file_count = file_count
            kb_row.chunk_count = chunk_count
            kb_row.done_chunks = chunk_count
            kb_row.total_chunks = chunk_count
            kb_row.dim = dim
            kb_row.status = KbStatus.READY
            kb_row.phase = None
            kb_row.error = None
            kb_row.updated_at = now
            link.indexed_commit = commit
            link.updated_at = now
            session.add(kb_row)
            session.add(link)
            await session.commit()
            committed = True
            # No await between the commit and the swap: a cold index load must
            # never see the new counts next to the old file.
            promote_index(pending, path)
            kb_index_cache[kb_id] = index
        except (Exception, asyncio.CancelledError) as exc:
            if committed:
                kb_index_cache.pop(kb_id, None)
                logger.error("code_index_swap_failed", user_id=user_id, error=type(exc).__name__)
                raise
            await session.rollback()
            if pending is not None:
                pending.unlink(missing_ok=True)
            if created and kb_id:
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(remove_kb_dir, user_id, kb_id)
            raise
    return _Committed(kb_id=kb_id, file_count=file_count, chunk_count=chunk_count, updated_at=now)


async def _evict_old(user_id: int, key: str, keep_kb_id: int) -> None:
    """Keep at most MAX_BASE_REFS code bases per user and repository; drop the stalest."""
    async with async_session_factory() as session:
        links = list(
            (
                await session.exec(
                    select(ReviewCodeIndex)
                    .where(ReviewCodeIndex.user_id == user_id, ReviewCodeIndex.repo_key == key)
                    .order_by(ReviewCodeIndex.updated_at, ReviewCodeIndex.id)
                )
            ).all()
        )
        surplus = len(links) - MAX_BASE_REFS
        for link in [item for item in links if item.kb_id != keep_kb_id][: max(surplus, 0)]:
            kb = await session.get(KnowledgeBase, link.kb_id)
            await _drop_base(session, link, kb)
            logger.info("code_index_evicted", user_id=user_id, base_ref=link.base_ref)


def spawn_refresh_job(
    user_id: int, repo_path: str, base_ref: str, embedding_model: str
) -> bool:
    """Start a refresh in the background; False when this user already has one running."""
    record = get_refresh_progress(user_id)
    job = _jobs.get(user_id)
    if record.running or (job is not None and not job.done()):
        return False
    record.running = True
    record.phase = PHASE_FETCH
    record.done = 0
    record.total = 0
    record.error = None
    task = asyncio.create_task(
        _run_job(user_id, repo_path, base_ref, embedding_model), name=f"code-index-{user_id}"
    )
    _jobs[user_id] = task

    def _forget(done: asyncio.Task[None]) -> None:
        if _jobs.get(user_id) is done:
            _jobs.pop(user_id, None)

    task.add_done_callback(_forget)
    return True


async def _run_job(user_id: int, repo_path: str, base_ref: str, embedding_model: str) -> None:
    """Run one refresh and store the outcome in the user's progress record."""
    record = get_refresh_progress(user_id)
    try:
        await refresh_code_index(user_id, repo_path, base_ref, None, embedding_model)
    except CodeIndexError as exc:
        record.error = exc.detail
    except asyncio.CancelledError:
        record.error = MSG_UNEXPECTED
        raise
    except Exception as exc:
        logger.error("code_index_job_failed", user_id=user_id, error=type(exc).__name__)
        record.error = MSG_UNEXPECTED
    finally:
        record.running = False
        record.phase = None
