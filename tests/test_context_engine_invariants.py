"""Context engine: invariant injection into build_system_prompt (INV-03, D-06)."""

import pytest
from httpx import AsyncClient

from agent import invariants
from agent.context_engine import build_system_prompt
from agent.profile import update_profile
from shared.database import async_session_factory
from shared.models import Chat, Settings, Task, TaskState

BASE_PROMPT = "Base prompt"


async def _create_chat(user_id: int | None, title: str = "Invariant chat") -> int:
    """Insert a Chat row with a known-constant base system prompt."""
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, system_prompt=BASE_PROMPT))
        await session.commit()
        return chat.id


@pytest.mark.asyncio
async def test_prompt_omits_invariant_block_when_none_configured(
    authenticated_client: AsyncClient,
) -> None:
    """No invariants configured anywhere -> no invariants header in the prompt."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "Active invariants" not in prompt


@pytest.mark.asyncio
async def test_prompt_includes_unoverridden_global_rule(
    authenticated_client: AsyncClient,
) -> None:
    """An unoverridden global rule appears as a bare [GLOBAL] line."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    async with async_session_factory() as session:
        await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "Active invariants (always follow these; flag if you cannot):" in prompt
    assert "[GLOBAL] Никогда не предлагай Docker" in prompt
    assert "[GLOBAL] Никогда не предлагай Docker (overridden" not in prompt


@pytest.mark.asyncio
async def test_prompt_includes_standalone_chat_rule(
    authenticated_client: AsyncClient,
) -> None:
    """A standalone (non-overriding) chat rule appears as a bare [CHAT] line."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    async with async_session_factory() as session:
        await invariants.create_chat_invariant(
            session,
            user_id,
            chat_id,
            "Chat rule",
            "Always respond in Russian",
            overrides_id=None,
        )

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "[CHAT] Always respond in Russian" in prompt
    assert "[CHAT] Always respond in Russian (overrides" not in prompt


@pytest.mark.asyncio
async def test_prompt_labels_an_override_pair_verbatim_per_d06(
    authenticated_client: AsyncClient,
) -> None:
    """An override pair injects both rules, labelled verbatim per D-06, on consecutive lines."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    async with async_session_factory() as session:
        g = await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")
    async with async_session_factory() as session:
        await invariants.create_chat_invariant(
            session,
            user_id,
            chat_id,
            "Docker OK here",
            "В этом чате Docker разрешён",
            overrides_id=g.id,
        )

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    expected_global_line = (
        "[GLOBAL] Никогда не предлагай Docker (overridden for this chat — see below)"
    )
    expected_chat_line = "[CHAT] В этом чате Docker разрешён (overrides the above)"
    assert expected_global_line in prompt
    assert expected_chat_line in prompt
    global_idx = prompt.index(expected_global_line)
    chat_idx = prompt.index(expected_chat_line)
    between = prompt[global_idx + len(expected_global_line) : chat_idx]
    assert between.strip() == ""


@pytest.mark.asyncio
async def test_prompt_invariants_do_not_replace_existing_blocks(
    authenticated_client: AsyncClient,
) -> None:
    """With a profile and an open task also present, all three blocks still appear."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    async with async_session_factory() as session:
        task = Task(
            user_id=user_id,
            chat_id=chat_id,
            title="Export chat history",
            goal="markdown export",
            state=TaskState.PLANNING,
        )
        session.add(task)
        await session.commit()
    async with async_session_factory() as session:
        await update_profile(session, user_id, style="коротко")
    async with async_session_factory() as session:
        await invariants.create_global(session, "Без Docker", "Никогда не предлагай Docker")

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "style=" in prompt
    assert "Open tasks in this chat:" in prompt
    assert "Active invariants (always follow these; flag if you cannot):" in prompt
