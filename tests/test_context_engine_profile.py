"""Context engine: profile injection into build_system_prompt (PERS-02)."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import profile
from agent.context_engine import RECENT_MESSAGE_COUNT, build_llm_context, build_system_prompt
from agent.llm_client import llm_client
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Message, Settings

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


async def _seed_alternating_messages(session, chat_id: int, count: int) -> None:
    """Append `count` alternating user/assistant messages, advancing the chat's active branch."""
    chat = await session.get(Chat, chat_id)
    assert chat is not None
    parent_id = chat.current_leaf_message_id
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"message {i}" + " word" * 50
        msg = Message(
            chat_id=chat_id,
            parent_id=parent_id,
            role=role,
            content=content,
            token_count=llm_client.count_tokens(content),
        )
        session.add(msg)
        await session.flush()
        parent_id = msg.id
    chat.current_leaf_message_id = parent_id
    session.add(chat)
    await session.commit()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", list(ContextStrategy))
async def test_profile_present_under_every_strategy(
    authenticated_client: AsyncClient,
    strategy: ContextStrategy,
) -> None:
    """build_llm_context's outbound system message carries the profile under every ContextStrategy."""
    user_id = authenticated_client.seeded_user_id
    marker = f"MARKER_{strategy.value.upper()}"
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
        settings_row = result.one()
        settings_row.strategy = strategy
        settings_row.context_length = 131072
        session.add(settings_row)
        await session.commit()

        await profile.update_profile(session, user_id, style=marker)
        await _seed_alternating_messages(session, chat_id, 25)

        context = await build_llm_context(session, chat_id, "test-model")

    assert context[0]["role"] == "system"
    assert "User's stated preferences" in context[0]["content"]
    assert marker in context[0]["content"]


@pytest.mark.asyncio
async def test_profile_present_on_first_and_late_turns(
    authenticated_client: AsyncClient,
) -> None:
    """The profile survives an empty chat and a chat compressed well past RECENT_MESSAGE_COUNT."""
    user_id = authenticated_client.seeded_user_id
    marker = "MARKER_TURN_COVERAGE"
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
        settings_row = result.one()
        settings_row.strategy = ContextStrategy.SLIDING_WINDOW
        settings_row.context_length = 512
        session.add(settings_row)
        await session.commit()

        await profile.update_profile(session, user_id, style=marker)

        empty_context = await build_llm_context(session, chat_id, "test-model")
        assert empty_context[0]["role"] == "system"
        assert marker in empty_context[0]["content"]

        await _seed_alternating_messages(session, chat_id, 30)

        late_context = await build_llm_context(session, chat_id, "test-model")

    assert late_context[0]["role"] == "system"
    assert "User's stated preferences" in late_context[0]["content"]
    assert marker in late_context[0]["content"]
    assert len(late_context) - 1 == RECENT_MESSAGE_COUNT
