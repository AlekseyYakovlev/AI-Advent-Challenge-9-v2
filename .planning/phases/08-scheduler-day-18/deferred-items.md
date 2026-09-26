# Deferred items (phase 08)

## Flaky: tests/test_scheduler_events.py::test_events_ws_unsubscribes_on_close (from 08-02)

- Found during 08-04 full-suite runs: fails intermittently (`assert 1 == 0` from `_wait_for_no_subscribers`): after the client closes the socket the server-side subscription is still present at the end of the 1 s polling window and only disappears at lifespan shutdown.
- Not caused by 08-04: reproduced on the base `agent/main.py` (bb5640d) with `pytest tests/test_cors.py tests/test_scheduler_events.py` (3 failures in 8 runs) and on HEAD (1 in 4). Passes when the events file runs alone.
- Likely cause to investigate: server-side disconnect detection latency in `agent/events.py::ws_events` (receive loop / pump task) under `TestClient`, versus the test's fixed 1 s window.

**RESOLVED (orchestrator, after wave 3):** root cause was in `agent/events.py::ws_events`, not the test. `TestClient.__exit__` sends the disconnect and then cancels the app task's anyio scope; cancellation is level-triggered, so the `await asyncio.gather(pump, ...)` in `finally` re-raised `CancelledError` before `hub.unsubscribe(...)` ran, leaking the queue (same on any real task cancellation). Fix: `hub.unsubscribe` now runs first in `finally`, before any await. 0 failures in 15 runs of `tests/test_cors.py tests/test_scheduler_events.py` (previously ~1 in 3).
