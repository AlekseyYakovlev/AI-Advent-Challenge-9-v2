---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 03
subsystem: rag
tags: [rag, history, query-condensing, task-memory]
requires:
  - phase: 17-01
    provides: task memory document, render_prompt_lines, stage LLM helpers
provides:
  - condense prompt, trim_answer, validate_condensed, build_condense_messages (agent/rag_rank.py)
  - condense_query LLM stage (agent/rag_llm.py)
  - HistoryContext, PipelineConfig.history, STAGE_HISTORY and the history-aware rewrite stage (agent/rag_pipeline.py)
  - load_history_pairs, prepare_rag_turn(parent_id=...), RagTurn.with_task_memory, IDK_HISTORY_MARKER (agent/rag_turn.py)
affects: [17-05, 17-06, 17-07]
key-files:
  created: [tests/test_rag_history.py]
  modified: [agent/rag_rank.py, agent/rag_llm.py, agent/rag_pipeline.py, agent/rag_turn.py]
requirements-completed: [RCHAT-01, RCHAT-03]
duration: n/a
completed: 2026-10-10
---

# Phase 17 Plan 03: History-aware retrieval Summary

Follow-up questions in a RAG chat are condensed with the last N dialog pairs of the active branch and the task memory into a standalone query; the raw question and the condensed query are both searched and merged by best cosine, with the strict gate unchanged.

## Tasks

1. Condense prompt, validation and pipeline stage - commit 068129a
2. History pairs loader and new `prepare_rag_turn` inputs - commit 79e4d70

## Behaviour

- `validate_condensed` uses absolute caps (200 chars, 30 words), keeps the empty/multi-line/fence/chatty/digit-subset rules and drops the stem-overlap and trailing-question-mark rules; `validate_rewrite` is untouched.
- Pipeline: the stage runs when `config.rewrite or config.history is not None`; with history set, only the condensing call is made (one LLM call even with `rewrite` on). Trace gains `condensed`, `history_pairs`, `config.history`; a rejected output is a skipped `history` stage and retrieval equals the raw-question result.
- `prepare_rag_turn(parent_id=None)` default keeps Phase 15 behaviour; `_history_context` returns None on first turn, with `TASK_MEMORY_ENABLED` false, a missing parent, a load failure (logged `rag_history_load_failed`, rollback on SQLAlchemy errors) or nothing to condense with. `history_turns` is clamped to 0..10 (None means 3).
- `load_history_pairs` walks `Message.parent_id` from the parent, pairs assistant with its user parent, replaces gated/model-idk answers with `IDK_HISTORY_MARKER`, and caps the walk at `2*limit+2` messages.

## Verification (run)

`python -m pytest tests/test_rag_history.py tests/test_rag_turn.py tests/test_rag_ws.py tests/test_rag_pipeline.py tests/test_rag_rank.py tests/test_rag_llm.py -q` -> 196 passed. tests/test_rag_history.py has 44 tests. Existing RAG test files were not edited. `import agent.rag_turn, agent.ws` succeeds. The `ws.py` call site is not wired (plan 17-06), so nothing changes at runtime until `parent_id` is passed.

## Deviations from Plan

- Worktree base was stale (d35a11c); reset to the required c1e5d69 as instructed by the branch check. Not a code deviation.
- `.planning/HANDOFF.json` was modified in the working tree by tooling, not by this plan; it is intentionally not committed.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-17-10..T-17-15 are implemented (tag neutralisation tested, raw question always searched, gate after merge, clamp/trim/token cap, branch-only walk, logs carry counts and error type only).

## Self-Check: PASSED

Created/modified files exist; commits 068129a and 79e4d70 are in git log.
