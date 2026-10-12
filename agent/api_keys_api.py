"""REST routes for per-user API keys (cookie session only)."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import get_session_user, require_allowed_origin, require_json_content_type
from agent.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from shared.auth import api_key_display_prefix, generate_api_key, hash_api_key
from shared.database import get_session
from shared.logger import get_logger
from shared.models import ApiKey, User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

API_KEY_MAX_PER_USER = 20
MSG_API_KEY_LIMIT = "Достигнут лимит: 20 активных ключей. Отзовите ненужный ключ, чтобы создать новый."
MSG_API_KEY_NOT_FOUND = "Ключ не найден"


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_api_key(
    body: ApiKeyCreate,
    response: Response,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_session_user),
) -> ApiKeyCreated:
    """Create a key; the full key is returned only in this response."""
    result = await session.exec(
        select(func.count()).select_from(ApiKey).where(ApiKey.user_id == current_user.id),
    )
    if result.one() >= API_KEY_MAX_PER_USER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_API_KEY_LIMIT)

    key = generate_api_key()
    row = ApiKey(
        user_id=current_user.id,
        name=body.name,
        key_hash=hash_api_key(key),
        key_prefix=api_key_display_prefix(key),
    )
    session.add(row)
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    await session.refresh(row)

    response.headers["Cache-Control"] = "no-store"
    logger.info("api_key_created", key_id=row.id, user_id=current_user.id)
    return ApiKeyCreated(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        key=key,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_session_user),
) -> list[ApiKey]:
    """List the caller's keys, newest first (metadata only)."""
    result = await session.exec(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc()),
    )
    return list(result.all())


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def revoke_api_key(
    key_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_session_user),
) -> None:
    """Revoke a key by deleting its row; a missing or foreign key is a 404."""
    row = await session.get(ApiKey, key_id)
    if row is None or row.user_id != current_user.id:
        logger.warning("api_key_access_denied", key_id=key_id, user_id=current_user.id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MSG_API_KEY_NOT_FOUND)

    await session.delete(row)
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    logger.info("api_key_revoked", key_id=key_id, user_id=current_user.id)
