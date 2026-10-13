"""Node-free source guard for the /help settings section, label, badge and notice."""

import re

import pytest

from tests.test_limits_ui import STATIC_DIR, _app_js_source, _function_source, _index_html
from tests.test_static_js_syntax import check_js_balance

_HELP_IDS: list[str] = [
    "help-section",
    "help-kb",
    "help-kb-clear",
    "help-top-k",
    "help-candidate-k",
    "help-threshold",
    "help-threshold-note",
    "help-threshold-reset",
    "help-stage-lexical",
    "help-stage-llm",
    "help-stage-hybrid",
    "help-stage-rewrite",
    "help-mcp-list",
    "help-tools-memory",
    "help-tools-tasks",
    "help-tools-scheduler",
    "help-save-status",
]

_STAGES: list[str] = ["lexical", "llm", "hybrid", "rewrite"]


def _input_tag(html: str, element_id: str) -> str:
    """Return the <input ...> tag carrying the given id."""
    match = re.search(rf"<input[^>]*id=\"{re.escape(element_id)}\"[^>]*>", html)
    assert match is not None, f"input {element_id} not found"
    return match.group(0)


def _stage_label_text(html: str, element_id: str) -> str:
    """Return the visible text of the label wrapping the given checkbox."""
    for block in re.findall(r"<label[^>]*>.*?</label>", html, re.DOTALL):
        if f'id="{element_id}"' in block:
            match = re.search(r"<span>([^<]+)</span>", block)
            assert match is not None, f"no label text for {element_id}"
            return match.group(1)
    raise AssertionError(f"label for {element_id} not found")


def test_help_section_position_and_ids() -> None:
    """The section sits between the MCP and LLM provider sections and has every control."""
    html: str = _index_html()
    assert html.index('id="mcp-section"') < html.index('id="help-section"') < html.index(
        'id="llm-providers-section"'
    )
    for element_id in _HELP_IDS:
        assert f'id="{element_id}"' in html, element_id
    assert "Справка по проекту (/help)" in html


def test_help_numeric_fields_ranges() -> None:
    """The numeric fields mirror the chat ranges."""
    html: str = _index_html()
    top_k: str = _input_tag(html, "help-top-k")
    assert 'min="1"' in top_k and 'max="20"' in top_k
    assert 'max="50"' in _input_tag(html, "help-candidate-k")
    assert 'step="0.01"' in _input_tag(html, "help-threshold")


@pytest.mark.parametrize("stage", _STAGES)
def test_help_stage_labels_match_chat_popover(stage: str) -> None:
    """Stage labels are the same strings as in the chat search popover."""
    html: str = _index_html()
    assert _stage_label_text(html, f"help-stage-{stage}") == _stage_label_text(html, f"rag-stage-{stage}")


def test_help_notice_case_is_transient() -> None:
    """The notice case never stores a message, toasts or reloads the tree, and frees the input."""
    body: str = _function_source(_app_js_source(), "handleWsMessage")
    start: int = body.index("case 'help_notice'")
    segment: str = body[start : body.index("break;", start)]
    for forbidden in ("state.messages", "showToast(", "loadChatTree("):
        assert forbidden not in segment, forbidden
    assert "unblockInput(" in segment


def test_help_builders_use_text_content_only() -> None:
    """Label, notice and section renderers never assign innerHTML with data."""
    src: str = _app_js_source()
    assert "innerHTML" not in _function_source(src, "buildHelpNotice")
    assert "innerHTML" not in _function_source(src, "buildHelpLabel")
    for match in re.finditer(r"innerHTML\s*=\s*`[^`]*\$\{", _function_source(src, "renderHelpSection")):
        raise AssertionError(match.group(0))


def test_render_messages_and_kb_card_markers() -> None:
    """Stored /help payloads render a label and a nothing-found note, and the card gets a badge."""
    src: str = _app_js_source()
    render: str = _function_source(src, "renderMessages")
    assert "help" in render
    assert "В документации ничего не найдено." in src
    assert "Документация проекта" in _function_source(src, "buildKbCard")


def test_send_message_does_not_inspect_command() -> None:
    """The client never recognises the command text."""
    assert "/help" not in _function_source(_app_js_source(), "sendMessage")


@pytest.mark.parametrize(
    "needle",
    [
        "Справка по проекту · ",
        "Это сообщение не сохраняется в чате.",
        "Сохранено",
        "Не удалось сохранить настройки /help. Проверьте соединение и попробуйте снова.",
        "/api/v1/help/config",
    ],
)
def test_help_strings_present(needle: str) -> None:
    """Required user-facing strings exist in app.js."""
    assert needle in _app_js_source()


def test_app_js_balanced() -> None:
    """app.js keeps balanced brackets."""
    assert check_js_balance((STATIC_DIR / "app.js").read_text(encoding="utf-8")) == []
