# Deferred items (phase 08)

## Flaky: tests/test_scheduler_events.py::test_events_ws_unsubscribes_on_close (from 08-02)

- Found during 08-04 full-suite runs: fails intermittently (`assert 1 == 0` from `_wait_for_no_subscribers`): after the client closes the socket the server-side subscription is still present at the end of the 1 s polling window and only disappears at lifespan shutdown.
- Not caused by 08-04: reproduced on the base `agent/main.py` (bb5640d) with `pytest tests/test_cors.py tests/test_scheduler_events.py` (3 failures in 8 runs) and on HEAD (1 in 4). Passes when the events file runs alone.
- Likely cause to investigate: server-side disconnect detection latency in `agent/events.py::ws_events` (receive loop / pump task) under `TestClient`, versus the test's fixed 1 s window.

**RESOLVED (orchestrator, after wave 3):** root cause was in `agent/events.py::ws_events`, not the test. `TestClient.__exit__` sends the disconnect and then cancels the app task's anyio scope; cancellation is level-triggered, so the `await asyncio.gather(pump, ...)` in `finally` re-raised `CancelledError` before `hub.unsubscribe(...)` ran, leaking the queue (same on any real task cancellation). Fix: `hub.unsubscribe` now runs first in `finally`, before any await. 0 failures in 15 runs of `tests/test_cors.py tests/test_scheduler_events.py` (previously ~1 in 3).

## RESOLVED (orchestrator, wave 4 gate): test_loop_runs_due_job_and_stop_ends_it teardown PermissionError (from 08-04)

- Symptom: `ERROR ... clean_test_db` teardown, `PermissionError: [WinError 32]` on `test_app.db`; ~25-50% of runs, also on pre-wave-4 commits (so not caused by 08-06/08-07).
- Root cause: the test waited for `RunStatus.SUCCESS`, which is committed early in `SchedulerService._finish_run`; the method keeps querying afterwards (finalize, refresh, build frame). `svc.stop()` then cancelled the run mid-query and orphaned an aiosqlite connection that kept the DB file locked on Windows. Not a scheduler defect (cancelling at shutdown is expected); the test sequencing was wrong.
- Fix: the test now also waits for `svc._runs` to drain before `stop()`. 0 errors in 30 runs (was ~6/12). A teardown retry in conftest was tried and did not help, so it was not kept.
