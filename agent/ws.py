"""WebSocket chat endpoint and multi-tab broadcasting."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.context_engine import (
    ContextOverflowError,
    build_llm_context,
    compute_chat_stats,
    extract_and_update_facts,
    get_effective_settings,
    serialize_tool_trace,
)
from agent.dependencies import get_current_user_ws
from agent import invariants, tasks
from agent.llm_client import llm_client
from agent.mcp_tools import McpToolset, build_mcp_toolset
from agent.state import (
    CORS_ORIGINS,
    active_streams,
    chat_locks,
    current_chat_model,
    ws_rate_limiter,
)
from agent.schemas import (
    TOOL_EVENT_ARGS_PREVIEW_CHARS,
    TOOL_EVENT_PREVIEW_CHARS,
    MessagePayload,
    ToolCallEvent,
)
from agent.text_tool_calls import TextToolCallFilter
from agent.tool_guard import (
    ACTION_ANNOUNCE_REMINDER,
    ACTION_CLAIM_REMINDER,
    MAX_TOOL_ROUNDS,
    MULTI_STEP_TOOL_HINT,
    TOOL_ERROR_REMINDER,
    TOOL_USE_RULE,
    TraceLeakFilter,
    build_clock_line,
    build_tool_fallback_summary,
    looks_like_action_announcement,
    looks_like_action_claim,
    strip_tool_use_rule,
)
from agent.tools import TOOL_REGISTRY, build_tool_schemas, dispatch_tool_calls
from shared.auth import SESSION_COOKIE_NAME
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Chat, Message

logger = get_logger(__name__)

IDLE_TIMEOUT_SECONDS = 300.0
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60.0
TASK_TOOL_NAMES = ("create_task", "transition_task", "pause_task", "resume_task")
SCHEDULER_TOOL_NAMES = ("schedule_task", "list_scheduled_tasks", "cancel_scheduled_task")
NON_MEMORY_TOOL_NAMES = TASK_TOOL_NAMES + SCHEDULER_TOOL_NAMES

active_connections: set[WebSocket] = set()


def _normalize_tool_calls_for_echo(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy tool calls for the follow-up request, replacing empty arguments with "{}".

    Providers reject an empty string as function arguments, and a no-argument call
    can legitimately arrive that way. The originals are left untouched.
    """
    normalized: list[dict[str, Any]] = []
    for call in tool_calls:
        call_copy = dict(call)
        function = dict(call_copy.get("function") or {})
        arguments = function.get("arguments")
        if not isinstance(arguments, str) or not arguments.strip():
            function["arguments"] = "{}"
        call_copy["function"] = function
        normalized.append(call_copy)
    return normalized


def _preview(text: str, limit: int) -> tuple[str, bool]:
    """Cut text to the limit and report whether it was cut."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _tool_call_frame(result: dict[str, Any]) -> dict[str, Any]:
    """Build the tool_call WebSocket frame for one dispatch result."""
    mcp_info = result.get("mcp")
    arguments, _ = _preview(result.get("arguments", ""), TOOL_EVENT_ARGS_PREVIEW_CHARS)
    preview, cut = _preview(
        result.get("result_text", result["content"]),
        TOOL_EVENT_PREVIEW_CHARS,
    )
    return ToolCallEvent(
        tool_call_id=result.get("tool_call_id"),
        name=result.get("name"),
        server=mcp_info["server_name"] if mcp_info else None,
        tool=mcp_info["tool"] if mcp_info else result.get("name"),
        arguments=arguments,
        ok=result["ok"],
        result=preview,
        truncated=bool(result.get("truncated")) or cut,
    ).model_dump()


async def _load_mcp_toolset(session: AsyncSession, user_id: int, chat_id: int) -> McpToolset:
    """Build the per-turn MCP toolset; an MCP problem must never break the chat turn."""
    try:
        return await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
    except Exception as exc:
        logger.warning("mcp_toolset_failed", chat_id=chat_id, error=str(exc))
        return McpToolset.empty()


def _validate_origin(websocket: WebSocket) -> bool:
    """Return True when the request Origin header is allowed or missing (single-user mode)."""
    origin = websocket.headers.get("origin")
    logger.info("ws_origin_check", origin=origin or "None")

    if origin is None:
        logger.info("ws_origin_allowed", reason="no_origin")
        return True

    if origin in CORS_ORIGINS:
        logger.info("ws_origin_allowed", reason="cors_origins", origin=origin)
        return True

    if origin == "null":
        logger.info("ws_origin_allowed", reason="null_origin")
        return True

    allowed_patterns = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]
    if origin in allowed_patterns:
        logger.info("ws_origin_allowed", reason="allowed_pattern", origin=origin)
        return True

    logger.warning("ws_origin_rejected", origin=origin)
    return False


async def _user_owns_chat(db: AsyncSession, user_id: int, chat_id: int) -> bool:
    """Return True when chat_id exists and belongs to user_id."""
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        logger.warning("ws_auth_rejected", chat_id=chat_id, reason="not_owner")
        return False
    return True


def _check_rate_limit(chat_id: int) -> bool:
    """Allow up to RATE_LIMIT_MAX messages per chat per minute."""
    now = time.monotonic()
    timestamps = ws_rate_limiter.setdefault(chat_id, [])
    ws_rate_limiter[chat_id] = [
        ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW
    ]
    if len(ws_rate_limiter[chat_id]) >= RATE_LIMIT_MAX:
        return False
    ws_rate_limiter[chat_id].append(now)
    return True


async def _persist_user_message(
    session: AsyncSession,
    chat: Chat,
    content: str,
) -> Message:
    """Insert a user message and advance the chat leaf."""
    user_msg = Message(
        chat_id=chat.id,
        parent_id=chat.current_leaf_message_id,
        role="user",
        content=content,
        token_count=llm_client.count_tokens(content),
    )
    session.add(user_msg)
    await session.flush()
    chat.current_leaf_message_id = user_msg.id
    session.add(chat)
    await session.commit()
    await session.refresh(user_msg)
    return user_msg


async def _persist_assistant_message(
    session: AsyncSession,
    chat: Chat,
    parent_id: int,
    content: str,
    *,
    tool_trace: str | None = None,
) -> Message:
    """Insert an assistant message and advance the chat leaf."""
    assistant_msg = Message(
        chat_id=chat.id,
        parent_id=parent_id,
        role="assistant",
        content=content,
        token_count=llm_client.count_tokens(content),
        tool_trace=tool_trace,
    )
    session.add(assistant_msg)
    await session.flush()
    chat.current_leaf_message_id = assistant_msg.id
    session.add(chat)
    await session.commit()
    await session.refresh(assistant_msg)
    return assistant_msg


class _StreamFilter(Protocol):
    """Anything that turns raw streamed chunks into text that is safe to show."""

    def feed(self, chunk: str) -> str:
        """Return the part of the chunk that is safe to emit now."""

    def flush(self) -> str:
        """Return any withheld text once the stream ends."""


class _ReplyFilter:
    """Streaming filter that captures text-written tool calls, then drops tool-trace imitations."""

    def __init__(self) -> None:
        self._text_calls = TextToolCallFilter()
        self._trace = TraceLeakFilter()

    def feed(self, chunk: str) -> str:
        """Return the part of the chunk that is safe to emit now."""
        return self._trace.feed(self._text_calls.feed(chunk))

    def flush(self) -> str:
        """Return any withheld text once the stream ends."""
        return self._trace.feed(self._text_calls.flush()) + self._trace.flush()

    def recovered_calls(self, tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the tool calls the model wrote as text during this stream."""
        return self._text_calls.captured_calls(tool_schemas)


async def _emit_filtered(websocket: WebSocket, trace_filter: _StreamFilter, token: str) -> str:
    """Send the filtered part of a token to the client and return what was sent."""
    safe = trace_filter.feed(token)
    if safe:
        await websocket.send_json({"type": "token", "content": safe})
    return safe


async def _flush_filtered(websocket: WebSocket, trace_filter: _StreamFilter) -> str:
    """Send any text the filter still withholds at stream end and return it."""
    safe = trace_filter.flush()
    if safe:
        await websocket.send_json({"type": "token", "content": safe})
    return safe


def _recover_text_calls(
    reply_filter: _ReplyFilter,
    tool_schemas: list[dict[str, Any]] | None,
    chat_id: int,
) -> list[dict[str, Any]]:
    """Return tool calls the model wrote as text, or [] when tools were not offered."""
    if not tool_schemas:
        return []
    recovered = reply_filter.recovered_calls(tool_schemas)
    if recovered:
        logger.info("text_tool_calls_recovered", chat_id=chat_id, count=len(recovered))
    return recovered


async def _stream_action_claim_retry(
    websocket: WebSocket,
    llm_messages: list[dict[str, Any]],
    payload: MessagePayload,
    temperature: float,
    max_tokens: int,
    tool_schemas: list[dict[str, Any]],
    chat_id: int,
) -> tuple[str, list[dict[str, Any]]]:
    """Stream the single corrective re-prompt; a failure keeps whatever was already sent."""
    retry_text = ""
    retry_tool_calls: list[dict[str, Any]] = []
    trace_filter = _ReplyFilter()
    active_streams[chat_id] = asyncio.current_task()

    async def send_retry_text(safe: str) -> None:
        """Send filtered retry text, preceded by a separator before the first chunk."""
        nonlocal retry_text
        if not safe:
            return
        if not retry_text:
            await websocket.send_json({"type": "token", "content": "\n\n"})
        retry_text += safe
        await websocket.send_json({"type": "token", "content": safe})

    try:
        async for event in llm_client.stream_chat(
            llm_messages,
            payload.model,
            temperature,
            max_tokens,
            tools=tool_schemas,
        ):
            if event["type"] == "content":
                await send_retry_text(trace_filter.feed(event["content"]))
            elif event["type"] == "tool_calls":
                retry_tool_calls = event["tool_calls"]
    except Exception as exc:
        logger.warning("action_claim_reprompt_failed", chat_id=chat_id, error=str(exc))
    finally:
        active_streams.pop(chat_id, None)
    await send_retry_text(trace_filter.flush())
    if not retry_tool_calls:
        retry_tool_calls = _recover_text_calls(trace_filter, tool_schemas, chat_id)
    return retry_text, retry_tool_calls


@dataclass
class _ToolTurn:
    """Per-turn context shared by the tool-round helpers."""

    websocket: WebSocket
    session: AsyncSession
    chat: Chat
    chat_id: int
    payload: MessagePayload
    llm_messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    toolset: McpToolset
    temperature: float
    max_tokens: int
    allowed_tools: frozenset[str] | None = None


@dataclass
class _ToolRoundsResult:
    """Everything the tool rounds of one turn produced."""

    results: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    rounds: int = 0
    error: Exception | None = None
    empty_retry_used: bool = False
    announce_nudge_used: bool = False
    error_nudge_used: bool = False


async def _stream_follow_up(
    turn: _ToolTurn,
    tools: list[dict[str, Any]] | None,
    separator: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Stream one follow-up; with tools it may return further tool calls, without it is text only.

    With separator, a blank line is sent before the first chunk so the text does not run
    into what was already shown.
    """
    text = ""
    tool_calls: list[dict[str, Any]] = []
    reply_filter = _ReplyFilter()

    async def send(safe: str) -> None:
        """Send filtered text, preceded by the separator before the first chunk."""
        nonlocal text
        if not safe:
            return
        if separator and not text:
            await turn.websocket.send_json({"type": "token", "content": "\n\n"})
            text += "\n\n"
        await turn.websocket.send_json({"type": "token", "content": safe})
        text += safe

    async for event in llm_client.stream_chat(
        turn.llm_messages,
        turn.payload.model,
        turn.temperature,
        turn.max_tokens,
        tools=tools,
    ):
        if not tools:
            await send(reply_filter.feed(event))
        elif event["type"] == "content":
            await send(reply_filter.feed(event["content"]))
        elif event["type"] == "tool_calls":
            tool_calls = event["tool_calls"]
    await send(reply_filter.flush())
    if not tool_calls:
        tool_calls = _recover_text_calls(reply_filter, tools, turn.chat_id)
    return text, tool_calls


async def _stream_follow_up_with_empty_retry(
    turn: _ToolTurn,
    tools: list[dict[str, Any]] | None,
    acc: _ToolRoundsResult,
    separator: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Stream a follow-up into acc.text; retry once without tools if it came back empty."""
    text, tool_calls = await _stream_follow_up(turn, tools, separator)
    acc.text += text
    # Some local models answer nothing when tools are offered right after a tool result.
    if not tools or tool_calls or text.strip() or acc.empty_retry_used:
        return text, tool_calls
    acc.empty_retry_used = True
    logger.info("tool_followup_empty_retry", chat_id=turn.chat_id, round=acc.rounds)
    retry_text, _ = await _stream_follow_up(turn, None, separator)
    acc.text += retry_text
    return retry_text, []


def _call_signatures(tool_calls: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return (name, canonical arguments) pairs used to spot a repeated call."""
    signatures: set[tuple[str, str]] = set()
    for call in tool_calls:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str) or not arguments.strip():
            canonical = "{}"
        else:
            try:
                canonical = json.dumps(json.loads(arguments), sort_keys=True)
            except json.JSONDecodeError:
                canonical = arguments.strip()
        signatures.add((str(function.get("name")), canonical))
    return signatures


async def _dispatch_round(
    turn: _ToolTurn,
    calls: list[dict[str, Any]],
    echo_text: str,
    acc: _ToolRoundsResult,
) -> list[dict[str, Any]]:
    """Run one round of tool calls, report them to the client and extend the LLM context."""
    results = await dispatch_tool_calls(
        turn.session,
        turn.chat.user_id,
        turn.chat_id,
        calls,
        mcp_bindings=turn.toolset.bindings,
        allowed_tools=turn.allowed_tools,
    )
    for result in results:
        await turn.websocket.send_json(_tool_call_frame(result))

    turn.llm_messages.append(
        {
            "role": "assistant",
            "content": echo_text,
            "tool_calls": _normalize_tool_calls_for_echo(calls),
        },
    )
    for result in results:
        turn.llm_messages.append(
            {
                "role": "tool",
                "tool_call_id": result["tool_call_id"],
                "content": result["content"],
            },
        )
    acc.results.extend(results)
    acc.calls.extend(calls)
    acc.rounds += 1
    return results


def _pick_nudge(acc: _ToolRoundsResult, text: str, last_results: list[dict[str, Any]]) -> str | None:
    """Return the kind of nudge the follow-up text calls for, marking it used, or None."""
    if not acc.announce_nudge_used and looks_like_action_announcement(text):
        acc.announce_nudge_used = True
        return "announce"
    mcp_failed = any(not r["ok"] and r.get("mcp") is not None for r in last_results)
    # Only MCP failures: native task-tool errors have their own transition re-prompt.
    if not acc.error_nudge_used and mcp_failed:
        acc.error_nudge_used = True
        return "error"
    return None


async def _stream_nudge(
    turn: _ToolTurn,
    tools: list[dict[str, Any]],
    acc: _ToolRoundsResult,
    text: str,
    kind: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Send the model one corrective re-prompt and stream its answer, tools still on offer."""
    reminder = ACTION_ANNOUNCE_REMINDER if kind == "announce" else TOOL_ERROR_REMINDER
    logger.info("tool_round_nudge", chat_id=turn.chat_id, kind=kind, rounds=acc.rounds)
    turn.llm_messages.append({"role": "assistant", "content": text})
    turn.llm_messages.append({"role": "user", "content": reminder})
    return await _stream_follow_up_with_empty_retry(turn, tools, acc, separator=True)


async def _run_tool_rounds(
    turn: _ToolTurn,
    first_calls: list[dict[str, Any]],
    first_echo_text: str,
) -> _ToolRoundsResult:
    """Dispatch tool calls round by round until the model answers in text or a bound is hit."""
    acc = _ToolRoundsResult()
    calls = first_calls
    echo_text = first_echo_text
    while True:
        last_results = await _dispatch_round(turn, calls, echo_text, acc)
        if acc.rounds == 1:
            # After a tool round the rule makes local models answer empty; drop it for
            # the rest of the turn (the shared list also covers later re-prompts).
            strip_tool_use_rule(turn.llm_messages)
        tools = turn.tool_schemas if acc.rounds < MAX_TOOL_ROUNDS else None
        if tools is None:
            logger.warning("tool_rounds_capped", chat_id=turn.chat_id, rounds=acc.rounds)
        try:
            text, next_calls = await _stream_follow_up_with_empty_retry(turn, tools, acc)
            if not next_calls and tools is not None:
                kind = _pick_nudge(acc, text, last_results)
                if kind is not None:
                    text, next_calls = await _stream_nudge(turn, tools, acc, text, kind)
            if next_calls and _call_signatures(next_calls) & _call_signatures(calls):
                logger.warning("tool_loop_detected", chat_id=turn.chat_id, rounds=acc.rounds)
                # The repeated call is not dispatched; the text-only stream ends the turn.
                text, next_calls = await _stream_follow_up(turn, None)
                acc.text += text
                next_calls = []
        except Exception as exc:
            acc.error = exc
            return acc
        if not next_calls:
            return acc
        calls = next_calls
        echo_text = text


def _collect_memory_writes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the successful memory writes, leaving out task and scheduler tool results."""
    return [
        r["write"]
        for r in results
        if r["ok"] and r["write"] is not None and r["name"] not in NON_MEMORY_TOOL_NAMES
    ]


def _collect_task_writes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return id/title/state of the tasks touched by successful task tool calls."""
    task_writes: list[dict[str, Any]] = []
    for result in results:
        if result["ok"] and result["name"] in TASK_TOOL_NAMES:
            try:
                task_result = json.loads(result["content"])
            except json.JSONDecodeError:
                continue
            task_writes.append(
                {
                    "id": task_result.get("id"),
                    "title": task_result.get("title"),
                    "state": task_result.get("state"),
                },
            )
    return task_writes


async def _send_tool_error_frames(
    websocket: WebSocket,
    results: list[dict[str, Any]],
) -> None:
    """Send a TOOL_ERROR frame for every failed non-MCP tool call."""
    for result in results:
        # MCP failures are shown on their tool_call card; an error frame would
        # make the client stop streaming mid-turn.
        if not result["ok"] and result.get("mcp") is None:
            try:
                error_detail = json.loads(result["content"]).get(
                    "error", result["content"],
                )
            except json.JSONDecodeError:
                error_detail = result["content"]
            await websocket.send_json(
                {
                    "type": "error",
                    "detail": error_detail,
                    "code": "TOOL_ERROR",
                },
            )


def _collect_rejected_transitions(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the parsed illegal_transition errors among the tool results."""
    rejected: list[dict[str, Any]] = []
    for result in results:
        if result["ok"] or result.get("name") != "transition_task":
            continue
        try:
            parsed = json.loads(result["content"])
        except json.JSONDecodeError:
            continue
        if parsed.get("code") != "illegal_transition":
            continue
        rejected.append(parsed)
    return rejected


async def _reprompt_rejected_transitions(
    turn: _ToolTurn,
    rejected: list[dict[str, Any]],
) -> str:
    """Ask the model to explain rejected task transitions and return the streamed text."""
    if not rejected:
        return ""
    turn.llm_messages.append(
        {
            "role": "user",
            "content": tasks.build_transition_illegal_prompt(rejected),
        },
    )
    text = ""
    trace_filter = TraceLeakFilter()
    try:
        async for token in llm_client.stream_chat(
            turn.llm_messages,
            turn.payload.model,
            turn.temperature,
            turn.max_tokens,
        ):
            text += await _emit_filtered(turn.websocket, trace_filter, token)
    except Exception as exc:
        logger.warning(
            "transition_illegal_reprompt_failed",
            chat_id=turn.chat_id,
            error=str(exc),
        )
    text += await _flush_filtered(turn.websocket, trace_filter)
    return text


async def _send_fallback_summary(
    websocket: WebSocket,
    results: list[dict[str, Any]],
    user_text: str,
    shown_text: str,
    chat_id: int,
) -> str:
    """Send the fixed tool-result summary as one token frame and return the text sent."""
    logger.info("tool_reply_fallback_summary", chat_id=chat_id, results=len(results))
    summary = build_tool_fallback_summary(results, user_text)
    if shown_text.strip():
        summary = "\n\n" + summary
    await websocket.send_json({"type": "token", "content": summary})
    return summary


async def _handle_chat_message(
    websocket: WebSocket,
    chat_id: int,
    payload: MessagePayload,
) -> None:
    """Process one inbound chat message under the per-chat lock."""
    current_chat_model.set(payload.model)
    if chat_id not in chat_locks:
        chat_locks[chat_id] = asyncio.Lock()
    async with chat_locks[chat_id]:
        async with async_session_factory() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None:
                await websocket.send_json(
                    {"type": "error", "detail": f"Chat {chat_id} not found"},
                )
                return

            user_msg = await _persist_user_message(
                session,
                chat,
                payload.content,
            )

            effective = await get_effective_settings(session, chat_id)
            logger.info(
                "strategy_selected",
                chat_id=chat_id,
                strategy=effective.strategy,
            )

            try:
                llm_messages = await build_llm_context(
                    session,
                    chat_id,
                    payload.model,
                )
            except ContextOverflowError as exc:
                logger.error(
                    "context_overflow_blocked",
                    chat_id=chat_id,
                    error=str(exc),
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": str(exc),
                        "code": "CONTEXT_OVERFLOW",
                        "suggested_strategy": "sliding",
                    },
                )
                await session.delete(user_msg)
                await session.commit()
                return

            temperature = effective.temperature
            max_tokens = effective.max_tokens

            toolset = McpToolset.empty()
            if chat.user_id is not None:
                toolset = await _load_mcp_toolset(session, chat.user_id, chat_id)
                tool_schemas = build_tool_schemas() + toolset.schemas
            else:
                tool_schemas = None
                logger.warning("tools_disabled_unowned_chat", chat_id=chat_id)

            if llm_messages and llm_messages[0]["role"] == "system":
                # The clock and the multi-step hint stay for the whole turn; only the
                # rule is removable, so it must remain the last suffix.
                suffix = "\n\n" + build_clock_line(datetime.now(timezone.utc).astimezone())
                if tool_schemas:
                    suffix += "\n\n" + MULTI_STEP_TOOL_HINT + "\n\n" + TOOL_USE_RULE
                llm_messages[0] = {
                    **llm_messages[0],
                    "content": llm_messages[0]["content"] + suffix,
                }

            assistant_text = ""
            pending_tool_calls: list[dict[str, Any]] = []
            stream_task = asyncio.current_task()
            active_streams[chat_id] = stream_task

            try:
                if tool_schemas:
                    reply_filter = _ReplyFilter()
                    async for event in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                        tools=tool_schemas,
                    ):
                        if event["type"] == "content":
                            assistant_text += await _emit_filtered(
                                websocket, reply_filter, event["content"],
                            )
                        elif event["type"] == "tool_calls":
                            pending_tool_calls = event["tool_calls"]
                    assistant_text += await _flush_filtered(websocket, reply_filter)
                    if not pending_tool_calls:
                        pending_tool_calls = _recover_text_calls(
                            reply_filter, tool_schemas, chat_id,
                        )
                else:
                    trace_filter = TraceLeakFilter()
                    async for token in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                    ):
                        assistant_text += await _emit_filtered(websocket, trace_filter, token)
                    assistant_text += await _flush_filtered(websocket, trace_filter)
            except Exception as exc:
                logger.error(
                    "llm_stream_failed",
                    chat_id=chat_id,
                    error=str(exc),
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": f"LLM error: {str(exc)}",
                        "code": "LLM_ERROR",
                    },
                )
                await session.delete(user_msg)
                await session.commit()
                return
            finally:
                active_streams.pop(chat_id, None)

            echo_text = assistant_text
            claim = bool(tool_schemas) and looks_like_action_claim(assistant_text)
            announce = bool(tool_schemas) and looks_like_action_announcement(assistant_text)
            if tool_schemas and not pending_tool_calls and (claim or announce):
                # One bounded retry: never re-checked, so a stubborn model cannot loop.
                logger.info(
                    "action_claim_without_tool" if claim else "action_announce_without_tool",
                    chat_id=chat_id,
                )
                llm_messages.append({"role": "assistant", "content": assistant_text})
                llm_messages.append(
                    {
                        "role": "user",
                        "content": ACTION_CLAIM_REMINDER if claim else ACTION_ANNOUNCE_REMINDER,
                    },
                )
                retry_text, pending_tool_calls = await _stream_action_claim_retry(
                    websocket,
                    llm_messages,
                    payload,
                    temperature,
                    max_tokens,
                    tool_schemas,
                    chat_id,
                )
                if retry_text:
                    assistant_text += "\n\n" + retry_text
                if pending_tool_calls:
                    echo_text = retry_text

            memory_writes: list[dict[str, Any]] = []
            task_writes: list[dict[str, Any]] = []
            tool_trace: str | None = None
            if pending_tool_calls:
                turn = _ToolTurn(
                    websocket=websocket,
                    session=session,
                    chat=chat,
                    chat_id=chat_id,
                    payload=payload,
                    llm_messages=llm_messages,
                    tool_schemas=tool_schemas,
                    toolset=toolset,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                rounds = await _run_tool_rounds(turn, pending_tool_calls, echo_text)
                if rounds.error is not None:
                    logger.error(
                        "llm_stream_failed",
                        chat_id=chat_id,
                        error=str(rounds.error),
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": f"LLM error: {str(rounds.error)}",
                            "code": "LLM_ERROR",
                        },
                    )
                    await session.delete(user_msg)
                    await session.commit()
                    return

                assistant_text += rounds.text
                tool_trace = serialize_tool_trace(rounds.results)
                memory_writes = _collect_memory_writes(rounds.results)
                task_writes = _collect_task_writes(rounds.results)
                await _send_tool_error_frames(websocket, rounds.results)
                reprompt_text = await _reprompt_rejected_transitions(
                    turn,
                    _collect_rejected_transitions(rounds.results),
                )
                assistant_text += reprompt_text
                if not (rounds.text + reprompt_text).strip():
                    assistant_text += await _send_fallback_summary(
                        websocket, rounds.results, payload.content, assistant_text, chat_id,
                    )
                pending_tool_calls = rounds.calls

            active_invariants = await invariants.resolve_active_invariants(session, chat_id)
            flagged: dict[str, Any] | None = None
            justification_text = ""
            critique: dict[str, Any] = {"conflict": False}
            if active_invariants:
                critique = await invariants.run_self_critique(
                    active_invariants,
                    assistant_text,
                    pending_tool_calls,
                    payload.model,
                )
                flagged = invariants.match_flagged_invariant(active_invariants, critique)

            if flagged is not None:
                llm_messages.append(
                    {
                        "role": "user",
                        "content": invariants.build_justify_retract_prompt(flagged, critique),
                    },
                )
                trace_filter = TraceLeakFilter()
                try:
                    async for token in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                    ):
                        safe = await _emit_filtered(websocket, trace_filter, token)
                        justification_text += safe
                        assistant_text += safe
                except Exception as exc:
                    logger.warning(
                        "invariant_justify_retract_failed",
                        chat_id=chat_id,
                        error=str(exc),
                    )
                tail = await _flush_filtered(websocket, trace_filter)
                justification_text += tail
                assistant_text += tail

            assistant_msg = await _persist_assistant_message(
                session,
                chat,
                user_msg.id,
                assistant_text,
                tool_trace=tool_trace,
            )

            conflict_payload: dict[str, Any] | None = None
            if flagged is not None:
                conflict_row = await invariants.record_conflict(
                    session,
                    chat_id,
                    assistant_msg.id,
                    flagged["scope"],
                    flagged["id"],
                    flagged["title"],
                    justification_text or critique.get("explanation", ""),
                )
                conflict_payload = {
                    "id": conflict_row.id,
                    "chat_id": conflict_row.chat_id,
                    "message_id": conflict_row.message_id,
                    "invariant_scope": conflict_row.invariant_scope,
                    "invariant_id": conflict_row.invariant_id,
                    "invariant_title": conflict_row.invariant_title,
                    "note": conflict_row.note,
                    "created_at": conflict_row.created_at.isoformat(),
                }

            extract_and_update_facts(
                session,
                chat_id,
                payload.content,
                payload.model,
            )
            stats = await compute_chat_stats(session, chat_id, payload.model)
            await websocket.send_json(
                {
                    "type": "done",
                    "message_id": assistant_msg.id,
                    "stats": stats,
                    "memory_writes": memory_writes,
                    "task_writes": task_writes,
                    "invariant_conflict": conflict_payload,
                },
            )


async def broadcast_model_event(model_id: str, event_type: str) -> None:
    """Broadcast a model lifecycle event to every active WebSocket."""
    payload = {
        "type": "model_event",
        "model_id": model_id,
        "event": event_type,
    }
    for connection in list(active_connections):
        try:
            await connection.send_json(payload)
        except WebSocketDisconnect:
            active_connections.discard(connection)
        except Exception as exc:
            logger.warning("broadcast_failed", error=str(exc))
            active_connections.discard(connection)


async def ws_chat(websocket: WebSocket, chat_id: int) -> None:
    """Handle streaming chat over WebSocket with security guards."""
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
    async with async_session_factory() as db:
        user = await get_current_user_ws(session_id, db)
        if user is None:
            logger.warning("ws_auth_rejected", chat_id=chat_id, reason="no_session")
            await websocket.close(code=1008, reason="Unauthorized")
            return

        if not await _user_owns_chat(db, user.id, chat_id):
            await websocket.close(code=1008, reason="Unauthorized")
            return

    await websocket.accept()
    active_connections.add(websocket)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="Idle timeout")
                break

            if not _check_rate_limit(chat_id):
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": "Rate limit exceeded",
                    },
                )
                continue

            try:
                payload = MessagePayload.model_validate(raw)
            except ValidationError as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": exc.errors(),
                    },
                )
                continue

            await _handle_chat_message(websocket, chat_id, payload)
    except WebSocketDisconnect:
        logger.info("ws_disconnected", chat_id=chat_id)
    finally:
        active_connections.discard(websocket)


def register_websocket_routes(app: Any) -> None:
    """Attach the chat WebSocket route to the FastAPI app."""
    app.websocket("/ws/chat/{chat_id}")(ws_chat)
