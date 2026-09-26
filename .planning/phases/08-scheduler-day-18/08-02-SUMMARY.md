---
phase: 08-scheduler-day-18
plan: 02
subsystem: agent-realtime
tags: [websocket, event-hub, scheduler, per-user-isolation]
requires: []
provides:
  - "agent/events.py: EventHub, hub singleton, ws_events handler"
  - "WS /ws/events route (origin + session-cookie checked)"
  - "test env guards: SCHEDULER_ENABLED=false, hub cleared per test"
affects: [08-04, 08-05, 08-07]
tech-stack:
  added: []
  patterns:
    - "user_id -> set[asyncio.Queue] fan-out with sync drop-oldest publish"
    - "single writer task per socket, wait_for-wrapped receive loop"
key-files:
  created:
    - agent/events.py
    - tests/test_scheduler_events.py
  modified:
    - agent/main.py
    - tests/conftest.py
key-decisions:
  - "Reuse ws._validate_origin and get_current_user_ws so /ws/events has identical handshake policy to /ws/chat"
  - "Do not reuse ws.active_connections/broadcast_model_event (ownerless)"
requirements-completed: [SCHED-12, SCHED-14]
metrics:
  tasks: 2
  files: 4
  completed: 2026-09-26
---

# Phase 8 Plan 02: Per-user live event channel Summary

In-memory `EventHub` (user_id to bounded queues, sync drop-oldest publish) plus an authenticated `WS /ws/events` endpoint, with test-environment guards so no scheduler loop starts under test.

## What was built

- `agent/events.py`: `EventHub` (`subscribe`, `unsubscribe`, `publish`, `subscriber_count`, `clear`), module singleton `hub`, `_pump` (single writer per socket) and `ws_events` (origin check, cookie auth via `get_current_user_ws`, both closing 1008 before accept; receive loop wrapped in `asyncio.wait_for` with a 60 s timeout; unsubscribes in `finally`).
- `agent/main.py`: `@app.websocket("/ws/events")` route delegating to `ws_events`.
- `tests/conftest.py`: `SCHEDULER_ENABLED=false` default and `events_hub.clear()` in `clean_test_db`.
- `tests/test_scheduler_events.py`: 11 tests (3 rejection cases with 1008, owner delivery, cross-user isolation with sentinel frame 999 as B's first frame, unsubscribe on close, same-user fan-out, and 4 pure hub unit tests: fan-out/isolation, drop-oldest, no-subscriber no-op, unsubscribe semantics).

## Verification (actually run)

- `pytest tests/test_ws_auth.py tests/test_ws_origin_validation.py -q`: 13 passed (after Task 1).
- `pytest tests/test_scheduler_events.py -q`: 11 passed.
- Full suite `pytest tests/ -q`: 646 passed.
- Acceptance greps: no `active_connections|broadcast_model_event` in events.py (0); `asyncio.wait_for` 1; `code=1008` 2; `1008` in tests 6; `999` in tests 2.

Not verified: behavior against a real browser and a live Agent process (only Starlette TestClient).

## Deviations from Plan

- Worktree base was behind the expected commit; reset to 180f6e1 per the startup check (mechanical, no code impact).
- No other deviations; plan executed as written.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (T-08-05..T-08-08 mitigated as specified; T-08-09 accepted).

## Self-Check: PASSED

- agent/events.py, tests/test_scheduler_events.py present; commits 9d9bf80 and ea1904a exist.
