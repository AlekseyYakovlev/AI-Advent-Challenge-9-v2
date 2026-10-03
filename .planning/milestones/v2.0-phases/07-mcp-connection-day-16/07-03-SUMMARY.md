---
phase: 07-mcp-connection-day-16
plan: 03
subsystem: api
tags: [mcp, fastapi, rest, ownership, idor, env-masking]

requires:
  - phase: 07-mcp-connection-day-16
    provides: "mcp_client session registry (07-01), McpServerConfig CRUD service (07-02)"
provides:
  - "McpServerCreate / McpServerUpdate / McpServerResponse schemas (env key names only)"
  - "/api/v1/mcp/servers CRUD plus connect, disconnect, status endpoints"
  - "Agent shutdown closes all live MCP sessions"
  - "tests/test_mcp_api.py (15 tests)"
affects: [07-04, 07-05, 07-06]

tech-stack:
  added: []
  patterns:
    - "Connect outcomes are always HTTP 200 with a typed connection object in the body"
    - "Foreign or missing server id returns 404, never 403"

key-files:
  created:
    - tests/test_mcp_api.py
  modified:
    - agent/schemas.py
    - agent/main.py

key-decisions:
  - "PUT always calls disconnect_server (safe when idle) instead of only when connected, so an edit also clears a stale remembered connect error"
  - "Last-resort except Exception around connect_server returns a PROTOCOL_ERROR result so no connect path can 500"

requirements-completed: [MCP-01, MCP-02, MCP-03, MCP-04, MCP-05]

duration: 20min
completed: 2026-09-24
---

# Phase 7 Plan 03: MCP REST API Summary

**User-scoped MCP server REST API (CRUD, connect, disconnect, status) with 404-only ownership checks, env values never returned, auto-disconnect on edit/disable/delete, lazy liveness on list/status, and shutdown cleanup.**

## Accomplishments

- `agent/schemas.py`: `McpServerCreate`, `McpServerUpdate` (blank name/command rejected after strip, env keys must match `^[A-Za-z_][A-Za-z0-9_]*$`, per-arg and per-value length limits, item-count limits) and `McpServerResponse` (no env-values field, only `env_keys`).
- `agent/main.py`: seven endpoints under `/api/v1/mcp/servers`, `_get_mcp_server_or_404`, `_mcp_server_to_response`, and `cleanup_all_sessions()` in the lifespan shutdown branch before `engine.dispose()`.
- `tests/test_mcp_api.py`: 15 tests covering auth, CRUD, validation, masking, masked-env merge, cross-user 404 on all five id routes, connect success and failure, /health after failures, D-08/D-09/D-13 behavior.

## Task Commits

1. Task 1: schemas, endpoints, lifespan cleanup - `818d15e`
2. Task 2: REST API tests - `7e832b4`

## Verification (observed)

- Task 1 verify command printed `ok` (all five route paths registered).
- `pytest tests/test_mcp_api.py -v`: 15 passed.
- `pytest tests/ -q`: 366 passed (351 before the new tests + 15), no regressions.
- `grep HTTP_403 agent/main.py | grep -i mcp`: no matches; `grep -c /health tests/test_mcp_api.py` = 2; `cleanup_all_sessions` present in `lifespan`.
- Not verified: an actual Agent shutdown (lifespan) with a live child process; only the code path was added, no test exercises the real lifespan teardown.

## Deviations from Plan

### Auto-fixed / adjusted

**1. [Rule 1 - Bug] PUT disconnects unconditionally**
- **Found during:** Task 1
- **Issue:** Plan disconnects only when `is_connected`. After a failed connect the registry remembers the error, so editing the config would keep showing a stale error.
- **Fix:** Call `disconnect_server` on every PUT (idempotent when idle).
- **Files modified:** agent/main.py
- **Commit:** 818d15e

**2. Worktree base reset** - HEAD started at `83f1399` (ancestor of the required base); ran `git reset --hard e790a07` after the branch-name safety check passed.

**3. Config import alias** - the last-resort connect guard needs `MCP_CONNECT_TIMEOUT`, so `shared.config.settings` is imported into `agent/main.py` as `app_config` to avoid clashing with the many local `settings` variables.

## Assumption Drift (advisory)

None.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-07-10 (auth on every route, 401 test), T-07-11 (404 ownership, cross-user test), T-07-13 (env_keys only), T-07-14 (guarded connect, /health tests), T-07-15 (Pydantic limits), T-07-16 (disconnect on PUT/DELETE, lifespan cleanup) implemented.

## Self-Check: PASSED

- agent/schemas.py, agent/main.py, tests/test_mcp_api.py, this summary exist
- Commits 818d15e and 7e832b4 exist
