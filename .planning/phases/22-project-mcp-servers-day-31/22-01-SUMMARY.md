---
phase: 22-project-mcp-servers-day-31
plan: 01
subsystem: mcp
tags: [mcp, git, fastmcp, stdio, safety, truncation]
requires: []
provides:
  - "mcp_servers/project_git.py core: launch args, fail-fast startup, bounded git runner, path gate, ref gate, truncation helpers"
affects: [22-03]
tech-stack:
  added: []
  patterns:
    - "shell-free process runner (create_subprocess_exec) with output cap and psutil tree kill"
    - "refs reach git only as commit hashes resolved with rev-parse --verify --end-of-options"
key-files:
  created:
    - mcp_servers/__init__.py
    - mcp_servers/project_git.py
    - tests/test_mcp_project_git_core.py
  modified: []
key-decisions:
  - "Ranges A..B and A...B are supported by resolve_ref_or_range (both sides resolved to hashes)"
  - "Server avoids the shared logger; stderr diagnostics only, no print calls"
requirements-completed: [PGIT-01, PGIT-02]
duration: 40min
completed: 2026-10-13
---

# Phase 22 Plan 01: Project git MCP server core Summary

Self-contained `mcp_servers/project_git.py` (FastMCP `local-git`, no tools yet) with fail-fast startup checks, a shell-free bounded git runner, a root-confining path gate, a hash-resolving ref gate and truncation helpers, covered by 66 unit tests.

## What was built

- Task 1 (f8339fa): package marker, `parse_args` (no abbreviations, `--max-chars >= 1000`, `--timeout > 0`), `fail`, `git_env` (all inherited `GIT_*` stripped), `base_argv`, `run_command`/`run_git` (output cap, stderr drained separately, psutil process-tree kill on timeout), `check_work_tree`, `main`.
- Task 2 (59c0cbf): `resolve_inside_root`, `check_ref_syntax`, `resolve_ref`, `resolve_ref_or_range`, `truncation_marker`, `fit_text`, `compose_truncated_diff`.

## Verification (observed)

- `pytest tests/test_mcp_project_git_core.py -q`: 66 passed.
- `pytest tests/test_mcp_cli.py tests/test_mcp_client.py tests/test_mcp_tools.py -q`: 51 passed.
- `python mcp_servers/project_git.py --root=C:\definitely\missing\folder`: exit 1, last stderr line "repository root does not exist or is not a folder".
- Acceptance one-liners (`fit_text` marker, `--output=x` rejection, import prints `local-git 18000 10.0`) ran and gave the expected output; forbidden-string grep count 0.
- Not run: no live MCP stdio session against the server (tools arrive in Plan 22-03).

## Deviations from Plan

**1. [Rule 3 - Blocking] Test DB lock from a concurrent pytest session.** Another process (a KB test run) held `test_app.db`, so the autouse fixture failed with PermissionError. Ran my verification with `DB_PATH=test_mcp_git_core.db` (conftest uses `setdefault`) and removed that file afterwards. No code change.

**2. TDD order.** Task 1 implementation was written before its tests (tests then run and one test-side bug fixed: child stdout CRLF in text mode on Windows). Task 2 was likewise implemented first, then tested. No separate RED commits.

**3. Timeout test uses `timeout=2.0`** rather than 0.5 so the grandchild pid file is reliably written on Windows before the kill; the tree-kill assertion is unchanged.

## Known notes

- Test run emits one benign asyncio Proactor `ValueError: I/O operation on closed pipe` unraisable warning from a killed child's pipe transport; it does not fail any test.
- `agent/kb_indexer.py` showed as modified in the working tree during the run; it belongs to another session and was not touched or committed here.

## Self-Check: PASSED

Files `mcp_servers/__init__.py`, `mcp_servers/project_git.py`, `tests/test_mcp_project_git_core.py` exist; commits f8339fa and 59c0cbf exist.
