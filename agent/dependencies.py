"""FastAPI authentication dependencies for REST and WebSocket handlers."""

from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.state import CORS_ORIGINS
from shared.auth import SESSION_COOKIE_NAME, SESSION_TTL_DAYS, hash_session_token
from shared.database import get_session
from shared.logger import get_logger
from shared.models import Session as SessionRow
from shared.models import User

logger = get_logger(__name__)


async def require_allowed_origin(request: Request) -> None:
    """Reject a browser request whose Origin is not an allowed app origin.

    A missing Origin passes: browsers always send it on cross-origin POST, PUT and DELETE,
    while non-browser clients (curl, tests) do not send it.
    """
    origin = request.headers.get("origin")
    if origin is None or origin in CORS_ORIGINS:
        return
    logger.warning("origin_rejected", origin=origin, path=request.url.path)
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cross-origin request rejected")


async def require_json_content_type(request: Request) -> None:
    """Reject a request body that is not declared as application/json.

    FastAPI would otherwise parse a body with no Content-Type as JSON, which lets a
    cross-origin "simple request" skip the CORS preflight.
    """
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json",
        )


def _as_aware_utc(value: datetime) -> datetime:
    """Normalize a naive SQLite-returned datetime to UTC-aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the session cookie to a User, extending the sliding expiry window."""
    if session_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    result = await db.exec(
        select(SessionRow).where(SessionRow.token_hash == hash_session_token(session_id)),
    )
    row = result.first()
    now = datetime.now(timezone.utc)
    if row is None or _as_aware_utc(row.expires_at) < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    row.expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    db.add(row)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    user = await db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def get_current_user_ws(
    session_id: str | None,
    db: AsyncSession,
) -> User | None:
    """Resolve the session cookie to a User for WebSocket handshakes, never raising."""
    if session_id is None:
        return None

    result = await db.exec(
        select(SessionRow).where(SessionRow.token_hash == hash_session_token(session_id)),
    )
    row = result.first()
    now = datetime.now(timezone.utc)
    if row is None or _as_aware_utc(row.expires_at) < now:
        return None

    row.expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    db.add(row)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return await db.get(User, row.user_id)
