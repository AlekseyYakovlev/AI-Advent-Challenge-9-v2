---
phase: 08-scheduler-day-18
plan: 01
subsystem: scheduler
tags: [cronsim, sqlmodel, sqlite, partial-index, schedule-math]
requires: []
provides:
  - "ScheduledTask / TaskRun tables with DB-level overlap invariant"
  - "SCHEDULER_* settings"
  - "agent/schedule.py pure schedule math and Russian validation"
affects: [08-02, 08-03, 08-04, 08-05, 08-06, 08-07]
tech-stack:
  added: ["cronsim==2.7"]
  patterns: ["partial unique index via Index(..., sqlite_where=text(...))", "cron on naive local wall time, UTC in/out"]
key-files:
  created:
    - agent/schedule.py
    - tests/test_scheduler_schedule.py
    - tests/test_scheduler_models.py
  modified:
    - requirements.txt
    - shared/config.py
    - shared/models.py
    - tests/test_database.py
key-decisions:
  - "Cron evaluated on naive local wall time via CronSim and converted to UTC; no zoneinfo (tz injectable for tests)"
  - "Interval catch-up returns exactly one slot strictly after now, anchored to the original cadence"
  - "Cron catch-up is computed from now, never from the old slot, so downtime never replays missed slots"
requirements-completed: [SCHED-01, SCHED-06, SCHED-07, SCHED-09, SCHED-14]
duration: ~25min
completed: 2026-09-26
---

# Phase 8 Plan 01: Scheduler data and time foundation Summary

ScheduledTask/TaskRun tables (with a partial unique index making "one running run per job" a DB invariant) plus a pure cronsim-backed `agent/schedule.py` with local-wall-time next-run math and Russian validation messages.

## Tasks

| Task | Commits | Result |
|------|---------|--------|
| 1. cronsim pin, SCHEDULER_* settings, `agent/schedule.py` (TDD) | RED 4458b31, GREEN 08791ef | 45 tests pass (39 test functions, one parametrized) |
| 2. ScheduledTask / TaskRun models (TDD) | RED 32d412f, GREEN 8911656 | 10 model tests pass |

## Verification (observed)

- `pytest tests/test_scheduler_schedule.py` : 45 passed
- `pytest tests/test_scheduler_models.py tests/test_cascade_delete.py tests/test_scoping.py tests/test_scheduler_schedule.py` : 79 passed
- Full suite `pytest tests` : 690 passed
- Acceptance greps: `cronsim==2.7` present once, `import cronsim` works, `SCHEDULER_*` defaults asserted, no `zoneinfo`/`utcnow` in `agent/schedule.py`, `uq_taskrun_one_running` and `sqlite_where` present once, no `Field(ondelete=`.
- Not verified: behaviour of the OS-local-zone path (`tz=None`) against a specific DST zone; tests use fixed-offset `UTC+3` for machine independence, and one test only checks the `tz=None` result is UTC-aware and later than the input.

## Deviations from Plan

**1. [Rule 3 - Blocking] Enums added in Task 1 instead of Task 2**
- `agent/schedule.py` imports `ScheduleType` from `shared.models`, so the four enums (ScheduleType, ScheduledTaskStatus, RunStatus, RunTrigger) were added in the Task 1 commit; Task 2 added only the tables. Commit: 08791ef.

**2. [Rule 1 - Bug] Existing table-set assertion updated**
- **Found during:** full-suite run after Task 2
- **Issue:** `tests/test_database.py::test_init_db_creates_all_tables` asserts the exact set of tables and failed once `scheduledtask` and `taskrun` existed.
- **Fix:** added both names to the expected set. Commit: 8911656.

**3. Model placement**
- Tables were placed before `McpServerConfig` (right after the enums) rather than after it; no functional difference.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-08-01 (5-field check before CronSim, 100-char cap, never-firing expressions rejected, min interval), T-08-02 (partial unique index) and T-08-03 (mandatory `user_id` FK on both tables) are implemented and covered by tests.

## Notes

`.planning/HANDOFF.json` shows as modified in the worktree; it is an orchestrator-owned file, untouched by this plan and not committed.

## Self-Check: PASSED

Files exist: agent/schedule.py, tests/test_scheduler_schedule.py, tests/test_scheduler_models.py. Commits found: 4458b31, 08791ef, 32d412f, 8911656.
