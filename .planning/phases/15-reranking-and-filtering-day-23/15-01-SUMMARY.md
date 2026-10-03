---
phase: 15-reranking-and-filtering-day-23
plan: 01
subsystem: rag
tags: [rag, rerank, fts5, rrf, calibration]
requires: []
provides:
  - "agent/rag_rank.py: 14 pure ranking/validation/prompt/calibration functions"
affects: [15-03, 15-04, 15-06]
tech-stack:
  added: []
  patterns: ["pure stdlib-only module", "tag neutralising for prompt data"]
key-files:
  created: [agent/rag_rank.py, tests/test_rag_rank.py]
  modified: []
requirements-completed: [RANK-02, RANK-03, RANK-04, RANK-05, RANK-07]
duration: 20min
completed: 2026-10-03
---

# Phase 15 Plan 01: Pure ranking helpers Summary

Stdlib-only `agent/rag_rank.py` with lexical scoring, cosine/lexical fusion, RRF (k=60), injection-safe FTS5 query builder, rewrite validator, rewrite/rerank prompt builders, strict rerank reply parser and the calibration threshold rule.

## Tasks

1. Tokenising, lexical scoring, fusion, RRF, FTS query builder, calibration: commit 5880b5e
2. Rewrite validator, prompt builders, rerank parser: commit 4af50c1

## Verification (observed)

- `python -m pytest tests/test_rag_rank.py -q`: 56 passed.
- Acceptance one-liners for validate_rewrite, parse_rerank_scores, build_rerank_messages, build_fts_query, choose_threshold: all passed.
- Module imports nothing from agent/, shared/ or scripts/ (grep empty).
- Full-suite run: see final report.

## Deviations from Plan

- [Rule 1 - Bug] `choose_threshold` half-up rounding: `floor(0.585*100+0.5)` yields 0.58 due to float error; value is rounded to 6 places before flooring so the midpoint gives the required 0.59.
- Worktree base was corrected with `git reset --hard` to the expected phase base before starting.

## Known Stubs

None.

## Self-Check: PASSED
