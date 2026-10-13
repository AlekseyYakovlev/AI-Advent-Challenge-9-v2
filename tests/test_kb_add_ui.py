"""Node-free source guard for adding files to a knowledge base."""

import re

import pytest

from tests.test_limits_ui import _app_js_source, _function_source, _index_html
from tests.test_static_js_syntax import check_js_balance

ADD_MODAL_IDS: list[str] = [
    "kb-add-modal",
    "kb-add-title",
    "btn-close-kb-add",
    "kb-add-form",
    "kb-add-files",
    "kb-add-folder",
    "kb-add-limits-hint",
    "kb-add-folder-report",
    "kb-add-file-list-header",
    "kb-add-file-count",
    "btn-kb-add-clear-list",
    "kb-add-file-list",
    "kb-add-file-list-empty",
    "kb-add-params",
    "kb-add-error",
    "btn-kb-add-submit",
]


def _add_modal() -> str:
    """Return the markup of #kb-add-modal."""
    html: str = _index_html()
    start: int = html.index('id="kb-add-modal"')
    ends: list[int] = [
        pos for pos in (html.find("<!-- ", start), html.find('-modal"', start + 20)) if pos != -1
    ]
    return html[start : min(ends) if ends else len(html)]


def _card_source() -> str:
    """Return buildKbCard plus the helpers it relies on."""
    src: str = _app_js_source()
    return "\n".join(
        _function_source(src, name) for name in ("buildKbCard", "kbAddFailureText", "kbErrorLine")
    )


@pytest.mark.parametrize("element_id", ADD_MODAL_IDS)
def test_add_modal_ids_present(element_id: str) -> None:
    """Each add modal id appears exactly once in index.html."""
    assert _index_html().count(f'id="{element_id}"') == 1


def test_add_modal_has_no_settings_fields() -> None:
    """The add modal carries no strategy, size, overlap or model fields."""
    modal: str = _add_modal()
    for text in ("kb-strategy", "kb-chunk-size", "kb-chunk-overlap", "kb-embedding-model", "<select"):
        assert text not in modal


def test_add_modal_folder_input() -> None:
    """The folder input is a directory picker without an accept filter."""
    match = re.search(r"<input[^>]*id=\"kb-add-folder\"[^>]*>", _index_html())
    assert match is not None
    assert "webkitdirectory" in match.group(0)
    assert "accept=" not in match.group(0)


def test_add_submit_posts_sources_to_files_route() -> None:
    """Submit posts each file under its source name to the files route only."""
    body: str = _function_source(_app_js_source(), "submitKbAdd")
    for text in ("/files", "method: 'POST'", "credentials: 'include'"):
        assert text in body
    assert re.search(r"append\(\s*'files'\s*,\s*entry\.file\s*,\s*entry\.source\s*\)", body)
    for text in ("Content-Type", "'strategy'", "'chunk_size'", "'embedding_model'", "'name'"):
        assert text not in body


def test_add_submit_copy() -> None:
    """The submit button texts are present."""
    body: str = _function_source(_app_js_source(), "submitKbAdd")
    assert "Добавляю…" in body
    assert "Добавить в базу" in body


def test_add_modal_states_base_parameters() -> None:
    """The modal states that the base's own parameters are used."""
    body: str = _function_source(_app_js_source(), "openKbAddModal")
    assert "Файлы будут проиндексированы с параметрами этой базы:" in body
    assert "Добавить файлы в «" in body


def test_adding_state_is_ready_plus_phase() -> None:
    """A running addition is a ready base with a phase."""
    body: str = _function_source(_app_js_source(), "kbIsAdding")
    assert "kb.status === 'ready'" in body
    assert "kb.phase" in body


@pytest.mark.parametrize(
    "text",
    [
        "Добавление: ",
        "Добавлено: ",
        "заменено: ",
        "без изменений: ",
        "Скрыть сводку",
        "Добавить файлы",
        "Идёт добавление файлов",
        "Не удалось добавить файлы: ",
        "База осталась без изменений.",
    ],
)
def test_card_copy(text: str) -> None:
    """Every card string of the add flow is present."""
    assert text in _card_source()


def test_add_button_only_for_ready() -> None:
    """The add button sits behind a ready check and the chip never shows the addition."""
    src: str = _app_js_source()
    card: str = _function_source(src, "buildKbCard")
    assert "kb.status === 'ready'" in card.split("'Добавить файлы'")[0].split("leftButtons.appendChild(searchBtn)")[1]
    chip: str = _function_source(src, "kbStatusChip")
    assert "adding" not in chip
    assert "Добавление" not in chip


def test_search_stays_enabled_during_add() -> None:
    """The search button is not gated on a running addition."""
    card: str = _function_source(_app_js_source(), "buildKbCard")
    start: int = card.index("Тест поиска")
    end: int = card.index("openKbSearchModal")
    assert "kbIsAdding" not in card[start:end]
    assert "adding" not in card[start:end]


def test_summary_kept_outside_kb_object() -> None:
    """The summary lives in a map keyed by base id."""
    src: str = _app_js_source()
    assert "state.kbAddSummary = new Map()" in src
    frame: str = _function_source(src, "handleEventFrame")
    assert "frame.add_summary" in frame
    assert "state.kbAddSummary.set(" in frame
    assert "state.kbAddSummary.delete(" in _function_source(src, "removeKb")


def test_success_toast() -> None:
    """A finished addition raises a success toast."""
    assert "Файлы добавлены в базу «" in _function_source(_app_js_source(), "handleEventFrame")


@pytest.mark.parametrize(
    "name",
    ["openKbAddModal", "submitKbAdd", "buildKbCard", "kbAddFailureText", "kbErrorLine", "kbIsAdding"],
)
def test_no_inner_html_in_add_code(name: str) -> None:
    """The add flow never writes HTML strings."""
    assert "innerHTML" not in _function_source(_app_js_source(), name)


def test_add_modal_close_button_is_bound() -> None:
    """The close button is wired and there is no overlay listener."""
    src: str = _app_js_source()
    assert "$('btn-close-kb-add').addEventListener('click'" in src
    assert "$('kb-add-modal').addEventListener" not in src


def test_app_js_still_balanced() -> None:
    """The script keeps balanced brackets and quotes."""
    assert not check_js_balance(_app_js_source())
