"""Thin CRUD layer owning all reads and writes to the invariant tables."""

from datetime import datetime, timezone
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import ChatInvariant, GlobalInvariant

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


async def list_chat_invariants(session: AsyncSession, chat_id: int) -> list[ChatInvariant]:
    """Return this chat's invariants, oldest first."""
    result = await session.exec(
        select(ChatInvariant)
        .where(ChatInvariant.chat_id == chat_id)
        .order_by(ChatInvariant.created_at, ChatInvariant.id),
    )
    return list(result.all())


async def get_chat_invariant(session: AsyncSession, invariant_id: int) -> ChatInvariant | None:
    """Return a single per-chat invariant by id, or None if it does not exist."""
    return await session.get(ChatInvariant, invariant_id)


async def create_chat_invariant(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    title: str,
    rule_text: str,
    overrides_id: int | None,
) -> ChatInvariant:
    """Create a new per-chat invariant, optionally overriding a global one (D-05)."""
    row = ChatInvariant(
        user_id=user_id,
        chat_id=chat_id,
        title=title,
        rule_text=rule_text,
        overrides_id=overrides_id,
    )
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_created", scope="chat", invariant_id=row.id, chat_id=chat_id)
    return row


async def update_chat_invariant(
    session: AsyncSession,
    invariant_id: int,
    **updates: Any,
) -> ChatInvariant | None:
    """Update the supplied fields on a per-chat invariant, or return None if missing."""
    row = await get_chat_invariant(session, invariant_id)
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
    logger.info("invariant_updated", scope="chat", invariant_id=invariant_id)
    return row


async def delete_chat_invariant(session: AsyncSession, invariant_id: int) -> bool:
    """Delete a per-chat invariant, returning False if it does not exist."""
    row = await get_chat_invariant(session, invariant_id)
    if row is None:
        return False
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_deleted", scope="chat", invariant_id=invariant_id)
    return True


async def resolve_active_invariants(session: AsyncSession, chat_id: int) -> list[dict[str, Any]]:
    """Return this chat's fully resolved, override-labelled active invariant set (D-05/D-06).

    The single source of truth consumed by both build_system_prompt (INV-03) and the
    05-03 self-critique prompt builder — read-only, never writes or commits.
    """
    globals_ = await list_global(session)
    chat_rows = await list_chat_invariants(session, chat_id)

    override_map: dict[int, ChatInvariant] = {}
    standalone: list[ChatInvariant] = []
    for row in chat_rows:
        if row.overrides_id is not None and row.overrides_id not in override_map:
            override_map[row.overrides_id] = row
        else:
            standalone.append(row)

    active: list[dict[str, Any]] = []
    for g in globals_:
        overriding = override_map.get(g.id)
        active.append(
            {
                "scope": "global",
                "id": g.id,
                "title": g.title,
                "rule_text": g.rule_text,
                "overridden_by": (
                    None
                    if overriding is None
                    else {
                        "id": overriding.id,
                        "title": overriding.title,
                        "rule_text": overriding.rule_text,
                    }
                ),
            },
        )
    for c in standalone:
        active.append(
            {
                "scope": "chat",
                "id": c.id,
                "title": c.title,
                "rule_text": c.rule_text,
                "overridden_by": None,
            },
        )
    return active
