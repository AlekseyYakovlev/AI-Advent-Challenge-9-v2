---
phase: 23-help-command-verification-and-report-day-31
plan: 04
subsystem: agent
tags: [help, websocket, http, rag, mcp]
requires:
  - phase: 23-01
    provides: HelpConfig, resolve_help_notice, build_help_tools
  - phase: 23-02
    provides: prepare_help_turn, finalize_help_turn, ws._run_turn_middle
provides:
  - "/help turn in agent/ws.py (_run_help_turn) for both channels"
  - "help_notice frame handling in the WebSocket loop and a 200 notice body over HTTP"
  - "HttpChatResponse.help"
affects: [23-05, 23-06]
key-files:
  created: [tests/test_help_ws.py, tests/test_help_http.py]
  modified: [agent/ws.py, agent/http_chat.py, agent/schemas.py, tests/test_http_chat.py]
requirements-completed: [HELP-02, HELP-03, HELP-04, HELP-05, HELP-06, HELP-07, VER-07]
completed: 2026-10-13
---

# Phase 23 Plan 04: /help over WebSocket and HTTP

`/help <question>` now answers from the marked documentation base with the chosen tools, no chat history and no side effects, over the browser WebSocket and the HTTP chat endpoint alike. Service replies (usage, no base) cost nothing.

## Tasks

| Task | Commit | Notes |
|------|--------|-------|
| 1. /help branch in the turn and the WebSocket loop | adfeedb | `tests/test_help_ws.py`, 20 tests |
| 2. /help over the HTTP chat endpoint | ff4778a | `tests/test_help_http.py`, 14 tests |

## Final shapes

Notice (WebSocket frame): `{"type": "help_notice", "kind": "usage" | "no_base", "text": ..., "stored": false}`.

Notice (HTTP, status 200): `{"chat_id": <id>, "stored": false, "help": {"kind": ..., "text": ...}}`.

Success body: the ordinary HTTP body plus `help = {"kb_name": <base name>, "warnings": [{"server", "text"}]}` (taken from `rag.help`); `null` for ordinary messages. `tool_calls` carries the ordinary tool_call frames.

## Behaviour

- Recognition happens once on the server. `_run_chat_turn` parses the message before any persistence; a notice returns before the provider is resolved.
- `_run_help_turn`: loads `HelpConfig` and the marked base with `chat.user_id`, persists the user message exactly as typed, builds only system prompt + fragments + question, passes `allowed_tools=tools.allowed_tools` (empty by default), then finalizes quotes and stores two ordinary messages. The chat's `ChatRagConfig` is never read or written.
- Rate slot: the "deferred check" choice. In `ws_chat` a /help message is validated first, then the notice is decided, and only a real turn takes a rate slot (`_reject_rate_limited`). Over HTTP the notice is returned before the busy check and rate check, without a task. Invalid payloads take no slot.
- HTTP: `apply_chat_kb` is skipped when the message is a /help command, so `kb_id` (id, missing id, null) is ignored and not validated; a race-case notice frame from the turn is mapped to the notice body without `chat_updated`.

## Side jobs skipped on a /help turn

Task-memory update, fact extraction, title generation, invariant resolution / self-critique / conflict recording. Only the two messages are added. `done.invariant_conflict` is always `null`.

## Tool-call cards

Tool-call cards remain live-only for every chat (not restored from the tree after a reload); this plan did not change that. The stored assistant message carries `tool_trace` as for any turn.

## Verification (actually run)

- `pytest tests/test_help_ws.py tests/test_rag_ws.py tests/test_rate_limit.py tests/test_mcp_chat_ws.py tests/test_tool_rounds_ws.py tests/test_ws_turn_failures.py -q` -> 87 passed.
- `pytest tests/test_help_http.py tests/test_http_chat.py tests/test_http_chat_rag.py tests/test_bearer_auth.py tests/test_generation_queue.py -q` -> 76 passed (before the one-line fix to a new test, then `test_help_http.py` alone 14 passed).
- AST check: `_run_help_turn` contains none of `build_llm_context`, `prepare_rag_turn`, `update_task_memory`, `extract_and_update_facts`, `schedule_title_generation`, `run_self_critique` -> ok.
- Full suite `pytest tests -q` -> 3075 passed, 13 skipped.

## Deviations from Plan

- [Rule 1 - Test] `tests/test_http_chat.py::RESPONSE_KEYS` extended with `help` (the success body gained the additive key); this is the existing test that asserts the exact key set.
- The WebSocket test helper treats a `TOOL_ERROR` error frame as non-terminal (as the HTTP layer already does); the failure test waits for the chat lock to be released before reading rows, because the discard runs after the error frame is sent.
- The tree route returns messages leaf-first, so the tree test reads the assistant message at index 0.

## Known Stubs

None.

## Self-Check: PASSED

Commits adfeedb and ff4778a exist; `agent/ws.py`, `agent/http_chat.py`, `agent/schemas.py`, `tests/test_help_ws.py`, `tests/test_help_http.py` present.
