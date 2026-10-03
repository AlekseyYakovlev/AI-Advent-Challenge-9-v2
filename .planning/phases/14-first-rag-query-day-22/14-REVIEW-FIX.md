---
phase: 14-first-rag-query-day-22
fixed_at: 2026-10-03T00:00:00Z
review_path: .planning/phases/14-first-rag-query-day-22/14-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 4
skipped: 1
status: partial
---

# Phase 14: Code Review Fix Report

**Fixed at:** 2026-10-03
**Source review:** .planning/phases/14-first-rag-query-day-22/14-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5
- Fixed: 4
- Skipped: 1

## Fixed Issues

### WR-01: saveChatRag / loadChatRag overwrite state for the wrong chat after a chat switch

**Files modified:** `ui/static/app.js`
**Commit:** 637be2a
**Applied fix:** Captured `chatId` in `saveChatRag`; both the success and catch paths now return early if the current chat changed. Added the same guard to the `loadChatRag` catch. Rapid concurrent saves building from a stale `prev` were not addressed. Status: fixed, requires human verification (logic).

### WR-02: Delimiter neutralization skips the source/label header line

**Files modified:** `agent/rag.py`
**Commit:** 7eb34d1
**Applied fix:** Header line `[N] source — label` goes through `_neutralize`; whitespace and newlines in source and label are collapsed.

### WR-03: Fragment merge can silently no-op while the payload claims sources were used

**Files modified:** `agent/rag.py`, `agent/rag_turn.py`
**Commit:** 3991c21
**Applied fix:** `merge_rag_block` now returns a bool. `prepare_rag_turn` raises `RagFailure("retrieval_failed")` when the merge fails, so no sources are reported. Status: fixed, requires human verification (logic).

### WR-04b: A generic exception in the pre-step can leave the shared session unusable

**Files modified:** `agent/rag_turn.py`
**Commit:** 8e989c9
**Applied fix:** On `SQLAlchemyError` in the generic handler, `await session.rollback()` is called. Confirmed that `_persist_user_message` commits before the pre-step, so the user message is not lost. Status: fixed, requires human verification (logic).

## Skipped Issues

### WR-04: PUT /rag silently coerces mode "rag" with no KB to "off"

**File:** `agent/rag_api.py:107-113`
**Reason:** The coercion is intentional and covered by the existing test `tests/test_rag_api.py::test_config_null_kb_forces_off`, which expects 200 with mode "off". Returning 422 would be a behavior and contract change that needs a product decision, so it was not applied.
**Original issue:** `{mode:"rag", kb_id:null}` returns 200 with `mode:"off"`, with no signal or log to the caller.

---

_Fixed: 2026-10-03_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
