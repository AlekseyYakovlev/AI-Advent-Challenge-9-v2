---
phase: 11-edit-and-delete-long-term-memory-entries-via-ui-day-21
plan: 02
subsystem: ui
tags: [vanilla-js, memory-panel, inline-edit]
requires:
  - phase: 11-01
    provides: PUT/DELETE /api/v1/memory/long-term/{id} routes (coded against the contract)
provides:
  - Editable long-term memory list in the sidebar (Редактировать / Удалить, inline form)
  - Source guard test tests/test_memory_panel_ui.py
affects: [11-04]
tech-stack:
  patterns: [draft kept in state with focus/caret restore across re-render, DOM built via textContent/.value only]
key-files:
  created: [tests/test_memory_panel_ui.py]
  modified: [ui/static/app.js]
requirements-completed: [MEMUI-01, MEMUI-02, MEMUI-05]
duration: 25min
completed: 2026-10-03
---

# Phase 11 Plan 02: Long-term memory edit/delete UI Summary

Each long-term memory entry in the sidebar now has "Редактировать" and "Удалить" buttons, an inline key/value edit form seeded with the full value, a confirm()-guarded delete, and a draft that survives panel re-renders; the working list stays read-only.

## Commits
- ba5a45e feat: edit state and save/delete flows (7 functions, 3 state fields)
- e0f8ed8 test: failing source guard (RED: 3 failed, 5 passed)
- 366ae48 feat: row buttons, inline form, focus capture/restore, editable only for the long-term container (GREEN)

## Verification (actually run)
- `pytest tests/test_memory_panel_ui.py tests/test_static_js_syntax.py tests/test_modal_close_policy.py -q`: 29 passed (guard file: 8 passed).
- `pytest tests/ -q`: 1320 passed, 1 skipped, 1 failed (`tests/test_supervisor.py::test_agent_restarts_within_5_seconds`, timeout).
- `ui/static/index.html` unchanged.
- Not verified: behavior in a real browser (planned for 11-04); the 11-01 routes were not available in this worktree, so the client was coded against the stated contract only.

## Deviations from Plan
None in code. Line endings: the repo warns LF->CRLF on app.js; content unchanged otherwise.

## Deferred Issues
- `test_supervisor.py::test_agent_restarts_within_5_seconds` fails with a timeout when run in this worktree (re-run alone: still fails). It exercises subprocess/port 8000/8001 handling, is unrelated to the files changed here, and was not investigated (out of scope; possibly environmental because the user's app owns those ports).

## Known Stubs
None.

## Self-Check: PASSED
Files and commits ba5a45e, e0f8ed8, 366ae48 exist.
