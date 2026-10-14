"""REST routes of the automated PR review: the review itself, its settings and the code index."""

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import code_git, code_index, mcp_config, providers, review_service
from agent.dependencies import (
    AUTH_METHOD_API_KEY,
    get_current_user,
    get_session_user,
    require_allowed_origin,
    require_json_content_type,
)
from agent.kb_api import _get_owned_kb, _unprocessable
from agent.rag_api import MSG_KB_NOT_READY
from agent.review_service import ReviewServiceError
from shared.database import get_session
from shared.logger import get_logger
from shared.models import KbStatus, KnowledgeBase, ReviewCodeIndex, ReviewConfig, User

logger = get_logger(__name__)

router = APIRouter(tags=["review"])

CODE_REVIEW_BUSY = "REVIEW_BUSY"
CODE_INDEX_BUSY = "REVIEW_INDEX_BUSY"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
MSG_REVIEW_BUSY = "Ревью этого Pull Request уже выполняется."
MSG_INDEX_BUSY = "Индекс кода уже обновляется."
MSG_INTERNAL = "Внутренняя ошибка при выполнении ревью."
MSG_BAD_REPO = "Не удалось прочитать репозиторий: {reason} Проверьте путь и повторите."
MSG_SAVE_FIRST = "Сначала укажите путь и сохраните настройки."
MSG_NO_BRANCH = "Не удалось определить ветку для индексации."
MSG_PROVIDER_NOT_FOUND = "Провайдер не найден"
MSG_SERVER_NOT_FOUND = "MCP-сервер не найден"

REPO_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
HEAD_SHA_PATTERN = r"^([0-9a-fA-F]{40})?$"
MIN_CONTEXT_LENGTH = 8192
MAX_CONTEXT_LENGTH = 262144

STATUS_BY_CODE: dict[str, int] = {
    "REVIEW_NOT_CONFIGURED": 409,
    "GITHUB_MCP_UNAVAILABLE": 503,
    "PR_NOT_FOUND": 404,
    "BASE_UNAVAILABLE": 503,
    "EMBEDDER_UNAVAILABLE": 503,
    "LM_STUDIO_UNAVAILABLE": 503,
    "LLM_UNREACHABLE": 503,
    "CODE_INDEX_UNAVAILABLE": 503,
    "LLM_ERROR": 502,
    "REVIEW_BAD_OUTPUT": 502,
    "PR_TOO_LARGE": 422,
}
DEFAULT_ERROR_STATUS = 502

_review_locks: dict[tuple[int, str, int], asyncio.Lock] = {}
# Strong references: the event loop keeps only weak ones to running tasks.
_review_tasks: set[asyncio.Task[Any]] = set()


class ReviewRequest(BaseModel):
    """Body of a review request; the repository path never comes from the caller."""

    model_config = ConfigDict(extra="forbid")

    repo: str = Field(max_length=200, pattern=REPO_PATTERN)
    pr_number: int = Field(ge=1)
    head_sha: str = Field(default="", pattern=HEAD_SHA_PATTERN)


class ReviewConfigIn(BaseModel):
    """Body of a settings update; an omitted field stays, an explicit null clears it."""

    model_config = ConfigDict(extra="forbid")

    repo_path: str | None = Field(default=None, max_length=1000)
    docs_kb_id: int | None = Field(default=None, ge=1)
    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, max_length=200)
    github_server_id: int | None = Field(default=None, ge=1)
    context_length: int | None = Field(
        default=None, ge=MIN_CONTEXT_LENGTH, le=MAX_CONTEXT_LENGTH
    )


class RefreshIn(BaseModel):
    """Optional body of an index refresh request."""

    model_config = ConfigDict(extra="forbid")

    base_ref: str | None = Field(default=None, max_length=255)


def _error_response(status_code: int, detail: str, code: str) -> JSONResponse:
    """Build the uniform error body; a JSONResponse keeps `code` that HTTPException drops."""
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


async def _run_review(
    lock: asyncio.Lock, key: tuple[int, str, int], user_id: int, body: ReviewRequest
) -> JSONResponse | dict[str, Any]:
    """Produce the review while owning the acquired per-PR lock, releasing it at the end."""
    try:
        return await review_service.produce_review(
            user_id, body.repo, body.pr_number, body.head_sha
        )
    except ReviewServiceError as exc:
        return _error_response(
            STATUS_BY_CODE.get(exc.code, DEFAULT_ERROR_STATUS), exc.detail, exc.code
        )
    except Exception as exc:  # noqa: BLE001 - the single outer boundary of the route
        logger.error("review_failed", user_id=user_id, error_type=type(exc).__name__)
        return _error_response(500, MSG_INTERNAL, CODE_INTERNAL_ERROR)
    finally:
        lock.release()
        if _review_locks.get(key) is lock:
            _review_locks.pop(key, None)


@router.post(
    "/api/v1/reviews",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def post_review(
    body: ReviewRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    """Produce the three-section review of one Pull Request for the key holder."""
    key = (current_user.id, body.repo.lower(), body.pr_number)
    lock = _review_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        return _error_response(409, MSG_REVIEW_BUSY, CODE_REVIEW_BUSY)
    await lock.acquire()
    task = asyncio.create_task(_run_review(lock, key, current_user.id, body))
    _review_tasks.add(task)
    task.add_done_callback(_review_tasks.discard)
    return await asyncio.shield(task)


def _iso(value: datetime | None) -> str | None:
    """Format a timestamp as an ISO string, treating naive values as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


async def _code_index_out(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    """Describe the user's most recently updated code base, or None."""
    link = await code_index.latest_index(session, user_id)
    if link is None:
        return None
    kb = await session.get(KnowledgeBase, link.kb_id)
    return {
        "base_ref": link.base_ref,
        "indexed_commit": link.indexed_commit,
        "file_count": kb.file_count if kb is not None else 0,
        "chunk_count": kb.chunk_count if kb is not None else 0,
        "updated_at": _iso(link.updated_at),
    }


async def _effective_out(
    session: AsyncSession, user_id: int, row: ReviewConfig | None
) -> dict[str, Any]:
    """Resolve the values a review would use right now without raising."""
    kb, source = await review_service.resolve_docs_kb(session, user_id, row)
    provider, model = await review_service.resolve_provider_and_model(
        session, user_id, row, strict=False
    )
    return {
        "docs_kb_id": kb.id if kb is not None else None,
        "docs_kb_name": kb.name if kb is not None else None,
        "docs_kb_source": source,
        "provider_id": provider.id if provider is not None else None,
        "provider_name": provider.name if provider is not None else None,
        "provider_kind": provider.kind if provider is not None else None,
        "model": model,
    }


async def _config_out(
    session: AsyncSession, user_id: int, row: ReviewConfig | None, include_path: bool
) -> dict[str, Any]:
    """Build the settings reply; the repository path only goes to a session caller."""
    out: dict[str, Any] = {"repo_configured": bool(row is not None and row.repo_path)}
    if include_path:
        out["repo_path"] = row.repo_path if row is not None else None
    out.update(
        docs_kb_id=row.docs_kb_id if row is not None else None,
        provider_id=row.provider_id if row is not None else None,
        model=row.model if row is not None else None,
        github_server_id=row.github_server_id if row is not None else None,
        context_length=(
            row.context_length if row is not None else review_service.DEFAULT_CONTEXT_LENGTH
        ),
        updated_at=_iso(row.updated_at) if row is not None else None,
        effective=await _effective_out(session, user_id, row),
        code_index=await _code_index_out(session, user_id),
        refresh=asdict(code_index.get_refresh_progress(user_id)),
    )
    return out


def _is_api_key_caller(request: Request) -> bool:
    """Whether the caller was authenticated by an API key rather than the session cookie."""
    return getattr(request.state, "auth_method", None) == AUTH_METHOD_API_KEY


@router.get("/api/v1/reviews/config")
async def get_review_config(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the caller's review settings, effective values and code index state."""
    row = await review_service.load_review_config(session, current_user.id)
    return await _config_out(
        session, current_user.id, row, include_path=not _is_api_key_caller(request)
    )


async def _validate_updates(
    session: AsyncSession, user_id: int, body: ReviewConfigIn
) -> str | None:
    """Check ownership and readiness of the sent ids; return the normalised repo path."""
    sent = body.model_fields_set
    top: str | None = None
    if "repo_path" in sent and body.repo_path is not None:
        try:
            top = await code_git.validate_repo_path(body.repo_path)
        except code_git.GitError as exc:
            raise _unprocessable(MSG_BAD_REPO.format(reason=exc.detail)) from exc
    if body.docs_kb_id is not None:
        kb = await _get_owned_kb(session, body.docs_kb_id, user_id)
        if kb.status != KbStatus.READY:
            raise _unprocessable(MSG_KB_NOT_READY)
    if body.provider_id is not None:
        if await providers.get_provider(session, user_id, body.provider_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=MSG_PROVIDER_NOT_FOUND)
    if body.github_server_id is not None:
        if await mcp_config.get_server(session, user_id, body.github_server_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=MSG_SERVER_NOT_FOUND)
    return top


def _apply_updates(row: ReviewConfig, body: ReviewConfigIn, top: str | None) -> list[str]:
    """Apply the sent fields to the row and return their names."""
    sent = body.model_fields_set
    changed: list[str] = []
    if "repo_path" in sent:
        row.repo_path = top
        changed.append("repo_path")
    for name in ("docs_kb_id", "provider_id", "model", "github_server_id"):
        if name in sent:
            setattr(row, name, getattr(body, name))
            changed.append(name)
    if "context_length" in sent:
        row.context_length = (
            body.context_length
            if body.context_length is not None
            else review_service.DEFAULT_CONTEXT_LENGTH
        )
        changed.append("context_length")
    return changed


@router.put(
    "/api/v1/reviews/config",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def put_review_config(
    body: ReviewConfigIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_session_user),
) -> dict[str, Any]:
    """Store the caller's review settings; only the web session may change them."""
    top = await _validate_updates(session, current_user.id, body)
    try:
        row = await session.get(ReviewConfig, current_user.id)
        if row is None:
            row = ReviewConfig(user_id=current_user.id)
            session.add(row)
        changed = _apply_updates(row, body, top)
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
    except SQLAlchemyError:
        await session.rollback()
        raise
    logger.info("review_config_updated", user_id=current_user.id, changed=changed)
    return await _config_out(session, current_user.id, row, include_path=True)


@router.get("/api/v1/reviews/code-index")
async def get_code_index(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the code index state and the refresh progress of the caller."""
    return {
        "code_index": await _code_index_out(session, current_user.id),
        "refresh": asdict(code_index.get_refresh_progress(current_user.id)),
    }


async def _read_refresh_body(request: Request) -> RefreshIn:
    """Parse the optional JSON body; an empty body means no base_ref."""
    raw = await request.body()
    if not raw.strip():
        return RefreshIn()
    await require_json_content_type(request)
    try:
        return RefreshIn.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise _unprocessable(MSG_NO_BRANCH) from exc


async def _resolve_base_ref(
    session: AsyncSession, user_id: int, repo_path: str, requested: str | None
) -> str:
    """Pick the branch to index: the request, the last index, else the local branch."""
    if requested is not None:
        try:
            code_git.check_base_ref(requested)
        except code_git.GitError as exc:
            raise _unprocessable(exc.detail) from exc
        return requested
    link: ReviewCodeIndex | None = await code_index.latest_index(session, user_id)
    if link is not None:
        return link.base_ref
    try:
        branch = await code_git.current_branch(repo_path)
    except code_git.GitError as exc:
        raise _unprocessable(MSG_NO_BRANCH) from exc
    if branch is None:
        raise _unprocessable(MSG_NO_BRANCH)
    return branch


@router.post(
    "/api/v1/reviews/code-index/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_allowed_origin)],
)
async def post_code_index_refresh(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_session_user),
) -> Any:
    """Start a background refresh of the code index for the saved repository."""
    body = await _read_refresh_body(request)
    row = await review_service.load_review_config(session, current_user.id)
    if row is None or not (row.repo_path or "").strip():
        raise _unprocessable(MSG_SAVE_FIRST)
    base_ref = await _resolve_base_ref(session, current_user.id, row.repo_path, body.base_ref)
    docs_kb, _ = await review_service.resolve_docs_kb(session, current_user.id, row)
    started = code_index.spawn_refresh_job(
        current_user.id,
        row.repo_path,
        base_ref,
        review_service.embedding_model_for(docs_kb),
    )
    if not started:
        return _error_response(409, MSG_INDEX_BUSY, CODE_INDEX_BUSY)
    logger.info("review_index_refresh_started", user_id=current_user.id, base_ref=base_ref)
    return {"running": True, "base_ref": base_ref}
