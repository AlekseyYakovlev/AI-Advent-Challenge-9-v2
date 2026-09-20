---
phase: 04-task-state-machine-day-13
plan: 01
subsystem: task-state-machine
tags: [tasks, sqlmodel, tool-dispatcher, rest-api, sidebar-ui]
dependency_graph:
  requires: [agent/tools.py dispatcher (Phase 2), agent/memory.py CRUD pattern (Phase 2), sidebar stacked-panel layout (Phase 2/3)]
  provides: [Task/TaskTransition tables, agent/tasks.py CRUD layer, create_task tool, GET /api/v1/chats/{chat_id}/tasks, #task-panel sidebar section, task_writes WS field]
  affects: [shared/models.py, agent/schemas.py, agent/tools.py, agent/main.py, agent/ws.py, ui/static/index.html, ui/static/app.js]
tech_stack:
  added: []
  patterns: [SAEnum values_callable idiom, dual user_id/chat_id FK-cascade ownership pattern, creation-writes-first-transition-row, IDOR-safe 404-never-403 ownership check, stacked-div sidebar panel (no tabs)]
key_files:
  created:
    - agent/tasks.py
    - tests/test_tasks.py
    - tests/test_task_api.py
  modified:
    - shared/models.py
    - agent/schemas.py
    - agent/tools.py
    - agent/main.py
    - agent/ws.py
    - ui/static/index.html
    - ui/static/app.js
    - tests/test_database.py
decisions: []
metrics:
  duration_minutes: 35
  completed: 2026-09-20
---

# Phase 4 Plan 1: First working vertical slice of the task state machine Summary

Adds `Task`/`TaskTransition` SQLModel tables, a `create_task` LLM tool wired through the existing (unmodified) tool dispatcher, a `GET /api/v1/chats/{chat_id}/tasks` REST endpoint with embedded oldest-first history, and a new "Задачи" sidebar panel that renders each task's title, Russian state label, and goal — end to end, from LLM tool call to visible UI card.

## What Was Built

- **`shared/models.py`**: `TaskState` enum (`planning`/`execution`/`validation`/`done`/`cancelled`), `Task` table (dual `user_id`/`chat_id` FK-cascade, `is_paused` as an orthogonal boolean flag per D-04, unused nullable `delegate_to` column per D-03, no unique constraint and no "current task" pointer per D-08), `TaskTransition` table (append-only history, `from_state` nullable). All three enum columns use the `values_callable` SAEnum idiom so the DB stores lowercase string values, not enum member names.
- **`agent/tasks.py`** (new): `create_task` (writes the `Task` row and its creation `TaskTransition` row — `from_state=NULL`, `to_state=planning` — in one commit), `list_tasks_for_chat` (flat, chat-scoped, oldest first), `list_transitions` (oldest first per D-11).
- **`agent/schemas.py`**: `CreateTaskArgs` (title/description/goal only — no `delegate_to`/`chat_id`/`user_id`/`state`, so the dispatcher's trusted scope values can never be overridden by the LLM), `TaskResponse`/`TaskTransitionResponse`, `TASK_*` length constants.
- **`agent/tools.py`**: registers `create_task` via the existing `@register_tool` decorator. The registry/dispatcher machinery (`register_tool`, `build_tool_schemas`, `dispatch_tool_calls`, `_SCOPE_KEYS`) is untouched — only import lines and a pure addition at the end changed.
- **`agent/main.py`**: `GET /api/v1/chats/{chat_id}/tasks`, gated by the existing `_get_chat_or_404` (404, never 403, so non-owners get no ownership oracle), returning each task with its full transition history via a new `_task_to_response` mapper.
- **`agent/ws.py`**: `TASK_TOOL_NAMES` constant (declares all four planned task-tool names now so later plans in this phase need no further edit here), partitions `dispatch_tool_calls` results into `memory_writes` (excluding task-tool results) and a new `task_writes` list, added to the WS `done` frame.
- **`ui/static/index.html`**: `#task-panel` — a stacked sidebar `<div>` (not a tab-switcher; this codebase has no tab machinery) positioned between `#profile-panel` and `#agent-status`, with a `#task-count` badge and `#task-list` container.
- **`ui/static/app.js`**: `loadChatTasks`/`renderTaskPanel` mirroring the existing `loadChatMemory`/`renderMemoryPanel` pattern; Russian state labels and per-state Tailwind badge colors (`TASK_STATE_LABELS`/`TASK_STATE_BADGE_CLASSES`); a `⏸` chip shown alongside (never replacing) the state badge when `is_paused`; all task-card DOM built via `createElement`/`textContent` only (never `innerHTML`) since task titles/descriptions/goals are LLM-generated; refreshed on chat-select and after every streamed turn's `done` frame.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated `test_init_db_creates_all_tables`'s exact table-set assertion**
- **Found during:** Task 2 (`python -m pytest tests/ -q` full-suite run)
- **Issue:** `tests/test_database.py::test_init_db_creates_all_tables` asserts the SQLite schema's table set with an exact equality check (`assert tables == {...}`). Adding the `Task`/`TaskTransition` tables (an intended, required outcome of this plan) broke that assertion.
- **Fix:** Added `"task"` and `"tasktransition"` to the expected set.
- **Files modified:** `tests/test_database.py`
- **Commit:** `21a9f98`

## Verification Evidence

- `python -m pytest tests/test_tasks.py tests/test_task_api.py -v` → 9/9 passed.
- `python -m pytest tests/ -q` → 194/194 passed (zero regressions; the pre-existing suite had 185 tests before this plan, plus 9 new).
- `python -c "from agent.tools import build_tool_schemas; print([s['function']['name'] for s in build_tool_schemas()])"` → `['save_working_memory', 'save_long_term_memory', 'create_task']`.
- `git diff agent/tools.py` → only import-line changes (outside the registry/dispatcher block) and a pure addition at the end; no line removed inside `register_tool`/`build_tool_schemas`/`dispatch_tool_calls`.
- `grep -c "values_callable" shared/models.py` → 4 (Settings.strategy, Task.state, TaskTransition.from_state, TaskTransition.to_state).
- `grep -c "Field(foreign_key=" shared/models.py` → 0.
- `grep -c "delegate_to" agent/schemas.py` → 1 (only inside `TaskResponse`).
- `grep -n "task_writes" agent/ws.py` → initialization, build loop, and `done`-frame key all present.
- `ui/static/index.html`: `#task-panel`/`#task-list`/`#task-count` each appear exactly once, positioned between `btn-save-profile` and `#agent-status`; zero tab-machinery matches (`tab-button`/`aria-selected`/`tab-content`).
- `ui/static/app.js`: `loadChatTasks` appears 3 times (definition + chat-select call + `done`-case call); `lastTasks` appears 3 times; exact UI-SPEC empty-state and error copy strings present; zero `innerHTML` inside `renderTaskPanel`; all five badge classes present.
- Manual browser smoke test (`python run.py`, open UI, verify empty-state panel) was **not run** this session — the plan marks it optional for this plan, formally verified in 04-04.

## Known Stubs

None — `create_task`, the read endpoint, and the sidebar panel are all wired to real data end to end; no hardcoded/mock values remain in the rendering path.

## Self-Check: PASSED

- FOUND: `agent/tasks.py`
- FOUND: `tests/test_tasks.py`
- FOUND: `tests/test_task_api.py`
- FOUND commit `9ca71f2` (test: RED phase)
- FOUND commit `21a9f98` (feat: GREEN phase, Task 2)
- FOUND commit `9087ef1` (feat: Task 3, sidebar panel)
