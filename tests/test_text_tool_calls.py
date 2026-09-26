"""Unit tests for recovery of tool calls that a model wrote as plain text."""

import json
import re
from typing import Any

import pytest

from agent.text_tool_calls import (
    TextToolCallFilter,
    parse_text_tool_calls,
    resolve_tool_name,
)


def _schema(name: str, props: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a minimal OpenAI tool schema with typed properties."""
    properties = {key: {"type": kind} for key, kind in (props or {}).items()}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "d",
            "parameters": {"type": "object", "properties": properties},
        },
    }


SCHEMAS = [
    _schema("mcp__filesystem__write_file", {"path": "string", "content": "string"}),
    _schema("mcp__filesystem__list_directory", {"path": "string"}),
    _schema("save_working_memory", {"key": "string", "content": "string"}),
    _schema(
        "typed_tool",
        {"n": "integer", "flag": "boolean", "items": "array", "obj": "object", "s": "string"},
    ),
    _schema("mcp__a__read_file", {"path": "string"}),
    _schema("mcp__b__read_file", {"path": "string"}),
]

QWEN_BLOCK = (
    "<tool_call><function=mcp_filesystem_write_file>"
    "<parameter=path>Sandbox/11.txt</parameter>"
    "<parameter=content>hi</parameter></function></tool_call>"
)


def _args(call: dict[str, Any]) -> dict[str, Any]:
    """Decode a recovered call's arguments JSON."""
    return json.loads(call["function"]["arguments"])


def test_qwen_xml_with_alias_name_resolves() -> None:
    """A qwen-XML call with an alias name maps to the exposed tool name."""
    calls = parse_text_tool_calls(QWEN_BLOCK, SCHEMAS)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "mcp__filesystem__write_file"
    assert _args(calls[0]) == {"path": "Sandbox/11.txt", "content": "hi"}


def test_recovered_call_shape() -> None:
    """Recovered calls carry a call_text_ id, function type and JSON-string arguments."""
    call = parse_text_tool_calls(QWEN_BLOCK, SCHEMAS)[0]
    assert re.fullmatch(r"call_text_[0-9a-f]{12}", call["id"])
    assert call["type"] == "function"
    assert isinstance(call["function"]["arguments"], str)


def test_hermes_json_parses() -> None:
    """Hermes JSON tool_call blocks parse into one call."""
    raw = (
        '<tool_call>{"name": "save_working_memory", '
        '"arguments": {"key": "k", "content": "v"}}</tool_call>'
    )
    calls = parse_text_tool_calls(raw, SCHEMAS)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "save_working_memory"
    assert _args(calls[0]) == {"key": "k", "content": "v"}


def test_hermes_json_missing_closing_tag_and_string_arguments() -> None:
    """A missing closing tag and arguments given as a JSON string still parse."""
    raw = '<tool_call>{"name": "save_working_memory", "arguments": "{\\"key\\": \\"k\\"}"}'
    calls = parse_text_tool_calls(raw, SCHEMAS)
    assert len(calls) == 1
    assert _args(calls[0]) == {"key": "k"}


def test_hermes_json_parameters_key_and_bad_arguments() -> None:
    """`parameters` is accepted as the arguments key; undecodable arguments become {}."""
    raw = '<tool_call>{"name": "save_working_memory", "parameters": {"key": "k"}}</tool_call>'
    assert _args(parse_text_tool_calls(raw, SCHEMAS)[0]) == {"key": "k"}
    bad = '<tool_call>{"name": "save_working_memory", "arguments": "not json"}</tool_call>'
    assert _args(parse_text_tool_calls(bad, SCHEMAS)[0]) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "<tool_call><function=save_working_memory><parameter=key>k</parameter>"
        "<parameter=content>v",
        "<tool_call><function=save_working_memory><parameter=key>k</parameter>"
        "<parameter=content>v</parameter>",
        "<function=save_working_memory><parameter=key>k</parameter>"
        "<parameter=content>v</function>",
        "<function=save_working_memory><parameter=key>k<parameter=content>v",
    ],
)
def test_qwen_xml_with_missing_closers_parses(raw: str) -> None:
    """Missing </parameter>, </function> or </tool_call> do not stop parsing."""
    calls = parse_text_tool_calls(raw, SCHEMAS)
    assert len(calls) == 1
    assert _args(calls[0]) == {"key": "k", "content": "v"}


def test_two_blocks_yield_two_calls_in_order() -> None:
    """Consecutive blocks give consecutive calls."""
    raw = (
        QWEN_BLOCK
        + "\n"
        + "<tool_call><function=mcp_filesystem_list_directory>"
        "<parameter=path>Sandbox</parameter></function></tool_call>"
    )
    calls = parse_text_tool_calls(raw, SCHEMAS)
    assert [c["function"]["name"] for c in calls] == [
        "mcp__filesystem__write_file",
        "mcp__filesystem__list_directory",
    ]


def test_bare_function_blocks_without_tool_call_wrapper() -> None:
    """`<function=...>` outside a tool_call wrapper is its own block."""
    raw = (
        "<function=save_working_memory><parameter=key>a</parameter></function>"
        "<function=save_working_memory><parameter=key>b</parameter></function>"
    )
    calls = parse_text_tool_calls(raw, SCHEMAS)
    assert [_args(c)["key"] for c in calls] == ["a", "b"]


def test_typed_parameters_are_coerced() -> None:
    """Integer, boolean, array and object parameters are json-decoded; strings are kept raw."""
    raw = (
        "<tool_call><function=typed_tool>"
        "<parameter=n>7</parameter>"
        "<parameter=flag>true</parameter>"
        "<parameter=items>[1, 2]</parameter>"
        '<parameter=obj>{"a": 1}</parameter>'
        "<parameter=s>\n 42 \n</parameter>"
        "</function></tool_call>"
    )
    args = _args(parse_text_tool_calls(raw, SCHEMAS)[0])
    assert args == {"n": 7, "flag": True, "items": [1, 2], "obj": {"a": 1}, "s": " 42 "}


def test_string_parameter_strips_only_one_newline_each_side() -> None:
    """Exactly one leading and one trailing newline are removed from string values."""
    raw = (
        "<tool_call><function=save_working_memory>"
        "<parameter=key>\n\nk\n\n</parameter></function></tool_call>"
    )
    assert _args(parse_text_tool_calls(raw, SCHEMAS)[0])["key"] == "\nk\n"


def test_undecodable_typed_parameter_stays_a_string() -> None:
    """A typed value that is not valid JSON is kept as the raw string."""
    raw = "<tool_call><function=typed_tool><parameter=n>seven</parameter></function></tool_call>"
    assert _args(parse_text_tool_calls(raw, SCHEMAS)[0])["n"] == "seven"


def test_unknown_and_ambiguous_names_are_dropped() -> None:
    """Names without an exact, normalized or unique-suffix match yield no call."""
    unknown = "<tool_call><function=rm_rf><parameter=path>/</parameter></function></tool_call>"
    assert parse_text_tool_calls(unknown, SCHEMAS) == []
    ambiguous = "<tool_call><function=read_file><parameter=path>x</parameter></function></tool_call>"
    assert parse_text_tool_calls(ambiguous, SCHEMAS) == []


def test_no_marker_yields_no_calls() -> None:
    """Text without markers parses to nothing."""
    assert parse_text_tool_calls("just text", SCHEMAS) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mcp__filesystem__write_file", "mcp__filesystem__write_file"),
        ("mcp_filesystem_write_file", "mcp__filesystem__write_file"),
        ("MCP-Filesystem-Write_File", "mcp__filesystem__write_file"),
        ("write_file", "mcp__filesystem__write_file"),
        ("read_file", None),
        ("nope", None),
        ("", None),
    ],
)
def test_resolve_tool_name(name: str, expected: str | None) -> None:
    """Exact, normalized and unique-suffix names resolve; others do not."""
    known = [s["function"]["name"] for s in SCHEMAS]
    assert resolve_tool_name(name, known) == expected


def _run_filter(chunks: list[str]) -> tuple[str, TextToolCallFilter]:
    """Feed chunks through a fresh filter and return the emitted text and the filter."""
    text_filter = TextToolCallFilter()
    out = "".join(text_filter.feed(chunk) for chunk in chunks)
    return out + text_filter.flush(), text_filter


def _chunks(text: str, size: int) -> list[str]:
    """Split text into fixed-size chunks."""
    return [text[i:i + size] for i in range(0, len(text), size)]


def _word_chunks(text: str) -> list[str]:
    """Split text into word-plus-trailing-space chunks."""
    parts = text.split(" ")
    return [part + " " for part in parts[:-1]] + [parts[-1]]


def _all_chunkings(text: str) -> list[list[str]]:
    """Return the whole text plus several chunkings of it."""
    return [[text], _chunks(text, 1), _chunks(text, 3), _word_chunks(text)]


@pytest.mark.parametrize("marker_block", [QWEN_BLOCK, QWEN_BLOCK.replace("<tool_call>", "")])
def test_filter_captures_call_and_emits_only_preceding_text(marker_block: str) -> None:
    """Text before the marker passes; the marker and everything after is captured."""
    text = "Сейчас создам.\n" + marker_block
    for chunks in _all_chunkings(text):
        emitted, text_filter = _run_filter(chunks)
        assert emitted == "Сейчас создам."
        calls = text_filter.captured_calls(SCHEMAS)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "mcp__filesystem__write_file"


def test_filter_withholds_trailing_whitespace_before_marker() -> None:
    """Whitespace before a marker is not emitted."""
    text = "Ok  \n\n" + QWEN_BLOCK
    for chunks in _all_chunkings(text):
        emitted, _ = _run_filter(chunks)
        assert emitted == "Ok"


def test_filter_drops_text_after_captured_call() -> None:
    """After a marker, later chunks are captured and never emitted."""
    text_filter = TextToolCallFilter()
    assert text_filter.feed("Hi <tool_call>{}") == "Hi"
    assert text_filter.feed("hallucinated result") == ""
    assert text_filter.flush() == ""


@pytest.mark.parametrize(
    "text",
    [
        "Plain reply without any marker.",
        "Trailing whitespace stays.  \n\n",
        "A tag <b>bold</b> and x < y and <tool",
        "Ends with a fragment <func",
        "Ends with <function",
        "",
    ],
)
def test_filter_passes_plain_text_unchanged(text: str) -> None:
    """Text that never becomes a marker is byte-identical after feed and flush."""
    for chunks in _all_chunkings(text):
        emitted, text_filter = _run_filter(chunks)
        assert emitted == text
        assert text_filter.captured_calls(SCHEMAS) == []


def test_filter_captured_calls_empty_when_nothing_captured() -> None:
    """No capture means no recovered calls."""
    text_filter = TextToolCallFilter()
    text_filter.feed("hello")
    text_filter.flush()
    assert text_filter.captured_calls(SCHEMAS) == []
