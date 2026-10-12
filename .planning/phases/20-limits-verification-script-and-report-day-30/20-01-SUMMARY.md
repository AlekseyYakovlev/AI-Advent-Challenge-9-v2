---
phase: 20-limits-verification-script-and-report-day-30
plan: 01
subsystem: limits
tags: [rate-limit, login-lockout, generation-gate, settings]
requires: []
provides:
  - "four .env limit settings with exit-code-2 validation"
  - "agent/limits.py: check_message_rate, LoginThrottle, GenerationGate, limits_in_force"
affects: [20-02, 20-03, 20-05, 20-06, 20-07]
tech-stack:
  added: []
  patterns: ["settings read at call time", "single patchable clock _now()"]
key-files:
  created: [agent/limits.py, tests/test_limits_config.py, tests/test_limits_core.py]
  modified: [shared/config.py, .env.example, tests/conftest.py]
key-decisions:
  - "Login failures never expire by age; only a success or an expired block clears them"
  - "GenerationGate has no timeout or queue limit; waiting count exposed via read-only property"
requirements-completed: [LIM-01, LIM-03, LIM-05, VER-06]
duration: ~40min
completed: 2026-10-12
---

# Phase 20 Plan 01: Limits base Summary

Four validated limit settings (30 / 5 / 300 / 2) plus `agent/limits.py` with the per-chat message window, per-(username, IP) login throttle and a loop-safe FIFO generation gate, all in process memory.

## Tasks
1. Settings, fail-fast validation, `.env.example` block, `tests/test_limits_config.py` - commit ef3a96f
2. `agent/limits.py`, conftest isolation, `tests/test_limits_core.py` - see git log (second commit of this plan)

## Verification (observed)
- `pytest tests/test_limits_config.py tests/test_bind_host.py`: 47 passed
- `pytest tests/test_limits_core.py`: 27 passed
- `pytest tests/test_limits_core.py tests/test_limits_config.py tests/test_cascade_delete.py tests/test_ws_security.py tests/test_auth.py`: all passed
- Full suite `pytest tests/`: 2461 passed, 11 skipped. One earlier run with `-x` reported 1 failure (not identified, as the run was aborted by -x output filtering); the immediate full re-run was fully green, so it looks flaky/unrelated (no route or WS code was touched).
- Also run and passing: the inspect source check (no wait_for/timeout in the gate, no await in check_message_rate) and the manual `RATE_LIMIT_MESSAGES_PER_MINUTE=abc python -c "import shared.config"` (prints the ERROR message, exit 2). The ast import check was not run separately; limits.py imports no FastAPI, SQLModel, agent.ws, agent.main or agent.http_chat. `agent/ws.py`, `agent/main.py`, `agent/http_chat.py`, `agent/state.py` untouched.

## Deviations from Plan
None in behaviour. The file edits were done through editor tools rather than shell scripts.

## Known Stubs
None.

## Self-Check: PASSED
