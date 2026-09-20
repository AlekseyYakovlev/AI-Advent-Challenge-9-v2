"""Thin CRUD layer owning all reads and writes to the invariant tables."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.llm_client import llm_client
from shared.logger import get_logger
from shared.models import ChatInvariant, GlobalInvariant, InvariantConflict

logger = get_logger(__name__)

INVARIANT_CONFLICT_NOTE_MAX_LENGTH = 2000


async def list_global(session: AsyncSession) -> list[GlobalInvariant]:
    """Return every global invariant, oldest first. Never filtered by owner — D-02."""
    result = await session.exec(
        select(GlobalInvariant).order_by(GlobalInvariant.created_at, GlobalInvariant.id),
    )
    return list(result.all())


async def get_global(session: AsyncSession, invariant_id: int) -> GlobalInvariant | None:
    """Return a single global invariant by id, or None if it does not exist."""
    return await session.get(GlobalInvariant, invariant_id)


async def create_global(
    session: AsyncSession,
    title: str,
    rule_text: str,
) -> GlobalInvariant:
    """Create a new global invariant, shared across every account (D-02)."""
    row = GlobalInvariant(title=title, rule_text=rule_text)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_created", scope="global", invariant_id=row.id)
    return row


async def update_global(
    session: AsyncSession,
    invariant_id: int,
    **updates: str,
) -> GlobalInvariant | None:
    """Update the supplied fields on a global invariant, or return None if missing."""
    row = await get_global(session, invariant_id)
    if row is None:
        return None
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_updated", scope="global", invariant_id=invariant_id)
    return row


async def delete_global(session: AsyncSession, invariant_id: int) -> bool:
    """Delete a global invariant, returning False if it does not exist."""
    row = await get_global(session, invariant_id)
    if row is None:
        return False
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_deleted", scope="global", invariant_id=invariant_id)
    return True


async def list_chat_invariants(session: AsyncSession, chat_id: int) -> list[ChatInvariant]:
    """Return this chat's invariants, oldest first."""
    result = await session.exec(
        select(ChatInvariant)
        .where(ChatInvariant.chat_id == chat_id)
        .order_by(ChatInvariant.created_at, ChatInvariant.id),
    )
    return list(result.all())


async def get_chat_invariant(session: AsyncSession, invariant_id: int) -> ChatInvariant | None:
    """Return a single per-chat invariant by id, or None if it does not exist."""
    return await session.get(ChatInvariant, invariant_id)


async def create_chat_invariant(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    title: str,
    rule_text: str,
    overrides_id: int | None,
) -> ChatInvariant:
    """Create a new per-chat invariant, optionally overriding a global one (D-05)."""
    row = ChatInvariant(
        user_id=user_id,
        chat_id=chat_id,
        title=title,
        rule_text=rule_text,
        overrides_id=overrides_id,
    )
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_created", scope="chat", invariant_id=row.id, chat_id=chat_id)
    return row


async def update_chat_invariant(
    session: AsyncSession,
    invariant_id: int,
    **updates: Any,
) -> ChatInvariant | None:
    """Update the supplied fields on a per-chat invariant, or return None if missing."""
    row = await get_chat_invariant(session, invariant_id)
    if row is None:
        return None
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_updated", scope="chat", invariant_id=invariant_id)
    return row


async def delete_chat_invariant(session: AsyncSession, invariant_id: int) -> bool:
    """Delete a per-chat invariant, returning False if it does not exist."""
    row = await get_chat_invariant(session, invariant_id)
    if row is None:
        return False
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_deleted", scope="chat", invariant_id=invariant_id)
    return True


async def resolve_active_invariants(session: AsyncSession, chat_id: int) -> list[dict[str, Any]]:
    """Return this chat's fully resolved, override-labelled active invariant set (D-05/D-06).

    The single source of truth consumed by both build_system_prompt (INV-03) and the
    05-03 self-critique prompt builder — read-only, never writes or commits.
    """
    globals_ = await list_global(session)
    chat_rows = await list_chat_invariants(session, chat_id)

    override_map: dict[int, ChatInvariant] = {}
    standalone: list[ChatInvariant] = []
    for row in chat_rows:
        if row.overrides_id is not None and row.overrides_id not in override_map:
            override_map[row.overrides_id] = row
        else:
            standalone.append(row)

    active: list[dict[str, Any]] = []
    for g in globals_:
        overriding = override_map.get(g.id)
        active.append(
            {
                "scope": "global",
                "id": g.id,
                "title": g.title,
                "rule_text": g.rule_text,
                "overridden_by": (
                    None
                    if overriding is None
                    else {
                        "id": overriding.id,
                        "title": overriding.title,
                        "rule_text": overriding.rule_text,
                    }
                ),
            },
        )
    for c in standalone:
        active.append(
            {
                "scope": "chat",
                "id": c.id,
                "title": c.title,
                "rule_text": c.rule_text,
                "overridden_by": None,
            },
        )
    return active


def build_critique_prompt(
    active: list[dict[str, Any]],
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
) -> str:
    """Render the active invariant set, the assistant's full turn, and the judgment ask.

    Mirrors build_system_prompt's D-06 override labelling exactly, so the critique judges
    the same resolved set the model itself was shown (05-RESEARCH.md Pitfall 3).
    """
    lines: list[str] = []
    for item in active:
        prefix = f'[{item["scope"].upper()} id={item["id"]}]'
        if item["overridden_by"] is not None:
            overriding = item["overridden_by"]
            lines.append(f'{prefix} {item["rule_text"]} (overridden for this chat — see below)')
            lines.append(
                f'[CHAT id={overriding["id"]}] {overriding["rule_text"]} (overrides the above)',
            )
        else:
            lines.append(f'{prefix} {item["rule_text"]}')

    tool_call_lines = [
        f'- {call.get("function", {}).get("name", "")}'
        f'({call.get("function", {}).get("arguments", "")})'
        for call in tool_calls
    ]

    parts = [
        "Active invariants (a rule marked as overridden must NOT be flagged — "
        "the chat's override replaces it):\n" + "\n".join(lines),
        "Assistant's response (prose):\n" + (assistant_text or "(empty)"),
    ]
    if tool_call_lines:
        parts.append("Assistant's tool calls:\n" + "\n".join(tool_call_lines))
    parts.append(
        "Does the response above (prose or tool calls) conflict with any active invariant? "
        "Answer with a single JSON object and nothing else, with keys \"conflict\" (boolean), "
        "\"invariant_scope\" (\"global\" or \"chat\"), \"invariant_id\" (integer, one of the ids "
        "listed above), and \"explanation\" (one short sentence). When nothing conflicts, return "
        '{"conflict": false}. Never cite an invariant marked as overridden.',
    )
    return "\n\n".join(parts)


def parse_critique_json(raw: str) -> dict[str, Any]:
    """Parse the critique call's JSON verdict, stripping a markdown code fence if present.

    Mirrors context_engine._parse_facts_json's lenient-parse-with-safe-fallback shape —
    a fence strip only, never a bracket-matching/regex JSON extractor.
    """
    text = raw.strip()
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"conflict": False}
    if not isinstance(data, dict) or not data.get("conflict"):
        return {"conflict": False}
    return data


async def run_self_critique(
    active: list[dict[str, Any]],
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    """Ask the LLM whether its own completed turn conflicts with an active invariant.

    Fails open: any failure (timeout, malformed output, connection error) returns
    {"conflict": False} rather than raising — INV-04 is a soft flag this phase, never
    a hard gate (hard enforcement is Phase 6's job).
    """
    prompt = build_critique_prompt(active, assistant_text, tool_calls)
    try:
        raw = await llm_client.complete_chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=512,
        )
        return parse_critique_json(raw)
    except Exception as exc:
        logger.warning("invariant_critique_failed", error=str(exc))
        return {"conflict": False}


def match_flagged_invariant(
    active: list[dict[str, Any]],
    critique: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the cited active entry, or None when the citation matches nothing.

    A citation that matches nothing (including a hallucinated id) is treated as no
    conflict — this is what keeps a hallucinated id out of the database and the UI.
    """
    if not critique.get("conflict"):
        return None
    scope = critique.get("invariant_scope")
    invariant_id = critique.get("invariant_id")
    for item in active:
        if item["scope"] == scope and item["id"] == invariant_id:
            return item
    return None


def build_justify_retract_prompt(
    flagged: dict[str, Any],
    critique: dict[str, Any],
) -> str:
    """Build the re-prompt asking the model to justify or retract the flagged step (D-09)."""
    explanation = critique.get("explanation", "")
    return (
        f'Your previous answer may conflict with the invariant "{flagged["title"]}": '
        f'{flagged["rule_text"]}. Concern raised: {explanation}. '
        "Either justify why your step is still correct despite this rule, or retract it and "
        "state the compliant alternative. Reply briefly, in the user's language, without "
        "repeating your whole previous answer."
    )


async def record_conflict(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    invariant_scope: str,
    invariant_id: int,
    invariant_title: str,
    note: str,
) -> InvariantConflict:
    """Persist a detected invariant conflict against the real, already-persisted message id."""
    row = InvariantConflict(
        chat_id=chat_id,
        message_id=message_id,
        invariant_scope=invariant_scope,
        invariant_id=invariant_id,
        invariant_title=invariant_title,
        note=note[:INVARIANT_CONFLICT_NOTE_MAX_LENGTH],
    )
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.warning(
        "invariant_conflict_detected",
        chat_id=chat_id,
        invariant_scope=invariant_scope,
        invariant_id=invariant_id,
    )
    return row


async def list_conflicts(session: AsyncSession, chat_id: int) -> list[InvariantConflict]:
    """Return this chat's persisted conflict log, oldest first."""
    result = await session.exec(
        select(InvariantConflict)
        .where(InvariantConflict.chat_id == chat_id)
        .order_by(InvariantConflict.created_at, InvariantConflict.id),
    )
    return list(result.all())
