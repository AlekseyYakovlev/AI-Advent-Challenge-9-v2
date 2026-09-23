---
phase: 07-mcp-connection-day-16
plan: 02
subsystem: database
tags: [mcp, sqlmodel, sqlite, crud, user-scoping]

requires:
  - phase: 01-auth
    provides: User table and user_id scoping convention
provides:
  - McpServerConfig SQLModel table (user-scoped, cascading FK)
  - agent/mcp_config.py CRUD service with args JSON list and masked env merge
affects: [07-03 REST endpoints, 07-04+ session manager]

tech-stack:
  added: []
  patterns:
    - "User-scoped get returns None for foreign-owned rows (caller maps to 404)"
    - "Env masking merge: mask literal keeps stored value, never persisted for unknown keys"

key-files:
  created:
    - agent/mcp_config.py
    - tests/test_mcp_config.py
  modified:
    - shared/models.py
    - tests/test_database.py

key-decisions:
  - "args stored as JSON list of strings (no shell parsing) so Windows paths with spaces round-trip intact"
  - "env stored as plaintext JSON dict; values never logged"
  - "update_server uses cwd_set flag so callers can explicitly clear cwd"

patterns-established:
  - "merge_env: masked value = leave unchanged; absent key = removed"

requirements-completed: [MCP-01]

duration: 15min
completed: 2026-09-23
---

# Phase 7 Plan 02: MCP Server Config Persistence Summary

**User-scoped `McpServerConfig` table plus a thin CRUD service with JSON args round-tripping and masked-env merge semantics, proven by 10 tests including cascade delete and engine-restart survival.**

## Accomplishments
- `McpServerConfig` table (non-unique `user_id` FK with `ondelete="CASCADE"` via `sa_column`, name/command/args_json/env_json/cwd/enabled/timestamps); created automatically by `init_db` on existing databases.
- `agent/mcp_config.py`: `ENV_MASK`, `load_args`, `load_env`, `merge_env`, `list_servers`, `get_server` (None for other user's row), `create_server`, `update_server` (partial updates, `cwd_set` to clear cwd), `delete_server`, all with commit/rollback handling and logs that omit env values.
- `tests/test_mcp_config.py`: scoping, cross-user None, args with spaces, env merge (incl. mask for unknown key), partial update + cwd clear, enabled toggle, delete, user-delete cascade, restart survival.

## Task Commits
1. Task 1: model and CRUD service - `ae2881d`
2. Task 2: tests (and test_database.py table-set fix) - `5312198`

## Verification (observed)
- Task 1 verify command printed `ok`; `grep -c "await session.rollback()"` = 3; no `shlex`.
- `pytest tests/test_mcp_config.py -v`: 10 passed.
- `pytest tests/ -q`: 336 passed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated expected table set in test_database.py**
- **Found during:** Task 2 (full suite run)
- **Issue:** `test_init_db_creates_all_tables` asserts the exact set of tables and failed once `mcpserverconfig` existed.
- **Fix:** Added `"mcpserverconfig"` to the expected set.
- **Files modified:** tests/test_database.py
- **Commit:** 5312198

### Other notes
- Plan asked to ensure branch `Day16`; this ran in an isolated worktree on its per-agent branch, so no branch switch was made (orchestrator merges).
- Plan-specified docstring for the model referenced "Day 16, D-10"; I omitted that planning framing from product code per project rules.
- Added one extra test (`test_update_env_never_persists_mask_for_unknown_key`) covering threat T-07-08 (10 tests vs the 9 required).

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model (T-07-06, T-07-08, T-07-09 mitigated and tested; T-07-07 accepted).

## Self-Check: PASSED
- agent/mcp_config.py, tests/test_mcp_config.py, shared/models.py (McpServerConfig) present; commits ae2881d and 5312198 exist.
