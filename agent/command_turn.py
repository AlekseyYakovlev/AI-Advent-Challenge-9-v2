"""Shared mechanism of slash-command turns (/help, /support) over the project documentation base."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config
from agent.context_engine import _message_tokens
from agent.llm_client import count_tokens
from agent.mcp_tools import McpToolset
from agent.rag import (
    MODE_RAG,
    MSG_CONTEXT_FULL,
    MSG_RETRIEVAL_FAILED,
    QUESTION_PREFIX,
    VERDICT_KB_UNAVAILABLE,
    RagFailure,
    build_rag_block,
    build_rag_payload,
    merge_rag_block,
    rag_budget,
    sources_from_chunks,
)
from agent.rag_pipeline import (
    VERDICT_BELOW_THRESHOLD,
    config_from_row,
    mark_over_budget,
    run_retrieval_pipeline,
)
from agent.rag_turn import RagTurn, finalize_rag_turn
from agent.tool_guard import TOOL_USE_RULE, build_clock_line
from agent.tools import TOOL_REGISTRY, build_tool_schemas
from shared.logger import get_logger
from shared.models import HelpConfig, KbStatus, KnowledgeBase

logger = get_logger(__name__)

NOTICE_FRAME_TYPE = "help_notice"
NOTICE_USAGE = "usage"
NOTICE_NO_BASE = "no_base"

TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "memory": ("save_working_memory", "save_long_term_memory"),
    "tasks": ("create_task", "transition_task", "pause_task", "resume_task"),
    "scheduler": ("schedule_task", "list_scheduled_tasks", "cancel_scheduled_task"),
}


@dataclass(frozen=True)
class CommandSpec:
    """Texts and identity that tell one slash command apart from another."""

    name: str
    payload_key: str
    default_server_name: str
    usage_text: str
    no_base_text: str
    base_not_ready_text: str
    mcp_warning_text: str
    system_prompt: str
    fragments_instruction: str
    no_fragments_note: str
    notice_command: str | None = None


@dataclass
class CommandTools:
    """The tools of one command turn and the warnings about chosen servers that are unavailable."""

    schemas: list[dict[str, Any]] = field(default_factory=list)
    toolset: McpToolset = field(default_factory=McpToolset.empty)
    allowed_tools: frozenset[str] = frozenset()
    warnings: list[dict[str, str]] = field(default_factory=list)


def notice_frame(kind: str, text: str, command: str | None = None) -> dict[str, Any]:
    """Build the service notice frame; such a notice is never stored in the chat."""
    frame: dict[str, Any] = {
        "type": NOTICE_FRAME_TYPE, "kind": kind, "text": text, "stored": False,
    }
    if command is not None:
        frame["command"] = command
    return frame


async def load_help_config(session: AsyncSession, user_id: int) -> HelpConfig:
    """Return the user's /help settings, or unsaved defaults when none are stored."""
    row = await session.get(HelpConfig, user_id)
    if row is None:
        return HelpConfig(user_id=user_id)
    return row


async def load_help_kb(
    session: AsyncSession, user_id: int, config: HelpConfig
) -> KnowledgeBase | None:
    """Return the marked base when it exists, is owned by the user and is ready."""
    if config.kb_id is None:
        return None
    kb = await session.get(KnowledgeBase, config.kb_id)
    if kb is None:
        return None
    if kb.user_id != user_id:
        logger.warning("help_kb_foreign", kb_id=config.kb_id, user_id=user_id)
        return None
    if kb.status != KbStatus.READY:
        return None
    return kb


async def resolve_command_notice(
    session: AsyncSession, user_id: int | None, question: str, spec: CommandSpec
) -> dict[str, Any] | None:
    """Return the notice frame for a command turn that must not call the model, else None."""
    if not question:
        logger.info(f"{spec.name}_notice_sent", kind=NOTICE_USAGE, user_id=user_id)
        return notice_frame(NOTICE_USAGE, spec.usage_text, spec.notice_command)
    text = spec.no_base_text
    if user_id is not None:
        config = await load_help_config(session, user_id)
        if await load_help_kb(session, user_id, config) is not None:
            return None
        if config.kb_id is not None:
            marked = await session.get(KnowledgeBase, config.kb_id)
            if marked is not None and marked.user_id == user_id:
                text = spec.base_not_ready_text.format(name=marked.name)
    logger.info(f"{spec.name}_notice_sent", kind=NOTICE_NO_BASE, user_id=user_id)
    return notice_frame(NOTICE_NO_BASE, text, spec.notice_command)


def stored_servers(config: Any, log_prefix: str = "help") -> list[dict[str, Any]] | None:
    """Return the stored server choice, or None when the default applies."""
    raw = config.mcp_servers_json
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"{log_prefix}_servers_malformed", user_id=config.user_id)
        return None
    if not isinstance(data, list):
        logger.warning(f"{log_prefix}_servers_malformed", user_id=config.user_id)
        return None
    return [
        {"id": item.get("id"), "name": item.get("name")}
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    ]


async def select_tools(
    session: AsyncSession,
    user_id: int,
    config: Any,
    spec: CommandSpec,
    *,
    toolset_builder: Callable[..., Awaitable[McpToolset]],
) -> CommandTools:
    """Select the built-in groups and whole MCP servers the user chose for command turns."""
    rows = await mcp_config.list_servers(session, user_id)
    by_id = {row.id: row for row in rows if row.id is not None}
    chosen_ids: list[int] = []
    missing_names: list[str] = []
    stored = stored_servers(config, spec.name)
    if stored is None:
        chosen_ids = [row.id for row in rows if row.name == spec.default_server_name and row.id]
        if not chosen_ids:
            missing_names.append(spec.default_server_name)
    else:
        for entry in stored:
            if entry["id"] in by_id:
                chosen_ids.append(entry["id"])
            else:
                missing_names.append(str(entry["name"] or entry["id"]))

    toolset = McpToolset.empty()
    if chosen_ids:
        try:
            toolset = await toolset_builder(
                session, user_id, set(TOOL_REGISTRY), only_server_ids=set(chosen_ids)
            )
        except Exception as exc:  # an MCP problem never breaks a command turn
            logger.warning(
                f"{spec.name}_mcp_toolset_failed",
                user_id=user_id,
                error_type=type(exc).__name__,
            )
            toolset = McpToolset.empty()

    live_ids = {binding.server_id for binding in toolset.bindings.values()}
    names: list[str] = []
    for server_id in chosen_ids:
        if server_id not in live_ids:
            names.append(by_id[server_id].name)
    names = missing_names + names
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        warnings.append({"server": name, "text": spec.mcp_warning_text.format(name=name)})

    allowed: set[str] = set()
    for group, enabled in (
        ("memory", config.tools_memory),
        ("tasks", config.tools_tasks),
        ("scheduler", config.tools_scheduler),
    ):
        if enabled:
            allowed.update(TOOL_GROUPS[group])
    schemas = [
        schema for schema in build_tool_schemas() if schema["function"]["name"] in allowed
    ] + list(toolset.schemas)
    return CommandTools(
        schemas=schemas,
        toolset=toolset,
        allowed_tools=frozenset(allowed),
        warnings=warnings,
    )


def build_command_messages(
    spec: CommandSpec,
    question: str,
    has_tools: bool,
    now: datetime,
    history: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build the whole outbound list of a command turn: optional history, no profile or invariants."""
    system = f"{spec.system_prompt}\n\n{build_clock_line(now)}"
    if has_tools:
        system = f"{system}\n\n{TOOL_USE_RULE}"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system, "token_count": count_tokens(system)}
    ]
    for user_text, assistant_text in history or []:
        messages.append(
            {"role": "user", "content": user_text, "token_count": count_tokens(user_text)}
        )
        messages.append(
            {
                "role": "assistant",
                "content": assistant_text,
                "token_count": count_tokens(assistant_text),
            }
        )
    messages.append({"role": "user", "content": question, "token_count": count_tokens(question)})
    return messages


def prepend_to_last_user(llm_messages: list[dict[str, Any]], prefix: str) -> None:
    """Prefix the last user message with text and the question label."""
    for position in range(len(llm_messages) - 1, -1, -1):
        message = llm_messages[position]
        if message.get("role") != "user":
            continue
        merged = f"{prefix}\n\n{QUESTION_PREFIX}{message['content']}"
        llm_messages[position] = {**message, "content": merged, "token_count": count_tokens(merged)}
        return


def command_payload(
    spec: CommandSpec,
    kb: KnowledgeBase,
    config: HelpConfig,
    warnings: list[dict[str, str]],
    extra: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """RAG payload of a command turn plus the additive command key (label and tool warnings)."""
    base = build_rag_payload(
        mode=MODE_RAG, kb_id=kb.id, kb_name=kb.name, top_k=config.top_k, **fields
    )
    return {
        **base,
        spec.payload_key: {"kb_name": kb.name, "warnings": list(warnings), **(extra or {})},
    }


def _failed_turn(
    spec: CommandSpec,
    kb: KnowledgeBase,
    config: HelpConfig,
    warnings: list[dict[str, str]],
    payload_extra: dict[str, Any] | None,
    code: str,
    text: str,
) -> RagTurn:
    """A turn that continues without fragments and carries the retrieval warning."""
    payload = command_payload(
        spec, kb, config, warnings, payload_extra,
        sources=[], dropped=0, context_tokens=0,
        warning={"code": code, "text": text},
        verdict=VERDICT_KB_UNAVAILABLE, search=None, strict=False, gated=False,
    )
    return RagTurn(MODE_RAG, payload)


async def prepare_command_turn(
    spec: CommandSpec,
    session: AsyncSession,
    chat: Any,
    kb: KnowledgeBase,
    config: HelpConfig,
    question: str,
    llm_messages: list[dict[str, Any]],
    context_length: int,
    max_tokens: int,
    extra_tokens: int,
    *,
    client: Any | None,
    model: str | None,
    warnings: list[dict[str, str]],
    context_block: str | None = None,
    retrieval_query: str | None = None,
    payload_extra: dict[str, Any] | None = None,
) -> RagTurn:
    """Retrieve fragments with the command settings; never gates, never raises except on cancel."""
    merged = False
    try:
        pipeline_config = config_from_row(config, kb, None)  # type: ignore[arg-type]
        chunks, trace = await run_retrieval_pipeline(
            session, kb, retrieval_query or question, pipeline_config, client, model
        )
        verdict = trace.pop("verdict")
        if verdict == VERDICT_BELOW_THRESHOLD or not chunks:
            prefix = spec.no_fragments_note
            if context_block:
                prefix = f"{prefix}\n\n{context_block}"
            prepend_to_last_user(llm_messages, prefix)
            merged = True
            turn = RagTurn(
                MODE_RAG,
                command_payload(
                    spec, kb, config, warnings, payload_extra,
                    sources=[], dropped=0, context_tokens=0, warning=None,
                    verdict=verdict, search=trace, strict=False, gated=False,
                ),
            )
        else:
            used = _message_tokens(llm_messages) + extra_tokens
            if context_block:
                used += count_tokens(context_block)
            budget = rag_budget(context_length, used, max_tokens)
            block, kept, dropped = build_rag_block(
                chunks, budget, strict=True, instruction=spec.fragments_instruction
            )
            if block is None:
                raise RagFailure("context_full", MSG_CONTEXT_FULL)
            full_block = f"{block}\n\n{context_block}" if context_block else block
            if not merge_rag_block(llm_messages, full_block):
                raise RagFailure("retrieval_failed", MSG_RETRIEVAL_FAILED)
            merged = True
            mark_over_budget(trace, len(kept))
            turn = RagTurn(
                MODE_RAG,
                command_payload(
                    spec, kb, config, warnings, payload_extra,
                    sources=sources_from_chunks(kept), dropped=dropped,
                    context_tokens=count_tokens(block), warning=None,
                    verdict=verdict, search=trace, strict=True, gated=False,
                ),
                kept_chunks=kept,
                strict=True,
            )
    except asyncio.CancelledError:
        raise
    except RagFailure as exc:
        if context_block and not merged:
            prepend_to_last_user(llm_messages, context_block)
        turn = _failed_turn(spec, kb, config, warnings, payload_extra, exc.code, exc.text)
    except Exception as exc:
        logger.error(f"{spec.name}_turn_failed", error_type=type(exc).__name__)
        if isinstance(exc, SQLAlchemyError):
            await session.rollback()
        if context_block and not merged:
            prepend_to_last_user(llm_messages, context_block)
        turn = _failed_turn(
            spec, kb, config, warnings, payload_extra, "retrieval_failed", MSG_RETRIEVAL_FAILED
        )
    warning = turn.payload["warning"]
    logger.info(
        f"{spec.name}_turn_prepared",
        kb_id=kb.id,
        kept=len(turn.payload["sources"]),
        dropped=turn.payload["dropped"],
        verdict=turn.payload["verdict"],
        warning=warning["code"] if warning else None,
    )
    return turn


def finalize_command_turn(
    turn: RagTurn,
    question: str,
    assistant_text: str,
    tool_results: list[dict[str, Any]],
    log_prefix: str = "help",
) -> tuple[str, RagTurn]:
    """Verify quotes, then drop code-picked quotes from a tools-only answer."""
    text, result = finalize_rag_turn(turn, question, assistant_text)
    try:
        if not any(item.get("ok") is True for item in tool_results):
            return text, result
        quotes = result.payload.get("quotes") or []
        if any(not q.get("auto") and q.get("state") != "unverified" for q in quotes):
            return text, result
        payload = {**result.payload, "quotes": [], "answer_supported": None}
        return text, replace(result, payload=payload)
    except Exception as exc:
        logger.error(f"{log_prefix}_cite_cleanup_failed", error_type=type(exc).__name__)
        return text, result
