"""Context compression strategies implementation."""

import asyncio
import json
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from agent.llm_client import llm_client
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import ContextStrategy, Settings

logger = get_logger(__name__)

SUMMARY_TRIGGER_RATIO = 0.75
RECENT_PAIR_COUNT = 5
SUMMARY_RECOMPRESS_TOKENS = 1500
FACTS_DEBOUNCE_SECONDS = 2.0
INITIAL_CONTEXT_COUNT = 5
RECENT_CONTEXT_COUNT = 10
SUMMARY_PROMPT = "Summarize the following conversation STRICTLY IN ENGLISH to save tokens. Keep key facts, decisions, and context. Be concise:"

_pending_messages: dict[int, str] = {}
_debounce_tasks: dict[int, asyncio.Task] = {}

def _message_tokens(messages: list[dict[str, str]]) -> int:
    return sum(llm_client.count_tokens(msg["content"]) for msg in messages)

def _combine_summaries(old_summary: str, new_summary: str) -> str:
    if old_summary and new_summary:
        return f"{old_summary}\n\n{new_summary}"
    return old_summary or new_summary

async def _recompress_summary_if_needed(summary: str, model: str) -> str:
    if llm_client.count_tokens(summary) <= SUMMARY_RECOMPRESS_TOKENS:
        return summary
    try:
        return await llm_client.complete_chat(
            messages=[{"role": "user", "content": f"{SUMMARY_PROMPT}\n\n{summary}"}],
            model=model, temperature=0.0, max_tokens=1024,
        )
    except Exception as exc:
        logger.warning("summary_recompress_failed", error=str(exc))
        return summary

async def _facts_target_row(session: AsyncSession, chat_id: int, settings_row: Settings | None) -> Settings:
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

def _parse_facts_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}

async def get_effective_settings(session: AsyncSession, chat_id: int) -> Settings:
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
    settings_row = await get_effective_settings(session, chat_id)
    parts = [settings_row.system_prompt]
    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")
    return "\n\n".join(parts)

class ContextOverflowError(Exception):
    """Raised when context exceeds LLM capacity and cannot be compressed."""
    pass

async def build_llm_context(
    session: AsyncSession,
    chat_id: int,
    all_messages: list[dict[str, str]],
    model: str,
) -> list[dict[str, str]]:
    """Build context for LLM based on strategy. Raises ContextOverflowError if NO_COMPRESSION exceeds capacity."""
    settings_row = await get_effective_settings(session, chat_id)
    total_tokens = _message_tokens(all_messages)
    context_length = getattr(settings_row, 'context_length', 4096)
    threshold = int(context_length * SUMMARY_TRIGGER_RATIO)
    strategy = settings_row.strategy
    
    if strategy == ContextStrategy.NO_COMPRESSION and total_tokens > context_length:
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

    if total_tokens <= threshold or len(all_messages) <= (RECENT_PAIR_COUNT * 2):
        logger.info("context_under_threshold", chat_id=chat_id, tokens=total_tokens, strategy=strategy)
        return all_messages

    if strategy == ContextStrategy.NO_COMPRESSION:
        logger.info("strategy_no_compression", chat_id=chat_id, messages_count=len(all_messages), tokens=total_tokens)
        return all_messages
    
    if strategy == ContextStrategy.SLIDING_WINDOW:
        recent_count = RECENT_PAIR_COUNT * 2
        recent = all_messages[-recent_count:]
        logger.info("strategy_sliding_window", chat_id=chat_id, total=len(all_messages), sent=len(recent))
        return recent
    
    if strategy == ContextStrategy.STICKY_FACTS:
        logger.info("strategy_sticky_facts", chat_id=chat_id, total_messages=len(all_messages))
        return await _build_sticky_context(session, chat_id, all_messages, model, settings_row)
    
    if strategy == ContextStrategy.TRUNCATE_MIDDLE:
        logger.info("strategy_truncate_middle", chat_id=chat_id, total_messages=len(all_messages))
        return await _build_truncate_middle_context(session, chat_id, all_messages, model)
    
    logger.warning("strategy_unknown_fallback", strategy=strategy)
    return all_messages[-(RECENT_PAIR_COUNT * 2):]

async def _build_sticky_context(session: AsyncSession, chat_id: int, all_messages: list[dict[str, str]], model: str, settings_row: Settings) -> list[dict[str, str]]:
    recent_count = RECENT_PAIR_COUNT * 2
    to_summarize = all_messages[:-recent_count]
    recent = all_messages[-recent_count:]
    if not to_summarize:
        return all_messages
    old_summary = settings_row.summary_text.strip() if settings_row else ""
    summary_input = "\n".join(f"{msg['role']}: {msg['content']}" for msg in to_summarize)
    try:
        new_summary = await llm_client.complete_chat(
            messages=[{"role": "user", "content": f"{SUMMARY_PROMPT}\n\n{summary_input}"}],
            model=model, temperature=0.0, max_tokens=1024,
        )
    except Exception as exc:
        logger.warning("summarization_failed", chat_id=chat_id, error=str(exc))
        return recent
    combined = _combine_summaries(old_summary, new_summary.strip())
    combined = await _recompress_summary_if_needed(combined, model)
    if settings_row:
        target = await _facts_target_row(session, chat_id, settings_row)
        target.summary_text = combined
        session.add(target)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
    context = [{"role": "system", "content": f"[Conversation summary]: {combined}"}]
    context.extend(recent)
    return context

async def _build_truncate_middle_context(session: AsyncSession, chat_id: int, all_messages: list[dict[str, str]], model: str) -> list[dict[str, str]]:
    initial = all_messages[:INITIAL_CONTEXT_COUNT]
    recent = all_messages[-RECENT_CONTEXT_COUNT:]
    middle = all_messages[INITIAL_CONTEXT_COUNT:-RECENT_CONTEXT_COUNT]
    if not middle:
        return all_messages
    middle_summary = ""
    try:
        middle_input = "\n".join(f"{msg['role']}: {msg['content']}" for msg in middle)
        middle_summary = (
            await llm_client.complete_chat(
                messages=[{"role": "user", "content": f"Summarize STRICTLY IN ENGLISH in 2-3 sentences:\n\n{middle_input}"}],
                model=model, temperature=0.0, max_tokens=256,
            )
        ).strip()
    except Exception as exc:
        logger.warning("middle_summarization_failed", chat_id=chat_id, error=str(exc))
    context = list(initial)
    if middle_summary:
        context.append({"role": "system", "content": f"[Middle of conversation summary]: {middle_summary}"})
    context.extend(recent)
    return context

async def _run_debounced_facts(chat_id: int, model: str) -> None:
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

async def _extract_facts(session: AsyncSession, chat_id: int, user_message: str, model: str) -> None:
    prompt = f"Extract key facts as JSON: {user_message}"
    try:
        raw = await llm_client.complete_chat(
            messages=[{"role": "user", "content": prompt}],
            model=model, temperature=0.0, max_tokens=512,
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

def extract_and_update_facts(session: AsyncSession, chat_id: int, user_message: str, model: str) -> None:
    _pending_messages[chat_id] = user_message
    if chat_id in _debounce_tasks:
        _debounce_tasks[chat_id].cancel()
    task = asyncio.create_task(_run_debounced_facts(chat_id, model))
    _debounce_tasks[chat_id] = task
