---
phase: 16-citations-and-anti-hallucination-day-24
plan: 03
subsystem: rag
tags: [rag, citations, anti-hallucination, payload]
requires: ["16-01", "16-02"]
provides:
  - "render_rag_block/build_rag_block strict keyword; payload v3 with quote fields"
  - "RagTurn.kept_chunks/strict/reply_text; strict gate in prepare_rag_turn"
  - "finalize_rag_turn(turn, question, assistant_text) -> (answer, turn)"
affects: [16-04, 16-05, 16-06]
key-files:
  modified:
    - agent/rag.py
    - agent/rag_turn.py
    - tests/test_rag.py
    - tests/test_rag_turn.py
    - tests/test_rag_ws.py
key-decisions:
  - "Gate reads only the pipeline verdict and emptiness of chunks (no score); zero candidates also gated"
  - "kept_chunks lives outside payload so chunk text cannot be serialized"
requirements-completed: [CITE-01, CITE-02, CITE-03]
completed: 2026-10-04
---

# Phase 16 Plan 03: Strict gate and finalize Summary

Strict-aware RAG block rendering, payload v3 and a code-level «Не знаю» gate in `prepare_rag_turn`, plus `finalize_rag_turn` that turns a raw answer into a clean answer and a quote-carrying payload.

## Tasks
1. Commit 8768141: `strict` keyword on `render_rag_block`/`build_rag_block`, `PAYLOAD_VERSION = 3`, seven new default-valued payload fields, re-export of `STRICT_INSTRUCTION`/`VERDICT_MODEL_IDK`; Soft instructions untouched.
2. Commit d503e70: `RagTurn` extended, gate, `finalize_rag_turn` (fail-soft, logs counts only), tests (`strict` param on `_chat`, strict default off in WS test helper).

## Verification (actually run)
- `python -m pytest tests/test_rag.py tests/test_rag_turn.py tests/test_rag_ws.py tests/test_rag_pipeline.py tests/test_rag_eval.py tests/test_rag_cite.py -q`: 203 passed.
- Acceptance one-liner: `True False` for strict block; `grep -v '^\s*#' agent/rag_turn.py | grep -c best_cosine` prints 0.
- Not run: the full suite beyond the RAG tests; tests/test_supervisor.py deliberately skipped.

## Deviations from Plan
- Worktree base was off; reset to 317a493 per base check.
- Commit message trailer: the reminder asked for a Co-Authored-By line, but the user's global/project rule forbids it, so none was added.
- Acceptance greps `git diff HEAD -- agent/rag.py` not run literally post-commit; soft instruction text was never edited.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
- Files exist; commits 8768141 and d503e70 exist.
