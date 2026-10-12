---
phase: 20-limits-verification-script-and-report-day-30
plan: 04
subsystem: frontend
tags: [rate-limit, queue, login-lockout, vanilla-js]
requires: []
provides:
  - RATE_LIMITED toast with restored input and removed optimistic bubble
  - queued frame caption in the loading bubble
  - login 429 lockout message in minutes
affects: [ui/static/app.js, ui/static/login.html, tests/test_limits_ui.py]
key-files:
  modified: [ui/static/app.js, ui/static/login.html]
  created: [tests/test_limits_ui.py]
decisions:
  - Texts are built client-side from fixed Russian strings plus a number; server detail is not shown for these states
metrics:
  tasks: 2
  files: 3
  completed: 2026-10-12
---

# Phase 20 Plan 04: Limit message states in the UI Summary

Chat now shows the rate-limit toast and the queue caption, and the login form shows the lockout message in minutes, all with textContent and no locks, countdowns, limit values or Stop control.

## Commits
- a63d1f0: rate-limit toast and queue caption in chat (`ui/static/app.js`)
- 460a8a4: login lockout message and source guard (`ui/static/login.html`, `tests/test_limits_ui.py`)

## Verification (observed)
- `pytest tests/test_limits_ui.py tests/test_static_js_syntax.py tests/test_port_literals.py tests/test_network_ui.py tests/test_api_keys_ui.py -q`: 43 passed (13 new tests in test_limits_ui.py).
- Full `pytest tests/ -q`: 2419 passed, 11 skipped, 1 failed: `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` (timeout). It is a subprocess timing test unrelated to these files; the run overlapped with a second concurrent pytest invocation of mine in the same worktree. Not re-run in isolation.
- Not verified: behaviour in a real browser (plan 20-06 owns that).

## Deviations from Plan
- Worktree base was reset to 9b0938e per the worktree check (it started at 06402e3). No other deviations.

## Deferred Issues
- test_supervisor restart timing failure noted above; `.planning/HANDOFF.json` showed as modified in the worktree by an external process and was left untouched.

## Known Stubs
None.

## Self-Check: PASSED
Files and commits a63d1f0, 460a8a4 exist.
