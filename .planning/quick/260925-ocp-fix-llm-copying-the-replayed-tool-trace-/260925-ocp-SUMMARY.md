---
phase: quick-260925-ocp
plan: 01
subsystem: agent-streaming
tags: [ws, tool-trace, streaming-filter, llm-output]
requires: [quick-260925-nv3]
provides: [TraceLeakFilter, TOOL_TRACE_HEADER in agent/tool_guard.py]
affects: [agent/ws.py, agent/context_engine.py]
key-files:
  created: []
  modified:
    - agent/tool_guard.py
    - agent/context_engine.py
    - agent/ws.py
    - tests/test_tool_guard.py
    - tests/test_tool_guard_ws.py
key-decisions:
  - "Pure streaming filter per stream; drops header and everything after it, plus the whitespace before the header"
  - "TOOL_TRACE_HEADER moved to tool_guard.py (imports only re) and imported by context_engine, no cycle"
metrics:
  tasks: 2
  files: 5
  completed: 2026-09-25
---

# Quick 260925-ocp: Filter model-imitated tool trace from streamed replies

`TraceLeakFilter` (agent/tool_guard.py) drops a copied "[Tool calls actually executed for this reply]" block from every LLM token stream in agent/ws.py, so neither the client nor the DB sees it.

## What was done

- **Task 1** (commits 55400e8, 068a402): added `TraceLeakFilter` (`feed`/`flush`, holds back only a possible header prefix and the whitespace run before it) and moved `TOOL_TRACE_HEADER` into `agent/tool_guard.py`; `agent/context_engine.py` imports it, so `context_engine.TOOL_TRACE_HEADER` still works. 14 new unit tests (chunk sizes 1, 3, word and whole; split header; header at end; mid-text drop; incomplete prefix flushed unchanged; empty chunks; instance independence; single-source constant).
- **Task 2** (commit b5d4b28): added `_emit_filtered` / `_flush_filtered` helpers and wired a separate `TraceLeakFilter()` into all six stream sites (first stream tools and no-tools branches, action-claim retry, post-tool follow-up, rejected-transition re-prompt, invariants justify/retract), each flushed at stream end. Retry keeps its "\n\n" separator, sent only before the first non-empty safe chunk. Added `test_leaked_trace_block_is_not_streamed_or_persisted` (tool call, then follow-up reply with leaked header + trace line: streamed tokens and persisted content both equal the reply text before the header).

## Verification (actually run)

- `pytest tests/test_tool_guard.py tests/test_tool_trace_history.py`: 39 passed.
- `pytest tests/test_tool_guard_ws.py`: 8 passed.
- `pytest tests/`: **529 passed** (514 previous + 15 new), 6 warnings, 133s.
- `grep -c 'TraceLeakFilter()' agent/ws.py` = 6; `grep -v '^#' agent/context_engine.py | grep -c 'TOOL_TRACE_HEADER = '` = 0.
- Not verified: a real LM Studio / qwen run; only mocked SSE streams. I did not confirm that the new WS test fails without the filter (no revert run).

## Deviations from Plan

**1. [Rule 1 - Bug] Malformed escapes in the first RED commit.** The test file committed as the RED step (55400e8) contained literal newlines inside string literals (heredoc escaping mistake), so it failed on a SyntaxError at collection rather than on a missing feature. Rewrote the file correctly in the GREEN commit (068a402). The RED gate therefore failed for a syntax reason plus the missing import, not purely the missing class.

**2. [Rule 3 - Blocking] Worktree base reset.** The worktree HEAD was not at d88e779; reset to it per the branch-check instructions before starting.

Otherwise the plan was executed as written. Retry-site flush is implemented via a small nested helper `send_retry_text` inside `_stream_action_claim_retry` to share the separator rule between the per-chunk and flush paths.

## Known Stubs

None.

## Threat Flags

None. T-ocp-01 and T-ocp-02 are mitigated (filter applied before accumulation and persistence).

## Self-Check: PASSED

- agent/tool_guard.py, agent/ws.py, tests/test_tool_guard.py, tests/test_tool_guard_ws.py contain the changes; commits 55400e8, 068a402, b5d4b28 exist in git log.
