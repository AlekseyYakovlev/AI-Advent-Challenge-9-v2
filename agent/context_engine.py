"""Context compression strategies implementation."""

import asyncio
import json
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.llm_client import llm_client
from agent import invariants, memory, profile, tasks
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Chat, ContextStrategy, Message, Profile, Settings

logger = get_logger(__name__)

# CONSTANTS - DO NOT make these database fields
SUMMARY_TRIGGER_RATIO = 0.75
RECENT_MESSAGE_COUNT = 10  # Number of recent messages to keep in all strategies
FACTS_DEBOUNCE_SECONDS = 2.0
TOOL_TRACE_ARGS_CHARS = 200
TOOL_TRACE_RESULT_CHARS = 300
TOOL_TRACE_HEADER = "[Tool calls actually executed for this reply]"

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


def _cut(text: str, limit: int) -> str:
    """Cut text to the limit, marking a cut with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def serialize_tool_trace(results: list[dict[str, Any]]) -> str | None:
    """Serialize dispatch results into a compact JSON trace; None when nothing was called."""
    if not results:
        return None
    entries = [
        {
            "name": str(result.get("name", "")),
            "arguments": _cut(str(result.get("arguments", "")), TOOL_TRACE_ARGS_CHARS),
            "ok": bool(result.get("ok")),
            "result": _cut(
                str(result.get("result_text", result.get("content", ""))),
                TOOL_TRACE_RESULT_CHARS,
            ),
        }
        for result in results
    ]
    return json.dumps(entries, ensure_ascii=False)


def render_tool_trace(raw: str | None) -> str:
    """Render a stored tool trace as plain text for later-turn context; '' when unusable."""
    if not raw:
        return ""
    try:
        entries = json.loads(raw)
        lines = [
            f"- {entry['name']}({entry['arguments']}) -> "
            f"{'ok' if entry['ok'] else 'error'}: {entry['result']}"
            for entry in entries
        ]
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("tool_trace_parse_failed", error=str(exc))
        return ""
    if not lines:
        return ""
    return "\n".join([TOOL_TRACE_HEADER, *lines])


async def get_effective_settings(session: AsyncSession, chat_id: int) -> Settings:
    """Return per-chat settings or fall back to the chat owner's global defaults."""
    result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
    settings = result.first()
    if settings is not None:
        return settings

    chat = await session.get(Chat, chat_id)
    owner_id = chat.user_id if chat is not None else None
    conditions = [Settings.chat_id.is_(None)]
    if owner_id is None:
        conditions.append(Settings.user_id.is_(None))
    else:
        conditions.append(Settings.user_id == owner_id)
    result = await session.exec(select(Settings).where(*conditions))
    settings = result.first()
    if settings is None:
        settings = Settings(chat_id=None, user_id=owner_id)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    """Build the system prompt with optional profile, facts, and memory layers."""
    settings_row = await get_effective_settings(session, chat_id)
    # PRODUCTION FIX: Protect against None in system_prompt
    parts = [settings_row.system_prompt or "You are a helpful assistant."]

    chat = await session.get(Chat, chat_id)
    if chat is not None and chat.user_id is not None:
        profile_row = await profile.get_profile(session, chat.user_id)
        if profile_row is not None:
            profile_text = _format_profile(profile_row)
            if profile_text:
                parts.append(profile_text)

    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")

    working = await memory.list_working_memory(session, chat_id)
    if working:
        parts.append(
            "Working memory (this chat's current task data): "
            + json.dumps({row.key: row.value for row in working}),
        )

    if chat is not None and chat.user_id is not None:
        long_term = await memory.list_long_term_memory(session, chat.user_id)
        if long_term:
            parts.append(
                "Long-term memory (persists across all your chats): "
                + json.dumps({row.key: row.value for row in long_term}),
            )

    open_tasks = await tasks.list_open_tasks(session, chat_id)
    if open_tasks:
        lines = []
        for task in open_tasks:
            pause_marker = " [ON PAUSE]" if task.is_paused else ""
            lines.append(
                f'- #{task.id} "{task.title}" (state={task.state.value}{pause_marker}): '
                f"goal={task.goal!r}",
            )
        parts.append("Open tasks in this chat:\n" + "\n".join(lines))

    active = await invariants.resolve_active_invariants(session, chat_id)
    if active:
        lines = []
        for item in active:
            if item["overridden_by"] is not None:
                lines.append(
                    f'[GLOBAL] {item["rule_text"]} (overridden for this chat — see below)',
                )
                lines.append(
                    f'[CHAT] {item["overridden_by"]["rule_text"]} (overrides the above)',
                )
            elif item["scope"] == "chat":
                lines.append(f'[CHAT] {item["rule_text"]}')
            else:
                lines.append(f'[GLOBAL] {item["rule_text"]}')
        parts.append(
            "Active invariants (always follow these; flag if you cannot):\n" + "\n".join(lines),
        )

    return "\n\n".join(parts)


def _format_profile(row: Profile) -> str:
    """Render non-empty profile fields as a system-prompt directive; empty profile -> ''."""
    fields = []
    if row.style.strip():
        fields.append(f"style={row.style.strip()!r}")
    if row.format.strip():
        fields.append(f"format={row.format.strip()!r}")
    if row.constraints.strip():
        fields.append(f"constraints={row.constraints.strip()!r}")
    if not fields:
        return ""
    return "User's stated preferences (always follow these): " + ", ".join(fields)


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
    chat = await session.get(Chat, chat_id)
    new_settings = Settings(chat_id=chat_id, user_id=chat.user_id if chat is not None else None)
    session.add(new_settings)
    await session.commit()
    await session.refresh(new_settings)
    return new_settings


class ContextOverflowError(Exception):
    """Raised when context exceeds LLM capacity and cannot be compressed."""

    pass


async def _load_branch_messages(
    session: AsyncSession,
    chat_id: int,
) -> list[dict[str, Any]]:
    """Walk the active branch and return message dicts oldest-first."""
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.current_leaf_message_id is None:
        return []
    path: list[Message] = []
    current_id: int | None = chat.current_leaf_message_id
    while current_id is not None:
        message = await session.get(Message, current_id)
        if message is None or message.chat_id != chat_id:
            break
        path.append(message)
        current_id = message.parent_id
    return [_message_to_dict(msg) for msg in reversed(path)]


def _message_to_dict(msg: Message) -> dict[str, Any]:
    """Convert a stored message to an LLM history dict, replaying any tool trace as text."""
    content = msg.content
    token_count = msg.token_count
    if msg.role == "assistant" and msg.tool_trace:
        rendered = render_tool_trace(msg.tool_trace)
        if rendered:
            content = f"{content}\n\n{rendered}" if content else rendered
            token_count += llm_client.count_tokens(rendered)
    return {"role": msg.role, "content": content, "token_count": token_count}


async def summarize_if_needed(
    session: AsyncSession,
    chat_id: int,
    history: list[dict[str, Any]],
    model: str | None,
) -> list[dict[str, Any]]:
    """Summarize old messages when approaching context limit (LLM calls only)."""
    if not model:
        return history
    return history


async def _apply_compression_strategy(
    session: AsyncSession,
    chat_id: int,
    all_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Apply compression strategy to user/assistant messages.

    Algorithms (NO DUPLICATION):
    - SLIDING_WINDOW: Last RECENT_MESSAGE_COUNT messages
    - STICKY_FACTS: First message + last RECENT_MESSAGE_COUNT messages (no overlap)
    - TRUNCATE_MIDDLE: First 2 messages + last RECENT_MESSAGE_COUNT messages (no overlap)
    - NO_COMPRESSION: All messages (raises error if exceeds context_length)
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


async def build_llm_context(
    session: AsyncSession,
    chat_id: int,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Build full LLM message list including system prompt and token counts."""
    history = await _load_branch_messages(session, chat_id)
    history = await summarize_if_needed(session, chat_id, history, model)
    compressed = await _apply_compression_strategy(session, chat_id, history)
    system_prompt = await build_system_prompt(session, chat_id)
    return [
        {"role": "system", "content": system_prompt},
        *compressed,
    ]


async def compute_chat_stats(
    session: AsyncSession,
    chat_id: int,
    model: str | None = None,
    context_window: int = 4096,
) -> dict[str, Any]:
    """Calculate context usage statistics for the active chat branch."""
    default_stats: dict[str, Any] = {
        "current_context_size": 0,
        "context_window_size": context_window,
        "usage_percent": 0.0,
        "message_count": 0,
        "total_request_tokens": 0,
        "total_response_tokens": 0,
        "chat_id": chat_id,
    }
    try:
        settings_row = await get_effective_settings(session, chat_id)
        if not settings_row:
            return default_stats

        llm_messages = await build_llm_context(session, chat_id, model)
        branch_messages = await _load_branch_messages(session, chat_id)

        effective_window = settings_row.context_length
        current_context_size = 0
        for msg in llm_messages:
            if msg["role"] == "system":
                current_context_size += llm_client.count_tokens(msg["content"])
            else:
                current_context_size += msg.get(
                    "token_count",
                    llm_client.count_tokens(msg["content"]),
                )

        usage_percent = (
            round((current_context_size / effective_window) * 100, 1)
            if effective_window > 0
            else 0.0
        )
        return {
            "current_context_size": current_context_size,
            "context_window_size": effective_window,
            "usage_percent": usage_percent,
            "message_count": len(llm_messages),
            "total_request_tokens": sum(
                msg.get("token_count", 0)
                for msg in branch_messages
                if msg["role"] == "user"
            ),
            "total_response_tokens": sum(
                msg.get("token_count", 0)
                for msg in branch_messages
                if msg["role"] == "assistant"
            ),
            "chat_id": chat_id,
        }
    except Exception as exc:
        logger.error(
            "stats_calculation_failed",
            chat_id=chat_id,
            error=str(exc),
        )
        return {**default_stats, "error": str(exc)}


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
