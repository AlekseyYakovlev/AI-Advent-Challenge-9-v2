"""Tests for agent/memory.py CRUD semantics: overwrite, scoping, and cascade."""

from datetime import datetime, timezone

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


async def _seed_long_term(user_id: int, key: str, value: str) -> int:
    async with async_session_factory() as session:
        row = await memory.save_long_term_memory(session, user_id, key, value)
        return row.id


@pytest.mark.asyncio
async def test_get_long_term_memory_scoped_by_user(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Only the owner can fetch an entry by id; foreign and unknown ids give None."""
    user_a = authenticated_client.seeded_user_id
    user_b = second_authenticated_client.seeded_user_id
    entry_id = await _seed_long_term(user_a, "city", "Berlin")

    async with async_session_factory() as session:
        own = await memory.get_long_term_memory(session, user_a, entry_id)
        assert own is not None and own.value == "Berlin"
        assert await memory.get_long_term_memory(session, user_b, entry_id) is None
        assert await memory.get_long_term_memory(session, user_a, 999999) is None


@pytest.mark.asyncio
async def test_update_long_term_memory_changes_value_and_keeps_created_at(
    authenticated_client: AsyncClient,
) -> None:
    """Updating the value refreshes updated_at and keeps created_at and the key."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed_long_term(user_id, "city", "Berlin")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)

    async with async_session_factory() as session:
        row = await session.get(LongTermMemory, entry_id)
        row.created_at = old
        row.updated_at = old
        session.add(row)
        await session.commit()

    async with async_session_factory() as session:
        updated = await memory.update_long_term_memory(session, user_id, entry_id, value="new")
        assert updated is not None

    async with async_session_factory() as session:
        row = await session.get(LongTermMemory, entry_id)
        assert row.value == "new"
        assert row.key == "city"
        assert row.created_at.replace(tzinfo=None) == datetime(2020, 1, 1)
        assert row.updated_at.replace(tzinfo=None) > datetime(2020, 1, 1)


@pytest.mark.asyncio
async def test_update_long_term_memory_renames_key(authenticated_client: AsyncClient) -> None:
    """Renaming keeps the same row and value and never creates a second row."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed_long_term(user_id, "city", "Berlin")

    async with async_session_factory() as session:
        updated = await memory.update_long_term_memory(session, user_id, entry_id, key="new_key")
        assert updated is not None and updated.id == entry_id

    async with async_session_factory() as session:
        rows = await memory.list_long_term_memory(session, user_id)
        assert len(rows) == 1
        assert rows[0].id == entry_id
        assert rows[0].key == "new_key"
        assert rows[0].value == "Berlin"


@pytest.mark.asyncio
async def test_update_long_term_memory_same_key_is_not_a_conflict(
    authenticated_client: AsyncClient,
) -> None:
    """Passing the row's own key together with a new value succeeds."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed_long_term(user_id, "city", "Berlin")

    async with async_session_factory() as session:
        updated = await memory.update_long_term_memory(
            session, user_id, entry_id, key="city", value="Paris",
        )
        assert updated is not None and updated.value == "Paris"


@pytest.mark.asyncio
async def test_update_long_term_memory_rename_conflict_raises_and_changes_nothing(
    authenticated_client: AsyncClient,
) -> None:
    """Renaming onto another existing key raises and leaves both rows intact."""
    user_id = authenticated_client.seeded_user_id
    id_a = await _seed_long_term(user_id, "a", "value a")
    await _seed_long_term(user_id, "b", "value b")

    async with async_session_factory() as session:
        with pytest.raises(memory.MemoryKeyConflictError):
            await memory.update_long_term_memory(session, user_id, id_a, key="b", value="x")

    async with async_session_factory() as session:
        rows = {r.key: r.value for r in await memory.list_long_term_memory(session, user_id)}
        assert rows == {"a": "value a", "b": "value b"}


@pytest.mark.asyncio
async def test_update_long_term_memory_commit_race_maps_to_conflict(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A unique violation at commit time (race with the LLM tool) becomes a conflict."""
    user_id = authenticated_client.seeded_user_id
    id_a = await _seed_long_term(user_id, "a", "value a")
    await _seed_long_term(user_id, "b", "value b")

    async def _never_taken(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(memory, "_long_term_key_taken", _never_taken)

    async with async_session_factory() as session:
        with pytest.raises(memory.MemoryKeyConflictError):
            await memory.update_long_term_memory(session, user_id, id_a, key="b")
        rows = await memory.list_long_term_memory(session, user_id)
        assert {r.key: r.value for r in rows} == {"a": "value a", "b": "value b"}


@pytest.mark.asyncio
async def test_update_long_term_memory_other_user_returns_none(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Another user's update returns None and leaves the owner's row unchanged."""
    user_a = authenticated_client.seeded_user_id
    user_b = second_authenticated_client.seeded_user_id
    entry_id = await _seed_long_term(user_a, "city", "Berlin")

    async with async_session_factory() as session:
        assert await memory.update_long_term_memory(
            session, user_b, entry_id, key="hacked", value="x",
        ) is None

    async with async_session_factory() as session:
        row = await session.get(LongTermMemory, entry_id)
        assert row.key == "city" and row.value == "Berlin"


@pytest.mark.asyncio
async def test_delete_long_term_memory_removes_only_own_row(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Delete is owner-scoped; a repeated delete returns False."""
    user_a = authenticated_client.seeded_user_id
    user_b = second_authenticated_client.seeded_user_id
    entry_a = await _seed_long_term(user_a, "city", "Berlin")
    await _seed_long_term(user_b, "city", "Paris")

    async with async_session_factory() as session:
        assert await memory.delete_long_term_memory(session, user_b, entry_a) is False
        assert await session.get(LongTermMemory, entry_a) is not None

    async with async_session_factory() as session:
        assert await memory.delete_long_term_memory(session, user_a, entry_a) is True

    async with async_session_factory() as session:
        assert await session.get(LongTermMemory, entry_a) is None
        assert await memory.delete_long_term_memory(session, user_a, entry_a) is False
        rows_b = await memory.list_long_term_memory(session, user_b)
        assert len(rows_b) == 1 and rows_b[0].value == "Paris"
