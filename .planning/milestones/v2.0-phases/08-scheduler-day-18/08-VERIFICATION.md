---
phase: 08-scheduler-day-18
verified: 2026-09-26T00:00:00Z
status: passed
score: 5/5 must-haves verified
has_blocking_gaps: false
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 5/5 (SC2 with one minor gap, CR-01)
  gaps_closed:
    - "CR-01: pause+resume while the final run is in flight re-arms an exhausted job"
  gaps_remaining: []
  regressions: []
gaps: []
deferred: []
human_verification: []
parked_backlog:
  - "WR-04 DST fall-back fold in cron math (999.7-999.10)"
  - "WR-05 cancel gate is a keyword heuristic"
  - "WR-06 schedule_task ungated, persisted prompt runs with all MCP tools"
  - "WR-07 /ws/events never re-validates the session after the handshake"
  - "IN-01..IN-07 info items"
---

# Phase 8: Scheduler (Day 18) Verification Report

**Phase Goal:** A user (or the LLM via chat tools) can schedule delayed (one-shot) and periodic (interval/cron) jobs; the agent runs them in the background, stores each job's status and every run's result, and the UI shows scheduled and completed jobs. Written from scratch in Python inside the Agent process.
**Verified:** 2026-09-26
**Status:** passed
**Re-verification:** Yes - after gap closure plan 08-09 (CR-01 plus WR-01, WR-02, WR-03, WR-08)

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | From chat the LLM creates a job; it appears in the sidebar panel, goes running -> success live, result opens in a Markdown modal | VERIFIED (accepted model limitation) | Unchanged since the previous report: `agent/scheduler_tools.py`, `ui/static/index.html` `#scheduler-panel`, `ui/static/app.js` `/ws/events` client with DOMPurify. Real-browser demo (08-08-SUMMARY) PASS. Local-model tool-selection caveat was accepted by the user. Gap closure did not touch `ui/` or `scheduler_tools.py` (git diff a5067db^..HEAD lists only agent/schedule.py, scheduler.py, scheduler_ops.py, schemas.py, tools.py). |
| 2 | Once/interval/cron fire exactly once per slot; overlaps recorded skipped; max_runs and one-shot jobs auto-complete | VERIFIED | Atomic claim intact: `claim_slot` guarded UPDATE (`agent/scheduler.py:140-155`, rowcount==1). Partial unique index `status = 'running'` intact (`shared/models.py:548`). `finalize_task` unchanged (`agent/scheduler.py:260-284`). CR-01 edge now closed (see Gap closure): pause and resume are refused while `next_run_at IS NULL`, so an exhausted job cannot be re-armed; new tests `test_once_job_final_run_cannot_be_rearmed_by_pause_resume` and `test_max_runs_final_run_cannot_be_rearmed_by_pause_resume` assert exactly one run and status COMPLETED. |
| 3 | After Agent restart a missed job runs once flagged late; interrupted runs are marked failed | VERIFIED | `recover_orphaned_runs` (`agent/scheduler.py:286-320`) unchanged; lifespan tests (`tests/test_scheduler_lifespan.py`) pass. Demo step 9 PASS (previous report). |
| 4 | REST `/api/v1/scheduler/*` and `/ws/events` are user-scoped (404 / owner-only events); LLM cannot cancel unless the user asked | VERIFIED | `get_owned_task` / `get_owned_run` still gate every op (`agent/scheduler_ops.py:156-176`); the new `_guarded_transition`, `delete_task` and `run_task_now` all start from `get_owned_task`, so foreign ids remain 404. Events and cancel-gate tests pass. Headless allowlist `HEADLESS_TOOL_ALLOWLIST = frozenset({"save_long_term_memory"})` intact (`agent/headless.py:35`). |
| 5 | `pytest tests/ -q` passes including new scheduler tests | VERIFIED | Orchestrator ran the full suite at HEAD: 928 passed (baseline 881, +47). Verifier ran the four requested files: 224 passed; additionally models/runner/events/lifespan: 53 passed (277 scheduler tests total, all green). |

**Score:** 5/5 truths verified

### Gap closure (plan 08-09)

All fixes verified by reading the current code; each has tests that the SUMMARY reports failing on the pre-fix source (not re-verified by the verifier, but the tests read as genuine regression tests).

| Finding | Fix evidence (current code) | Test evidence |
|---------|-----------------------------|---------------|
| CR-01 pause/resume re-arms exhausted job | `agent/scheduler_ops.py:239-240` (pause) and `:262-263` (resume) raise `SchedulerConflictError(MSG_FINAL_RUN_IN_PROGRESS)` when `next_run_at is None`; `_guarded_transition` (`:202-229`) additionally requires `next_run_at IS NOT NULL` in the UPDATE WHERE so a race cannot slip through; `finalize_task` unchanged | `tests/test_scheduler_service.py:688-744` (once and max_runs=1: pause and resume refused, tick yields no second run, job ends COMPLETED with run_count 1); `tests/test_scheduler_api.py:566` REST pause/resume -> 409 |
| WR-08 unguarded manual run | `agent/scheduler.py:450-469`: guarded UPDATE with `status IN (ACTIVE, PAUSED)`, `rowcount != 1` -> rollback + `SchedulerConflictError(MSG_ALREADY_FINISHED)`, `TaskRun` added only after the update succeeds; exhaustion computed in SQL `CASE` from stored `run_count`/`max_runs` (`:433-439, 461`) | `test_start_manual_run_refuses_job_cancelled_after_status_check` (`:747`, no run, run_count 0), `test_start_manual_run_exhaustion_uses_stored_run_count` (`:777`) |
| WR-01 stranded RUNNING row / tick abort | `_claim_and_announce` calls `spawn_run` immediately after the claim commit, before refresh/build/publish (`agent/scheduler.py:218-222`); announce moved to `_announce_started` with try/except logging `scheduler_announce_failed` (`:242-258`); `start_manual_run` spawns before announcing (`:478-480`); `tick` isolates each job with `try/except Exception` -> `scheduler_claim_failed`, `CancelledError` not caught (`:199-204`) | `test_failed_announce_still_runs_the_claimed_run_and_next_slot_is_claimable` (`:811`), `test_manual_run_is_spawned_even_when_announce_fails` (`:844`), `test_one_failing_job_does_not_abort_the_tick` (`:864`) |
| WR-02 delete races poll loop | `agent/scheduler_ops.py:311-339`: guarded commit of `status=CANCELLED, next_run_at=NULL` first, then `abort_task_runs`, then delete, then a second `abort_task_runs`; `task_deleted` frame and 404-for-foreign preserved | `test_delete_takes_job_out_of_scheduling_before_aborting_runs` (`:887`): tick during abort claims nothing, no runs left, exactly one `run_started` and `task_deleted` last |
| WR-03 unbounded schedule inputs | Named limits `MAX_DELAY_SECONDS`/`MAX_INTERVAL_SECONDS` (10 years) and `MAX_RUNS_LIMIT` (`agent/schedule.py:14-17`); OverflowError/OSError/ValueError mapped to `ScheduleValidationError` in `_to_local_naive`, `_from_local_naive`, `_add_seconds`, `parse_run_at` (`:61-134`); run_at capped at 10 years ahead (`:168`); `ScheduleTaskArgs` `le=` bounds (`agent/schemas.py:499,512,522`); `dispatch_tool_calls` wraps the handler, logs `tool_handler_failed`, returns an error result, `CancelledError` (BaseException) still propagates (`agent/tools.py:164-173`) | `tests/test_scheduler_schedule.py:370-422` (extremes for delay/interval/max_runs/run_at, overflow in slot math); `tests/test_scheduler_api.py:492` (REST 422 Russian detail); `tests/test_scheduler_tools.py:355,371,410,746` (handler ok=False, dispatch does not raise, tool_failed for raising handler, WS turn survives) |

Deviation noted and judged acceptable: `ScheduledTaskCreate` (REST body) has no `le=`; bounds are enforced in the schedule layer, which yields a 422 with a Russian string detail (better for the UI than Pydantic's list-shaped detail) and also protects the direct-handler path. `SchedulerConflictError` and `MSG_ALREADY_FINISHED` moved into `agent/scheduler.py` (re-exported by `scheduler_ops`) to avoid a circular import; existing catchers unaffected.

### Parked to backlog (non-blocking, not gaps)

WR-04 (DST fold in cron math, DST-observing hosts only), WR-05 (cancel gate is a keyword heuristic), WR-06 (`schedule_task` ungated; persisted prompt runs with MCP tools), WR-07 (`/ws/events` never re-validates the session), IN-01..IN-07 (info). Tracked as backlog 999.7-999.10. None breaks a ROADMAP success criterion.

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `shared/models.py` (ScheduledTask, TaskRun, partial unique index) | VERIFIED | index at :548 |
| `agent/schedule.py` | VERIFIED | bounds and error mapping added |
| `agent/scheduler.py` | VERIFIED | claim, spawn-before-announce, tick isolation, guarded manual run |
| `agent/headless.py` | VERIFIED | allowlist intact |
| `agent/scheduler_ops.py`, `scheduler_api.py`, `scheduler_schemas.py` | VERIFIED | scoped ops, guarded transitions |
| `agent/scheduler_tools.py`, `agent/tools.py` | VERIFIED | handler failures contained |
| `agent/events.py`, `ui/static/index.html`, `ui/static/app.js` | VERIFIED | unchanged by 08-09 |

### Key Link Verification

| From | To | Status |
|------|----|--------|
| `agent/main.py` lifespan | recover_orphaned_runs / start / stop | WIRED (unchanged) |
| `execute_run` | `run_headless_turn` under timeout + semaphore | WIRED |
| `scheduler_ops.delete_task` | `scheduler.abort_task_runs` (twice, around the row delete) | WIRED |
| `scheduler_ops.run_task_now` | `scheduler.start_manual_run` with `SchedulerConflictError` propagated to REST 409 | WIRED |
| `dispatch_tool_calls` | `TOOL_REGISTRY[name]` inside try/except | WIRED |

### Data-Flow Trace (Level 4)

Unchanged from the previous report: sidebar panel <- `list_scheduled_tasks` (SQL, user-filtered) plus WS events; run modal <- `TaskRun.result_text` from `_finish_run`. FLOWING.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Requested scheduler files | `python -m pytest tests/test_scheduler_service.py tests/test_scheduler_schedule.py tests/test_scheduler_api.py tests/test_scheduler_tools.py -q -p no:warnings` | 224 passed | PASS |
| Remaining scheduler files | `python -m pytest tests/test_scheduler_models.py tests/test_scheduler_runner.py tests/test_scheduler_events.py tests/test_scheduler_lifespan.py -q -p no:warnings` | 53 passed | PASS |
| Full suite | orchestrator-run at HEAD | 928 passed | PASS (not re-run) |

### Probe Execution

Step 7c: SKIPPED (no probes declared by any PLAN).

### Requirements Coverage

SCHED-01..SCHED-14 are all declared in PLAN frontmatter and mapped to Phase 8; no orphans. Plan 08-09 declares `requirements: []` (gap closure).

| Requirement | Status | Note |
|-------------|--------|------|
| SCHED-01 create with Russian validation | SATISFIED | out-of-range inputs now 422 (WR-03 closed) |
| SCHED-02 LLM tools, origin SET NULL | SATISFIED | |
| SCHED-03 poll loop, atomic claim, restart-safe | SATISFIED | |
| SCHED-04 catch-up flagged late, orphan recovery | SATISFIED | |
| SCHED-05 headless turn, allowlist, timeout | SATISFIED | |
| SCHED-06 TaskRun records, nothing in chat | SATISFIED | |
| SCHED-07 overlap skipped, DB-enforced | SATISFIED | |
| SCHED-08 max_runs / one-shot auto-complete | SATISFIED | CR-01 edge closed |
| SCHED-09 5-field cron, local tz, UTC storage | SATISFIED | WR-04 DST fold caveat parked |
| SCHED-10 user-scoped REST, 404 foreign | SATISFIED | |
| SCHED-11 cancel gate | SATISFIED | heuristic (WR-05 parked) |
| SCHED-12 `/ws/events` origin + cookie | SATISFIED | WR-07 parked |
| SCHED-13 sidebar panel | SATISFIED | |
| SCHED-14 pytest coverage | SATISFIED | 277 scheduler tests, 928 total |

Bookkeeping: `.planning/REQUIREMENTS.md` was reported as still showing SCHED-01..14 as pending in the previous report; the orchestrator should flip them to Complete (verifier does not modify tracking files).

### Anti-Patterns Found

No `TBD`/`FIXME`/`XXX` markers in `agent/scheduler*.py`, `agent/schedule.py`, `agent/tools.py`. No stubs. Broad `except Exception` in the new tick/announce/dispatch paths is deliberate isolation with error-type-only logging and `CancelledError` propagating (BaseException), consistent with CLAUDE.md (no bare except, no traceback logging).

### Human Verification Required

None. Note (from SUMMARY 08-09, informational): the gap closure was verified by the automated suite only, not in a live app; a very fast run could theoretically publish `run_finished` before `run_started`, which the UI tolerates because each frame is a full task snapshot.

### Gaps Summary

CR-01 is closed with a defence in depth (pre-check plus a guarded UPDATE requiring `next_run_at IS NOT NULL`), and WR-01, WR-02, WR-03, WR-08 are fixed in the current code with regression tests. Earlier verified truths (atomic claim, partial unique overlap index, restart recovery, user scoping/IDOR, headless allowlist) show no regression. No open gaps; the phase goal is achieved.

---

_Verified: 2026-09-26_
_Verifier: Claude (gsd-verifier)_
