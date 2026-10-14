"""Source-level checks of the review settings section in the Settings window."""

import re
from pathlib import Path

from tests.test_static_js_syntax import check_js_balance

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_HTML_COPY: tuple[str, ...] = (
    "Ревью Pull Request",
    "Сохранить настройки ревью",
    "Обновить индекс кода",
    "Путь к репозиторию",
    "База документации",
    "Не выбрана",
    "Состояние индекса кода",
    "Не построен",
    "Сначала укажите путь и сохраните настройки.",
)
_JS_COPY: tuple[str, ...] = ("Индексация…", "Идёт индексация", "Обновлён: ")
_CONTROLS: tuple[str, ...] = (
    "review-repo-path",
    "review-docs-kb",
    "review-provider",
    "review-model",
)
_ROUTES: tuple[str, ...] = (
    "/api/v1/reviews/config",
    "/api/v1/reviews/code-index/refresh",
    "/api/v1/reviews/code-index'",
    "/api/v1/kb",
)
_FUNCTIONS: tuple[str, ...] = ("loadReviewSettings", "saveReviewSettings", "refreshReviewIndex")


def _app_js() -> str:
    """Return app.js."""
    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def _section() -> str:
    """Return the review settings section markup."""
    html: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    start: int = html.index('<section id="review-settings-section"')
    return html[start : html.index("</section>", start)]


def _function_source(src: str, name: str) -> str:
    """Return the source of a top-level function up to the next top-level function."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.MULTILINE)
    assert match is not None, f"function {name} not found"
    rest: str = src[match.end() :]
    nxt = re.search(r"^(?:async )?function ", rest, re.MULTILINE)
    return src[match.start() : match.end() + (nxt.start() if nxt else len(rest))]


def test_section_position() -> None:
    """The section sits after the providers section and before the API keys section."""
    html: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    providers: int = html.index('id="llm-providers-section"')
    review: int = html.index('id="review-settings-section"')
    keys: int = html.index('id="api-keys-section"')
    assert providers < review < keys


def test_html_copy_present() -> None:
    """The contract copy is in the markup."""
    section: str = _section()
    for text in _HTML_COPY:
        assert text in section, text


def test_js_copy_present() -> None:
    """The contract copy built in app.js is present."""
    js: str = _app_js()
    for text in _JS_COPY:
        assert text in js, text


def test_controls_have_labels() -> None:
    """Each of the four controls has a label bound by for=."""
    section: str = _section()
    for control in _CONTROLS:
        assert f'id="{control}"' in section
        assert f'for="{control}"' in section
    assert len(re.findall(r"<(?:input|select|textarea)\b", section)) == 4


def test_aria_live_lines() -> None:
    """The status and error lines are polite live regions."""
    section: str = _section()
    for line_id in ("review-index-status", "review-error"):
        tag: str = re.search(rf'<p id="{line_id}"[^>]*>', section).group(0)  # type: ignore[union-attr]
        assert 'aria-live="polite"' in tag


def test_accent_only_on_save_button() -> None:
    """The indigo fill is used by the save button alone, apart from the focus ring."""
    section: str = _section()
    fills: list[str] = re.findall(r"<[^>]*bg-indigo-600[^>]*>", section)
    assert len(fills) == 1
    assert 'id="review-save-btn"' in fills[0]


def test_cloud_note_present() -> None:
    """A note about code leaving the machine sits under the provider selector."""
    section: str = _section()
    assert 'id="review-cloud-note"' in section
    assert "text-yellow-500" in section


def test_functions_do_not_use_inner_html() -> None:
    """Server strings are written without innerHTML in the new functions."""
    js: str = _app_js()
    for name in _FUNCTIONS:
        assert "innerHTML" not in _function_source(js, name), name


def test_routes_and_wiring() -> None:
    """Routes are used and the Settings window opens the section loader."""
    js: str = _app_js()
    for route in _ROUTES:
        assert route in js, route
    assert "loadReviewSettings" in _function_source(js, "openSettingsModal")
    assert "stopReviewPolling" in _function_source(js, "closeSettingsModal")


def test_js_balance() -> None:
    """app.js stays syntactically balanced."""
    assert check_js_balance(_app_js()) == []
