"""Context engine: profile injection into build_system_prompt (PERS-02)."""

import pytest
from httpx import AsyncClient

from agent import profile
from agent.context_engine import build_system_prompt
from shared.database import async_session_factory
from shared.models import Chat, Settings

BASE_PROMPT = "Base prompt"


async def _create_chat(user_id: int | None, title: str = "Profile chat") -> int:
    """Insert a Chat row (optionally unowned) with a known-constant base system prompt."""
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, system_prompt=BASE_PROMPT))
        await session.commit()
        return chat.id


@pytest.mark.asyncio
async def test_no_profile_row_leaves_prompt_unchanged(
    authenticated_client: AsyncClient,
) -> None:
    """A chat whose owner has no Profile row produces a prompt with no preferences substring."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)
        assert "User's stated preferences" not in prompt


@pytest.mark.asyncio
async def test_all_blank_profile_leaves_prompt_unchanged(
    authenticated_client: AsyncClient,
) -> None:
    """After get_or_create_profile creates an all-blank row, the prompt still has no preferences substring."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await profile.get_or_create_profile(session, user_id)
        prompt = await build_system_prompt(session, chat_id)
        assert "User's stated preferences" not in prompt


@pytest.mark.asyncio
async def test_saved_profile_fields_appear_in_prompt(
    authenticated_client: AsyncClient,
) -> None:
    """With all three fields set, the prompt contains the label and each saved value."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await profile.update_profile(
            session,
            user_id,
            style="one sentence replies",
            format="bulleted list",
            constraints="no code",
        )
        prompt = await build_system_prompt(session, chat_id)
        assert "User's stated preferences" in prompt
        assert "one sentence replies" in prompt
        assert "bulleted list" in prompt
        assert "no code" in prompt


@pytest.mark.asyncio
async def test_only_non_empty_fields_are_injected(
    authenticated_client: AsyncClient,
) -> None:
    """With only style set, the prompt contains style= but neither format= nor constraints=."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await profile.update_profile(session, user_id, style="terse")
        prompt = await build_system_prompt(session, chat_id)
        assert "style=" in prompt
        assert "terse" in prompt
        assert "format=" not in prompt
        assert "constraints=" not in prompt


@pytest.mark.asyncio
async def test_profile_injected_on_the_very_first_turn(
    authenticated_client: AsyncClient,
) -> None:
    """The profile appears in the prompt for a chat with zero messages."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await profile.update_profile(session, user_id, style="terse")
        prompt = await build_system_prompt(session, chat_id)
        assert "User's stated preferences" in prompt


@pytest.mark.asyncio
async def test_profile_not_leaked_across_users(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """User A's saved profile does not appear in the prompt built for a chat owned by user B."""
    user_a = authenticated_client.seeded_user_id
    user_b = second_authenticated_client.seeded_user_id
    chat_b = await _create_chat(user_b)

    async with async_session_factory() as session:
        await profile.update_profile(session, user_a, style="user a style")
        prompt_b = await build_system_prompt(session, chat_b)
        assert "User's stated preferences" not in prompt_b
        assert "user a style" not in prompt_b


@pytest.mark.asyncio
async def test_unowned_chat_does_not_crash() -> None:
    """build_system_prompt for a chat with user_id None returns normally with no profile text."""
    chat_id = await _create_chat(None)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)
        assert "User's stated preferences" not in prompt
        assert prompt == BASE_PROMPT
