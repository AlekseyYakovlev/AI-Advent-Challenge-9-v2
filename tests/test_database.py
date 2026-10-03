"""Database engine, WAL mode, retry decorator, and CASCADE delete tests."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from shared.database import (
    async_session_factory,
    engine,
    ensure_kb_chunk_fts,
    init_db,
    migrate_add_chatragconfig_rank_columns,
    migrate_add_message_rag_sources,
    migrate_add_task_transition_rejection_columns,
    retry_on_locked_db,
)
from kb_helpers import seed_kb, seed_user
from shared.models import Chat, ContextStrategy, KbChunk, KbDocument, Message, Settings, TokenUsage


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
    """init_db() should create chat, message, settings, tokenusage, user, session, memory, profile, task, and invariant tables."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ),
        )
        tables = {
            row[0]
            for row in result.fetchall()
            if not row[0].startswith("kb_chunk_fts")
        }

    assert tables == {
        "chat",
        "message",
        "settings",
        "tokenusage",
        "user",
        "session",
        "workingmemory",
        "longtermmemory",
        "profile",
        "task",
        "tasktransition",
        "globalinvariant",
        "chatinvariant",
        "invariantconflict",
        "mcpserverconfig",
        "scheduledtask",
        "taskrun",
        "llmprovider",
        "llmproviderseed",
        "knowledgebase",
        "kbdocument",
        "kbchunk",
        "chatragconfig",
    }


@pytest.mark.asyncio
async def test_migrate_add_task_transition_rejection_columns_is_idempotent() -> None:
    """Running the tasktransition rejection-column migration twice raises nothing."""
    async with engine.begin() as conn:
        await migrate_add_task_transition_rejection_columns(conn)
        await migrate_add_task_transition_rejection_columns(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA table_info(tasktransition)"))
        columns = [row[1] for row in result.fetchall()]

    assert columns.count("rejected") == 1
    assert columns.count("rejection_reason") == 1


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


@pytest.mark.asyncio
async def test_migrate_add_message_rag_sources_is_idempotent(tmp_path) -> None:
    """The rag_sources migration adds the column once to a legacy message table."""
    from sqlalchemy.ext.asyncio import create_async_engine

    legacy = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with legacy.begin() as conn:
        await conn.execute(text("CREATE TABLE message (id INTEGER PRIMARY KEY, content TEXT)"))
        await conn.execute(text("INSERT INTO message (content) VALUES ('old')"))
        await migrate_add_message_rag_sources(conn)
        await migrate_add_message_rag_sources(conn)
    async with legacy.connect() as conn:
        columns = [row[1] for row in (await conn.execute(text("PRAGMA table_info(message)"))).fetchall()]
        legacy_value = (await conn.execute(text("SELECT rag_sources FROM message"))).scalar()
    await legacy.dispose()

    assert columns.count("rag_sources") == 1
    assert legacy_value is None


_RANK_COLUMNS = ("candidate_k", "threshold", "lexical", "llm_rerank", "hybrid", "rewrite")


@pytest.mark.asyncio
async def test_migrate_add_chatragconfig_rank_columns_is_idempotent(tmp_path) -> None:
    """The rank-column migration adds six columns once to a Phase 14 chatragconfig table."""
    from sqlalchemy.ext.asyncio import create_async_engine

    legacy = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with legacy.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE chatragconfig (chat_id INTEGER PRIMARY KEY, kb_id INTEGER, "
                "mode VARCHAR, top_k INTEGER, updated_at DATETIME)"
            )
        )
        await conn.execute(
            text("INSERT INTO chatragconfig (chat_id, mode, top_k) VALUES (1, 'off', 5)")
        )
        await migrate_add_chatragconfig_rank_columns(conn)
        await migrate_add_chatragconfig_rank_columns(conn)
    async with legacy.connect() as conn:
        columns = [
            row[1] for row in (await conn.execute(text("PRAGMA table_info(chatragconfig)"))).fetchall()
        ]
        row = (
            await conn.execute(
                text(
                    "SELECT candidate_k, threshold, lexical, llm_rerank, hybrid, rewrite "
                    "FROM chatragconfig"
                )
            )
        ).one()
    await legacy.dispose()

    for name in _RANK_COLUMNS:
        assert columns.count(name) == 1
    assert tuple(row) == (20, None, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_migrate_add_chatragconfig_rank_columns_without_table(tmp_path) -> None:
    """A database without chatragconfig is left untouched."""
    from sqlalchemy.ext.asyncio import create_async_engine

    legacy = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    async with legacy.begin() as conn:
        await migrate_add_chatragconfig_rank_columns(conn)
    await legacy.dispose()


def test_chatragconfig_model_defaults() -> None:
    """A fresh config row defaults to a threshold-only pipeline with 20 candidates."""
    from shared.config import settings
    from shared.models import ChatRagConfig

    cfg = ChatRagConfig(chat_id=1)
    assert (cfg.candidate_k, cfg.threshold, cfg.lexical, cfg.llm_rerank, cfg.hybrid, cfg.rewrite) == (
        20,
        None,
        False,
        False,
        False,
        False,
    )
    assert settings.RAG_LLM_STAGE_TIMEOUT == 45.0


async def _fts_state() -> tuple[set[str], int]:
    async with engine.connect() as conn:
        names = {
            row[0]
            for row in (
                await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE name LIKE 'kb%fts%'")
                )
            ).fetchall()
        }
        count = (await conn.execute(text("SELECT count(*) FROM kb_chunk_fts"))).scalar()
    return names, count


@pytest.mark.asyncio
async def test_ensure_kb_chunk_fts_is_idempotent_and_backfills() -> None:
    """FTS table and triggers exist after init_db; missing rows are backfilled once."""
    names, _ = await _fts_state()
    assert {"kb_chunk_fts", "kbchunk_fts_ai", "kbchunk_fts_ad"} <= names

    async with engine.begin() as conn:
        await conn.execute(text("DROP TRIGGER kbchunk_fts_ai"))
        await conn.execute(text("DROP TRIGGER kbchunk_fts_ad"))
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    async with async_session_factory() as session:
        doc = (await session.exec(select(KbDocument).where(KbDocument.kb_id == kb_id))).first()
        for i in range(2):
            session.add(
                KbChunk(
                    kb_id=kb_id,
                    document_id=doc.id,
                    chunk_index=i,
                    chunk_id=f"c{i}",
                    text=f"text {i}",
                    source="s",
                    title="t",
                    char_start=0,
                    char_end=1,
                )
            )
        await session.commit()
    _, before = await _fts_state()
    assert before == 0

    async with engine.begin() as conn:
        await ensure_kb_chunk_fts(conn)
    names, after = await _fts_state()
    assert {"kbchunk_fts_ai", "kbchunk_fts_ad"} <= names
    assert after == 2

    async with engine.begin() as conn:
        await ensure_kb_chunk_fts(conn)
    _, again = await _fts_state()
    assert again == 2
