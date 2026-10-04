---
phase: 16-citations-and-anti-hallucination-day-24
reviewed: 2026-10-04T00:00:00Z
depth: standard
files_reviewed: 24
files_reviewed_list:
  - agent/rag.py
  - agent/rag_api.py
  - agent/rag_cite.py
  - agent/rag_turn.py
  - agent/ws.py
  - scripts/e2e_rag_cite_playwright.py
  - scripts/e2e_rag_playwright.py
  - scripts/e2e_rag_search_playwright.py
  - scripts/rag_eval.py
  - scripts/rag_judge.py
  - shared/database.py
  - shared/models.py
  - tests/test_database.py
  - tests/test_rag.py
  - tests/test_rag_api.py
  - tests/test_rag_cite.py
  - tests/test_rag_cite_static.py
  - tests/test_rag_eval_cite.py
  - tests/test_rag_judge.py
  - tests/test_rag_report_day24.py
  - tests/test_rag_turn.py
  - tests/test_rag_ws.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 16: Code Review Report

**Reviewed:** 2026-10-04
**Depth:** standard
**Files Reviewed:** 24
**Status:** issues_found

## Summary

Core logic (rag_cite, rag_turn, the gated WS path, the UI quote block) was read in full. The scripts (rag_eval, e2e) and tests were checked only through diffs and grep patterns, not line by line. I found no security defects. The frontend builds quote and source DOM with textContent-style helpers, and quote text is never inserted as HTML. The gate and fail-soft paths are sound. The weaknesses are in how much the "verified" status proves, and a few edge cases in the tail parser.

## Warnings

### WR-01: Trivial quotes get "verified" status, which suppresses the "not supported" warning

**File:** `agent/rag_cite.py:179-191, 388-389`
**Issue:** `match_quote` returns EXACT whenever every ellipsis-separated part of at least 8 characters occurs in some fragment. Text shorter than `MIN_PART_CHARS` is discarded. A quote such as «в базе знаний», or «a ... b» with two common 8-character parts, therefore becomes `exact`. It also rebinds to any fragment containing the phrase (`_verify_line`). One such quote sets `verified=True`, which makes `answer_supported=True` and hides the "ответ не подтверждён фрагментами" warning, so the anti-hallucination signal can be defeated by a model that cites trivially. Dropped short parts also let a quote made only of short parts pass as "no parts" (UNVERIFIED), which is inconsistent.
**Fix:** Require a minimum total normalized quote length (for example `FUZZY_MIN_CHARS`, 25) for EXACT. Alternatively, require the matched text to cover at least N% of the fragment sentence, or mark short exact matches as weak.

### WR-02: Auto-picked quotes are stored as `exact` and rendered as "✓ подтверждена"

**File:** `agent/rag_cite.py:416`, `ui/static/app.js:4296-4300`
**Issue:** `_auto_quotes` creates `Quote(text, STATE_EXACT, rank, auto=True)`. The code picks the sentence by word overlap, so it does not support the model's claim. The UI shows the green "✓ подтверждена" chip ("Цитата дословно найдена в указанном фрагменте") next to "подобрана автоматически". It looks like an answer-level confirmation when the model's quotes all failed. `test_rag_cite` and the E2E scripts likely pin this state, so changing it needs test updates.
**Fix:** Give auto quotes their own state (for example `auto`) or have the UI render a neutral chip when `quote.auto` is true. Do not reuse `exact`.

### WR-03: More than `MAX_QUOTE_LINES` quote lines leak into the visible answer

**File:** `agent/rag_cite.py:250-253, 263-265`
**Issue:** When 10 quote lines are parsed, `resume = index` is set at the first extra line. That line and all following quote lines are then appended to `clean` through `after`. The stored and displayed answer ends with raw «[N] «…»» lines, and `body_refs` counts their `[N]` markers as body references. That inflates `valid`, which can make `supported=True` and skews `cited_ranks`.
**Fix:** After reaching the cap, keep consuming lines that still match `_parse_quote_line` (discard them) and set `resume` only at the first non-quote line.

### WR-04: Existing chat configs silently become strict, and the gated reply path has no failure handling

**File:** `shared/database.py:150`, `agent/rag_turn.py:141`, `agent/ws.py:700-748, 859-863`
**Issue:** The migration adds `strict BOOLEAN DEFAULT 1`, so every existing RAG chat starts returning code-built "Не знаю" replies with no LLM call. That is a behaviour change that may be intended, but it is not opt-in. Separately, `_complete_gated_turn` runs before the stream try/finally. It is not registered in `active_streams`, and `send_json`, persist and `compute_chat_stats` are not guarded. If persist fails, the user message stays committed and the client never gets an error frame or a rollback.
**Fix:** Confirm that the default of 1 for old rows is intended and document it. Wrap the gated send/persist in the same error and rollback handling as the normal path (project convention: `await session.rollback()` on exception).

## Info

### IN-01: `assert` used for control flow in production code

**File:** `agent/rag_cite.py:298`
**Issue:** `assert number is not None` is only a type-narrowing hint and is stripped under `python -O`. Restructure `in_range` so no assert is needed.

### IN-02: Known item, bare «Цитаты:» heading

**File:** `agent/rag_cite.py:229-268`
**Issue:** A «не знаю» reply that ends with an empty heading keeps the heading line in the stored content (already flagged by the 16-08 executor). A related case is `if not clean: return text, parsed`, where a quotes-only answer keeps its quote lines in the visible text.
**Fix:** Strip a trailing empty heading when `parsed` is empty and the text is an idk reply.

### IN-03: `build_rag_payload` quote-related parameters are unused by the producer

**File:** `agent/rag.py:268-299`, `agent/rag_turn.py:266-294`
**Issue:** `finalize_rag_turn` merges `payload_fields()` into the dict by hand and does not use the new `build_rag_payload` keyword arguments (`quotes`, `cited_ranks`, ...). There are two sources of truth for the payload shape. The `# noqa: F401` re-export of `VERDICT_MODEL_IDK` from `agent.rag` is also indirect, because `rag_turn` could import it from `rag_cite`.
**Fix:** Use one path (for example `build_rag_payload(**fields)`), or remove the unused parameters.

---

_Reviewed: 2026-10-04_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
