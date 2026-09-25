"""Recovery of tool calls that a model wrote as plain text instead of structured tool_calls."""

import json
import re
import uuid
from typing import Any

from shared.logger import get_logger

logger = get_logger(__name__)

TEXT_CALL_MARKERS: tuple[str, ...] = ("<tool_call>", "<function=")

_TOOL_CALL_OPEN = "<tool_call>"
_SEPARATOR_RUN_RE = re.compile(r"[_\-.\s]+")
_FUNCTION_HEADER_RE = re.compile(r"<function=\s*([^>\s]+?)\s*>")
_PARAMETER_RE = re.compile(
    r"<parameter=\s*([^>\s]+?)\s*>(.*?)"
    r"(?=</parameter>|<parameter=|</function>|</tool_call>|\Z)",
    re.DOTALL,
)


def _normalize_name(name: str) -> str:
    """Lowercase a tool name and collapse separator runs into single underscores."""
    return _SEPARATOR_RUN_RE.sub("_", name.strip().lower()).strip("_")


def resolve_tool_name(name: str, known_names: list[str]) -> str | None:
    """Map a model-written tool name onto a known exposed name, or None when unsure."""
    if name in known_names:
        return name
    wanted = _normalize_name(name)
    if not wanted:
        return None
    normalized = {known: _normalize_name(known) for known in known_names}
    exact = [known for known, norm in normalized.items() if norm == wanted]
    if len(exact) == 1:
        return exact[0]
    suffixed = [known for known, norm in normalized.items() if norm.endswith("_" + wanted)]
    if len(suffixed) == 1:
        return suffixed[0]
    return None


def _split_blocks(raw: str) -> list[str]:
    """Split raw text into candidate call blocks, one per marker occurrence."""
    blocks: list[str] = []
    for segment in re.split(r"(?=<tool_call>)", raw):
        if segment.startswith(_TOOL_CALL_OPEN):
            blocks.append(segment)
            continue
        blocks.extend(
            piece for piece in re.split(r"(?=<function=)", segment) if piece.startswith("<function=")
        )
    return blocks


def _param_types(tool_schemas: list[dict[str, Any]], name: str) -> dict[str, list[str]]:
    """Return declared JSON-schema types per parameter of the named tool."""
    for schema in tool_schemas:
        function = schema.get("function", {})
        if function.get("name") != name:
            continue
        properties = function.get("parameters", {}).get("properties", {}) or {}
        types: dict[str, list[str]] = {}
        for key, spec in properties.items():
            declared = spec.get("type") if isinstance(spec, dict) else None
            if isinstance(declared, list):
                types[key] = [str(item) for item in declared]
            elif isinstance(declared, str):
                types[key] = [declared]
        return types
    return {}


def _coerce_value(raw_value: str, declared: list[str] | None) -> Any:
    """Convert a raw XML parameter value into the type the tool schema declares."""
    if not declared or "string" in declared:
        value = raw_value
        if value.startswith("\r\n"):
            value = value[2:]
        elif value.startswith("\n"):
            value = value[1:]
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        return value
    stripped = raw_value.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        return stripped


def _parse_qwen_block(block: str, tool_schemas: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Parse one `<function=NAME><parameter=K>V</parameter></function>` block."""
    header = _FUNCTION_HEADER_RE.search(block)
    if header is None:
        return None
    raw_name = header.group(1)
    known = [s["function"]["name"] for s in tool_schemas]
    resolved = resolve_tool_name(raw_name, known)
    if resolved is None:
        logger.warning("text_tool_call_unknown", name=raw_name)
        return None
    types = _param_types(tool_schemas, resolved)
    arguments: dict[str, Any] = {}
    for match in _PARAMETER_RE.finditer(block, header.end()):
        arguments[match.group(1)] = _coerce_value(match.group(2), types.get(match.group(1)))
    return resolved, arguments


def _parse_hermes_block(block: str, tool_schemas: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Parse one `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` block."""
    start = block.find("{")
    if start == -1:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(block, start)
    except ValueError:
        logger.warning("text_tool_call_unparsable")
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return None
    raw_name: str = payload["name"]
    known = [s["function"]["name"] for s in tool_schemas]
    resolved = resolve_tool_name(raw_name, known)
    if resolved is None:
        logger.warning("text_tool_call_unknown", name=raw_name)
        return None
    arguments: Any = payload.get("arguments", payload.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return resolved, arguments


def parse_text_tool_calls(raw: str, tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse hermes-JSON and qwen-XML tool calls from text into structured tool_calls."""
    calls: list[dict[str, Any]] = []
    for block in _split_blocks(raw):
        body = block[len(_TOOL_CALL_OPEN):] if block.startswith(_TOOL_CALL_OPEN) else block
        if body.lstrip().startswith("<function="):
            parsed = _parse_qwen_block(body, tool_schemas)
        else:
            parsed = _parse_hermes_block(body, tool_schemas)
        if parsed is None:
            continue
        name, arguments = parsed
        calls.append(
            {
                "id": f"call_text_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            },
        )
    logger.info("text_tool_calls_parsed", count=len(calls))
    return calls


def _hold_start(buf: str) -> int:
    """Return the index from which buf may still turn into a marker and must be withheld."""
    start = len(buf)
    longest = max(len(marker) for marker in TEXT_CALL_MARKERS) - 1
    for length in range(min(longest, len(buf)), 0, -1):
        tail = buf[-length:]
        if any(marker.startswith(tail) and len(marker) > length for marker in TEXT_CALL_MARKERS):
            start = len(buf) - length
            break
    while start > 0 and buf[start - 1].isspace():
        start -= 1
    return start


class TextToolCallFilter:
    """Streaming filter that captures a text-written tool call and everything after it."""

    def __init__(self) -> None:
        self._pending: str = ""
        self._captured: str = ""
        self._capturing: bool = False

    def feed(self, chunk: str) -> str:
        """Return the part of the chunk that is safe to emit now."""
        if self._capturing:
            self._captured += chunk
            return ""
        buf: str = self._pending + chunk
        indexes = [i for i in (buf.find(m) for m in TEXT_CALL_MARKERS) if i != -1]
        if indexes:
            idx = min(indexes)
            self._capturing = True
            self._pending = ""
            self._captured = buf[idx:]
            return buf[:idx].rstrip()
        start: int = _hold_start(buf)
        self._pending = buf[start:]
        return buf[:start]

    def flush(self) -> str:
        """Return any withheld text once the stream ends."""
        if self._capturing:
            return ""
        tail: str = self._pending
        self._pending = ""
        return tail

    def captured_calls(self, tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parse the captured text into structured tool_calls; empty when nothing was captured."""
        if not self._captured:
            return []
        return parse_text_tool_calls(self._captured, tool_schemas)
