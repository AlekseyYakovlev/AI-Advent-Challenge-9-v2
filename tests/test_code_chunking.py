"""Code chunker tests: structure, size cap, Cyrillic, fallbacks, line windows."""

from pathlib import Path

import pytest

from agent.code_chunking import (
    MAX_CHUNK_CHARS,
    chunk_code,
    chunk_lines,
    chunk_python,
    parse_line_range,
)

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = '''import os

X = 1


@decorator
def f(a):
    return a


class C:
    """Doc."""

    attr = 1

    def m1(self):
        return 1

    def m2(self):
        return 2
'''


def _names(drafts: list) -> list[str]:
    return [d.section.rsplit(" (", 1)[0] for d in drafts]


def test_python_structure_and_sections() -> None:
    """Module code, a decorated function, a class header and each method are separate."""
    drafts = chunk_code("a/b.py", SAMPLE)
    assert _names(drafts) == ["<module>", "f", "C", "C.m1", "C.m2"]
    sections = {d.section.rsplit(" (", 1)[0]: d for d in drafts}
    assert sections["<module>"].section == "<module> (L1-5)"
    assert sections["f"].section == "f (L6-8)"
    assert sections["C"].section == "C (L11-15)"
    assert sections["C.m1"].section == "C.m1 (L16-18)"
    assert sections["C.m2"].section == "C.m2 (L19-20)"
    for draft in drafts:
        assert draft.text.startswith(f"# a/b.py :: {_names([draft])[0]}\n")
        assert len(draft.text) <= MAX_CHUNK_CHARS
        assert draft.char_start < draft.char_end
    assert "@decorator" in sections["f"].text


def test_async_function_and_nested_function() -> None:
    """Async definitions are fragments; a nested function stays in its parent."""
    src = "async def outer():\n    def inner():\n        return 1\n    return inner\n"
    drafts = chunk_python("x.py", src)
    assert len(drafts) == 1
    assert drafts[0].section == "outer (L1-4)"
    assert "def inner" in drafts[0].text


def test_long_function_is_split_and_covers_all_lines() -> None:
    """A 5000-line function becomes several capped fragments covering every line."""
    body = "\n".join(f"    value_{i} = {i}" for i in range(4999))
    src = f"def big():\n{body}\n"
    drafts = chunk_python("big.py", src)
    assert len(drafts) > 1
    covered: set[int] = set()
    for draft in drafts:
        assert draft.section.startswith("big (L")
        assert len(draft.text) <= MAX_CHUNK_CHARS
        rng = parse_line_range(draft.section)
        assert rng is not None
        covered.update(range(rng[0], rng[1] + 1))
    assert covered == set(range(1, 5001))


def test_single_huge_line_is_hard_cut() -> None:
    """One 10 000-character line is cut into capped pieces with the same line number."""
    src = "X = '" + "a" * 10_000 + "'\n"
    drafts = chunk_python("w.py", src)
    assert len(drafts) > 1
    for draft in drafts:
        assert len(draft.text) <= MAX_CHUNK_CHARS
        assert parse_line_range(draft.section) == (1, 1)


def test_cyrillic_text_is_sliced_by_lines() -> None:
    """Fragment text equals the same lines of a source with Cyrillic literals."""
    src = 'A = "привет"\n\n\ndef f():\n    return "мир — ёж"\n'
    drafts = chunk_python("c.py", src)
    lines = src.split("\n")
    for draft in drafts:
        start, end = parse_line_range(draft.section) or (0, 0)
        body = draft.text.split("\n", 1)[1]
        assert body == "\n".join(lines[start - 1 : end])


@pytest.mark.parametrize("src", ["def f(:\n", "x = 1\x00\n", "def g():\n    pass\n\x00"])
def test_python_fallback_to_line_windows(src: str) -> None:
    """Broken Python falls back to line windows without raising."""
    drafts = chunk_python("bad.py", src)
    assert drafts
    assert drafts[0].section.startswith("L1-")


def test_empty_file_gives_no_fragments() -> None:
    """An empty file yields an empty list for both chunkers."""
    assert chunk_python("e.py", "") == []
    assert chunk_code("e.txt", "") == []


def test_line_windows_for_other_files() -> None:
    """Non-Python files use 50-line windows with 8 lines of overlap."""
    text = "\n".join(f"line {i}" for i in range(1, 121))
    drafts = chunk_code("ui/static/app.js", text)
    assert [d.section for d in drafts][:3] == ["L1-50", "L43-92", "L85-120"]
    assert drafts[0].text.startswith("# ui/static/app.js\n")
    assert chunk_code("pytest.ini", text)[1].section == "L43-92"


def test_crlf_is_normalised() -> None:
    """CRLF input produces the same fragments as LF input."""
    assert chunk_code("a.txt", "a\r\nb\r\nc\r\n") == chunk_code("a.txt", "a\nb\nc\n")


def test_chunk_lines_name_and_first_line() -> None:
    """Named windows carry the name and the caller's first line number."""
    drafts = chunk_lines("a.txt", "x\ny\n", name="N", first_line=10)
    assert drafts[0].section == "N (L10-11)"
    assert drafts[0].text.startswith("# a.txt :: N\n")


def test_parse_line_range() -> None:
    """Line ranges are read from named and unnamed sections only."""
    assert parse_line_range("C.m1 (L10-25)") == (10, 25)
    assert parse_line_range("L43-92") == (43, 92)
    assert parse_line_range("Введение") is None
    assert parse_line_range(None) is None


def test_char_offsets_point_into_normalised_text() -> None:
    """char_start/char_end delimit the fragment's lines in the normalised text."""
    drafts = chunk_python("b.py", SAMPLE)
    by_name = {d.section.rsplit(" (", 1)[0]: d for d in drafts}
    f = by_name["f"]
    assert SAMPLE[f.char_start : f.char_end] == "@decorator\ndef f(a):\n    return a"


def test_size_cap_over_real_repository_files() -> None:
    """No fragment of the repository's own sources exceeds the cap."""
    files = sorted((ROOT / "agent").glob("*.py")) + [ROOT / "ui" / "static" / "app.js"]
    assert len(files) > 5
    for file in files:
        text = file.read_text(encoding="utf-8")
        drafts = chunk_code(file.relative_to(ROOT).as_posix(), text)
        assert drafts, file
        for draft in drafts:
            assert len(draft.text) <= MAX_CHUNK_CHARS, (file, draft.section)
