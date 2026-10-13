---
phase: 23-help-command-verification-and-report-day-31
plan: 01
subsystem: api
tags: [fastapi, sqlmodel, mcp, rag, help-command]

requires:
  - phase: 21-project-docs-in-rag-day-31
    provides: ready knowledge bases, a ready base stays ready while files are added
  - phase: 22-project-mcp-servers-day-31
    provides: the local-git MCP server name
provides:
  - HelpConfig per-user table (documentation base mark, search settings, tool choice)
  - agent/help_command.py (recognition, notices, per-turn tool selection)
  - GET/PUT /api/v1/help/config
  - only_server_ids filter in build_mcp_toolset
affects: [23-02, 23-03, 23-04, settings-ui, http-chat]

tech-stack:
  added: []
  patterns:
    - "Explicit NULL threshold kept by a Column(Float) without a column default"
    - "allowed_tools is always a frozenset; empty blocks every built-in tool"

key-files:
  created:
    - agent/help_command.py
    - agent/help_api.py
    - tests/test_help_command.py
    - tests/test_help_api.py
  modified:
    - shared/models.py
    - agent/mcp_tools.py
    - agent/main.py
    - tests/test_database.py

key-decisions:
  - "Recognition rule: whole word /help, case-insensitive, leading whitespace allowed"
  - "A ready base with phase adding counts as ready for /help"
  - "threshold column is Column(Float, nullable=True) with no column default so an explicit null is stored as NULL"

requirements-completed: [HELP-01, HELP-04, HELP-05]

duration: 40min
completed: 2026-10-13
---

# Phase 23 Plan 01: /help configuration and building blocks Summary

**Per-user `/help` settings table with REST routes, plus pure functions for command recognition, no-model notices and per-turn tool selection (default: only the `local-git` MCP server).**

## Accomplishments
- `HelpConfig` table with `kb_id` foreign key `ON DELETE SET NULL`; deleting the marked base clears the mark (tested through the real DELETE route).
- `agent/help_command.py` with all contracts from the plan; it does not import `agent.ws`.
- `GET/PUT /api/v1/help/config` with ownership (404), readiness (422), bounds (422), origin and JSON content-type guards.
- `build_mcp_toolset(..., only_server_ids=...)` filters before auto-connect.

## Exports of agent/help_command.py
Constants: `NOTICE_FRAME_TYPE="help_notice"`, `NOTICE_USAGE="usage"`, `NOTICE_NO_BASE="no_base"`, `DEFAULT_MCP_SERVER_NAME="local-git"`, `DEFAULT_HELP_THRESHOLD=0.45`, `DEFAULT_HELP_TOP_K=5`, `DEFAULT_HELP_CANDIDATE_K=20`, `TOOL_GROUPS`, `HELP_USAGE_TEXT`, `HELP_NO_BASE_TEXT`, `HELP_BASE_NOT_READY_TEXT` (format field `name`), `HELP_MCP_WARNING_TEXT` (format field `name`).
Functions: `parse_help`, `is_help_command`, `notice_frame`, `load_help_config`, `load_help_kb`, `resolve_help_notice`, `stored_servers`, `build_help_tools`; dataclass `HelpTools(schemas, toolset, allowed_tools: frozenset[str], warnings: list[{"server","text"}])`.

## REST fields (as implemented)
- PUT body (all optional): `kb_id` (int >= 1 or null to clear), `top_k` (1..20), `candidate_k` (1..50), `threshold` (0..1, explicit null = calibrated), `lexical`, `llm_rerank`, `hybrid`, `rewrite`, `tools_memory`, `tools_tasks`, `tools_scheduler` (strict booleans), `mcp_server_ids` (list of own server ids, max 50; explicit null = default).
- GET/PUT reply: `kb_id`, `kb_name`, `kb_status`, `top_k`, `candidate_k`, `threshold`, `calibrated_threshold`, `lexical`, `llm_rerank`, `hybrid`, `rewrite`, `mcp_server_ids`, `mcp_default`, `mcp_missing`, `tools_memory`, `tools_tasks`, `tools_scheduler`.
- Errors: 404 for foreign or missing base or server, 422 for a base that is not ready or out-of-range values, 403 for a foreign Origin, 401 without a session.

## Task Commits
1. Task 1: HelpConfig, help_command, only_server_ids filter - `4e7c4de`
2. Task 2: help config routes (includes the threshold NULL fix and the table-set update in `tests/test_database.py`) - `2405606`

## Verification (actually run)
- `pytest tests/test_help_command.py` : 28 passed (before the final commit; also within the full run).
- `pytest tests/test_help_api.py tests/test_help_command.py tests/test_cascade_delete.py tests/test_settings_fallback.py` : 65 passed.
- `pytest tests/test_mcp_chat_ws.py tests/test_mcp_tools.py tests/test_mcp_auto_connect.py` : 42 passed.
- Full suite `pytest tests`: 3012 passed, 13 skipped, 0 failed.

## Deviations from Plan

**1. [Rule 1 - Bug] Explicit NULL threshold was replaced by the default on insert**
- **Found during:** Task 2 tests
- **Issue:** with a plain `Field(default=0.45)` SQLAlchemy applied the column default when the value was None on a new row, so "reset to calibrated" was lost.
- **Fix:** `threshold` uses `sa_column=Column(Float, nullable=True)` (no column-level default).
- **Files:** shared/models.py. **Commit:** 2405606

**2. [Rule 3 - Blocking] Table-set assertion in `tests/test_database.py`**
- **Issue:** `test_init_db_creates_all_tables` lists every table; the new `helpconfig` table failed it.
- **Fix:** added `"helpconfig"` to the expected set. **Commit:** 2405606

`tests/test_mcp_tools.py` exists, so it stayed in the verify command.

## Known Stubs
None.

## Threat Flags
None beyond the plan threat model (T-23-01..05 mitigated and tested: foreign base and foreign server return 404, bounds, origin check, frozenset default).

## Self-Check: PASSED
- agent/help_command.py, agent/help_api.py, tests/test_help_command.py, tests/test_help_api.py exist.
- Commits 4e7c4de and 2405606 exist.
