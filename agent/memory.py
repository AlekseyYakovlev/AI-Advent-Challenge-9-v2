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
