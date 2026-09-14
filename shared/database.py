"""Async SQLite database engine, session factory, and helpers."""

import asyncio
import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.config import settings
from shared.logger import get_logger
from shared.models import Chat, Message, Settings, TokenUsage  # noqa: F401

logger = get_logger(__name__)

T = TypeVar("T")

DATABASE_URL = f"sqlite+aiosqlite:///{settings.DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
    """Apply WAL mode, busy timeout, and foreign-key enforcement."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def retry_on_locked_db(
    func: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    """Retry async DB operations on SQLite lock errors with exponential backoff."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        last_error: OperationalError | None = None
        for attempt in range(5):
            try:
                return await func(*args, **kwargs)
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last_error = exc
                if attempt < 4:
                    await asyncio.sleep(0.05 * (2 ** attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("retry_on_locked_db exhausted without result")

    return wrapper


async def _migrate_legacy_strategies(conn: Any) -> None:
    """Map removed strategy values to their replacements."""
    await conn.execute(
        text("UPDATE settings SET strategy = 'sliding' WHERE strategy = 'branching'"),
    )


async def migrate_add_context_length(conn: Any) -> None:
    """Add context_length column to settings when missing (idempotent)."""
    table_check = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='settings'",
        ),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(settings)"))
    columns = [row[1] for row in result.fetchall()]
    if "context_length" not in columns:
        logger.info("migrating_settings_add_context_length")
        await conn.execute(
            text(
                "ALTER TABLE settings ADD COLUMN context_length INTEGER DEFAULT 4096",
            ),
        )


async def init_db() -> None:
    """Create all database tables if they do not exist."""
    async with engine.begin() as conn:
        await migrate_add_context_length(conn)
        await conn.run_sync(SQLModel.metadata.create_all)
        await _migrate_legacy_strategies(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
