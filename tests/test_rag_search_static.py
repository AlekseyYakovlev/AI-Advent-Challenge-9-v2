"""Static guards for the search UI: DOM ids, copy strings and textContent-only rendering of trace data."""
import re
from pathlib import Path

import pytest

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"
APP_JS: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
INDEX_HTML: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

SEARCH_RENDER_FUNCTIONS: list[str] = [
    "buildRagDetailsBlock",
    "buildRagThresholdLine",
    "renderRagSearchPopover",
    "saveRagSearchSetting",
]

# The details block is assembled from these helpers; the whole group is guarded.
DETAILS_FUNCTIONS: list[str] = [
    "buildRagDetailsBlock",
    "buildRagCandidatesTable",
    "buildRagStatusCell",
    "buildRagDetailRow",
    "ragSkipReasonText",
    "buildRagThresholdLine",
]

SEARCH_IDS: list[str] = [
    "rag-search-btn",
    "rag-search-popover",
    "rag-candidate-k",
    "rag-threshold",
    "rag-threshold-reset",
    "rag-stage-lexical",
    "rag-stage-llm",
    "rag-stage-hybrid",
    "rag-stage-rewrite",
]


def _function_source(name: str) -> str:
    """Return the source text of a top-level JS function by name."""
    match = re.search(
        rf"^(?:async )?function {name}\(.*?(?=^(?:async )?function |\Z)",
        APP_JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"function {name} not found in app.js"
    return match.group(0)


@pytest.mark.parametrize("name", SEARCH_RENDER_FUNCTIONS + DETAILS_FUNCTIONS)
def test_function_has_no_html_injection(name: str) -> None:
    src = _function_source(name)
    for forbidden in ("innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert forbidden not in src


@pytest.mark.parametrize("name", ["buildRagDetailsBlock", "buildRagThresholdLine"])
def test_details_functions_use_text_content(name: str) -> None:
    src = _function_source(name)
    assert "textContent" in src or "mcpEl(" in src


@pytest.mark.parametrize("name", DETAILS_FUNCTIONS)
def test_details_functions_have_no_toast_or_yellow(name: str) -> None:
    src = _function_source(name)
    assert "showToast" not in src
    assert "yellow" not in src


@pytest.mark.parametrize(
    "text",
    [
        "Детали поиска",
        "Запрос:",
        "Переписан:",
        "Этапы:",
        "Пропущено:",
        "было→стало",
        "Источник",
        "Статус",
        "✓ в ответе",
        "ниже порога",
        "вне top-K",
        "не вошло в бюджет",
        "≈ прошло по FTS",
        "исходный",
        "переписан",
        "оба",
        "Фрагменты не прошли порог (лучший ",
        "Кандидатов нет: в базе нечего сравнивать",
        "некорректный ответ модели",
        "таймаут",
        "ошибка запроса FTS5",
        "пустой или слишком длинный результат",
        "(калибр.)",
        "(нет калибровки)",
        "Не удалось сохранить настройки поиска. Проверьте соединение и попробуйте снова.",
    ],
)
def test_app_js_contains_copy(text: str) -> None:
    assert text in APP_JS


def test_details_block_handles_all_statuses() -> None:
    src = "".join(_function_source(name) for name in DETAILS_FUNCTIONS)
    for token in ("below_threshold", "outside_top_k", "over_budget", "in_answer", "fts_exempt"):
        assert token in src


@pytest.mark.parametrize("dom_id", SEARCH_IDS)
def test_index_html_has_search_ids(dom_id: str) -> None:
    assert f'id="{dom_id}"' in INDEX_HTML


def test_four_independent_switches() -> None:
    assert INDEX_HTML.count('role="switch"') >= 4
    for stage in ("lexical", "llm", "hybrid", "rewrite"):
        tag = re.search(rf'<input id="rag-stage-{stage}"[^>]*>', INDEX_HTML)
        assert tag
        assert 'type="checkbox"' in tag.group(0)
        assert "name=" not in tag.group(0)


def test_input_bounds() -> None:
    candidate = re.search(r'<input id="rag-candidate-k"[^>]*>', INDEX_HTML)
    threshold = re.search(r'<input id="rag-threshold"[^>]*>', INDEX_HTML)
    assert candidate and 'max="50"' in candidate.group(0)
    assert threshold and 'step="0.01"' in threshold.group(0)


def test_details_wired_into_message_rendering() -> None:
    assert "buildRagDetailsBlock(rag)" in _function_source("renderMessages")
    assert "buildRagThresholdLine(rag)" in _function_source("buildRagMeta")
