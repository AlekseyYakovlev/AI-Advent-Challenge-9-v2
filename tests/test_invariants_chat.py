"""Tests for agent/invariants.py per-chat CRUD and resolve_active_invariants (D-05, D-06)."""

import pytest

from agent import invariants
from shared.auth import hash_password
from shared.database import async_session_factory
from shared.models import Chat, ChatInvariant, User


async def _create_user_and_chat(username: str, title: str = "Chat") -> tuple[int, int]:
    """Insert a User and an owned Chat row directly, returning (user_id, chat_id)."""
    async with async_session_factory() as session:
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.commit()
        await session.refresh(user)

        chat = Chat(title=title, user_id=user.id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return user.id, chat.id


@pytest.mark.asyncio
async def test_create_chat_invariant_persists_scope_and_override_link() -> None:
    """create_chat_invariant persists the right user_id, chat_id, and overrides_id."""
    user_id, chat_id = await _create_user_and_chat("chatuser1")
    async with async_session_factory() as session:
        g = await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")

    async with async_session_factory() as session:
        row = await invariants.create_chat_invariant(
            session,
            user_id,
            chat_id,
            "Тут можно Docker",
            "В этом чате Docker разрешён",
            overrides_id=g.id,
        )
        assert row.id is not None
        assert row.user_id == user_id
        assert row.chat_id == chat_id
        assert row.overrides_id == g.id


@pytest.mark.asyncio
async def test_create_chat_invariant_accepts_null_override() -> None:
    """create_chat_invariant with overrides_id=None persists as None."""
    user_id, chat_id = await _create_user_and_chat("chatuser2")
    async with async_session_factory() as session:
        row = await invariants.create_chat_invariant(
            session, user_id, chat_id, "Just chat rule", "rule text", overrides_id=None,
        )
        assert row.overrides_id is None


@pytest.mark.asyncio
async def test_resolve_active_invariants_lists_unoverridden_global_and_chat_rules() -> None:
    """One unoverridden global plus one standalone chat rule produce two separate entries."""
    user_id, chat_id = await _create_user_and_chat("chatuser3")
    async with async_session_factory() as session:
        g = await invariants.create_global(session, "Global A", "global rule")
    async with async_session_factory() as session:
        c = await invariants.create_chat_invariant(
            session, user_id, chat_id, "Chat A", "chat rule", overrides_id=None,
        )

    async with async_session_factory() as session:
        active = await invariants.resolve_active_invariants(session, chat_id)

    assert len(active) == 2
    global_entry = next(item for item in active if item["scope"] == "global")
    chat_entry = next(item for item in active if item["scope"] == "chat")
    assert global_entry["id"] == g.id
    assert global_entry["title"] == g.title
    assert global_entry["rule_text"] == g.rule_text
    assert global_entry["overridden_by"] is None
    assert chat_entry["id"] == c.id
    assert chat_entry["title"] == c.title
    assert chat_entry["rule_text"] == c.rule_text
    assert chat_entry["overridden_by"] is None


@pytest.mark.asyncio
async def test_resolve_active_invariants_pairs_an_override_with_its_global() -> None:
    """A chat rule pointing at a global produces one paired entry, not two standalone ones."""
    user_id, chat_id = await _create_user_and_chat("chatuser4")
    async with async_session_factory() as session:
        g = await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")
    async with async_session_factory() as session:
        c = await invariants.create_chat_invariant(
            session,
            user_id,
            chat_id,
            "Тут можно Docker",
            "В этом чате Docker разрешён",
            overrides_id=g.id,
        )

    async with async_session_factory() as session:
        active = await invariants.resolve_active_invariants(session, chat_id)

    assert len(active) == 1
    entry = active[0]
    assert entry["scope"] == "global"
    assert entry["id"] == g.id
    assert entry["overridden_by"] is not None
    assert entry["overridden_by"]["id"] == c.id
    assert entry["overridden_by"]["title"] == c.title
    assert entry["overridden_by"]["rule_text"] == c.rule_text


@pytest.mark.asyncio
async def test_resolve_active_invariants_second_override_of_same_global_is_standalone() -> None:
    """A second chat rule overriding an already-claimed global degrades to standalone."""
    user_id, chat_id = await _create_user_and_chat("chatuser5")
    async with async_session_factory() as session:
        g = await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")
    async with async_session_factory() as session:
        first = await invariants.create_chat_invariant(
            session, user_id, chat_id, "First override", "first rule", overrides_id=g.id,
        )
    async with async_session_factory() as session:
        second = await invariants.create_chat_invariant(
            session, user_id, chat_id, "Second override", "second rule", overrides_id=g.id,
        )

    async with async_session_factory() as session:
        active = await invariants.resolve_active_invariants(session, chat_id)

    assert len(active) == 2
    global_entry = next(item for item in active if item["scope"] == "global")
    assert global_entry["overridden_by"]["id"] == first.id
    standalone_entry = next(item for item in active if item["scope"] == "chat")
    assert standalone_entry["id"] == second.id
    assert standalone_entry["overridden_by"] is None


@pytest.mark.asyncio
async def test_resolve_active_invariants_is_empty_when_nothing_configured() -> None:
    """resolve_active_invariants returns [] when no global or chat invariants exist."""
    _, chat_id = await _create_user_and_chat("chatuser6")
    async with async_session_factory() as session:
        active = await invariants.resolve_active_invariants(session, chat_id)
    assert active == []


@pytest.mark.asyncio
async def test_resolve_active_invariants_ignores_other_chats_rules() -> None:
    """A chat rule on chat B is absent from chat A's resolved set."""
    user_id, chat_a = await _create_user_and_chat("chatuser7", "Chat A")
    async with async_session_factory() as session:
        chat_b = Chat(title="Chat B", user_id=user_id)
        session.add(chat_b)
        await session.commit()
        await session.refresh(chat_b)
        chat_b_id = chat_b.id

    async with async_session_factory() as session:
        await invariants.create_chat_invariant(
            session, user_id, chat_b_id, "B rule", "b rule text", overrides_id=None,
        )

    async with async_session_factory() as session:
        active_a = await invariants.resolve_active_invariants(session, chat_a)
    assert active_a == []


@pytest.mark.asyncio
async def test_delete_global_invariant_nulls_the_overriding_chat_rule() -> None:
    """Deleting the overridden global leaves the chat rule alive with overrides_id set to None."""
    user_id, chat_id = await _create_user_and_chat("chatuser8")
    async with async_session_factory() as session:
        g = await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")
    async with async_session_factory() as session:
        c = await invariants.create_chat_invariant(
            session, user_id, chat_id, "Override", "override rule", overrides_id=g.id,
        )

    async with async_session_factory() as session:
        deleted = await invariants.delete_global(session, g.id)
        assert deleted is True

    async with async_session_factory() as session:
        row = await session.get(ChatInvariant, c.id)
        assert row is not None
        assert row.overrides_id is None
