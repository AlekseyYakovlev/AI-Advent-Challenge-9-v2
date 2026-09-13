"""Context management: settings resolution, facts, and summarization."""

import asyncio
import json
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.llm_client import llm_client
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Settings

logger = get_logger(__name__)

FACTS_DEBOUNCE_SECONDS = 2.0
SUMMARY_TRIGGER_RATIO = 0.75
SUMMARY_RECOMPRESS_TOKENS = 1500
RECENT_PAIR_COUNT = 2
SUMMARY_PROMPT = "Summarize STRICTLY IN ENGLISH to save tokens"

_debounce_tasks: dict[int, asyncio.Task[None]] = {}
_pending_messages: dict[int, str] = {}


async def get_effective_settings(
    session: AsyncSession,
    chat_id: int,
) -> Settings:
    """Return per-chat settings or fall back to global defaults."""
    result = await session.exec(
        select(Settings).where(Settings.chat_id == chat_id),
    )
    row = result.first()
    if row is not None:
        return row
    result = await session.exec(
        select(Settings).where(Settings.chat_id.is_(None)),
    )
    global_row = result.first()
    if global_row is not None:
        return global_row
    row = Settings(chat_id=None)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def extract_and_update_facts(
    session: AsyncSession,
    chat_id: int,
    user_message: str,
    model: str,
) -> None:
    """Schedule debounced fact extraction for the latest user message."""
    _pending_messages[chat_id] = user_message
    existing = _debounce_tasks.get(chat_id)
    if existing is not None and not existing.done():
        existing.cancel()
    _debounce_tasks[chat_id] = asyncio.create_task(
        _run_debounced_facts(chat_id, model),
    )


async def _run_debounced_facts(chat_id: int, model: str) -> None:
    """Wait for debounce, then extract and merge facts into settings."""
    try:
        await asyncio.sleep(FACTS_DEBOUNCE_SECONDS)
        user_message = _pending_messages.pop(chat_id, "")
        if not user_message:
            return
        async with async_session_factory() as session:
            await _extract_facts(session, chat_id, user_message, model)
    except asyncio.CancelledError:
        return
    finally:
        _debounce_tasks.pop(chat_id, None)


async def _extract_facts(
    session: AsyncSession,
    chat_id: int,
    user_message: str,
    model: str,
) -> None:
    """Call the LLM to extract facts and merge them into settings."""
    prompt = f"Extract key facts as JSON: {user_message}"
    try:
        raw = await llm_client.complete_chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=512,
        )
        new_facts = _parse_facts_json(raw)
    except Exception as exc:
        logger.warning("facts_extraction_failed", chat_id=chat_id, error=str(exc))
        return

    settings_row = await get_effective_settings(session, chat_id)
    existing = _parse_facts_json(settings_row.facts_json)
    merged = {**existing, **new_facts}
    target = await _facts_target_row(session, chat_id, settings_row)
    target.facts_json = json.dumps(merged)
    session.add(target)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise


async def _facts_target_row(
    session: AsyncSession,
    chat_id: int,
    settings_row: Settings,
) -> Settings:
    """Return a writable per-chat settings row for fact storage."""
    if settings_row.chat_id == chat_id:
        return settings_row
    result = await session.exec(
        select(Settings).where(Settings.chat_id == chat_id),
    )
    row = result.first()
    if row is not None:
        return row
    row = Settings(chat_id=chat_id)
    session.add(row)
    await session.flush()
    return row


def _parse_facts_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object from stored or LLM-produced facts text."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}


def _message_tokens(messages: list[dict[str, str]]) -> int:
    """Count total tokens across chat messages."""
    return sum(llm_client.count_tokens(msg["content"]) for msg in messages)


async def summarize_if_needed(
    session: AsyncSession,
    chat_id: int,
    messages: list[dict[str, str]],
    model: str,
) -> list[dict[str, str]]:
    """Summarize older messages when context exceeds 75% of max_tokens."""
    settings_row = await get_effective_settings(session, chat_id)
    total_tokens = _message_tokens(messages)
    threshold = int(settings_row.max_tokens * SUMMARY_TRIGGER_RATIO)
    if total_tokens <= threshold or len(messages) <= RECENT_PAIR_COUNT * 2:
        return messages

    recent_count = RECENT_PAIR_COUNT * 2
    to_summarize = messages[:-recent_count]
    recent = messages[-recent_count:]
    if not to_summarize:
        return messages

    summary_input = "\n".join(
        f"{msg['role']}: {msg['content']}" for msg in to_summarize
    )
    try:
        new_summary = await llm_client.complete_chat(
            messages=[
                {
                    "role": "user",
                    "content": f"{SUMMARY_PROMPT}\n\n{summary_input}",
                },
            ],
            model=model,
            temperature=0.0,
            max_tokens=1024,
        )
    except Exception as exc:
        logger.warning("summarization_failed", chat_id=chat_id, error=str(exc))
        return messages

    old_summary = settings_row.summary_text.strip()
    combined = _combine_summaries(old_summary, new_summary.strip())
    combined = await _recompress_summary_if_needed(combined, model)
    target = await _facts_target_row(session, chat_id, settings_row)
    target.summary_text = combined
    session.add(target)
    try:
        await session.commit()
        await session.refresh(target)
    except Exception:
        await session.rollback()
        raise
    return recent


def _combine_summaries(old_summary: str, new_summary: str) -> str:
    """Accumulate old and new summaries."""
    if old_summary and new_summary:
        return f"{old_summary}\n\n{new_summary}"
    return old_summary or new_summary


async def _recompress_summary_if_needed(summary: str, model: str) -> str:
    """Re-summarize when accumulated summary exceeds 1500 tokens."""
    if llm_client.count_tokens(summary) <= SUMMARY_RECOMPRESS_TOKENS:
        return summary
    try:
        return await llm_client.complete_chat(
            messages=[
                {
                    "role": "user",
                    "content": f"{SUMMARY_PROMPT}\n\n{summary}",
                },
            ],
            model=model,
            temperature=0.0,
            max_tokens=1024,
        )
    except Exception as exc:
        logger.warning("summary_recompress_failed", error=str(exc))
        return summary


async def build_system_prompt(
    session: AsyncSession,
    chat_id: int,
) -> str:
    """Build the system prompt with optional facts and summary."""
    settings_row = await get_effective_settings(session, chat_id)
    parts = [settings_row.system_prompt]
    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")
    return "\n\n".join(parts)
