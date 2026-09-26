---
phase: 08-scheduler-day-18
reviewed: 2026-09-26T00:00:00Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - agent/events.py
  - agent/headless.py
  - agent/main.py
  - agent/schedule.py
  - agent/scheduler.py
  - agent/scheduler_api.py
  - agent/scheduler_ops.py
  - agent/scheduler_schemas.py
  - agent/scheduler_tools.py
  - agent/schemas.py
  - agent/state.py
  - agent/tool_guard.py
  - agent/tools.py
  - agent/ws.py
  - requirements.txt
  - shared/config.py
  - shared/models.py
  - tests/conftest.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 1
  warning: 8
  info: 7
  total: 16
status: issues_found
---

# Phase 08: Code Review Report

**Reviewed:** 2026-09-26
**Depth:** standard
**Files Reviewed:** 20
**Status:** issues_found

## Summary

The scheduler core is generally well built: the guarded-UPDATE slot claim, the partial unique index backstop, user-scoped `get_owned_*` helpers (foreign ids 404), the events hub unsubscribe fix, DOMPurify on the only `innerHTML` sink, and textContent-only rendering elsewhere in `app.js` all hold up. No XSS or IDOR gap was found in the reviewed code.

The problems are concentrated in state-machine edges (pause/resume around an in-flight run), gaps between "claim committed" and "run spawned", unbounded numeric input that escapes as an unhandled exception (including inside the chat tool loop), and a DST fold defect in cron math. No convention-check output is included: the `gsd-tools.cjs` binary could not be located in this environment (`CLAUDE_PLUGIN_ROOT` unset, no plugin cache), so the CONVENTION tier was skipped. No structural (fallow) findings were supplied.

Note on SQLite semantics (checked, not a finding): with the default pysqlite legacy transaction mode a plain SELECT does not open a transaction, so the read-then-write sequences in `claim_slot` and `delete_task` do not hit `SQLITE_BUSY_SNAPSHOT`.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Pause + resume while a run is in flight re-arms an exhausted job (one-shot runs twice, `max_runs` exceeded)

**File:** `agent/scheduler_ops.py:204-251` (interacts with `agent/scheduler.py:128-129`, `agent/scheduler.py:398-411`)
**Issue:** When a run is claimed for a `once` job, or for the final allowed run of a periodic job, `claim_slot` / `start_manual_run` sets `next_run_at = NULL` while the status stays `ACTIVE` until the run finishes. `pause_task` accepts any `ACTIVE` job and the UI shows "Пауза" for it (`task.status === 'active'`). `resume_task` then unconditionally calls `next_run_on_resume`, which for `ONCE` returns `run_at` (already in the past) and for interval/cron returns a fresh future slot, and sets status `ACTIVE`. Consequences:
- A one-shot job that is still running fires again on the next tick (SKIPPED if the first run is still going, a full second execution if it has finished, and the job is never completed).
- A periodic job with `max_runs=N` whose Nth run is in flight gets a new `next_run_at`; `claim_slot` then counts run N+1, so the cap is exceeded, and `finalize_task` never completes it because `next_run_at` is not NULL.
The run window is up to `SCHEDULER_RUN_TIMEOUT` (120 s), long enough for a user to click Pause then Resume.
**Fix:** Make resume refuse (or complete) an exhausted job instead of re-arming it:
```python
async def resume_task(...):
    task = await get_owned_task(session, user_id, task_id)
    if task.status != ScheduledTaskStatus.PAUSED:
        raise SchedulerConflictError(MSG_NOT_PAUSED)
    exhausted = task.next_run_at is None  # only ever NULL while a final run is in flight
    if exhausted:
        raise SchedulerConflictError(MSG_ALREADY_FINISHED)  # finalize_task completes it when the run ends
    ...
```
and/or reject `pause_task` when `task.next_run_at is None`. Add a test: once job, claim, pause, resume, tick -> exactly one run.

## Warnings

### WR-01: A run can be left RUNNING (blocking the job) if anything fails between the claim commit and `spawn_run`; one failing job also aborts the rest of the tick

**File:** `agent/scheduler.py:196-229`, `agent/scheduler.py:434-441`, `agent/scheduler.py:171-194`
**Issue:** `claim_slot` commits the RUNNING `TaskRun`, then `_claim_and_announce` still awaits `session.refresh(task)` and `build_task_out(...)` before `spawn_run`. Likewise `start_manual_run` awaits refresh/`build_task_out` after the commit and before `spawn_run`. Any exception there (DB locked, task deleted concurrently, cancellation during `stop()`) leaves a committed RUNNING row with no asyncio task. The partial unique index then makes every later slot of that job a SKIPPED overlap until the next Agent restart (`recover_orphaned_runs`), and the UI shows it as running forever. In `tick`, an exception from one `_claim_and_announce` also propagates out of the `for` loop, so the remaining due jobs in the batch are not claimed this tick.
**Fix:** Spawn immediately after the commit, before any further awaits, and publish afterwards; wrap the post-commit section so a failure marks the run failed. Isolate per-job failures in `tick`:
```python
run = await self.claim_slot(session, task, now)
if run is not None and run.status == RunStatus.RUNNING and spawn:
    self._run_task_ids[run.id] = task_id
    self.spawn_run(run.id)
...
for task_id in due_ids:
    try:
        run_id = await self._claim_and_announce(task_id, now, spawn)
    except Exception as exc:
        logger.error("scheduler_claim_failed", task_id=task_id, error=type(exc).__name__)
        continue
```
(Keep `asyncio.CancelledError` propagating.)

### WR-02: `delete_task` races the poll loop: a new run can be claimed after the abort and keep executing against a deleted job

**File:** `agent/scheduler_ops.py:276-287`
**Issue:** `delete_task` calls `scheduler.abort_task_runs(task_id)` and only afterwards deletes the row. While the abort is awaiting the cancelled runs (which write their FAILED record), the poll loop can claim a due slot of the same still-ACTIVE job and spawn a fresh run. The subsequent DELETE cascades the run row away, but the spawned coroutine keeps running its LLM/MCP tools (with real side effects) until it finishes or times out, then `_finish_run` finds no row and silently returns. The `task_deleted` frame can also be followed by a late `run_started`.
**Fix:** Take the job out of scheduling before aborting: in one guarded UPDATE set `status=CANCELLED, next_run_at=NULL`, commit, then `abort_task_runs`, then delete. Optionally re-run `abort_task_runs` after the delete for any run spawned in between.

### WR-03: Unbounded schedule inputs escape as unhandled exceptions (REST 500; from the LLM tool path they crash the whole chat turn)

**File:** `agent/schedule.py:104-112,139,192`, `agent/schemas.py:495-520` (`ScheduleTaskArgs`), `agent/scheduler_schemas.py:70-81`, `agent/tools.py:164`
**Issue:** `delay_seconds`, `interval_seconds` and `max_runs` have only lower bounds. `delay_seconds=10**12` or a huge `interval_seconds` makes `now + timedelta(...)` raise `OverflowError`; `max_runs=10**30` raises `OverflowError` at SQLite bind; `run_at="0001-01-01T00:00:00"` or `"9999-12-31T23:59:59"` makes `naive.astimezone()` / `astimezone(utc)` raise `OverflowError`/`OSError` (Windows), and `parse_run_at` only catches `ValueError`/`AttributeError`. None is a `ScheduleValidationError`, so REST returns 500. Worse, `dispatch_tool_calls` does not wrap `TOOL_REGISTRY[name](...)` in try/except and `_dispatch_round` in `agent/ws.py` is outside the `try` in `_run_tool_rounds`, so an LLM-supplied out-of-range value propagates out of `_handle_chat_message`, kills the WebSocket handler (the user message stays persisted without a reply).
**Fix:** Add upper bounds (`le=`) to the Pydantic models (e.g. delay/interval <= 10 years in seconds, `max_runs <= 1_000_000`) and convert range errors in the schedule layer:
```python
try:
    target = as_aware_utc(now) + timedelta(seconds=delay_seconds)
except OverflowError as exc:
    raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc
```
Catch `(ValueError, OverflowError, OSError)` in `parse_run_at`/`_from_local_naive`. Consider a catch-all in `dispatch_tool_calls` that turns handler exceptions into an `ok=False` result.

### WR-04: DST fall-back fold is lost in cron math, producing a slot in the past and a per-second refire loop

**File:** `agent/schedule.py:56-91`
**Issue:** `_to_local_naive` drops `fold`, and `_from_local_naive` re-interprets the naive time with `fold=0` (first occurrence). During the repeated hour on the fall-back night, with `now` in the second pass (e.g. 01:45, fold=1) and a cron like `50 1 * * *`, `next_cron_run` returns 01:50 fold=0, one hour before `now`. The slot is immediately due; after the claim `next_run_after_claim` computes the same past instant again, so the job re-fires (or writes a SKIPPED row) every poll tick for up to an hour, inflating `run_count`, exhausting `max_runs`, and flooding `taskrun`. Only affects machines in a DST-observing zone, hence WARNING rather than BLOCKER.
**Fix:** Guarantee strict monotonicity:
```python
local_next = next(CronSim(expr, local_after))
candidate = _from_local_naive(local_next, tz)
while candidate <= as_aware_utc(after_utc):
    local_next = next(CronSim(expr, local_next))
    candidate = _from_local_naive(local_next, tz)
return candidate
```
(with the existing exception mapping) and add a test with a fold-crossing `now`.

### WR-05: The cancel gate is a keyword heuristic that is not bound to the task or to a real instruction

**File:** `agent/scheduler_tools.py:132-172`, `agent/tool_guard.py:106-127`
**Issue:** `cancel_scheduled_task` passes when the latest user message merely contains a cancel/stop/delete/удали word not directly preceded by a negation. A question or hypothetical ("How do I cancel a job?", "should I stop the 5 min job?") satisfies it, and the gate never verifies the message relates to scheduled jobs or to the `task_id` the model supplies, so a single "cancel the report job" message lets the model cancel any (or several) of the user's jobs in one turn. Cancellation is soft/recoverable only by recreating the job.
**Fix:** Tighten the gate (reject messages ending in `?`; require the message to mention scheduling terms such as задани/job/task/расписан, or the numeric id/title of the target) and limit the tool to one cancellation per turn. At minimum document the residual risk.

### WR-06: `schedule_task` is ungated and persists an unattended prompt that runs with all of the user's MCP tools

**File:** `agent/scheduler_tools.py:50-95`, `agent/headless.py:143-147`
**Issue:** Any model output (including one steered by tool results, file contents or web/GitLab text read through MCP in the chat) can call `schedule_task` without user confirmation, storing an arbitrary prompt that later runs headless with filesystem/GitLab MCP tools, no user present and no confirmation (the allowlist restricts only built-ins, MCP tools are unrestricted). This turns a transient prompt injection into a persistent, delayed action. The asymmetry with the deliberately gated cancel tool is notable.
**Fix:** Require an explicit intent gate for creation as well (reuse the latest-user-message check with scheduling keywords), surface created jobs prominently, and consider an opt-in per-job flag for MCP tool access.

### WR-07: `/ws/events` never re-validates the session after the handshake

**File:** `agent/events.py:84-121`
**Issue:** The session cookie is checked once. After logout, session expiry or user removal the socket stays subscribed and keeps receiving the user's job titles, prompts and run results until the browser closes it (the client redirects on logout, but another tab or a non-browser client is unaffected). The idle `receive_text` loop with `continue` on timeout never re-checks.
**Fix:** On each receive timeout (and/or every N minutes) re-run `get_current_user_ws`; close with code 1008 when it returns `None`. Also close the user's event sockets from the logout handler.

### WR-08: `start_manual_run` does not guard status inside the transaction

**File:** `agent/scheduler.py:401-441`, `agent/scheduler_ops.py:290-301`
**Issue:** `run_task_now` checks the status on an ORM object, but `start_manual_run` issues `UPDATE ... WHERE id = :id` with no status condition. If the job is cancelled (or completed) between the check and the update, a run is still created and executed, `run_count` is incremented and the cancelled job runs one more time. `run_count + 1 >= max_runs` is likewise evaluated from the stale ORM value.
**Fix:** Add `ScheduledTask.status.in_((ACTIVE, PAUSED))` to the UPDATE's WHERE and treat `rowcount != 1` as a conflict (rollback, raise `SchedulerConflictError(MSG_ALREADY_FINISHED)`); insert the run only after the guarded update succeeds.

## Info

### IN-01: `list_scheduled_tasks` sorts by `created_at.timestamp()` on naive datetimes

**File:** `agent/scheduler_ops.py:193`
**Issue:** SQLite returns naive datetimes that represent UTC; `.timestamp()` on a naive value interprets it as local time. Ordering stays consistent except across DST changes, but it is inconsistent with the `as_aware_utc` use two lines below.
**Fix:** `as_aware_utc(row.created_at).timestamp()`.

### IN-02: Duplicated constants and helpers

**File:** `agent/schemas.py:27-28` vs `agent/schedule.py:11-12`; `agent/scheduler_tools.py:19` vs `agent/scheduler_ops.py:40`; `agent/scheduler.py:87-97` vs `agent/scheduler.py:66-73`
**Issue:** Title/prompt max lengths are defined twice, `_LIVE_STATUSES` twice (set vs tuple), and the "has running run" query twice. They can drift.
**Fix:** Define once (e.g. in `agent/schedule.py`/`shared.models`) and import.

### IN-03: `RecordingSink.frames` accumulates data nobody reads

**File:** `agent/headless.py:64-73`
**Issue:** Every non-token frame (including tool_call previews) is appended and never consumed; dead state for the life of the run.
**Fix:** Make `send_json` a no-op, or expose/consume the frames.

### IN-04: Events UI can show stale state and drops keyboard focus on every render

**File:** `ui/static/app.js:1970-1989, 2191-2216, 2565-2595`
**Issue:** A `loadSchedulerTasks` response that was requested before an event but resolves after it overwrites the newer event-driven state (polling is disabled while the socket is open, so it stays stale until the next event or fold click). Each event also rebuilds the whole panel (`replaceChildren`), losing focus on the active button and re-fetching all expanded histories. A dropped hub frame (full queue) is likewise never healed while the socket is open.
**Fix:** Ignore responses older than the last applied event (sequence counter), patch cards in place or restore focus, and re-sync on `visibilitychange`.

### IN-05: A binary frame on `/ws/events` raises `KeyError` from `receive_text()`

**File:** `agent/events.py:104-111`
**Issue:** Starlette's `receive_text` indexes `message["text"]`; an authenticated client sending a binary frame triggers an unhandled `KeyError`, ending the socket with 1011 and a traceback.
**Fix:** Use `receive()` and ignore non-text messages, or catch `KeyError`.

### IN-06: Headless runner is coupled to private `agent.ws` helpers and duplicates a query

**File:** `agent/headless.py:19,182-201`, `agent/headless.py:141-142`
**Issue:** It calls `ws._ToolRoundsResult`, `_stream_follow_up_with_empty_retry`, `_pick_nudge`, `_stream_nudge`, `_run_tool_rounds` and `ws._ToolTurn` (with `SimpleNamespace` standing in for `Chat`/`MessagePayload`/`WebSocket`, contrary to the annotations). Any refactor of ws.py breaks scheduled runs silently at runtime. It also calls `mcp_config.list_servers` and then `build_mcp_toolset`, which lists servers again.
**Fix:** Promote the tool-loop helpers to a public module (e.g. `agent/tool_loop.py`) with a small sink protocol; reuse the server rows.

### IN-07: Any built-in `TimeoutError` is reported as the run deadline

**File:** `agent/scheduler.py:322-325`
**Issue:** `except TimeoutError` cannot distinguish the `asyncio.timeout` deadline from a `TimeoutError` raised inside a tool/MCP call, so the latter is recorded as "Превышено время выполнения (120 с)".
**Fix:** Bind the timeout context (`async with asyncio.timeout(...) as cm`) and check `cm.expired()` before choosing the message.

---

_Reviewed: 2026-09-26_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
