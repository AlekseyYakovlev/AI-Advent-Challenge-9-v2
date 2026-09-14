"""Context engine: settings, facts extraction, and summarization tests."""

import asyncio
import json

import httpx
import pytest
import respx
from sqlmodel import select

from agent.context_engine import (
    FACTS_DEBOUNCE_SECONDS,
    SUMMARY_PROMPT,
    _debounce_tasks,
    _pending_messages,
    build_system_prompt,
    extract_and_update_facts,
    get_effective_settings,
    summarize_if_needed,
)
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Settings

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
        session.add(Settings(chat_id=chat.id, facts_json='{"name": "Bob"}'))
        await session.commit()
        await session.refresh(chat)

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


@respx.mock
@pytest.mark.asyncio
async def test_summarize_if_needed_triggers_and_accumulates() -> None:
    """Summarization should trigger above 75% and use the English prompt."""
    captured_prompts: list[str] = []

    def mock_completion(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_prompts.append(body["messages"][0]["content"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "Short English summary."}},
                ],
            },
        )

    respx.post(f"{BASE_URL}/v1/chat/completions").mock(side_effect=mock_completion)

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
        chat = Chat(title="Summary chat")
        session.add(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                context_length=1000,
                summary_text="Old summary.",
            ),
        )
        await session.commit()
        await session.refresh(chat)

        trimmed = await summarize_if_needed(session, chat.id, messages, MODEL)

        assert len(trimmed) == 4
        assert SUMMARY_PROMPT in captured_prompts[0]
        result = await session.exec(
            select(Settings).where(Settings.chat_id == chat.id),
        )
        row = result.first()
        assert row is not None
        assert "Old summary." in row.summary_text
        assert "Short English summary." in row.summary_text


@pytest.mark.asyncio
async def test_summarize_if_needed_skips_below_threshold() -> None:
    """Short conversations should not be summarized."""
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
    ]

    async with async_session_factory() as session:
        chat = Chat(title="Small chat")
        session.add(chat)
        session.add(Settings(chat_id=chat.id, context_length=4096))
        await session.commit()
        await session.refresh(chat)

        result = await summarize_if_needed(session, chat.id, messages, MODEL)
        assert result == messages


@respx.mock
@pytest.mark.asyncio
async def test_summarize_if_needed_skips_no_compression() -> None:
    """NO_COMPRESSION strategy should never trigger summarization."""
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
                strategy=ContextStrategy.NO_COMPRESSION,
            ),
        )
        await session.commit()
        await session.refresh(chat)

        result = await summarize_if_needed(session, chat.id, messages, MODEL)
        assert result == messages
        assert len(respx.calls) == 0
