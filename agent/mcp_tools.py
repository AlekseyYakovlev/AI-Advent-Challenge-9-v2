"""MCP tools exposed to the chat LLM: naming, schema building and dispatch."""

import asyncio
import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from mcp.shared.exceptions import McpError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config
from agent.mcp_client import (
    ensure_connected,
    get_live_session,
    get_live_tools,
    has_recorded_failure,
)
from agent.schemas import McpToolInfo
from shared.config import settings
from shared.logger import get_logger
from shared.models import McpServerConfig

logger = get_logger(__name__)

MCP_TOOL_PREFIX = "mcp__"
MAX_TOOL_NAME_LENGTH = 64
SERVER_SLUG_MAX_LENGTH = 20
MAX_MCP_TOOLS = 128
DESCRIPTION_MAX_LENGTH = 1024

_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class McpToolBinding:
    """Maps an exposed tool name back to exactly one (user, server, tool)."""

    exposed_name: str
    user_id: int
    server_id: int
    server_name: str
    tool_name: str


@dataclass
class McpToolset:
    """OpenAI tool schemas plus the bindings needed to dispatch them."""

    schemas: list[dict[str, Any]] = field(default_factory=list)
    bindings: dict[str, McpToolBinding] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "McpToolset":
        """Return a toolset with no tools."""
        return cls()


def _sanitize(text: str) -> str:
    """Replace characters outside [A-Za-z0-9_-] and trim separators from both ends."""
    return _INVALID_NAME_CHARS.sub("_", text).strip("_-")


def server_slug(name: str, server_id: int) -> str:
    """Return a lowercase, name-safe slug for a server, falling back to server<id>."""
    slug = _sanitize(name).lower()[:SERVER_SLUG_MAX_LENGTH].strip("_-")
    return slug or f"server{server_id}"


def _exposed_name(slug: str, server_id: int, tool_name: str, taken: set[str]) -> str | None:
    """Build a unique exposed name for a tool, or None if even the hashed form collides."""
    tool_part = _sanitize(tool_name)
    candidate = f"{MCP_TOOL_PREFIX}{slug}__{tool_part}"
    if tool_part and len(candidate) <= MAX_TOOL_NAME_LENGTH and candidate not in taken:
        return candidate

    digest = hashlib.sha1(f"{server_id}:{tool_name}".encode("utf-8")).hexdigest()[:8]
    head = f"{MCP_TOOL_PREFIX}{slug}__"
    room = MAX_TOOL_NAME_LENGTH - len(head) - len("-") - len(digest)
    hashed = f"{head}{tool_part[: max(room, 0)]}-{digest}"
    if len(hashed) > MAX_TOOL_NAME_LENGTH or hashed in taken:
        return None
    return hashed


def _mcp_parameters(input_schema: Any) -> dict[str, Any]:
    """Copy a tool's JSON Schema into an OpenAI-safe object schema without title keys."""
    if not isinstance(input_schema, dict):
        return {"type": "object", "properties": {}}
    schema = copy.deepcopy(input_schema)
    schema["type"] = "object"
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    schema.pop("$schema", None)
    schema.pop("title", None)
    for prop in schema["properties"].values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return schema


def _describe(server_name: str, tool: McpToolInfo) -> str:
    """Prefix the tool description with its server so the model can tell servers apart."""
    text = f"[MCP server: {server_name}] {tool.description or ''}".strip()
    return text[:DESCRIPTION_MAX_LENGTH]


def build_toolset_from_servers(
    user_id: int,
    servers: list[tuple[int, str, list[McpToolInfo]]],
    reserved: set[str],
) -> McpToolset:
    """Build schemas and bindings from (server_id, server_name, tools) tuples without I/O."""
    toolset = McpToolset()
    taken: set[str] = set(reserved)
    used_slugs: set[str] = set()

    for server_id, server_name, tools in servers:
        slug = server_slug(server_name, server_id)
        if slug in used_slugs:
            slug = f"{slug}-{server_id}"
        used_slugs.add(slug)

        for tool in tools:
            if len(toolset.schemas) >= MAX_MCP_TOOLS:
                logger.warning("mcp_tools_capped", user_id=user_id, cap=MAX_MCP_TOOLS)
                return toolset
            exposed = _exposed_name(slug, server_id, tool.name, taken)
            if exposed is None:
                logger.warning(
                    "mcp_tool_name_collision",
                    user_id=user_id,
                    server_id=server_id,
                    tool=tool.name,
                )
                continue
            taken.add(exposed)
            toolset.bindings[exposed] = McpToolBinding(
                exposed_name=exposed,
                user_id=user_id,
                server_id=server_id,
                server_name=server_name,
                tool_name=tool.name,
            )
            toolset.schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": exposed,
                        "description": _describe(server_name, tool),
                        "parameters": _mcp_parameters(tool.input_schema),
                    },
                },
            )
    return toolset


async def _auto_connect_one(user_id: int, row: McpServerConfig) -> None:
    """Connect one server on demand and log the outcome; exceptions surface to the caller."""
    started = time.monotonic()
    result = await ensure_connected(
        user_id,
        row.id,
        row.command,
        mcp_config.load_args(row),
        mcp_config.load_env(row) or None,
        row.cwd,
    )
    outcome = result.error_code.value if result.error_code else result.status.value
    logger.info(
        "mcp_auto_connect",
        user_id=user_id,
        server_id=row.id,
        outcome=outcome,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


async def _auto_connect_missing(user_id: int, rows: list[McpServerConfig]) -> None:
    """Concurrently connect enabled servers that have no live session and no recorded failure."""
    candidates = [
        row
        for row in rows
        if row.enabled
        and row.id is not None
        and get_live_tools(user_id, row.id) is None
        and not has_recorded_failure(user_id, row.id)
    ]
    if not candidates:
        return

    outcomes = await asyncio.gather(
        *(_auto_connect_one(user_id, row) for row in candidates),
        return_exceptions=True,
    )
    cancelled: BaseException | None = None
    for row, outcome in zip(candidates, outcomes):
        if not isinstance(outcome, BaseException):
            continue
        # Log the exception type only: messages can echo args or environment values.
        logger.warning(
            "mcp_auto_connect_failed",
            user_id=user_id,
            server_id=row.id,
            error_type=type(outcome).__name__,
        )
        if isinstance(outcome, asyncio.CancelledError):
            cancelled = outcome
    if cancelled is not None:
        raise cancelled


async def build_mcp_toolset(
    session: AsyncSession,
    user_id: int,
    reserved: set[str],
) -> McpToolset:
    """Build the toolset from the user's enabled servers, connecting idle ones first.

    With MCP_AUTO_CONNECT on, enabled servers without a live session or a recorded failure
    are connected concurrently before their tools are read; failures add no tools.
    """
    rows = await mcp_config.list_servers(session, user_id)
    if settings.MCP_AUTO_CONNECT:
        await _auto_connect_missing(user_id, rows)
    live: list[tuple[int, str, list[McpToolInfo]]] = []
    for row in rows:
        if not row.enabled or row.id is None:
            continue
        tools = get_live_tools(user_id, row.id)
        if tools is None:
            continue
        live.append((row.id, row.name, tools))

    toolset = build_toolset_from_servers(user_id, live, reserved)
    logger.debug(
        "mcp_toolset_built",
        user_id=user_id,
        server_count=len(live),
        tool_count=len(toolset.schemas),
    )
    return toolset


def _error(text: str) -> dict[str, Any]:
    """Build a failed call outcome."""
    return {"ok": False, "is_error": True, "text": text, "truncated": False}


def _flatten_content(result: Any) -> str:
    """Join text items of a CallToolResult; note omitted non-text items."""
    parts: list[str] = []
    has_text = False
    for item in result.content or []:
        item_type = getattr(item, "type", "unknown")
        if item_type == "text":
            has_text = True
            parts.append(getattr(item, "text", ""))
        else:
            parts.append(f"[{item_type} content omitted]")
    if not has_text and getattr(result, "structuredContent", None):
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return "\n".join(parts)


async def call_mcp_tool(binding: McpToolBinding, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one MCP tool on the user's live session; every failure becomes an error outcome."""
    session = get_live_session(binding.user_id, binding.server_id)
    if session is None:
        return _error(f"MCP server '{binding.server_name}' is not connected")

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            session.call_tool(binding.tool_name, arguments=arguments),
            settings.MCP_TOOL_CALL_TIMEOUT,
        )
    except TimeoutError:
        logger.warning(
            "mcp_tool_call_failed",
            user_id=binding.user_id,
            server_id=binding.server_id,
            tool=binding.tool_name,
            error_type="TimeoutError",
        )
        return _error(f"MCP tool call timed out after {settings.MCP_TOOL_CALL_TIMEOUT:g}s")
    except McpError as exc:
        logger.warning(
            "mcp_tool_call_failed",
            user_id=binding.user_id,
            server_id=binding.server_id,
            tool=binding.tool_name,
            error_type="McpError",
        )
        return _error(f"MCP error: {exc}")
    except Exception as exc:
        logger.warning(
            "mcp_tool_call_failed",
            user_id=binding.user_id,
            server_id=binding.server_id,
            tool=binding.tool_name,
            error_type=type(exc).__name__,
        )
        return _error(f"MCP server failed or disconnected: {type(exc).__name__}")

    text = _flatten_content(result)
    truncated = False
    limit = settings.MCP_TOOL_RESULT_MAX_CHARS
    if len(text) > limit:
        cut = len(text) - limit
        text = f"{text[:limit]}\n…[truncated {cut} chars]"
        truncated = True

    ok = not result.isError
    logger.info(
        "mcp_tool_call_dispatched",
        user_id=binding.user_id,
        server_id=binding.server_id,
        tool=binding.tool_name,
        ok=ok,
        duration_ms=int((time.monotonic() - started) * 1000),
        result_chars=len(text),
    )
    return {"ok": ok, "is_error": not ok, "text": text, "truncated": truncated}
