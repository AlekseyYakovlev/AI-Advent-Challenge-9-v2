---
phase: 08-scheduler-day-18
plan: 05
subsystem: scheduler
tags: [fastapi, rest, ownership-404, idor, russian-validation, live-events]
requires:
  - phase: 08-01
    provides: schedule math and validation (build_schedule_spec, initial_next_run, next_run_on_resume, ScheduleValidationError)
  - phase: 08-02
    provides: hub.publish per-user event fan-out
  - phase: 08-04
    provides: scheduler singleton (start_manual_run, abort_task_runs), build_task_out, response schemas and frame builders
provides:
  - "agent/scheduler_ops.py: user-scoped job operations (shared by REST here and by the LLM tools in 08-06)"
  - "agent/scheduler_api.py: APIRouter /api/v1/scheduler with 10 routes, included in agent/main.py"
affects: [08-06, 08-07, 08-08]
tech-stack:
  added: []
  patterns:
    - "ops layer raises SchedulerNotFoundError / SchedulerConflictError(.message) / ScheduleValidationError(.message); the router maps them to 404 / 409 / 422 with Russian string details"
    - "foreign id == missing id: one ownership helper compares row.user_id, logs a warning and raises NotFound"
key-files:
  created:
    - agent/scheduler_ops.py
    - agent/scheduler_api.py
    - tests/test_scheduler_api.py
  modified:
    - agent/main.py
    - tests/test_scoping.py
key-decisions:
  - "Field validation (title, prompt, model, schedule) runs before the per-user cap check, so an invalid request is always a 422 even for a user at the cap"
  - "Soft cancel does not abort an in-flight run; it finishes and records its result (Assumption A5)"
  - "run_task_now publishes task_updated after start_manual_run (which already published run_started), so the owner gets both frames"
patterns-established:
  - "Ops functions take (session, user_id, ...) and never the User row, so LLM tools can reuse them without a request context"
requirements-completed: [SCHED-01, SCHED-08, SCHED-09, SCHED-10, SCHED-14]
metrics:
  tasks: 2
  files: 5
  completed: 2026-09-26
---

# Phase 8 Plan 05: Scheduler ops and REST API Summary

User-scoped scheduler operations plus the `/api/v1/scheduler/*` REST router: create/list/get/pause/resume/cancel/run now/delete/run history/run detail, with 404-not-403 ownership, Russian 422/409 details and live `task_updated` / `task_deleted` events to the owner.

## Tasks

| Task | Name | Commit |
|------|------|--------|
| 1 | agent/scheduler_ops.py user-scoped job operations + ops tests | 70e5de7 |
| 2 | REST router, main.py wiring, UNAUTH_ROUTES and REST/IDOR tests | b11ea95 |

## What was built

- `agent/scheduler_ops.py`: `create_scheduled_task` (strip + length caps, `build_schedule_spec`, `initial_next_run`, per-user cap counting active+paused jobs, commit/refresh, event), `get_owned_task` / `get_owned_run` (ownership by `user_id`, foreign == missing), `pause_task` (keeps `next_run_at`), `resume_task` (`next_run_on_resume`), `cancel_task` (soft, `next_run_at` cleared, runs kept), `delete_task` (`abort_task_runs` awaited before `session.delete`, runs cascade via FK, `task_deleted` frame), `run_task_now` (`RunAlreadyActiveError` -> "Задание уже выполняется", finished jobs -> "Задание уже завершено"), `list_scheduled_tasks` (live jobs first, then `next_run_at` ascending with None last, then newest), `list_task_runs` (newest first), `publish_task_updated`. Every commit has a rollback on error.
- `agent/scheduler_api.py`: routes exactly as the plan interface; mutating routes carry `require_allowed_origin`, `POST /tasks` also `require_json_content_type`; 422 uses the literal status with the same Starlette comment as `main.py`; no 403 anywhere; runs `limit` is `Query(20, ge=1, le=100)`.
- `agent/main.py`: import plus `app.include_router(scheduler_router)` directly after the CORS block; nothing else changed.
- `tests/test_scoping.py`: 10 scheduler entries added to `UNAUTH_ROUTES` (they answered 401 without any header changes).
- `tests/test_scheduler_api.py`: 42 tests (19 ops-level, 23 REST including parametrised cases).

## Verification (actually run)

- `pytest tests/test_scheduler_api.py tests/test_scoping.py -q`: 71 passed.
- Full suite `pytest tests -q -x`: 813 passed, 0 failed (the known flaky `test_events_ws_unsubscribes_on_close` did not fail in this run).
- Acceptance greps: ownership comparisons in ops 6 (>= 3); `abort_task_runs` 1, at line 279 before `session.delete(` at 281; `await session.rollback()` 5 (>= 4); `APIRouter(prefix="/api/v1/scheduler"` 1; `include_router(scheduler_router)` 1; `status_code=403|HTTP_403` in scheduler_api.py 0; `"/api/v1/scheduler` in test_scoping.py 10; tests in test_scheduler_api.py 42 (>= 30), of which 19 ops tests (>= 14).
- Covered by tests: 201 with UTC ISO timestamps, exact Russian 422 for 6-field cron / past run_at / blank title / interval below minimum / blank model, 415 without JSON and 403 for a foreign Origin, 409 cap, pause/resume/cancel/run conflicts, `RunSummary` without `result_text`, `RunDetail` with `result_text`/`error`/`tool_trace`/`task_title`, delete leaves no `TaskRun` rows, every foreign-id route (and the foreign run) answers 404 and leaves the job unchanged, events go to the owner only.

Not verified: a real running Agent process with a browser `/ws/events` socket (events are asserted on the in-process hub queues), and real LLM execution of a manual run (`spawn_run` is monkeypatched to a no-op in these tests; execution is covered by 08-03/08-04).

## Deviations from Plan

- **[Rule 3 - Blocking]** Worktree base was not the expected commit; `git reset --hard db35e1b` per the startup check (mechanical, no work lost).
- **Test fix (not a product change):** SQLite hands back naive datetimes after `refresh`, so the first ops tests compared naive with aware values; they now normalise with `as_aware_utc`. The REST mappers already serialise UTC-aware values, which the REST test confirms.
- **Ordering choice:** the plan lists the cap count before building the spec; validation runs first so invalid input stays a 422 regardless of the cap (see key-decisions). Behaviour matches every listed bullet.
- **Extras beyond the plan:** ops test for `resume` of a once job keeping `run_at`, cap test showing cancelled jobs and other users do not count, a foreign-Origin 403 test for mutating routes, an events-are-owner-only test.
- TDD: tests were written together with the implementation in each task commit; no separate RED commits.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-08-21 (ownership helper, two-user test on every route), T-08-22 (`get_current_user` on all routes, 10 `UNAUTH_ROUTES` entries), T-08-23 (origin + JSON content-type guards, tested), T-08-24 (cap, min interval, length caps, `limit <= 100`), T-08-25 (server-side validation with Russian 422) and T-08-26 (logs carry ids and schedule type only, never title/prompt) are implemented.

## Notes

- `.planning/HANDOFF.json`, STATE.md and ROADMAP.md were not touched (orchestrator-owned in worktree mode).

## Self-Check: PASSED

Files exist: agent/scheduler_ops.py, agent/scheduler_api.py, tests/test_scheduler_api.py. Commits found: 70e5de7, b11ea95.
