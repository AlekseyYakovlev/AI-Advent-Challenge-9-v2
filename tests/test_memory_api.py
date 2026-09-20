"""Tests for GET /api/v1/chats/{chat_id}/memory."""

import pytest
from httpx import AsyncClient

from agent import memory
from shared.database import async_session_factory


@pytest.mark.asyncio
async def test_get_chat_memory_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/chats/1/memory")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_chat_memory_returns_expected_shape(authenticated_client: AsyncClient) -> None:
    """Authenticated GET on an owned chat returns 200 with all expected keys."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Memory chat"})
    chat_id = chat_resp.json()["id"]

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"chat_id", "short_term_message_count", "working", "long_term"}
    assert body["chat_id"] == chat_id
    assert body["working"] == []
    assert body["long_term"] == []


@pytest.mark.asyncio
async def test_working_memory_not_leaked_across_chats(authenticated_client: AsyncClient) -> None:
    """Working memory saved for chat A does NOT appear in chat B's response."""
    user_id = authenticated_client.seeded_user_id
    chat_a_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat A"})
    chat_b_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat B"})
    chat_a = chat_a_resp.json()["id"]
    chat_b = chat_b_resp.json()["id"]

    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_a, "task", "chat A task")

    resp_b = await authenticated_client.get(f"/api/v1/chats/{chat_b}/memory")
    assert resp_b.status_code == 200
    assert resp_b.json()["working"] == []

    resp_a = await authenticated_client.get(f"/api/v1/chats/{chat_a}/memory")
    assert resp_a.status_code == 200
    assert len(resp_a.json()["working"]) == 1


@pytest.mark.asyncio
async def test_long_term_memory_visible_from_any_chat(authenticated_client: AsyncClient) -> None:
    """Long-term memory saved while in chat A DOES appear in chat B's response (D-02)."""
    user_id = authenticated_client.seeded_user_id
    chat_a_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat A"})
    chat_b_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat B"})
    chat_a = chat_a_resp.json()["id"]
    chat_b = chat_b_resp.json()["id"]

    async with async_session_factory() as session:
        await memory.save_long_term_memory(session, user_id, "profile_name", "Alex")

    resp_a = await authenticated_client.get(f"/api/v1/chats/{chat_a}/memory")
    resp_b = await authenticated_client.get(f"/api/v1/chats/{chat_b}/memory")
    assert len(resp_a.json()["long_term"]) == 1
    assert len(resp_b.json()["long_term"]) == 1
    assert resp_a.json()["long_term"][0]["key"] == "profile_name"


@pytest.mark.asyncio
async def test_cross_user_memory_access_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """second_authenticated_client GET on the first user's chat_id returns 404, leaking no rows."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned by user A"})
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        await memory.save_working_memory(
            session, authenticated_client.seeded_user_id, chat_id, "secret", "leak?",
        )

    resp = await second_authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert resp.status_code == 404
    assert "leak" not in resp.text


@pytest.mark.asyncio
async def test_short_term_message_count_matches_active_branch(
    authenticated_client: AsyncClient,
) -> None:
    """short_term_message_count equals the number of messages on the active branch."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Counted chat"})
    chat_id = chat_resp.json()["id"]

    from shared.database import async_session_factory as _factory
    from shared.models import Chat, Message

    async with _factory() as session:
        chat = await session.get(Chat, chat_id)
        msg1 = Message(chat_id=chat_id, role="user", content="Hi")
        session.add(msg1)
        await session.commit()
        await session.refresh(msg1)

        msg2 = Message(chat_id=chat_id, role="assistant", content="Hello", parent_id=msg1.id)
        session.add(msg2)
        await session.commit()
        await session.refresh(msg2)

        chat.current_leaf_message_id = msg2.id
        session.add(chat)
        await session.commit()

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert resp.status_code == 200
    assert resp.json()["short_term_message_count"] == 2
