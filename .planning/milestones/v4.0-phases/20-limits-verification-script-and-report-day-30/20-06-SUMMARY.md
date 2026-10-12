---
phase: 20-limits-verification-script-and-report-day-30
plan: 06
subsystem: testing
tags: [playwright, e2e, rate-limit, queue, verify-service, isolated-copy]
requires:
  - phase: 20-02..20-05
    provides: limits, guarded route, supervisor readiness, queue UI, verify_service.py
provides:
  - scripts/e2e_limits_playwright.py (loopback rehearsal of the phase on an isolated copy)
affects: [20-08]
key-files:
  created: [scripts/e2e_limits_playwright.py]
  modified: []
requirements-completed: [LIM-02, LIM-03, LIM-04, LIM-05, VER-01, VER-02, VER-03]
duration: 40min
completed: 2026-10-12
---

# Phase 20 Plan 06: Limits end-to-end rehearsal Summary

Isolated-copy script (ports 18000/18001, demo limits 5/2/3/120, slow 3 s stub) that proves supervisor start-up, the limits log line, the route guard, a real `verify_service.py` run and the three UI states in headless Chromium. Exit code 0 on the first run.

## Result of the run

- `python scripts/e2e_limits_playwright.py`: exit code 0, 27 checks recorded, 27 passed, 0 failed.
- Scratch directory: `C:\Users\Aleksey\AppData\Local\Temp\limits_e2e_gp1ecd05` (results.json, app.log, verify output, screenshots).
- Start-up time (S1): 2.8 s, `agent_ready` on the first launch, no `agent_startup_incomplete`.
- Queue waits from V1 (6 parallel, cap 2, 3 s stub): 0.0, 6.61, 0.0, 3.42, 3.24, 6.43 s; stub saw at most 2 streams in flight.
- Rate limit: 429 on request 6 in both modes, Retry-After 44.
- Browser: lockout message "... через 2 мин" with `Retry-After: 120` readable cross-origin; toast "... через 44 с" with text restored and 5 bubbles; queue caption shown then removed; no page errors.
- `PASS: ports 8000/8001 untouched` (no listeners on 8000/8001 before or after) and `PASS: isolated ports are free again`.
- `python -m pytest tests/ -q`: 2553 passed, 11 skipped. `tests/test_port_literals.py`: 2 passed. `git status --porcelain eval_out`: empty.

## Task commits

- Tasks 1 and 2: d267e1f (both tasks live in the single new file and were written together; one commit instead of two).

## Deviations from Plan

- Single commit for both tasks (the "pending Task 2" placeholder stage was skipped). No defects were found in code from plans 20-01..20-05, so no fix commits.
- Check B5 (no pageerror) and sub-checks V1a..V1k / B1b are separate PASS lines.

## Behaviour changes affecting docs

None

## Known Stubs

None.

## Self-Check: PASSED

- scripts/e2e_limits_playwright.py exists; commit d267e1f exists; no files under docs/ or eval_out/ touched.
