# Quick 261006-mby: Scheduler backlog 999.7-999.9 Summary

Intent-gated schedule_task, id/title-bound cancel, fold-safe strictly monotonic next_cron_run, and a /ws/events socket that re-validates its session and rejects binary frames.

## Commits
- 942dfe3 fix: gate schedule_task on user intent, bind cancel to the named job (999.7)
- 085bf82 fix: fold-safe strictly monotonic next_cron_run (999.8)
- 6f69c0e fix: /ws/events session recheck (1008) and binary frames (1003) (999.9)
- d98e35f docs: remove backlog 999.7-999.9 from ROADMAP and delete their phase dirs (999.10 kept)

## Verification (actually run)
- Task 1: `pytest tests/test_scheduler_tools.py tests/test_scheduler_providers.py tests/test_tool_guard.py tests/test_tool_guard_ws.py -q` -> 183 passed
- Task 2: `pytest tests/test_scheduler_schedule.py tests/test_scheduler_service.py tests/test_scheduler_runner.py -q` -> 133 passed
- Task 3: `pytest tests/test_scheduler_events.py -q` -> 15 passed
- Full `pytest tests/ -q` -> 1918 passed, 11 skipped, 0 failures (no flaky failures observed)
- ROADMAP has no 999.7/999.8/999.9 entries, their phase dirs are removed, 999.10 present.

## Deviations
- Tests were written together with the implementation rather than as a strict RED-then-GREEN commit sequence; RED was not separately observed.
- [Rule 1] The first events test compared expires_at from before the handshake; the handshake slides expiry (existing behaviour), so the baseline is taken after connect.
- Existing ws test message "run p at the dawn of time" changed to "run p in a minute" so the new schedule gate sees intent.
- Worktree was reset to base 2b579a9 at start (merge-base differed).

## Notes / residual
- A message naming several jobs authorises each (accepted, T-mby-06).
- Chained `*/5` cron across a fall-back night skips the repeated 01:00-01:55 EST hour when started from EDT 01:55 (strictly increasing, as required, but not every repeated slot fires).
- `.planning/HANDOFF.json` shows as modified in the worktree; it is not part of this task and was not committed.

## Self-Check: PASSED
