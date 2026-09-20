"""Tests for GET /api/v1/chats/{chat_id}/invariant-conflicts (D-13)."""

import pytest
from httpx import AsyncClient

from shared.database import async_session_factory
from shared.models import Chat, InvariantConflict, Message


async def _seed_conflict(chat_id: int, message_id: int) -> InvariantConflict:
    """Insert an InvariantConflict row directly and return it."""
    async with async_session_factory() as session:
        row = InvariantConflict(
            chat_id=chat_id,
            message_id=message_id,
            invariant_scope="global",
            invariant_id=1,
            invariant_title="Без Docker",
            note="Беру свои слова назад.",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def _create_chat_and_message(user_id: int) -> tuple[int, int]:
    """Create a chat and one assistant message owned by user_id, return their ids."""
    async with async_session_factory() as session:
        chat = Chat(title="Conflicts", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        message = Message(chat_id=chat.id, role="assistant", content="Разверни через Docker")
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return chat.id, message.id


@pytest.mark.asyncio
async def test_get_invariant_conflicts_returns_persisted_rows(
    authenticated_client: AsyncClient,
) -> None:
    """GET returns 200 and a one-element list carrying the persisted fields."""
    chat_id, message_id = await _create_chat_and_message(authenticated_client.seeded_user_id)
    await _seed_conflict(chat_id, message_id)

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/invariant-conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["chat_id"] == chat_id
    assert row["message_id"] == message_id
    assert row["invariant_scope"] == "global"
    assert row["invariant_title"] == "Без Docker"
    assert row["note"] == "Беру свои слова назад."
    assert "id" in row
    assert "created_at" in row


@pytest.mark.asyncio
async def test_get_invariant_conflicts_empty_list_for_new_chat(
    authenticated_client: AsyncClient,
) -> None:
    """GET on a chat with no conflicts returns 200 and an empty list."""
    resp = await authenticated_client.post("/api/v1/chats", json={"title": "Fresh"})
    chat_id = resp.json()["id"]

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/invariant-conflicts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_invariant_conflicts_other_users_chat_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A non-owner GET returns 404 (never 403), matching the IDOR-safe convention."""
    chat_id, message_id = await _create_chat_and_message(authenticated_client.seeded_user_id)
    await _seed_conflict(chat_id, message_id)

    resp = await second_authenticated_client.get(f"/api/v1/chats/{chat_id}/invariant-conflicts")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_invariant_conflicts_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/chats/1/invariant-conflicts")
    assert resp.status_code == 401
