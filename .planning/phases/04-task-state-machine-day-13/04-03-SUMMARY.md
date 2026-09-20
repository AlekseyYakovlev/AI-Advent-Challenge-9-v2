---
phase: 04-task-state-machine-day-13
plan: 03
subsystem: task-state-machine
tags: [tasks, tool-dispatcher, rest-api, chat-locks, sidebar-ui]
dependency_graph:
  requires: [Task/TaskTransition tables (04-01), transition_task tool + _get_owned_task (04-02), agent/state.py::chat_locks (Phase 2), Задачи sidebar panel + history timeline (04-01/04-02)]
  provides: [pause_task/resume_task LLM tools, agent/tasks.py::set_paused/cancel_task, _get_task_or_404, POST /api/v1/tasks/{id}/pause|resume|cancel, per-task Пауза/Продолжить/Отменить controls]
  affects: [agent/schemas.py, agent/tasks.py, agent/tools.py, agent/main.py, ui/static/app.js, tests/test_tasks.py, tests/test_task_api.py]
tech_stack:
  added: []
  patterns: [orthogonal-boolean-flag-not-history-event (is_paused writes no TaskTransition row), manual-REST-bypasses-dispatcher-but-not-chat_locks (D-12 + RESEARCH Pattern 2), 404-never-403 IDOR guard mirrored at both tool and REST layers, narrower-tool-registry-as-enforcement (no cancel_task registered)]
key_files:
  created: []
  modified:
    - agent/schemas.py
    - agent/tasks.py
    - agent/tools.py
    - agent/main.py
    - ui/static/app.js
    - tests/test_tasks.py
    - tests/test_task_api.py
decisions: []
metrics:
  duration_minutes: 30
  completed: 2026-09-20
---

# Phase 4 Plan 3: Pause, resume, and manual cancel controls Summary

Gives the task lifecycle a pause button on both control paths: the LLM can pause/resume a task it is working on via `pause_task`/`resume_task` tool calls (never writing a `TaskTransition` row, since `is_paused` is orthogonal to state per D-04), and the user gets three lock-protected REST endpoints — `POST /api/v1/tasks/{id}/pause|resume|cancel` — that bypass the tool dispatcher entirely but still serialize under `agent/state.py::chat_locks[task.chat_id]`, plus matching Пауза/Продолжить/Отменить buttons in the Задачи panel. `cancelled` remains reachable only through the manual REST path and always appends one `TaskTransition` row.

## What Was Built

- **`agent/schemas.py`**: `PauseTaskArgs`/`ResumeTaskArgs`, each a single `task_id: int = Field(gt=0)` field — no other fields, so `delegate_to`/`new_state` can never be smuggled in via these tools (D-09).
- **`agent/tasks.py`**: `set_paused(session, user_id, chat_id, task_id, is_paused)` flips only `Task.is_paused` and bumps `updated_at` — it never touches `Task.state` and writes no `TaskTransition` row (D-04, RESEARCH Pattern 4). `cancel_task(session, user_id, chat_id, task_id)` sets `Task.state = TaskState.CANCELLED`, bumps `updated_at`, and appends one `TaskTransition(from_state=<previous>, to_state=CANCELLED, note="")` in the same commit — with deliberately no legality check on the previous state (Phase 6 owns TRANS-01). Both route ownership through the existing `_get_owned_task` guard from 04-02.
- **`agent/tools.py`**: registers `pause_task` and `resume_task` via the existing `@register_tool` decorator, additions-only — the registry/dispatcher machinery (`register_tool`, `build_tool_schemas`, `dispatch_tool_calls`, `_SCOPE_KEYS`) is untouched (confirmed via `git diff` against the pre-plan base: zero removed lines in `agent/tools.py`). No `cancel_task` tool is registered anywhere — cancellation is manual-only (D-07/D-12), enforced by the registry itself rather than by a runtime check.
- **`agent/main.py`**: imports `chat_locks` alongside the existing `CORS_ORIGINS`/`cleanup_chat_caches` import. Adds `_get_task_or_404(session, task_id, user_id)` next to `_get_chat_or_404`, following the identical 404-never-403 idiom (including the same "never 403, to avoid an IDOR oracle" docstring phrasing). Adds `_task_response_with_history(session, row)` so the three new endpoints and the existing `GET /api/v1/chats/{chat_id}/tasks` share one response-mapping path. Adds three routes — `POST /api/v1/tasks/{task_id}/pause|resume|cancel` — placed immediately after `get_chat_tasks`; each resolves ownership via `_get_task_or_404`, lazily initializes `chat_locks[task.chat_id]` if absent, and holds that lock for the entire write via `async with chat_locks[task.chat_id]:` before calling into `agent/tasks.py`. This lock is mandatory even though D-12's dispatcher-bypass applies: `Task` is chat-scoped (unlike the lock-free, user-scoped `PUT /api/v1/profile` precedent), so an unlocked manual write could interleave with an in-flight `transition_task` inside `agent/ws.py`'s per-chat lock and corrupt history ordering.
- **`ui/static/app.js`**: `pauseTask`/`resumeTask`/`cancelTask` async functions, each POSTing to the corresponding endpoint via `apiFetch`, then re-rendering via `loadChatTasks(state.currentChatId)`, then showing the exact UI-SPEC Russian success/error toast. `cancelTask` is gated by `confirm('Отменить эту задачу? Это действие нельзя отменить.')`; pause/resume fire immediately with no dialog. `renderTaskPanel()` now appends an action row per non-terminal task card (suppressed entirely when `task.state === 'done' || 'cancelled'`): a neutral slate Пауза button when not paused, an indigo-accented Продолжить button when paused, and always a red Отменить button — all built via `createElement`/`textContent`, with listeners attached at creation time (no static `#btn-*` ids, matching the dynamic-list precedent already used for tasks).

## Deviations from Plan

None — plan executed exactly as written. Every task's `<action>` and `<acceptance_criteria>` were followed verbatim; no auto-fixes, no architectural questions, no scope changes.

## Verification Evidence

- `python -m pytest tests/test_tasks.py tests/test_task_api.py -x` (before implementation) → 1 failed (`test_pause_task_tool_sets_flag_without_changing_state`), 13 passed — confirmed RED phase. Several IDOR/negative-path tests (e.g. `test_pause_task_other_users_task_is_rejected`, `test_cancel_is_not_an_llm_tool`, `test_pause_endpoint_other_users_task_returns_404`) passed even pre-implementation because an unregistered tool or a nonexistent route already produces the expected error/404 by default — this is consistent with how similar negative-path tests behaved in 04-01/04-02's RED phases.
- `python -m pytest tests/ -q --ignore=tests/test_tasks.py --ignore=tests/test_task_api.py` (RED-phase checkpoint) → 186 passed, zero regressions.
- `python -m pytest tests/test_tasks.py tests/test_task_api.py -v` (after implementation) → 34/34 passed, all new pause/resume/cancel tests GREEN.
- `python -m pytest tests/ -q` (final, after all three tasks) → 220/220 passed.
- `python -c "from agent.tools import TOOL_REGISTRY; print(sorted(TOOL_REGISTRY))"` → `['create_task', 'pause_task', 'resume_task', 'save_long_term_memory', 'save_working_memory', 'transition_task']` — `cancel_task` absent, as required.
- `grep -cE "def test_(pause|resume|cancel)_" tests/test_tasks.py` → 7 (≥5 required). `grep -cE "def test_(pause|resume|cancel)" tests/test_task_api.py` → 7 (≥7 required). `grep -c "test_cancel_is_not_an_llm_tool" tests/test_tasks.py` → 1.
- `grep -c "chat_locks\[task.chat_id\]" agent/main.py` → 6 (the lazy-init check plus the `async with` in each of the three endpoints).
- `grep -n "async def _get_task_or_404" agent/main.py` → match at line 102; `sed -n '/async def _get_task_or_404/,/return task/p' agent/main.py | grep -c "HTTP_404_NOT_FOUND"` → 1 (only the docstring prose mentions "403", matching the exact precedent phrasing already used in `_get_chat_or_404`).
- `sed -n '/async def set_paused/,/return task/p' agent/tasks.py | grep -c "TaskTransition"` → 0 (pause writes no history row). `sed -n '/async def cancel_task/,/return task/p' agent/tasks.py | grep -c "TaskTransition"` → 1.
- `grep -cE "^\s*except\s*:" agent/tasks.py agent/tools.py agent/main.py` → 0/0/0 (no bare except anywhere touched).
- `git diff 1c57748..HEAD -- agent/tools.py` shows zero removed lines — the frozen registry/dispatcher block (`register_tool`, `build_tool_schemas`, `dispatch_tool_calls`, `_SCOPE_KEYS`) is untouched; only additive tool registrations were appended.
- `node --check ui/static/app.js` → exit 0.
- `grep -cE "async function (pauseTask|resumeTask|cancelTask)" ui/static/app.js` → 3. `grep -c "/api/v1/tasks/" ui/static/app.js` → 3.
- `grep -n "Отменить эту задачу? Это действие нельзя отменить." ui/static/app.js` → match; `sed -n '/async function cancelTask/,/^}/p' ui/static/app.js | grep -c "confirm("` → 1; the same range check for `pauseTask`/`resumeTask` → 0/0 (non-destructive actions fire immediately, no dialog).
- All six exact UI-SPEC strings present: `grep -c "Задача поставлена на паузу\|Задача возобновлена\|Задача отменена" ui/static/app.js` → 3; `grep -c "Не удалось поставить задачу на паузу\|Не удалось возобновить задачу\|Не удалось отменить задачу" ui/static/app.js` → 3.
- `grep -c "bg-indigo-600" ui/static/app.js` → 2 (Продолжить plus the pre-existing settings-modal usage); `grep -c "bg-red-700" ui/static/app.js` → 2 (Отменить plus the pre-existing destructive-delete-message usage).
- `sed -n '/function renderTaskPanel/,/^}/p' ui/static/app.js | grep -c "innerHTML"` → 0. `grep -cE "new_state|transition_task" ui/static/app.js` → 0 (the UI never drives arbitrary lifecycle transitions).
- Manual browser smoke test (`python run.py`, click through Пауза/Продолжить/Отменить in the actual UI) was **not run** this session — verification relied on the automated pytest/grep/sed/node checks above plus the full backend test suite; visual confirmation is deferred to 04-04, matching the pattern used by 04-01 and 04-02.

## Known Stubs

None — `pause_task`/`resume_task`/`cancel_task` are wired end to end from both the LLM tool-call path and the manual REST path through to the persisted `Task` rows, and the Задачи panel's action buttons call the real endpoints and re-render from the live server response. No hardcoded/mock values were introduced.

## Self-Check: PASSED

- FOUND: `agent/schemas.py` (`PauseTaskArgs`, `ResumeTaskArgs`)
- FOUND: `agent/tasks.py` (`set_paused`, `cancel_task`)
- FOUND: `agent/tools.py` (`pause_task`/`resume_task` registrations, `_pause_task`/`_resume_task` handlers; no `cancel_task`)
- FOUND: `agent/main.py` (`_get_task_or_404`, `_task_response_with_history`, three `/api/v1/tasks/{task_id}/...` routes)
- FOUND: `ui/static/app.js` (`pauseTask`, `resumeTask`, `cancelTask`, action row in `renderTaskPanel`)
- FOUND: `tests/test_tasks.py` (seven `test_pause_*`/`test_resume_*`/`test_cancel_*` tool-path tests)
- FOUND: `tests/test_task_api.py` (nine `test_pause_*`/`test_resume_*`/`test_cancel_*` REST-path tests)
- FOUND commit `34bb6d5` (test: RED phase — failing pause/resume/cancel tests)
- FOUND commit `0322530` (feat: GREEN phase — tools + locked REST endpoints)
- FOUND commit `31a5153` (feat: Task 3 — Задачи panel action buttons)
