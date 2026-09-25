---
phase: quick-260925-oya
plan: 01
subsystem: context-engine
tags: [openai-tool-calls, tool-trace, context-compression, token-accounting]
requires:
  - phase: quick-260925-nv3
    provides: Message.tool_trace column and serialize_tool_trace (DD-2 kept)
provides:
  - Structured OpenAI-format replay of stored tool traces (assistant tool_calls -> tool -> assistant text)
  - JSON-safe stored/replayed tool arguments
  - Expansion-aware token accounting (usage %, overflow, stats)
affects: [agent/context_engine.py, tests/test_tool_trace_history.py]
key-files:
  modified: [agent/context_engine.py, tests/test_tool_trace_history.py]
key-decisions:
  - "Expansion runs after _apply_compression_strategy, so position-based cuts never orphan a tool message"
  - "Empty stored reply omits the trailing assistant message (assistant(tool_calls) -> tool -> user is valid)"
  - "Tool call ids are call_<message id>_<index>; arguments that are not valid JSON replay as {}"
  - "TOOL_TRACE_HEADER stays imported in context_engine only because tests/test_tool_guard.py asserts the re-export"
metrics:
  tasks: 2
  files: 2
  tests: "533 passed (520 other + 13 in rewritten file); baseline was 529"
---

# Quick 260925-oya: Structured tool-trace replay Summary

Later turns now replay tool usage to the model as OpenAI-format `assistant{content:"", tool_calls}` -> `tool{tool_call_id, content}` -> `assistant{stored text}` groups instead of a text trace appended to the assistant content. This supersedes DD-1 and DD-3 of quick 260925-nv3. DD-2, DD-4/DD-5 and the TraceLeakFilter wiring were not touched.

## What changed

- `agent/context_engine.py`
  - `serialize_tool_trace` stores arguments through `_trace_arguments`: valid JSON at most `TOOL_TRACE_ARGS_CHARS` (raised 200 -> 1000) is kept byte-identical; empty, invalid or over-limit becomes `"{}"`. Results are still cut to 300 chars with the ellipsis.
  - `_load_branch_messages` / `_message_to_dict` return stored content unchanged plus `id` and raw `tool_trace`; token_count of traced assistants includes the expanded extra tokens.
  - `_expand_tool_traces` / `_expand_trace_message` build the tool-call groups. `build_llm_context` calls `_expand_tool_traces(compressed)` after compression. Untraced dicts come out as exactly `{role, content, token_count}`.
  - `_parse_trace_entries` and `_replay_arguments` handle corrupt, nameless and legacy (ellipsis-cut) traces: corrupt/nameless rows replay as plain messages, cut arguments replay as `"{}"`.
  - `_dict_texts` / `_dict_tokens` / `_message_tokens` count content plus tool_calls name+arguments and tool content, over the expanded list, and tolerate `content` of `""`/`None`. `compute_chat_stats` no longer evaluates `count_tokens(msg["content"])` eagerly.
  - `render_tool_trace` deleted.
- `tests/test_tool_trace_history.py` rewritten: scenarios (a) structure and id pairing, (b) no orphan tool for SLIDING and TRUNCATE_MIDDLE with the window start on a traced assistant, (c) untraced turns keep the exact key set, (d) stats/token accounting, (e) legacy/corrupt traces, (f) WS integration checking roles `[user, assistant, tool, assistant, user]` and no `TOOL_TRACE_HEADER` in any message.

## Commits

- 10adb45 fix(quick-260925-oya): replay tool traces as structured tool_calls/tool messages
- e90517c test(quick-260925-oya): cover structured tool trace replay, orphan safety, stats and legacy rows

No Co-Authored-By line in either commit (project memory rule).

## Verification (observed)

- Task 1 automated check (expansion roles, id pairing, `_replay_arguments`, `_dict_tokens` on tool_calls-only dict, `render_tool_trace` count 0): printed `ok`, grep count `0`.
- `pytest tests/test_tool_trace_history.py`: 13 passed.
- Full `pytest tests/ -q`: 533 passed, 0 failed (6 pre-existing warnings). Baseline of 529 included 9 tests in the old version of this file; those were replaced by 13.
- `grep render_tool_trace agent tests`: no matches. `grep "_expand_tool_traces(compressed)"`: 1 match. `git diff --stat 935f011 HEAD -- agent/ws.py agent/tool_guard.py`: empty.
- Not verified: behaviour against a real model (the 8/8 tool-calling figure comes from the plan's earlier measurements, not from this run).

## Deviations from Plan

None affecting behaviour. Minor notes:

- Tasks 1 and 2 were committed as two commits (source, then tests); the plan mentioned a single commit at the end of Task 2. The Task 1 source commit was made while the old test file was stale, so the tree is green only at the second commit.
- In the (b) test the chain length is `RECENT_MESSAGE_COUNT + 5` rather than `+ 4`, so the first element of the recent window is an assistant message (with `+ 4` it lands on a user message).
- Worktree was based on a different commit than the required base; reset to 935f011 per the worktree branch check before any work.
- Expanded dicts still carry `token_count` in the outbound request, same as untraced dicts do today (pre-existing behaviour; ws.py/llm_client not modified).

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Self-Check: PASSED

- agent/context_engine.py and tests/test_tool_trace_history.py exist and are committed (10adb45, e90517c).
