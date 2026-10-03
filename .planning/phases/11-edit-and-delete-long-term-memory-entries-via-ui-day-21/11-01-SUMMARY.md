---
phase: 11-edit-and-delete-long-term-memory-entries-via-ui-day-21
plan: 01
subsystem: api
tags: [fastapi, sqlmodel, memory, rest, pytest]
requires: []
provides:
  - "PUT/DELETE /api/v1/memory/long-term/{entry_id} (user-scoped, 404/409/422, Origin + JSON checks)"
  - "agent.memory get/update/delete long-term helpers and MemoryKeyConflictError"
  - "LongTermMemoryUpdate request schema"
affects: [11-02, 11-03, 11-04]
tech-stack:
  added: []
  patterns: ["single SELECT filtered by id AND user_id (IDOR-safe)", "pre-check conflict plus IntegrityError fallback"]
key-files:
  created: []
  modified:
    - agent/memory.py
    - agent/schemas.py
    - agent/main.py
    - tests/test_memory.py
    - tests/test_memory_api.py
    - tests/test_context_engine_memory.py
    - .planning/ROADMAP.md
key-decisions:
  - "Foreign and unknown ids return the same 404 detail (no existence leak)"
  - "Value is validated non-blank but stored verbatim (not stripped); key is stripped"
requirements-completed: [MEMUI-03, MEMUI-04, MEMUI-05, MEMUI-06]
duration: 25min
completed: 2026-10-03
---

# Phase 11 Plan 01: Long-term memory write API Summary

User-scoped PUT/DELETE routes for long-term memory entries, backed by new helpers in `agent/memory.py` and a validated `LongTermMemoryUpdate` schema.

## Tasks

| Task | Commit |
|------|--------|
| 1. Roadmap assumptions line (MEMUI IDs) | 5b89d09 |
| 2. Helpers + tests (RED) | 51eff53 |
| 2. Helpers (GREEN) | 3568bdd |
| 3. REST tests (RED) | fa55b27 |
| 3. Schema + routes (GREEN) | c37679d |

## Verification (observed)

- RED: before the helpers existed, 10 new tests in test_memory.py / test_context_engine_memory.py failed (`AttributeError`); before the routes existed, 20 new REST tests failed (8 passed: the 6 existing plus the two 401 tests).
- `pytest tests/test_memory.py tests/test_context_engine_memory.py tests/test_memory_ws.py tests/test_tools.py -q`: 40 passed.
- `pytest tests/test_memory_api.py -q`: 28 passed.
- Full suite `pytest tests/ -q`: 1344 passed, 1 skipped, 1 failed (`tests/test_supervisor.py::test_agent_restarts_within_5_seconds`, timing-sensitive; it passed when re-run alone, 1 passed in 7.24s, so it is flaky under full-suite load and unrelated to this plan).
- Not run: the route-registration and schema `python -c` one-liners were not run separately; the REST tests exercise both.

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree base mismatch** - The worktree started on an older commit; reset to the specified base 0d956cd per the startup check (allowed step).

**2. Task 1 mostly pre-satisfied** - At base 0d956cd, REQUIREMENTS.md already contained MEMUI-01..06 (definitions and traceability rows) under v3.0 (coverage counts 44, no TITLE block), and the ROADMAP Phase 11 Requirements line already listed MEMUI-01..06. Only the "Assumptions" line was missing; I added it to ROADMAP.md Phase 11 section (commit 5b89d09). REQUIREMENTS.md was not modified. The orchestrator should be aware ROADMAP.md was touched (one inserted line, Phase 11 section only).

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model; all `mitigate` items (T-11-01..07) are implemented and tested.

## Self-Check: PASSED
