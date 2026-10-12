---
phase: 20-limits-verification-script-and-report-day-30
plan: 02
subsystem: limits
tags: [rate-limit, generation-queue, websocket, http-chat]
requires: [20-01]
provides:
  - "one shared per-chat rate check on WebSocket and HTTP chat (HTTP 429 + Retry-After, WS RATE_LIMITED frame)"
  - "ws.run_limited_turn: every chat turn runs in a generation slot, queued frame, stats.queue_wait_seconds"
affects: [20-04, 20-05, 20-06, 20-07]
key-files:
  modified: [agent/ws.py, agent/http_chat.py, tests/test_ws_security.py, tests/test_http_chat.py]
  created: [tests/test_rate_limit.py, tests/test_generation_queue.py]
key-decisions:
  - "Lock first, then slot: a turn waiting for its chat lock holds no generation slot"
  - "Rate check sits after the 404 and 409 checks and before lock.acquire(), with no await between locked() and acquire()"
requirements-completed: [LIM-01, LIM-02, LIM-05, VER-06]
completed: 2026-10-12
---

# Phase 20 Plan 02: Shared rate limit and generation queue Summary

WebSocket and HTTP chat now share one per-chat message window (default 30) and run every turn through `run_limited_turn`, which queues turns beyond `MAX_CONCURRENT_GENERATIONS`, announces the queue with a `queued` frame and reports `stats.queue_wait_seconds`.

## Commits
- c8cb3d2 feat: `agent/ws.py` (removed RATE_LIMIT_MAX/_check_rate_limit, added `_QueueStatsSink`, `run_limited_turn`, RATE_LIMITED frame), `agent/http_chat.py` (429 with Retry-After, `headers` param on `_error_response`, turn through the gate)
- 470c46f test: `tests/test_rate_limit.py` (16 tests), `test_ws_rate_limiting` rewritten
- c02b378 test: `tests/test_generation_queue.py` (15 tests), HTTP stats assertion updated

## Verification (observed)
- Task 1 verify command: source assertions printed `ok`; lock/tool-guard/concurrent-WS/HTTP-RAG/port-literal tests: 32 passed
- `pytest tests/test_rate_limit.py tests/test_ws_security.py tests/test_http_chat.py`: 39 passed
- `pytest tests/test_generation_queue.py`: 15 passed, five runs in a row all green
- Full suite: 2530 passed, 11 skipped, 1 failed: `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` ("Agent on port ... did not become healthy"). The same test also fails when run alone on the base commit a0d859e (checked out, run, returned), so it is a pre-existing/environmental failure in this worktree, not caused by this plan (supervisor not touched).
- `tests/test_concurrent_ws.py`, `test_ws_turn_failures.py`, `test_tool_guard_ws.py` unchanged (empty diff vs base) and passing.

## Deviations from Plan
None in behaviour. The worktree base was stale and was reset to a0d859e as instructed.

## Known Stubs
None.

## Deferred Issues
- `test_supervisor.py::test_agent_restarts_within_5_seconds` fails in this worktree environment independent of this plan.

## Self-Check: PASSED
