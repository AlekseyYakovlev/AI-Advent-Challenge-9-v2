"""Async SQLite database engine, session factory, and helpers."""

import asyncio
import functools
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.auth import hash_password
from shared.config import settings
from shared.logger import get_logger
from shared.models import Chat, Message, Session, Settings, TokenUsage, User  # noqa: F401

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
                "ALTER TABLE settings ADD COLUMN context_length INTEGER DEFAULT 16384",
            ),
        )


async def migrate_add_message_tool_trace(conn: Any) -> None:
    """Add tool_trace column to message when missing (idempotent)."""
    table_check = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='message'",
        ),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(message)"))
    columns = [row[1] for row in result.fetchall()]
    if "tool_trace" not in columns:
        logger.info("migrating_message_add_tool_trace")
        await conn.execute(text("ALTER TABLE message ADD COLUMN tool_trace TEXT"))


async def migrate_add_user_id_columns(conn: Any) -> None:
    """Add nullable user_id columns to chat and settings when missing (idempotent)."""
    for table in ("chat", "settings"):
        table_check = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=:table",
            ),
            {"table": table},
        )
        if table_check.fetchone() is None:
            continue

        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        columns = [row[1] for row in result.fetchall()]
        if "user_id" not in columns:
            logger.info("migrating_add_user_id", table=table)
            await conn.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES user(id)",
                ),
            )


async def migrate_add_task_transition_rejection_columns(conn: Any) -> None:
    """Add rejected/rejection_reason columns to tasktransition when missing (idempotent)."""
    table_check = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='tasktransition'",
        ),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(tasktransition)"))
    columns = [row[1] for row in result.fetchall()]
    if "rejected" not in columns or "rejection_reason" not in columns:
        logger.info("migrating_tasktransition_add_rejection_columns")
        if "rejected" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE tasktransition ADD COLUMN rejected BOOLEAN DEFAULT 0",
                ),
            )
        if "rejection_reason" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE tasktransition ADD COLUMN rejection_reason TEXT",
                ),
            )


async def ensure_bootstrap_admin() -> tuple[str, str] | None:
    """Create a bootstrap admin account when no users exist (D-05)."""
    async with async_session_factory() as session:
        result = await session.exec(select(User))
        if result.first() is not None:
            return None

        username = "admin"
        password = secrets.token_urlsafe(18)
        row = User(username=username, password_hash=hash_password(password))
        session.add(row)
        try:
            await session.commit()
            await session.refresh(row)
        except Exception:
            await session.rollback()
            raise

        logger.info("bootstrap_admin_created", username=username)
        return username, password


async def backfill_user_id(admin_id: int) -> None:
    """Assign every ownerless chat/settings row to the given admin (D-07)."""
    async with engine.begin() as conn:
        for table in ("chat", "settings"):
            result = await conn.execute(
                text(
                    f"UPDATE {table} SET user_id = :admin_id WHERE user_id IS NULL",
                ),
                {"admin_id": admin_id},
            )
            logger.info("backfilled_user_id", table=table, rows=result.rowcount)


async def bootstrap_admin_if_needed() -> tuple[str, str] | None:
    """Run migrations, create the bootstrap admin if needed, and backfill legacy rows."""
    await init_db()
    credentials = await ensure_bootstrap_admin()

    async with async_session_factory() as session:
        if credentials is not None:
            result = await session.exec(
                select(User).where(User.username == credentials[0]),
            )
        else:
            result = await session.exec(select(User).order_by(User.created_at.asc()))
        admin = result.first()

    if admin is None:
        return None

    await backfill_user_id(admin.id)
    return credentials


async def init_db() -> None:
    """Create all database tables if they do not exist."""
    async with engine.begin() as conn:
        await migrate_add_context_length(conn)
        await migrate_add_user_id_columns(conn)
        await migrate_add_task_transition_rejection_columns(conn)
        await migrate_add_message_tool_trace(conn)
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
