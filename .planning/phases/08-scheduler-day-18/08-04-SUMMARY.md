---
phase: 08-scheduler-day-18
plan: 04
subsystem: scheduler
tags: [asyncio, sqlite, optimistic-claim, poll-loop, lifespan, event-hub]
requires:
  - phase: 08-01
    provides: ScheduledTask/TaskRun tables, partial unique index, agent/schedule.py math, SCHEDULER_* settings
  - phase: 08-02
    provides: hub.publish per-user event fan-out
  - phase: 08-03
    provides: run_headless_turn, HeadlessResult, HeadlessRunError
provides:
  - "agent/scheduler_schemas.py: RunSummary/RunDetail/ScheduledTaskOut/ScheduledTaskCreate, UTC-aware mappers, WS frame builders"
  - "agent/scheduler.py: SchedulerService (claim_slot, tick, finalize_task, recover_orphaned_runs, execute_run, start_manual_run, abort_task_runs, start/stop) + scheduler singleton + build_task_out"
  - "agent lifespan: startup orphan recovery, SCHEDULER_ENABLED-gated poll loop, stop before MCP cleanup and engine.dispose()"
affects: [08-05, 08-06, 08-07, 08-08]
tech-stack:
  added: []
  patterns:
    - "guarded UPDATE (status active AND next_run_at = old) with rowcount==1 plus run insert in one commit"
    - "partial unique index as the overlap backstop; IntegrityError rolls the whole claim back so the next tick records a skipped run"
    - "semaphore acquired outside asyncio.timeout so queueing does not consume the run deadline"
    - "post-commit task snapshot (refresh + build_task_out) attached to run_started/run_finished frames"
key-files:
  created:
    - agent/scheduler_schemas.py
    - agent/scheduler.py
    - tests/test_scheduler_service.py
    - tests/test_scheduler_lifespan.py
  modified:
    - agent/main.py
key-decisions:
  - "Manual 'Run now' counts toward run_count/max_runs, leaves a periodic job's next_run_at alone and clears it for once/exhausted jobs (Assumption A5)"
  - "Semaphore is rebuilt in start() so it belongs to the loop the service actually runs on (per-test TestClient loops)"
  - "No MCP retry changes: mcp_tool_count == 0 is only logged (scheduler_run_no_mcp_tools); the user-visible note comes from run_headless_turn"
  - "claim_slot reads task.id before any rollback because rollback expires the instance"
requirements-completed: [SCHED-03, SCHED-04, SCHED-05, SCHED-06, SCHED-07, SCHED-08, SCHED-09, SCHED-14]
metrics:
  tasks: 2
  files: 5
  completed: 2026-09-26
---

# Phase 8 Plan 04: Scheduler engine Summary

Exactly-once slot claiming, one-run catch-up, overlap skipping, restart recovery and a semaphore + deadline run executor around the headless runner, wired into the Agent lifespan and pushing per-owner live events.

## Tasks

| Task | Name | Commit |
|------|------|--------|
| 1 | Schemas/mappers, atomic claim, tick, finalize, startup recovery | 1a1c101 |
| 2 | Run executor, run-now, abort, loop start/stop, lifespan wiring | fb45968 |

## What was built

- `agent/scheduler_schemas.py`: response models and mappers; every datetime goes through `as_aware_utc` so JSON ends in `Z`; `duration_ms` only once finished; `tool_trace` parsed with a JSON-error fallback to `[]`; frame builders (`run_started`, `run_finished`, `task_updated`, `task_deleted`). Run objects in frames never carry `result_text`.
- `claim_slot`: overlap check, `next_run_after_claim`, `max_runs` exhaustion, guarded UPDATE, TaskRun insert, one commit. `rowcount != 1` or `IntegrityError` rolls back and returns None (the slot stays due and the next tick records it as skipped).
- `tick`: up to 20 due jobs per pass, each claimed in its own session; RUNNING runs publish `run_started` with a refreshed snapshot and (optionally) spawn; SKIPPED runs finalize the job and publish `run_finished`.
- `finalize_task`: one guarded UPDATE (active/paused, `next_run_at IS NULL`, no running run) covers once completion, `max_runs` completion and crash-between-claim-and-finish. `recover_orphaned_runs` fails RUNNING rows with the restart message and finalizes exhausted jobs.
- `execute_run`: `async with self._semaphore` then `asyncio.timeout(SCHEDULER_RUN_TIMEOUT)` around `run_headless_turn`; maps HeadlessRunError, timeout, cancellation and unexpected errors to Russian failure text; `_finish_run` tolerates a deleted row and only touches RUNNING rows.
- `start_manual_run`, `abort_task_runs`, `spawn_run` with strong task references, poll loop (`start`/`stop`), `is_running_loop`.
- `agent/main.py` lifespan: `recover_orphaned_runs()` always, `start()` only when `SCHEDULER_ENABLED`, `stop()` before `cleanup_all_sessions()` and `engine.dispose()`.

## Verification (actually run)

- `pytest tests/test_scheduler_service.py tests/test_scheduler_lifespan.py tests/test_memory_ws.py -q`: 37 passed (28 service + 3 lifespan + memory_ws).
- Acceptance greps: `rowcount` 4, `ScheduledTask.next_run_at == ` 1, `== None` 0, `utcnow` 0, `except IntegrityError` 1, `asyncio.timeout(settings.SCHEDULER_RUN_TIMEOUT)` 1 (line 317, after `async with self._semaphore` on 316), `recover_orphaned_runs` in main.py 1, `SCHEDULER_ENABLED` in main.py 1, lifespan order stop/cleanup/dispose confirmed, `except asyncio.CancelledError` 2.
- Full suite `pytest tests` (run twice): 760 passed, 1 failed both times. The single failure is `tests/test_scheduler_events.py::test_events_ws_unsubscribes_on_close` from plan 08-02, which is flaky and not caused by this plan: it also fails intermittently with the base `agent/main.py` restored (3 of 8 runs of `pytest tests/test_cors.py tests/test_scheduler_events.py`) and passes when its file runs alone. Logged in `deferred-items.md`, not fixed (out of scope).

Not verified: behaviour against a real LM Studio / MCP server (the runner is monkeypatched in these tests; runner behaviour is covered by 08-03), a hard kill of the Agent process (recovery is tested by seeding a RUNNING row before startup, not by killing a real process), and DST behaviour of local-time cron.

## Deviations from Plan

- **[Rule 3 - Blocking]** Worktree base was behind the expected commit; `git reset --hard bb5640d` per the startup check (mechanical).
- **[Rule 1 - Bug]** `claim_slot` originally logged `task.id` after `session.rollback()`, which raised `MissingGreenlet` (rollback expires the instance). Fixed by reading `task_id` before any rollback; caught by the IntegrityError-race test. Included in commit 1a1c101.
- **Task split:** `tick` references `spawn_run`, which the plan assigns to Task 2; in the Task 1 commit `spawn_run` did not exist yet (only reachable with `spawn=True`; Task 1 tests use `spawn=False`). Task 2 added it immediately after.
- Extra tests beyond the listed behaviours: not-yet-due job, stale claim of a consumed slot, inactive jobs, mapper duration, end-to-end loop test, stop-ordering test.
- TDD: tests were written alongside the implementation in each task commit; no separate RED commits.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-08-15 (guarded claim + IntegrityError backstop), T-08-16 (semaphore, timeout, overlap skip, batch limit 20, resilient loop), T-08-17 (frames published with `hub.publish(task.user_id, ...)` only, no `result_text`; two-user test), T-08-18 (startup recovery + finalize rule), T-08-19 (logs carry ids, statuses, durations and exception type names only) and T-08-20 (abort cancels and awaits; `_finish_run` tolerates a missing row) are implemented and tested.

## Notes

- `.planning/HANDOFF.json` is modified in the main checkout and was not touched.
- STATE.md and ROADMAP.md were not modified (orchestrator-owned in worktree mode).

## Self-Check: PASSED

Files exist: agent/scheduler.py, agent/scheduler_schemas.py, tests/test_scheduler_service.py, tests/test_scheduler_lifespan.py. Commits found: 1a1c101, fb45968.
