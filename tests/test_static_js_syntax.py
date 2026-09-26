"""Node-free structural sanity check for the vanilla JS frontend."""

from pathlib import Path

import pytest

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_CLOSERS: dict[str, str] = {")": "(", "]": "[", "}": "{"}
_REGEX_PRECEDERS: frozenset[str] = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS: frozenset[str] = frozenset(
    {"return", "typeof", "case", "in", "of", "void", "delete", "throw", "new"}
)


def _skip_string(src: str, start: int, line: int) -> tuple[int, int, str | None]:
    """Scan a quoted string; return (next index, line, error or None)."""
    quote: str = src[start]
    i: int = start + 1
    while i < len(src):
        ch: str = src[i]
        if ch == "\\":
            if src[i + 1 : i + 2] == "\n":
                line += 1
            i += 2
            continue
        if ch == "\n":
            return i, line, f"unterminated string line {line}"
        if ch == quote:
            return i + 1, line, None
        i += 1
    return i, line, f"unterminated string line {line}"


def _skip_regex(src: str, start: int, line: int) -> tuple[int, str | None]:
    """Scan a regex literal (with flags); return (next index, error or None)."""
    i: int = start + 1
    in_class: bool = False
    while i < len(src):
        ch: str = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "\n":
            return i, f"unterminated regex line {line}"
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            while i < len(src) and src[i].isalpha():
                i += 1
            return i, None
        i += 1
    return i, f"unterminated regex line {line}"


def _template_step(
    src: str, i: int, line: int, stack: list[tuple[str, int]]
) -> tuple[int, int]:
    """Advance one step inside template-literal text; return (next index, line)."""
    ch: str = src[i]
    if ch == "\\":
        if src[i + 1 : i + 2] == "\n":
            line += 1
        return i + 2, line
    if ch == "\n":
        return i + 1, line + 1
    if ch == "`":
        stack.pop()
        return i + 1, line
    if ch == "$" and src[i + 1 : i + 2] == "{":
        stack.append(("${", line))
        return i + 2, line
    return i + 1, line


def _is_regex_start(last: str) -> bool:
    """Decide whether a '/' begins a regex given the previous significant token."""
    return last == "" or last in _REGEX_PRECEDERS or last in _REGEX_KEYWORDS


def check_js_balance(src: str) -> list[str]:
    """Report unbalanced brackets, unterminated strings/templates and comments."""
    errors: list[str] = []
    stack: list[tuple[str, int]] = []
    i: int = 0
    line: int = 1
    last: str = ""
    n: int = len(src)

    while i < n:
        ch: str = src[i]
        if stack and stack[-1][0] == "`":
            i, line = _template_step(src, i, line, stack)
            last = "x"
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        nxt: str = src[i + 1 : i + 2]
        if ch == "/" and nxt == "/":
            end: int = src.find("\n", i)
            i = n if end == -1 else end
            continue
        if ch == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            if end == -1:
                errors.append(f"unterminated comment from line {line}")
                break
            line += src.count("\n", i, end)
            i = end + 2
            continue
        if ch in "'\"":
            i, line, err = _skip_string(src, i, line)
            if err:
                errors.append(err)
            last = "x"
            continue
        if ch == "`":
            stack.append(("`", line))
            i += 1
            continue
        if ch == "/":
            if _is_regex_start(last):
                i, err = _skip_regex(src, i, line)
                if err:
                    errors.append(err)
                last = "x"
            else:
                last = "/"
                i += 1
            continue
        if ch in "([{":
            stack.append((ch, line))
            last = ch
            i += 1
            continue
        if ch in ")]}":
            if ch == "}" and stack and stack[-1][0] == "${":
                stack.pop()
            elif not stack:
                errors.append(f"unexpected {ch} @{line}")
            else:
                opener, opened_at = stack.pop()
                if opener != _CLOSERS[ch]:
                    errors.append(f"mismatch {opener} @{opened_at} closed by {ch} @{line}")
            last = ch
            i += 1
            continue
        if ch.isalnum() or ch in "_$":
            j: int = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            last = src[i:j]
            i = j
            continue
        last = ch
        i += 1

    for opener, opened_at in stack:
        label: str = "template literal" if opener == "`" else opener
        errors.append(f"unclosed {label} from line {opened_at}")
    return errors


@pytest.mark.parametrize("path", sorted(STATIC_DIR.glob("*.js")), ids=lambda p: p.name)
def test_static_js_is_balanced(path: Path) -> None:
    """Every shipped JS file has balanced brackets, strings and templates."""
    text: str = path.read_text(encoding="utf-8")
    assert check_js_balance(text) == []


@pytest.mark.parametrize(
    "sample",
    [
        "function f() {\n  if (x) {\n    y();\n}\n",
        "function f() { return 1 )\n",
        "const s = 'abc\nconst t = 1;\n",
        "const s = `a ${b;\n",
    ],
    ids=["unclosed-brace", "wrong-closer", "unterminated-string", "unclosed-template-expr"],
)
def test_checker_detects_errors(sample: str) -> None:
    """The scanner reports structural breakage instead of passing silently."""
    assert check_js_balance(sample) != []


def test_checker_accepts_tricky_valid_code() -> None:
    """Regex literals, nested templates, comments and strings do not confuse the scanner."""
    sample: str = (
        "const re = /[/\\]]+/g;\n"
        "const s = `outer ${ items.map((x) => `inner ${x}`).join(',') } end`;\n"
        "// comment with { unmatched\n"
        "/* block ( comment */\n"
        'const t = "a ) b";\n'
        "const ratio = total / count / 2;\n"
    )
    assert check_js_balance(sample) == []
