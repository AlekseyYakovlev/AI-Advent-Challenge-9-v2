"""Node-free source guard for the /support settings section, label, notes and notice."""

import re

import pytest

from tests.test_help_ui import _input_tag, _stage_label_text
from tests.test_limits_ui import STATIC_DIR, _app_js_source, _function_source, _index_html
from tests.test_static_js_syntax import check_js_balance

_SUPPORT_IDS: list[str] = [
    "support-section",
    "support-section-title",
    "support-kb-info",
    "support-top-k",
    "support-candidate-k",
    "support-threshold",
    "support-threshold-note",
    "support-threshold-reset",
    "support-stage-lexical",
    "support-stage-llm",
    "support-stage-hybrid",
    "support-stage-rewrite",
    "support-mcp-list",
    "support-mcp-hint",
    "support-tools-memory",
    "support-tools-tasks",
    "support-tools-scheduler",
    "support-save-status",
]

_STAGES: list[str] = ["lexical", "llm", "hybrid", "rewrite"]


def test_support_section_position_and_ids() -> None:
    """The section sits right after /help and before the LLM providers, with every control."""
    html: str = _index_html()
    assert html.index('id="help-section"') < html.index('id="support-section"') < html.index(
        'id="llm-providers-section"'
    )
    for element_id in _SUPPORT_IDS:
        assert f'id="{element_id}"' in html, element_id
    assert "Поддержка (/support)" in html


def test_support_has_no_kb_select() -> None:
    """The base is chosen in the /help section; /support only describes it."""
    html: str = _index_html()
    assert 'id="support-kb"' not in html
    assert 'id="support-kb-clear"' not in html


def test_support_numeric_fields_ranges() -> None:
    """The numeric fields carry the same bounds as the /help section."""
    html: str = _index_html()
    top_k: str = _input_tag(html, "support-top-k")
    assert 'min="1"' in top_k and 'max="20"' in top_k
    assert 'max="50"' in _input_tag(html, "support-candidate-k")
    assert 'step="0.01"' in _input_tag(html, "support-threshold")


@pytest.mark.parametrize("stage", _STAGES)
def test_support_stage_labels_match_help(stage: str) -> None:
    """Stage labels are the same strings as in the /help section."""
    html: str = _index_html()
    assert _stage_label_text(html, f"support-stage-{stage}") == _stage_label_text(html, f"help-stage-{stage}")


@pytest.mark.parametrize(
    "needle",
    [
        "Поддержка",
        "Сервер поддержки не добавлен. Добавьте его в разделе «MCP серверы» (имя support).",
        "База документации: та же, что отмечена для /help",
        "Не удалось сохранить настройки /support. Проверьте соединение и попробуйте снова.",
        "/api/v1/support/config",
        "data.command === 'support'",
        "SUPPORT_DEFAULT_SERVER_NAME = 'support'",
    ],
)
def test_support_strings_present(needle: str) -> None:
    """Required user-facing strings and wiring exist in app.js."""
    assert needle in _app_js_source()


def test_support_payload_used_by_render_and_threshold_line() -> None:
    """Stored /support payloads render the label and the threshold line skips them."""
    src: str = _app_js_source()
    assert "rag_sources.support" in _function_source(src, "renderMessages")
    assert "rag.support" in _function_source(src, "buildRagThresholdLine")


def test_support_builders_use_text_content_only() -> None:
    """Server strings never reach innerHTML in the label, notice, notes and section renderers."""
    src: str = _app_js_source()
    for name in ("buildHelpNotice", "buildHelpLabel", "buildHelpNotes"):
        assert "innerHTML" not in _function_source(src, name), name
    for match in re.finditer(r"innerHTML\s*=\s*`[^`]*\$\{", _function_source(src, "renderSupportSection")):
        raise AssertionError(match.group(0))
    assert "innerHTML" not in _function_source(src, "renderSupportMcpList")


def test_support_save_uses_put_with_rollback() -> None:
    """A failed save restores the previous config and shows an error toast."""
    body: str = _function_source(_app_js_source(), "saveSupportSetting")
    assert "method: 'PUT'" in body
    assert "state.supportConfig = previous" in body
    assert "showToast(SUPPORT_SAVE_ERROR, 'error')" in body


def test_send_message_does_not_inspect_support_command() -> None:
    """The client never recognises the command text."""
    assert "/support" not in _function_source(_app_js_source(), "sendMessage")


def test_app_js_balanced() -> None:
    """app.js keeps balanced brackets."""
    assert check_js_balance((STATIC_DIR / "app.js").read_text(encoding="utf-8")) == []
