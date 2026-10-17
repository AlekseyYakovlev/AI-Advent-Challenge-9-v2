"""Node-free source guard for the unified-diff rendering inside tool-call cards."""

import re
from pathlib import Path

import pytest

APP_JS: Path = Path(__file__).resolve().parent.parent / "ui" / "static" / "app.js"

_DIFF_FUNCTIONS: tuple[str, ...] = (
    "isUnifiedDiff",
    "classifyDiffLine",
    "diffLineCounts",
    "diffSummarySuffix",
    "buildDiffBlock",
)


def _raw_app_js() -> str:
    """Return app.js unmodified."""
    return APP_JS.read_text(encoding="utf-8")


def _app_js_source() -> str:
    """Return app.js without full-line // comments."""
    return "\n".join(
        line for line in _raw_app_js().splitlines() if not line.lstrip().startswith("//")
    )


def _function_source(src: str, name: str) -> str:
    """Return the source of a top-level function up to the next top-level function."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.MULTILINE)
    assert match is not None, f"function {name} not found"
    rest: str = src[match.end() :]
    nxt = re.search(r"^(?:async )?function ", rest, re.MULTILINE)
    return src[match.start() : match.end() + (nxt.start() if nxt else len(rest))]


@pytest.mark.parametrize("name", _DIFF_FUNCTIONS)
def test_diff_functions_defined(name: str) -> None:
    """Each diff helper is a top-level function in app.js."""
    assert re.search(rf"^function {name}\(", _app_js_source(), re.MULTILINE), f"missing {name}"


@pytest.mark.parametrize("name", _DIFF_FUNCTIONS)
def test_diff_functions_never_use_html_sinks(name: str) -> None:
    """Diff text must never reach innerHTML or insertAdjacentHTML."""
    body: str = _function_source(_app_js_source(), name)
    assert "innerHTML" not in body, f"{name} uses innerHTML"
    assert "insertAdjacentHTML" not in body, f"{name} uses insertAdjacentHTML"


def test_build_diff_block_uses_text_content_and_div() -> None:
    """Rows are plain divs filled through textContent."""
    body: str = _function_source(_app_js_source(), "buildDiffBlock")
    assert "textContent" in body, "textContent missing"
    assert "createElement('div')" in body, "createElement('div') missing"


def test_build_diff_block_accessibility_and_classes() -> None:
    """The container is a focusable labelled region with the specified palette."""
    body: str = _function_source(_app_js_source(), "buildDiffBlock")
    for needle in (
        "role",
        "region",
        "Diff изменения",
        "max-h-96",
        "font-mono",
        "bg-slate-950",
        "whitespace-pre",
        "min-w-full w-max",
        "bg-green-950/60 text-green-300",
        "bg-red-950/60 text-red-300",
        "bg-slate-800 text-indigo-300",
        "text-slate-400 font-semibold",
        "text-slate-300",
        "focus:ring-indigo-500",
    ):
        assert needle in body, f"buildDiffBlock missing {needle!r}"
    assert "tabindex" in body.lower(), "buildDiffBlock missing tabindex"


def test_classify_diff_line_rules() -> None:
    """The classifier recognises headers and checks hunk state before +/- rules."""
    body: str = _function_source(_app_js_source(), "classifyDiffLine")
    for needle in ("diff --git ", "'--- '", "'+++ '", "'@@'"):
        assert needle in body, f"classifyDiffLine missing {needle!r}"
    assert "inHunk" in body, "classifyDiffLine does not track inHunk"
    assert body.index("inHunk") < body.index("'+'"), "inHunk must be checked before the '+' rule"


def test_build_tool_call_card_uses_diff_branch() -> None:
    """The card switches to the diff block only for successful diff results."""
    body: str = _function_source(_app_js_source(), "buildToolCallCard")
    assert "evt.ok && isUnifiedDiff" in body, "diff branch condition missing"
    assert "buildDiffBlock" in body, "buildDiffBlock not used in the card"
    for needle in ("'Аргументы'", "' (обрезано)'", "resultPre"):
        assert needle in body, f"buildToolCallCard lost {needle!r}"


def test_diff_summary_suffix_contract() -> None:
    """Truncated results take counts from the server's first-line (+A -R)."""
    body: str = _function_source(_app_js_source(), "diffSummarySuffix")
    assert "evt.truncated" in body, "evt.truncated missing"
    assert r"\(\+(\d+) -(\d+)\)" in body, "first-line (+A -R) regex missing"
    assert "−" in body, "U+2212 minus missing"
