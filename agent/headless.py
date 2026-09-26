"""Headless (socket-free) LLM turn used by scheduled jobs."""

# The tool loop (sequential calls, round cap, empty-answer retry, nudges, loop detection)
# lives in agent.ws behind underscore helpers. A headless turn deliberately reuses them
# instead of re-implementing the loop: they only ever call `send_json` on the "socket",
# so a recording sink stands in for it. The helpers are read as attributes of the module
# (not imported by name) so that patches applied to agent.ws are honoured here too.

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config, memory, ws
from agent.context_engine import serialize_tool_trace
from agent.mcp_tools import McpToolset, build_mcp_toolset
from agent.tool_guard import (
    MULTI_STEP_TOOL_HINT,
    TOOL_USE_RULE,
    build_clock_line,
    build_tool_fallback_summary,
)
from agent.tools import TOOL_REGISTRY, build_tool_schemas
from shared.logger import get_logger
from shared.models import Settings

logger = get_logger(__name__)

HEADLESS_CHAT_ID = 0
HEADLESS_TOOL_ALLOWLIST: frozenset[str] = frozenset({"save_long_term_memory"})
HEADLESS_PREFACE = (
    "You are running as an unattended scheduled job. No user is present: never ask "
    "questions or wait for confirmation. Complete the task with the available tools, "
    "then finish with a concise report of the result."
)
MCP_UNAVAILABLE_NOTE = "Примечание: MCP-инструменты были недоступны во время этого запуска."
EMPTY_ANSWER_MESSAGE = "Модель вернула пустой ответ"
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class HeadlessRunError(Exception):
    """A headless turn failed; `message` is the Russian text stored on the run verbatim."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class HeadlessResult:
    """Outcome of one headless turn."""

    text: str
    results: list[dict[str, Any]]
    tool_trace: str | None
    mcp_tool_count: int


class RecordingSink:
    """Duck-typed stand-in for a WebSocket that records every non-token frame."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        """Keep the frame unless it is a streamed token (the helpers accumulate those)."""
        if frame.get("type") != "token":
            self.frames.append(frame)


@dataclass
class _Prepared:
    """Everything read from the database before the long LLM call."""

    messages: list[dict[str, Any]]
    schemas: list[dict[str, Any]]
    toolset: McpToolset
    temperature: float
    max_tokens: int
    mcp_missing: bool = field(default=False)


async def _global_settings(session: AsyncSession, user_id: int) -> Settings:
    """Return the user's global settings row, or unsaved defaults (never creates the row)."""
    result = await session.exec(
        select(Settings).where(Settings.chat_id.is_(None), Settings.user_id == user_id),
    )
    row = result.first()
    return row if row is not None else Settings()


async def _build_system_prompt(
    session: AsyncSession,
    user_id: int,
    base_prompt: str,
    has_tools: bool,
) -> str:
    """Assemble the system prompt; the tool-use rule stays last so it can be stripped later."""
    parts = [(base_prompt or DEFAULT_SYSTEM_PROMPT) + "\n\n" + HEADLESS_PREFACE]
    long_term = await memory.list_long_term_memory(session, user_id)
    if long_term:
        parts.append(
            "Long-term memory (persists across all your chats): "
            + json.dumps({row.key: row.value for row in long_term}, ensure_ascii=False),
        )
    parts.append(build_clock_line(datetime.now(timezone.utc).astimezone()))
    if has_tools:
        parts.append(MULTI_STEP_TOOL_HINT)
        parts.append(TOOL_USE_RULE)
    return "\n\n".join(parts)


async def _safe_mcp_toolset(session: AsyncSession, user_id: int) -> McpToolset:
    """Build the user's MCP toolset; an MCP problem must never fail the run."""
    try:
        return await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
    except Exception as exc:
        logger.warning(
            "headless_mcp_toolset_failed",
            user_id=user_id,
            error=type(exc).__name__,
        )
        return McpToolset.empty()


async def _prepare(
    session: AsyncSession,
    user_id: int,
    prompt: str,
) -> _Prepared:
    """Read settings, memory and MCP tools, then end the read transaction before the LLM call."""
    row = await _global_settings(session, user_id)
    temperature = row.temperature
    max_tokens = row.max_tokens
    base_prompt = row.system_prompt
    mcp_configured = any(server.enabled for server in await mcp_config.list_servers(session, user_id))
    toolset = await _safe_mcp_toolset(session, user_id)
    schemas = [
        schema
        for schema in build_tool_schemas()
        if schema["function"]["name"] in HEADLESS_TOOL_ALLOWLIST
    ] + toolset.schemas
    system_prompt = await _build_system_prompt(session, user_id, base_prompt, bool(schemas))
    await session.commit()
    return _Prepared(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        schemas=schemas,
        toolset=toolset,
        temperature=temperature,
        max_tokens=max_tokens,
        mcp_missing=mcp_configured and not toolset.schemas,
    )


def _map_llm_error(exc: BaseException) -> HeadlessRunError | None:
    """Translate a transport failure into its Russian message; None when it is not one."""
    if isinstance(exc, httpx.ConnectError):
        return HeadlessRunError("Модель недоступна: LM Studio не запущен")
    if isinstance(exc, httpx.HTTPStatusError):
        return HeadlessRunError(f"Модель недоступна: HTTP {exc.response.status_code}")
    if isinstance(exc, httpx.TimeoutException):
        return HeadlessRunError("Тайм-аут ответа модели")
    return None


def _raise_mapped(exc: Exception) -> None:
    """Raise the mapped HeadlessRunError for a known LLM failure, else the original error."""
    mapped = _map_llm_error(exc)
    if mapped is None:
        raise exc
    raise mapped from exc


async def _drive_tool_loop(
    turn: Any,
    prepared: _Prepared,
) -> tuple[str, Any | None]:
    """Run the first LLM call and any tool rounds; return the streamed text and the rounds."""
    acc = ws._ToolRoundsResult()
    try:
        text, calls = await ws._stream_follow_up_with_empty_retry(turn, prepared.schemas, acc)
        if not calls:
            kind = ws._pick_nudge(acc, text, [])
            if kind is not None:
                text, calls = await ws._stream_nudge(turn, prepared.schemas, acc, text, kind)
        if not calls:
            return acc.text, None
        rounds = await ws._run_tool_rounds(turn, calls, text)
    except httpx.HTTPError as exc:
        _raise_mapped(exc)
    if rounds.error is not None:
        _raise_mapped(rounds.error)
    return acc.text + rounds.text, rounds


def _finalize_text(
    streamed: str,
    rounds: Any | None,
    prompt: str,
    mcp_missing: bool,
) -> str:
    """Pick the final report text, falling back to a tool summary; fail on an empty answer."""
    final = streamed.strip()
    if not final and rounds is not None and rounds.results:
        final = build_tool_fallback_summary(rounds.results, prompt)
    if not final:
        suffix = " (MCP-инструменты недоступны)" if mcp_missing else ""
        raise HeadlessRunError(EMPTY_ANSWER_MESSAGE + suffix)
    if mcp_missing:
        final = final + "\n\n" + MCP_UNAVAILABLE_NOTE
    return final


async def run_headless_turn(
    session: AsyncSession,
    user_id: int,
    prompt: str,
    model: str,
) -> HeadlessResult:
    """Send the job prompt to the LLM and run its tool loop without a socket, chat or lock.

    Only the headless allowlist plus the user's MCP tools are offered, and the dispatcher
    rejects anything else. Mapped LLM failures and an empty answer raise HeadlessRunError;
    TimeoutError and cancellation propagate so the caller owns the overall deadline.
    """
    prepared = await _prepare(session, user_id, prompt)
    turn = ws._ToolTurn(
        websocket=RecordingSink(),
        session=session,
        chat=SimpleNamespace(user_id=user_id),
        chat_id=HEADLESS_CHAT_ID,
        payload=SimpleNamespace(model=model),
        llm_messages=prepared.messages,
        tool_schemas=prepared.schemas,
        toolset=prepared.toolset,
        temperature=prepared.temperature,
        max_tokens=prepared.max_tokens,
        allowed_tools=HEADLESS_TOOL_ALLOWLIST,
    )
    try:
        streamed, rounds = await _drive_tool_loop(turn, prepared)
        final = _finalize_text(streamed, rounds, prompt, prepared.mcp_missing)
    except HeadlessRunError:
        logger.info(
            "headless_turn_finished",
            user_id=user_id,
            mcp_tool_count=len(prepared.toolset.schemas),
            ok=False,
        )
        raise
    results = rounds.results if rounds is not None else []
    logger.info(
        "headless_turn_finished",
        user_id=user_id,
        rounds=rounds.rounds if rounds is not None else 0,
        mcp_tool_count=len(prepared.toolset.schemas),
        ok=True,
    )
    return HeadlessResult(
        text=final,
        results=results,
        tool_trace=serialize_tool_trace(results),
        mcp_tool_count=len(prepared.toolset.schemas),
    )
