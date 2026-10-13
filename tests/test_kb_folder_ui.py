"""Node-free source guard for the folder upload and pending list of the knowledge base form."""

import re

import pytest

from tests.test_limits_ui import STATIC_DIR, _app_js_source, _function_source, _index_html
from tests.test_static_js_syntax import check_js_balance


def _create_modal() -> str:
    """Return the markup of #kb-create-modal."""
    html: str = _index_html()
    start: int = html.index('id="kb-create-modal"')
    end: int = html.index('id="kb-search-modal"') if 'id="kb-search-modal"' in html else len(html)
    return html[start:end]


def _input_tag(html: str, element_id: str) -> str:
    """Return the <input ...> tag carrying the given id."""
    match = re.search(rf"<input[^>]*id=\"{re.escape(element_id)}\"[^>]*>", html)
    assert match is not None, f"input {element_id} not found"
    return match.group(0)


def test_create_modal_has_folder_input() -> None:
    """The folder input exists, has no accept/multiple, and the files input keeps both."""
    html: str = _index_html()
    folder: str = _input_tag(html, "kb-folder")
    assert "webkitdirectory" in folder
    assert "accept=" not in folder
    assert "multiple" not in folder
    files: str = _input_tag(html, "kb-files")
    assert "multiple" in files
    assert 'accept=".pdf,.txt,.md"' in files


@pytest.mark.parametrize(
    "element_id",
    [
        "kb-limits-hint",
        "kb-folder-report",
        "kb-file-list-header",
        "kb-file-count",
        "btn-kb-clear-list",
        "kb-file-list",
        "kb-file-list-empty",
    ],
)
def test_create_modal_ids_present(element_id: str) -> None:
    """Each new element id appears exactly once in index.html."""
    assert _index_html().count(f'id="{element_id}"') == 1


def test_limits_are_not_hardcoded_in_markup() -> None:
    """No limit numbers live in the create modal markup."""
    modal: str = _create_modal()
    for text in ("200 файл", "50 МБ", "100 МБ"):
        assert text not in modal


def test_limits_come_from_server() -> None:
    """Limit numbers are read from the server payload, not hardcoded."""
    src: str = _app_js_source()
    assert "/api/v1/kb/limits" in _function_source(src, "loadKbLimits")
    pending: str = _function_source(src, "createKbPending")
    for key in ("max_files", "max_file_bytes", "max_total_bytes"):
        assert key in pending
    for pattern in (r"\b200\b", r"\b50 МБ", r"\b100 МБ"):
        assert re.search(pattern, pending) is None


def test_source_name_uses_relative_path() -> None:
    """The source name is the relative path with a plain-name fallback."""
    body: str = _function_source(_app_js_source(), "kbSourceName")
    assert "webkitRelativePath" in body
    assert "file.name" in body


def test_upload_sends_source_name_as_filename() -> None:
    """The third argument of FormData.append carries the source name."""
    body: str = _function_source(_app_js_source(), "submitKbCreate")
    assert re.search(r"append\(\s*'files'\s*,\s*entry\.file\s*,\s*entry\.source\s*\)", body)
    assert "Content-Type" not in body


@pytest.mark.parametrize(
    "text",
    [
        "Из папки «",
        "пропущено",
        "неподдерживаемого типа",
        "нет файлов PDF, TXT или MD",
        "Уже в списке:",
        "Слишком много файлов:",
        "В списке:",
        "Убрать файл ",
    ],
)
def test_pending_list_copy(text: str) -> None:
    """The pending list controller carries the specified Russian copy."""
    assert text in _function_source(_app_js_source(), "createKbPending")


def test_pending_list_dedupes_by_source_and_never_filters_directories() -> None:
    """Duplicates are detected by source name; no directory skip list exists."""
    body: str = _function_source(_app_js_source(), "createKbPending")
    assert re.search(r"(some|find)\([^\n]*\.source ===", body)
    for name in (".git", "node_modules", "__pycache__", ".planning"):
        assert name not in body


@pytest.mark.parametrize(
    "name",
    ["createKbPending", "bindKbPending", "kbSourceName", "submitKbCreate", "buildKbResultCard"],
)
def test_no_inner_html_in_kb_upload_code(name: str) -> None:
    """Untrusted file names never reach innerHTML."""
    assert "innerHTML" not in _function_source(_app_js_source(), name)


def test_inputs_are_reset_after_choice() -> None:
    """The same file or folder can be chosen twice in a row."""
    assert "e.target.value = ''" in _function_source(_app_js_source(), "bindKbPending")


def test_old_selection_state_is_gone() -> None:
    """The flat selection state was replaced by the pending controller."""
    src: str = _app_js_source()
    assert "kbSelectedFiles" not in src
    assert "renderKbFileList" not in src


def test_result_card_shows_full_path() -> None:
    """Result cards expose the full source path on hover."""
    body: str = _function_source(_app_js_source(), "buildKbResultCard")
    assert "truncate" in body
    assert ".title = item.source" in body


def test_empty_state_mentions_folder() -> None:
    """The empty-state copy mentions folder upload."""
    assert "(можно целую папку)" in _app_js_source()


def test_app_js_still_balanced() -> None:
    """Brackets, strings and templates of app.js stay balanced."""
    text: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert check_js_balance(text) == []
