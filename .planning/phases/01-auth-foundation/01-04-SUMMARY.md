---
phase: 01-auth-foundation
plan: 04
subsystem: auth
tags: [auth, websocket, session-cookie, idor, ownership-gate]

requires:
  - phase: 01-auth-foundation (Plan 01)
    provides: agent/dependencies.py::get_current_user_ws, shared/auth.py::SESSION_COOKIE_NAME
  - phase: 01-auth-foundation (Plan 03)
    provides: tests/conftest.py::login_test_client, REST 404-not-403 IDOR pattern
provides:
  - "agent/ws.py::ws_chat pre-accept session-cookie + chat-ownership gate"
  - "agent/ws.py::_user_owns_chat(db, user_id, chat_id) -> bool"
  - "tests/test_ws_auth.py — no-cookie/bad-cookie/expired/wrong-owner/happy-path/no-revalidation WS coverage"
affects: [phase-02-memory, phase-03-personalization]

tech-stack:
  added: []
  patterns:
    - "Pre-accept WebSocket auth gate: open one async_session_factory() session, resolve get_current_user_ws, check _user_owns_chat, close the session, THEN accept() — never after accept()"
    - "Identical close code+reason (1008, 'Unauthorized') for both no-session and wrong-owner failures, mirroring the REST 404-not-403 IDOR pattern from Plan 03"
    - "D-10: session validated exactly once per WS connection at accept time, never re-checked inside the message loop"

key-files:
  created:
    - tests/test_ws_auth.py
    - .planning/phases/01-auth-foundation/deferred-items.md
  modified:
    - agent/ws.py

key-decisions:
  - "Both auth failure modes (no/invalid/expired session, wrong chat owner) close with the identical code=1008 reason='Unauthorized' — the client cannot distinguish 'not logged in' from 'not your chat', matching the REST IDOR mitigation already established in Plan 03"

requirements-completed: [AUTH-03, AUTH-04]

duration: ~60min
completed: 2026-09-20
---

# Phase 01 Plan 04: WebSocket Session and Ownership Gate Summary

`agent/ws.py::ws_chat` now validates the session cookie and chat ownership once, strictly before `websocket.accept()`, closing every unauthenticated or cross-user handshake with code 1008 — completing AUTH-03's "valid for both REST and WebSocket" requirement that Plan 03 left half-done.

## Performance

- **Duration:** ~60 min (mostly spent diagnosing pre-existing WS test-suite flakiness, not implementation)
- **Tasks:** 2 completed
- **Files modified:** 2 (`agent/ws.py`, `tests/test_ws_auth.py`), plus 1 new tracking doc (`deferred-items.md`)

## Accomplishments

- Every WebSocket handshake to `/ws/chat/{chat_id}` now requires a valid, unexpired session cookie AND ownership of that chat, checked once before `accept()` — closing the last unauthenticated door identified in the plan's objective.
- `_user_owns_chat(db, user_id, chat_id)` added, mirroring `_validate_origin`'s no-raise/bool-return style; a chat with `user_id IS NULL` is treated as unowned (never matches).
- D-10 preserved: the session is resolved once, inside a single `async with async_session_factory() as db:` block that closes before `accept()`; the `while True` message loop is untouched (proven by an `inspect.getsource` slice assertion, not just eyeballing).
- `tests/test_ws_auth.py` added: 6 sync `TestClient` tests covering happy-path accept, no-cookie, garbage-cookie, expired-session, wrong-owner (chat A still exists in the DB afterward), and the D-10 no-revalidation case (deleting the session mid-connection does not kill the open socket; the next invalid payload gets a normal `{"type": "error"}` reply, not a close).

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the failing WebSocket authentication suite** - `42dbcd9` (test)
2. **Task 2: Add the pre-accept session and ownership gate to ws_chat** - `a96aa11` (feat)

## Files Created/Modified

- `tests/test_ws_auth.py` - 6 sync `TestClient` tests pinning down the pre-accept gate's accept/reject/no-revalidation behavior
- `agent/ws.py` - `_user_owns_chat` helper; `ws_chat` now opens a session, resolves `get_current_user_ws`, checks ownership, and closes with 1008 before ever calling `accept()`
- `.planning/phases/01-auth-foundation/deferred-items.md` - new tracking doc for the pre-existing WS test-combination flake found during verification (see below)

## Decisions Made

- Both auth failure paths (no session vs. wrong owner) return the identical `code=1008, reason="Unauthorized"` — the plan's explicit instruction, carrying forward the REST 404-not-403 IDOR precedent from Plan 03 so a client can never distinguish "you're not logged in" from "this isn't your chat."

## Deviations from Plan

None — the code exactly matches the plan's `<action>` text for both tasks. No auto-fixes were needed in `agent/ws.py` or `tests/test_ws_auth.py` beyond what the plan specified.

### Deferred Issues (not in scope, not fixed)

- **`tests/test_concurrent_ws.py::test_five_parallel_ws_messages_no_integrity_error` intermittently times out (WebSocketDisconnect, code 1000 "Idle timeout") when run via the plan's own narrower 5-file Task 2 `<verify>` command** (`tests/test_ws_auth.py tests/test_ws_origin_validation.py tests/test_ws_security.py tests/test_websocket_cors.py tests/test_concurrent_ws.py`). Reproduced 3/3 times with this exact file selection, both **with and without** the Task 2 fix applied (confirmed by reverting `agent/ws.py` to its pre-Task-2 state via `git checkout -- agent/ws.py`, rerunning, then reapplying via `git apply` on a saved diff) — so this predates and is independent of Plan 01-04's changes. It does NOT reproduce in isolation (6/6 consecutive standalone runs of `test_concurrent_ws.py` passed in ~1.1-1.2s each) or in the full suite (`python -m pytest tests/ -q` passed 114/114, zero failures, twice). Logged in full detail, including the exact commands and evidence, in `.planning/phases/01-auth-foundation/deferred-items.md`. Same category as the already-documented pre-existing flake `tests/test_context_engine.py::test_extract_and_update_facts_debounce_and_merge` noted in Plans 01-01/01-02/01-03.

## Verification Evidence

- `tests/test_ws_auth.py` RED (before Task 2): happy-path passed; no-cookie, garbage-cookie, and expired-session rejection cases genuinely failed (`assert code == 1008` failed because `ws_chat` accepted every handshake) — observed directly, not assumed. The run was stopped after 3 of 4 rejection cases had already failed, due to each hanging case's ~300s idle-timeout wait; this satisfies the plan's "non-zero exit, rejection cases connect instead of closing with 1008" expectation.
- `python -m pytest tests/test_ws_auth.py -v` (after Task 2) — 6 passed, confirmed on 3 separate runs.
- `python -c "...get_current_user_ws...index...accept()..."` — printed `ok` (auth check precedes `accept()`, called exactly once).
- `python -c "...while True:...get_current_user_ws not in loop and _user_owns_chat not in loop..."` — printed `ok` (D-10 enforced, no per-message re-validation).
- `grep -c 'reason="Unauthorized"' agent/ws.py` — 2.
- `grep -n "await websocket.accept()" -B 2 agent/ws.py` — shows the ownership check immediately above it.
- `grep -n "1008" ui/static/app.js` — still present inside `STOP_RECONNECT_CODES`; no frontend change needed.
- `python -m pytest tests/ -q` (full suite, authoritative per the plan's `<verification>` block) — **114 passed, 0 failures, 24.64s**.
- `python -m pytest tests/test_ws_auth.py tests/test_ws_origin_validation.py tests/test_ws_security.py tests/test_websocket_cors.py tests/test_concurrent_ws.py -v` — 20/21 passed with the fix applied (the 1 failure is the pre-existing, deferred flake documented above); confirmed identical failure reproduces on the unmodified baseline.

## Known Stubs

None — every artifact (the pre-accept gate, `_user_owns_chat`, the full WS auth test suite) is wired to real code paths and exercised by passing tests.

## Assumption Drift (advisory)

None — implementation matched the plan's `<action>` text and `01-CONTEXT.md`'s D-10 throughout.

## Threat Flags

None — every trust boundary and threat this plan touches (`T-01-24` through `T-01-29`) is already enumerated in the plan's own `<threat_model>`; no new, un-registered surface was introduced.

## Next Phase Readiness

- AUTH-03 is now fully complete: the session cookie is validated for both REST (Plan 03) and WebSocket (this plan) paths.
- AUTH-04 (data scoped by `user_id`) is reinforced on the WebSocket path via `_user_owns_chat`.
- Phase 01 Auth Foundation's WebSocket half is done; no blockers for Phase 2 (Memory), which can rely on `get_current_user_ws`/`_user_owns_chat`-equivalent patterns for any future WS-adjacent auth needs.
- The deferred WS-suite-combination flake should be picked up by a future test-infrastructure pass (not urgent — it does not affect the authoritative full-suite run).

---
*Phase: 01-auth-foundation*
*Completed: 2026-09-20*
