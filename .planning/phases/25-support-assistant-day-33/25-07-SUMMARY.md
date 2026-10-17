---
phase: 25-support-assistant-day-33
plan: 07
subsystem: agent
tags: [support, websocket, http-chat, mcp, rag]
requires: ["25-01", "25-04", "25-05"]
provides:
  - "agent/ws.py: _run_support_turn and the three /support recognition seams"
  - "agent/http_chat.py: /support notice body, kb_id skip, support response field"
  - "agent/schemas.py: HttpChatResponse.support"
affects: [25-08]
tech-stack:
  added: []
  patterns: ["command turn mirroring _run_help_turn with a pre-stream direct MCP lookup"]
key-files:
  created:
    - tests/test_support_ws.py
    - tests/test_support_http.py
  modified:
    - agent/ws.py
    - agent/http_chat.py
    - agent/schemas.py
    - tests/test_http_chat.py
key-decisions:
  - "The direct lookup result is emitted as an ordinary tool_call frame before streaming and stored first in tool_trace (D-07)"
  - "Notices for /support are resolved before the rate limit and chat lock, as for /help (D-04)"
requirements-completed: [SUP-01, SUP-04, SUP-08, SUP-09, SUP-10, SUP-11, SUP-12]
duration: 25min
completed: 2026-10-17
---

# Phase 25 Plan 07: /support over WebSocket and HTTP Summary

`/support [#T-NNNN | @login] <question>` now runs end to end in any chat over WebSocket and the plain-HTTP endpoint: a direct ticket/user lookup card is sent before the first token, earlier `/support` pairs are replayed, warnings never refuse a turn, and `/help` paths are untouched.

## Tasks
1. `_run_support_turn` plus dispatch seams in `_run_chat_turn` and the `ws_chat` loop, `tests/test_support_ws.py` (9 tests): commit e814234
2. HTTP `_notice_response` keyed by command, `kb_id` skip, `support` field in `HttpChatResponse`, `tests/test_support_http.py` (7 tests): commit 5afc76a

## Verification (observed)
- `pytest tests/test_support_ws.py tests/test_help_ws.py tests/test_help_turn.py -q`: 42 passed.
- `pytest tests/test_support_http.py tests/test_help_http.py tests/test_http_chat.py -n0 -q`: 42 passed.
- `pytest tests/ -q` (full suite): 3680 passed, 14 skipped.
- Not verified: behaviour against a real LLM; the upstream is mocked with respx.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] RESPONSE_KEYS in tests/test_http_chat.py**
- **Found during:** Task 2
- **Issue:** the new `support` field is part of every HTTP chat body, so the shared key set in the existing test no longer matched.
- **Fix:** added `"support"` to `RESPONSE_KEYS`.
- **Files modified:** tests/test_http_chat.py
- **Commit:** 5afc76a

Helpers in the new test files are imported from `tests/test_help_ws.py` and `tests/test_help_http.py` rather than copied; behavior coverage is as specified.

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model; mitigations T-25-30 to T-25-35 are implemented as specified (notices before rate limit, kb_id skip, fenced data in the user message, id/count-only logging, direct call visible as tool_call and in tool_trace).

## Self-Check: PASSED
- agent/ws.py, agent/http_chat.py, agent/schemas.py, tests/test_support_ws.py, tests/test_support_http.py exist; commits e814234 and 5afc76a exist.
