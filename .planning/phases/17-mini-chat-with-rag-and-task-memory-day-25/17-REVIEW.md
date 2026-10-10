---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
reviewed: 2026-10-10T00:00:00Z
depth: standard
files_reviewed: 21
files_reviewed_list:
  - agent/context_engine.py
  - agent/main.py
  - agent/rag.py
  - agent/rag_api.py
  - agent/rag_llm.py
  - agent/rag_pipeline.py
  - agent/rag_rank.py
  - agent/rag_turn.py
  - agent/schemas.py
  - agent/task_memory.py
  - agent/task_memory_api.py
  - agent/ws.py
  - scripts/rag_dialog.py
  - scripts/rag_eval.py
  - scripts/rag_judge.py
  - scripts/e2e_rag_dialog_playwright.py
  - shared/config.py
  - shared/database.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 17: Code Review Report

**Reviewed:** 2026-10-10
**Depth:** standard
**Files Reviewed:** 21

## Summary

Focus was on the Phase 17 changes: task memory, history-aware condensing, the new routes and the UI panel. Ownership checks (`_get_owned_chat`, 404 instead of 403), user_id scoping, origin and content-type guards, commit/rollback handling and UI rendering (all `textContent`, no unsanitized HTML) are sound. The scripts under `scripts/` are CLI tools; I found nothing security-relevant in them. No blockers. The warnings below are robustness defects on failure paths and one UI race.

## Warnings

### WR-01: Rollback inside `update_task_memory` expires `chat` / `user_msg`, which the caller then reads

**File:** `agent/task_memory.py:466-469` (callers `agent/ws.py:747-757`, `agent/ws.py:1128-1138`)
**Issue:** On a `SQLAlchemyError` the helper calls `session.rollback()`. A rollback expires all persistent instances in the session, even with `expire_on_commit=False`. The WS handler then calls `_persist_assistant_message`, which reads `chat.id` and `user_msg.id`. On an async session that triggers a lazy load, so it raises `MissingGreenlet`. In the gated path that error is caught by the generic handler, and the user message is deleted and an error frame sent. In the streaming path (`ws.py:1128`) it is not clearly protected, so the reply that was already streamed to the user is lost. The same pattern exists in `rag_turn._history_context` (`rag_turn.py:176-178`), where `chat.id` is read afterwards. `context_engine.build_system_prompt` documents this hazard ("Read before any rollback expires the chat instance") but this path does not follow it.
**Fix:** Capture plain values before the call and avoid attribute reads afterwards, for example `chat_id = chat.id` and `user_msg_id = user_msg.id` before `update_task_memory`, then pass the ids to the persist helper. Alternatively, `await session.refresh(chat)` and `await session.refresh(user_msg)` after a rollback. Add a test where the staging flush raises `SQLAlchemyError`.

### WR-02: `done` handler overwrites the memory panel of whichever chat is open

**File:** `ui/static/app.js:1721-1728`
**Issue:** `state.lastMemory.task_state` is replaced from `data.rag.task_memory` without checking that the frame belongs to `state.currentChatId`. If the user switches chats while a reply is streaming, the old chat's task state is written into the new chat's panel. It stays there until the next `loadChatMemory`, and the next goal edit or reset would then be applied against the wrong displayed state.
**Fix:** Keep the chat id of the socket (for example `ws.chatId`) and only apply the snapshot when it equals `state.currentChatId`. Otherwise skip the update, since `loadChatMemory` refreshes it anyway.

### WR-03: Manual task-memory edits can race an in-flight turn that uses a stale document

**File:** `agent/task_memory_api.py:53-57, 77-85, 104-107`; `agent/ws.py:1128`
**Issue:** The REST routes take `chat_locks[chat_id]`. The turn runs `update_task_memory` (load, then an LLM call of up to 30 s, then stage) and commits only later in `_persist_assistant_message`. I did not verify that the turn holds the same lock across that whole span. If it does not, a goal edit or reset made during the extraction is silently overwritten by the staged old-doc-plus-delta (lost update). If it does hold the lock, the PUT/DELETE/POST requests block for up to ~30 s without a timeout.
**Fix:** Confirm the lock scope in `ws.py`. If the lock is not held, re-load the document right before `stage_doc` (inside the lock) and merge into that. If it is held, document the blocking behavior and let the UI handle slow responses.

### WR-04: `filter_user_stated` accepts a goal on a single shared stem and does not check its numbers

**File:** `agent/task_memory.py:183-188`
**Issue:** Items need at least 50% stem overlap and all their numbers present in the user message (`_is_user_stated`). A goal only needs one overlapping stem (`stems(goal) & user_stems`) and has no digit check. A goal that contains a model-invented article number or amount passes as soon as one word matches. That text goes into the system prompt on every later turn through `render_prompt_lines`, so the guard against taking content from `<assistant_answer>` is weaker for the goal than for items.
**Fix:** Apply the same `_is_user_stated(goal, user_stems, user_text)` check to the goal as to the items.

## Info

### IN-01: `DEFAULT_HISTORY_TURNS = 3` is defined twice

**File:** `agent/rag_api.py:26`, `agent/rag_turn.py:46`
**Issue:** The same constant lives in two modules, so the API default and the turn default can drift apart.
**Fix:** Define it once, for example in `agent/rag.py`, and import it in both.

### IN-02: `task_memory_api` imports a private helper from another router module

**File:** `agent/task_memory_api.py:11`
**Issue:** `from agent.rag_api import _get_owned_chat` uses an underscore-prefixed name across modules. The same pattern already exists for `kb_api` helpers (CONVENTION: private-name import).
**Fix:** Move the helper to `agent/dependencies.py` or give it a public name.

### IN-03: Goal cannot be cleared from the UI except through a full reset

**File:** `agent/schemas.py:~437` (`TaskGoalUpdate.min_length=1`), `ui/static/app.js:608-621`
**Issue:** `set_goal` supports clearing the goal with an empty string, but the API rejects blank goals with 422, so that branch is unreachable. The UI shows a generic error toast for an empty save.
**Fix:** Either allow an empty goal in the schema or drop the dead branch in `set_goal`. Also disable Save in the UI when the field is empty.

---

_Reviewed: 2026-10-10_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
