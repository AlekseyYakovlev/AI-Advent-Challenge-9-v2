# Phase 19: API keys and HTTP chat (Day 30) - Context

**Gathered:** 2026-10-12
**Status:** Ready for planning

<domain>
## Phase Boundary

A user creates named API keys in Settings and uses `Authorization: Bearer <key>` to call REST routes as the key's owner. A new plain-HTTP endpoint posts a message into an existing chat and returns the whole reply as one JSON body with token statistics; the exchange is stored in the normal message tree and goes through the same turn as a WebSocket message (settings, memory, compression, RAG, tools). Failures map to clear HTTP statuses, and `docs/API_SPEC.md` documents the endpoint with a full curl scenario.

Covers KEY-01..KEY-05 and HCHAT-01..HCHAT-04.

Locked by REQUIREMENTS.md / PROJECT.md / Phase 18 (not re-discussed): only a hash of the key is stored; the key list shows name, prefix, creation date and last-used date; WebSocket endpoints stay cookie-only; no SSE streaming and no stateless chat (HCHAT-F1/F2 deferred); a request without an `Origin` header passes the Origin check (Phase 18 D-05); UI text is Russian; no new dependencies; branch `Day30`.

Belongs to Phase 20, not here: configurable per-chat rate limit shared by WS and HTTP, 429 with `Retry-After`, login attempt limit, `/debug/routes` auth, concurrency cap with queue, verification script, report.

</domain>

<decisions>
## Implementation Decisions

### API key permissions
- **D-01:** Key management (create, list, revoke) is cookie-session only. A request authenticated by an API key cannot create, list or revoke keys.
- **D-02:** Adding a user (`POST /api/v1/auth/users`) and logout (`POST /api/v1/auth/logout`) are also cookie-only. A key-authenticated request to any cookie-only route gets 403 with a clear message. Every other REST route (chats, settings, memory, tasks, invariants, MCP, knowledge bases, RAG config, scheduler, LLM providers, LM Studio) accepts the key with the same ownership checks as the cookie. **This narrows KEY-04** ("every REST route") — update the REQUIREMENTS.md wording when the phase lands.
- **D-03:** Keys do not expire; a key lives until revoked. Each user may hold at most 20 active keys; creating one more is refused with a clear error.
- **D-04:** When an `Authorization: Bearer` header is present it alone decides: an invalid or revoked key gives 401 even if a valid session cookie is also sent. There is no fallback to the cookie. Without the header, the cookie path is unchanged.

### HTTP chat request
- **D-05:** The request body mirrors the WebSocket message: `content`, `model` (required — the model is client state, not stored on the chat) and optional `provider_id`. Without `provider_id` the user's LM Studio provider answers; with it, any enabled provider of the user.
- **D-06:** The body has one extra optional field selecting the knowledge base for RAG on this turn:
  - field given with a KB id → the turn uses that KB, and the value is saved into the chat's RAG config so it also applies to later turns and is visible in the UI;
  - field given as explicit `null` → RAG is off for this turn, and that is saved too;
  - field omitted → the chat's saved RAG config is used ("same as the previous turn"); a new chat has RAG off, so its first turn runs without RAG.
  The other search parameters (top_k, threshold, hybrid, rerank, ...) come from the saved chat config. The same ownership and "KB must be ready" checks as `PUT /api/v1/chats/{chat_id}/rag` apply.
- **D-07:** The list of knowledge bases comes from the existing `GET /api/v1/kb`, called with the key. No new listing endpoint.
- **D-08:** MCP is controlled through the existing routes, called with the key: `GET /api/v1/mcp/servers` lists servers with their state, `PUT /api/v1/mcp/servers/{id}` with `enabled` switches one on or off. The switch is per user (it affects the UI and all of the user's chats). No per-chat or per-request MCP switch is added. These routes must be covered by a key-auth test and shown in the curl scenario.
- **D-09:** The model has the same tools in an HTTP turn as in a WebSocket turn: built-in tools (memory, tasks, scheduler) and MCP tools. No allowlist like the headless scheduler one.
- **D-10:** The message always attaches to the chat's current leaf, as in a WebSocket turn. A client wanting another branch first calls the existing `POST /api/v1/chats/{chat_id}/branch`.
- **D-11:** The endpoint never loads or switches an LM Studio model. A client that needs a model loaded calls the existing `GET /api/v1/lm-studio/models` and `POST /api/v1/lm-studio/load-model` with the key; the curl scenario shows this.
- **D-12:** The same background actions run as after a WebSocket turn: auto-title for the first message of a new chat, and fact extraction.

### HTTP chat response
- **D-13:** A successful response is one JSON body carrying everything the WebSocket `done` frame carries plus the reply itself: assistant text, ids of the stored user and assistant messages, `stats`, tool calls made during the turn, `memory_writes`, `task_writes`, `invariant_conflict` and the `rag` payload (sources, quotes).
- **D-14:** If the client drops the connection mid-generation, the turn runs to completion and the reply is saved to the tree; the client reads it later through the chat tree route or sees it in the UI.
- **D-15:** Messages that arrived over HTTP look the same in the UI as typed ones. No "API" badge, no new column on `Message`.

### Errors and busy chat
- **D-16:** If the chat's lock is held by another turn (from the UI or another HTTP request), the endpoint answers 409 immediately with code `CHAT_BUSY` and stores nothing. No waiting. WebSocket behaviour (wait for the lock) is unchanged.
- **D-17:** Status mapping, with a uniform body of `detail` (text) and `code` (the same machine code the WebSocket error frames use):
  - context overflow → 413, `CONTEXT_OVERFLOW`, with `suggested_strategy`;
  - LM Studio not running, or provider unavailable (deleted/disabled) → 503;
  - other model error or timeout (`LLM_ERROR`) → 502;
  - chat busy → 409, `CHAT_BUSY`;
  - chat missing or owned by another user → 404;
  - invalid or revoked key → 401.
- **D-18:** What stays in the tree after a failed turn is exactly what a WebSocket turn leaves for the same failure (on overflow the user message is deleted; on a model error, the current `agent/ws.py` behaviour). The error body states whether the user message was kept.
- **D-19:** When an HTTP turn finishes, the server publishes an event to the chat owner over the existing `/ws/events` channel. A browser with that chat open re-reads the tree and shows the new messages at once; the chat list refreshes (new title, order). The event is sent once, at the end of the turn; there is no "generation in progress" indicator and no input lock for HTTP turns.

### "API ключи" section in Settings
- **D-20:** A new section in the Settings modal, styled like "MCP серверы" and "Провайдеры LLM": list of keys (name, prefix, created, last used), a create form with a name field, and a revoke button per row.
- **D-21:** After creation a separate modal shows the full key with a "Скопировать" button and a warning that the key will not be shown again. It closes only via the × button (Phase 10 modal policy).
- **D-22:** The same modal shows a ready curl example under the key — sending a message to a chat — with the server address the UI was opened from and the new key substituted, also copyable. The section itself carries one line explaining the `Authorization: Bearer` header.
- **D-23:** Key format: a recognizable fixed prefix plus a random string, `aia_<random>`. The list shows the beginning of the key (for example `aia_3fK9…`) as the "prefix".
- **D-24:** Revoking asks for confirmation and then deletes the row from the database. The list shows only active keys, and the 20-key cap (D-03) counts only them.

### Claude's Discretion
- Path and exact field names of the HTTP chat endpoint and of the key management routes; the name of the KB field in D-06 and the RAG mode applied when a KB is given while the saved mode is `off`.
- Hash function and random length for keys, table/column layout, how `last_used` is updated (every request or throttled).
- How Bearer auth is wired into `get_current_user` and how cookie-only routes are marked (D-01, D-02).
- How the HTTP turn reuses the WebSocket turn code (for example the `RecordingSink` approach from `agent/headless.py`) and how D-14 is achieved under client disconnect.
- Exact 503 codes (`PROVIDER_UNAVAILABLE` exists; the code for "LM Studio is not running" is the planner's call), wording of all messages and UI strings.
- Name and payload of the `/ws/events` frame in D-19 and how the frontend refreshes an open chat without disturbing a WebSocket turn in progress.
- Server-side timeout for a very long generation, if any.

</decisions>

<specifics>
## Specific Ideas

- The demonstration the user has in mind: run curl from another LAN machine and watch the answer appear in the open browser chat (D-19).
- The curl scenario in `docs/API_SPEC.md` must go beyond HCHAT-04's minimum: log in and create a key, create a chat, list knowledge bases and send a message with a chosen KB, list MCP servers and toggle one with `PUT`, load a model when needed.
- "Default RAG = same as the previous turn; first turn = no RAG" is the user's wording for D-06.
- Copy buttons must work when the UI is opened over plain HTTP from a LAN IP (no secure context) — reuse the HTTP-safe copy helper introduced in Phase 18 (plan 18-03).

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` — KEY-01..KEY-05, HCHAT-01..HCHAT-04, Future Requirements (HCHAT-F1/F2, KEY-F1), Out of Scope table
- `.planning/ROADMAP.md` §Phase 19 — goal and the five success criteria; §Phase 20 — what is deliberately left out (limits, 429, queue, script)
- `.planning/PROJECT.md` — milestone v4.0 goal and hard constraints (two-process model, vanilla JS, cookie session, no new dependencies)

### Prior phase decisions
- `.planning/phases/18-lan-exposure-day-30/18-CONTEXT.md` — Origin rule (D-01..D-07; missing `Origin` allowed), Settings "Сеть" section, HTTP-safe clipboard copy
- `.planning/phases/18-lan-exposure-day-30/18-UI-SPEC.md` — design contract for the Settings section added in Phase 18; the "API ключи" section must be consistent with it
- `.planning/phases/18-lan-exposure-day-30/18-03-PLAN.md` — the clipboard copy helper to reuse

### Docs to update
- `docs/API_SPEC.md` — key routes, HTTP chat endpoint, status table, full curl scenario (HCHAT-04)
- `docs/TESTING_GUIDE.md` — required scenario coverage per test file; API key and HTTP chat tests are written in this phase (VER-06 is tracked in Phase 20)
- `docs/ARCHITECTURE.md` — auth section gains Bearer keys next to the cookie session
- `docs/USER_GUIDE.md` — how to create a key and call the service

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md` — process split, WebSocket chat flow, state
- `.planning/codebase/INTEGRATIONS.md` — LM Studio / provider integration and error surfaces
- `.planning/codebase/TESTING.md` — fixtures and test isolation

No external specs or ADRs — the rest is captured in the decisions above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/dependencies.py::get_current_user` — the single auth dependency used by 68 REST routes (`agent/main.py` 39, `scheduler_api.py` 10, `kb_api.py` 7, `providers_api.py` 6, `rag_api.py` 3, `task_memory_api.py` 3). Bearer support added here reaches every route at once.
- `shared/auth.py::generate_session_token` / `hash_session_token` (`secrets.token_urlsafe`, sha256) and the `Session` model in `shared/models.py` (`token_hash` unique + indexed, `user_id` FK with `ondelete="CASCADE"`) — direct model for the key table and hashing.
- `agent/headless.py::RecordingSink` — duck-typed socket stand-in that records every non-token frame; the turn helpers in `agent/ws.py` only ever call `send_json`, so a sink lets the HTTP endpoint run the same turn and read the `done` / `error` / `tool_call` frames.
- `agent/ws.py::_handle_chat_message(websocket, chat_id, payload)` — the whole turn under the per-chat lock: provider resolution, user message, context build, overflow handling, RAG, tool rounds, persistence, fact extraction, auto-title, final `done` frame with `stats`, `memory_writes`, `task_writes`, `invariant_conflict`, `rag`.
- `agent/schemas.py::MessagePayload` — `content`, `model`, optional `provider_id`; base for the HTTP body.
- `agent/events.py` — `hub.publish(user_id, frame)` and the `/ws/events` socket, already used by the scheduler and KB indexing (`agent/kb_indexer.py:104`, `agent/kb_api.py:333`).
- `agent/rag_api.py::put_rag_config` (`PUT /api/v1/chats/{chat_id}/rag`) and `ChatRagConfig` in `shared/models.py` (`kb_id`, `mode` default `off`, search settings) — the saved per-chat RAG state D-06 reads and writes; `_get_owned_kb` and the `KbStatus.READY` check are the validations to reuse.
- `agent/kb_api.py` router (`prefix="/api/v1/kb"`, `GET ""` lists KBs); `GET /api/v1/mcp/servers` and `PUT /api/v1/mcp/servers/{server_id}` with `McpServerUpdate.enabled` in `agent/main.py` — already exist (D-07, D-08).

### Established Patterns
- Error codes already used in WebSocket frames: `CONTEXT_OVERFLOW` (with `suggested_strategy: "sliding"`, user message deleted, `agent/ws.py:868-880`), `PROVIDER_UNAVAILABLE`, `LLM_ERROR`, `TOOL_ERROR`, `RAG_GATED_FAILED`. `agent/ws.py::_llm_error_detail` builds the human text.
- There is no "chat busy" status today: `_handle_chat_message` awaits `chat_locks[chat_id]` (`agent/state.py`). D-16 needs a non-waiting check for the HTTP path only.
- Mutating REST routes carry `dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)]`.
- `ConnectError` from LM Studio means "LM Studio is not running" and is surfaced as such (`list_lm_studio_models` in `agent/main.py`).
- New tables go through the migration mechanism in `shared/database.py`; FK cascade via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`.
- `current_chat_model` / `current_chat_provider_id` context vars are set at the start of each turn (`agent/state.py`).
- Rate limiting (`agent/ws.py::_check_rate_limit`, `RATE_LIMIT_MAX = 10`) is applied in the socket loop, outside `_handle_chat_message` — Phase 20 makes it shared; this phase does not add it to HTTP.

### Integration Points
- `agent/dependencies.py` — Bearer resolution, cookie-only marker.
- `shared/models.py`, `shared/database.py`, `shared/auth.py` — key table, migration, key generation and hashing.
- `agent/main.py` (or a new router module like `agent/providers_api.py`) — key routes and the HTTP chat endpoint; `agent/schemas.py` — request/response models.
- `agent/ws.py` — reuse of the turn; `agent/events.py` — the "chat updated" frame.
- `ui/static/index.html` (`#settings-modal`, after `#llm-providers-section`) and `ui/static/app.js` — "API ключи" section, one-time key modal, handling of the new event.
- `tests/` — new files for key auth and HTTP chat (`respx` for the LLM), following `tests/test_auth.py` and `tests/test_rag_ws.py`.

</code_context>

<deferred>
## Deferred Ideas

- Per-chat or per-request MCP switch — considered, not chosen; the per-user `enabled` toggle is used. Backlog candidate if it becomes necessary.
- "API" badge on messages that arrived over HTTP — not chosen.
- Key expiry dates — not chosen.
- "Generation in progress" indicator in the UI for an HTTP turn — not chosen.
- Already tracked in REQUIREMENTS.md: SSE streaming (HCHAT-F1), stateless chat (HCHAT-F2), API keys for WebSocket (KEY-F1).

</deferred>

---

*Phase: 19-api-keys-and-http-chat-day-30*
*Context gathered: 2026-10-12*
