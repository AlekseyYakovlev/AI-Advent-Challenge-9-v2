# Phase 19: API keys and HTTP chat (Day 30) - Research

**Researched:** 2026-10-12
**Domain:** FastAPI Bearer-key auth seam, reuse of the WebSocket chat turn over plain HTTP, SQLModel table, vanilla-JS Settings UI
**Confidence:** HIGH (all findings come from reading the real code in this repo; no new libraries)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Key management (create, list, revoke) is cookie-session only. A request authenticated by an API key cannot create, list or revoke keys.
- **D-02:** Adding a user (`POST /api/v1/auth/users`) and logout (`POST /api/v1/auth/logout`) are also cookie-only. A key-authenticated request to any cookie-only route gets 403 with a clear message. Every other REST route (chats, settings, memory, tasks, invariants, MCP, knowledge bases, RAG config, scheduler, LLM providers, LM Studio) accepts the key with the same ownership checks as the cookie. **This narrows KEY-04** ("every REST route") - update the REQUIREMENTS.md wording when the phase lands.
- **D-03:** Keys do not expire; a key lives until revoked. Each user may hold at most 20 active keys; creating one more is refused with a clear error.
- **D-04:** When an `Authorization: Bearer` header is present it alone decides: an invalid or revoked key gives 401 even if a valid session cookie is also sent. There is no fallback to the cookie. Without the header, the cookie path is unchanged.
- **D-05:** The request body mirrors the WebSocket message: `content`, `model` (required - the model is client state, not stored on the chat) and optional `provider_id`. Without `provider_id` the user's LM Studio provider answers; with it, any enabled provider of the user.
- **D-06:** The body has one extra optional field selecting the knowledge base for RAG on this turn:
  - field given with a KB id -> the turn uses that KB, and the value is saved into the chat's RAG config so it also applies to later turns and is visible in the UI;
  - field given as explicit `null` -> RAG is off for this turn, and that is saved too;
  - field omitted -> the chat's saved RAG config is used ("same as the previous turn"); a new chat has RAG off, so its first turn runs without RAG.
  The other search parameters (top_k, threshold, hybrid, rerank, ...) come from the saved chat config. The same ownership and "KB must be ready" checks as `PUT /api/v1/chats/{chat_id}/rag` apply.
- **D-07:** The list of knowledge bases comes from the existing `GET /api/v1/kb`, called with the key. No new listing endpoint.
- **D-08:** MCP is controlled through the existing routes, called with the key: `GET /api/v1/mcp/servers` and `PUT /api/v1/mcp/servers/{id}` with `enabled`. Per user switch. No per-chat or per-request MCP switch. These routes must be covered by a key-auth test and shown in the curl scenario.
- **D-09:** The model has the same tools in an HTTP turn as in a WebSocket turn: built-in tools (memory, tasks, scheduler) and MCP tools. No allowlist.
- **D-10:** The message always attaches to the chat's current leaf. A client wanting another branch first calls `POST /api/v1/chats/{chat_id}/branch`.
- **D-11:** The endpoint never loads or switches an LM Studio model. The curl scenario shows `GET /api/v1/lm-studio/models` and `POST /api/v1/lm-studio/load-model`.
- **D-12:** The same background actions run as after a WebSocket turn: auto-title for the first message of a new chat, and fact extraction.
- **D-13:** A successful response is one JSON body carrying everything the WS `done` frame carries plus the reply itself: assistant text, ids of the stored user and assistant messages, `stats`, tool calls made during the turn, `memory_writes`, `task_writes`, `invariant_conflict` and the `rag` payload.
- **D-14:** If the client drops the connection mid-generation, the turn runs to completion and the reply is saved to the tree.
- **D-15:** HTTP messages look the same in the UI as typed ones. No "API" badge, no new column on `Message`.
- **D-16:** If the chat's lock is held by another turn, the endpoint answers 409 immediately with code `CHAT_BUSY` and stores nothing. No waiting. WebSocket behaviour (wait for the lock) is unchanged.
- **D-17:** Status mapping, uniform body of `detail` (text) and `code`: context overflow -> 413 `CONTEXT_OVERFLOW` with `suggested_strategy`; LM Studio not running or provider unavailable -> 503; other model error or timeout (`LLM_ERROR`) -> 502; chat busy -> 409 `CHAT_BUSY`; chat missing/foreign -> 404; invalid/revoked key -> 401.
- **D-18:** What stays in the tree after a failed turn is exactly what a WebSocket turn leaves for the same failure. The error body states whether the user message was kept.
- **D-19:** When an HTTP turn finishes, the server publishes an event to the chat owner over `/ws/events`. A browser with that chat open re-reads the tree; the chat list refreshes. Sent once at the end; no "in progress" indicator, no input lock.
- **D-20:** New "API ключи" Settings section styled like "MCP серверы" / "Провайдеры LLM": list (name, prefix, created, last used), create form with name, revoke per row.
- **D-21:** After creation a separate modal shows the full key with "Скопировать" and a warning it will not be shown again. Closes only via the x button (Phase 10 policy).
- **D-22:** The same modal shows a ready curl example (server address the UI was opened from, key substituted), copyable. The section carries one explanatory line about `Authorization: Bearer`.
- **D-23:** Key format `aia_<random>`. The list shows the beginning of the key (e.g. `aia_3fK9...`) as "prefix".
- **D-24:** Revoking asks for confirmation then deletes the row. The list shows only active keys; the 20-key cap counts only them.

### Claude's Discretion
- Path and exact field names of the HTTP chat endpoint and of the key management routes; the name of the KB field in D-06 and the RAG mode applied when a KB is given while the saved mode is `off`.
- Hash function and random length for keys, table/column layout, how `last_used` is updated (every request or throttled).
- How Bearer auth is wired into `get_current_user` and how cookie-only routes are marked.
- How the HTTP turn reuses the WebSocket turn code and how D-14 is achieved under client disconnect.
- Exact 503 codes, wording of all messages and UI strings.
- Name and payload of the `/ws/events` frame in D-19 and how the frontend refreshes an open chat without disturbing a WebSocket turn in progress.
- Server-side timeout for a very long generation, if any.

### Deferred Ideas (OUT OF SCOPE)
- Per-chat or per-request MCP switch; "API" badge on HTTP messages; key expiry dates; "generation in progress" indicator.
- Already tracked: SSE streaming (HCHAT-F1), stateless chat (HCHAT-F2), API keys for WebSocket (KEY-F1).
- Belongs to Phase 20: per-chat rate limit shared by WS and HTTP, 429 + `Retry-After`, login attempt limit, `/debug/routes` auth, concurrency cap with queue, verification script, report. Do NOT add rate limiting to the HTTP endpoint here.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| KEY-01 | Create a named API key in Settings; full key shown once | `ApiKey` table + `POST /api/v1/api-keys` returns the raw key once; one-time modal (UI-SPEC) |
| KEY-02 | Only a hash stored; list shows name, prefix, created, last-used | sha256 of the key (same pattern as `hash_session_token`), `key_prefix`, `created_at`, `last_used_at` columns |
| KEY-03 | Revoke; revoked key stops working immediately | Hard `DELETE` of the row (D-24); auth looks the hash up in the DB on every request (no cache) |
| KEY-04 | Bearer authenticates REST routes as the owner; WS cookie-only | Single seam: `agent/dependencies.py::get_current_user` (all 68 REST routes use it); WS handlers use `get_current_user_ws` (cookie only) and are untouched |
| KEY-05 | Invalid/revoked key -> 401 | Bearer branch raises 401 and never falls through to the cookie (D-04) |
| HCHAT-01 | POST text+model to an existing chat, whole reply in one JSON body with stats | New route reuses the WS turn through a recording sink; stats come from `done.stats` |
| HCHAT-02 | Written to the message tree; same settings/memory/compression/RAG | Same `_run_chat_turn` body as the WS path (extracted, not copied) |
| HCHAT-03 | Clear HTTP statuses for overflow, LM Studio down, chat busy | Error-frame -> status mapper (table below) + non-waiting `lock.locked()` check |
| HCHAT-04 | `docs/API_SPEC.md` documents endpoint + full curl scenario | Doc section outline below |
</phase_requirements>

## Summary

The codebase already has every seam this phase needs. All 68 REST routes authenticate through one dependency, `agent/dependencies.py::get_current_user` (cookie -> `Session` row -> `User`); the two WebSocket endpoints use a separate function `get_current_user_ws`. Adding a Bearer branch to `get_current_user` therefore reaches every REST route at once and leaves WebSocket cookie-only for free. Cookie-only routes (key CRUD, add user, logout) get a second dependency that calls `get_current_user` and then rejects non-session auth with 403.

The WebSocket turn (`agent/ws.py::_handle_chat_message`, ~380 lines) only ever talks to its "socket" through `send_json`, and `agent/headless.py::RecordingSink` already proves a duck-typed sink works. The right extraction is small: split `_handle_chat_message` into (a) the lock wrapper and (b) a new `_run_chat_turn(websocket, chat_id, payload)` containing the existing body unchanged. WebSocket behaviour is identical; the HTTP endpoint takes the lock itself non-blockingly (`lock.locked()` -> 409), applies the optional KB selection under the lock, then calls `_run_chat_turn` with a recording sink and converts the recorded frames into a JSON response or an error status.

Three findings the planner must act on that CONTEXT.md did not anticipate: (1) **a pre-existing data bug** - deleting the user message after a failed turn nulls `chat.current_leaf_message_id` via `ON DELETE SET NULL`, orphaning the chat history; verified by experiment; the HTTP endpoint would expose this to every failed request, so it must be fixed in this phase (shared helper used by all four delete sites). (2) The `LLM_ERROR` frame carries no cause, so "LM Studio not running" (503) cannot be told from other model errors (502) without a small, backward-compatible addition to the frame. (3) A client disconnect does not cancel a plain Starlette handler, but D-14 should be guaranteed structurally with `create_task` + `asyncio.shield`, not by relying on server behaviour.

**Primary recommendation:** New module `agent/http_chat.py` (router: HTTP chat endpoint) and `agent/api_keys_api.py` (router: key CRUD); extend `get_current_user` with a Bearer branch; extract `_run_chat_turn` from `_handle_chat_message`; add an `ApiKey` SQLModel table (auto-created by `create_all`, no migration code needed); fix the leaf-restore bug with one shared helper.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Key generation, hashing, storage, revoke | API / Backend (Agent, port 8001) | Database (SQLite `apikey`) | Secrets never leave the Agent; only sha256 stored |
| Bearer resolution | API / Backend (`get_current_user`) | - | Single auth seam for REST |
| HTTP chat turn | API / Backend (Agent) | Database | Reuses the WS turn code in-process under the per-chat lock |
| Busy detection | API / Backend (`agent.state.chat_locks`, in-memory) | - | Lock is per-process state; no cross-process coordination needed |
| Live refresh of open chat | Agent `/ws/events` hub -> Browser | Browser (`handleEventFrame`) | Existing per-user event channel |
| Key list / create form / one-time modal / curl text | Browser (vanilla JS) | - | UI process only serves static files; calls Agent with cookie |
| External `curl` target | Agent port 8001 directly | - | The UI process (8000) does NOT proxy the API; `AGENT_BASE = http://<hostname>:8001` in app.js; Agent binds `BIND_HOST` (0.0.0.0 in LAN mode, Phase 18) |

## Standard Stack

No new dependencies (locked). Everything uses what is installed.

### Core (all already in `requirements.txt`; versions verified with `pip list` in this environment)
| Library | Version installed | Purpose | Why |
|---------|-------------------|---------|-----|
| fastapi | 0.141.1 | Routers, dependencies, `Request` header access | existing |
| starlette | 1.6.0 | Pure-ASGI middleware only (no `BaseHTTPMiddleware`) | existing |
| sqlmodel | 0.0.42 | `ApiKey` table | existing |
| stdlib `secrets`, `hashlib` | Python 3.13.15 | Key generation and sha256 hashing | same as session tokens in `shared/auth.py` |
| respx | 0.23.1 | LLM mocking in tests | existing |
| pytest / pytest-asyncio | 9.1.1 / asyncio_mode=auto | tests | existing |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| sha256 for key hash | argon2 (`password_hash`) | Keys are 256-bit random; a slow hash only adds per-request latency (argon2 ~50-100 ms on every REST call). sha256 is the right tool for high-entropy tokens and matches `hash_session_token`. |
| Hard delete (D-24) | `revoked_at` soft delete | Locked by D-24; hard delete also makes the 20-cap count trivial. |

**Installation:** none. **Package Legitimacy Audit:** no external packages are added; slopcheck not needed. `Packages removed: none`. `Packages flagged: none`.

## Architecture Patterns

### System Architecture Diagram

```
curl / script                                      Browser (UI :8000 static)
  | Authorization: Bearer aia_...                     | cookie session_id
  v                                                   v
Agent :8001  (HostMatchCORSMiddleware: no Origin -> pass)
  |
  +-- get_current_user ----+-- Authorization: Bearer present? --yes--> sha256 -> apikey row?
  |   (agent/dependencies) |                                            no  -> 401 (no cookie fallback, D-04)
  |                        |                                            yes -> user, auth_method="api_key",
  |                        |                                                   touch last_used_at (throttled)
  |                        +-- no header --> cookie path (unchanged), auth_method="session"
  |
  +-- cookie-only routes (api-keys CRUD, auth/users, auth/logout)
  |       Depends(get_session_user) = get_current_user + 403 if auth_method == "api_key"
  |
  +-- POST /api/v1/chats/{id}/messages  (HTTP chat)
        1. owner check (404)             2. parse body (kb_id omitted vs null via model_fields_set)
        3. lock = chat_locks.setdefault(id, Lock());  lock.locked() ? -> 409 CHAT_BUSY (nothing stored)
        4. task = create_task(run) ; await asyncio.shield(task)    <- survives client disconnect (D-14)
              run():  async with lock:
                        apply KB selection (validate owned+READY, write ChatRagConfig)   -> 404/422
                        sink = RecordingSink
                        ws._run_chat_turn(sink, chat_id, payload)    <- SAME body as WebSocket
                          (provider -> user msg -> settings -> context/compression -> MCP+tools
                           -> RAG -> LLM stream/tool rounds -> invariants -> persist -> facts
                           -> stats -> title job -> frames)
                      read frames: done?  -> load assistant Message.content by id -> 200 JSON
                                   error? -> map code -> HTTP status + {detail, code, ...}
                      hub.publish(user_id, {"type":"chat_updated","chat_id":id})  (only on success)
        5. JSON response
                                                   /ws/events (cookie) --> handleEventFrame
                                                     chat_updated -> loadChats(); if open chat and
                                                     !state.isStreaming -> re-read tree/stats/memory/tasks/rag
```

### Recommended Project Structure
```
shared/auth.py            # + generate_api_key(), hash_api_key(), API_KEY_PREFIX, API_KEY_PREFIX_DISPLAY_LEN
shared/models.py          # + ApiKey table
agent/dependencies.py     # get_current_user gains Bearer branch; + get_session_user (cookie-only)
agent/api_keys_api.py     # NEW router /api/v1/api-keys (POST/GET/DELETE), cookie-only
agent/http_chat.py        # NEW router POST /api/v1/chats/{chat_id}/messages
agent/ws.py               # _handle_chat_message split -> _run_chat_turn; leaf-restore helper; llm error cause
agent/rag_api.py          # + helper apply_chat_kb(session, chat, user_id, kb_id) reused by http_chat
agent/main.py             # include 2 routers; logout/create_user -> get_session_user
agent/schemas.py          # HttpChatRequest(MessagePayload), HttpChatResponse, ApiKeyCreate/Out/Created
ui/static/index.html      # #api-keys-section (between llm-providers and network), #api-key-modal
ui/static/app.js          # loadApiKeys, create/revoke, one-time modal, chat_updated handler
docs/*.md, tests/test_api_keys*.py, tests/test_http_chat*.py
```

### Pattern 1: Bearer branch in `get_current_user` (verified current code, `agent/dependencies.py:53`)
**What:** add `request: Request` to the dependency; if an `Authorization` header with scheme `Bearer` is present, authenticate by key only.
**Example (shape; planner finalises):**
```python
# shared/auth.py
API_KEY_PREFIX = "aia_"
API_KEY_DISPLAY_PREFIX_LEN = 8          # "aia_" + 4 random chars -> shown as "aia_3fK9…"

def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)      # 43 random chars, 256 bits

def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

# agent/dependencies.py
async def get_current_user(request: Request, session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
                           db: AsyncSession = Depends(get_session)) -> User:
    bearer = _bearer_token(request.headers.get("authorization"))   # None unless scheme is "Bearer" (case-insensitive)
    if bearer is not None:
        user = await _user_from_api_key(db, bearer)                # raises 401 "Invalid or revoked API key"
        request.state.auth_method = "api_key"
        return user
    ... existing cookie logic ...
    request.state.auth_method = "session"
    return user

async def get_session_user(request: Request, user: User = Depends(get_current_user)) -> User:
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(403, detail="Эта операция доступна только из веб-интерфейса (сессия), не по API-ключу")
    return user
```
Ordering guarantee: for a cookie-only route an invalid key yields 401 (from `get_current_user`), a valid key yields 403 (from `get_session_user`), matching D-02/D-04. Non-Bearer `Authorization` schemes (e.g. `Basic`) should be ignored (fall to cookie) - recommended; document it. An empty token (`Bearer ` with nothing) -> 401.
**`last_used_at`:** update only when `NULL` or older than 60 s (one small UPDATE + commit; `get_current_user` already commits on every cookie request, so the write cost is comparable). A test: first call sets it, an immediate second call leaves it unchanged.
**Header casing:** Starlette `request.headers` is case-insensitive.

### Pattern 2: Extract the turn, do not copy it
`_handle_chat_message` (ws.py:798-1179) is: set contextvars -> get/create lock -> `async with lock:` -> body. Split into:
```python
async def _handle_chat_message(websocket, chat_id, payload):   # unchanged public behaviour (tests patch/call it)
    lock = chat_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        await _run_chat_turn(websocket, chat_id, payload)

async def _run_chat_turn(websocket, chat_id, payload):          # old body from `current_chat_model.set(...)` on
    current_chat_model.set(payload.model); current_chat_provider_id.set(payload.provider_id)
    async with async_session_factory() as session: ...         # verbatim
```
Move the two `ContextVar.set` calls INSIDE `_run_chat_turn` (they are per-task; the HTTP path runs the turn in a spawned task, whose context is copied at `create_task` time, so setting inside the task is the robust choice). The headless runner reads helpers as `ws.<name>` attributes; keep every helper name stable.

### Pattern 3: HTTP wrapper (non-blocking lock, shielded task, sink -> response)
```python
lock = chat_locks.setdefault(chat_id, asyncio.Lock())
if lock.locked():                      # no await between this check and the acquire inside run() starting
    raise HTTPException(409, detail="...", ) with code CHAT_BUSY
task = asyncio.create_task(_run_http_turn(...))   # keep a strong ref: _http_turns.add(task); task.add_done_callback(_http_turns.discard)
result = await asyncio.shield(task)               # handler cancelled on disconnect -> task keeps running (D-14)
```
Race note: everything before the `locked()` check that awaits the DB (auth, ownership) is fine; there must be **no `await` between `lock.locked()` and the `async with lock` that starts the turn**. `asyncio.Lock.acquire()` on an unlocked lock does not yield, so check-then-acquire inside one task start is atomic with respect to other coroutines. Since `create_task` defers the task start to the next loop iteration, take the lock in the handler (`await lock.acquire()` right after the check; it returns without suspending) and pass ownership to the task, which releases it in `finally` - this removes the one-iteration gap entirely. (Planner: pick this "acquire in handler, release in task" form.)
Under client disconnect with a shielded task: the response is simply lost; the reply is in the tree (D-14). Server shutdown cancels tasks - acceptable.
No overall timeout is required (each httpx call already has `LLM_TIMEOUT`); a hard cap would contradict D-14. If one is wanted, apply it to `await asyncio.wait_for(asyncio.shield(task), N)` so the turn still completes (returns 504 to the client, reply saved).

### Pattern 4: Frames -> response / error
`RecordingSink` (agent/headless.py) drops `token` frames and keeps the rest. Do not accumulate tokens: on `done`, read the assistant text from the DB (`Message` by `done.message_id`), which is exactly the stored reply (includes gated RAG replies, fallback summaries and failure notes); the user message id is that row's `parent_id`.

Success body (D-13), suggested names:
```json
{"chat_id": 1, "user_message_id": 10, "assistant_message_id": 11, "content": "...",
 "model": "...", "stats": {...}, "tool_calls": [ {tool_call frames} ], "memory_writes": [], "task_writes": [],
 "invariant_conflict": null, "rag": null}
```
`tool_calls` = sink frames with `type == "tool_call"`. Non-terminal `TOOL_ERROR` frames in a turn that still reached `done` are not failures; they are already visible as `ok: false` tool_call entries - an optional `tool_errors` list is enough.

Terminal failure = no `done` frame and an `error` frame. Mapping (D-17/D-18):

| Frame | HTTP | `code` | Notes (user message after WS failure) |
|-------|------|--------|------|
| `CONTEXT_OVERFLOW` | 413 | `CONTEXT_OVERFLOW` + `suggested_strategy:"sliding"` | deleted -> `user_message_kept:false` |
| `PROVIDER_UNAVAILABLE` | 503 | `PROVIDER_UNAVAILABLE` | never stored |
| `LLM_ERROR`, cause = unreachable (httpx.ConnectError) | 503 | `LLM_UNREACHABLE` (suggested), detail "LM Studio is not running" for an LM Studio provider, "server <name> is not responding" otherwise | deleted |
| `LLM_ERROR`, other (HTTP error / timeout / parse) | 502 | `LLM_ERROR` | deleted |
| `RAG_GATED_FAILED` | 500 | `RAG_GATED_FAILED` | deleted |
| `{"type":"error","detail":"Chat N not found"}` (no code) | 404 | - | pre-checked by the wrapper, unreachable normally |
| busy | 409 | `CHAT_BUSY` | nothing stored |
| unexpected exception inside the turn | 500 | `INTERNAL_ERROR` | message state unknown -> do not claim; log `str(exc)` only |

Error body: `{"detail": str, "code": str, "user_message_kept": bool, ["suggested_strategy": str]}`. FastAPI `HTTPException(detail=...)` yields `{"detail": ...}` only; to include `code` return a `JSONResponse(status_code=..., content=...)` (or a custom exception handler). All existing frontend/`apiFetch` code reads `body.detail` as a string, so keep `detail` a string. Do NOT forget that chat-not-found / validation (422) bodies produced by FastAPI should stay in the default shape (docs must say so in the status table).

**LLM_ERROR cause (required small change):** the stream failure handlers (ws.py:949-965 and 1021-1038) emit `{"type":"error","detail":_llm_error_detail(exc,...),"code":"LLM_ERROR"}`; for a down LM Studio `llm_client.stream_chat` lets `httpx.ConnectError` propagate raw (only `TimeoutException` is specially handled, llm_client.py:173), so the frame text is just `LLM error: All connection attempts failed`. Introduce `_llm_error_frame(exc, provider_row) -> dict` used at both sites that adds an extra key, e.g. `"cause": "unreachable" | "timeout" | "upstream"`. Existing WS tests only index `frame["code"]`/`frame["detail"]` (grep `LLM_ERROR` in tests), and the frontend ignores unknown keys; document the optional field in API_SPEC WS section. The provider kind (to choose the "LM Studio is not running" wording) can be resolved by the HTTP wrapper via `resolve_client`'s provider row, or the helper can add `"provider_kind"`.

### Pattern 5: KB selection (D-06) - reuse validation, not the PUT route
`put_rag_config` resets `top_k` to the body default, so it cannot be called as-is for "keep the saved search settings". Add to `agent/rag_api.py`:
```python
async def apply_chat_kb(session, chat_id, user_id, kb_id: int | None) -> None:
    if kb_id is not None:
        kb = await _get_owned_kb(session, kb_id, user_id)          # 404 foreign/missing (existing)
        if kb.status != KbStatus.READY: raise _unprocessable(MSG_KB_NOT_READY)   # 422 (existing)
    row = await session.get(ChatRagConfig, chat_id) or new ChatRagConfig(chat_id=chat_id)
    row.kb_id = kb_id
    row.mode = MODE_RAG if kb_id is not None else MODE_OFF         # recommended answer to the open discretion item
    row.updated_at = now; commit (rollback on SQLAlchemyError)
```
Everything else on the row (top_k, threshold, flags, strict, history_turns) is untouched. Detect "field given" with Pydantic v2 `body.model_fields_set` (`"kb_id" in body.model_fields_set`): omitted -> skip entirely; explicit `null` -> off. Do this inside the lock and BEFORE `_run_chat_turn`, so a bad KB returns 404/422 with nothing stored. `MODE_RAG`/`MODE_OFF` live in `agent/rag.py`.
Caveat: `strict` defaults to True on new rows, so a first KB turn uses the strict "do not know" gate - that is the same as the UI; mention in docs.

### Pattern 6: `/ws/events` frame (D-19)
`hub.publish(user_id, {"type": "chat_updated", "chat_id": chat_id})` - non-blocking, never raises (agent/events.py). Publish after the turn finished successfully (the assistant message is committed before `done`). Optionally also after failures? Not needed: with the leaf fix the tree is unchanged on failure. Title generation publishes its own `chat_title_updated` later. `hub` import: `agent/http_chat.py` importing `agent.events` is safe (`agent.events` imports `agent.ws`, not the reverse; do not import `hub` from `agent/ws.py` at module top).
Frontend (`handleEventFrame`, app.js:3371): add before the `applySchedulerEvent` fall-through:
```js
if (frame && frame.type === 'chat_updated') { onChatUpdatedEvent(frame); return; }
```
`onChatUpdatedEvent`: `await loadChats()`; if `frame.chat_id === state.currentChatId && !state.isStreaming` then clear `childrenByParent`/`activeChildByParent` like `selectChat` does and call `loadChatTree`, `loadChatStats`, `loadChatMemory`, `loadChatTasks`, `loadChatRag` (RAG may have changed via kb_id) and scroll to bottom. Do NOT call `selectChat` (it early-returns for the same chat with an open WS, and would reconnect otherwise). `state.isStreaming` (app.js:18, set at ~1455) is the existing local-turn flag.

### Anti-Patterns to Avoid
- **Copying the 380-line turn body into the HTTP route.** Extract `_run_chat_turn` instead (headless.py already depends on `ws` helper names).
- **Calling `PUT /rag` logic for D-06** (resets top_k and search flags).
- **Awaiting between `lock.locked()` and acquisition** (breaks the 409 guarantee).
- **Caching key lookups** (breaks "revoked key stops working immediately").
- **Falling back to the cookie when a Bearer header is present** (violates D-04).
- **Putting the raw key in logs, exception messages, `repr`, or the list response.** Log `key_id`/`user_id` only.
- **Letting the `Authorization` header pass the browser Origin rule differently.** Mutating routes keep `Depends(require_allowed_origin), Depends(require_json_content_type)`; a request without `Origin` passes (Phase 18 D-05), which is why curl works.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Random key + hash | custom RNG/hash | `secrets.token_urlsafe`, `hashlib.sha256` (as `shared/auth.py`) | proven pattern in repo |
| Capturing a turn's output | a second turn implementation | `RecordingSink` + extracted `_run_chat_turn` | identical settings/memory/compression/RAG by construction |
| Owner check | new ownership code | `_get_chat_or_404` (agent/main.py) / `rag_api._get_owned_chat` | 404-not-403 IDOR convention already established |
| KB validation | new checks | `kb_api._get_owned_kb`, `KbStatus.READY`, `_unprocessable` | same messages as PUT /rag |
| Live notification | new channel | `agent.events.hub.publish` | per-user fan-out, bounded queues |
| Clipboard | new copy code | `copyTextToClipboard` (app.js:2135, HTTP-safe) | required for plain-HTTP LAN origins |
| DB table creation | migration script | `SQLModel.metadata.create_all` in `init_db` | new tables need no `migrate_*` function; `ApiKey` only has to be defined in `shared.models` (imported before `init_db` runs; add it to the explicit import list in `shared/database.py` for clarity) |
| Timestamps | `utcnow()` | `datetime.now(timezone.utc)` (project rule); normalise SQLite-naive values with `_as_aware_utc` when comparing | SQLite returns naive datetimes |

## Runtime State Inventory

Not a rename/refactor/migration phase - section omitted. (New table only; existing databases get it from `create_all` on next start.)

## Common Pitfalls

### Pitfall 1: Failed turn orphans the chat history (PRE-EXISTING, verified)
**What goes wrong:** On overflow / LLM error / gated-reply failure the WS code does `session.delete(user_msg); commit()` (ws.py:751, 863, 963, 1036). `Chat.current_leaf_message_id` is `ForeignKey("message.id", ondelete="SET NULL")` and the user message was just made the leaf, so the DB nulls the leaf. I reproduced it: after delete, `chat.current_leaf_message_id` read back `None`. The next turn then gets `parent_id=None` - the visible path restarts and the previous conversation disappears from the tree/context (and the "first message of a new chat" auto-title test `user_msg.parent_id is None` misfires).
**Why it matters here:** every HTTP error (413/502/503) would corrupt the chat; a retrying script would repeatedly orphan history.
**How to avoid:** one helper `_discard_user_message(session, chat, user_msg)` that sets `chat.current_leaf_message_id = user_msg.parent_id`, deletes the message and commits (rollback on error); use it at all four sites. This fixes WS too, so D-18 ("exactly what a WS turn leaves") stays true by construction. Add a regression test for both transports. Flag to the user in the plan as a bug fix riding along.
**Warning signs:** `GET /tree` returns only the latest messages after an error.

### Pitfall 2: Lock check races
See Pattern 3. Test with a pre-acquired lock (`lock = asyncio.Lock(); await lock.acquire(); agent_state.chat_locks[chat_id] = lock`) - deterministic, no timing. `conftest.py` clears `chat_locks` between tests (locks are bound to the test's loop).

### Pitfall 3: `ConnectError` indistinguishable from other LLM errors
See Pattern 4 (`cause`). Also tool-round errors: when `rounds.error` exists but tool results exist, the WS turn succeeds with a failure note in the text (200 for HTTP too); only the no-results case is an error.

### Pitfall 4: Cookie also sent with Bearer
Browsers' `fetch(..., credentials: 'include')` always send the cookie. With a Bearer header present the key must decide (D-04); a test must cover "valid cookie + bad key -> 401" and "valid cookie + revoked key -> 401".

### Pitfall 5: `apiFetch` 401 behaviour
`apiFetch` redirects to the login page on any 401. The key routes use cookie auth only, so that is fine; never add a key-based fetch to the browser UI.

### Pitfall 6: Modal policy test auto-discovers modals
`tests/test_modal_close_policy.py` scans every `id="...-modal"` in index.html and requires `id="btn-close-<stem>"` bound by `$('btn-close-<stem>').addEventListener('click'` in app.js, forbids backdrop listeners and any `Escape` handling. For `#api-key-modal` the button must be `btn-close-api-key` and bound exactly in that form. `tests/test_static_js_syntax.py` also parses app.js - run it.

### Pitfall 7: UI-SPEC path placeholder
UI-SPEC's curl uses an illustrative `/http-message` path; the final path chosen here is `POST /api/v1/chats/{chat_id}/messages` (no route currently uses `/messages`; verified by grep). Update the curl text builder to match.

### Pitfall 8: Sensitive data in logs / responses
Never log `Authorization`, the raw key, or the request body. structlog keys: `api_key_created key_id=, user_id=`, `api_key_revoked`, `http_chat_started chat_id=, model=`. Project rule: log `str(exc)` only.

### Pitfall 9: Cap race and name validation
Cap 20: `SELECT count(*) WHERE user_id=` then insert. Refuse with 409 `API_KEY_LIMIT`-style detail (Russian text, UI shows `detail` verbatim). Name: strip, 1..100 chars (UI maxlength 100); reject blank with 422. Duplicate names are allowed (UI-SPEC says names only distinguish keys).

## Code Examples

### Model (shared/models.py; follows `Session`)
```python
class ApiKey(SQLModel, table=True):
    """A named, revocable API key; only its sha256 hash is stored."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True))
    name: str = Field(max_length=100)
    key_hash: str = Field(unique=True, index=True)
    key_prefix: str = Field(max_length=16)          # "aia_3fK9" ; UI appends the ellipsis
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: Optional[datetime] = Field(default=None)
```
Table name auto = `apikey`. Cascade-on-user-delete uses `sa_column`, never `Field(ondelete=)` (project rule). Deleting a user is not exposed today but the FK keeps it correct.

### Key routes (cookie-only, `dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)]` on POST/DELETE as in other routers)
```
POST   /api/v1/api-keys        {name}  -> 201 {id, name, key (ONLY here), key_prefix, created_at, last_used_at: null}
GET    /api/v1/api-keys                -> [{id, name, key_prefix, created_at, last_used_at}]  newest first (no key, no hash)
DELETE /api/v1/api-keys/{id}           -> 204; 404 if missing or another user's
```
All three use `Depends(get_session_user)`. Return `Cache-Control: no-store` on the create response (nice-to-have).

### Test pattern (httpx AsyncClient + respx; see `tests/conftest.py`, `tests/test_rag_ws.py::_plain_content_response`)
```python
@respx.mock
async def test_http_chat_roundtrip(authenticated_client):
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(return_value=_plain_content_response("Ответ готов."))
    key = (await authenticated_client.post("/api/v1/api-keys", json={"name": "t"})).json()["key"]
    chat_id = (await authenticated_client.post("/api/v1/chats", json={"title": "x"})).json()["id"]
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")   # no cookie
    r = await anon.post(f"/api/v1/chats/{chat_id}/messages", json={"content": "hi", "model": "m"},
                        headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["content"] == "Ответ готов."
```
(`ASGITransport` is not intercepted by respx, so only the LM Studio `httpx` calls are mocked.) For ConnectError: `respx.post(...).mock(side_effect=httpx.ConnectError("refused"))`.
Existing WS tests use sync `TestClient` + `login_test_client`; HTTP tests are simpler with the async fixtures `authenticated_client` / `second_authenticated_client` (cross-user 404 for chat, foreign KB, and key routes).

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| Blocking on the per-chat lock (WS) | Non-blocking `locked()` -> 409 for HTTP | WS unchanged, HTTP never hangs |
| Handler lifetime = request lifetime | Spawned + shielded task | Reply survives disconnects (D-14) |

Starlette 1.6.0 / uvicorn 0.52.4: a plain `async def` route is not cancelled by client disconnect, but the shielded task makes D-14 independent of server version [ASSUMED: server behaviour; the shield makes it moot].

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Adding an optional `cause` key to the `LLM_ERROR` WS frame does not break the frontend or any test | Pattern 4 | Low: grep showed tests only index `code`/`detail`; run full suite to confirm |
| A2 | Ignoring non-Bearer `Authorization` schemes (fall back to cookie) is acceptable | Pattern 1 | Low; D-04 only speaks about `Bearer` |
| A3 | `mode="rag"` when a KB is given and the saved mode is `off` is the intended behaviour | Pattern 5 | Low; resolves the explicit discretion item. Confirm in plan |
| A4 | Fixing the leaf-NULL bug for WS as well is acceptable scope | Pitfall 1 | Medium: changes WS failure behaviour (strictly a bug fix); user should be told |
| A5 | Cloud-provider `ConnectError` also maps to 503 (code `LLM_UNREACHABLE`) | Pattern 4 | Low; D-17 only fixes the LM Studio/provider-unavailable wording |
| A6 | Throttled `last_used_at` (>=60 s) satisfies "last-used date" | Pattern 1 | Low; list shows a date/time, not exact seconds |

## Open Questions (RESOLVED)

1. RESOLVED (plan 19-02): **Leaf-restore fix (A4)** - recommended to include in Plan 1 together with the `_run_chat_turn` extraction since both touch the same four code sites. No user decision strictly required; call it out in the SUMMARY. Resolution: the fix ships in plan 19-02 together with the `_run_chat_turn` extraction (`_discard_user_message` restores `current_leaf_message_id`), is covered by `tests/test_ws_turn_failures.py`, and is declared as a WebSocket behaviour change in that plan's must_haves; the HTTP side is asserted in plan 19-04.
2. RESOLVED (plan 19-04): **Whole-turn timeout** - recommend none (D-14); optional `wait_for(shield(task))` returning 504 can be added later in Phase 20 with the concurrency work. Resolution: plan 19-04 adds no whole-turn timeout; the turn runs in a shielded task and completes even when the client disconnects (D-14).
3. RESOLVED (plan 19-04): **Event on failure** - not published (tree unchanged after the leaf fix). Revisit if the UI should show errors from HTTP turns (out of scope). Resolution: plan 19-04 publishes `chat_updated` only after a successful turn (D-19); a failed turn publishes nothing because plan 19-02 leaves the tree unchanged.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | - |
| pytest, pytest-asyncio, respx, httpx | tests | yes | 9.1.1 / - / 0.23.1 / 0.28.1 | - |
| LM Studio | manual UAT only | not probed (tests mock it) | - | respx in automated tests; manual curl needs a running model |
| curl | docs scenario / manual UAT | available in Git Bash | - | - |

No missing blocking dependencies. Nothing to install.

## Validation Architecture

Skipped: `workflow.nyquist_validation` is `false` in `.planning/config.json`. (Test plan guidance, not the formal section:)

- New files: `tests/test_api_keys.py` (create/list/revoke, one-time key, hash-only storage, cap 20, cross-user 404, cookie-only 403 for create/list/revoke/add-user/logout with a key, 401 invalid/revoked/revoked-with-valid-cookie, WS rejects a key), `tests/test_bearer_auth.py` (Bearer on a sample of routes incl. `GET/PUT /api/v1/mcp/servers` (D-08), `GET /api/v1/kb`, `GET /api/v1/chats`, ownership 404, `last_used_at` throttle), `tests/test_http_chat.py` (success body incl. stats/tool_calls/rag; tree contains both messages; settings/compression applied; 413 overflow with no_compression + tiny context_length and user message removed and leaf restored; 503 ConnectError; 502 HTTP 500 / timeout; 409 with pre-acquired lock and nothing stored; 404 foreign chat; KB omitted/null/id incl. foreign KB 404 and non-ready 422 and saved config; `chat_updated` event on a subscribed hub queue (`events_hub.subscribe(user_id)`); disconnect survival by cancelling the request task and then asserting the reply is stored), a WS regression test for the leaf-restore fix, `tests/test_modal_close_policy.py` / `test_static_js_syntax.py` reruns, and a static UI test like `test_network_ui.py` for the new section/ids.
- Quick command: `pytest tests/test_api_keys.py tests/test_http_chat.py -x -q`; full: `pytest tests/ -q`.
- `docs/TESTING_GUIDE.md` must get the new scenario list (CONTEXT canonical refs).

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | 256-bit random key, sha256 at rest, unique indexed hash, 401 on invalid, no cookie fallback |
| V3 Session Management | yes (boundary) | Keys are not sessions; key management and logout stay cookie-only so a leaked key cannot mint more keys or users |
| V4 Access Control | yes | Same ownership checks as cookie (`user_id` scoping, 404-not-403); cookie-only routes return 403 for keys |
| V5 Input Validation | yes | Pydantic models (`content` max 100 000, `model` 1..200, name 1..100, `provider_id>=1`), JSON content-type check |
| V6 Cryptography | yes | `secrets` + `hashlib` only, never custom |
| V7 Logging | yes | never log key/Authorization |

### Known Threat Patterns
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Key theft from DB | Information disclosure | store hash only; display prefix only |
| CSRF using a browser-held cookie against key routes | Tampering | existing `require_allowed_origin` + JSON content type; unchanged |
| Key used to escalate (create keys / users) | Elevation | D-01/D-02 cookie-only guard (403) |
| Brute-force of keys | Spoofing | 256-bit entropy makes it infeasible; rate limiting is Phase 20 |
| Timing side channel on lookup | Information disclosure | lookup by hash equality in DB (attacker cannot control hash); acceptable; `secrets.compare_digest` unnecessary |
| Token leakage via error text | Information disclosure | uniform 401 text, no echo of the presented token |
| Prompt/tool abuse via key (full tools incl. MCP) | Elevation | accepted by D-09; same as the cookie user |

## Documentation Deliverables (HCHAT-04)

- `docs/API_SPEC.md`: new sections "API keys (Day 30)" (routes, shapes, cookie-only note, 20 cap) and "HTTP chat (Day 30)" (route, body incl. `kb_id` omitted/null/id semantics, success body, status table with `detail`/`code`, CHAT_BUSY, disconnect semantics, `chat_updated` event added to the `/ws/events` section, `cause` field on WS `LLM_ERROR`), plus the full curl scenario. Remember the Agent address: `http://<host>:8001`, not the UI port; LAN mode requires `BIND_HOST=0.0.0.0`. Scenario steps (all `-H "Content-Type: application/json"`): login with `-c cookies.txt` -> `POST /api/v1/api-keys` with `-b cookies.txt` (note the key) -> `POST /api/v1/chats` with Bearer -> `GET /api/v1/lm-studio/models` / `POST /api/v1/lm-studio/load-model` -> `GET /api/v1/kb` -> `GET /api/v1/mcp/servers` and `PUT /api/v1/mcp/servers/{id}` `{"enabled": true}` -> `POST /api/v1/chats/{id}/messages` with and without `kb_id` -> `GET /api/v1/chats/{id}/tree` -> revoke with cookie and show 401. (Check the exact `PUT mcp/servers` body model `McpServerUpdate` in `agent/schemas.py` and the login body `LoginRequest` when writing it; Windows cmd quoting differs from bash - show bash.)
- `docs/ARCHITECTURE.md`: auth section gains Bearer keys; `docs/USER_GUIDE.md`: create a key, call the service; `docs/TESTING_GUIDE.md`: scenarios; `.planning/REQUIREMENTS.md`: reword KEY-04 per D-02.
- CLAUDE.md note: "No external identity providers" - API keys are consistent (equal capability, per-user). Optionally update the Hard constraints line "session via HTTP-only cookie (never JWT/localStorage)" to mention Bearer keys for REST.

## Project Constraints (from CLAUDE.md)

- No Docker/npm/Node/Redis/RabbitMQ/Celery; no `multiprocessing`/`os.fork`; IPC REST+WS only; in-memory per-process state.
- Frontend vanilla JS + CDN only; never insert HTML without `DOMPurify.sanitize()` (use `textContent`/`mcpEl`).
- Type hints everywhere; async I/O; `structlog` via `get_logger(__name__)` (no `print`); `datetime.now(timezone.utc)`.
- SQLModel FK cascade only via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`; `.is_(None)` in `where()`; always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- No bare `except:`; secrets only via `.env`; wrap `websocket.receive_json()` in `wait_for` (untouched).
- Deleting a chat must call `cleanup_chat_caches` (unchanged).
- Tests: pytest + pytest-asyncio auto, respx; separate test DB; consult `docs/TESTING_GUIDE.md`.
- Branch stays `Day30`; no Co-Authored-By Claude line in commits (user rule) even if a reminder suggests one; planning docs in English, UI strings Russian.

## Sources

### Primary (HIGH confidence - read in this session)
- `agent/dependencies.py`, `shared/auth.py`, `shared/models.py` (Session, Chat, ChatRagConfig), `shared/database.py` (`init_db`, `create_all`), `shared/origins.py`
- `agent/ws.py` (full turn, helpers, frames, error handling, lock), `agent/headless.py` (`RecordingSink`), `agent/state.py`, `agent/events.py`, `agent/titles.py` (event shape)
- `agent/main.py` (auth routes, middleware, router registration, LM Studio routes), `agent/rag_api.py`, `agent/network_api.py`, `agent/providers.py`, `agent/llm_client.py` (error propagation)
- `ui/main.py`, `ui/static/app.js` (`apiFetch`, `handleEventFrame`, `openSettingsModal`, `copyTextToClipboard`, `AGENT_BASE`), `ui/static/index.html` (settings sections), `tests/conftest.py`, `tests/test_modal_close_policy.py`, `tests/test_rag_ws.py`
- Experiment: delete of the leaf user message nulls `chat.current_leaf_message_id` (scratch script, run against a temp SQLite DB, removed afterwards)
- `pip list` for installed versions

### Secondary / Tertiary
- None. No web sources were needed; no library behaviour outside this repo was relied on (asyncio `Lock.acquire` fast path and `asyncio.shield` semantics are standard-library behaviour from training knowledge, tagged [ASSUMED] where used for guarantees; both are covered by the deterministic tests proposed above).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new packages; versions read from the environment
- Architecture: HIGH - every seam read in code; extraction points identified by line
- Pitfalls: HIGH for leaf bug (reproduced), MEDIUM for disconnect/lock semantics (standard-library behaviour, covered by tests)

**Research date:** 2026-10-12
**Valid until:** 2026-11-12 (stable internal code; re-check line references if `agent/ws.py` changes first)
