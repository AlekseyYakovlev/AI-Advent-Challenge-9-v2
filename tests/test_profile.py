"""Tests for agent/profile.py CRUD semantics: lazy-create, partial update, and cascade."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import profile
from shared.database import async_session_factory
from shared.models import Profile, User


@pytest.mark.asyncio
async def test_get_profile_returns_none_when_absent(authenticated_client: AsyncClient) -> None:
    """get_profile returns None for a user who has never been touched; it never inserts."""
    user_id = authenticated_client.seeded_user_id

    async with async_session_factory() as session:
        row = await profile.get_profile(session, user_id)
        assert row is None

    async with async_session_factory() as session:
        result = await session.exec(select(Profile).where(Profile.user_id == user_id))
        assert result.all() == []


@pytest.mark.asyncio
async def test_get_or_create_profile_is_idempotent(authenticated_client: AsyncClient) -> None:
    """Calling get_or_create_profile twice leaves exactly one all-blank Profile row."""
    user_id = authenticated_client.seeded_user_id

    async with async_session_factory() as session:
        row1 = await profile.get_or_create_profile(session, user_id)
        assert row1.style == ""
        assert row1.format == ""
        assert row1.constraints == ""

    async with async_session_factory() as session:
        await profile.get_or_create_profile(session, user_id)

    async with async_session_factory() as session:
        result = await session.exec(select(Profile).where(Profile.user_id == user_id))
        rows = result.all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_update_profile_partial_leaves_other_fields(
    authenticated_client: AsyncClient,
) -> None:
    """A partial update changes only the supplied field and bumps updated_at."""
    user_id = authenticated_client.seeded_user_id

    async with async_session_factory() as session:
        full_row = await profile.update_profile(
            session,
            user_id,
            style="full style",
            format="full format",
            constraints="full constraints",
        )
        original_updated_at = full_row.updated_at

    async with async_session_factory() as session:
        row = await profile.update_profile(session, user_id, style="s")
        assert row.style == "s"
        assert row.format == "full format"
        assert row.constraints == "full constraints"
        assert row.updated_at >= original_updated_at


@pytest.mark.asyncio
async def test_profile_deleted_when_user_deleted(authenticated_client: AsyncClient) -> None:
    """Deleting the User row cascades away the Profile row (FK ondelete=CASCADE)."""
    user_id = authenticated_client.seeded_user_id

    async with async_session_factory() as session:
        await profile.get_or_create_profile(session, user_id)

    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        await session.delete(user)
        await session.commit()

    async with async_session_factory() as session:
        result = await session.exec(select(Profile).where(Profile.user_id == user_id))
        assert result.all() == []
