---
phase: 15-reranking-and-filtering-day-23
plan: 03
subsystem: rag-retrieval-primitives
tags: [rag, faiss, fts5, llm-stages]
requires: ["15-01", "15-02"]
provides:
  - agent.kb_search.search_kb_vectors, cosine_for_ids (search_kb unchanged)
  - agent.rag_fts.fts_search, load_chunks_by_ids, FtsQueryError
  - agent.rag_llm.rewrite_query, llm_rerank, StageOutcome
affects: [15-04]
key-files:
  created: [agent/rag_fts.py, agent/rag_llm.py, tests/test_rag_fts.py, tests/test_rag_llm.py]
  modified: [agent/kb_search.py, tests/test_kb_search.py]
key-decisions:
  - "FTS query runs through session.connection() to avoid the SQLModel session.execute deprecation warning"
requirements-completed: [RANK-03, RANK-04, RANK-05]
duration: ~20min
completed: 2026-10-03
---

# Phase 15 Plan 03: Retrieval I/O primitives Summary

Vector search exposing row ids and the normalised query vector (plus cosine lookup by chunk id via faiss reconstruct), KB-scoped bound-parameter FTS5 keyword search, and non-streaming rewrite/rerank stage calls that return a validated value or a reason code.

## Tasks

| Task | Commit |
|------|--------|
| 1: search_kb_vectors, cosine_for_ids | 659c9b0 |
| 2: fts_search, load_chunks_by_ids | 3f16d03 |
| 3: rewrite_query, llm_rerank | ac074f6, fix 8ef3201 |

## Verification (actually run)
`python -m pytest tests/test_rag_llm.py tests/test_rag_fts.py tests/test_kb_search.py tests/test_kb_api.py tests/test_rag.py tests/test_rag_turn.py -q`: 98 passed.
Grep gates checked: no resolve_client/stream_chat/LM_STUDIO in rag_llm.py; no string-built SQL in rag_fts.py. Full suite was not run.

## Deviations from Plan

**1. [Rule 1 - Bug] Rerank test reply format.** Task 3 tests first used a "[N] score" reply, but parse_rerank_scores (plan 15-01) expects "N: score". Tests were fixed in 8ef3201 (the first Task 3 commit contained one failing test; corrected immediately).

**2. [Rule 3] FTS query executed via `session.connection()`** instead of `session.execute` to avoid SQLModel's deprecation warning; behavior is identical.

## Assumption Drift (advisory)
None material.

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model.

## Self-Check: PASSED
Created files exist; commits 659c9b0, 3f16d03, ac074f6, 8ef3201 exist.
