"""Database engine, WAL mode, retry decorator, and CASCADE delete tests."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from shared.database import async_session_factory, engine, init_db, retry_on_locked_db
from shared.models import Chat, ContextStrategy, Message, Settings, TokenUsage


@pytest.mark.asyncio
async def test_context_strategy_has_four_values() -> None:
    """ContextStrategy enum should expose all four compression strategies."""
    values = {item.value for item in ContextStrategy}
    assert values == {
        "sliding",
        "sticky",
        "truncate_middle",
        "no_compression",
    }


@pytest.mark.asyncio
async def test_init_db_migrates_branching_strategy() -> None:
    """init_db() should rewrite legacy branching strategy to sliding."""
    async with async_session_factory() as session:
        row = Settings(strategy="branching")
        session.add(row)
        await session.commit()
        settings_id = row.id

    await init_db()

    async with async_session_factory() as session:
        migrated = (
            await session.exec(select(Settings).where(Settings.id == settings_id))
        ).one()

    assert migrated.strategy == ContextStrategy.SLIDING_WINDOW.value


@pytest.mark.asyncio
async def test_init_db_creates_all_tables() -> None:
    """init_db() should create chat, message, settings, and tokenusage tables."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ),
        )
        tables = {row[0] for row in result.fetchall()}

    assert tables == {"chat", "message", "settings", "tokenusage"}


@pytest.mark.asyncio
async def test_wal_mode_enabled() -> None:
    """SQLite connection should use WAL journal mode."""
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()

    assert mode is not None
    assert mode.lower() == "wal"


@pytest.mark.asyncio
async def test_retry_on_locked_db_retries_then_succeeds() -> None:
    """retry_on_locked_db should retry on lock errors with exponential backoff."""
    call_count = 0

    @retry_on_locked_db
    async def flaky_operation() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise OperationalError("database is locked", None, None)
        return "ok"

    with patch("shared.database.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await flaky_operation()

    assert result == "ok"
    assert call_count == 3
    assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_retry_on_locked_db_raises_after_max_attempts() -> None:
    """retry_on_locked_db should re-raise after five failed lock attempts."""

    @retry_on_locked_db
    async def always_locked() -> None:
        raise OperationalError("database is locked", None, None)

    with patch("shared.database.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(OperationalError, match="database is locked"):
            await always_locked()


@pytest.mark.asyncio
async def test_cascade_delete_removes_messages_and_token_usage() -> None:
    """Deleting a chat should CASCADE-delete related messages and token usage."""
    from shared.database import async_session_factory

    async with async_session_factory() as session:
        chat = Chat(title="Cascade test")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        message = Message(
            chat_id=chat.id,
            role="user",
            content="Hello",
            token_count=5,
        )
        session.add(message)
        usage = TokenUsage(
            chat_id=chat.id,
            date=datetime.now(timezone.utc),
            prompt_tokens=10,
            completion_tokens=20,
        )
        session.add(usage)
        await session.commit()

        chat_id = chat.id
        message_id = message.id
        usage_id = usage.id

        await session.delete(chat)
        await session.commit()

        remaining_messages = (
            await session.exec(select(Message).where(Message.id == message_id))
        ).first()
        remaining_usage = (
            await session.exec(select(TokenUsage).where(TokenUsage.id == usage_id))
        ).first()
        remaining_chat = (
            await session.exec(select(Chat).where(Chat.id == chat_id))
        ).first()

    assert remaining_messages is None
    assert remaining_usage is None
    assert remaining_chat is None
