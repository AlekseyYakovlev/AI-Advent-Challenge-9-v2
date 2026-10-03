---
phase: 07-mcp-connection-day-16
plan: 04
subsystem: mcp-client
tags: [mcp, cli, stdio, console-proof]

requires:
  - phase: 07-mcp-connection-day-16
    provides: "agent.mcp_client.connect_once_and_list and McpConnectResult (plan 01)"
provides:
  - "scripts/mcp_list_tools.py: standalone CLI printing serverInfo and every tool with parameters"
  - "tests/test_mcp_cli.py: 6 tests (success, failure, stderr tail, usage, param formatting, subprocess run)"
affects: []

tech-stack:
  added: []
  patterns:
    - "CLI reuses the shared connect implementation; no duplicated stdio/session code"
    - "Logging silenced in main() only, so importing the script in tests does not reconfigure logging"

key-files:
  created:
    - scripts/mcp_list_tools.py
    - tests/test_mcp_cli.py
  modified: []

key-decisions:
  - "Exit codes: 0 success, 1 connect failure, 2 usage error"
  - "User-facing output uses print() (same CLI exception as run.py); logging is set to CRITICAL in main() so stdout stays clean"

requirements-completed: [MCP-06, MCP-05]

duration: 10min
completed: 2026-09-24
---

# Phase 7 Plan 04: MCP list-tools CLI Summary

**`python scripts/mcp_list_tools.py <command> [args...]` connects through the shared `connect_once_and_list`, prints serverInfo and all tools with typed/required parameters, and on failure prints code, Russian message, detail and stderr tail to stderr with exit code 1.**

## Accomplishments

- `describe_params`, `format_result`, `run`, `main` implemented per the plan contract.
- Runs from the repo root without PYTHONPATH (script inserts the repo root into `sys.path`).
- stdout is free of JSON log lines (verified by a subprocess test asserting the first line starts with `Server:`).

## Task Commits

1. Task 1 RED: failing tests - `982e1e2` (test)
2. Task 1 GREEN: CLI implementation - `d9cba37` (feat)

## Verification (observed)

- `pytest tests/test_mcp_cli.py -v`: 6 passed.
- `pytest tests/ -q`: 357 passed, no regressions.
- `python scripts/mcp_list_tools.py C:/definitely/not/here/nope.exe` printed `ERROR COMMAND_NOT_FOUND: Команда не найдена...` plus Detail on stderr, `exit=1`.
- `python scripts/mcp_list_tools.py` (no args): `Usage: ...`, `exit=2`.
- `python scripts/mcp_list_tools.py C:/Users/Aleksey/go/bin/filesystem.exe .`: `Server: filesystem-mcp-server`, `Version: 1.0.0`, `Protocol: 2024-11-05`, `Tools (17):` (filesystem.exe is installed here); parameter lines render with `*` for required.
- `grep -c "stdio_client\|ClientSession" scripts/mcp_list_tools.py` returned 0; `from agent.mcp_client import connect_once_and_list` present.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Reset stale worktree base**
- **Found during:** startup
- **Issue:** Worktree merge-base (`83f1399`) differed from the required base `e790a07`; ran the prescribed `git reset --hard e790a07` after the branch-name check passed.

Otherwise the plan was executed as written. The plan's `python ...; echo "exit=$?"` check was run in Git Bash; the Russian message renders correctly because `main()` reconfigures stdout/stderr to UTF-8.

## Assumption Drift (advisory)

None.

## Known Stubs

None.

## Threat Flags

None. T-07-18 (timeout/cleanup inherited from `connect_once_and_list`) and T-07-19 (logging set to CRITICAL in `main()`, CLI accepts no env) are mitigated as planned.

## Self-Check: PASSED

- scripts/mcp_list_tools.py, tests/test_mcp_cli.py, 07-04-SUMMARY.md exist
- Commits 982e1e2 and d9cba37 exist
