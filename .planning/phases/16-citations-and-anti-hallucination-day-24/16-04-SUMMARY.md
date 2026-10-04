---
phase: 16-citations-and-anti-hallucination-day-24
plan: 04
subsystem: agent-ws
tags: [rag, citations, websocket, strict-mode]
requires: ["16-03"]
provides:
  - gated strict turns answered without an LLM call
  - post-stream citation processing before persist
affects: [agent/ws.py, tests/test_rag_ws.py]
key-files:
  modified: [agent/ws.py, tests/test_rag_ws.py]
metrics:
  completed: 2026-10-04
---

# Phase 16 Plan 04: WebSocket gate and citation wiring Summary

Strict chats now short-circuit below-threshold turns with the code-built «Не знаю» reply (no streaming LLM request), and strict answers are cut of their «Цитаты:» tail via `finalize_rag_turn` (run through `asyncio.to_thread`) before being stored, with verified quotes in `done.rag` and `Message.rag_sources`.

## What changed
- `agent/ws.py`: new `_complete_gated_turn` (one `token` frame, persist with trace, facts, stats, title, `done` with empty writes); early return in `_handle_chat_message` right after `prepare_rag_turn`, before `active_streams` registration; `finalize_rag_turn` call right before the normal-path persist. No stream filter added, no re-indentation.
- `tests/test_rag_ws.py`: 3 gate tests (zero `stream: true` requests, persisted reply and trace equal to `done.rag`, second message works) and 5 strict-answer tests (exact quote with clean stored content and raw tail in streamed tokens, unverified quote kept plus auto quotes with `done` as the last frame, no-ref answer unsupported, model_idk without quotes, strict off unchanged).

## Verification (observed)
- `python -m pytest tests/test_rag_ws.py -q`: 20 passed.
- `pytest tests/test_rag_ws.py tests/test_rag_turn.py tests/test_rag.py tests/test_rag_cite.py tests/test_concurrent_ws.py tests/test_cascade_delete.py -q`: 157 passed.

## Commits
- 7af7b66: feat(16-04): gate short-circuit and post-stream citation processing

## Deviations from Plan
- [Process] Tasks 1 and 2 were committed together in one commit because both edit the same two files and were implemented and verified in one pass. Behavior and tests for both tasks are covered.
- Worktree base was corrected with `git reset --hard e0b8a79` at startup per the branch check.

## Known Stubs
None.

## Self-Check: PASSED
