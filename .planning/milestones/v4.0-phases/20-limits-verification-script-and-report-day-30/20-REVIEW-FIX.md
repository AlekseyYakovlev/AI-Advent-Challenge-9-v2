---
phase: 20
fixed_at: 2026-10-12T21:00:00Z
review_path: .planning/phases/20-limits-verification-script-and-report-day-30/20-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 20: Code Review Fix Report

**Fixed at:** 2026-10-12
**Source review:** .planning/phases/20-limits-verification-script-and-report-day-30/20-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (CR-01, WR-01..WR-05)
- Fixed: 6
- Skipped: 0

Fixes were made in an isolated worktree and fast-forwarded onto Day30.

## Fixed Issues

### CR-01: Login lockout is bypassed by parallel requests

**Files modified:** `agent/limits.py`, `agent/main.py`, `tests/test_login_lockout.py`
**Commit:** 473fe7d
**Applied fix:** Added `LoginThrottle.register_attempt` (block check plus count in one synchronous step); `login()` calls it before any await. Added a gather-based test (20 parallel guesses, at most 3 get 401, 17 get 429). Logic fix: requires human verification.

### WR-01: A client can evict its own failure counter by flooding the table

**Files modified:** `agent/limits.py`, `tests/test_login_lockout.py`
**Commit:** e164afc
**Applied fix:** New pairs from an address already holding 50 pairs are refused with the lockout block (`MAX_PAIRS_PER_ADDRESS`), so a username flood cannot evict real counters. The dummy-hash timing suggestion was not applied. Logic fix: requires human verification.

### WR-02: A later turn can overtake a queued turn

**Files modified:** `agent/limits.py`, `tests/test_limits_core.py`
**Commit:** 713790d
**Applied fix:** The waiter joins the semaphore queue (`ensure_future(acquire)`) before `on_wait`, with cancellation and release handling as suggested. Regression test with a slow `on_wait` added. Logic fix: requires human verification.

### WR-03: A queued WebSocket turn takes a slot after the client has gone

**Files modified:** `agent/ws.py`, `ui/static/app.js`
**Commit:** e544256
**Applied fix:** `run_limited_turn` sends a `{"type": "dequeued"}` frame after a slot is obtained following a wait, so a dead socket aborts the turn before anything is persisted. The frontend removes the queue note on that frame (this also addresses the main part of IN-01). Logic fix: requires human verification.

### WR-04: `--rate-mode both` can misnumber the request that receives the 429

**Files modified:** `scripts/verify_service.py`
**Commit:** a7de87d (shared with WR-05; both touch one function in one file)
**Applied fix:** In `both` mode the fast sequence starts at request 1 with no prior record.

### WR-05: The rate-limit sequence keeps sending after transport failures

**Files modified:** `scripts/verify_service.py`
**Commit:** a7de87d
**Applied fix:** The sequence aborts after 3 consecutive answerless requests with a note; the run is reported as limit-not-reached (a failure).

## Skipped Issues

None.

Info and Convention findings were out of scope (fix_scope: critical_warning).

Verification: targeted pytest runs for test_login_lockout, test_limits_core, test_generation_queue, test_docs_day30, test_ws_security, test_limits_ui and test_verify_service all passed. The full suite was not run.

---

_Fixed: 2026-10-12_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
