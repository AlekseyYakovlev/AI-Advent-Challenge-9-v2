---
phase: 18-lan-exposure-day-30
plan: 04
subsystem: network-security
tags: [origin, cors, tests, e2e-scripts]
requires: ["18-02"]
provides:
  - "single loopback origin list (shared.origins.LOOPBACK_ORIGINS); guard test against default-port literals"
affects: [18-07]
tech-stack:
  added: []
  patterns: ["source-scan guard test"]
key-files:
  created: [tests/test_port_literals.py]
  modified: [agent/state.py, tests/test_cors.py, tests/test_mcp_origin.py, tests/test_memory_api.py, tests/test_task_memory_api.py, scripts/e2e_kb_playwright.py, scripts/e2e_llm_providers_playwright.py]
key-decisions:
  - "agent.state.CORS_ORIGINS removed with no alias; LOOPBACK_ORIGINS is the only list"
metrics:
  duration: ~25min
  tasks: 2
  files: 8
  completed: 2026-10-12
requirements-completed: [NET-03, NET-04]
---

# Phase 18 Plan 04: Origin list consolidation Summary

Deleted the duplicated `CORS_ORIGINS` list from `agent/state.py`, repointed four test modules to `shared.origins.LOOPBACK_ORIGINS`, removed the impossible `agent/state.py` patches from the two isolated-copy e2e scripts, and added `tests/test_port_literals.py` as a permanent guard against 8000/8001 literals in application code.

## Commits
- 255373f: refactor(18-04): drop agent.state.CORS_ORIGINS in favour of shared LOOPBACK_ORIGINS
- 4084716: test(18-04): drop agent/state.py patches from e2e copy scripts and guard port literals
- ff0c6c6: fix(18-04): place LOOPBACK_ORIGINS import at module level in test_memory_api

## Verification (observed)
- Task 1 verify set (cors, mcp_origin, memory_api, task_memory_api, cascade_delete): 88 passed.
- `tests/test_port_literals.py`: 2 passed.
- `prepare_copy` of both e2e scripts into temp dirs: printed `copy ok`.
- Full suite: 2278 passed, 11 skipped, 0 failed.
- The `CORS_ORIGINS` name no longer appears in agent/, ui/, shared/, tests/, scripts/ (grep before edit showed only the expected sites).
- Not run: a live browser e2e on ports 18000/18001 (only copy preparation was checked, as planned).

## Deviations from Plan

**1. [Rule 1 - Bug] Import placed into a function body in test_memory_api.py**
- **Found during:** Task 2 full-suite run
- **Issue:** my import-reordering script inserted the line after an indented local `from shared.models import` inside a test, causing IndentationError at collection.
- **Fix:** moved the import to the module import block.
- **Files modified:** tests/test_memory_api.py
- **Commit:** ff0c6c6

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
