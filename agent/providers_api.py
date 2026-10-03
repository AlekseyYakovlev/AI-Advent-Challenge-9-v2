"""REST routes for user-scoped LLM providers (/api/v1/llm-providers)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import providers
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.schemas import (
    LlmProviderCreate,
    LlmProviderOut,
    LlmProviderUpdate,
    ProviderCheckOut,
    ProviderModelGroup,
)
from shared.database import get_session
from shared.logger import get_logger
from shared.models import LlmProvider, User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/llm-providers", tags=["llm-providers"])

MSG_PROVIDER_NOT_FOUND = "Провайдер не найден"


def _check_out(row: LlmProvider) -> ProviderCheckOut:
    """Map the cached check of a provider to its response shape."""
    cached = providers.cached_check(row)
    if cached is None:
        return ProviderCheckOut(status="not_checked")
    if cached.status == "ok":
        return ProviderCheckOut(
            status="ok",
            model_count=len(cached.models),
            checked_at=cached.checked_at,
        )
    return ProviderCheckOut(
        status="error",
        code=cached.code,
        message=cached.message,
        checked_at=cached.checked_at,
    )


def _to_out(row: LlmProvider) -> LlmProviderOut:
    """Serialize a provider; only the key variable name is exposed, never a key value."""
    return LlmProviderOut(
        id=row.id,
        name=row.name,
        base_url=row.base_url,
        kind=row.kind,
        api_key_env=row.api_key_env,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
        check=_check_out(row),
    )


async def _get_or_404(session: AsyncSession, user_id: int, provider_id: int) -> LlmProvider:
    """Return the user's provider or raise 404 (missing and foreign ids alike, never 403)."""
    row = await providers.get_provider(session, user_id, provider_id)
    if row is None:
        logger.warning("llm_provider_access_denied", user_id=user_id, provider_id=provider_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MSG_PROVIDER_NOT_FOUND)
    return row


def _unprocessable(message: str) -> HTTPException:
    """Build the 422 carrying the Russian message the UI shows verbatim."""
    return HTTPException(status_code=422, detail=message)


def _conflict() -> HTTPException:
    """Build the 409 for a duplicate provider name."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=providers.MSG_NAME_TAKEN)


@router.get("", response_model=list[LlmProviderOut])
async def list_llm_providers(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[LlmProviderOut]:
    """List the user's providers, seeding the defaults on first access."""
    await providers.ensure_seeded(session, current_user.id)
    rows = await providers.list_providers(session, current_user.id)
    return [_to_out(row) for row in rows]


@router.get("/models", response_model=list[ProviderModelGroup])
async def list_provider_models(
    refresh: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return one model group per enabled provider; refresh=true re-fetches all of them."""
    return await providers.list_model_groups(session, current_user.id, refresh)


@router.post(
    "",
    response_model=LlmProviderOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_llm_provider(
    body: LlmProviderCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LlmProviderOut:
    """Create an OpenAI-compatible provider."""
    try:
        fields = providers.validate_provider_fields(
            body.name, body.base_url, body.api_key_env, partial=False
        )
        fields["enabled"] = body.enabled
        row = await providers.create_provider(session, current_user.id, fields)
    except providers.ProviderValidationError as exc:
        raise _unprocessable(exc.message) from exc
    except providers.ProviderNameConflictError as exc:
        raise _conflict() from exc
    return _to_out(row)


@router.put(
    "/{provider_id}",
    response_model=LlmProviderOut,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def update_llm_provider(
    provider_id: int,
    body: LlmProviderUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LlmProviderOut:
    """Partially update a provider; an explicit empty api_key_env clears the key reference."""
    row = await _get_or_404(session, current_user.id, provider_id)
    raw = body.model_dump(exclude_unset=True)
    try:
        fields = providers.validate_provider_fields(
            raw.get("name"),
            raw.get("base_url"),
            raw.get("api_key_env", None) if "api_key_env" in raw else None,
            partial=True,
        )
        if "api_key_env" in raw and "api_key_env" not in fields:
            fields["api_key_env"] = None
        if raw.get("enabled") is not None:
            fields["enabled"] = raw["enabled"]
        row = await providers.update_provider(session, row, fields)
    except providers.ProviderValidationError as exc:
        raise _unprocessable(exc.message) from exc
    except providers.ProviderNameConflictError as exc:
        raise _conflict() from exc
    return _to_out(row)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_llm_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Delete a provider; always allowed."""
    row = await _get_or_404(session, current_user.id, provider_id)
    await providers.delete_provider(session, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{provider_id}/check",
    response_model=LlmProviderOut,
    dependencies=[Depends(require_allowed_origin)],
)
async def check_llm_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LlmProviderOut:
    """Run a fresh connection check; failures are reported in the body, not as HTTP errors."""
    row = await _get_or_404(session, current_user.id, provider_id)
    await providers.check_provider(row)
    return _to_out(row)
