---
phase: 25-support-assistant-day-33
plan: 08
subsystem: ui
tags: [vanilla-js, settings, support, rag]
requires:
  - phase: 25-04
    provides: "rag_sources.support payload and help_notice command key"
  - phase: 25-06
    provides: "GET/PUT /api/v1/support/config"
provides:
  - "Settings section «Поддержка (/support)» with autosave"
  - "Support label, warnings and notice chip in chat"
affects: []
tech-stack:
  added: []
  patterns: ["builders parameterised by command title instead of copied"]
key-files:
  created: [tests/test_support_ui.py]
  modified: [ui/static/index.html, ui/static/app.js]
key-decisions:
  - "Existing help builders take optional label/info/title arguments; no duplicates"
requirements-completed: [SUP-03, SUP-11]
duration: 15min
completed: 2026-10-17
---

# Phase 25 Plan 08: /support UI Summary

Settings section for /support (cloned from /help without the base select), plus label, warnings and notice chip generalised to the `support` payload.

## Tasks
1. Markup and settings logic: commit 591b8f9
2. Label/notes/notice generalisation and tests: commit 3c9afda

## Verification (observed)
- `pytest tests/test_support_ui.py tests/test_help_ui.py -q -n0`: 35 passed.
- Section order help < support < llm-providers confirmed by the plan's python check.
- `tests/test_help_ui.py` unchanged. Live browser check not run (source-guard tests only).

## Deviations from Plan
- Plan acceptance said `grep -c 'id="support-'` is at least 20; the UI-SPEC id list has 18 ids, all present, so the count is 18. Not a functional gap.
- `renderSupportMcpList` also lists `mcp_missing` names in yellow when the support server exists but saved ids point to missing servers.

## Known Stubs
None.

## Self-Check: PASSED
