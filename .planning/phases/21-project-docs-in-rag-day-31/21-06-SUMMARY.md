---
phase: 21-project-docs-in-rag-day-31
plan: 06
subsystem: frontend-knowledge-base
tags: [kb, ui, add-files, modal, vanilla-js]
requires: ["21-04", "21-05"]
provides:
  - "#kb-add-modal and openKbAddModal/submitKbAdd in ui/static/app.js"
  - "kbIsAdding, kbAddFailureText, kbErrorLine, state.kbAddSummary"
affects: [21-07]
tech-stack:
  added: []
  patterns: ["adding state read as status ready + phase", "summary kept in a Map outside the kb object"]
key-files:
  created: [tests/test_kb_add_ui.py]
  modified: [ui/static/index.html, ui/static/app.js, tests/test_modal_close_policy.py]
decisions:
  - "Running addition = kb.status === 'ready' && kb.phase; failed addition = ready with kb.error and no phase"
  - "Add summary stored in state.kbAddSummary (Map by kb id) because upsertKb replaces the kb object"
  - "Add modal closes only via its x button (no Escape or backdrop handler), matching the close-policy test"
metrics:
  tasks: 2
  completed: 2026-10-13
---

# Phase 21 Plan 06: Add-files flow in the browser Summary

A ready knowledge base card now has a neutral "Добавить файлы" button opening a compact modal (reusing the pending-list controller) that posts files under their source names to `POST /api/v1/kb/{id}/files`; the card shows live "Добавление: N из M" progress while keeping the `готово` chip and enabled search, then the added/replaced/unchanged summary or a non-destructive failure line.

## Commits
- 2d63970: add-files modal markup and submit flow
- db8402e: card states, frame handling, close-policy extension, `tests/test_kb_add_ui.py` (44 cases)

## Verification (observed, DB_PATH=test_plan06.db)
- `pytest tests/test_kb_add_ui.py tests/test_kb_folder_ui.py tests/test_static_js_syntax.py tests/test_modal_close_policy.py`: 99 passed
- `pytest tests/`: 2746 passed, 11 skipped
- Not verified: real browser behaviour of the flow; planned for the Plan 21-07 Playwright run.

## Deviations from Plan

None. `state.kbAddSummary` is declared in Task 1's commit (needed by `submitKbAdd`) rather than Task 2.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED
- ui/static/index.html, ui/static/app.js, tests/test_kb_add_ui.py exist; commits 2d63970 and db8402e exist.
