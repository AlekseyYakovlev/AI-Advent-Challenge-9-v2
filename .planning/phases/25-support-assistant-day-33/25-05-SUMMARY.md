---
phase: 25-support-assistant-day-33
plan: 05
subsystem: agent
tags: [support, mcp, lookup, prompt-injection]
requires: ["25-02"]
provides:
  - "agent/support_lookup.py: SupportLookup, direct_lookup, fence_support_data, retrieval_query, warning texts and constants"
  - "agent/tools.py: public dispatch_mcp_call wrapper"
affects: [25-07, 25-09]
tech-stack:
  added: []
  patterns: ["app-initiated MCP call with the same result shape as a model tool call", "fenced untrusted data block with tag neutralisation"]
key-files:
  created:
    - agent/support_lookup.py
    - tests/test_support_lookup.py
  modified:
    - agent/tools.py
key-decisions:
  - "Direct lookup never raises: every MCP failure becomes a Russian warning (D-10)"
  - "Server resolved only by fixed name `support` among the caller's own rows (D-09)"
requirements-completed: [SUP-08, SUP-09, SUP-12]
duration: 15min
completed: 2026-10-17
---

# Phase 25 Plan 05: Support lookup Summary

In-code lookup of a ticket (`get_ticket`) or user (`get_user`) on the caller's `support` MCP server, returning a card-ready dispatch result plus a `<support_data>` fenced prompt block (preamble "data, not instructions", inner tags neutralised, 6000-char cap), a ticket-aware retrieval query (question + `Subject:`/`Error:`, 300 chars, one-constant switch) and precise warnings for not-found, not-added, disabled, not-connecting, tool-error and crash paths.

## Tasks
1. Public `dispatch_mcp_call` and `agent/support_lookup.py`: commit 3234571
2. `tests/test_support_lookup.py` (18 tests, real support server): commit 319090e

## Verification (observed)
- `pytest tests/test_support_lookup.py tests/test_mcp_support_desk.py tests/test_mcp_project_git.py -q`: 98 passed.
- Task 1 verify command: fence yields exactly one closing tag; `retrieval_query` printed `Почему? S E` (run with PYTHONIOENCODING=utf-8; the default cp1252 console cannot print Cyrillic).
- Acceptance greps: `_dispatch_mcp_call` not referenced in support_lookup.py; `RETRIEVAL_HINT_ENABLED: bool = True` present. The full suite was not run for this plan.

## Deviations from Plan

**1. [Rule 1 - Bug] Test assertion for the cap**
- **Found during:** Task 2
- **Issue:** counting "a" characters also counted the letters in the preamble.
- **Fix:** assert a run of exactly 6000 data characters instead.
- **Files modified:** tests/test_support_lookup.py
- **Commit:** 319090e

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
