"""Thin CRUD layer owning all reads and writes to the memory tables."""

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import LongTermMemory, WorkingMemory

logger = get_logger(__name__)


async def list_working_memory(session: AsyncSession, chat_id: int) -> list[WorkingMemory]:
    """Return this chat's working memory rows, ordered by key."""
    result = await session.exec(
        select(WorkingMemory).where(WorkingMemory.chat_id == chat_id).order_by(WorkingMemory.key),
    )
    return list(result.all())


async def list_long_term_memory(session: AsyncSession, user_id: int) -> list[LongTermMemory]:
    """Return the user's full long-term memory, ordered by key (cross-chat, D-02)."""
    result = await session.exec(
        select(LongTermMemory).where(LongTermMemory.user_id == user_id).order_by(LongTermMemory.key),
    )
    return list(result.all())


async def save_working_memory(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    key: str,
    value: str,
) -> WorkingMemory:
    """Insert or overwrite the working memory row for (chat_id, key)."""
    result = await session.exec(
        select(WorkingMemory).where(WorkingMemory.chat_id == chat_id, WorkingMemory.key == key),
    )
    row = result.first()
    now = datetime.now(timezone.utc)
    if row is not None:
        row.value = value
        row.updated_at = now
        session.add(row)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await session.refresh(row)
        logger.info("working_memory_saved", chat_id=chat_id, key=key)
        return row

    row = WorkingMemory(user_id=user_id, chat_id=chat_id, key=key, value=value, updated_at=now)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        result = await session.exec(
            select(WorkingMemory).where(WorkingMemory.chat_id == chat_id, WorkingMemory.key == key),
        )
        row = result.first()
        if row is None:
            raise
        row.value = value
        row.updated_at = now
        session.add(row)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    except Exception:
        await session.rollback()
        raise
    await session.refresh(row)
    logger.info("working_memory_saved", chat_id=chat_id, key=key)
    return row


async def save_long_term_memory(
    session: AsyncSession,
    user_id: int,
    key: str,
    value: str,
) -> LongTermMemory:
    """Insert or overwrite the long-term memory row for (user_id, key), preserving created_at."""
    result = await session.exec(
        select(LongTermMemory).where(LongTermMemory.user_id == user_id, LongTermMemory.key == key),
    )
    row = result.first()
    now = datetime.now(timezone.utc)
    if row is not None:
        row.value = value
        row.updated_at = now
        session.add(row)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await session.refresh(row)
        logger.info("long_term_memory_saved", user_id=user_id, key=key)
        return row

    row = LongTermMemory(user_id=user_id, key=key, value=value, created_at=now, updated_at=now)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        result = await session.exec(
            select(LongTermMemory).where(LongTermMemory.user_id == user_id, LongTermMemory.key == key),
        )
        row = result.first()
        if row is None:
            raise
        row.value = value
        row.updated_at = now
        session.add(row)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    except Exception:
        await session.rollback()
        raise
    await session.refresh(row)
    logger.info("long_term_memory_saved", user_id=user_id, key=key)
    return row


class MemoryKeyConflictError(Exception):
    """Raised when a long-term key rename collides with another entry of the same user."""


async def get_long_term_memory(
    session: AsyncSession,
    user_id: int,
    entry_id: int,
) -> LongTermMemory | None:
    """Return the user's long-term entry by id, or None when missing or owned by someone else."""
    result = await session.exec(
        select(LongTermMemory).where(
            LongTermMemory.id == entry_id,
            LongTermMemory.user_id == user_id,
        ),
    )
    return result.first()


async def _long_term_key_taken(
    session: AsyncSession,
    user_id: int,
    key: str,
    exclude_id: int,
) -> bool:
    """Return True when another entry of the user already uses this key."""
    result = await session.exec(
        select(LongTermMemory.id).where(
            LongTermMemory.user_id == user_id,
            LongTermMemory.key == key,
            LongTermMemory.id != exclude_id,
        ),
    )
    return result.first() is not None


async def update_long_term_memory(
    session: AsyncSession,
    user_id: int,
    entry_id: int,
    key: str | None = None,
    value: str | None = None,
) -> LongTermMemory | None:
    """Edit the key and/or value of the user's own entry; None when it does not exist."""
    row = await get_long_term_memory(session, user_id, entry_id)
    if row is None:
        return None
    if key is not None and key != row.key:
        # Check before mutating: a dirty row would be autoflushed into the check query.
        if await _long_term_key_taken(session, user_id, key, entry_id):
            raise MemoryKeyConflictError(key)
        row.key = key
    if value is not None:
        row.value = value
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise MemoryKeyConflictError(key) from None
    except Exception:
        await session.rollback()
        raise
    await session.refresh(row)
    logger.info("long_term_memory_updated", user_id=user_id, entry_id=entry_id)
    return row


async def delete_long_term_memory(session: AsyncSession, user_id: int, entry_id: int) -> bool:
    """Delete the user's own entry; False when it does not exist."""
    row = await get_long_term_memory(session, user_id, entry_id)
    if row is None:
        return False
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("long_term_memory_deleted", user_id=user_id, entry_id=entry_id)
    return True
