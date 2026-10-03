---
phase: 09-auto-rename-chats-with-llm-day-21
plan: 03
subsystem: agent
tags: [titles, websocket, tests]
requires: [09-01]
provides:
  - schedule_title_generation hook in agent/ws.py::_handle_chat_message
  - tests/test_titles_ws.py (14 end-to-end tests)
affects: []
key-files:
  created: [tests/test_titles_ws.py]
  modified: [agent/ws.py]
key-decisions:
  - "Hook placed after compute_chat_stats and before the done send, guarded by chat.title == DEFAULT_CHAT_TITLE and user_msg.parent_id is None"
requirements-completed: [TITLE-01, TITLE-02, TITLE-03, TITLE-04, TITLE-05]
duration: 20min
completed: 2026-10-02
---

# Phase 9 Plan 03: Wire title service into the chat turn Summary

One guarded call in the WebSocket turn makes the first successful turn of a default-titled chat generate its title, proven by 14 end-to-end tests.

## Tasks

1. Hook + trigger-rule tests (5 tests): e23f9f9
2. End-to-end tests over the real job (9 more): 28c9de1

## Verification (observed)

- `pytest tests/test_titles_ws.py tests/test_titles.py tests/test_tool_rounds_ws.py tests/test_invariants_ws.py tests/test_concurrent_ws.py -q`: 73 passed.
- `pytest tests/test_titles_ws.py -q`: 14 passed.
- Full suite `python -m pytest tests/ -q`: 990 passed in 198.86s.
- `import agent.main, agent.events, agent.ws`: exit 0.
- `grep -c "schedule_title_generation(" agent/ws.py` = 1; no `time.sleep` in the new tests.

## Deviations from Plan

- Worktree base was reset to 1bb3e9f per the branch check.
- The plan's multi-line `python -c` structural acceptance assertion (hook order) was not run literally; order verified by reading the diff (hook sits between compute_chat_stats and the done send).

## Known Stubs

None.

## Self-Check: PASSED
