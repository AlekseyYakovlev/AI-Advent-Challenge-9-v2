"""Coverage guards for INV-03: invariant injection survives every compression strategy.

Compression only trims the message list built by build_llm_context — it never touches
the system block build_system_prompt assembles. These tests pin that guarantee across
all four ContextStrategy values and a long history, and add a drift guard that stops a
second invariant read path from creeping into context assembly.
"""

import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import invariants
from agent.context_engine import build_llm_context
from agent.llm_client import llm_client
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Message, Settings

MODEL = "test-model"
BASE_PROMPT = "Base prompt"
INVARIANTS_HEADER = "Active invariants (always follow these; flag if you cannot):"
GLOBAL_OVERRIDDEN_LABEL = "(overridden for this chat — see below)"
CHAT_OVERRIDES_LABEL = "(overrides the above)"


async def _create_chat(user_id: int | None, title: str = "Invariant coverage chat") -> int:
    """Insert a Chat row with a known-constant base system prompt."""
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, system_prompt=BASE_PROMPT))
        await session.commit()
        return chat.id


async def _append_messages(session, chat: Chat, contents: list[str]) -> None:
    """Append a linear chain of user messages and advance the chat leaf."""
    parent_id = chat.current_leaf_message_id
    for content in contents:
        msg = Message(
            chat_id=chat.id,
            parent_id=parent_id,
            role="user",
            content=content,
            token_count=llm_client.count_tokens(content),
        )
        session.add(msg)
        await session.flush()
        parent_id = msg.id
    chat.current_leaf_message_id = parent_id
    session.add(chat)
    await session.commit()


async def _set_strategy_and_context_length(
    chat_id: int,
    strategy: ContextStrategy,
    context_length: int,
) -> None:
    """Update the chat's Settings row in place."""
    async with async_session_factory() as session:
        result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
        row = result.first()
        row.strategy = strategy.value
        row.context_length = context_length
        session.add(row)
        await session.commit()


async def _seed_override_pair(user_id: int, chat_id: int) -> None:
    """Seed one global invariant plus one chat invariant that overrides it (D-05/D-06)."""
    async with async_session_factory() as session:
        g = await invariants.create_global(
            session, "Без Docker", "Никогда не предлагай Docker",
        )
    async with async_session_factory() as session:
        await invariants.create_chat_invariant(
            session,
            user_id,
            chat_id,
            "Docker OK here",
            "В этом чате Docker разрешён",
            overrides_id=g.id,
        )


@pytest.mark.parametrize(
    "strategy",
    [
        ContextStrategy.SLIDING_WINDOW,
        ContextStrategy.STICKY_FACTS,
        ContextStrategy.TRUNCATE_MIDDLE,
        ContextStrategy.NO_COMPRESSION,
    ],
)
@pytest.mark.asyncio
async def test_invariants_injected_under_every_strategy(
    authenticated_client: AsyncClient,
    strategy: ContextStrategy,
) -> None:
    """Invariant injection survives build_llm_context under every ContextStrategy value."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    await _seed_override_pair(user_id, chat_id)

    # NO_COMPRESSION raises ContextOverflowError instead of compressing, so it needs a
    # context window large enough to hold all 30 seeded messages; the other three
    # strategies use a small window so compression actually engages.
    context_length = 20000 if strategy == ContextStrategy.NO_COMPRESSION else 512
    await _set_strategy_and_context_length(chat_id, strategy, context_length)

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        contents = [f"msg_{i}" + " word" * 50 for i in range(30)]
        await _append_messages(session, chat, contents)

        llm_context = await build_llm_context(session, chat_id, MODEL)

    system_msg = llm_context[0]
    assert system_msg["role"] == "system"
    assert INVARIANTS_HEADER in system_msg["content"]
    assert GLOBAL_OVERRIDDEN_LABEL in system_msg["content"]
    assert CHAT_OVERRIDES_LABEL in system_msg["content"]


@pytest.mark.asyncio
async def test_invariants_still_injected_on_a_long_history(
    authenticated_client: AsyncClient,
) -> None:
    """40 messages (well past RECENT_MESSAGE_COUNT=10) still carry the invariants header."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    await _seed_override_pair(user_id, chat_id)
    await _set_strategy_and_context_length(chat_id, ContextStrategy.SLIDING_WINDOW, 512)

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        contents = [f"msg_{i}" + " word" * 50 for i in range(40)]
        await _append_messages(session, chat, contents)

        llm_context = await build_llm_context(session, chat_id, MODEL)

    system_msg = llm_context[0]
    assert system_msg["role"] == "system"
    assert INVARIANTS_HEADER in system_msg["content"]


@pytest.mark.asyncio
async def test_invariants_injected_when_context_is_near_the_overflow_threshold(
    authenticated_client: AsyncClient,
) -> None:
    """NO_COMPRESSION with headroom below the overflow limit still carries the header."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    await _seed_override_pair(user_id, chat_id)
    await _set_strategy_and_context_length(chat_id, ContextStrategy.NO_COMPRESSION, 20000)

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        contents = [f"msg_{i}" + " word" * 50 for i in range(30)]
        await _append_messages(session, chat, contents)

        # Must not raise ContextOverflowError -- invariants are not dropped as a
        # size-saving measure, and this call would raise if the window were too small.
        llm_context = await build_llm_context(session, chat_id, MODEL)

    system_msg = llm_context[0]
    assert system_msg["role"] == "system"
    assert INVARIANTS_HEADER in system_msg["content"]


@pytest.mark.asyncio
async def test_no_invariants_configured_leaves_system_prompt_unchanged(
    authenticated_client: AsyncClient,
) -> None:
    """Zero invariants configured -> no invariants header, base system prompt intact."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        llm_context = await build_llm_context(session, chat_id, MODEL)

    system_msg = llm_context[0]
    assert system_msg["role"] == "system"
    assert "Active invariants" not in system_msg["content"]
    assert BASE_PROMPT in system_msg["content"]


def test_resolver_is_the_only_invariant_read_path_in_context_assembly() -> None:
    """Drift guard: context_engine.py reads invariants only via resolve_active_invariants."""
    context_engine_path = Path(__file__).resolve().parent.parent / "agent" / "context_engine.py"
    raw_lines = context_engine_path.read_text(encoding="utf-8").splitlines()
    non_comment_lines = [line for line in raw_lines if not re.match(r"^\s*#", line)]
    source = "\n".join(non_comment_lines)

    assert source.count("resolve_active_invariants") == 1
    assert "list_global(" not in source
    assert "list_chat_invariants(" not in source
