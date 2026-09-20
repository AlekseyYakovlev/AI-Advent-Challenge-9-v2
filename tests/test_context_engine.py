"""Context engine: settings, facts extraction, and summarization tests."""

import asyncio
import json

import httpx
import pytest
import respx
from sqlmodel import select

from agent.context_engine import (
    FACTS_DEBOUNCE_SECONDS,
    RECENT_MESSAGE_COUNT,
    ContextOverflowError,
    _apply_compression_strategy,
    _debounce_tasks,
    _pending_messages,
    build_llm_context,
    build_system_prompt,
    extract_and_update_facts,
    get_effective_settings,
)
from agent.llm_client import llm_client
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Message, Settings

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"


@pytest.fixture(autouse=True)
def reset_debounce_state() -> None:
    """Clear module-level debounce state between tests."""
    _pending_messages.clear()
    for task in _debounce_tasks.values():
        if not task.done():
            task.cancel()
    _debounce_tasks.clear()


@pytest.mark.asyncio
async def test_get_effective_settings_falls_back_to_global() -> None:
    """Per-chat lookup should inherit global settings when no override exists."""
    async with async_session_factory() as session:
        chat = Chat(title="Context chat")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        global_row = Settings(chat_id=None, system_prompt="Global", temperature=0.4)
        session.add(global_row)
        await session.commit()

        effective = await get_effective_settings(session, chat.id)
        assert effective.system_prompt == "Global"
        assert effective.temperature == 0.4


@pytest.mark.asyncio
async def test_get_effective_settings_uses_per_chat_row() -> None:
    """Per-chat settings row should override global defaults."""
    async with async_session_factory() as session:
        chat = Chat(title="Override")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        session.add(Settings(chat_id=None, temperature=0.2))
        session.add(Settings(chat_id=chat.id, temperature=0.95))
        await session.commit()

        effective = await get_effective_settings(session, chat.id)
        assert effective.chat_id == chat.id
        assert effective.temperature == 0.95


@pytest.mark.asyncio
async def test_build_system_prompt_includes_facts_and_summary() -> None:
    """System prompt should append facts and summary when present."""
    async with async_session_factory() as session:
        chat = Chat(title="Prompt chat")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        row = Settings(
            chat_id=chat.id,
            system_prompt="Base prompt",
            facts_json='{"name": "Alice"}',
            summary_text="User asked about Python.",
        )
        session.add(row)
        await session.commit()

        prompt = await build_system_prompt(session, chat.id)
        assert "Base prompt" in prompt
        assert "Known facts" in prompt
        assert "Alice" in prompt
        assert "Conversation summary" in prompt
        assert "Python" in prompt


@respx.mock
@pytest.mark.asyncio
async def test_extract_and_update_facts_debounce_and_merge() -> None:
    """Facts extraction should debounce and merge JSON into settings."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"language": "Python"}',
                        },
                    },
                ],
            },
        ),
    )

    async with async_session_factory() as session:
        chat = Chat(title="Facts chat")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, facts_json='{"name": "Bob"}'))
        await session.commit()

        extract_and_update_facts(session, chat.id, "I love Python", MODEL)
        extract_and_update_facts(session, chat.id, "I love Python again", MODEL)
        await asyncio.sleep(FACTS_DEBOUNCE_SECONDS + 0.2)

        result = await session.exec(
            select(Settings).where(Settings.chat_id == chat.id),
        )
        row = result.first()
        assert row is not None
        facts = json.loads(row.facts_json)
        assert facts["name"] == "Bob"
        assert facts["language"] == "Python"


@pytest.mark.asyncio
async def test_sticky_facts_keeps_first_and_recent_without_duplication() -> None:
    """STICKY_FACTS should keep first message plus recent messages with no overlap."""
    long_text = "word " * 4000
    messages = [
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "reply one"},
    ]
    for i in range(8):
        messages.extend([
            {"role": "user", "content": f"older question {i}"},
            {"role": "assistant", "content": f"older answer {i}"},
        ])
    messages.extend([
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ])

    async with async_session_factory() as session:
        chat = Chat(title="Sticky chat")
        session.add(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                context_length=1000,
                strategy=ContextStrategy.STICKY_FACTS.value,
            ),
        )
        await session.commit()
        await session.refresh(chat)

        trimmed = await _apply_compression_strategy(session, chat.id, messages)

        assert trimmed[0]["content"] == long_text
        assert trimmed[-1]["content"] == "recent answer"
        assert len(trimmed) == RECENT_MESSAGE_COUNT + 1
        assert len(trimmed) == len({msg["content"] for msg in trimmed})


@pytest.mark.asyncio
async def test_build_llm_context_skips_below_threshold() -> None:
    """Short conversations should not be compressed."""
    async with async_session_factory() as session:
        chat = Chat(title="Small chat")
        session.add(chat)
        session.add(Settings(chat_id=chat.id, context_length=4096))
        await session.commit()
        await session.refresh(chat)

        parent_id = chat.current_leaf_message_id
        for role, content in [("user", "Hi"), ("assistant", "Hello")]:
            msg = Message(
                chat_id=chat.id,
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

        result = await build_llm_context(session, chat.id, MODEL)
        history = [msg for msg in result if msg["role"] != "system"]
        assert len(history) == 2
        assert history[0]["content"] == "Hi"
        assert history[1]["content"] == "Hello"


@respx.mock
@pytest.mark.asyncio
async def test_no_compression_raises_on_overflow() -> None:
    """NO_COMPRESSION strategy should raise when context exceeds window."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Should not run."}}]},
        ),
    )

    long_text = "word " * 4000
    messages = [
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "reply one"},
        {"role": "user", "content": "older question"},
        {"role": "assistant", "content": "older answer"},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]

    async with async_session_factory() as session:
        chat = Chat(title="No compression chat")
        session.add(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                context_length=1000,
                strategy=ContextStrategy.NO_COMPRESSION.value,
            ),
        )
        await session.commit()
        await session.refresh(chat)

        with pytest.raises(ContextOverflowError):
            await _apply_compression_strategy(session, chat.id, messages)
        assert len(respx.calls) == 0
