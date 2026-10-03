---
phase: 08-scheduler-day-18
plan: 09
subsystem: scheduler
tags: [gap-closure, code-review, state-machine, race-conditions, input-validation, tool-dispatch]
requires:
  - phase: 08-04
    provides: SchedulerService (claim_slot, start_manual_run, tick, abort_task_runs)
  - phase: 08-05
    provides: scheduler_ops (pause/resume/delete/run_now) and REST routes
  - phase: 08-06
    provides: schedule_task tool and dispatch_tool_calls integration
provides:
  - "Pause/resume can no longer re-arm a job whose final run is in flight (CR-01)"
  - "start_manual_run guards status in the UPDATE and inserts the run only afterwards (WR-08)"
  - "Claimed runs are spawned before any further await; one failing job cannot abort a tick (WR-01)"
  - "delete_task takes the job out of scheduling before aborting runs (WR-02)"
  - "Out-of-range schedule inputs are validation errors; tool handler failures never kill a chat turn (WR-03)"
affects: [08-verification]
tech-stack:
  added: []
  patterns:
    - "Guarded UPDATE (status + next_run_at IS NOT NULL) as the state-transition primitive for pause/resume"
    - "Exhaustion of max_runs evaluated in SQL (CASE) against the row being updated, not against a stale ORM copy"
    - "Spawn-then-announce: the run task exists before any post-commit await; announce failures are logged, not propagated"
key-files:
  created: []
  modified:
    - agent/scheduler.py
    - agent/scheduler_ops.py
    - agent/schedule.py
    - agent/schemas.py
    - agent/tools.py
    - tests/test_scheduler_service.py
    - tests/test_scheduler_api.py
    - tests/test_scheduler_schedule.py
    - tests/test_scheduler_tools.py
key-decisions:
  - "A job with next_run_at NULL is 'final run in flight': pause and resume both refuse it with the new Russian message 'Задание выполняет последний запуск' (409); finalize_task is unchanged"
  - "SchedulerConflictError and MSG_ALREADY_FINISHED now live in agent/scheduler.py (start_manual_run must raise it and scheduler_ops imports scheduler, so the reverse would be circular); scheduler_ops re-exports them so existing imports keep working"
  - "run_at is bounded to at most 10 years ahead (same limit as delay_seconds) so 9999-12-31 is a validation error independent of the host time zone"
patterns-established:
  - "Schedule range limits are named constants in agent/schedule.py (MAX_DELAY_SECONDS, MAX_INTERVAL_SECONDS, MAX_RUNS_LIMIT) reused by the tool schema"
requirements-completed: []
metrics:
  tasks: 3
  files: 9
  completed: 2026-09-26
---

# Phase 8 Plan 09: Review gap closure (CR-01, WR-01, WR-02, WR-03, WR-08) Summary

Closes the five code-review findings the user chose to fix now: the pause/resume re-arm of an exhausted job, the unguarded manual run, the claim-to-spawn gap and tick isolation, the delete-versus-poll-loop race, and unbounded schedule inputs escaping as 500s or killed chat turns. WR-04..WR-07 and IN-01..IN-07 were left untouched as instructed.

## Tasks

| # | Task | Commit |
|---|------|--------|
| 1 | CR-01 pause/resume re-arm and WR-08 unguarded manual run | a5067db |
| 2 | WR-01 spawn-before-await and per-job tick isolation; WR-02 delete race | 2ee1e1a |
| 3 | WR-03 unbounded schedule inputs | 15d0828 |

## What changed

**CR-01 (scheduler_ops.py).** `pause_task` and `resume_task` now go through `_guarded_transition`, a single guarded UPDATE requiring the expected status and `next_run_at IS NOT NULL`. A job whose slot is consumed (final run of a once job or of the last allowed `max_runs` run) answers 409 `Задание выполняет последний запуск` instead of being re-armed. The UI already shows `err.message` from a failed `apiFetch` as an error toast, so no frontend change was needed.

**WR-08 (scheduler.py).** `start_manual_run` runs the guarded UPDATE first (`status IN (ACTIVE, PAUSED)`); `rowcount != 1` rolls back and raises `SchedulerConflictError(MSG_ALREADY_FINISHED)`. The `TaskRun` is added only after that succeeds. `next_run_at` clearing for once/exhausted jobs is a SQL `CASE` on the stored `run_count`/`max_runs`.

**WR-01 (scheduler.py).** `_claim_and_announce` and `start_manual_run` call `spawn_run` immediately after the commit; the refresh/`build_task_out`/publish part moved to `_announce_started`, which logs `scheduler_announce_failed` (error type only) instead of propagating. `tick` wraps each due job in `try/except Exception`, logging `scheduler_claim_failed` and continuing; `CancelledError` still propagates.

**WR-02 (scheduler_ops.py).** `delete_task` commits `status=CANCELLED, next_run_at=NULL` first, then `abort_task_runs`, then deletes the row, then calls `abort_task_runs` once more for a run spawned in between. The 404-for-foreign-id path and the `task_deleted` event are unchanged.

**WR-03 (schedule.py, schemas.py, tools.py).** Named limits `MAX_DELAY_SECONDS`/`MAX_INTERVAL_SECONDS` (10 years) and `MAX_RUNS_LIMIT` (1_000_000). Overflow/OS/value errors from timedelta addition, `astimezone` and datetime construction become `ScheduleValidationError(MSG_BAD_SCHEDULE)`; run_at beyond 10 years ahead is rejected. `ScheduleTaskArgs` carries the same `le=` bounds. `dispatch_tool_calls` wraps the handler call: an unexpected exception logs `tool_handler_failed` (tool name, exception type) and becomes an `ok=False` result with code `tool_failed`; `CancelledError` propagates.

## Test evidence

- New tests were run against the pre-fix source and failed, then passed on the fix: 5 failed on old code for Task 1, 4 for Task 2 (the teardown of those also errored because of the leaked run tasks the bug produces), and 18 for Task 3 (the schedule unit file cannot even import the new constants on old code).
- Full suite: `python -m pytest tests/ -q` -> 928 passed (baseline 881, +47 new), no failures.
- All seven scheduler test files (267 tests) looped three times: 267 passed each time, no flakes.

## Deviations from Plan

**1. [Rule 1 - Bug/Consistency] No `le=` on `ScheduledTaskCreate` (REST body)**
- **Planned:** add upper bounds to both `ScheduleTaskArgs` and `ScheduledTaskCreate`.
- **Actual:** bounds are enforced only in the schedule layer for REST; `ScheduleTaskArgs` has `le=`.
- **Why:** a Pydantic `le` on the REST body produces FastAPI's list-shaped 422 detail, but the UI shows `detail` verbatim and the plan's own acceptance test wants a Russian string. The schedule layer gives the same 422 with a Russian message and also guards the direct-handler path. `agent/scheduler_schemas.py` is therefore unchanged.

**2. [Rule 3 - Blocking] `SchedulerConflictError` moved into `agent/scheduler.py`**
- **Found during:** Task 1. `start_manual_run` must raise it but `scheduler_ops` imports `scheduler`. Defined in `scheduler.py` (with `MSG_ALREADY_FINISHED`) and imported back into `scheduler_ops`, so `scheduler_ops.SchedulerConflictError` and all existing catchers are unchanged.

**3. [Deviation - message] New constant `MSG_FINAL_RUN_IN_PROGRESS`** instead of reusing "already finished", because the job is not finished yet (its last run is still executing) and the text is shown in the UI toast.

**4. [Test adaptation] `test_delete_removes_runs_and_aborts_first`** now expects two abort calls (one while the job is CANCELLED and still stored, one after the delete) - a direct consequence of the plan's WR-02 design, not a weakened check.

**5. run_at upper bound** (at most 10 years ahead) was added beyond the literal plan text so `9999-12-31T23:59:59` is a validation error regardless of the host time zone (it did not overflow on a UTC+ host).

## Assumption Drift (advisory)

- **Found during:** Task 3, WebSocket test. **Planned:** an out-of-range tool call ends the turn with a normal `done` reply. **Actual:** existing behaviour for any failed native tool is a reply followed by a `TOOL_ERROR` frame, then `done`; the test asserts the tool result, the streamed reply, the `done` frame and that the same socket serves a second message. No product change.
- **Found during:** Task 3. **Planned:** tool call with out-of-range numbers returns `invalid_schedule`. **Actual:** through `dispatch_tool_calls` the `le=` bound rejects numeric out-of-range values at argument validation (ok=False, Pydantic message); the `invalid_schedule` code is returned by the handler for run_at extremes and for direct handler calls. Both are ok=False and non-raising.

## Known limitations

- Spawning before announcing means a very fast run could in theory publish `run_finished` before `run_started`; the UI treats each frame as a full task snapshot so the final state is still correct.
- Not verified in a live app (no running Agent/UI used in this worktree); verification is by the automated suite only.

## Known Stubs

None.

## Threat Flags

None. No new endpoints or trust-boundary surface; the changes tighten validation and state transitions.

## Self-Check: PASSED

- agent/scheduler.py, agent/scheduler_ops.py, agent/schedule.py, agent/schemas.py, agent/tools.py: modified and committed (a5067db, 2ee1e1a, 15d0828).
- tests/test_scheduler_service.py, test_scheduler_api.py, test_scheduler_schedule.py, test_scheduler_tools.py: modified and committed.
- STATE.md, ROADMAP.md, HANDOFF.json and config.json untouched.
