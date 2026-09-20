"""Chat deletion cascade and in-memory cache cleanup tests."""

import asyncio

import pytest
from httpx import AsyncClient
from agent.state import chat_locks, ws_rate_limiter
from shared.database import async_session_factory
from shared.models import Chat, Message, Settings, TokenUsage


@pytest.mark.asyncio
async def test_delete_chat_cleans_in_memory_caches(authenticated_client: AsyncClient) -> None:
    """DELETE should remove ws_rate_limiter and chat_locks entries."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Delete me"})
    chat_id = chat_resp.json()["id"]

    ws_rate_limiter[chat_id] = [1.0, 2.0]
    chat_locks[chat_id] = asyncio.Lock()

    resp = await authenticated_client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 204
    assert chat_id not in ws_rate_limiter
    assert chat_id not in chat_locks


@pytest.mark.asyncio
async def test_delete_chat_cascades_db_records(authenticated_client: AsyncClient) -> None:
    """DELETE should CASCADE-remove messages, settings, and token usage."""
    from datetime import datetime, timezone

    async with async_session_factory() as session:
        chat = Chat(title="Cascade API", user_id=authenticated_client.seeded_user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        message = Message(chat_id=chat.id, role="user", content="Hi")
        settings_row = Settings(chat_id=chat.id, temperature=0.8)
        usage = TokenUsage(
            chat_id=chat.id,
            date=datetime.now(timezone.utc),
            prompt_tokens=5,
            completion_tokens=10,
        )
        session.add(message)
        session.add(settings_row)
        session.add(usage)
        await session.commit()

        chat_id = chat.id
        message_id = message.id
        settings_id = settings_row.id
        usage_id = usage.id

    resp = await authenticated_client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 204

    async with async_session_factory() as session:
        assert (await session.get(Chat, chat_id)) is None
        assert (await session.get(Message, message_id)) is None
        assert (await session.get(Settings, settings_id)) is None
        assert (await session.get(TokenUsage, usage_id)) is None
