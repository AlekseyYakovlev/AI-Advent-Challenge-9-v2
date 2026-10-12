---
phase: 19-api-keys-and-http-chat-day-30
plan: 02
subsystem: agent-chat
tags: [websocket, chat-turn, bugfix, llm-error]
requires: []
provides:
  - "agent.ws._run_chat_turn (lock-free turn body)"
  - "agent.ws._discard_user_message (leaf-restoring cleanup)"
  - "agent.ws._llm_error_frame (LLM_ERROR with cause and provider_kind)"
affects: [agent/ws.py, tests/test_ws_turn_failures.py]
key-files:
  created: [tests/test_ws_turn_failures.py]
  modified: [agent/ws.py]
requirements: [HCHAT-02, HCHAT-03]
metrics:
  tasks: 2
  files: 2
completed: 2026-10-12
---

# Phase 19 Plan 02: Reusable chat turn, leaf restore, LLM_ERROR cause Summary

The whole chat turn now lives in `_run_chat_turn` (caller holds the per-chat lock), failed turns restore the chat leaf instead of nulling it, and LLM_ERROR frames carry `cause` and `provider_kind`.

## What was done

- Task 1 (commit d4c6221): moved the turn body, de-indented and otherwise unchanged, from `_handle_chat_message` into `_run_chat_turn(websocket, chat_id, payload)`. `_handle_chat_message` now only creates and takes `chat_locks[chat_id]` and awaits the turn (WebSocket still waits for a busy chat). The two ContextVars are set inside the turn. Added `_discard_user_message` (used at all four failure sites: overflow, first stream failure, tool-round failure with no results, gated-reply storage failure) and `_llm_error_frame` (used at both LLM failure sites).
- Task 2: `tests/test_ws_turn_failures.py`, 9 tests (overflow restore, branch continuation with tree check, unreachable/timeout/upstream causes, first-turn failure, helper edge cases, lock delegation, turn with `RecordingSink`).

## Behaviour change for WebSocket

Pre-existing bug fixed for both transports. Before, deleting the user message after a failed turn made the database set `Chat.current_leaf_message_id` to NULL (`ON DELETE SET NULL`), so the next message started a new root and the earlier conversation vanished from the tree and from the model context. Now `_discard_user_message` points the leaf back at the pre-turn leaf (the parent of the discarded user message) before deleting it; a failed first turn leaves an empty chat with a NULL leaf. Additionally, `LLM_ERROR` frames gain two keys, `cause` (`unreachable`, `timeout`, `upstream`) and `provider_kind`; `code` and `detail` are unchanged.

## Verification (observed)

- Existing WS suites (concurrent_ws, rag_ws, memory_ws, invariants_ws, mcp_chat_ws, multistep_tools_ws, tool_rounds_ws, titles_ws, llm_providers_routing): 93 passed.
- `tests/test_ws_turn_failures.py`: 9 passed.
- Full suite `pytest tests/`: 2310 passed, 11 skipped.
- Plan acceptance one-liners: introspection check printed `ok`; counts: 0 remaining `session.delete(user_msg)`, 4 `_discard_user_message(` calls (including the definition-free call sites: 1 gated + 3 turn), 2 `_llm_error_frame` sends, 1 `async with chat_locks[chat_id]`.

## Deviations from Plan

- [Rule 1 - Bug, test design] The error frame is sent before the cleanup runs, so tests must wait for the chat lock to be released before reading the database; `_send_and_drain` does this. No existing test needed changing (none asserted a NULL leaf).
- `GET /tree` returns leaf-first order, so the continuation test asserts the first user message is the last element rather than the first.
- A worktree base mismatch required `git reset --hard 1907073` at start (as instructed).

## Known Stubs

None.

## Self-Check: PASSED
