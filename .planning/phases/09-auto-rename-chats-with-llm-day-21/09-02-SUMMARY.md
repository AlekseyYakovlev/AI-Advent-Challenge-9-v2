---
phase: 09-auto-rename-chats-with-llm-day-21
plan: 02
subsystem: frontend
tags: [websocket, events, chat-title]
requires: []
provides: [chat_title_updated frame handling in the frontend]
affects: [ui/static/app.js]
key-files:
  modified: [ui/static/app.js]
requirements-completed: [TITLE-05, TITLE-06]
metrics:
  tasks: 1
  files: 1
---

# Phase 9 Plan 02: Frontend chat_title_updated handling Summary

The /ws/events client now routes `chat_title_updated` frames through a new `handleEventFrame` dispatcher that patches the sidebar and the open chat's header via `textContent`, and reloads the chat list after a socket reconnect.

## What changed (ui/static/app.js)
- `applyChatTitleUpdate(frame)`: validates `chat_id` (number) and `title` (non-empty string), slices the title to 200 chars, patches `state.chats`, calls `renderChatList()`, and updates `#chat-title` for the open chat. An unknown chat triggers `loadChats()`.
- `handleEventFrame(frame)`: handles title frames first, otherwise falls through to `applySchedulerEvent`. `ws.onmessage` now calls it.
- `refreshChatsAfterEventsReconnect()` runs from `ws.onopen` only on reconnects, guarded by the new `state.eventsHasConnected` flag, so the first connect during `init()` is unaffected.

## Verification (run)
- `pytest tests/test_static_js_syntax.py -q`: 6 passed.
- Source assertions: `ws.onmessage` body contains `handleEventFrame(frame)` and no `applySchedulerEvent(frame)`; `applyChatTitleUpdate` uses `textContent` and no `innerHTML`; `eventsHasConnected` appears 3 times; `'chat_title_updated'` appears once.
- Not verified: live browser behaviour (planned for 09-04).

## Deviations from Plan
- Worktree base was corrected with `git reset --hard b9ee552` per the startup check (merge-base was 64d13b5). No code deviations.

## Self-Check: PASSED
