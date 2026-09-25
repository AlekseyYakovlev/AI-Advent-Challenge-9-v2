"""Unit tests for the claimed-action heuristic and the trace-leak streaming filter."""

import pytest

from agent import context_engine
from agent import tool_guard
from agent.tool_guard import (
    TOOL_TRACE_HEADER,
    TOOL_USE_RULE,
    TraceLeakFilter,
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
