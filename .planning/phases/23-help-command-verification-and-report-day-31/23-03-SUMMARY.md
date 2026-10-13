---
phase: 23-help-command-verification-and-report-day-31
plan: 03
subsystem: ui
tags: [frontend, vanilla-js, help-command, settings, rag]
requires:
  - phase: 23-01
    provides: GET/PUT /api/v1/help/config
provides:
  - Settings section "Справка по проекту (/help)" with autosave
  - "Документация проекта" badge on the marked knowledge base card
  - Answer label, warning and nothing-found lines rendered from the stored payload
  - Transient help_notice rendering that never enters state.messages
affects: [23-04, 23-07]
tech-stack:
  added: []
  patterns: [autosave-on-change with rollback, textContent-only rendering]
key-files:
  created: [tests/test_help_ui.py]
  modified: [ui/static/index.html, ui/static/app.js, .planning/phases/23-help-command-verification-and-report-day-31/23-UI-SPEC.md]
key-decisions:
  - "UI-SPEC rule 2 amended: label, sources, quotes and warning survive a reload; tool-call cards are live-only, as for every chat. No restore of cards was built."
  - "The knowledge base list load also fetches the help config silently so the badge is current whenever the list renders."
requirements-completed: [HELP-01, HELP-03, HELP-04, HELP-05, HELP-07]
duration: 25min
completed: 2026-10-13
---

# Phase 23 Plan 03: /help browser UI Summary

The browser side of `/help` is built: a Settings section with autosaving controls, the knowledge base badge, the answer label with warning and nothing-found lines, and a transient service notice.

## Commits
- fb9eec2: Settings section and knowledge base badge
- 2e82b0a: answer label, warnings, notice, guard tests, UI-SPEC amendment

## Selectors for Plan 23-07
Settings: `#help-section`, `#help-kb`, `#help-kb-clear`, `#help-top-k`, `#help-candidate-k`, `#help-threshold`, `#help-threshold-note`, `#help-threshold-reset`, `#help-stage-lexical`, `#help-stage-llm`, `#help-stage-hybrid`, `#help-stage-rewrite`, `#help-mcp-list` (rows `#help-mcp-{id}`), `#help-mcp-hint`, `#help-tools-memory`, `#help-tools-tasks`, `#help-tools-scheduler`, `#help-save-status`, `#help-kb-hint`.
Chat: notice element has CSS class `help-notice` (role="status"); the label is a chip with text "Справка по проекту · <имя>" as the first child of the assistant bubble.

## Deviations from Plan
None to the product scope. Notes:
- The chat popover has no "Top-K" label (only "K"), so the section uses "Top-K" from the UI-SPEC; the four stage labels are identical to the popover and guarded by a test.
- `buildRagThresholdLine` skips payloads that carry `help`, so the nothing-found note replaces the below-threshold line (per plan).

## Verification (actually run)
- Plan task 1 verify (id/string presence check, `pytest tests/test_static_js_syntax.py tests/test_port_literals.py`): ok, 8 passed.
- Plan task 2 verify (`pytest tests/test_help_ui.py tests/test_static_js_syntax.py tests/test_kb_folder_ui.py tests/test_limits_ui.py tests/test_rag_cite_static.py`): 99 passed; UI-SPEC check script printed ok.
- Full suite `pytest tests/`: 3041 passed, 13 skipped, 0 failed (a first run overlapped with a second pytest process on the shared test DB and showed one spurious setup error in `tests/test_titles.py`; that file passes alone and the clean rerun was fully green).
- Not verified: actual browser behaviour (no browser run in this plan; Plan 23-07 covers it), and the server frame/payload (Plan 23-04 emits them).

## Known Stubs
None.

## Self-Check: PASSED
