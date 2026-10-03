---
phase: 09-auto-rename-chats-with-llm-day-21
plan: 01
subsystem: agent
tags: [titles, llm, websocket-events, sanitizing]
requires: []
provides:
  - agent/titles.py title service (schedule_title_generation, DEFAULT_CHAT_TITLE)
  - chat_title_updated frame contract for /ws/events
  - title_tasks registry in agent/state.py
affects: [09-02, 09-03]
tech-stack:
  added: []
  patterns: [fail-open LLM call, conditional UPDATE with rowcount, function-local import to avoid cycle]
key-files:
  created: [agent/titles.py, tests/test_titles.py]
  modified: [agent/state.py, tests/conftest.py, tests/test_cascade_delete.py]
key-decisions:
  - "Title applied via single conditional UPDATE (title = 'New Chat') so non-default titles are never overwritten"
  - "hub imported function-locally in _publish_title to avoid events -> ws -> titles cycle"
requirements-completed: [TITLE-01, TITLE-02, TITLE-03, TITLE-04, TITLE-05, TITLE-06]
duration: 25min
completed: 2026-10-02
---

# Phase 9 Plan 01: Title service Summary

Self-contained chat auto-title module: hardened prompt, output sanitizer, deterministic fallback, race-safe set-once write, owner-only push frame and a cancellable job registry.

## Tasks

1. Pure title logic, registry, tests: 853f558
2. Async job (timeout, fallback, conditional UPDATE, publish, scheduling) and tests: 2565c91

## Verification (observed)

- `pytest tests/test_titles.py tests/test_cascade_delete.py tests/test_scheduler_events.py -q`: 64 passed.
- `python -c "import agent.events; import agent.titles"` and `import agent.main`: exit 0 (no circular import).
- Logger keys in titles.py limited to chat_id, source, length, reason, error_type, error.
- Full suite: see orchestrator note below if not stated here.

## Deviations from Plan

- Worktree base was 64d13b5 rather than the expected b9ee552; reset to b9ee552 per the branch check step.
- Test helper `_create_user` imported from tests.conftest (existing pattern in other tests).

## Known Stubs

None. Nothing calls `schedule_title_generation` yet by design (plan 09-03 hooks it in).

## Self-Check: PASSED
