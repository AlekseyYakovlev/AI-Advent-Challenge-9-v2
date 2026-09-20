"""Tests for agent/memory.py CRUD semantics: overwrite, scoping, and cascade."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import memory
from shared.database import async_session_factory
from shared.models import Chat, LongTermMemory, WorkingMemory


async def _create_chat(user_id: int, title: str = "Memory chat") -> int:
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


@pytest.mark.asyncio
async def test_save_working_memory_overwrites_same_key(
    authenticated_client: AsyncClient,
) -> None:
    """Saving the same key twice updates in place; total row count stays 1."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_id, "task", "first value")
        await memory.save_working_memory(session, user_id, chat_id, "task", "second value")

    async with async_session_factory() as session:
        result = await session.exec(select(WorkingMemory).where(WorkingMemory.chat_id == chat_id))
        rows = result.all()
        assert len(rows) == 1
        assert rows[0].value == "second value"


@pytest.mark.asyncio
async def test_working_memory_isolated_per_chat(authenticated_client: AsyncClient) -> None:
    """The same key in two different chats produces two independent rows."""
    user_id = authenticated_client.seeded_user_id
    chat_a = await _create_chat(user_id, "Chat A")
    chat_b = await _create_chat(user_id, "Chat B")

    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_a, "note", "value A")
        await memory.save_working_memory(session, user_id, chat_b, "note", "value B")

    async with async_session_factory() as session:
        rows_a = await memory.list_working_memory(session, chat_a)
        rows_b = await memory.list_working_memory(session, chat_b)
        assert len(rows_a) == 1 and rows_a[0].value == "value A"
        assert len(rows_b) == 1 and rows_b[0].value == "value B"


@pytest.mark.asyncio
async def test_long_term_memory_visible_across_chats(authenticated_client: AsyncClient) -> None:
    """A long-term row saved while in chat A is returned regardless of chat (D-02)."""
    user_id = authenticated_client.seeded_user_id
    chat_a = await _create_chat(user_id, "Chat A")

    async with async_session_factory() as session:
        await memory.save_long_term_memory(session, user_id, "favorite_language", "Python")

    async with async_session_factory() as session:
        rows = await memory.list_long_term_memory(session, user_id)
        assert len(rows) == 1
        assert rows[0].key == "favorite_language"
        assert rows[0].value == "Python"
    # Not chat-scoped: no chat_id argument exists on list_long_term_memory at all,
    # so chat_a's existence is only used to prove the write happened "from" a chat.
    assert chat_a is not None


@pytest.mark.asyncio
async def test_long_term_memory_isolated_per_user(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Two different users with the same key get two independent long-term rows."""
    user_a = authenticated_client.seeded_user_id
    user_b = second_authenticated_client.seeded_user_id

    async with async_session_factory() as session:
        await memory.save_long_term_memory(session, user_a, "shared_key", "value from A")
        await memory.save_long_term_memory(session, user_b, "shared_key", "value from B")

    async with async_session_factory() as session:
        rows_a = await memory.list_long_term_memory(session, user_a)
        rows_b = await memory.list_long_term_memory(session, user_b)
        assert len(rows_a) == 1 and rows_a[0].value == "value from A"
        assert len(rows_b) == 1 and rows_b[0].value == "value from B"


@pytest.mark.asyncio
async def test_deleting_chat_cascades_working_memory_but_not_long_term(
    authenticated_client: AsyncClient,
) -> None:
    """Deleting a Chat cascades away WorkingMemory rows; LongTermMemory survives."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_id, "scratch", "temp value")
        await memory.save_long_term_memory(session, user_id, "profile_name", "Alex")

    resp = await authenticated_client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 204

    async with async_session_factory() as session:
        result = await session.exec(select(WorkingMemory).where(WorkingMemory.chat_id == chat_id))
        assert result.all() == []
        long_term = await memory.list_long_term_memory(session, user_id)
        assert len(long_term) == 1
        assert long_term[0].key == "profile_name"


@pytest.mark.asyncio
async def test_list_working_memory_empty_chat_returns_empty_list(
    authenticated_client: AsyncClient,
) -> None:
    """A chat with no working memory rows returns an empty list."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        rows = await memory.list_working_memory(session, chat_id)
        assert rows == []
