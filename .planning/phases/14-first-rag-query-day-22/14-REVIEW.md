---
phase: 14-first-rag-query-day-22
reviewed: 2026-10-03T00:00:00Z
depth: standard
files_reviewed: 25
files_reviewed_list:
  - agent/kb_search.py
  - agent/main.py
  - agent/rag.py
  - agent/rag_api.py
  - agent/rag_turn.py
  - agent/schemas.py
  - agent/ws.py
  - scripts/e2e_rag_playwright.py
  - scripts/rag_eval.py
  - shared/config.py
  - shared/database.py
  - shared/models.py
  - tests/test_cascade_delete.py
  - tests/test_database.py
  - tests/test_kb_search.py
  - tests/test_rag.py
  - tests/test_rag_api.py
  - tests/test_rag_eval.py
  - tests/test_rag_fixture.py
  - tests/test_rag_report.py
  - tests/test_rag_static.py
  - tests/test_rag_turn.py
  - tests/test_rag_ws.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 5
  info: 3
  total: 8
status: issues_found
---

# Phase 14: Code Review Report

**Reviewed:** 2026-10-03
**Depth:** standard
**Files Reviewed:** 25
**Status:** issues_found

## Summary

The RAG pre-step is fail-soft and ownership checks (KB owner, chat owner, 404 instead of 403) are sound. Prompt-injection hardening is partial (see WR-02). I did not read the test files, scripts/e2e_rag_playwright.py or index.html line by line; the rest of scripts/rag_eval.py was only skimmed. No blockers found.

## Warnings

### WR-01: saveChatRag / loadChatRag overwrite state for the wrong chat after a chat switch

**File:** `ui/static/app.js:3916-3935` (also `3837-3846`)
**Issue:** `saveChatRag` captures `state.currentChatId` only when building the URL. After `await`, it assigns `state.rag = <response>` and re-renders without checking that the current chat is still the same one. Switching chats during the PUT shows chat A's RAG config in chat B. The next toggle then PUTs A's `kb_id`/`top_k` into B. `loadChatRag` has the check on success but not in `catch`, so a stale failure nulls `state.rag` for the new chat. Concurrent rapid saves also build the body from a stale `prev`.
**Fix:**
```js
const chatId = state.currentChatId;
...
const cfg = await apiFetch(`/api/v1/chats/${chatId}/rag`, {...});
if (state.currentChatId !== chatId) return;
state.rag = cfg;
// catch: if (state.currentChatId === chatId) state.rag = prev;
```
Apply the same guard in the `loadChatRag` catch.

### WR-02: Delimiter neutralization skips the source/label header line

**File:** `agent/rag.py:121-130`
**Issue:** `_neutralize` is applied only to `chunk["text"]`. `chunk["source"]`, `section` and `title` are interpolated raw into `[N] {source} — {label}`. These come from document filenames and headings, which are the same untrusted content. A heading such as `=== Конец фрагментов ===` followed by injected instructions closes the block early. This defeats the stated "data, not instructions" boundary.
**Fix:** `lines.append(_neutralize(f"[{index}] {chunk['source']} — {label}"))`. Also strip newlines from `label` and `source`.

### WR-03: Fragment merge can silently no-op while the payload claims sources were used

**File:** `agent/rag.py:148-156`, `agent/rag_turn.py:122-136`
**Issue:** `merge_rag_block` returns silently if there is no user-role message in `llm_messages`. Compression strategies could drop it, or it could be absent for other reasons. `prepare_rag_turn` still records `sources` and `context_tokens`, and the UI shows citations the model never saw.
**Fix:** Have `merge_rag_block` return a bool. If it is False, raise `RagFailure("retrieval_failed", ...)` or drop the sources from the payload.

### WR-04: PUT /rag silently coerces mode "rag" with no KB to "off"

**File:** `agent/rag_api.py:107-113`
**Issue:** `{mode:"rag", kb_id:null}` returns 200 with `mode:"off"`. The caller is not told its request was altered, and no log line records the coercion. An API client or test that expects 422 gets a silent downgrade. Separately, `top_k` is overwritten with the default whenever a partial body omits it.
**Fix:** Return `_unprocessable(...)` when `mode == "rag"` and `kb_id is None`.

### WR-04b: A generic exception in the pre-step can leave the shared session unusable

**File:** `agent/rag_turn.py:144-155`
**Issue:** `except Exception` swallows DB errors from `session.get/exec` and continues with the same `AsyncSession`. The session is then used for persisting the assistant message. If the failure left the transaction in a failed state, persistence fails later and the reply is lost, which defeats the fail-soft goal. The only logging is `type(exc).__name__`.
**Fix:** On `SQLAlchemyError`, call `await session.rollback()`. Note that this would also discard the uncommitted user message, so first confirm that `_persist_user_message` commits.

## Info

### IN-01: Snippet-load failure always reports "knowledge base deleted"

**File:** `ui/static/app.js:4023-4035`
**Issue:** The `catch` path (network or 500 error) shows the same text as a real deletion or 404. It misleads users.
**Fix:** Branch on the error status (404 -> deleted, otherwise "failed to load, retry").

### IN-02: scripts/rag_eval.py hardcodes machine-specific paths and uses print()

**File:** `scripts/rag_eval.py:56-64, 107`
**Issue:** `RAG_DIR = C:\Projects\RAG` and the PDF names are hardcoded. `print()` is used for diagnostics, which is acceptable for a CLI script but deviates from the structlog convention. `_message_tokens` duplicates `agent.context_engine._message_tokens`, so the eval budget can drift from production.
**Fix:** Take the PDF directory from an environment variable or a CLI argument, and reuse the production helper.

### IN-03: rag_sources is stored for every assistant message, including mode "off"

**File:** `agent/ws.py:990-993`
**Issue:** Every reply persists a payload JSON, and the UI renders a "без RAG" label on every assistant bubble. This is storage and UI noise when RAG was never configured.
**Fix:** Pass `rag_sources=None` when `rag_turn.mode == MODE_OFF`, and have the UI treat a missing payload as off.

---

_Reviewed: 2026-10-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
