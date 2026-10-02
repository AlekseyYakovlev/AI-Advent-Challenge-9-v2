---
phase: 10-modals-close-only-via-x-button-day-21
plan: 01
subsystem: ui
tags: [frontend, modals, vanilla-js, pytest-guard]
requires: []
provides:
  - "Modals close only via x / Cancel / post-success; no backdrop-click or Escape closer"
  - "tests/test_modal_close_policy.py source guard (discovers *-modal ids from index.html)"
affects: [ui/static/app.js, ui/static/index.html]
tech-stack:
  added: []
  patterns: ["Node-free source guard test for frontend policy"]
key-files:
  created: [tests/test_modal_close_policy.py]
  modified: [ui/static/app.js, ui/static/index.html, docs/TESTING_GUIDE.md, docs/USER_GUIDE.md]
decisions:
  - "A-01 (reversible): Escape no longer closes any modal"
metrics:
  completed: 2026-10-03
---

# Phase 10 Plan 01: Modals close only via x Summary

Removed the four backdrop-click closers and the global Escape closer from `ui/static/app.js`, with a pytest source guard that fails if an overlay `*-modal` regains a pointer listener or an Escape closer.

## Assumption A-01 (prominent)

Escape no longer closes modals. This is a planner assumption and is reversible; see the assumptions table in 10-01-PLAN.md for how to flip it (re-add the `keydown` listener before the `chat-list` contextmenu binding and delete `test_escape_key_does_not_close_modals`).

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 RED: guard test | 60ad2a8 | 5 failed, 6 passed on the unedited app.js (4 backdrop cases + Escape) |
| 1 GREEN: deletions, aria, docs | b113310 | `test_modal_close_policy.py` 11 passed; full suite `pytest tests/ -q`: 1159 passed, 1 skipped |
| 2 Browser UAT | (no repo changes) | see below |

Task 1 diff: app.js 22 lines changed (5 insertions of a 2-line comment, deletions of the 4 overlay listeners and the Escape block); index.html only gained `type="button"` and `aria-label="Закрыть"` on `#btn-close-settings`; one bullet in TESTING_GUIDE, one numbered tip in USER_GUIDE.

Grep checks run: `e.target === $('` count 0; `document.addEventListener('keydown'` count 0; message-input keydown and chat-list contextmenu bindings still present (guard test passes).

## Browser UAT

Run with headless Chromium (Python Playwright) on an isolated copy in the session scratchpad, ports 18000/18001, scratch DB, user seeded `uat10`, one paused scheduled task and one successful run. Per modal the script asserted open after backdrop click, open after Escape, closed after the x click.

- S1 Settings: PASS (also card click keeps open, Cancel closes)
- S2 Add user: PASS (typed draft survives backdrop clicks)
- S3 Scheduler create: PASS (focus restored to #btn-scheduler-new)
- S4 Scheduler run: PASS, but the fallback was used: the run row could not be clicked via my guessed selector, so `openSchedulerRunModal(1, null)` was called through `page.evaluate`
- S5 Auto-close: PASS for Add user (closes itself after create) and PASS for Settings save (closed itself)
- S6 Unrelated handlers: PASS (zero pageerror events; right-click on a chat raised the delete confirm, dismissed)

Nothing on ports 8000/8001 was started, stopped or killed. Only my own copy processes were terminated; ports 18000/18001 verified free afterwards, scratch DB deleted, `git status` shows no UAT files in the repository.

## Deviations from Plan

1. **[Rule 3 - Blocking] Worktree base reset.** The worktree merge-base was not 7bd7f49; ran the prescribed `git reset --hard 7bd7f49` after the branch-namespace assertion passed.
2. **Docs numbering.** The plan said the USER_GUIDE Tips list ends at 5; it actually ended at 8, so the new item is number 9.
3. **Test-DB lock.** A full-suite run started in the background kept `test_app.db` locked and made concurrent targeted pytest runs error; resolved by waiting for it and re-running (no code change).

## Known Stubs

None.

## Self-Check: PASSED

Files exist (test file, SUMMARY); commits 60ad2a8 and b113310 present in `git log`.
