"""Standalone CLI that connects to an MCP stdio server and lists its tools.

Usage: python scripts/mcp_list_tools.py <command> [args...]
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# The script directory is sys.path[0] when run directly, so add the repo root
# to make the `agent` and `shared` packages importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mcp_client import connect_once_and_list  # noqa: E402
from agent.schemas import McpConnectionStatus, McpConnectResult  # noqa: E402
from shared.logger import setup_logging  # noqa: E402

USAGE: str = "Usage: python scripts/mcp_list_tools.py <command> [args...]"


def describe_params(input_schema: dict[str, Any]) -> list[tuple[str, str, bool, str]]:
    """Return (name, type, required, description) for each schema property."""
    required: set[str] = set(input_schema.get("required", []))
    params: list[tuple[str, str, bool, str]] = []
    for name, prop in input_schema.get("properties", {}).items():
        raw_type = prop.get("type")
        if isinstance(raw_type, list):
            type_name = "|".join(str(t) for t in raw_type)
        else:
            type_name = str(raw_type) if raw_type else "any"
        params.append((name, type_name, name in required, prop.get("description", "")))
    return params


def format_result(result: McpConnectResult) -> str:
    """Render a successful connect result as a human-readable listing."""
    info = result.server_info
    lines: list[str] = [
        f"Server: {info.name if info else ''}",
        f"Version: {info.version if info else ''}",
        f"Protocol: {info.protocol_version if info else ''}",
        "",
        f"Tools ({len(result.tools)}):",
    ]
    for tool in result.tools:
        lines.append("")
        lines.append(f"- {tool.name}: {tool.description or ''}")
        params = describe_params(tool.input_schema)
        if not params:
            lines.append("    (no parameters)")
        for name, type_name, required, description in params:
            marker = " *" if required else ""
            lines.append(f"    {name} ({type_name}){marker}: {description}")
    return "\n".join(lines)


async def run(argv: list[str]) -> int:
    """Connect, list tools and print the outcome; return the process exit code."""
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2

    result = await connect_once_and_list(argv[0], argv[1:])
    if result.status == McpConnectionStatus.CONNECTED:
        print(format_result(result))
        return 0

    code = result.error_code.value if result.error_code else "UNKNOWN"
    print(f"ERROR {code}: {result.error_message}", file=sys.stderr)
    print(f"Detail: {result.detail}", file=sys.stderr)
    if result.stderr_tail:
        print("--- stderr (last 20 lines) ---", file=sys.stderr)
        print(result.stderr_tail, file=sys.stderr)
    return 1


def main() -> None:
    """Configure console output and run the CLI."""
    # Russian error messages must survive the Windows console code page.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    # Keep structlog JSON and the mcp SDK's stdlib logging out of CLI output.
    setup_logging(logging.CRITICAL)
    logging.getLogger().setLevel(logging.CRITICAL)
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
