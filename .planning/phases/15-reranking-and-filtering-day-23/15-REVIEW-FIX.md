---
phase: 15-reranking-and-filtering-day-23
fixed_at: 2026-10-03T00:00:00Z
review_path: .planning/phases/15-reranking-and-filtering-day-23/15-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 4
skipped: 1
status: partial
---

# Phase 15: Code Review Fix Report

**Fixed at:** 2026-10-03
**Source review:** .planning/phases/15-reranking-and-filtering-day-23/15-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5
- Fixed: 4
- Skipped: 1

## Fixed Issues

### WR-01: FTS threshold exemption matches article numbers as raw substrings

**Files modified:** `agent/rag_pipeline.py`, `agent/rag_rank.py`
**Commit:** 995585a
**Applied fix:** Renamed `_contains_number` to public `contains_number` and used it in `_earns_fts_exemption` instead of `number in text`. Fixed: requires human verification (logic change).

### WR-02: Rewrite validator rejects legitimate queries starting with "ответ"

**Files modified:** `agent/rag_rank.py`, `tests/test_rag_rank.py`
**Commit:** fd77c64
**Applied fix:** `CHATTY_PREFIXES` now uses `"ответ:"`, `"ответ."` and `"я не "` so "ответственность", "ответчик" and "ответ на ..." are accepted. The existing test case `"Ответ штраф превышение 40"` was changed to `"Ответ: штраф превышение 40"` to match. Fixed: requires human verification (logic change).

### WR-03: Judge loop can abort on non-HTTP failures

**Files modified:** `scripts/rag_judge.py`
**Commit:** cba6630
**Applied fix:** `_judge_one` also catches `ValueError`, `KeyError` and `TypeError`, returning `JUDGE_ERROR`.

### WR-04: FTS5 migration is unguarded

**Files modified:** `shared/database.py`
**Commit:** b96df64
**Applied fix:** `ensure_kb_chunk_fts` call in `init_db` is wrapped in a savepoint and `try/except OperationalError`, logging `kb_chunk_fts_unavailable`. The count-only backfill comparison was not changed (the review marked it as optional).

## Skipped Issues

### WR-05: No overall time bound on the optional LLM stages

**File:** `agent/rag_pipeline.py:118-167, 275-305`, `agent/rag_llm.py:75-78`
**Reason:** Needs a design decision (new overall-budget setting threaded through the pipeline signatures, or a new WS "searching" frame plus frontend handling). Too broad for an automated per-finding fix.
**Original issue:** Worst case of about 30+45+30+45 s of stacked per-stage timeouts with no progress to the client.

## Verification

Full `pytest tests/` run: 1395 passed, 1 skipped, 1 failed (`test_supervisor.py::test_agent_restarts_within_5_seconds`, a timing test under load). It passes on its own both on the baseline and on the fixed code. Remaining test files after it in alphabetical order were run in part (task/tools/ws/titles files: 168 passed).

---

_Fixed: 2026-10-03_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
