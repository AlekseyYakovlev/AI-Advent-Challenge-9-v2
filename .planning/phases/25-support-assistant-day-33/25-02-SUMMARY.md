---
phase: 25-support-assistant-day-33
plan: 02
subsystem: mcp
tags: [mcp, fastmcp, support, stdio]
requires: []
provides:
  - "mcp_servers/support_desk.py read-only stdio MCP server named support (get_ticket, get_user, list_user_tickets, search_tickets)"
  - "mcp_servers/support_data.json seed: 4 users, 6 tickets"
affects: [25-05, 25-09, 25-10]
tech-stack:
  added: []
  patterns: ["self-contained FastMCP server modelled on project_git.py", "mtime_ns+size cache key for re-reading a JSON file"]
key-files:
  created:
    - mcp_servers/support_desk.py
    - mcp_servers/support_data.json
    - tests/test_mcp_support_desk.py
  modified: []
key-decisions:
  - "Cache is dropped in configure() so reconfiguring in tests never serves stale data"
requirements-completed: [SUP-05, SUP-06, SUP-07, SUP-12]
duration: 15min
completed: 2026-10-17
---

# Phase 25 Plan 02: Support desk MCP server Summary

Read-only FastMCP stdio server over a JSON file of users and tickets, re-read on mtime/size change, with a seed where T-1001 (429 lockout) and T-1002 (403 Origin mismatch) are both auth tickets with different causes.

## Tasks
1. Server and seed data: commit 65f6c05
2. Tests: commit 5dff663

## Verification (observed)
- Task 1 verify command printed `Ticket T-1002: open, high priority`; grep gates: no shared/agent imports, no print(, 4 tool decorators, 4 example.test emails.
- `python mcp_servers/support_desk.py --data=nope.json` exited 1 with "data file not found".
- `pytest tests/test_mcp_support_desk.py`: 25 passed (includes stdio round-trip exposing mcp__support__get_ticket, re-read, invalid JSON recovery, startup failures).
- `pytest tests/test_mcp_project_git.py tests/test_docs_mcp_servers.py`: 131 passed.

## Deviations from Plan
None - plan executed as written.

## Known Stubs
None.

## Self-Check: PASSED
