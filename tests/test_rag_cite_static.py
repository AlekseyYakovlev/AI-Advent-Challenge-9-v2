"""Static guards for the citation UI: strict switch, quotes block, chips, answer lines and textContent-only rendering."""
import re
from pathlib import Path

import pytest

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"
APP_JS: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
INDEX_HTML: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    """Return the source text of a top-level JS function by name."""
    match = re.search(
        rf"^(?:async )?function {name}\(.*?(?=^(?:async )?function |\Z)",
        APP_JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"function {name} not found in app.js"
    return match.group(0)


def _strict_input_tag() -> str:
    match = re.search(r"<input[^>]*id=\"rag-strict\"[^>]*>", INDEX_HTML)
    assert match, "rag-strict input not found in index.html"
    return match.group(0)


def test_strict_switch_markup() -> None:
    assert INDEX_HTML.count('id="rag-strict"') == 1
    tag = _strict_input_tag()
    assert 'type="checkbox"' in tag
    assert 'role="switch"' in tag
    assert "name=" not in tag


def test_strict_switch_is_first_in_switch_list() -> None:
    assert INDEX_HTML.index('id="rag-strict"') < INDEX_HTML.index('id="rag-stage-lexical"')


def test_strict_switch_copy() -> None:
    assert "Строгий режим" in INDEX_HTML
    assert "цитаты + «не знаю»" in INDEX_HTML


def test_popover_renders_strict_value() -> None:
    assert "rag-strict" in _function_source("renderRagSearchPopover")


def test_strict_switch_does_not_light_search_button() -> None:
    src = _function_source("renderRagSearchPopover")
    flags = re.search(r"const flags = \{.*?\};", src, re.DOTALL)
    assert flags
    assert "rag-strict" not in flags.group(0)


def test_strict_is_saved_through_rag_put() -> None:
    assert "'strict'" in _function_source("saveChatRag")
    assert "strict: e.target.checked" in _function_source("bindRagSearchUi")


QUOTE_FUNCTIONS: list[str] = [
    "buildRagQuotesBlock",
    "buildRagQuoteRow",
    "buildRagQuoteChip",
]


@pytest.mark.parametrize("name", QUOTE_FUNCTIONS)
def test_quote_functions_have_no_html_injection(name: str) -> None:
    src = _function_source(name)
    for forbidden in ("innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert forbidden not in src


@pytest.mark.parametrize("name", QUOTE_FUNCTIONS)
def test_quote_functions_do_not_strike_through(name: str) -> None:
    assert "line-through" not in _function_source(name)


@pytest.mark.parametrize(
    "copy",
    [
        "Цитаты (",
        "✓ подтверждена",
        "≈ почти дословно",
        "✗ не подтверждена",
        "(нет такого источника)",
        "источник исправлен",
        "подобрана автоматически",
    ],
)
def test_quote_copy_present(copy: str) -> None:
    assert copy in APP_JS


def test_quotes_block_conditions() -> None:
    src = _function_source("buildRagQuotesBlock")
    assert "'open'" in src or ".open = true" in src
    assert "rag.strict" in src
    assert "model_idk" in src


def test_quotes_block_precedes_sources_in_render_messages() -> None:
    src = _function_source("renderMessages")
    assert "buildRagQuotesBlock(rag)" in src
    assert src.index("buildRagQuotesBlock(rag)") < src.index("buildRagSourcesBlock(rag)")


@pytest.mark.parametrize(
    "copy",
    [
        "цитируется",
        "⚠ ответ не подтверждён фрагментами",
        "Модель ответила «не знаю»: фрагменты не содержат ответа",
        "Несуществующих ссылок на источники в ответе: ",
        "Модель не вернула текст ответа",
    ],
)
def test_answer_line_copy_present(copy: str) -> None:
    assert copy in APP_JS


def test_sources_block_orders_cited_first() -> None:
    assert "cited_ranks" in _function_source("buildRagSourcesBlock")


def test_meta_renders_answer_lines() -> None:
    src = _function_source("buildRagMeta")
    assert "buildRagThresholdLine(rag)" in src
    for token in ("model_idk", "answer_supported", "invalid_refs", "answer_empty"):
        assert token in src


def test_threshold_line_skips_missing_cosine() -> None:
    assert "best_cosine" in _function_source("buildRagThresholdLine")


@pytest.mark.parametrize("name", ["buildRagMeta", "buildRagSourcesBlock", "buildRagSourceRow"])
def test_answer_line_functions_have_no_html_injection(name: str) -> None:
    src = _function_source(name)
    for forbidden in ("innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert forbidden not in src
