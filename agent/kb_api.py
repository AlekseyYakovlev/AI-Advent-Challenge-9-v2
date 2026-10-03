"""REST routes for knowledge bases (/api/v1/kb)."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
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
    MAX_TOTAL_BYTES,
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
from shared.database import get_session
from shared.kb_storage import remove_kb_dir, uploads_dir
from shared.logger import get_logger
from shared.models import KbDocument, KbStatus, KbStrategy, KnowledgeBase, User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])

MSG_KB_NOT_FOUND = "База знаний не найдена"
MSG_NAME_REQUIRED = "Введите название базы знаний."
MSG_NAME_TOO_LONG = "Название не длиннее 200 символов."
MSG_FILES_REQUIRED = "Выберите хотя бы один файл."
MSG_TOO_MANY_FILES = "Можно загрузить не больше 10 файлов."
MSG_BAD_EXTENSION = "Файл {name}: поддерживаются только PDF, TXT и MD."
MSG_FILE_TOO_LARGE = "Файл {name} больше 50 МБ."
MSG_TOTAL_TOO_LARGE = "Общий размер файлов больше 100 МБ."
MSG_EMPTY_FILE = "Файл {name} пустой."
MSG_DUPLICATE = "Файл {name} уже добавлен."
MSG_BAD_STRATEGY = "Неизвестная стратегия разбиения."
MSG_MODEL_REQUIRED = "Выберите модель эмбеддингов."

READ_CHUNK_BYTES = 1024 * 1024
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


def _clean_filenames(files: list[UploadFile] | None) -> list[str]:
    """Validate the file list and return sanitised display names."""
    if not files:
        raise _unprocessable(MSG_FILES_REQUIRED)
    if len(files) > MAX_FILES:
        raise _unprocessable(MSG_TOO_MANY_FILES)
    names: list[str] = []
    for upload in files:
        # Display name only; the file is stored under a server-generated name.
        name = Path((upload.filename or "").replace("\\", "/")).name
        if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise _unprocessable(MSG_BAD_EXTENSION.format(name=name or "без имени"))
        names.append(name)
    return names


def _write_chunk(handle: BinaryIO, data: bytes) -> None:
    """Write one chunk to an open file (worker thread)."""
    handle.write(data)


async def _store_upload(
    upload: UploadFile, name: str, target: Path, index: int, total_so_far: int
) -> tuple[str, int, str]:
    """Stream one upload to disk; returns (stored_name, size, sha256)."""
    digest = hashlib.sha256()
    size = 0
    tmp_path = target / f"{index:02d}.part"
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
    sha = digest.hexdigest()
    stored = f"{index:02d}_{sha[:12]}{Path(name).suffix.lower()}"
    await asyncio.to_thread(tmp_path.replace, target / stored)
    return stored, size, sha


async def _store_all(
    files: list[UploadFile], names: list[str], user_id: int, kb_id: int
) -> list[tuple[str, str, int, str]]:
    """Stream all uploads; returns (filename, stored_name, size, sha256) per file."""
    target = uploads_dir(user_id, kb_id)
    await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
    stored_rows: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()
    total = 0
    for index, (upload, name) in enumerate(zip(files, names)):
        stored, size, sha = await _store_upload(upload, name, target, index, total)
        if sha in seen:
            raise UploadRejectedError(MSG_DUPLICATE.format(name=name))
        seen.add(sha)
        total += size
        stored_rows.append((name, stored, size, sha))
    return stored_rows


@router.get("/embedding-models")
async def embedding_models_route(
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List LM Studio models with type, loaded state and embedding eligibility."""
    try:
        return await list_embedding_models()
    except EmbeddingError as exc:
        raise _embedding_http_error(exc) from exc


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
        names = _clean_filenames(files)
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
        stored = await _store_all(files or [], names, user_id, kb_id)
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
