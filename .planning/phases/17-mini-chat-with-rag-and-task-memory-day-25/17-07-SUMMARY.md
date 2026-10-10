---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 07
subsystem: ui
tags: [vanilla-js, task-memory, rag, sidebar, static-guards]
requires:
  - phase: 17-01
    provides: task-memory data model
  - phase: 17-04
    provides: task_state in GET memory, task-memory REST routes, history_turns in rag config
provides:
  - Sidebar block "Память задачи" with edit goal, delete item, reset
  - Per-answer collapsed task-memory snapshot with "новое" markers
  - "Ходов истории" input in the search popover
  - "Детали поиска" condensed-query label, history stage, skip text
affects: [17-11]
tech-stack:
  added: []
  patterns: [textContent-only rendering, source-slice static guards]
key-files:
  created: [tests/test_task_memory_ui.py]
  modified: [ui/static/index.html, ui/static/app.js, tests/test_rag_search_static.py, tests/test_modal_close_policy.py]
key-decisions:
  - "Task-memory UI functions live after loadChatTasks, outside the memory-region slice that bans Escape/keydown handlers"
requirements-completed: [RCHAT-02, RCHAT-03]
duration: 25min
completed: 2026-10-10
---

# Phase 17 Plan 07: Task-memory UI Summary

Sidebar task-memory block with manual control, per-answer snapshot, "Ходов истории" setting and condensed-query details, all rendered through textContent.

## Tasks

1. Sidebar block (C1, C5) - commit 0dea311. `#memory-task-state` between the short-term and working rows (hidden without `task_state`), three labelled parts, goal inline edit (textarea, maxlength 300, Escape cancels, Enter does not submit), per-item "×" (no confirm), "Сбросить" behind `confirm()`. The `done` frame copies `rag.task_memory` into `state.lastMemory.task_state` and re-renders before the existing reloads; `branchFromMessage` / `switchBranch` clear the goal draft and call `loadChatMemory`. Load failure toast only when the block was visible.
2. Snapshot, setting, details (C2-C4) - commit b838715. `buildTaskMemoryBlock` (collapsed, read-only, count and "новое: K" in summary, neutral "память не обновлена" chip) placed after "Детали поиска"; `#rag-history-turns` after the threshold row and before the strict switch, `Number.isNaN` handling and clamp 0..10 (0 stays 0), `history_turns` added to the PUT key list; "Уточнён:" label when `search.condensed`, `история ×N` stage part, `history` skip name and bad_output text.

## Verification (run)

- `python -m pytest tests/test_task_memory_ui.py tests/test_rag_search_static.py tests/test_rag_static.py tests/test_rag_cite_static.py tests/test_static_js_syntax.py tests/test_memory_panel_ui.py tests/test_modal_close_policy.py -q`: 158 passed.
- Acceptance greps: no `innerHTML` in added app.js lines (0), `cdn.` count unchanged (3 = 3), `tests/test_memory_panel_ui.py` untouched.
- NOT verified: real-browser behaviour (planned for 17-11); the JS was only checked by static source guards and the existing syntax test.

## Deviations from Plan

**1. [Rule 3 - Blocking] tests/test_modal_close_policy.py updated**
- **Issue:** `test_escape_key_does_not_close_modals` rejects any `Escape` outside lines containing `closeRagSearchPopover(true)`; the UI-SPEC requires Escape to cancel the goal edit.
- **Fix:** Allowed lines containing `cancelTaskGoalEdit()` in that guard (textarea-scoped handler, not a modal closer); the handler line carries that call. All other assertions unchanged.
- **Commit:** 0dea311

**2. [Placement] Task-memory functions placed after `loadChatTasks`**, not in the memory region, because `tests/test_memory_panel_ui.py` bans `'Escape'`/`'keydown'` in that region and must pass unchanged. `renderMemoryPanel` calls `renderTaskMemoryBlock` (hoisted function).

**3. Mutation helpers:** each mutation function calls `apiFetch` itself (needed for the "confirm before apiFetch" guard) and all task buttons are disabled while any mutation is in flight (plan: the clicked button).

## Known Stubs

None.

## Notes

- `.planning/HANDOFF.json` showed as modified in the worktree before any edit of mine (line-ending/orchestrator artifact); it was not staged or committed.
- Worktree base was behind the expected base commit 71b1e83; fast-forwarded with the sanctioned startup `git reset --hard` (tree was clean).

## Self-Check: PASSED

Files exist (tests/test_task_memory_ui.py, edited index.html/app.js); commits 0dea311 and b838715 present.
