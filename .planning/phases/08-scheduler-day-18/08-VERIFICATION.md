---
phase: 08-scheduler-day-18
verified: 2026-09-26T00:00:00Z
status: gaps_found
score: 5/5 must-haves verified (SC2 carries one known edge-case defect, CR-01)
has_blocking_gaps: false
overrides_applied: 0
re_verification: null
gaps:
  - truth: "Once / interval / cron jobs fire exactly once per slot; max_runs and one-shot jobs auto-complete (ROADMAP SC2, SCHED-07, SCHED-08) - holds on every normal path, violated by a pause+resume issued while the job's final run is in flight (CR-01)"
    status: partial
    severity: minor
    reason: "claim_slot / start_manual_run set next_run_at=NULL while status stays ACTIVE until the last run finishes. pause_task accepts any ACTIVE job; resume_task (agent/scheduler_ops.py:204-251) then unconditionally calls next_run_on_resume, which re-arms the job (ONCE -> the past run_at; interval/cron -> a fresh future slot). finalize_task no longer finds next_run_at IS NULL, so the one-shot job runs again and never completes, and a max_runs=N job runs N+1 times. Confirmed by reading agent/scheduler_ops.py, agent/schedule.py::next_run_on_resume and agent/scheduler.py::finalize_task. Requires a user to click Pause then Resume within the run window (<= SCHEDULER_RUN_TIMEOUT = 120 s) on a job whose last run is executing. The phase goal (create, run in background, persist, show in UI, restart recovery, scoping) and the demo path are unaffected, so this is judged non-goal-blocking; it is a real correctness defect and should be fixed soon (5-line fix + 1 test)."
    artifacts:
      - path: "agent/scheduler_ops.py"
        issue: "resume_task re-arms a job whose next_run_at is NULL (final run in flight); pause_task does not reject next_run_at IS NULL"
    missing:
      - "resume_task (or pause_task) must refuse when task.next_run_at is None (raise SchedulerConflictError(MSG_ALREADY_FINISHED)) so finalize_task completes the job when the run ends"
      - "Regression test: once job, claim, pause, resume, tick -> exactly one run and status completed; same for max_runs=1 interval job"
deferred: []
human_verification: []
---

# Phase 8: Scheduler (Day 18) Verification Report

**Phase Goal:** A user (or the LLM via chat tools) can schedule delayed (one-shot) and periodic (interval/cron) jobs; the agent runs them in the background, stores each job's status and every run's result, and the UI shows scheduled and completed jobs. Written from scratch in Python inside the Agent process.
**Verified:** 2026-09-26
**Status:** gaps_found (one minor, non-goal-blocking defect: CR-01)
**Re-verification:** No - initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | From chat the LLM creates a job; it appears in the sidebar panel, goes `выполняется` -> `успешно` live, result opens in a Markdown modal | VERIFIED (accepted model limitation) | Tools `schedule_task`/`list_scheduled_tasks`/`cancel_scheduled_task` in `agent/scheduler_tools.py`, imported/registered from `agent/main.py:57`, name tuple in `agent/ws.py:67`. Panel `#scheduler-panel` in `ui/static/index.html:155`; live client `new WebSocket(.../ws/events)` at `ui/static/app.js:2603`; `DOMPurify.sanitize` used 3x in app.js and DOMPurify CDN loaded in index.html:9. Demo steps 2-5 executed end-to-end in a real browser (08-08-SUMMARY, PASS). Caveat recorded by user decision: with local qwen3.5-9b a natural "через минуту ..." phrase can be executed immediately instead of calling `schedule_task`; explicit phrasing works. Model tool-selection behaviour, not a code defect. |
| 2 | Once/interval/cron (local-time cron) fire exactly once per slot; overlaps recorded `skipped`; `max_runs` and one-shot jobs auto-complete | VERIFIED with known edge defect | `SchedulerService.claim_slot` (`agent/scheduler.py:100-170`): guarded `UPDATE ... WHERE id AND status='active' AND next_run_at=old`, rowcount==1 check, run insert in the same commit; SKIPPED on overlap; `new_next=None` when `run_count+1 >= max_runs`; `finalize_task` completes when `next_run_at IS NULL` and no running run. Partial unique index on `status='running'` at `shared/models.py:543-548`. Cron via pinned `cronsim==2.7` in local tz (`agent/schedule.py`). 230 scheduler tests pass (run by verifier). Edge defect: CR-01 (pause+resume while last run in flight) - see gaps. Also WR-04 (DST fold) on DST-observing machines only. |
| 3 | After Agent restart a missed job runs once flagged late; runs interrupted by restart are marked failed | VERIFIED | `recover_orphaned_runs` (`agent/scheduler.py:236-270`) marks RUNNING runs FAILED with `MSG_INTERRUPTED_RESTART` and finalizes exhausted jobs; called in lifespan before `scheduler.start()` (`agent/main.py:396-398`). Catch-up: `is_late = lag > SCHEDULER_LATE_THRESHOLD_SECONDS`, `next_run_after_claim` computes from `now` (no replay). Demo step 9 (hard kill, restart): exactly one run, "с опозданием" chip - PASS. |
| 4 | REST `/api/v1/scheduler/*` and `/ws/events` are user-scoped (404 / owner-only events); LLM cannot cancel unless the user asked | VERIFIED | `get_owned_task`/`get_owned_run` raise not-found for foreign ids (`agent/scheduler_ops.py:121-145`); 11 routes in `agent/scheduler_api.py`; `hub.publish(user_id, ...)` per-user; `ws_events` checks origin + session cookie before accept (`agent/events.py:84-99`, close 1008); cancel gate `user_asked_to_cancel` + required flag in `agent/scheduler_tools.py` / `agent/tool_guard.py:121`, 8+ cancel-gate tests in `tests/test_scheduler_tools.py`. Demo steps 8 and 10 PASS (foreign id 404, 0 leaked frames). WR-05 notes the gate is a keyword heuristic (residual risk, non-blocking). |
| 5 | `pytest tests/ -q` passes including new scheduler tests | VERIFIED | Orchestrator regression gate: 881 passed at HEAD. Verifier re-ran `tests/test_scheduler_*.py`: 230 passed. Test files present: models, schedule, service, runner, api, events, lifespan, tools. |

**Score:** 5/5 truths verified (SC2 with one minor known defect)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `shared/models.py` (ScheduledTask, TaskRun) | Tables, CASCADE/SET NULL FKs, partial unique index | VERIFIED | Index `status = 'running'` at :543-548; `ix_scheduledtask_status_next` :490 |
| `agent/schedule.py` | Pure schedule math, validation, local-tz cron | VERIFIED | 235 lines; build_schedule_spec / initial_next_run / next_run_after_claim / next_run_on_resume |
| `agent/scheduler.py` | Poll loop, claim, recovery, executor, timeout | VERIFIED | 493 lines, wired in lifespan |
| `agent/headless.py` | Headless LLM+MCP runner, allowlist | VERIFIED | `HEADLESS_TOOL_ALLOWLIST = {"save_long_term_memory"}` + user's MCP tools |
| `agent/scheduler_ops.py` / `scheduler_api.py` / `scheduler_schemas.py` | Scoped ops + REST | VERIFIED | router included in `agent/main.py:416` |
| `agent/scheduler_tools.py` | 3 LLM tools with cancel gate | VERIFIED | registered via import at `agent/main.py:57` |
| `agent/events.py` | Per-user EventHub + `/ws/events` | VERIFIED | route at `agent/main.py:1246` |
| `ui/static/index.html`, `ui/static/app.js` | Panel, forms, modal, live client | VERIFIED | vanilla JS, CDN libs only |
| `requirements.txt` cronsim pin | dependency | VERIFIED | `cronsim==2.7` |
| `shared/config.py` SCHEDULER_* | settings | VERIFIED | 7 settings (:26-38) |
| docs (API_SPEC, ARCHITECTURE, TESTING_GUIDE) | doc sync | VERIFIED per SUMMARY 08-08 (commit 2b626e3) |

### Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `agent/main.py` lifespan | `scheduler.recover_orphaned_runs()` / `start()` / `stop()` | lines 396-401, gated by `SCHEDULER_ENABLED` | WIRED |
| `agent/scheduler.py::execute_run` | `run_headless_turn` | under `asyncio.timeout(SCHEDULER_RUN_TIMEOUT)` + semaphore | WIRED |
| scheduler / ops | `hub.publish(user_id, frame)` | run_started/run_finished/task_updated/task_deleted | WIRED |
| REST/tools | `scheduler_ops` shared functions | same validation path for UI and LLM | WIRED |
| `app.js` | `/api/v1/scheduler/*`, `/ws/events` | apiFetch + WebSocket | WIRED |

### Data-Flow Trace (Level 4)

| Artifact | Data | Source | Real Data | Status |
|----------|------|--------|-----------|--------|
| Sidebar panel task cards | `state.schedulerTasks` | `GET /api/v1/scheduler/tasks` -> `list_scheduled_tasks` (SQL select on `ScheduledTask` filtered by user_id) + WS events | Yes | FLOWING |
| Run modal | run result | `GET /api/v1/scheduler/runs/{id}` -> `TaskRun.result_text` written by `_finish_run` from real headless output | Yes (demo step 5) | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Scheduler test suite | `python -m pytest tests/test_scheduler_*.py -q` | 230 passed, 5 warnings | PASS |
| Full suite | orchestrator-run | 881 passed | PASS (not re-run) |
| Real E2E demo (LM Studio + filesystem MCP, Playwright) | per 08-08-SUMMARY steps 1-10 | all pass, accepted limitation noted | PASS (relied on, not redone) |

### Probe Execution

Step 7c: SKIPPED (no probes declared by any PLAN).

### Requirements Coverage

Every ID SCHED-01..SCHED-14 is declared in PLAN frontmatter (08-01: 01,06,07,09,14; 08-02: 12,14; 08-03: 05,06,14; 08-04: 03-09,14; 08-05: 01,08,09,10,14; 08-06: 02,11,14; 08-07: 12,13; 08-08: all 14) and mapped to Phase 8 in REQUIREMENTS.md. No orphaned requirements.

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|----------------|--------|----------|
| SCHED-01 create job via UI/REST, validation in Russian | 01, 05, 08 | SATISFIED | `create_scheduled_task`, POST /tasks, `MSG_*` Russian messages. Note WR-03: no upper bounds on numeric inputs (500 on overflow) |
| SCHED-02 LLM tools + origin_chat_id SET NULL | 06, 08 | SATISFIED | `scheduler_tools.py`; `test_chat_delete_keeps_job_with_null_origin` |
| SCHED-03 background poll loop, atomic claim, survives restart | 04, 08 | SATISFIED | `claim_slot`, `_run_loop`, demo step 9 |
| SCHED-04 catch-up once with is_late; orphan recovery | 04, 08 | SATISFIED | `recover_orphaned_runs`, `is_late` |
| SCHED-05 headless turn, allowlist, MAX_TOOL_ROUNDS, timeout, model down -> failed | 03, 04, 08 | SATISFIED | `headless.py`, `execute_run`, `test_scheduler_runner.py` |
| SCHED-06 TaskRun stores status/trigger/timings/late/result/error/trace; nothing in chat | 01, 03, 04, 08 | SATISFIED | `TaskRun` fields, `_finish_run` |
| SCHED-07 overlap -> skipped, DB-enforced, no retries | 01, 04, 08 | SATISFIED | partial unique index + SKIPPED path |
| SCHED-08 max_runs / one-shot auto-complete | 04, 05, 08 | SATISFIED on normal paths; violated by CR-01 edge | `finalize_task`; see gap |
| SCHED-09 5-field cron in machine-local tz; UTC storage/API | 01, 04, 05, 08 | SATISFIED | `agent/schedule.py`; WR-04 DST fold caveat |
| SCHED-10 REST routes, user-scoped, 404 for foreign ids | 05, 08 | SATISFIED | 11 routes + 401/404 tests |
| SCHED-11 cancel gate | 06, 08 | SATISFIED (heuristic, WR-05) | `user_asked_to_cancel` + flag + tests |
| SCHED-12 `/ws/events` origin+cookie, owner-only frames | 02, 07, 08 | SATISFIED | `agent/events.py`; WR-07 (no session re-check after handshake) |
| SCHED-13 sidebar panel per UI-SPEC | 07, 08 | SATISFIED | index.html/app.js, demo steps 1-7 |
| SCHED-14 pytest coverage | 01-06, 08 | SATISFIED | 230 scheduler tests, 881 total |

Bookkeeping: `.planning/REQUIREMENTS.md` still shows SCHED-01..14 as `[ ]` / "Pending" in the checklist and traceability table; the orchestrator should flip them to Complete (verifier does not modify tracking files beyond this report).

### Anti-Patterns Found

No `TBD`/`FIXME`/`XXX` markers in phase-modified scheduler files (grep clean). No stubs found; all artifacts substantive and wired.

### Code Review Findings (advisory, 08-REVIEW.md)

| ID | Severity here | Summary |
|----|---------------|---------|
| CR-01 | minor gap (see frontmatter) | Pause+resume during final in-flight run re-arms an exhausted job. Confirmed by verifier. Judged non-goal-blocking: needs a deliberate UI race inside a <=120 s window, all phase success criteria and the demo pass on normal paths. Fix is small; recommend closing it before the next milestone step (`/bm:code-review-fix` or backlog). If the developer prefers strictness (a one-shot job running a second time can repeat side-effecting MCP calls) it can be escalated to blocking. |
| WR-01 | non-blocking | RUNNING run can be left blocking a job if an exception occurs between claim commit and spawn; one failing job aborts the tick batch (recovered on next restart) |
| WR-02 | non-blocking | delete_task races poll loop (new run claimed after abort) |
| WR-03 | non-blocking | Unbounded delay/interval/max_runs/run_at -> OverflowError; REST 500, and from the LLM tool path can kill the chat WS turn |
| WR-04 | non-blocking | DST fall-back fold in cron math (DST zones only) |
| WR-05 | non-blocking | Cancel gate is a keyword heuristic, not bound to the task |
| WR-06 | non-blocking | `schedule_task` ungated; persisted prompt runs with all MCP tools (prompt-injection persistence) - design/security follow-up |
| WR-07 | non-blocking | `/ws/events` never re-validates session after handshake |
| WR-08 | non-blocking | `start_manual_run` lacks in-transaction status guard |
| IN-01..07 | info | as listed in 08-REVIEW.md |

WR-01..WR-08 do not break any ROADMAP success criterion; WR-03 and WR-06 are the highest-value follow-ups (robustness of the LLM tool path and prompt-injection hardening).

### Human Verification Required

None outstanding. The end-to-end browser/real-model demo was already executed (08-08-SUMMARY); the local-model tool-selection limitation was accepted by the user.

### Gaps Summary

The phase goal is achieved: models, poll loop with atomic claim, headless runner, REST, LLM tools, `/ws/events`, and the sidebar UI all exist, are substantive, wired and data-flowing; restart recovery, per-user scoping and the cancel gate are implemented and tested (230 scheduler tests, 881 overall, real demo pass). One open defect (CR-01) breaks "exactly once / auto-complete" only in a narrow pause+resume-during-final-run race; it is recorded as a minor (non-goal-blocking) gap, so `has_blocking_gaps: false` and it routes to backlog by default.

---

_Verified: 2026-09-26_
_Verifier: Claude (gsd-verifier)_
