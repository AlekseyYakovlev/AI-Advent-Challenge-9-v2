"""Context statistics REST endpoint tests."""

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlmodel import select

from agent.context_engine import RECENT_MESSAGE_COUNT, build_llm_context
from agent.llm_client import llm_client
from shared.config import settings
from shared.database import async_session_factory, init_db
from shared.models import Chat, ContextStrategy, Message, Settings

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"


async def _append_user_message(session, chat: Chat, content: str) -> Message:
    """Append a single user message and advance the chat leaf."""
    msg = Message(
        chat_id=chat.id,
        parent_id=chat.current_leaf_message_id,
        role="user",
        content=content,
        token_count=llm_client.count_tokens(content),
    )
    session.add(msg)
    await session.flush()
    chat.current_leaf_message_id = msg.id
    session.add(chat)
    await session.commit()
    await session.refresh(msg)
    return msg


@pytest.mark.asyncio
async def test_stats_endpoint_returns_correct_context_size(authenticated_client: AsyncClient) -> None:
    """Stats endpoint should reflect system prompt plus stored message tokens."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Stats chat"})
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await _append_user_message(session, chat, "Hello world")

    stats_resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["current_context_size"] > 0
    assert stats["context_window_size"] == 4096
    assert stats["usage_percent"] >= 0
    assert stats["message_count"] >= 2


@pytest.mark.asyncio
async def test_stats_handles_empty_chat(authenticated_client: AsyncClient) -> None:
    """Empty chat stats should return defaults without HTTP 500."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Empty"})
    chat_id = chat_resp.json()["id"]

    stats_resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["current_context_size"] >= 0
    assert stats["context_window_size"] == 4096
    assert stats["message_count"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_stats_does_not_call_llm(authenticated_client: AsyncClient) -> None:
    """REST stats must not trigger LLM summarization calls."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "summary"}}]},
        ),
    )

    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "No LLM"})
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await _append_user_message(session, chat, "Test message for stats")

    stats_resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/stats")
    assert stats_resp.status_code == 200
    assert len(respx.calls) == 0


@pytest.mark.asyncio
async def test_stats_reflects_compression_strategy(authenticated_client: AsyncClient) -> None:
    """Sliding window stats should not include more than window + system."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Sliding stats"})
    chat_id = chat_resp.json()["id"]

    await authenticated_client.put(
        "/api/v1/settings",
        json={
            "chat_id": chat_id,
            "strategy": "sliding",
            "context_length": 512,
        },
    )

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        parent_id = chat.current_leaf_message_id
        for i in range(20):
            msg = Message(
                chat_id=chat.id,
                parent_id=parent_id,
                role="user",
                content=f"msg_{i}" + " word" * 50,
                token_count=llm_client.count_tokens(f"msg_{i}" + " word" * 50),
            )
            session.add(msg)
            await session.flush()
            parent_id = msg.id
        chat.current_leaf_message_id = parent_id
        session.add(chat)
        await session.commit()

    stats_resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["message_count"] <= RECENT_MESSAGE_COUNT + 1


@pytest.mark.asyncio
async def test_migration_context_length_idempotent() -> None:
    """Repeated init_db should not fail when context_length column exists."""
    await init_db()
    await init_db()

    async with async_session_factory() as session:
        result = await session.exec(select(Settings).limit(1))
        row = result.first()
        if row is not None:
            assert hasattr(row, "context_length")
