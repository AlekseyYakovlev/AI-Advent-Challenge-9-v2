---
phase: 07-mcp-connection-day-16
plan: 01
subsystem: mcp-client
tags: [mcp, stdio, asyncio, anyio, session-registry, error-classification]

requires: []
provides:
  - "agent/mcp_client.py: owner-task persistent MCP stdio session registry keyed by (user_id, server_id)"
  - "Fixed error classification (COMMAND_NOT_FOUND, PROCESS_EXITED, HANDSHAKE_TIMEOUT, PROTOCOL_ERROR) with Russian messages"
  - "McpConnectResult and related schemas in agent/schemas.py"
  - "Portable Python fixture MCP server (tests/fixtures/mcp_stdio_server.py)"
affects: [07-02, 07-03, 07-04]

tech-stack:
  added: ["mcp==1.30.0"]
  patterns:
    - "Owner task enters stdio_client + ClientSession so anyio cancel scopes exit in the same task"
    - "Typed result objects instead of raised exceptions (mirrors ModelLoadResult)"
    - "Real OS-backed tempfile for child stderr capture"

key-files:
  created:
    - agent/mcp_client.py
    - tests/test_mcp_client.py
    - tests/fixtures/mcp_stdio_server.py
  modified:
    - requirements.txt
    - shared/config.py
    - agent/schemas.py
    - tests/conftest.py

key-decisions:
  - "Concurrent Connect on the same server is serialized with a per-key asyncio.Lock (not rejected)"
  - "Reconnect on an existing key closes the previous session first, so at most one process per server"
  - "Failed connects are remembered in _last_results so a status fetch keeps showing the error until Disconnect"

patterns-established:
  - "Failure paths always close the handle (stops child, closes stderr temp file) before returning an ERROR result"
  - "Test teardown calls cleanup_all_sessions to avoid leaked processes and loop-bound locks"

requirements-completed: [MCP-02, MCP-03, MCP-04, MCP-05]

duration: 25min
completed: 2026-09-23
---

# Phase 7 Plan 01: MCP stdio client core Summary

**Owner-task MCP stdio client (mcp 1.30.0) with in-memory (user_id, server_id) session registry, single handshake timeout, four fixed error codes with Russian messages, stderr tail capture and lazy liveness.**

## Performance

- **Tasks:** 2 of 2
- **Files created:** 3, modified: 4

## Accomplishments

- `agent/mcp_client.py` implements the full contract: `open_session`, `connect_once_and_list`, `connect_server`, `disconnect_server`, `get_status`, `is_connected`, `get_live_session`, `cleanup_*`, `classify_mcp_error`, `read_stderr_tail`, `build_error_result`, `close_handle`.
- Failure classification flattens nested `ExceptionGroup`s and never returns raw group text.
- A session held by the owner task is usable from other asyncio tasks (proved by a test calling `list_tools()` from a separate task).
- Fixture server covers six modes (ok, die_after, garbage, stderr_exit, hang, bad_protocol), so the default suite does not depend on `filesystem.exe`.

## Task Commits

1. **Task 1: mcp pin, MCP_CONNECT_TIMEOUT, schemas, fixture server** - `f8ec9a9` (feat)
2. **Task 2 (RED): failing tests for MCP client** - `68fc3bb` (test)
3. **Task 2 (GREEN): mcp_client implementation + conftest teardown** - `bc212fe` (feat)

## Verification (observed)

- `pytest tests/test_mcp_client.py -v`: 15 passed (including both real `filesystem.exe` tests, which ran because the binary is installed here; 17 tools, `filesystem-mcp-server`, and `-list` -> PROCESS_EXITED all confirmed).
- `pytest tests/ -q`: 341 passed (326 existing + 15 new), no regressions.
- Task 1 verify command printed `ok`; fixture `garbage` mode exits 0 printing `this is not json-rpc`; `stderr_exit` exits 1; `grep -c "print(" ` on the fixture returns 0.
- Acceptance greps: `errlog=`, `tempfile.TemporaryFile`, `asyncio.timeout`, `reschedule(None)` present; `StringIO` count 0; no env in log calls; no bare `except:`; `cleanup_all_sessions` in conftest.
- Orphan-process check: a psutil scan for `mcp_stdio_server` in command lines after the suite matched only the scanning shell/python commands themselves (self-matches), no leftover fixture processes.

## Deviations from Plan

### Auto-fixed / adjusted

**1. [Rule 3 - Blocking] Skipped the "create Day16 branch" step in Task 1**
- **Found during:** Task 1
- **Issue:** The plan says to switch to branch `Day16` if on `main`. This plan ran in an isolated worktree on a per-agent branch based on the Day16 head (`c327187`), and worktree commit-safety rules forbid switching to other branches.
- **Fix:** Committed on the worktree branch; the orchestrator merges into `Day16`. `git branch --show-current` acceptance criterion is therefore not applicable here.
- **Commit:** n/a

**2. [Rule 3 - Blocking] Reset stale worktree base**
- **Found during:** startup
- **Issue:** Worktree HEAD (`83f1399`) was an ancestor of the required base; ran the prescribed `git reset --hard c327187` after the branch-name safety check passed.

**3. Comment wording** - a code comment originally mentioned "StringIO", which tripped the plan's `grep -c StringIO == 0` criterion; reworded (commit `bc212fe`).

## Assumption Drift (advisory)

None material. Timeout wall-clock overshoot (research Pitfall 4) behaved as documented; the hang test asserts elapsed < timeout + 6 s.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. Mitigations implemented: T-07-02 (single timeout + READY_GRACE safety wait + task cancel), T-07-03 (owner task cleanup, close_handle cancel on CLOSE_TIMEOUT, errfile closed, replace-on-reconnect), T-07-04 (env never logged), T-07-05 (all exceptions flattened and mapped).

## Self-Check: PASSED

- agent/mcp_client.py, tests/test_mcp_client.py, tests/fixtures/mcp_stdio_server.py, 07-01-SUMMARY.md exist
- Commits f8ec9a9, 68fc3bb, bc212fe exist
