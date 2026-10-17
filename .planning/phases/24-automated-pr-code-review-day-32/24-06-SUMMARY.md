---
phase: 24-automated-pr-code-review-day-32
plan: 06
subsystem: agent
tags: [review, rag, prompts, llm]
requires:
  - phase: 24-02
    provides: parse_line_range
  - phase: 24-03
    provides: PullRequestData, PrFile
provides:
  - agent/review_prompts.py (Russian prompts, neutralise, message builders)
  - agent/review_pipeline.py (plan_parts, build_query, parse_review, sanitize_markdown, run_review, ReviewError)
affects: [24-07]
key-files:
  created:
    - agent/review_prompts.py
    - agent/review_pipeline.py
    - tests/test_review_prompts.py
    - tests/test_review_pipeline.py
key-decisions:
  - "Fragment budget is half of 25% of the context per base; the full text of changed files at head is not fetched"
  - "Tool-less complete_chat_detailed inside generation_gate; module may not import headless/tools/mcp_tools/ws"
requirements-completed: [REV-05]
duration: 40min
completed: 2026-10-14
---

# Phase 24 Plan 06: Review core Summary

Review core: a budget decision between one call and per-part review with a merge call, separate retrieval over the documentation and code bases with their own quotas, Russian three-section output, parsing, sanitising and named error codes.

## Tasks

1. Prompts and pure helpers (commits e743271, f00ad0d): `agent/review_prompts.py`, `plan_parts`, `split_diff_by_file`, `build_query`, `parse_review`, `sanitize_markdown`, `estimate_tokens`. `python -m pytest tests/test_review_prompts.py tests/test_review_pipeline.py -q` gave 25 passed after fixing a test.
2. `run_review` (commit 3bb09af): per-part retrieval for each base, tool-less calls in `generation_gate.slot()`, reasoning-off with retry on 400/422, one repair call, merge level by level, error mapping. `python -m pytest tests/test_review_pipeline.py tests/test_review_prompts.py tests/test_rag_pipeline.py tests/test_rag_llm.py -q` gave 92 passed. The import gate (`grep ... | grep -c`) printed 0.

## Deviations from Plan

- [Rule 1 - Bug, test] The first commit of Task 1 contained a test with a wrong fixture (diff without the second file, context too small for the fixed overhead); fixed in the follow-up commit f00ad0d.
- Tests for `run_review` fake `run_retrieval_pipeline` (monkeypatched in `agent.review_pipeline`) instead of seeding real knowledge bases with `tests/kb_helpers.py`. Retrieval itself is covered by `tests/test_rag_pipeline.py`; the fake lets the tests assert the per-base `PipelineConfig` and the per-part call count directly. The two knowledge bases are plain objects with an `id`.
- Worktree base was behind the expected base; reset to 83ec20a as the startup check prescribes.
- A docs failure in a later part keeps `docs_used=True` if an earlier part used documentation; the note about unavailable documentation is still added.

## Known Stubs

None.

## Not verified

No live model or real embedder was used; all model calls and retrievals are faked. The threshold for the code base (0.0, relying on top-K) is as planned and not measured here.

## Self-Check: PASSED
