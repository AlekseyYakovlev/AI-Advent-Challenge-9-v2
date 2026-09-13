"""Async SQLite database engine, session factory, and helpers."""

import asyncio
import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.config import settings
from shared.models import Chat, Message, Settings, TokenUsage  # noqa: F401

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


async def init_db() -> None:
    """Create all database tables if they do not exist."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
