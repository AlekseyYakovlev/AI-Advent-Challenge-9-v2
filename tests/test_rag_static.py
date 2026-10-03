"""Static guards for the RAG UI: required DOM ids and textContent-only rendering of KB data."""
import re
from pathlib import Path

import pytest

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"
APP_JS: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
INDEX_HTML: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

RENDER_FUNCTIONS: list[str] = [
    "buildRagMeta",
    "buildRagSourcesBlock",
    "buildRagSourceRow",
    "loadRagSnippets",
    "renderRagControls",
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


@pytest.mark.parametrize("name", RENDER_FUNCTIONS)
def test_render_function_has_no_inner_html(name: str) -> None:
    assert "innerHTML" not in _function_source(name)


@pytest.mark.parametrize("name", RENDER_FUNCTIONS)
def test_render_function_uses_text_content(name: str) -> None:
    src = _function_source(name)
    assert "textContent" in src or "mcpEl(" in src


@pytest.mark.parametrize(
    "text",
    [
        "Источники (",
        "показать полностью",
        "свернуть",
        "Текст фрагмента недоступен: база знаний удалена",
        "без RAG (сбой поиска)",
        "Поиск по базе знаний не удался — ответ дан без RAG",
        "Не удалось сохранить настройки RAG",
        "encodeURIComponent(chunkId)",
        "data.rag && data.rag.warning",
    ],
)
def test_app_js_contains_copy(text: str) -> None:
    assert text in APP_JS


@pytest.mark.parametrize(
    "dom_id", ["rag-badge", "rag-toggle", "rag-kb-select", "rag-k-wrap", "rag-k-input"]
)
def test_index_html_has_rag_ids(dom_id: str) -> None:
    assert f'id="{dom_id}"' in INDEX_HTML


def test_k_input_bounds() -> None:
    tag = re.search(r'<input id="rag-k-input"[^>]*>', INDEX_HTML)
    assert tag
    assert 'min="1"' in tag.group(0)
    assert 'max="20"' in tag.group(0)
    assert 'role="switch"' in INDEX_HTML
