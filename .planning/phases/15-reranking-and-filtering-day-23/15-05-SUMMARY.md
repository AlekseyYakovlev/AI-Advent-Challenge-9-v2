---
phase: 15-reranking-and-filtering-day-23
plan: 05
subsystem: rag-chat-turn
tags: [rag, payload-v2, trace, below-threshold]
requires: ["15-04"]
provides:
  - agent.rag.PAYLOAD_VERSION 2, NO_FRAGMENTS_INSTRUCTION, merge_no_fragments_note, VERDICT_OFF, VERDICT_KB_UNAVAILABLE
  - prepare_rag_turn(..., client, model) driven by run_retrieval_pipeline
affects: [15-06]
key-files:
  modified: [agent/rag.py, agent/rag_turn.py, agent/ws.py, tests/test_rag.py, tests/test_rag_turn.py, tests/test_rag_ws.py]
key-decisions:
  - "All-cut case sends a no-fragments note in the outbound user message; no block, no warning, verdict below_threshold (D-09)"
  - "Stage skips never produce a warning; visible only in search.skipped (D-14)"
requirements-completed: [RANK-01, RANK-03, RANK-05, RANK-06]
duration: ~30min
completed: 2026-10-03
---

# Phase 15 Plan 05: Pipeline-driven chat turn Summary

Chat turns now run the two-stage retrieval pipeline with the chat's stored settings and the answer's own provider client and model, and store and send payload v2 (v1 keys plus `verdict` and a metadata-only `search` trace) in `Message.rag_sources` and `done.rag`.

## Tasks

| Task | Commit |
|------|--------|
| 1: payload v2, no-fragments note, pipeline-driven prepare_rag_turn, over_budget marking | 3b72399 |
| 2: client/model passed from ws.py, ws tests for trace, below-threshold, stage skips | 78748dd |

## Verification (actually run)
- `pytest tests/test_rag.py tests/test_rag_turn.py tests/test_rag_pipeline.py tests/test_rag_api.py`: 106 passed.
- `pytest tests/test_rag_ws.py`: 12 passed.
- Full suite `pytest tests/ --ignore=tests/test_kb_real_pdfs.py`: 1625 passed, 1 skipped.
- Signature check for prepare_rag_turn parameters: exit 0. `grep -c "prepare_rag_turn(" agent/ws.py` = 1 (no new call sites).

## Deviations from Plan

**1. [Rule 3 - Blocking] Existing tests patched `rag_turn.retrieve`.** Since the import was removed as planned, those tests (mode-off, failure, unexpected error, cancellation, empty result) now patch `rag_turn.run_retrieval_pipeline`; the empty-result stub returns `([], trace)`. Assertions themselves were not weakened.

**2. Test helper changes.** `_set_config` / `_open_rag_chat` in test_rag_ws.py accept config flags (used via `functools.partial` because the portal `call` takes no kwargs). Rewrite/rerank ws tests mock the stage calls at the HTTP level with respx (stream false -> JSON/500) rather than a scripted fake client, since ws uses the real client object against a respx-mocked endpoint.

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model. Seeded chunk text absence from stored JSON is asserted; `_log` logs ids and counts only.

## Self-Check: PASSED
Commits 3b72399 and 78748dd exist; modified files exist.
