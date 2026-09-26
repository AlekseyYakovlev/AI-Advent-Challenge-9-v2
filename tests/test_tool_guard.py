"""Unit tests for the claimed-action heuristic and the trace-leak streaming filter."""

from datetime import datetime, timedelta, timezone

import pytest

from agent import context_engine
from agent import tool_guard
from agent.tool_guard import (
    ACTION_ANNOUNCE_REMINDER,
    MAX_TOOL_ROUNDS,
    MULTI_STEP_TOOL_HINT,
    TOOL_ERROR_REMINDER,
    TOOL_TRACE_HEADER,
    TOOL_USE_RULE,
    TraceLeakFilter,
    build_clock_line,
    build_tool_fallback_summary,
    looks_like_action_announcement,
    looks_like_action_claim,
    strip_tool_use_rule,
)


@pytest.mark.parametrize(
    "text",
    [
        "Файл 1.txt создан.",
        "Готово! Я создал файл 1.txt в каталоге test",
        "Текст записан в файл 1.txt",
        "Файл скопирован в 2.txt",
        "Каталог удалён.",
        "I have created the file notes.txt.",
        "File copied to 2.txt.",
    ],
)
def test_claims_are_detected(text: str) -> None:
    """Completed-action statements about file-like objects are flagged."""
    assert looks_like_action_claim(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Привет! Чем могу помочь?",
        "Python был создан Гвидо ван Россумом в 1991 году.",
        "Файл не создан: нет доступа.",
        "Файл будет создан после подтверждения.",
        "Хотите, чтобы я создал файл?",
        "Создание файла — простая операция.",
        "I can create the file for you.",
        "The file will be created when you confirm.",
    ],
)
def test_non_claims_are_ignored(text: str) -> None:
    """Plain chat, questions, negations and future phrasings are not flagged."""
    assert looks_like_action_claim(text) is False


def _run_filter(chunks: list[str]) -> str:
    """Feed chunks through a fresh filter and return everything emitted, flush included."""
    trace_filter = TraceLeakFilter()
    out = "".join(trace_filter.feed(chunk) for chunk in chunks)
    return out + trace_filter.flush()


def _chunks(text: str, size: int) -> list[str]:
    """Split text into fixed-size chunks."""
    return [text[i:i + size] for i in range(0, len(text), size)]


def _word_chunks(text: str) -> list[str]:
    """Split text into word-plus-trailing-space chunks like the SSE test helper does."""
    parts = text.split(" ")
    return [part + " " for part in parts[:-1]] + [parts[-1]]


def _all_chunkings(text: str) -> list[list[str]]:
    """Return the whole text plus several chunkings of it."""
    return [[text], _chunks(text, 1), _chunks(text, 3), _word_chunks(text)]


LEAKY = "Done.\n\n" + TOOL_TRACE_HEADER + "\n- x() -> ok: y"


@pytest.mark.parametrize(
    "text",
    [
        "Plain reply without any marker.",
        "Trailing whitespace stays.  \n\n",
        "Array [0] and [Tool use] mid sentence, then more text.",
        "Says [Tool calls are useful] and continues",
        "",
    ],
)
def test_filter_passes_plain_text_unchanged(text: str) -> None:
    """Text without the header is emitted byte-identical for any chunking."""
    for chunks in _all_chunkings(text):
        assert _run_filter(chunks) == text


def test_filter_drops_split_header_and_preceding_whitespace() -> None:
    """A header split across chunks is removed along with the whitespace before it."""
    for chunks in _all_chunkings(LEAKY):
        assert _run_filter(chunks) == "Done."


def test_filter_header_at_end_after_blank_line() -> None:
    """A header at the very end leaves only the reply text."""
    text = "Reply text\n\n" + TOOL_TRACE_HEADER
    for chunks in _all_chunkings(text):
        assert _run_filter(chunks) == "Reply text"


def test_filter_drops_everything_after_header() -> None:
    """Once dropped, later chunks and flush emit nothing."""
    trace_filter = TraceLeakFilter()
    assert trace_filter.feed("Hi " + TOOL_TRACE_HEADER + "\n- a() -> ok") == "Hi"
    assert trace_filter.feed("more text") == ""
    assert trace_filter.feed(TOOL_TRACE_HEADER) == ""
    assert trace_filter.flush() == ""


@pytest.mark.parametrize(
    "text",
    ["Answer [Tool", "Answer [Tool calls", "Answer  \n[Tool calls actually"],
)
def test_filter_incomplete_prefix_is_flushed_unchanged(text: str) -> None:
    """A never-completed header prefix is withheld by feed but returned by flush."""
    trace_filter = TraceLeakFilter()
    fed = trace_filter.feed(text)
    assert len(fed) < len(text)
    assert fed + trace_filter.flush() == text
    for chunks in _all_chunkings(text):
        assert _run_filter(chunks) == text


def test_filter_empty_chunk_is_noop() -> None:
    """Empty chunks emit nothing and do not disturb pending state."""
    trace_filter = TraceLeakFilter()
    assert trace_filter.feed("") == ""
    assert trace_filter.feed("abc [Tool") == "abc"
    assert trace_filter.feed("") == ""
    assert trace_filter.feed(" calls actually executed for this reply]") == ""
    assert trace_filter.flush() == ""


def test_filter_instances_are_independent() -> None:
    """One instance being in the dropped state does not affect another."""
    first = TraceLeakFilter()
    second = TraceLeakFilter()
    first.feed(TOOL_TRACE_HEADER)
    assert second.feed("still streaming") + second.flush() == "still streaming"


def test_trace_header_single_source_of_truth() -> None:
    """context_engine re-exports the very same header constant."""
    assert context_engine.TOOL_TRACE_HEADER is tool_guard.TOOL_TRACE_HEADER


SUFFIX = "\n\n" + TOOL_USE_RULE


def test_strip_rule_removes_suffix_and_keeps_other_keys() -> None:
    """The rule suffix is dropped from the leading system message; later messages stay."""
    tail = {"role": "user", "content": "hi" + SUFFIX}
    messages = [{"role": "system", "content": "You are helpful." + SUFFIX, "name": "sys"}, tail]

    strip_tool_use_rule(messages)

    assert messages[0] == {"role": "system", "content": "You are helpful.", "name": "sys"}
    assert messages[1] is tail


def test_strip_rule_replaces_dict_instead_of_mutating() -> None:
    """A shared reference to the original system dict keeps its content."""
    original = {"role": "system", "content": "Base" + SUFFIX}
    messages = [original]

    strip_tool_use_rule(messages)

    assert original["content"] == "Base" + SUFFIX
    assert messages[0] is not original


def test_strip_rule_without_suffix_is_noop() -> None:
    """A system message that never had the rule is left as the same object."""
    original = {"role": "system", "content": "Plain prompt"}
    messages = [original]

    strip_tool_use_rule(messages)

    assert messages[0] is original
    assert original["content"] == "Plain prompt"


def test_strip_rule_keeps_remainder_byte_identical() -> None:
    """Whitespace, newlines and unicode of the base prompt survive untouched."""
    base = "Line1\n  Правило  \n"
    messages = [{"role": "system", "content": base + SUFFIX}]

    strip_tool_use_rule(messages)

    assert messages[0]["content"] == base


def test_strip_rule_ignores_rule_not_at_the_end() -> None:
    """The rule in the middle of the content is not the appended suffix and stays."""
    original = {"role": "system", "content": "A" + SUFFIX + "\n\nextra"}
    messages = [original]

    strip_tool_use_rule(messages)

    assert messages[0] is original


def test_strip_rule_ignores_non_system_first_message() -> None:
    """Only a leading system message is touched."""
    original = {"role": "user", "content": "hi" + SUFFIX}
    messages = [original]

    strip_tool_use_rule(messages)

    assert messages[0] is original


@pytest.mark.parametrize("content", [None, [{"type": "text", "text": "x"}]])
def test_strip_rule_ignores_non_str_content(content: object) -> None:
    """Non-string system content is left alone without raising."""
    original = {"role": "system", "content": content}
    messages = [original]

    strip_tool_use_rule(messages)

    assert messages[0] is original


def test_strip_rule_handles_empty_list() -> None:
    """An empty message list is a no-op."""
    messages: list[dict[str, object]] = []

    strip_tool_use_rule(messages)

    assert messages == []


def test_strip_rule_removes_one_suffix_per_call() -> None:
    """Repeated calls remove one suffix each and never eat into the base text."""
    messages = [{"role": "system", "content": "Base" + SUFFIX + SUFFIX}]

    strip_tool_use_rule(messages)
    assert messages[0]["content"] == "Base" + SUFFIX
    strip_tool_use_rule(messages)
    assert messages[0]["content"] == "Base"
    strip_tool_use_rule(messages)
    assert messages[0]["content"] == "Base"


def test_round_cap_allows_ten_rounds() -> None:
    """Multi-step requests such as commit + push + MR need up to ten tool rounds."""
    assert MAX_TOOL_ROUNDS == 15


@pytest.mark.parametrize(
    "text",
    [
        "Сначала создам её.",
        "Сейчас создам файл 12.txt",
        "Теперь запишу дату в 13.txt",
        "Далее создам ветку Test.",
        "Let me check the branches.",
        "I'll create the file now.",
        "I will commit the changes.",
        "I'm going to open the MR.",
    ],
)
def test_announcements_are_detected(text: str) -> None:
    """A stated next step with no tool call is flagged."""
    assert looks_like_action_announcement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Привет! Чем могу помочь?",
        "Хотите, чтобы я создал файл?",
        "Сейчас 15:00.",
        "Готово.",
        "Финал.",
        "The tool failed.",
        "Task created and set to planning.",
        "Trying to finish it.",
        "Файл создан.",
        "Не вышло.",
        "Sorry, something went wrong, but here is my answer.",
        "Сначала создам её. Хотите продолжить?",
        "Let me know if you want more?",
        "Let me know if you need anything else.",
    ],
)
def test_non_announcements_are_ignored(text: str) -> None:
    """Final answers, questions and completed-action statements are not flagged."""
    assert looks_like_action_announcement(text) is False


def test_clock_line_has_date_time_and_offset() -> None:
    """The clock line carries the local date, HH:MM and the UTC offset."""
    now = datetime(2026, 9, 26, 14, 5, tzinfo=timezone(timedelta(hours=3)))

    line = build_clock_line(now)

    assert "2026-09-26" in line
    assert "14:05" in line
    assert "UTC+03:00" in line


def test_clock_line_negative_offset() -> None:
    """A negative half-hour offset is rendered with a minus sign."""
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone(-timedelta(hours=3, minutes=30)))

    assert "UTC-03:30" in build_clock_line(now)


def test_reminders_and_hint_are_nonempty() -> None:
    """The new prompt fragments exist and the hint stays short for a small local model."""
    assert ACTION_ANNOUNCE_REMINDER
    assert TOOL_ERROR_REMINDER
    assert 0 < len(MULTI_STEP_TOOL_HINT.split()) <= 130


_RESULTS = [
    {
        "name": "mcp__filesystem__write_file",
        "ok": True,
        "content": '{"ok": true}',
        "result_text": "Successfully wrote to Sandbox/12.txt",
        "mcp": {"server_name": "filesystem", "tool": "write_file"},
    },
    {"name": "save_working_memory", "ok": False, "content": '{"error": "boom"}', "mcp": None},
    {
        "name": "mcp__x__long",
        "ok": True,
        "content": "a\n" * 300,
        "mcp": {"server_name": "x", "tool": "long"},
    },
]


def test_fallback_summary_russian() -> None:
    """A Cyrillic user message gets the Russian header and one marked line per result."""
    summary = build_tool_fallback_summary(_RESULTS, "создай файл")

    lines = summary.split("\n")
    assert lines[0].startswith("Инструменты выполнены")
    body = [line for line in lines if line.startswith("- ")]
    assert len(body) == 3
    assert "filesystem/write_file: OK" in body[0]
    assert "Successfully wrote to Sandbox/12.txt" in body[0]
    assert "save_working_memory: ошибка" in body[1]
    assert all("\n" not in line for line in body)
    preview = body[2].split(" — ", 1)[1]
    assert len(preview) <= 200
    assert preview.endswith("…")


def test_fallback_summary_english() -> None:
    """A non-Cyrillic user message gets the English header and error marker."""
    summary = build_tool_fallback_summary(_RESULTS, "create a file")

    assert summary.startswith("Tools ran but the model gave no answer.")
    assert "save_working_memory: error" in summary


def test_fallback_summary_empty_results_still_has_header() -> None:
    """No results still returns a non-empty header line."""
    assert build_tool_fallback_summary([], "create a file").strip()
    assert build_tool_fallback_summary([], "создай файл").strip()
