"""Thin CRUD layer owning all reads and writes to the invariant tables."""

from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import GlobalInvariant

logger = get_logger(__name__)


async def list_global(session: AsyncSession) -> list[GlobalInvariant]:
    """Return every global invariant, oldest first. Never filtered by owner — D-02."""
    result = await session.exec(
        select(GlobalInvariant).order_by(GlobalInvariant.created_at, GlobalInvariant.id),
    )
    return list(result.all())


async def get_global(session: AsyncSession, invariant_id: int) -> GlobalInvariant | None:
    """Return a single global invariant by id, or None if it does not exist."""
    return await session.get(GlobalInvariant, invariant_id)


async def create_global(
    session: AsyncSession,
    title: str,
    rule_text: str,
) -> GlobalInvariant:
    """Create a new global invariant, shared across every account (D-02)."""
    row = GlobalInvariant(title=title, rule_text=rule_text)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_created", scope="global", invariant_id=row.id)
    return row


async def update_global(
    session: AsyncSession,
    invariant_id: int,
    **updates: str,
) -> GlobalInvariant | None:
    """Update the supplied fields on a global invariant, or return None if missing."""
    row = await get_global(session, invariant_id)
    if row is None:
        return None
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_updated", scope="global", invariant_id=invariant_id)
    return row


async def delete_global(session: AsyncSession, invariant_id: int) -> bool:
    """Delete a global invariant, returning False if it does not exist."""
    row = await get_global(session, invariant_id)
    if row is None:
        return False
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_deleted", scope="global", invariant_id=invariant_id)
    return True
