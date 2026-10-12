---
phase: 20-limits-verification-script-and-report-day-30
verified: 2026-10-12T23:00:00Z
status: passed
score: 5/5 roadmap success criteria verified (11/11 requirements accounted for)
has_blocking_gaps: false
overrides_applied: 0
---

# Phase 20: Limits, verification script and report (Day 30) Verification Report

**Phase Goal:** The service is protected against abuse and overload, and a live run from a second LAN machine proves it works as a private service.
**Status:** passed. **Re-verification:** No.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Per-chat message limit set in `.env`, applies to WS and HTTP; HTTP 429 with Retry-After | VERIFIED | `shared/config.py` RATE_LIMIT_MESSAGES_PER_MINUTE (default 30, ge=0). `agent/ws.py` and `agent/http_chat.py` both call `limits.check_message_rate`. HTTP returns 429 with `headers={"Retry-After": ...}`. Tests `test_rate_limit.py` pass. The live run shows 429 on request 6 with Retry-After 46. |
| 2 | Login lockout is configurable; `/debug/routes` needs auth; `/health` open | VERIFIED | `agent/main.py` login checks `limits.login_throttle.blocked_seconds` before the user lookup, then `record_failure` and `reset`. `/debug/routes` has `dependencies=[Depends(get_current_user)]`. `/health` has no auth dependency. LOGIN_MAX_FAILED_ATTEMPTS and LOGIN_LOCKOUT_SECONDS are in config. `test_login_lockout.py` passes. |
| 3 | Concurrency cap; excess generations queue and all complete | VERIFIED | `ws.run_limited_turn` runs in `limits.generation_gate.slot`. `test_generation_queue.py` passes. In the live run 6 parallel requests all returned 200, 4 waited in the queue, 0 errors. |
| 4 | Script reports reachability/auth, N parallel real-model requests, rate limit and context overflow | VERIFIED | `scripts/verify_service.py` exists. `test_verify_service.py` passes. The live output has all four sections (auth 200/200/401, 6/6 successes with timings, 429 in chat and fast modes, 413 CONTEXT_OVERFLOW). |
| 5 | `Day30_report.md` has a live LAN run plus a manual UI chat; pytest and TESTING_GUIDE cover the areas | VERIFIED | `Day30_report.md` and `eval_out/day30/*` exist. The run came from macOS against 192.168.88.224:8001 with exit code 0. The report contains the VER-05 statement, labelled as the user's claim without details. `test_docs_day30.py` passes (44 tests). |

**Evidence caveats:** VER-04 rests on files the user returned from the Mac. I cross-checked their internal consistency (non-loopback address, macOS platform, real model qwen/qwen3.5-9b, exit code 0) but did not rerun the live run. VER-05 is the user's own confirmation (2026-10-12) with no details, which the plan accepts.

## Requirements Coverage

| Req | Status | Evidence |
|-----|--------|----------|
| LIM-01 | SATISFIED | config plus the shared `limits.check_message_rate` in WS and HTTP |
| LIM-02 | SATISFIED | HTTP 429 with a Retry-After header (code and tests) |
| LIM-03 | SATISFIED | `limits.login_throttle` in login, configurable |
| LIM-04 | SATISFIED | `/debug/routes` guarded by `get_current_user` |
| LIM-05 | SATISFIED | `GenerationGate` with a configurable cap, live queue waits observed |
| VER-01 | SATISFIED | verify_service auth checks |
| VER-02 | SATISFIED | parallel table with timings |
| VER-03 | SATISFIED | rate-limit and 413 tables |
| VER-04 | SATISFIED | live-run files and report (see caveat) |
| VER-05 | SATISFIED | user confirmation, accepted per plan 20-08 |
| VER-06 | SATISFIED | test files and TESTING_GUIDE sections exist; `test_docs_day30.py` guards pass |

No orphaned requirement IDs: all 11 IDs in REQUIREMENTS.md for Phase 20 are claimed by plan frontmatter.

## Spot-Checks

Ran `pytest` on test_limits_core, test_limits_config, test_rate_limit, test_generation_queue, test_login_lockout, test_verify_service, test_docs_day30 and test_limits_ui. Result: 185 passed. I did not rerun the full suite. The summaries report 2572 passed and 11 skipped for the whole suite. `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` was flaky in several worktrees. Plan 20-03 addressed it with a process-poll fix.

## Anti-Patterns

No TBD, FIXME or XXX markers in `agent/limits.py`, `scripts/verify_service.py`, `scripts/e2e_limits_playwright.py` or `Day30_report.md`.

## Warnings (non-blocking)

- Bookkeeping: `.planning/REQUIREMENTS.md` still shows LIM-01..05, VER-01..03 and VER-06 as `[ ]` / Pending. Only VER-04 and VER-05 are marked complete. Update these when closing the phase.
- The ROADMAP phase 20 list still shows `[ ]` at the top (line 14). Update it when closing the phase.
- The VER-05 check ran on the Phase 18-19 build, before the limits existed. It is not a re-check of the final build, but it is what the requirement and the plan accept.

## Human Verification Required

None outstanding.

## Gaps Summary

No gaps. The phase goal is achieved in code, tests, docs and the live-run report.

_Verifier: Claude (gsd-verifier)_
