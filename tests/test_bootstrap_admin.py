"""Bootstrap-admin creation, backfill, and migration-idempotency tests."""

from sqlalchemy import text
from sqlmodel import select

import pytest

from shared.auth import hash_password, verify_password
from shared.database import (
    async_session_factory,
    backfill_user_id,
    bootstrap_admin_if_needed,
    engine,
    ensure_bootstrap_admin,
    init_db,
)
from shared.models import Chat, Settings, User


@pytest.mark.asyncio
async def test_ensure_bootstrap_admin_creates_single_user() -> None:
    """ensure_bootstrap_admin() against an empty user table creates exactly one User."""
    credentials = await ensure_bootstrap_admin()

    assert credentials is not None
    username, password = credentials
    assert username
    assert password

    async with async_session_factory() as session:
        result = await session.exec(select(User))
        users = result.all()

    assert len(users) == 1
    assert users[0].username == username


@pytest.mark.asyncio
async def test_ensure_bootstrap_admin_password_verifies_and_is_not_plaintext() -> None:
    """The returned password must verify against the stored hash, never stored raw."""
    _, password = await ensure_bootstrap_admin()

    async with async_session_factory() as session:
        result = await session.exec(select(User))
        user = result.one()

    assert user.password_hash != password
    assert verify_password(password, user.password_hash)


@pytest.mark.asyncio
async def test_ensure_bootstrap_admin_password_has_sufficient_entropy() -> None:
    """The generated password must not be a short fixed default."""
    _, password = await ensure_bootstrap_admin()

    assert len(password) >= 16


@pytest.mark.asyncio
async def test_ensure_bootstrap_admin_is_noop_on_second_call() -> None:
    """A second call must return None and leave exactly one User row."""
    first = await ensure_bootstrap_admin()
    assert first is not None

    second = await ensure_bootstrap_admin()
    assert second is None

    async with async_session_factory() as session:
        result = await session.exec(select(User))
        users = result.all()

    assert len(users) == 1


@pytest.mark.asyncio
async def test_backfill_user_id_sets_null_rows() -> None:
    """backfill_user_id should set user_id on chat/settings rows left NULL."""
    async with async_session_factory() as session:
        admin = User(username="admin", password_hash=hash_password("adminpass"))
        session.add(admin)
        await session.commit()
        await session.refresh(admin)

        chat = Chat(title="Legacy chat")
        settings_row = Settings()
        session.add(chat)
        session.add(settings_row)
        await session.commit()

    await backfill_user_id(admin.id)

    async with async_session_factory() as session:
        chat_result = await session.exec(select(Chat).where(Chat.user_id.is_(None)))
        settings_result = await session.exec(
            select(Settings).where(Settings.user_id.is_(None)),
        )

    assert chat_result.all() == []
    assert settings_result.all() == []


@pytest.mark.asyncio
async def test_backfill_user_id_does_not_overwrite_populated_rows() -> None:
    """backfill_user_id must never overwrite an already-populated user_id."""
    async with async_session_factory() as session:
        admin = User(username="admin", password_hash=hash_password("adminpass"))
        other_user = User(
            username="other", password_hash=hash_password("otherpass"),
        )
        session.add(admin)
        session.add(other_user)
        await session.commit()
        await session.refresh(admin)
        await session.refresh(other_user)

        owned_chat = Chat(title="Owned chat", user_id=other_user.id)
        session.add(owned_chat)
        await session.commit()
        await session.refresh(owned_chat)
        owned_chat_id = owned_chat.id

    await backfill_user_id(admin.id)

    async with async_session_factory() as session:
        chat = await session.get(Chat, owned_chat_id)

    assert chat is not None
    assert chat.user_id == other_user.id


@pytest.mark.asyncio
async def test_init_db_is_idempotent_for_user_id_columns() -> None:
    """Calling init_db() twice must not error or duplicate the user_id column."""
    await init_db()
    await init_db()

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(chat)"))
        columns = [row[1] for row in result.fetchall()]

    assert columns.count("user_id") == 1


@pytest.mark.asyncio
async def test_bootstrap_admin_if_needed_creates_and_backfills() -> None:
    """bootstrap_admin_if_needed() on a fresh database returns credentials and backfills."""
    async with async_session_factory() as session:
        chat = Chat(title="Pre-existing chat")
        settings_row = Settings()
        session.add(chat)
        session.add(settings_row)
        await session.commit()

    credentials = await bootstrap_admin_if_needed()

    assert credentials is not None

    async with async_session_factory() as session:
        chat_result = await session.exec(select(Chat).where(Chat.user_id.is_(None)))
        settings_result = await session.exec(
            select(Settings).where(Settings.user_id.is_(None)),
        )

    assert chat_result.all() == []
    assert settings_result.all() == []


@pytest.mark.asyncio
async def test_bootstrap_admin_if_needed_second_run_is_noop() -> None:
    """A second bootstrap_admin_if_needed() call must return None."""
    first = await bootstrap_admin_if_needed()
    assert first is not None

    second = await bootstrap_admin_if_needed()
    assert second is None
