---
phase: 21-project-docs-in-rag-day-31
plan: 05
subsystem: frontend-knowledge-base
tags: [kb, ui, folder-upload, pending-list, vanilla-js]
requires: ["21-01"]
provides:
  - "createKbPending(ids, options), bindKbPending(pending), loadKbLimits(), kbSourceName(file) in ui/static/app.js"
  - "state.kbLimits, state.kbPending"
  - "folder input #kb-folder and pending-list markup in #kb-create-modal"
affects: [21-06, 21-07]
tech-stack:
  added: []
  patterns: ["one reusable pending-list controller per modal", "limits sourced only from GET /api/v1/kb/limits", "FormData.append third argument carries relative path"]
key-files:
  created: [tests/test_kb_folder_ui.py]
  modified: [ui/static/index.html, ui/static/app.js]
decisions:
  - "Limits and extension filter come only from /api/v1/kb/limits; submit is disabled until loaded, no fallback numbers"
  - "One controller factory serves create and add modals"
  - "Controller exposes its element ids as pending.ids so bindKbPending(pending) needs one argument"
metrics:
  tasks: 2
  completed: 2026-10-13
---

# Phase 21 Plan 05: Folder upload and pending list in the create form Summary

The "new knowledge base" form now accepts a whole folder, accumulates files and folders into one deduplicated pending list shown with relative paths, reports taken/skipped counts, shows server-sourced limits, and uploads every file under its source name.

## Commits
- 340a97e: markup, `createKbPending` controller, upload under source names, full-path result cards, empty-state copy
- e7f7fed: `tests/test_kb_folder_ui.py` source guard (31 cases)

## Verification (observed, DB_PATH=test_plan05.db)
- `pytest tests/test_kb_folder_ui.py tests/test_static_js_syntax.py tests/test_modal_close_policy.py tests/test_rag_search_static.py tests/test_rag_static.py tests/test_limits_ui.py`: 150 passed
- `pytest tests/ -k "ui or static or modal"`: 361 passed
- Not verified: real browser behaviour (`webkitRelativePath` shape, third `FormData.append` argument), planned for Plan 21-07 Playwright run.

## Deviations from Plan

**1. [Rule 3 - Blocking] `bindKbPending` needs element ids**
- **Found during:** Task 1
- **Issue:** The contract `bindKbPending(pending)` takes only the controller, but the ids live in the factory closure.
- **Fix:** The controller exposes `ids`; `bindKbPending` reads them from there.
- **Files modified:** ui/static/app.js

The `kbSourceName` backslash normalisation uses `split('\\').join('/')` instead of a regex literal to stay safe with the project's JS balance scanner.

## Interpretation for the user to confirm

KBUP-03 and roadmap criterion 3 mention a "knowledge base file list"; this phase shows relative-path source names in the pending list (create form and add modal), in test-search results and in answer sources, and adds no per-base document list on the card or in the API. A per-base list would be new scope.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED
- ui/static/index.html, ui/static/app.js, tests/test_kb_folder_ui.py exist; commits 340a97e and e7f7fed exist.
