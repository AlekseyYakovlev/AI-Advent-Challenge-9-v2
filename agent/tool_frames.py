"""Shared builder of tool_call WebSocket frames."""

from typing import Any

from agent.schemas import (
    TOOL_EVENT_ARGS_PREVIEW_CHARS,
    TOOL_EVENT_PREVIEW_CHARS,
    ToolCallEvent,
)


def preview(text: str, limit: int) -> tuple[str, bool]:
    """Cut text to the limit and report whether it was cut."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def tool_call_frame(result: dict[str, Any]) -> dict[str, Any]:
    """Build the tool_call WebSocket frame for one dispatch result."""
    mcp_info = result.get("mcp")
    arguments, _ = preview(result.get("arguments", ""), TOOL_EVENT_ARGS_PREVIEW_CHARS)
    shown, cut = preview(
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
        result=shown,
        truncated=bool(result.get("truncated")) or cut,
    ).model_dump()
