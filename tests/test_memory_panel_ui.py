"""Node-free source guard for the editable long-term memory panel in app.js."""

import re
from pathlib import Path

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_START_MARKER: str = "async function loadChatMemory("
_END_MARKER: str = "async function loadChatTasks("


def _app_js_source() -> str:
    """Return app.js without full-line // comments."""
    text: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )


def _memory_region() -> str:
    """Return the app.js text between loadChatMemory and loadChatTasks."""
    src: str = _app_js_source()
    assert src.count(_START_MARKER) == 1
    assert src.count(_END_MARKER) == 1
    start: int = src.index(_START_MARKER)
    end: int = src.index(_END_MARKER)
    assert start < end
    return src[start:end]


def _function_source(src: str, name: str) -> str:
    """Return the source of a top-level function up to the next top-level function."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.MULTILINE)
    assert match is not None, f"function {name} not found"
    rest: str = src[match.end() :]
    nxt = re.search(r"^(?:async )?function ", rest, re.MULTILINE)
    return src[match.start() : match.end() + (nxt.start() if nxt else len(rest))]


def test_memory_region_markers_exist() -> None:
    """Both region markers exist exactly once and in order."""
    assert _memory_region()


def test_memory_buttons_have_required_labels() -> None:
    """The region carries all four Russian button labels."""
    region: str = _memory_region()
    for label in ("'Редактировать'", "'Удалить'", "'Сохранить'", "'Отмена'"):
        assert label in region


def test_memory_region_never_inserts_html() -> None:
    """Memory text must only reach the DOM as text."""
    region: str = _memory_region()
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "marked.parse",
        "renderMarkdown(",
    ):
        assert banned not in region


def test_memory_edit_uses_user_scoped_routes() -> None:
    """Edit and delete go through the long-term memory REST routes."""
    region: str = _memory_region()
    assert "/api/v1/memory/long-term/" in region
    assert "method: 'PUT'" in region
    assert "method: 'DELETE'" in region


def test_delete_asks_confirmation_before_request() -> None:
    """confirm() precedes the DELETE request."""
    body: str = _function_source(_memory_region(), "deleteLongTermMemory")
    assert "confirm(" in body
    assert body.index("confirm(") < body.index("method: 'DELETE'")


def test_only_long_term_list_is_editable() -> None:
    """Only the long-term container is rendered with editable controls."""
    region: str = _memory_region()
    assert region.count("editable: true") == 1
    lines: list[str] = region.splitlines()
    long_term = [ln for ln in lines if "renderMemoryEntries(longTermEl" in ln]
    working = [ln for ln in lines if "renderMemoryEntries(workingEl" in ln]
    assert len(long_term) == 1 and "editable: true" in long_term[0]
    assert len(working) == 1 and "editable" not in working[0]


def test_edit_form_is_seeded_from_full_value() -> None:
    """The edit draft carries the full value, never the truncated preview."""
    region: str = _memory_region()
    form: str = _function_source(region, "buildMemoryEditForm")
    assert "state.editingMemory" in form
    assert ".slice(" not in form
    assert "value: entry.value" in _function_source(region, "startMemoryEdit")


def test_memory_ui_adds_no_modal_or_key_handler() -> None:
    """The inline editor adds no modal and no keyboard handler."""
    region: str = _memory_region()
    for banned in ("-modal", "'keydown'", "'keyup'", "'Escape'"):
        assert banned not in region
