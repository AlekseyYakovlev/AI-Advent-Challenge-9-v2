"""Code-initiated MCP calls of the release pipeline, each rendered as a tool card."""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from mcp.shared.exceptions import McpError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config
from agent.mcp_client import ensure_connected, get_live_session
from agent.mcp_tools import server_slug
from agent.release_command import (
    GITHUB_SERVER_NAME,
    MSG_SERVER_NOT_CONFIGURED,
    RELEASE_SERVER_NAME,
    FrameSender,
    ToolOutcome,
)
from agent.schemas import McpConnectionStatus
from agent.tool_frames import tool_call_frame
from shared.config import settings
from shared.logger import get_logger
from shared.models import McpServerConfig


logger = get_logger(__name__)

RELEASE_LONG_TOOLS: frozenset[str] = frozenset(
    {"release_context", "release_push", "github_create_draft_release"}
)
RELEASE_LONG_TIMEOUT: float = 150.0


class ReleaseMcpError(Exception):
    """The release MCP server cannot be used."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail: str = detail


@dataclass(frozen=True)
class ReleaseRows:
    """The user's server rows used by the release pipeline."""

    release: McpServerConfig
    github: McpServerConfig | None


async def resolve_release_rows(session: AsyncSession, user_id: int) -> ReleaseRows:
    """Find the enabled release-git row (required) and the github row (optional)."""
    release: McpServerConfig | None = None
    github: McpServerConfig | None = None
    for row in await mcp_config.list_servers(session, user_id):
        if not row.enabled or row.id is None:
            continue
        if release is None and row.name == RELEASE_SERVER_NAME:
            release = row
        elif github is None and row.name == GITHUB_SERVER_NAME:
            github = row
    if release is None:
        raise ReleaseMcpError(MSG_SERVER_NOT_CONFIGURED)
    return ReleaseRows(release=release, github=github)


def _flatten(result: Any) -> str:
    """Join the text items of a tool result; note omitted non-text items."""
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


class ReleaseMcp:
    """Callable that runs one tool on the release or github server and emits a tool card."""

    def __init__(self, user_id: int, rows: ReleaseRows, send: FrameSender) -> None:
        self.user_id: int = user_id
        self.rows: ReleaseRows = rows
        self.send: FrameSender = send
        self._connected: set[int] = set()

    @property
    def has_github(self) -> bool:
        """True when a github server row is configured."""
        return self.rows.github is not None

    async def __call__(self, server_key: str, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
        is_release = server_key in ("release", RELEASE_SERVER_NAME)
        row = self.rows.release if is_release else self.rows.github
        if row is None:
            name = RELEASE_SERVER_NAME if is_release else GITHUB_SERVER_NAME
            outcome = ToolOutcome(False, f"MCP server '{name}' is not configured")
            await self._emit(name, 0, tool, arguments, outcome)
            return outcome
        outcome = await self._run(row, tool, arguments)
        await self._emit(row.name, row.id or 0, tool, arguments, outcome)
        return outcome

    async def _session(self, row: McpServerConfig) -> Any:
        """Return the live session of a row, connecting it on first use."""
        assert row.id is not None
        if row.id not in self._connected:
            result = await ensure_connected(
                self.user_id,
                row.id,
                row.command,
                mcp_config.load_args(row),
                mcp_config.load_env(row) or None,
                row.cwd,
            )
            if result.status != McpConnectionStatus.CONNECTED:
                return None
            self._connected.add(row.id)
        return get_live_session(self.user_id, row.id)

    async def _run(self, row: McpServerConfig, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
        session = await self._session(row)
        if session is None:
            return ToolOutcome(False, f"MCP server '{row.name}' is not connected")
        timeout = RELEASE_LONG_TIMEOUT if tool in RELEASE_LONG_TOOLS else settings.MCP_TOOL_CALL_TIMEOUT
        try:
            result = await asyncio.wait_for(session.call_tool(tool, arguments=arguments), timeout)
        except TimeoutError:
            return self._failure(row, tool, "TimeoutError", f"MCP tool call timed out after {timeout:g}s")
        except McpError as exc:
            return self._failure(row, tool, "McpError", f"MCP error: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            return self._failure(row, tool, name, f"MCP server failed or disconnected: {name}")
        text = _flatten(result)
        truncated = False
        limit = settings.MCP_TOOL_RESULT_MAX_CHARS
        if len(text) > limit:
            text = f"{text[:limit]}\n…[truncated {len(text) - limit} chars]"
            truncated = True
        ok = not result.isError
        logger.info(
            "release_mcp_call",
            user_id=self.user_id,
            server_id=row.id,
            tool=tool,
            ok=ok,
            chars=len(text),
        )
        return ToolOutcome(ok, text, truncated)

    def _failure(self, row: McpServerConfig, tool: str, error_type: str, text: str) -> ToolOutcome:
        logger.warning(
            "release_mcp_call_failed",
            user_id=self.user_id,
            server_id=row.id,
            tool=tool,
            error_type=error_type,
        )
        return ToolOutcome(False, text)

    async def _emit(
        self, server_name: str, server_id: int, tool: str, arguments: dict[str, Any], outcome: ToolOutcome
    ) -> None:
        """Send the standard tool card for one call."""
        await self.send(
            tool_call_frame(
                {
                    "tool_call_id": "release-" + uuid.uuid4().hex[:12],
                    "name": f"mcp__{server_slug(server_name, server_id)}__{tool}",
                    "mcp": {"server_name": server_name, "tool": tool},
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                    "ok": outcome.ok,
                    "result_text": outcome.text,
                    "content": outcome.text,
                    "truncated": outcome.truncated,
                }
            )
        )
