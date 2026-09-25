---
phase: quick-260925-q0s
plan: 01
subsystem: agent/ws
tags: [tool-calls, websocket, llm-loop]
requires: []
provides:
  - "Bounded multi-round tool-call loop in the chat turn (MAX_TOOL_ROUNDS = 5)"
affects: [agent/ws.py, agent/tool_guard.py]
tech-stack:
  added: []
  patterns: ["_ToolTurn/_ToolRoundsResult dataclasses; per-round dispatch with aggregated results"]
key-files:
  created: [tests/test_tool_rounds_ws.py]
  modified: [agent/ws.py, agent/tool_guard.py, tests/test_tool_guard_ws.py]
decisions:
  - "Loop detection compares each round's calls with the previous round only (name + canonical JSON args)"
metrics:
  completed: 2026-09-25
---

# Quick 260925-q0s: Multi-round tool-call loop in chat turn Summary

A chat turn now runs up to `MAX_TOOL_ROUNDS` (5) tool rounds. Follow-up streams keep `tools=`, so a model can chain steps (get_file_contents then write_file) instead of emitting raw `<tool_call>` text.

## What changed

- `agent/tool_guard.py`: `MAX_TOOL_ROUNDS = 5`.
- `agent/ws.py`: new `_ToolTurn`, `_ToolRoundsResult`, `_stream_follow_up`, `_call_signatures`, `_dispatch_round`, `_run_tool_rounds`, and the aggregation helpers `_collect_memory_writes`, `_collect_task_writes`, `_send_tool_error_frames`, `_collect_rejected_transitions`, `_reprompt_rejected_transitions`. The inline single-round block in `_handle_chat_message` is replaced by one `_run_tool_rounds` call, and the LLM_ERROR failure path is unchanged.
- The round cap logs `tool_rounds_capped` and finishes with one tools-less follow-up. A call repeated from the previous round is not dispatched: `tool_loop_detected` is logged and one tools-less follow-up ends the turn.
- tool_trace, memory_writes, task_writes, TOOL_ERROR frames and rejected transitions are aggregated over all rounds. The invariants self-critique receives all dispatched calls plus the combined assistant text.

## Commits

- cc0a2ad feat(quick-260925-q0s): multi-round tool-call loop in chat turn
- 081106f test(quick-260925-q0s): cover multi-round tool loop, cap, loop detection, round-2 failure

## Verification (actually run)

- Task 1 verify (`test_tool_guard_ws, test_memory_ws, test_task_ws, test_invariants_ws, test_mcp_chat_ws`): 32 passed.
- `pytest tests/test_tool_rounds_ws.py`: 5 passed.
- Full `pytest tests/ -q`: **538 passed, 0 failed** (533 existing + 5 new), 6 warnings.
- Not verified: a live model run (qwen3.5-9b + MCP); only mocked LLM responses were used.

## Deviations from Plan

**1. [Rule 1 - Test race] Round-2 failure test polls inside the open websocket.** The LLM_ERROR frame is sent before the user message is deleted and committed. Leaving the `with websocket_connect` block cancelled the handler before the delete, so the test polls for user-message removal while the socket is still open (`_wait_for_user_message_removal`). This is a test-timing detail, not a product change; the delete path itself is the pre-existing behavior.

**2. Existing test adjusted as planned.** Only `test_tool_call_turn_never_gets_reminder` (added `assert "tools" in bodies[1]`); no other existing test needed changes.

Behavior (a)-(f) of the plan map to tests: two-round chaining and next-turn replay, cap (MAX_TOOL_ROUNDS patched to 3, also covers aggregation), repeated-call detection, round-2 failure, aggregated TOOL_ERROR ordering; (d) is the adjusted existing test.

## Known Stubs

None.

## Threat Flags

None. All rounds go through the unchanged `dispatch_tool_calls` with the same user scope and bindings.

## Self-Check: PASSED

Files agent/ws.py, agent/tool_guard.py, tests/test_tool_rounds_ws.py exist; commits cc0a2ad and 081106f exist.
