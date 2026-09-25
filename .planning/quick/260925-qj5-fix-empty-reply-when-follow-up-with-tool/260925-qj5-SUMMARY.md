---
phase: quick-260925-qj5
plan: 01
subsystem: agent/ws
tags: [tool-rounds, websocket, local-llm]
requires: [quick-260925-q0s]
provides: "one tools-less retry when a post-tool follow-up with tools is empty"
key-files:
  modified: [agent/ws.py, tests/test_tool_rounds_ws.py]
metrics:
  completed: 2026-09-25
---

# Quick 260925-qj5: Empty follow-up retry Summary

When a post-tool follow-up sent with `tools=` returns no text and no tool_calls, `_run_tool_rounds` now does one extra tools-less follow-up and uses its text as the answer.

## Changes

- `agent/ws.py`: `_ToolRoundsResult.empty_retry_used` flag (caps retry at once per turn); new `_stream_follow_up_with_empty_retry` helper (appends to `acc.text`, logs `tool_followup_empty_retry`, retries via `_stream_follow_up(turn, None)`); `_run_tool_rounds` calls it instead of the direct tools-enabled `_stream_follow_up`. Cap follow-up (`tools=None`) never retries; an empty retry adds no fallback text or error.
- `tests/test_tool_rounds_ws.py`: 4 new WS tests (retry once, empty retry ends cleanly with no 4th request, text follow-up unchanged, round-two empty retry keeps full trace/memory_writes).

## Commits

- 4eb25fa fix(quick-260925-qj5): retry once without tools when a post-tool follow-up is empty
- 2d1a97d test(quick-260925-qj5): cover empty follow-up retry in the tool-round loop

## Verification (observed)

- `pytest tests/test_tool_rounds_ws.py -q`: 9 passed (5 existing + 4 new).
- `pytest tests/ -q`: 542 passed, 0 failed (143.59s).

## Deviations from Plan

None. Worktree base was not eb61253 at start and was reset to it per the worktree check.

## Threat coverage

T-qj5-01: flag caps retry at one; test (b) proves no 4th streaming request. T-qj5-02: retry reuses `_stream_follow_up`, so TraceLeakFilter still applies.

## Self-Check: PASSED
