"""Thin CRUD layer owning all reads and writes to the profile table."""

from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import Profile

logger = get_logger(__name__)


async def get_profile(session: AsyncSession, user_id: int) -> Profile | None:
    """Return the caller's profile row, or None if it has never been created."""
    result = await session.exec(select(Profile).where(Profile.user_id == user_id))
    return result.first()


async def get_or_create_profile(session: AsyncSession, user_id: int) -> Profile:
    """Return the caller's profile row, creating an empty-field row on first access."""
    row = await get_profile(session, user_id)
    if row is not None:
        return row
    row = Profile(user_id=user_id)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("profile_created", user_id=user_id)
    return row


async def update_profile(
    session: AsyncSession,
    user_id: int,
    style: str | None = None,
    format: str | None = None,
    constraints: str | None = None,
) -> Profile:
    """Update the caller's profile fields (partial), creating the row if missing."""
    row = await get_or_create_profile(session, user_id)
    if style is not None:
        row.style = style
    if format is not None:
        row.format = format
    if constraints is not None:
        row.constraints = constraints
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("profile_updated", user_id=user_id)
    return row
