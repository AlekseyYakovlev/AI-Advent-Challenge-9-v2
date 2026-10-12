---
phase: 18-lan-exposure-day-30
plan: 02
subsystem: network-security
tags: [origin, cors, websocket, host-match]
requires: []
provides:
  - "shared.origins.is_allowed_origin / LOOPBACK_ORIGINS / HostMatchCORSMiddleware"
affects: [18-04, 18-05, 18-07]
tech-stack:
  added: []
  patterns: ["single host-match origin rule", "pure ASGI CORS wrapper delegating to stock CORSMiddleware"]
key-files:
  created: [shared/origins.py, tests/test_origins.py]
  modified: [agent/dependencies.py, agent/ws.py, agent/main.py, ui/main.py, tests/test_ws_origin_validation.py, tests/test_websocket_cors.py]
key-decisions:
  - "Origin accepted when scheme is http, host equals Host header host and port equals UI_PORT; loopback UI origins always accepted"
  - "Origin null and Agent-port origins now rejected on WebSocket"
metrics:
  duration: ~35min
  tasks: 3
  files: 8
  completed: 2026-10-12
requirements-completed: [NET-03, NET-04]
---

# Phase 18 Plan 02: Host-match Origin rule Summary

One shared host-match Origin rule (`shared/origins.py`) now drives REST CORS on both apps, `require_allowed_origin` and the WebSocket handshake check; LAN IP and machine-name origins pass with no lists, foreign, null, https, port-less and Agent-port origins are rejected.

## Commits
- e1c0ea4: feat(18-02): add host-match origin rule and CORS wrapper
- dde9285: feat(18-02): route CORS, REST guard and WebSocket check through shared origin rule
- 772fdb3: test(18-02): cover LAN host match, foreign origins, null and Agent-port origins

## Verification (observed)
- `pytest tests/test_origins.py`: 28 passed after Task 1, 46 after Task 3.
- Task 2 verify set (origins, cors, mcp_origin, ws_security, scheduler_events): 76 passed.
- `pytest tests/test_ws_origin_validation.py tests/test_websocket_cors.py`: 15 passed.
- Full suite: 2266 passed, 11 skipped, 1 failed (`test_supervisor.py::test_agent_restarts_within_5_seconds`, timing under load); it passes alone (1 passed), same flake as noted in 18-01.
- No `CORS_ORIGINS` outside `agent/state.py`; no 8000/8001 literal in `shared/origins.py` or `agent/ws.py`; `import agent.main, ui.main` prints ok.

## Deviations from Plan
- TDD RED/GREEN not committed separately for Task 1 (tests and implementation in one commit).

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model (DNS rebinding T-18-10 accepted as planned).

## Self-Check: PASSED
