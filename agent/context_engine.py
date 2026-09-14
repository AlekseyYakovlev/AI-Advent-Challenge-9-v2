"""Context compression strategies implementation."""

import asyncio
import json
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.llm_client import llm_client
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import ContextStrategy, Settings

logger = get_logger(__name__)

# CONSTANTS - DO NOT make these database fields
SUMMARY_TRIGGER_RATIO = 0.75
RECENT_MESSAGE_COUNT = 10  # Number of recent messages to keep in all strategies
FACTS_DEBOUNCE_SECONDS = 2.0

_pending_messages: dict[int, str] = {}
_debounce_tasks: dict[int, asyncio.Task] = {}


def _message_tokens(messages: list[dict[str, str]]) -> int:
    """Count total tokens in messages using local tokenizer with fallback."""
    try:
        return sum(llm_client.count_tokens(msg["content"]) or 0 for msg in messages)
    except Exception as e:
        logger.warning("token_count_failed", error=str(e))
        # Fallback: ~4 characters per token approximation
        return sum(len(msg["content"]) // 4 for msg in messages)


async def get_effective_settings(session: AsyncSession, chat_id: int) -> Settings:
    """Return per-chat settings or fall back to global defaults."""
    result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
    settings = result.first()
    if settings is not None:
        return settings
    result = await session.exec(select(Settings).where(Settings.chat_id.is_(None)))
    settings = result.first()
    if settings is None:
        settings = Settings(chat_id=None)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    """Build the system prompt with optional facts."""
    settings_row = await get_effective_settings(session, chat_id)
    # PRODUCTION FIX: Protect against None in system_prompt
    parts = [settings_row.system_prompt or "You are a helpful assistant."]
    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")
    return "\n\n".join(parts)


def _parse_facts_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object from stored facts text."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}


async def _facts_target_row(
    session: AsyncSession,
    chat_id: int,
    settings_row: Settings | None,
) -> Settings:
    """Get or create settings row for facts storage."""
    if settings_row is not None and settings_row.chat_id == chat_id:
        return settings_row
    result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
    row = result.first()
    if row is not None:
        return row
    new_settings = Settings(chat_id=chat_id)
    session.add(new_settings)
    await session.commit()
    await session.refresh(new_settings)
    return new_settings


class ContextOverflowError(Exception):
    """Raised when context exceeds LLM capacity and cannot be compressed."""

    pass


async def build_llm_context(
    session: AsyncSession,
    chat_id: int,
    all_messages: list[dict[str, str]],
    model: str,
) -> list[dict[str, str]]:
    """
    Build context for LLM based on strategy.

    CRITICAL: System prompt is NOT in all_messages. It's added separately in ws.py.
    all_messages contains ONLY user and assistant messages from database.

    Algorithms (NO DUPLICATION):
    - SLIDING_WINDOW: Last RECENT_MESSAGE_COUNT messages
    - STICKY_FACTS: First message + last RECENT_MESSAGE_COUNT messages (no overlap)
    - TRUNCATE_MIDDLE: First 2 messages + last RECENT_MESSAGE_COUNT messages (no overlap)
    - NO_COMPRESSION: All messages (raises error if exceeds context_length)

    Returns: list of messages WITHOUT system prompt (it's added in ws.py)
    """
    settings_row = await get_effective_settings(session, chat_id)
    total_tokens = _message_tokens(all_messages)
    context_length = settings_row.context_length
    threshold = int(context_length * SUMMARY_TRIGGER_RATIO)
    strategy = settings_row.strategy

    logger.info(
        "build_llm_context_start",
        chat_id=chat_id,
        strategy=strategy,
        total_messages=len(all_messages),
        total_tokens=total_tokens,
        context_length=context_length,
        threshold=threshold,
    )

    # Handle empty or very small chats
    if len(all_messages) == 0:
        return []

    # NO_COMPRESSION: Send all messages, but check overflow before threshold shortcuts
    if strategy == ContextStrategy.NO_COMPRESSION:
        if total_tokens > context_length:
            logger.error(
                "context_overflow_no_compression",
                chat_id=chat_id,
                tokens=total_tokens,
                context_length=context_length,
            )
            raise ContextOverflowError(
                f"Context size ({total_tokens} tokens) exceeds context window "
                f"({context_length} tokens). Please change compression strategy "
                f"or reduce conversation length."
            )
        logger.info(
            "strategy_no_compression",
            chat_id=chat_id,
            messages_count=len(all_messages),
            tokens=total_tokens,
        )
        return all_messages

    # If under threshold, send all messages (no compression needed)
    if total_tokens <= threshold or len(all_messages) <= RECENT_MESSAGE_COUNT:
        logger.info(
            "context_under_threshold",
            chat_id=chat_id,
            tokens=total_tokens,
            strategy=strategy,
        )
        return all_messages

    # SLIDING_WINDOW: Keep only recent messages
    if strategy == ContextStrategy.SLIDING_WINDOW:
        recent = all_messages[-RECENT_MESSAGE_COUNT:]
        logger.info(
            "strategy_sliding_window",
            chat_id=chat_id,
            total=len(all_messages),
            sent=len(recent),
            discarded=len(all_messages) - len(recent),
        )
        return recent

    # STICKY_FACTS: First message + recent messages (NO DUPLICATION)
    if strategy == ContextStrategy.STICKY_FACTS:
        if len(all_messages) <= 1:
            return all_messages

        recent_start_idx = max(0, len(all_messages) - RECENT_MESSAGE_COUNT)
        recent = all_messages[recent_start_idx:]

        if recent_start_idx > 0:
            result = [all_messages[0]] + recent
            logger.info(
                "strategy_sticky_facts",
                chat_id=chat_id,
                total=len(all_messages),
                sent=len(result),
                first_message_included=True,
                recent_start_idx=recent_start_idx,
            )
        else:
            result = recent
            logger.info(
                "strategy_sticky_facts",
                chat_id=chat_id,
                total=len(all_messages),
                sent=len(result),
                first_message_included=False,
                reason="already_in_recent",
            )

        return result

    # TRUNCATE_MIDDLE: First 2 messages + recent messages (NO DUPLICATION)
    if strategy == ContextStrategy.TRUNCATE_MIDDLE:
        if len(all_messages) <= 2:
            return all_messages

        recent_start_idx = max(0, len(all_messages) - RECENT_MESSAGE_COUNT)
        recent = all_messages[recent_start_idx:]

        first_two = []
        for i in range(min(2, len(all_messages))):
            if i < recent_start_idx:
                first_two.append(all_messages[i])

        result = first_two + recent

        logger.info(
            "strategy_truncate_middle",
            chat_id=chat_id,
            total=len(all_messages),
            sent=len(result),
            first_two_count=len(first_two),
            recent_count=len(recent),
            recent_start_idx=recent_start_idx,
            middle_removed=len(all_messages) - len(result),
        )

        return result

    # Default fallback (should never reach here)
    logger.warning("strategy_unknown_fallback", strategy=strategy)
    return all_messages[-RECENT_MESSAGE_COUNT:]


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

    logger.info(
        "facts_extracted_success",
        chat_id=chat_id,
        new_facts_count=len(new_facts),
        total_facts_count=len(merged),
    )

    target = await _facts_target_row(session, chat_id, settings_row)
    target.facts_json = json.dumps(merged)
    session.add(target)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise


def extract_and_update_facts(
    session: AsyncSession,
    chat_id: int,
    user_message: str,
    model: str,
) -> None:
    """Schedule debounced facts extraction."""
    _pending_messages[chat_id] = user_message
    if chat_id in _debounce_tasks:
        _debounce_tasks[chat_id].cancel()
    task = asyncio.create_task(_run_debounced_facts(chat_id, model))
    _debounce_tasks[chat_id] = task
