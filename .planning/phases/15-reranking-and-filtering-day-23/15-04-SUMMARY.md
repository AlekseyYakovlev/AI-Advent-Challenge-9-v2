---
phase: 15-reranking-and-filtering-day-23
plan: 04
subsystem: rag-retrieval-pipeline
tags: [rag, rerank, hybrid, threshold, trace]
requires: ["15-01", "15-02", "15-03"]
provides:
  - agent.rag.retrieve_vectors
  - agent.rag_pipeline.PipelineConfig, config_from_row, run_retrieval_pipeline, mark_over_budget and status/verdict/stage constants
affects: [15-05, 15-06]
key-files:
  created: [agent/rag_pipeline.py, tests/test_rag_pipeline.py]
  modified: [agent/rag.py]
key-decisions:
  - "D-08 FTS exemption implemented literally (no keyword gate); effect left for plan 15-09 to present"
  - "stages list is [threshold, ...stages that completed in run order]; an additional stage_ms dict is recorded"
requirements-completed: [RANK-01, RANK-02, RANK-03, RANK-04, RANK-05, RANK-06]
duration: ~25min
completed: 2026-10-03
---

# Phase 15 Plan 04: Retrieval pipeline Summary

One shared `run_retrieval_pipeline` doing candidate fetch, optional rewrite (merged with the original query, best cosine kept), optional FTS5 + RRF hybrid, raw-cosine threshold with FTS exemption, optional lexical fusion, optional single-prompt LLM rerank of the top 10, final top-K, and a metadata-only trace; every optional stage fails soft with a recorded reason.

## Tasks

| Task | Commit |
|------|--------|
| 1: retrieve_vectors, core pipeline (threshold, top-K, trace), config_from_row, mark_over_budget, core tests | 42d2d8a |
| 2: stage tests (rewrite, hybrid, exemption, lexical, LLM, soft-fail) | 2b3eda2 |

## Verification (actually run)
- `python -m pytest tests/test_rag_pipeline.py tests/test_rag.py tests/test_rag_turn.py tests/test_rag_ws.py tests/test_kb_search.py -q`: 86 passed (test_rag_pipeline.py alone: 32 passed). tests/test_rag.py is unmodified.
- Grep gates: `to_thread(lexical_rerank` present once; no line mentions threshold together with lex/rrf/llm_score/fused; PAYLOAD_VERSION still 1.
- Full suite was not run.

## Deviations from Plan

**1. [Process] Stage code landed in the Task 1 commit.** The whole pipeline (including the optional stages) was written in one pass and committed with Task 1; the Task 2 commit contains the stage tests plus a one-line reformat. Behaviour is unaffected.

**2. [Rule 1 - Bug] Test fix.** `test_lexical_order_is_applied` first compared a top-5 list against a reversal of all 20 survivors; fixed by using candidate_k=top_k=5.

**3. Extra reason code.** An unexpected exception inside rewrite/lexical/LLM is recorded as `stage_error` (not in the plan's reason list) so nothing is raised.

## Assumption Drift (advisory)
Planned: a lexical-vs-cosine reorder test with a real article-number match. Actual: lexical ordering is tested by comparing against `lexical_rerank` output and by a monkeypatched reversal, because fake hash embeddings give no controllable cosine spread.

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model.

## Self-Check: PASSED
agent/rag_pipeline.py and tests/test_rag_pipeline.py exist; commits 42d2d8a and 2b3eda2 exist.
