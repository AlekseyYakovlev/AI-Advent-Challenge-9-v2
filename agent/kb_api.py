"""REST routes for knowledge bases (/api/v1/kb)."""

import asyncio
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import kb_indexer
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.embeddings import (
    MSG_LM_STUDIO_DOWN,
    EmbeddingError,
    embed_query,
    ensure_embedding_model,
    list_embedding_models,
)
from agent.events import hub
from agent.kb_chunking import validate_chunk_params
from agent.kb_limits import (
    ALLOWED_EXTENSIONS,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    MAX_EMBED_CHARS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_KB_NAME_CHARS,
    MAX_QUERY_CHARS,
    MAX_REQUEST_BYTES,
    MAX_SOURCE_NAME_CHARS,
    MAX_TOTAL_BYTES,
    PHASE_ADDING,
    SEARCH_DEFAULT_TOP_K,
    SEARCH_MAX_TOP_K,
)
from agent.kb_schemas import KbOut, kb_out, kb_progress_frame
from agent.kb_search import (
    MSG_INDEX_CORRUPT,
    MSG_NOT_READY,
    KbIndexCorruptError,
    KbNotReadyError,
    search_kb,
)
from shared.database import async_session_factory, get_session
from shared.kb_storage import new_staging_dir, remove_kb_dir, remove_tree, uploads_dir
from shared.logger import get_logger
from shared.models import KbDocument, KbStatus, KbStrategy, KnowledgeBase, User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])

MSG_KB_NOT_FOUND = "База знаний не найдена"
MSG_NAME_REQUIRED = "Введите название базы знаний."
MSG_NAME_TOO_LONG = "Название не длиннее 200 символов."
MSG_FILES_REQUIRED = "Выберите хотя бы один файл."
MSG_TOO_MANY_FILES = f"Можно загрузить не больше {MAX_FILES} файлов за одну загрузку."
MSG_BAD_EXTENSION = "Файл {name}: поддерживаются только PDF, TXT и MD."
MSG_FILE_TOO_LARGE = "Файл {name} больше " + f"{MAX_FILE_BYTES // (1024 * 1024)} МБ."
MSG_TOTAL_TOO_LARGE = f"Общий размер файлов больше {MAX_TOTAL_BYTES // (1024 * 1024)} МБ."
MSG_SOURCE_TOO_LONG = "Имя файла {name} длиннее " + f"{MAX_SOURCE_NAME_CHARS} символов."
MSG_EMPTY_FILE = "Файл {name} пустой."
MSG_DUPLICATE = "Файл {name} уже добавлен."
MSG_BAD_STRATEGY = "Неизвестная стратегия разбиения."
MSG_MODEL_REQUIRED = "Выберите модель эмбеддингов."
MSG_ADD_NOT_READY = "Добавлять файлы можно только в готовую базу знаний."
MSG_ADD_BUSY = "Идёт добавление файлов. Дождитесь его окончания."

READ_CHUNK_BYTES = 1024 * 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_SEGMENT = re.compile(r"^[A-Za-z]:$")
CHECK_PROBE_TEXT = "проверка эмбеддинга"


class UploadRejectedError(Exception):
    """An upload violated a validation rule; carries the Russian message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class KbSearchRequest(BaseModel):
    """Body of a test search."""

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: int = Field(default=SEARCH_DEFAULT_TOP_K, ge=1, le=SEARCH_MAX_TOP_K)


class EmbeddingCheckRequest(BaseModel):
    """Body of an embedding model check."""

    model: str = Field(min_length=1)


def _not_found() -> HTTPException:
    """Build the 404 used for missing and foreign ids alike (never 403)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MSG_KB_NOT_FOUND)


def _unprocessable(message: str) -> HTTPException:
    """Build the 422 carrying the Russian message the UI shows inline."""
    return HTTPException(status_code=422, detail=message)


def _embedding_http_error(exc: EmbeddingError) -> HTTPException:
    """Map an embedding failure to 503 (LM Studio down) or 422."""
    code = 503 if exc.message == MSG_LM_STUDIO_DOWN else 422
    return HTTPException(status_code=code, detail=exc.message)


async def _require_content_length_within_cap(request: Request) -> None:
    """Reject a declared request size above the upload cap before reading the body."""
    declared = request.headers.get("content-length")
    if declared is None:
        raise HTTPException(411, detail="Length Required")
    if not declared.isdigit():
        raise HTTPException(400, detail="Invalid Content-Length")
    if int(declared) > MAX_REQUEST_BYTES:
        raise HTTPException(413, detail=MSG_TOTAL_TOO_LARGE)


async def _get_owned_kb(session: AsyncSession, kb_id: int, user_id: int) -> KnowledgeBase:
    """Return the caller's knowledge base or raise 404."""
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None or kb.user_id != user_id:
        raise _not_found()
    return kb


def _validate_settings(
    name: str, strategy: str, chunk_size: int, chunk_overlap: int, model: str
) -> tuple[str, KbStrategy, int, int, str]:
    """Validate scalar form fields, returning normalized values or raising 422."""
    name = name.strip()
    if not name:
        raise _unprocessable(MSG_NAME_REQUIRED)
    if len(name) > MAX_KB_NAME_CHARS:
        raise _unprocessable(MSG_NAME_TOO_LONG)
    try:
        kb_strategy = KbStrategy(strategy)
    except ValueError as exc:
        raise _unprocessable(MSG_BAD_STRATEGY) from exc
    if kb_strategy == KbStrategy.FIXED:
        error = validate_chunk_params(chunk_size, chunk_overlap)
        if error is not None:
            raise _unprocessable(error)
    else:
        chunk_size, chunk_overlap = MAX_EMBED_CHARS, 0
    model = model.strip()
    if not model:
        raise _unprocessable(MSG_MODEL_REQUIRED)
    return name, kb_strategy, chunk_size, chunk_overlap, model


def _clean_source_name(raw: str) -> str:
    """Normalise an uploaded file name into a safe relative source name."""
    text = unicodedata.normalize("NFC", raw).replace("\\", "/")
    text = _CONTROL_CHARS.sub("", text)
    segments = [seg.strip() for seg in text.split("/")]
    segments = [seg for seg in segments if seg and seg not in (".", "..")]
    if segments and _DRIVE_SEGMENT.match(segments[0]):
        segments = segments[1:]
    return "/".join(segments)


def _clean_source_names(files: list[UploadFile] | None) -> list[str]:
    """Validate the file list and return sanitised source names (relative paths)."""
    if not files:
        raise _unprocessable(MSG_FILES_REQUIRED)
    if len(files) > MAX_FILES:
        raise _unprocessable(MSG_TOO_MANY_FILES)
    names: list[str] = []
    seen: set[str] = set()
    for upload in files:
        # Display identity only; the file is stored under a server-generated name.
        source = _clean_source_name(upload.filename or "")
        if Path(source).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise _unprocessable(MSG_BAD_EXTENSION.format(name=source or "без имени"))
        if len(source) > MAX_SOURCE_NAME_CHARS:
            raise _unprocessable(MSG_SOURCE_TOO_LONG.format(name=source[:60] + "…"))
        if source in seen:
            raise _unprocessable(MSG_DUPLICATE.format(name=source))
        seen.add(source)
        names.append(source)
    return names


def _write_chunk(handle: BinaryIO, data: bytes) -> None:
    """Write one chunk to an open file (worker thread)."""
    handle.write(data)


async def _store_upload(
    upload: UploadFile, name: str, target: Path, total_so_far: int
) -> tuple[str, int, str]:
    """Stream one upload to disk; returns (stored_name, size, sha256)."""
    digest = hashlib.sha256()
    size = 0
    tmp_path = target / f"{uuid4().hex}.part"
    try:
        handle = await asyncio.to_thread(tmp_path.open, "wb")
        try:
            while True:
                data = await upload.read(READ_CHUNK_BYTES)
                if not data:
                    break
                size += len(data)
                if size > MAX_FILE_BYTES:
                    raise UploadRejectedError(MSG_FILE_TOO_LARGE.format(name=name))
                if total_so_far + size > MAX_TOTAL_BYTES:
                    raise UploadRejectedError(MSG_TOTAL_TOO_LARGE)
                digest.update(data)
                await asyncio.to_thread(_write_chunk, handle, data)
        finally:
            await asyncio.to_thread(handle.close)
        if size == 0:
            raise UploadRejectedError(MSG_EMPTY_FILE.format(name=name))
        stored = f"{uuid4().hex}{Path(name).suffix.lower()}"
        await asyncio.to_thread(tmp_path.replace, target / stored)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return stored, size, digest.hexdigest()


async def _store_all(
    files: list[UploadFile], names: list[str], target: Path
) -> list[tuple[str, str, int, str]]:
    """Stream all uploads into target; returns (source, stored_name, size, sha256) per file."""
    await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
    stored_rows: list[tuple[str, str, int, str]] = []
    total = 0
    for upload, name in zip(files, names):
        stored, size, sha = await _store_upload(upload, name, target, total)
        total += size
        stored_rows.append((name, stored, size, sha))
    return stored_rows


async def _classify_additions(
    session: AsyncSession,
    kb_id: int,
    staging: Path,
    stored: list[tuple[str, str, int, str]],
) -> tuple[list[kb_indexer.AddItem], int]:
    """Split staged files into added, replaced and unchanged by source name and hash."""
    rows = await session.exec(
        select(KbDocument.id, KbDocument.filename, KbDocument.sha256).where(
            KbDocument.kb_id == kb_id
        )
    )
    groups: dict[str, list[tuple[int, str]]] = {}
    for doc_id, filename, sha in rows.all():
        groups.setdefault(filename, []).append((doc_id, sha))
    items: list[kb_indexer.AddItem] = []
    unchanged = 0
    for source, stored_name, size, sha in stored:
        group = groups.get(source, [])
        if len(group) == 1 and group[0][1] == sha:
            unchanged += 1
            await asyncio.to_thread((staging / stored_name).unlink, missing_ok=True)
            continue
        items.append(
            kb_indexer.AddItem(
                source=source,
                stored_name=stored_name,
                sha256=sha,
                size_bytes=size,
                replaces=tuple(sorted(doc_id for doc_id, _ in group)),
            )
        )
    return items, unchanged


async def _claim_kb_for_add(session: AsyncSession, kb_id: int) -> bool:
    """Atomically mark a ready, idle base as being added to; False when another request won."""
    try:
        result = await session.exec(
            update(KnowledgeBase)
            .where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.status == KbStatus.READY,
                KnowledgeBase.phase.is_(None),
            )
            .values(
                phase=PHASE_ADDING,
                error=None,
                done_chunks=0,
                total_chunks=0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return result.rowcount == 1


async def _release_add_claim(kb_id: int) -> None:
    """Clear the addition marker when a claimed request fails before its job starts."""
    async with async_session_factory() as session:
        try:
            await session.exec(
                update(KnowledgeBase)
                .where(KnowledgeBase.id == kb_id, KnowledgeBase.phase == PHASE_ADDING)
                .values(phase=None)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@router.get("/embedding-models")
async def embedding_models_route(
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List LM Studio models with type, loaded state and embedding eligibility."""
    try:
        return await list_embedding_models()
    except EmbeddingError as exc:
        raise _embedding_http_error(exc) from exc


@router.get("/limits")
async def limits_route(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Return the upload limits so the page never hard-codes them."""
    return {
        "max_files": MAX_FILES,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "max_source_name_chars": MAX_SOURCE_NAME_CHARS,
        "extensions": sorted(ALLOWED_EXTENSIONS),
    }


@router.post(
    "/embedding-check",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def embedding_check_route(
    body: EmbeddingCheckRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the indexing guard on a model and report its vector dimension."""
    model = body.model.strip()
    try:
        await ensure_embedding_model(model)
        vector = await embed_query(model, CHECK_PROBE_TEXT)
    except EmbeddingError as exc:
        raise _embedding_http_error(exc) from exc
    return {"model": model, "dim": len(vector)}


@router.get("", response_model=list[KbOut])
async def list_kbs(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[KbOut]:
    """List the current user's knowledge bases, newest first."""
    rows = await session.exec(
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == current_user.id)
        .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
    )
    return [kb_out(kb) for kb in rows.all()]


@router.post(
    "",
    response_model=KbOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_allowed_origin), Depends(_require_content_length_within_cap)],
)
async def create_kb(
    name: str = Form(""),
    strategy: str = Form("fixed"),
    chunk_size: int = Form(DEFAULT_CHUNK_SIZE),
    chunk_overlap: int = Form(DEFAULT_CHUNK_OVERLAP),
    embedding_model: str = Form(""),
    files: list[UploadFile] | None = File(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KbOut:
    """Validate and store the uploads, then queue background indexing (202)."""
    try:
        name, kb_strategy, size, overlap, model = _validate_settings(
            name, strategy, chunk_size, chunk_overlap, embedding_model
        )
        names = _clean_source_names(files)
    except HTTPException as exc:
        logger.warning("kb_create_rejected", user_id=current_user.id, status=exc.status_code)
        raise
    user_id = current_user.id
    kb_id: int | None = None
    kb = KnowledgeBase(
        user_id=user_id,
        name=name,
        status=KbStatus.QUEUED,
        strategy=kb_strategy,
        chunk_size=size,
        chunk_overlap=overlap,
        embedding_model=model,
        file_count=len(names),
    )
    session.add(kb)
    try:
        await session.flush()
        kb_id = kb.id
        stored = await _store_all(files or [], names, uploads_dir(user_id, kb_id))
        for filename, stored_name, file_size, sha in stored:
            session.add(
                KbDocument(
                    kb_id=kb_id,
                    filename=filename,
                    sha256=sha,
                    size_bytes=file_size,
                    stored_name=stored_name,
                )
            )
        await session.commit()
    except UploadRejectedError as exc:
        await session.rollback()
        if kb_id is not None:
            await asyncio.to_thread(remove_kb_dir, user_id, kb_id)
        logger.warning("kb_create_rejected", user_id=user_id, reason="upload_rule")
        raise _unprocessable(exc.message) from exc
    except BaseException:
        # BaseException so a client disconnect (CancelledError) still cleans up files.
        await asyncio.shield(session.rollback())
        if kb_id is not None:
            remove_kb_dir(user_id, kb_id)
        raise
    await session.refresh(kb)
    kb_indexer.spawn_index_job(kb.id, user_id)
    hub.publish(user_id, kb_progress_frame(kb))
    logger.info(
        "kb_created",
        kb_id=kb.id,
        user_id=user_id,
        file_count=kb.file_count,
        strategy=kb_strategy.value,
    )
    return kb_out(kb)


@router.get("/{kb_id}", response_model=KbOut)
async def get_kb_route(
    kb_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KbOut:
    """Return one of the current user's knowledge bases."""
    return kb_out(await _get_owned_kb(session, kb_id, current_user.id))


@router.delete(
    "/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_kb_route(
    kb_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Delete a knowledge base in any status, cancelling a running job."""
    kb = await _get_owned_kb(session, kb_id, current_user.id)
    await kb_indexer.delete_kb(session, kb)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{kb_id}/files",
    response_model=KbOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_allowed_origin), Depends(_require_content_length_within_cap)],
)
async def add_files_route(
    kb_id: int,
    files: list[UploadFile] | None = File(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KbOut:
    """Validate and stage the uploads, classify them against the base and queue the addition (202)."""
    user_id = current_user.id
    kb = await _get_owned_kb(session, kb_id, user_id)
    if kb.status != KbStatus.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_ADD_NOT_READY)
    if kb.phase is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_ADD_BUSY)
    try:
        names = _clean_source_names(files)
    except HTTPException as exc:
        logger.warning("kb_add_rejected", kb_id=kb_id, user_id=user_id, status=exc.status_code)
        raise
    staging: Path | None = None
    claimed = False
    try:
        staging = await asyncio.to_thread(new_staging_dir, user_id, kb_id)
        stored = await _store_all(files or [], names, staging)
        items, unchanged = await _classify_additions(session, kb_id, staging, stored)
        claimed = await _claim_kb_for_add(session, kb_id)
        if not claimed:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_ADD_BUSY)
        await session.refresh(kb)
        kb_indexer.spawn_add_job(kb.id, user_id, staging, items, unchanged)
    except UploadRejectedError as exc:
        await session.rollback()
        if staging is not None:
            await asyncio.to_thread(remove_tree, staging)
        logger.warning("kb_add_rejected", kb_id=kb_id, user_id=user_id, reason="upload_rule")
        raise _unprocessable(exc.message) from exc
    except HTTPException:
        if staging is not None:
            remove_tree(staging)
        raise
    except BaseException:
        # BaseException so a client disconnect (CancelledError) still cleans up.
        await asyncio.shield(session.rollback())
        if staging is not None:
            remove_tree(staging)
        if claimed:
            await asyncio.shield(_release_add_claim(kb_id))
        raise
    hub.publish(user_id, kb_progress_frame(kb))
    logger.info(
        "kb_add_accepted",
        kb_id=kb_id,
        user_id=user_id,
        added=sum(1 for item in items if not item.replaces),
        replaced=sum(1 for item in items if item.replaces),
        unchanged=unchanged,
    )
    return kb_out(kb)


@router.post(
    "/{kb_id}/search",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def search_route(
    kb_id: int,
    body: KbSearchRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, list[dict[str, Any]]]:
    """Run a test search against a ready knowledge base."""
    kb = await _get_owned_kb(session, kb_id, current_user.id)
    query = body.query.strip()
    if not query:
        raise _unprocessable("Введите поисковый запрос.")
    try:
        results = await search_kb(session, kb, query, body.top_k)
    except KbNotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_NOT_READY) from exc
    except KbIndexCorruptError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_INDEX_CORRUPT) from exc
    except EmbeddingError as exc:
        raise _embedding_http_error(exc) from exc
    return {"results": results}
