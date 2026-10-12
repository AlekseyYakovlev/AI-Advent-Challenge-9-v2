---
phase: 20-limits-verification-script-and-report-day-30
plan: 03
subsystem: auth-limits
tags: [login-lockout, debug-routes, supervisor, cors]
requires: [20-01]
provides:
  - "login lockout per (username, client IP): 429 + Retry-After, LOGIN_BLOCKED body"
  - "authenticated /debug/routes; supervisor probe accepts its 401"
  - "limits_in_force log line at Agent start; CORS exposes Retry-After"
key-files:
  created: [tests/test_login_lockout.py]
  modified: [agent/main.py, shared/origins.py, ui/supervisor.py, tests/test_supervisor_host.py, tests/test_scoping.py, tests/test_network_info.py, tests/test_supervisor.py]
requirements-completed: [LIM-03, LIM-04, VER-06]
completed: 2026-10-12
---

# Phase 20 Plan 03: Login lockout and guarded /debug/routes Summary

Login is blocked per username and socket peer address after the configured wrong passwords (429 with Retry-After, identical for unknown users), `/debug/routes` needs a cookie or API key, and the supervisor treats the route probe's 401 as ready.

## Commits
- df099c7 feat: login lockout, Retry-After CORS exposure, limits_in_force log, 17 lockout tests
- ffe1af9 feat: `/debug/routes` auth, `_agent_routes_ready` probe, `agent_not_ready` log, tests
- (test commit) real-process `test_start_reaches_ready_without_forced_restart`
- (fix commit) supervisor polls process death every 0.5s

## Verification (observed)
- Task 1 command: 77 passed. Task 2 command: 66 passed. `tests/test_supervisor.py`: 2 passed.
- Full suite `pytest tests/`: 2522 passed, 11 skipped.
- Source-order check on `login` (blocked_seconds < select(User) < verify_password, no `password=` log keyword) printed ok.

## Deviations from Plan

**1. [Rule 1 - Bug] Crash-recovery test became consistently failing**
- **Found during:** full-suite run after Task 3
- **Issue:** `test_agent_restarts_within_5_seconds` failed 4 times in a row (recovery measured 4.8-5.3s). Before this plan `start()` took about 60s because the probe never saw 200, so the kill landed at a random phase of the 3s health-check tick. With a correct ready signal the kill always lands right after loop start and waits nearly a full tick plus about 2s Agent start.
- **Fix:** `_healthcheck_loop` now polls process death every `PROCESS_POLL_INTERVAL` (0.5s) while keeping the 3s `/health` ping and the startup grace. Restores the documented 5-second recovery.
- **Files:** ui/supervisor.py. Commit: last commit of this plan.

## Known Stubs
None.

## Self-Check: PASSED
