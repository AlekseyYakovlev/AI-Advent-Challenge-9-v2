---
phase: 11-edit-and-delete-long-term-memory-entries-via-ui-day-21
reviewed: 2026-10-03T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - agent/main.py
  - agent/memory.py
  - agent/schemas.py
  - tests/test_context_engine_memory.py
  - tests/test_memory.py
  - tests/test_memory_api.py
  - tests/test_memory_panel_ui.py
  - ui/static/app.js
findings:
  critical: 0
  warning: 2
  info: 3
  total: 5
status: issues_found
---

# Phase 11: Code Review Report

**Reviewed:** 2026-10-03
**Depth:** standard
**Files Reviewed:** 8 (diff vs 0d956cd; tests skimmed only for reliability)

## Summary

The new PUT/DELETE routes are correctly user-scoped (lookup filters on `user_id` plus `id`, 404 for foreign rows, so no existence leak). Key-conflict handling checks before mutating and also catches `IntegrityError` as a backstop. The frontend uses `textContent` only, so there is no XSS. No critical issues. A few robustness gaps remain.

## Warnings

### WR-01: Unneeded write and `updated_at` bump on a no-op edit
**File:** `agent/memory.py:178-195`
**Issue:** `update_long_term_memory` always sets `row.updated_at` and commits, even when the key and value are unchanged. The UI always sends both fields, so pressing "Save" without changes silently changes `updated_at`. That can reorder entries wherever the list is sorted by recency and misreports the edit time.
**Fix:**
```python
changed = False
if key is not None and key != row.key: ...; changed = True
if value is not None and value != row.value: row.value = value; changed = True
if not changed:
    return row
row.updated_at = datetime.now(timezone.utc)
```

### WR-02: Stale memory refresh can overwrite the panel after a chat switch
**File:** `ui/static/app.js` (`saveLongTermMemory` / `deleteLongTermMemory` / `refreshMemoryPanel`)
**Issue:** Both handlers end with `await refreshMemoryPanel()`, which reads `state.currentChatId` before the await. If the user switches chats while the PUT/DELETE is in flight, an older `loadChatMemory` response can land after the newer one and render the wrong chat's short-term and working memory. Long-term memory is user-wide, so only the other sections are affected. `patchLongTermMemoryLocally` also mutates whichever `lastMemory` is current at that moment. The same race may already exist in `loadChatMemory`, but these handlers widen the window.
**Fix:** In `loadChatMemory`, capture the `chatId` and discard the result when `state.currentChatId !== chatId` (or use an abort controller, as stats does).

## Info

### IN-01: Frontend limits duplicated as magic numbers
**File:** `ui/static/app.js` (`keyInput.maxLength = 200`, `valueInput.maxLength = 50000`)
**Issue:** These must stay in sync with `MEMORY_KEY_MAX_LENGTH` and `MEMORY_VALUE_MAX_LENGTH` in `agent/schemas.py`. Define them as named constants in `app.js` with a comment pointing at the backend values.
**Fix:** `const MEMORY_KEY_MAX = 200; const MEMORY_VALUE_MAX = 50000;`

### IN-02: Error-text heuristic is fragile
**File:** `ui/static/app.js` (`memoryErrorText`)
**Issue:** It suppresses messages starting with `[` or `{` to hide JSON-stringified 422 details. Any server message that legitimately starts with those characters is replaced by the fallback. Acceptable, but it is a heuristic rather than a contract.
**Fix:** Have `apiFetch` attach `err.status` and `err.detail`, and branch on them.

### IN-03: Conflict error carries a possibly-None key and the 409 message is non-English
**File:** `agent/memory.py:191`, `agent/main.py:700`
**Issue:** On the `IntegrityError` path `MemoryKeyConflictError(key)` can receive `None` (value-only update). It is harmless because the message is unused, but it is misleading. Also, `except IntegrityError` assumes any integrity failure is a key conflict. Currently the only unique constraint on this table is `(user_id, key)`, so this holds.
**Fix:** Raise `MemoryKeyConflictError(key or row.key)`.

---

_Reviewed: 2026-10-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
