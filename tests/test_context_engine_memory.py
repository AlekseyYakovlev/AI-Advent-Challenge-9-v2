"""Context engine: read-only memory injection into build_system_prompt."""

import pytest
from httpx import AsyncClient

from agent import memory
from agent.context_engine import build_system_prompt, compute_chat_stats
from shared.database import async_session_factory
from shared.models import Chat, Settings

MODEL = "test-model"


async def _create_chat(user_id: int | None, title: str = "Memory chat") -> int:
    """Insert a Chat row (optionally unowned) and return its id."""
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, system_prompt="Base prompt"))
        await session.commit()
        return chat.id


@pytest.mark.asyncio
async def test_build_system_prompt_unchanged_with_no_memory_rows() -> None:
    """With no memory rows, the prompt carries no memory labels at all."""
    chat_id = await _create_chat(None, "No memory chat")

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)
        assert prompt == "Base prompt"
        assert "Working memory" not in prompt
        assert "Long-term memory" not in prompt


@pytest.mark.asyncio
async def test_working_memory_row_appears_in_prompt(
    authenticated_client: AsyncClient,
) -> None:
    """A saved working-memory row appears under the working-memory label."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id, "Working memory chat")

    async with async_session_factory() as session:
        await memory.save_working_memory(
            session, user_id, chat_id, "current_task_step", "drafting intro",
        )

        prompt = await build_system_prompt(session, chat_id)
        assert "Working memory (this chat's current task data)" in prompt
        assert "current_task_step" in prompt
        assert "drafting intro" in prompt


@pytest.mark.asyncio
async def test_long_term_memory_row_appears_in_prompt(
    authenticated_client: AsyncClient,
) -> None:
    """A saved long-term row appears under the long-term label for its owning chat."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id, "Long term chat")

    async with async_session_factory() as session:
        await memory.save_long_term_memory(session, user_id, "user_name", "Alex")

        prompt = await build_system_prompt(session, chat_id)
        assert "Long-term memory (persists across all your chats)" in prompt
        assert "user_name" in prompt
        assert "Alex" in prompt


@pytest.mark.asyncio
async def test_long_term_memory_is_scoped_to_owner_not_visible_cross_user(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A long-term row saved by user A must not appear in a chat owned by user B."""
    user_a = authenticated_client.seeded_user_id
    user_b = second_authenticated_client.seeded_user_id
    chat_a = await _create_chat(user_a, "User A chat")
    chat_b = await _create_chat(user_b, "User B chat")

    async with async_session_factory() as session:
        await memory.save_long_term_memory(session, user_a, "user_name", "Alex")

        prompt_b = await build_system_prompt(session, chat_b)
        assert "Long-term memory" not in prompt_b
        assert "Alex" not in prompt_b

        prompt_a = await build_system_prompt(session, chat_a)
        assert "Long-term memory" in prompt_a
        assert "Alex" in prompt_a


@pytest.mark.asyncio
async def test_unowned_chat_produces_no_long_term_block_and_raises_nothing() -> None:
    """A chat with user_id None must not attempt a long-term lookup or raise."""
    chat_id = await _create_chat(None, "Legacy chat")

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)
        assert "Long-term memory" not in prompt


@pytest.mark.asyncio
async def test_compute_chat_stats_grows_after_memory_row_saved(
    authenticated_client: AsyncClient,
) -> None:
    """Injected memory must be token-accounted by the existing stats path."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id, "Stats chat")

    async with async_session_factory() as session:
        stats_before = await compute_chat_stats(session, chat_id, MODEL)

        await memory.save_long_term_memory(session, user_id, "user_name", "Alex " * 200)

        stats_after = await compute_chat_stats(session, chat_id, MODEL)

        assert stats_after["current_context_size"] > stats_before["current_context_size"]
