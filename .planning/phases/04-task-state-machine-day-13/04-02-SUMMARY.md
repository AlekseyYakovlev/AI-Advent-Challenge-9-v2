---
phase: 04-task-state-machine-day-13
plan: 02
subsystem: task-state-machine
tags: [tasks, tool-dispatcher, ownership-guard, sidebar-ui, cascade-delete]
dependency_graph:
  requires: [Task/TaskTransition tables (04-01), agent/tools.py dispatcher (Phase 2), agent/tasks.py CRUD layer (04-01), Задачи sidebar panel (04-01)]
  provides: [transition_task tool + tool-level ownership guard, agent/tasks.py::_get_owned_task/transition_task, per-task history timeline in the sidebar, Task/TaskTransition cascade-delete test coverage]
  affects: [agent/schemas.py, agent/tasks.py, agent/tools.py, ui/static/app.js, tests/test_tasks.py, tests/test_cascade_delete.py]
tech_stack:
  added: []
  patterns: [narrower-Pydantic-enum-as-enforcement-mechanism (LlmTaskState excludes cancelled), tool-level TaskNotFoundError-to-dict conversion, IDOR-safe task-id ownership guard mirroring _get_chat_or_404, createElement/textContent-only timeline rendering for LLM-authored text]
key_files:
  created: []
  modified:
    - agent/schemas.py
    - agent/tasks.py
    - agent/tools.py
    - ui/static/app.js
    - tests/test_tasks.py
    - tests/test_cascade_delete.py
decisions: []
metrics:
  duration_minutes: 25
  completed: 2026-09-20
---

# Phase 4 Plan 2: Task transitions and the state-change timeline Summary

Adds the `transition_task` LLM tool so an existing task can move `planning → execution → validation → done`, with every accepted move appending an immutable `TaskTransition` row and every `task_id` checked against the calling chat/user before any write — plus the Задачи panel now renders that history as a chronological, Russian-labeled timeline per task, and cascade-delete coverage confirms deleting a chat leaves zero orphaned `Task`/`TaskTransition` rows.

## What Was Built

- **`agent/schemas.py`**: `LlmTaskState` (4 values — `planning`/`execution`/`validation`/`done`, deliberately omitting `cancelled` per D-07) and `TransitionTaskArgs` (`task_id: int = Field(gt=0)`, `new_state: LlmTaskState`, `note: str` defaulting to `""`). The narrower enum is the enforcement mechanism itself: `build_tool_schemas()` publishes its four values as the JSON-Schema `enum`, so Pydantic rejects `"cancelled"` before the handler ever runs.
- **`agent/tasks.py`**: `TaskNotFoundError` (plain `Exception`, no FastAPI import — `agent/main.py` owns HTTP translation), `_get_owned_task` (loads a task and raises `TaskNotFoundError` unless `task.chat_id == chat_id and task.user_id == user_id`, logging `task_access_denied` on denial — this is the IDOR mitigation the RESEARCH doc's Pitfall 2 flagged, since `agent/tools.py::_SCOPE_KEYS` only guards `chat_id`/`user_id` keys and does nothing for a legitimate-looking `task_id`), and `transition_task` (mutates `task.state`, bumps `task.updated_at`, appends one `TaskTransition` row capturing `from_state`/`to_state`/`note`, all inside one commit — with zero transition-graph legality checks, per the plan's explicit scope boundary; that hardening is Phase 6/TRANS-01's job).
- **`agent/tools.py`**: registers `transition_task` via the existing `@register_tool` decorator, additions-only — the registry/dispatcher machinery (`register_tool`, `build_tool_schemas`, `dispatch_tool_calls`, `_SCOPE_KEYS`) is untouched. The handler wraps `tasks.transition_task` in `try/except tasks.TaskNotFoundError` and converts the exception into `{"status": "error", "error": "task {id} not found in this chat"}` — the same message regardless of whether the task doesn't exist or belongs to someone else, mirroring `_get_chat_or_404`'s 404-never-403 policy so the error never becomes an ownership oracle.
- **`ui/static/app.js`**: `formatTaskTimestamp` (renders `ru-RU` locale timestamps, e.g. `20.09.2026, 14:32`) and `renderTaskHistory` (one line per transition via `document.createElement` + `textContent` only — never `innerHTML`, since `note` is LLM-authored — formatted as `{fromLabel} → {toLabel} · {timestamp}` with the creation row's null `from_state` rendered as `создана`, and any non-empty `note` on its own muted line below). `renderTaskPanel` now appends a `mt-1 space-y-0.5` history container to each task card, after the goal line.
- **`tests/test_tasks.py`**: nine `test_transition_*` functions covering state moves + history append, optional `note`, `updated_at` bump, the full `planning → execution → validation → done` lifecycle (asserting 4 total `TaskTransition` rows), the `cancelled` and unknown-state rejections, both IDOR paths (cross-chat and cross-user, each asserting unchanged state AND unchanged transition-row count), and an unknown `task_id`. Added a module-level `_seed_chat` helper (wrapping the existing `_create_chat`) and `_create_task_via_tool` helper per the plan's "factor it into a helper now" instruction.
- **`tests/test_cascade_delete.py`**: `test_delete_chat_cascades_tasks_and_transitions` — creates a chat, a task, and two transition rows directly via `async_session_factory`, deletes the chat via the REST endpoint, and asserts both the task and every transition row are gone.

## Deviations from Plan

None — plan executed exactly as written. Every task's `<action>` and `<acceptance_criteria>` were followed verbatim; no auto-fixes, no architectural questions, no scope changes.

## Verification Evidence

- `python -m pytest tests/test_tasks.py -x` (before implementation) → 1 failed (`test_transition_task_moves_state_and_appends_history`), 5 passed — confirmed RED phase; the new test module correctly failed because `transition_task`/`TransitionTaskArgs`/`LlmTaskState` did not exist yet.
- `python -m pytest tests/ -q --ignore=tests/test_tasks.py` (RED-phase checkpoint) → 190 passed, zero regressions.
- `python -m pytest tests/test_tasks.py tests/test_task_api.py tests/test_cascade_delete.py -v` (after implementation) → 21/21 passed, including all nine `test_transition_*` cases.
- `python -m pytest tests/ -q` (final, after all three tasks) → 204/204 passed.
- `python -c "...build_tool_schemas()..."` for `transition_task` → `new_state` enum is exactly `["planning", "execution", "validation", "done"]`; `cancelled` absent.
- `grep -c "class LlmTaskState" agent/schemas.py` → 1; `sed -n '/class LlmTaskState/,/^class /p' agent/schemas.py | grep -c cancelled` → 0.
- `grep -n "task.chat_id != chat_id" agent/tasks.py` → match at line 81 (ownership guard present).
- `grep -n "task_access_denied" agent/tasks.py` → match at line 82 (structlog warning on denial).
- `grep -c "TaskNotFoundError" agent/tools.py` → 1.
- `grep -cE "^\s*except\s*:" agent/tasks.py agent/tools.py` → 0/0 (no bare except).
- `git diff agent/tools.py` → only an import-block reformat (before the `ToolHandler` type alias) and a pure addition at the end; zero lines removed inside `register_tool`/`build_tool_schemas`/`dispatch_tool_calls`.
- `node --check ui/static/app.js` → exit 0 (syntax valid; Node was available in this environment, no Python bracket-balance fallback needed).
- `grep -c "function renderTaskHistory" ui/static/app.js` → 1; `grep -c "function formatTaskTimestamp" ui/static/app.js` → 1; `grep -n "ru-RU"` and `grep -n "создана"` and `grep -n "→"` all matched.
- `sed -n '/function renderTaskHistory/,/^}/p' ui/static/app.js | grep -c innerHTML` → 0.
- `grep -cE "<details>|tab-button|aria-selected" ui/static/app.js` → 0 (no tab-switching machinery introduced, per RESEARCH Pitfall 6).
- Manual browser smoke test (`python run.py`, click through a task's timeline rendering in the actual UI) was **not run** this session — verification relied on the automated syntax/grep checks above plus the full backend test suite; visual confirmation is deferred to 04-04 per the same pattern 04-01 used.

## Known Stubs

None — `transition_task` is wired end to end from LLM tool call through to the persisted `Task`/`TaskTransition` rows, and the sidebar timeline renders real `history` data returned by the existing `GET /api/v1/chats/{chat_id}/tasks` endpoint (built in 04-01). No hardcoded/mock values were introduced.

## Self-Check: PASSED

- FOUND: `agent/schemas.py` (`LlmTaskState`, `TransitionTaskArgs`)
- FOUND: `agent/tasks.py` (`TaskNotFoundError`, `_get_owned_task`, `transition_task`)
- FOUND: `agent/tools.py` (`transition_task` registration, `_transition_task` handler)
- FOUND: `ui/static/app.js` (`formatTaskTimestamp`, `renderTaskHistory`)
- FOUND: `tests/test_tasks.py` (nine `test_transition_*` functions)
- FOUND: `tests/test_cascade_delete.py` (`test_delete_chat_cascades_tasks_and_transitions`)
- FOUND commit `353ad89` (test: RED phase — failing transition/cascade tests)
- FOUND commit `3925ecf` (feat: GREEN phase — transition_task implementation)
- FOUND commit `f53290f` (feat: Task 3 — history timeline rendering)
