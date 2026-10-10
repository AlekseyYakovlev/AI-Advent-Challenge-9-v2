"""Node-free source guards for the task-memory UI in index.html and app.js."""

import re
from pathlib import Path

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"
INDEX_HTML: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
APP_JS: str = "\n".join(
    line
    for line in (STATIC_DIR / "app.js").read_text(encoding="utf-8").splitlines()
    if not line.lstrip().startswith("//")
)

BANNED_HTML_SINKS: tuple[str, ...] = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
)


def _function_source(name: str) -> str:
    """Return the source of a top-level JS function up to the next top-level function."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", APP_JS, re.MULTILINE)
    assert match is not None, f"function {name} not found in app.js"
    rest: str = APP_JS[match.end() :]
    nxt = re.search(r"^(?:async )?function ", rest, re.MULTILINE)
    return APP_JS[match.start() : match.end() + (nxt.start() if nxt else len(rest))]


def _case_done_source() -> str:
    """Return the handleWsMessage `done` case body."""
    start: int = APP_JS.index("case 'done':")
    end: int = APP_JS.index("case 'error':", start)
    return APP_JS[start:end]


def test_sidebar_block_markup_and_position() -> None:
    """The wrapper sits between the short-term and working rows and is accessible."""
    tag = re.search(r'<div id="memory-task-state"[^>]*>', INDEX_HTML)
    assert tag is not None
    assert 'role="group"' in tag.group(0)
    assert 'aria-label="Память задачи"' in tag.group(0)
    assert "hidden" in tag.group(0)
    assert INDEX_HTML.count('id="memory-task-state"') == 1
    assert 'id="memory-task-actions"' in INDEX_HTML
    assert 'id="memory-task-body"' in INDEX_HTML
    short = INDEX_HTML.index("memory-short-term-count")
    task = INDEX_HTML.index("memory-task-state")
    working = INDEX_HTML.index("memory-working-count")
    assert short < task < working


def test_task_memory_functions_defined() -> None:
    """All sidebar functions exist."""
    for name in (
        "renderTaskMemoryBlock",
        "saveTaskGoal",
        "deleteTaskMemoryItem",
        "resetTaskMemory",
    ):
        assert re.search(rf"^(?:async )?function {name}\(", APP_JS, re.MULTILINE), name


def test_sidebar_copy_and_routes() -> None:
    """The labels, the reset confirmation and the three routes are present."""
    block: str = _function_source("renderTaskMemoryBlock")
    for label in ("'Цель'", "'Уточнено'", "'Ограничения и термины'", "'Пока пусто'"):
        assert label in block
    assert "Цель и уточнения появятся после первого ответа в этом чате" in block
    assert "'task-reset'" in block
    assert "'Сбросить'" in block
    assert "task-memory/goal" in APP_JS
    assert "task-memory/items/" in APP_JS
    assert "task-memory/reset" in APP_JS
    assert "Сбросить память задачи этого чата?" in _function_source("resetTaskMemory")


def test_reset_confirms_before_request_and_item_delete_does_not() -> None:
    """Reset asks first; deleting one point is immediate."""
    reset: str = _function_source("resetTaskMemory")
    assert "confirm(" in reset
    assert reset.index("confirm(") < reset.index("apiFetch")
    assert "confirm(" not in _function_source("deleteTaskMemoryItem")


def test_item_delete_button_is_accessible() -> None:
    """The per-item button has a title, an aria-label with the text and its action."""
    items: str = _function_source("buildTaskItemList")
    assert "'×'" in items
    assert "'task-item-delete'" in items
    assert "Удалить пункт" in items
    assert "aria-label" in items
    assert "role" in items


def test_goal_edit_form_contract() -> None:
    """The goal editor is a bounded textarea that cancels on Escape and never submits on Enter."""
    form: str = _function_source("buildTaskGoalEditForm")
    assert "createElement('textarea')" in form
    assert "field.maxLength = 300" in form
    assert "Опишите цель диалога" in form
    assert "'task-goal'" in form
    assert "'Escape'" in form
    assert "'Enter'" not in form
    assert "state.editingTaskGoal" in form


def test_task_memory_text_never_reaches_html_sinks() -> None:
    """Task-memory text is rendered through textContent only."""
    names = (
        "renderTaskMemoryBlock",
        "buildTaskGoalEditForm",
        "buildTaskGoalRow",
        "buildTaskItemList",
        "saveTaskGoal",
        "deleteTaskMemoryItem",
        "resetTaskMemory",
        "applyTaskMemoryResult",
        "startTaskGoalEdit",
    )
    for name in names:
        source: str = _function_source(name)
        for banned in BANNED_HTML_SINKS:
            assert banned not in source, f"{banned} in {name}"


def test_done_frame_refreshes_sidebar_without_request() -> None:
    """The done case copies rag.task_memory into the memory state before reloading."""
    done: str = _case_done_source()
    assert "task_memory" in done
    assert "state.lastMemory.task_state" in done
    assert done.index("task_memory") < done.index("loadChatMemory(")


def test_branch_changes_reload_memory() -> None:
    """Branching and switching a branch reload the restored task memory."""
    for name in ("branchFromMessage", "switchBranch"):
        source: str = _function_source(name)
        assert "loadChatMemory(" in source
        assert "state.editingTaskGoal = null" in source


def test_render_memory_panel_renders_task_block() -> None:
    """renderMemoryPanel passes the task_state to the block."""
    assert "renderTaskMemoryBlock(data.task_state)" in _function_source("renderMemoryPanel")


def test_snapshot_block_contract() -> None:
    """The per-message block is collapsed, read-only and marks new items as text."""
    block: str = _function_source("buildTaskMemoryBlock")
    items: str = _function_source("buildTaskMemoryItemList")
    assert "rag-task-memory" in block
    assert "новое" in block + items
    assert "память не обновлена" in block
    assert "RAG_CHIP_NEUTRAL" in block
    assert ".open" not in block and "setAttribute('open'" not in block
    for name in ("buildTaskMemoryBlock", "buildTaskMemoryItemList"):
        source: str = _function_source(name)
        for banned in BANNED_HTML_SINKS:
            assert banned not in source, f"{banned} in {name}"
        assert "confirm(" not in source and "apiFetch" not in source


def test_snapshot_block_wired_after_details() -> None:
    """renderMessages places the snapshot after the search details."""
    render: str = _function_source("renderMessages")
    assert "buildTaskMemoryBlock(rag.task_memory)" in render
    assert render.index("buildRagDetailsBlock(rag)") < render.index("buildTaskMemoryBlock(")


def test_condensed_query_label_and_history_stage() -> None:
    """The details block labels a condensed query and shows the history stage."""
    details: str = _function_source("buildRagDetailsBlock")
    assert "'Уточнён:'" in details
    assert "'Переписан:'" in details
    assert "search.condensed" in details
    assert "'история'" in details
    assert "history_pairs" in details
    skip_names = re.search(r"const RAG_SKIP_STAGE_NAMES = \{.*?\};", APP_JS, re.DOTALL)
    assert skip_names is not None
    assert "history: 'Уточнение запроса'" in skip_names.group(0)
    skip_text: str = _function_source("ragSkipReasonText")
    assert "ответ модели не подошёл, искали по исходному вопросу" in skip_text
