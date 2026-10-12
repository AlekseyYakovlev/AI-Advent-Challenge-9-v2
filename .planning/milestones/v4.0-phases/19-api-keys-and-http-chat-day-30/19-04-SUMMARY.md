---
phase: 19-api-keys-and-http-chat-day-30
plan: 04
subsystem: agent-http-chat
tags: [http, chat, api-keys, rag, fastapi]
requires: [19-01, 19-02]
provides:
  - "POST /api/v1/chats/{chat_id}/messages (one JSON body per turn)"
  - "agent.rag_api.apply_chat_kb"
  - "HttpChatRequest, HttpChatResponse"
affects: [agent/http_chat.py, agent/rag_api.py, agent/schemas.py, agent/main.py]
key-files:
  created: [agent/http_chat.py, tests/test_http_chat.py, tests/test_http_chat_rag.py]
  modified: [agent/rag_api.py, agent/schemas.py, agent/main.py]
requirements-completed: [HCHAT-01, HCHAT-02, HCHAT-03]
completed: 2026-10-12
---

# Phase 19 Plan 04: Plain-HTTP chat endpoint Summary

`POST /api/v1/chats/{chat_id}/messages` runs the shared WebSocket turn (`agent.ws._run_chat_turn` with a `RecordingSink`) under a non-waiting chat lock inside a shielded background task, and returns one JSON body with the reply, statistics, tool calls, memory/task writes, conflict and RAG info.

## Commits

| Task | Commit |
|------|--------|
| 1 endpoint, `apply_chat_kb`, schemas, router | e70b229 |
| 2 tests/test_http_chat.py (20 tests) | 9ae6689 |
| 3 tests/test_http_chat_rag.py (12 tests) | 622aa5e |

## Behaviour

- Lock: `lock.locked()` check then immediate `await lock.acquire()` with nothing awaited between; busy chat gives 409 `CHAT_BUSY` without storing anything. The turn task releases the lock in its outermost `finally`.
- Disconnect: the handler awaits `asyncio.shield(task)`; the task is strongly referenced in `_http_turns`, so a dropped client still gets the reply stored, the lock freed and `chat_updated` published (once, success only).
- Error mapping: 413 `CONTEXT_OVERFLOW` (+`suggested_strategy`), 503 `LM_STUDIO_UNAVAILABLE` / `LLM_UNREACHABLE` / `PROVIDER_UNAVAILABLE`, 502 `LLM_ERROR`, 500 `RAG_GATED_FAILED` / `INTERNAL_ERROR`, all with `user_message_kept`; chat/KB 404 and KB-not-ready 422 keep the default `{"detail"}` shape.
- `kb_id`: omitted keeps the saved config, `null` saves mode off, an id saves kb_id plus mode `rag`; other search settings (top_k, hybrid, strict, ...) are never touched.
- Content in the response is read from the stored assistant row; the user message id is its `parent_id`.

## Verification (observed)

- `python -m pytest tests/test_http_chat.py tests/test_http_chat_rag.py -q` -> 32 passed (20 + 12).
- `python -m pytest tests/test_rag_api.py tests/test_rag_ws.py tests/test_ws_turn_failures.py tests/test_port_literals.py -q` -> 65 passed (after Task 1).
- Full suite `python -m pytest tests/ -q` -> 2381 passed, 1 failed, 11 skipped; the single failure was `test_selection_persists_to_the_next_turn`, a test-side assertion that I then fixed; after the fix the two new files pass (32 passed). The full suite was NOT re-run after that last test-only edit.
- Source-introspection acceptance checks (no await between `locked()` and `acquire()`, no model-management or rate-limit code, `release()` in the turn coroutine) printed `ok`.

## Deviations from Plan

1. **[Rule 3 - Blocking] Route-existence verify command.** The plan's `app.routes` one-liner fails on this FastAPI version (routers are wrapped in `_IncludedRouter` without `.path`). Verified the route through `app.openapi()["paths"]` instead.
2. **[Test design]** The sliding-window test needs `context_length=512` and long messages, because compression only triggers at 75% of the context. The RAG persistence test counts requests containing the RAG block, since auxiliary LLM calls (query condensing and post-turn calls) interleave with the chat calls.
3. **[Process]** Task 3's D-07 check (list KBs with a key) is its own test, giving 12 tests in `test_http_chat_rag.py`.
4. Worktree base was reset to 7f8f482 at start as instructed.

## Known Stubs

None.

## Self-Check: PASSED

Files agent/http_chat.py, tests/test_http_chat.py, tests/test_http_chat_rag.py exist; commits e70b229, 9ae6689, 622aa5e exist; `agent/ws.py` untouched; STATE.md and ROADMAP.md untouched.
