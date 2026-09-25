---
phase: quick-260925-nv3
plan: 01
subsystem: agent
tags: [tool-calling, context-engine, websocket, guard, settings]
requires: []
provides:
  - "Message.tool_trace persisted on assistant rows and replayed in later-turn context"
  - "Tool-use rule appended to the system prompt only when tools are offered"
  - "One-shot corrective re-prompt when a reply claims an action without a tool call"
  - "context_length max 32768, default 16384"
affects: [agent/ws.py, agent/context_engine.py, shared/models.py, shared/database.py]
tech-stack:
  added: []
  patterns: ["compact text replay of tool history (one dict per DB row)"]
key-files:
  created:
    - agent/tool_guard.py
    - tests/test_tool_trace_history.py
    - tests/test_tool_guard.py
    - tests/test_tool_guard_ws.py
    - tests/test_settings_limits.py
  modified:
    - shared/models.py
    - shared/database.py
    - agent/context_engine.py
    - agent/ws.py
    - agent/schemas.py
    - ui/static/index.html
    - ui/static/app.js
    - docs/API_SPEC.md
    - tests/test_stats.py
key-decisions:
  - "DD-1: trace replayed as compact text inside the assistant content, not as OpenAI tool messages, so index-based compression slicing cannot orphan tool messages"
  - "DD-2: nullable Message.tool_trace JSON text, assistant rows only, token_count still counts content only"
  - "DD-3: replay token accounting adds rendered-trace tokens to the history dict"
  - "DD-4: rule appended in ws.py to the system message only inside the tools-offered branch; build_system_prompt unchanged"
  - "DD-5: guard runs after the first stream and before dispatch; exactly one retry, no re-check"
  - "DD-6: existing settings rows intentionally NOT rewritten; only defaults, validators, UI and docs changed"
requirements-completed: [NV3-TRACE, NV3-RULE, NV3-GUARD, NV3-CTXLEN]
duration: n/a
completed: 2026-09-25
---

# Quick 260925-nv3: Stop hallucinated tool actions Summary

Tool calls are now persisted per assistant turn and replayed (truncated) in later context, a short tool-use rule is added when tools are offered, and a bounded one-shot re-prompt catches "done" claims without a tool call; Context Length is capped at 32768 with a 16384 default.

## Commits

- 0ffc4c3: persist tool trace on assistant messages and replay it in later context
- 8bc910b: tool-use rule and one-shot guard for claimed actions without tool calls
- 35a8f6c: context length max 32768, default 16384

## What was built

1. **Trace persistence and replay.** `Message.tool_trace` (nullable TEXT, idempotent `migrate_add_message_tool_trace` called from `init_db`). `serialize_tool_trace` cuts arguments to 200 and results to 300 chars; `render_tool_trace` yields a header line plus `- name(args) -> ok|error: result` lines and returns "" on bad JSON (warning logged). `_load_branch_messages` appends the rendered trace to traced assistant rows and adds its tokens to `token_count`; untraced rows are byte-identical to before.
2. **Rule and guard.** `agent/tool_guard.py` holds `TOOL_USE_RULE`, `ACTION_CLAIM_REMINDER` and the pure `looks_like_action_claim` heuristic (past-tense verb + object noun in a non-question sentence, negation/future rejected). In `_handle_chat_message` the rule is appended to the system message only when tool schemas exist. The guard runs right after the first stream via `_stream_action_claim_retry`; a retry tool call flows through the existing dispatch path; a failed retry logs `action_claim_reprompt_failed` and keeps the original text.
3. **Context length.** `le=32768` on `Settings` and `SettingsUpdate`, default 16384 on the model, ALTER default, `compute_chat_stats` default arg, UI slider (max 32768, value 16384, tick labels 512/16384/32768), JS fallbacks, API docs, and updated `test_stats.py` expectations.

## Verification (observed)

- Task 1 command (`tests/test_tool_trace_history.py test_context_engine test_strategies test_stats test_mcp_chat_ws test_memory_ws`): 46 passed.
- Task 2 command (`test_tool_guard test_tool_guard_ws test_mcp_chat_ws test_memory_ws test_task_ws test_invariants_ws`): 47 passed.
- Task 3, full suite `pytest tests/`: **514 passed, 0 failed** (131.77s). No pre-existing failures observed.
- Plan grep checks: `tool_trace=` and `looks_like_action_claim(` match in agent/ws.py; `le=32768` matches in agent/schemas.py and shared/models.py.
- Not verified: behavior against a real LM Studio / qwen model and the browser UI slider (no live host / GUI here); only mocked-LLM tests and static edits.

## Deviations from Plan

- **[Rule 1 - Bug in my own test]** `test_plain_turn_has_no_trace_and_history_is_unchanged` initially compared history dicts including the pre-existing `token_count` key sent to the provider; adjusted the assertion to compare `role`/`content` only. No product code change.
- **[Minor]** The no-tools WS test cancels pending debounced facts tasks in `finally` to avoid dangling tasks. The retry helper appends the "\n\n" separator to the persisted text only when the retry produced content; tokens streamed before a mid-retry failure are kept (they were already shown to the client).
- Worktree base was `b4f82d1`; reset to the required `a66424a` per the branch check before any work.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-nv3-01 (truncation), T-nv3-02 (single bounded retry, failure caught) and T-nv3-03 (`le=32768` -> 422, tested) are implemented.

## Notes

- Existing `settings` rows in `app.db` were intentionally not rewritten (DD-6): legacy 4096 values, or values above 32768 saved under the old limit, stay as-is; the UI slider clamps them on display and the next save stores <= 32768. `logs/` untouched.
- `total_response_tokens` in stats now includes trace tokens for tool turns only (DD-3).

## Self-Check: PASSED

Files exist (agent/tool_guard.py, tests/test_tool_trace_history.py, tests/test_tool_guard.py, tests/test_tool_guard_ws.py, tests/test_settings_limits.py); commits 0ffc4c3, 8bc910b, 35a8f6c present in `git log`.
