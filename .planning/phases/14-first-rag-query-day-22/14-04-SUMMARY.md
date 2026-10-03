---
phase: 14-first-rag-query-day-22
plan: 04
subsystem: rag
tags: [rag, websocket, budget, fail-soft]
requires: ["14-01"]
provides:
  - agent/rag_turn.py RagTurn and prepare_rag_turn (fail-soft, never raises except CancelledError)
  - ws.py chat turn retrieves, merges fragments into the outbound copy only, persists rag_sources, sends done.rag
affects: [14-05, 14-06, 14-08]
tech-stack:
  added: []
  patterns: [single payload serialized for both DB and done frame, post-strategy budget]
key-files:
  created: [agent/rag_turn.py, tests/test_rag_turn.py, tests/test_rag_ws.py]
  modified: [agent/ws.py]
key-decisions:
  - "D-12 ordering: CONTEXT.md D-12 says the budget is applied before the compression strategy; the implementation instead computes it after build_llm_context from the exact used tokens (history, system prompt with suffixes, tool schemas) and merges only into the outbound copy. This satisfies D-12's intent: RAG tokens never enter the strategy or the no_compression overflow check, so RAG can never cause the user message to be deleted, and min(30% context_length, free space) guarantees the request fits. Stricter than a pre-strategy estimate. To be documented in docs/ARCHITECTURE.md by 14-08."
  - "Foreign-owned KB (kb.user_id != chat.user_id) is treated as kb_deleted and reported with kb_id None"
requirements-completed: [RAG-02, RAG-03, RAG-04, RAG-05]
duration: 25min
completed: 2026-10-03
---

# Phase 14 Plan 04: RAG in the chat turn Summary

Each chat turn now loads the chat's RAG config, retrieves, budgets and merges the numbered fragments block into the outbound last user message only, stores a metadata-only rag_sources payload on the assistant message, and returns it as done.rag, degrading to a plain answer with a warning on every failure.

## Tasks

| Task | Commit | Verification (observed) |
|------|--------|-------------------------|
| 1. agent/rag_turn.py + tests | 62947a4 | `pytest tests/test_rag_turn.py`: 11 passed |
| 2. ws.py touch points + WS tests | 8daa08b | `pytest tests/test_rag_ws.py tests/test_rag_turn.py`: 18 passed |

ws.py changes: `rag_sources` keyword on `_persist_assistant_message`; `await prepare_rag_turn(...)` right before `assistant_text = ""` (after system suffix and tool schemas); `rag_sources=rag_turn.sources_json` on the turn's persist call; `"rag": rag_turn.done_payload` in the done frame. No RAG outcome touches the ContextOverflowError or LLM_ERROR branches.

## Verification

- Full suite with `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` deselected: 1404 passed, 1 skipped.
- That supervisor test fails with a timeout in this worktree (it spawns a real agent subprocess on real ports); it was not touched by this plan and was not investigated further. It was seen failing in the first full run only.
- no_compression test accepts either context_full or a shrunk block (context_tokens <= 30% with dropped > 0); which branch triggered was not separately asserted.

## Deviations from Plan

- [Rule 3 - Blocking] Worktree HEAD was not at the expected base; reset to 84ff870 per the startup check before any work.
- Import of `agent.rag_turn` in ws.py sits before `agent.providers` (not strictly alphabetical); cosmetic only.

## Known Stubs

None.

## Self-Check: PASSED
Files agent/rag_turn.py, tests/test_rag_turn.py, tests/test_rag_ws.py exist; commits 62947a4 and 8daa08b exist.
