"""FastAPI authentication dependencies for REST and WebSocket handlers."""

from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    hash_api_key,
    hash_session_token,
)
from shared.database import get_session
from shared.logger import get_logger
from shared.models import ApiKey
from shared.models import Session as SessionRow
from shared.models import User
from shared.origins import is_allowed_origin

logger = get_logger(__name__)

AUTH_METHOD_SESSION = "session"
AUTH_METHOD_API_KEY = "api_key"
API_KEY_LAST_USED_THROTTLE_SECONDS = 60
MSG_INVALID_API_KEY = "Invalid or revoked API key"
MSG_SESSION_ONLY = "Эта операция доступна только из веб-интерфейса (по сессии), а не по API-ключу"


async def require_allowed_origin(request: Request) -> None:
    """Reject a browser request whose Origin is not an allowed app origin.

    A missing Origin passes: browsers always send it on cross-origin POST, PUT and DELETE,
    while non-browser clients (curl, tests) do not send it.
    """
    origin = request.headers.get("origin")
    if origin is None or is_allowed_origin(origin, request.headers.get("host")):
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


def _bearer_token(header: str | None) -> str | None:
    """Return the Bearer token (possibly empty) or None when the header is not a Bearer one."""
    if header is None:
        return None
    parts = header.strip().split(None, 1)
    if not parts or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() if len(parts) > 1 else ""


async def _user_from_api_key(db: AsyncSession, token: str, path: str) -> User:
    """Resolve an API key to its owner on every call (no cache, so a revoked key dies at once)."""
    invalid = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail=MSG_INVALID_API_KEY,
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        logger.warning("api_key_rejected", path=path)
        raise invalid

    result = await db.exec(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token)))
    row = result.first()
    user = await db.get(User, row.user_id) if row is not None else None
    if row is None or user is None:
        logger.warning("api_key_rejected", path=path)
        raise invalid

    now = datetime.now(timezone.utc)
    last_used = _as_aware_utc(row.last_used_at) if row.last_used_at is not None else None
    if last_used is None or (now - last_used).total_seconds() > API_KEY_LAST_USED_THROTTLE_SECONDS:
        row.last_used_at = now
        db.add(row)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return user


async def get_current_user(
    request: Request,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the caller to a User.

    An Authorization: Bearer header alone decides (API key, no cookie fallback). Without it the
    session cookie is resolved, extending the sliding expiry window.
    """
    bearer = _bearer_token(request.headers.get("authorization"))
    if bearer is not None:
        user = await _user_from_api_key(db, bearer, request.url.path)
        request.state.auth_method = AUTH_METHOD_API_KEY
        return user

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
    request.state.auth_method = AUTH_METHOD_SESSION
    return user


async def get_session_user(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Like get_current_user, but refuse a caller authenticated by an API key."""
    if getattr(request.state, "auth_method", None) == AUTH_METHOD_API_KEY:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=MSG_SESSION_ONLY)
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
