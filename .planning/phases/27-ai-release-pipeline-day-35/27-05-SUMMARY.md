---
phase: 27-ai-release-pipeline-day-35
plan: 05
subsystem: ui
tags: [vanilla-js, websocket, release-card, dompurify]
requires:
  - phase: 27-02
    provides: release_plan / release_progress / release_result frame contract (agent/release_command.py)
provides:
  - Release plan card in chat (editable version/title, validation, confirm)
  - In-place progress/result updates and stale-card handling
affects: [27-07]
tech-stack:
  added: []
  patterns: [createElement + textContent only; changelog via renderMarkdown (DOMPurify)]
key-files:
  created: [tests/test_release_ui.py]
  modified: [ui/static/app.js]
key-decisions:
  - "Plan card is inserted by appendReleasePlanCard with the appendToolCallCard insert-before-streaming logic"
requirements-completed: [D-02, D-03, D-05, D-07, D-09, D-13, D-14]
duration: 25min
completed: 2026-10-17
---

# Phase 27 Plan 05: Release plan card Summary

Release plan card in `app.js` rendered per 27-UI-SPEC: editable validated version/title, confirm via the `release_confirm` frame, and in-place step/result updates with stale-card handling.

## Tasks

1. Source-guard tests (12 tests) - commit e9b5ff1 (RED: 11 failed, 1 passed balance check)
2. Plan card, version validation, confirm, `release_plan` case - commit da7ce8d
3. `updateReleaseStep`, `applyReleaseResult`, `markReleaseCardsStale`, `release_progress`/`release_result` cases - 6a8e047

## Verification (observed)

- `pytest tests/test_release_ui.py tests/test_help_ui.py tests/test_static_js_syntax.py -q -n0`: 34 passed
- `node --check ui/static/app.js`: no errors
- `grep -c "case 'release_"` = 3; `function markReleaseCardsStale` count = 1
- Not verified: visual/browser behaviour (planned in 27-07 e2e).

## Deviations from Plan

1. [Rule 3 - Blocking] Worktree base was 03c151a, not the expected 58bfabe; reset --hard to 58bfabe per the startup check (clean tree).
2. Test `test_plan_switch_case` asserts `appendReleasePlanCard` (the helper that wraps `buildReleasePlanCard`) instead of `buildReleasePlanCard` directly in the case body.
3. Added small helpers beyond the plan list: `setReleaseStepRow`, `setReleaseHeader`, `releaseCardEntry`, `findReleaseStepRow`, `appendReleasePlanCard`. A typed `/release confirm` is handled by `updateReleaseStep` switching the latest card to the busy state on the first progress frame.

## Known Stubs

None.

## Self-Check: PASSED
