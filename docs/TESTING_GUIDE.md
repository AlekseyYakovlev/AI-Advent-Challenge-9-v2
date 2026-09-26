# Testing Guide

## Required Tests
- test_database.py: WAL, retry_on_locked_db, CASCADE
- test_settings_fallback.py: global → per-chat inheritance
- test_cascade_delete.py: delete chat → cleanup in-memory caches
- test_lm_studio_client.py: load/unload/timeout scenarios
- test_concurrent_ws.py: 5 parallel WS messages, no IntegrityError
- test_ws_security.py: origin validation, rate limiting, idle timeout
- test_supervisor.py: agent crash → restart within 5 seconds
- test_scheduler_*.py: see "Scheduler (Day 18)" below

## Fixtures
- Use conftest.py for shared fixtures
- Separate test_app.db for each test session
- respx for HTTP mocking

## Strategy Tests

### test_no_compression_blocks_on_overflow
- Set strategy to "no_compression"
- Set context_length to 100
- Send 10 long messages (200+ tokens each)
- Verify ContextOverflowError is raised
- Verify user message is deleted from database
- Verify error sent to WebSocket with code "CONTEXT_OVERFLOW"

### test_sliding_window_preserves_database
- Set strategy to "sliding"
- Set context_length to 500
- Send 20 long messages
- Verify all 20 messages preserved in database
- Verify only recent messages sent to LLM (check logs)

### test_sticky_facts_creates_summary
- Set strategy to "sticky"
- Set context_length to 500
- Send 20 long messages
- Verify all messages preserved in database
- Verify summary_text field populated in settings
- Verify summary is in English (check logs)

### test_truncate_middle_preserves_structure
- Set strategy to "truncate_middle"
- Set context_length to 500
- Send 25 messages
- Verify all 25 messages preserved in database
- Verify first 5 and last 10 messages sent to LLM
- Verify middle summary generated (check logs)

### test_strategy_change_unblocks_input
- Set strategy to "no_compression" with small context_length
- Trigger overflow
- Verify input blocked
- Change strategy to "sliding"
- Verify input unblocked
- Verify new messages work normally

### test_statistics_endpoint
- Create chat with messages
- Call GET /api/v1/chats/{chat_id}/stats
- Verify all fields present
- Verify current_context_size reflects strategy
- Verify context_usage_percent calculated correctly

## Scheduler (Day 18)

`tests/conftest.py` sets `SCHEDULER_ENABLED=false`, so no poll loop runs during tests. Service tests
call `SchedulerService.tick(now)` (optionally with `spawn=False`) with an explicit clock; runs are
executed by calling `execute_run` with `run_headless_turn` patched, or by driving the headless
runner against a `respx`-mocked LM Studio. Never wait for wall-clock ticks.

### test_scheduler_schedule.py (pure schedule math)
- Cron: exactly five fields accepted and whitespace-normalized; 6 fields, garbage and over-long
  expressions rejected; next slot is strictly after the reference time in local wall time.
- Interval: future anchor kept; after downtime exactly one next slot on the original cadence;
  an exact slot moves strictly forward.
- `run_at`: naive means local time, an offset is honoured, garbage rejected, past rejected.
- `build_schedule_spec`: once needs exactly one of delay/run_at; interval minimum enforced;
  `max_runs` >= 1 (and ignored for once); unknown type rejected.
- `next_run_after_claim` (catch-up: interval keeps cadence, cron from now, once -> none) and
  `next_run_on_resume`; module must not use `zoneinfo` or `utcnow`.

### test_scheduler_models.py
- `init_db` creates both tables and the partial index `uq_taskrun_one_running`.
- A second `running` run for the same job is rejected; many `skipped` runs, running runs of
  different jobs, and a new run after the previous finished are accepted.
- Enums are stored as lowercase literals.
- Deleting a chat keeps the job with `origin_chat_id = NULL` (SET NULL); deleting a job deletes its
  runs; deleting a user deletes jobs and runs (CASCADE).

### test_scheduler_service.py (claim, recovery, execution)
- Claim atomicity: two concurrent claims of the same slot yield exactly one run; a stale claim of an
  already consumed slot returns nothing; a racing manual run rolls the claim back and the next tick
  records a skipped run.
- Catch-up: interval and cron jobs overdue after downtime run once, flagged late, and land on the
  next boundary.
- Overlap skip: a slot that comes due while a run is `running` creates a `skipped` run that does not
  count toward `run_count`.
- `max_runs`: exhausts the job (`next_run_at` cleared, then `completed`); skips do not count; a
  paused job with a pending slot is never finalized; inactive jobs are never claimed.
- Recovery: orphaned `running` runs become `failed` with the restart message and exhausted jobs are
  finalized.
- Execution: success persists result and publishes `run_finished`; a headless error, a timeout
  (`Превышено время выполнения (120 с)`) and an unexpected exception each map to a Russian error and
  the next slot still fires; a one-shot job completes after its run.
- Manual run on an interval job keeps the next slot; on a one-shot job it clears it. Aborting a
  job's runs cancels the in-flight task. The loop test waits for `_runs` to drain before `stop()`.
- Events: `run_started` goes only to the owner and carries a fresh task snapshot; datetimes are UTC.

### test_scheduler_runner.py (headless runner)
- Plain answer needs no chat or lock; a tool round saving long-term memory writes for the job owner.
- Only allowlisted built-in tools plus MCP tools are offered; a hallucinated or non-allowed built-in
  tool is rejected; the allowlist does not restrict MCP bindings; without an allowlist chat dispatch
  is unchanged.
- LM Studio unreachable, HTTP error and read timeout (including in a later round) map to Russian
  messages; an empty answer fails; the round cap bounds tool rounds.
- Sampling comes from global settings; a missing settings row uses defaults without creating it; the
  system message carries the preface, clock line and long-term memory.
- MCP unavailable: note appended (or named in the empty-answer error); a toolset failure never fails
  the run.

### test_scheduler_lifespan.py
- Loop stays off when `SCHEDULER_ENABLED=false` but orphaned runs are still recovered.
- Loop runs inside the lifespan when enabled and stops on exit, before MCP cleanup and engine
  disposal.

### test_scheduler_api.py (ops and REST)
- Create: once sets `next_run_at` and origin chat; invalid text fields rejected; per-user cap gives
  409 (Russian message); REST create returns 201 with UTC ISO datetimes; Russian 422 details; 415 for
  a non-JSON body; 403 for a foreign `Origin` on every mutating route.
- Lifecycle: pause keeps `next_run_at` and rejects a second pause; resume restarts interval jobs from
  now and keeps a once `run_at`; cancel is soft and 409 on a finished job; run-now creates a manual
  run, works when paused, 409 when finished or already running; delete aborts in-flight runs and
  removes runs.
- IDOR: another user's job or run gives 404 (never 403) on every route.
- List ordering (live first, then next run, then newest), runs newest first with `limit`, run detail.
- Mutations publish events to the owner only.
- Unauthenticated requests get 401.

### test_scheduler_tools.py (LLM tools)
- Argument validation per schedule type; cancel args require the flag.
- `schedule_task` creates once, interval and cron jobs with the chat's model and origin chat;
  `model_unknown`, `invalid_schedule` and `too_many` error codes.
- `list_scheduled_tasks` returns only the caller's active and paused jobs.
- Cancel gate: blocked when the latest message has no cancel intent, when the flag is false, when the
  chat leaf is not a user message, and without a chat; succeeds only when the user asked; foreign,
  missing and finished jobs give `not_found` / `conflict`; intent detection handles negation.
- Deleting the origin chat keeps the job. The tool list has nine tools; `schedule_task` is not
  counted as a long-term memory write.

### test_scheduler_events.py (`/ws/events` and `EventHub`)
- Rejected with 1008 without a cookie, with a garbage cookie, and with a foreign `Origin`.
- A published frame reaches the owner; users are isolated; every socket of one user gets the frame;
  the subscription is removed on close.
- Hub: delivery only to the user's queues, oldest frame dropped when a queue is full, publish without
  subscribers is a no-op, the user key is removed with the last queue.
