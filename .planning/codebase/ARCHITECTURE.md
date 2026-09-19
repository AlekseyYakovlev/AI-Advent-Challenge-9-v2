<!-- refreshed: 2026-09-19 -->
# Architecture

**Analysis Date:** 2026-09-19

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│              Browser UI (vanilla JS + CDN libs)                      │
│  `ui/static/index.html` + `ui/static/app.js`                         │
└────────────────────┬────────────────────────────────────────────────┘
                     │ HTTP/WebSocket (port 8000 → 8001)
┌────────────────────▼────────────────────────────────────────────────┐
│                  UI Server (FastAPI)                                │
│  `ui/main.py`, `ui/supervisor.py`                                   │
│  - Serves static frontend                                           │
│  - Manages Agent subprocess lifecycle via AgentSupervisor           │
│  - Health polling via GET /health (every 3 seconds)                │
└────────────────────┬────────────────────────────────────────────────┘
                     │ asyncio.create_subprocess_exec
                     │ Port 8001 (spawned subprocess)
┌────────────────────▼────────────────────────────────────────────────┐
│                Agent Server (FastAPI)                               │
│  `agent/main.py`, `agent/ws.py`                                     │
│  - REST API (/api/v1/chats, /api/v1/settings, etc.)                │
│  - WebSocket streaming chat endpoint (/ws/chat/{chat_id})           │
│  - LLM integration (DeepSeek cloud + LM Studio local)               │
│  - Context compression and statistics                              │
└────────────┬──────────────────────────────────────────┬─────────────┘
             │                                          │
             ▼                                          ▼
┌──────────────────────────────────────┐  ┌──────────────────────────┐
│  SQLite Database (sqlite+aiosqlite)  │  │  LLM Backends            │
│  `app.db`                            │  │  - DeepSeek API          │
│  `shared/database.py`                │  │  - LM Studio             │
│  - Chat (tree structure)             │  │  `agent/llm_client.py`   │
│  - Message (parent_id)               │  │                          │
│  - Settings (global + per-chat)      │  └──────────────────────────┘
│  - TokenUsage (stats)                │
└──────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **UI Server** | Static file serving, agent process management | `ui/main.py` |
| **Agent Supervisor** | Agent subprocess lifecycle, health checks, orphan cleanup | `ui/supervisor.py` |
| **Agent Server** | REST API, WebSocket chat, LLM calls, context building | `agent/main.py`, `agent/ws.py` |
| **Context Engine** | Message tree loading, compression strategies, statistics | `agent/context_engine.py` |
| **LLM Client** | OpenAI-compatible streaming, token counting, model management | `agent/llm_client.py` |
| **Database** | Async SQLite, migrations, session management | `shared/database.py` |
| **Models** | SQLModel schemas for Chat, Message, Settings, TokenUsage | `shared/models.py` |
| **Frontend** | JavaScript SPA, WebSocket listener, UI state management | `ui/static/app.js` |

## Pattern Overview

**Overall:** Two-process, single-user, local-first AI chat application with a message-tree data model and pluggable context compression strategies.

**Key Characteristics:**
- **Process isolation:** UI and Agent are separate Python processes, communicating only via HTTP/WebSocket
- **Message tree, not flat log:** Every message has a `parent_id`, enabling branching and non-linear conversations
- **Database-first history:** Full conversation history always persists; compression only affects LLM context window, never modifies stored messages
- **Per-chat settings with global fallback:** Settings can be overridden per-chat; `NULL` chat_id means global defaults
- **Pluggable LLM backends:** OpenAI-compatible API for DeepSeek (cloud) and LM Studio (local)
- **Async-first:** All I/O is async (asyncio, aiosqlite, httpx) with typed dependencies
- **No Docker, no npm, no external services:** Single SQLite DB, standard Python packages only

## Layers

**Frontend (Vanilla JS):**
- Purpose: User-facing chat UI, message rendering, tree branching controls
- Location: `ui/static/`
- Contains: HTML/CSS (Tailwind, Marked.js, DOMPurify), JavaScript state management
- Depends on: HTTP/WebSocket API exposed by Agent
- Used by: End user's browser

**UI Server (FastAPI):**
- Purpose: Static file serving, agent lifecycle management
- Location: `ui/main.py`
- Contains: File serving, CORS middleware, lifespan hooks
- Depends on: `ui/supervisor.py`, `shared/config.py`
- Used by: Browser (static files), supervisor owns Agent process

**Agent Supervisor (Background Task):**
- Purpose: Monitor and restart the Agent subprocess
- Location: `ui/supervisor.py`
- Contains: Process spawning via `asyncio.create_subprocess_exec`, health polling, orphan cleanup
- Depends on: `psutil`, `httpx`, logging
- Used by: UI server's lifespan context manager

**Agent Server (FastAPI):**
- Purpose: Main API and WebSocket endpoint
- Location: `agent/main.py`, `agent/ws.py`
- Contains: REST endpoints for chats/messages/settings, WebSocket chat streaming
- Depends on: `agent/context_engine.py`, `agent/llm_client.py`, `shared/database.py`
- Used by: Frontend (HTTP/WebSocket)

**Context Compression & Stats (Deterministic Slicing):**
- Purpose: Build LLM context and calculate usage statistics
- Location: `agent/context_engine.py`
- Contains: Four compression strategies (sliding, sticky, truncate_middle, no_compression), fact extraction
- Depends on: SQLAlchemy queries, LLM token counting
- Used by: WebSocket handler during message streaming

**LLM Integration (OpenAI-Compatible):**
- Purpose: Streaming completions and local model management
- Location: `agent/llm_client.py`
- Contains: `LLMClient` (streaming), `LMStudioClient` (control API)
- Depends on: `httpx`, `tiktoken`
- Used by: WebSocket handler, context engine (for facts extraction)

**Shared Infrastructure:**
- **Database:** `shared/database.py` — async engine, session factory, migrations
- **Models:** `shared/models.py` — SQLModel schemas (Chat, Message, Settings, TokenUsage)
- **Config:** `shared/config.py` — environment-based settings (ports, API keys, LM Studio URL)
- **Logging:** `shared/logger.py` — structlog JSON output, bound loggers per module

**In-Memory State (Agent Process):**
- Purpose: Concurrency guards, rate limiting, active stream tracking
- Location: `agent/state.py`
- Contains: `active_streams`, `chat_locks`, `ws_rate_limiter`, cleanup function
- Used by: WebSocket handler, broadcast functions

## Data Flow

### Primary Request Path (User sends a message)

1. **Frontend:** User types message, clicks send (or hits Enter)
   - `ui/static/app.js::handleSendMessage()` collects content + selected model
   - Sends via WebSocket: `{ content: "...", model: "..." }`

2. **WebSocket Accept & Validation** (`agent/ws.py::ws_chat`)
   - Validate origin header via `_validate_origin()` (CORS origins in `agent/state.py`)
   - Check rate limit: 10 messages per 60 seconds per chat
   - Parse and validate `MessagePayload` schema

3. **Lock Acquisition** (`agent/ws.py::_handle_chat_message`)
   - Acquire per-chat lock (ensures serialized message processing)
   - Load chat from database or return 404

4. **User Message Persistence** (`agent/ws.py::_persist_user_message`)
   - Create `Message(chat_id, parent_id=chat.current_leaf_message_id, role="user", content)`
   - Set `chat.current_leaf_message_id = user_msg.id` (advance branch pointer)
   - Commit to database

5. **Settings Resolution** (`agent/context_engine.py::get_effective_settings`)
   - Query per-chat settings; fall back to global (`chat_id.is_(None)`)
   - Resolve strategy, temperature, context_length, max_tokens

6. **Context Building** (`agent/context_engine.py::build_llm_context`)
   - Load full message tree from root to current leaf via `_load_branch_messages()`
   - Apply compression strategy: `_apply_compression_strategy()`
   - Build system prompt with optional facts/summary
   - Return `[{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}, ...]`

7. **LLM Streaming** (`agent/llm_client.py::stream_chat`)
   - POST to `/v1/chat/completions` with OpenAI-compatible payload
   - Parse SSE stream, yield tokens as they arrive
   - Accumulate full response in `assistant_text`

8. **Token Streaming to Frontend** (`agent/ws.py::_handle_chat_message`)
   - Send each token via WebSocket: `{ type: "token", content: "..." }`
   - Frontend appends to current message in real-time

9. **Assistant Message Persistence** (`agent/ws.py::_persist_assistant_message`)
   - Create `Message(chat_id, parent_id=user_msg.id, role="assistant", content)`
   - Set `chat.current_leaf_message_id = assistant_msg.id` (advance again)
   - Commit to database

10. **Stats Calculation** (`agent/context_engine.py::compute_chat_stats`)
    - Count tokens in LLM context (what was actually sent)
    - Sum request/response tokens across full tree (not compressed)
    - Calculate usage percentage: `(current_context_size / context_length) * 100`

11. **Facts Extraction** (`agent/context_engine.py::extract_and_update_facts`, debounced)
    - After 2 seconds (debounced), call LLM to extract key facts as JSON from user message
    - Merge new facts into `Settings.facts_json` (existing keys overwritten)
    - Persist to database

12. **WebSocket Done Message** (`agent/ws.py::_handle_chat_message`)
    - Send: `{ type: "done", message_id: assistant_msg.id, stats: {...} }`
    - Frontend marks message as complete, updates stats panel

### Settings/Chat Management Flow

**Get Chat List:**
- GET `/api/v1/chats` → Query all chats ordered by `created_at DESC`

**Create Chat:**
- POST `/api/v1/chats` with `{title}` → Insert `Chat`, return with `id`

**Get Chat Tree:**
- GET `/api/v1/chats/{chat_id}/tree` → Walk from `current_leaf_message_id` up to root via `parent_id`, reverse to root-first order

**Branch to Message:**
- POST `/api/v1/chats/{chat_id}/branch` with `{message_id}` → Repoint `chat.current_leaf_message_id` to message_id (no data copy)

**Get Settings:**
- GET `/api/v1/settings?chat_id={id}` → Resolve per-chat or global; return effective settings

**Update Settings:**
- PUT `/api/v1/settings` with `{chat_id?, system_prompt?, temperature?, ...}` → Create or update row, cascade to all future turns

**LM Studio Model Management:**
- GET `/api/v1/lm-studio/models` → List available models
- POST `/api/v1/lm-studio/load-model` with `{model_id, gpu_offload}` → Load via control API
- POST `/api/v1/lm-studio/unload-model/{model_id}` → Unload

**State Management:**
- `state.py::cleanup_chat_caches(chat_id)` called on chat deletion to remove in-memory locks, streams, rate-limit records

## Key Abstractions

**Message Tree:**
- Purpose: Enable non-linear conversations (branching, re-editing, rewinding)
- Examples: `shared/models.py::Message`, `agent/main.py::_build_tree_path()`
- Pattern: Each message has `parent_id` (nullable). `Chat.current_leaf_message_id` marks the active tip. Walking parent links reconstructs the branch.

**Context Compression Strategies:**
- Purpose: Manage LLM context window without modifying stored history
- Examples: `agent/context_engine.py::_apply_compression_strategy()`
- Pattern: Deterministic slicing of messages—no summarization, no database updates. Only affects outbound LLM calls.

**Settings Fallback Resolution:**
- Purpose: Support per-chat customization while maintaining global defaults
- Examples: `agent/context_engine.py::get_effective_settings()`
- Pattern: Query `Settings` with `chat_id == <id>`; if not found, query with `chat_id.is_(None)` (global row).

**Per-Chat Concurrency Locks:**
- Purpose: Serialize message processing within a single chat (prevent race conditions on `current_leaf_message_id`)
- Examples: `agent/state.py::chat_locks`, `agent/ws.py::_handle_chat_message()` async with pattern
- Pattern: `dict[int, asyncio.Lock]` keyed by chat ID, auto-cleanup on chat deletion.

**Supervisor Health Polling:**
- Purpose: Detect Agent crashes and auto-restart
- Examples: `ui/supervisor.py::_healthcheck_loop()`, `_ping_health()`
- Pattern: Every 3 seconds (configurable), GET `/health`. On timeout/404, restart subprocess.

## Entry Points

**run.py:**
- Location: `run.py`
- Triggers: `python run.py` from CLI
- Responsibilities:
  - Cleanup orphan processes on ports 8000/8001 (handles unclean shutdowns)
  - Spawn UI server on port 8000
  - UI server's lifespan hook starts AgentSupervisor (which spawns Agent subprocess)

**UI Server Entry:**
- Location: `ui/main.py::app` (FastAPI instance)
- Entry via: `uvicorn ui.main:app` (wrapped in lifespan)
- Responsibilities:
  - Mount static files at `/static`
  - Serve `index.html` at `/`
  - CORS middleware (allow localhost:8000)
  - Lifespan startup: Create AgentSupervisor and await `supervisor.start()`

**Agent Server Entry:**
- Location: `agent/main.py::app` (FastAPI instance)
- Entry via: `uvicorn agent.main:app` (spawned by supervisor, not run directly)
- Responsibilities:
  - Initialize database on startup (`init_db()`)
  - Ensure global settings row exists (`_ensure_global_settings()`)
  - Register all REST and WebSocket routes
  - Dispose SQLAlchemy engine on shutdown

**WebSocket Entry:**
- Location: `agent/main.py::websocket_chat_endpoint`, delegates to `agent/ws.py::ws_chat()`
- Route: `WS /ws/chat/{chat_id}`
- Flow: Accept → validate origin → message loop → handle_chat_message

## Architectural Constraints

- **Threading:** Single-threaded event loop per process (asyncio). No `multiprocessing` or `os.fork`. Agent is a separate OS process via `create_subprocess_exec`.
- **Global state per process:** In-memory dicts in `agent/state.py` (active_streams, chat_locks, ws_rate_limiter). Each process instance is independent; UI and Agent do not share memory.
- **SQLite single-writer:** Database uses WAL mode and 5s busy timeout. Multiple async tasks within Agent can query concurrently, but only one write at a time. Per-chat locks (`agent/state.py::chat_locks`) further serialize writes to a single chat's message tree.
- **Circular dependencies:** None detected. Import order: stdlib → third-party → local. `shared/` is imported by both processes and does not import from `ui/` or `agent/`.
- **Foreign key enforcement:** Enabled via SQLite pragma. `ondelete="CASCADE"` on Message.chat_id cleans up messages when chat is deleted; `ondelete="SET NULL"` on Chat.current_leaf_message_id allows safe deletion of a message.

## Anti-Patterns

### Bare `except:` blocks

**What happens:** Code catches all exceptions without discrimination.

**Why it's wrong:** Masks bugs, swallows KeyboardInterrupt/SystemExit, makes debugging harder.

**Do this instead:** Use specific exception types. Example in `shared/database.py::retry_on_locked_db()`:
```python
except OperationalError as exc:
    if "locked" not in str(exc).lower():
        raise
```

### Hardcoded secrets

**What happens:** API keys or credentials appear in source code.

**Why it's wrong:** Accidental commits, repository leaks.

**Do this instead:** Load from `.env` via `shared/config.py::Settings`, which uses pydantic-settings. See `DEEPSEEK_API_KEY`, `LM_STUDIO_BASE_URL`.

### Modifying database history for compression

**What happens:** Storing a summarized or truncated message tree in the database to save space.

**Why it's wrong:** Original context is lost forever; cannot audit or re-analyze conversation; future strategies cannot recover full information.

**Do this instead:** Store full history in database; compression only affects the LLM context window. See `agent/context_engine.py::_apply_compression_strategy()` — it returns a sliced list but never calls `session.delete()`.

### Omitting `await session.commit()` or `await session.rollback()`

**What happens:** Changes are not persisted; exceptions leave session in inconsistent state.

**Why it's wrong:** Silent data loss; subsequent queries return stale data.

**Do this instead:** Always commit after writes, rollback in exception handlers. Example in `agent/main.py::delete_chat()`:
```python
try:
    await session.delete(chat)
    await session.commit()
except Exception:
    await session.rollback()
    raise
```

### Duplicate `parent_id` logic

**What happens:** Tree traversal (loading branch messages) reimplemented in multiple places.

**Why it's wrong:** Bug fixes and changes to tree logic must be applied everywhere; easy to diverge.

**Do this instead:** Centralize in `_build_tree_path()` (used by REST endpoints) and `_load_branch_messages()` (used by context engine). Both walk `parent_id` consistently.

## Error Handling

**Strategy:** Exceptions bubble up to the FastAPI exception handler or WebSocket disconnect logic. Contextual logging via structlog. User-facing errors are sent via WebSocket JSON messages with a `type: "error"` and optional error code.

**Patterns:**

1. **HTTP endpoints:** FastAPI catches exceptions, returns 4xx/5xx responses. Example:
   ```python
   raise HTTPException(status_code=404, detail="Chat not found")
   ```

2. **WebSocket errors:** Send JSON error message; do not close the socket (allows recovery). Example:
   ```python
   await websocket.send_json({"type": "error", "detail": "...", "code": "CONTEXT_OVERFLOW"})
   ```

3. **Database errors:** Wrap in `try/except`, always rollback on exception. Log the error. Example:
   ```python
   try:
       await session.commit()
   except Exception as exc:
       await session.rollback()
       logger.error("update_failed", error=str(exc))
       raise
   ```

4. **LLM stream errors:** Log, send error message via WebSocket, delete the unpersisted user message to avoid orphaned half-conversations. Example in `agent/ws.py::_handle_chat_message()`.

5. **Agent process crash:** Supervisor detects via health check timeout, logs `agent_unhealthy`, restarts subprocess. Concurrent WebSocket clients will see connection drops and auto-reconnect via browser logic.

## Cross-Cutting Concerns

**Logging:** Structured JSON via structlog (`shared/logger.py`). Every major action is logged with context (chat_id, strategy, tokens, etc.). Log output goes to stdout; capture/centralize as needed.

**Validation:** Pydantic schemas in `agent/schemas.py` validate inbound HTTP and WebSocket payloads. SQLModel validates database operations. Size limits on content (100KB), facts_json (50KB), system_prompt (10KB).

**Authentication:** None. Single-user local-first design. CORS allows only localhost:8000 for WebSocket; origin validation in `_validate_origin()`.

**Rate Limiting:** Per-chat, per-minute: max 10 messages per 60 seconds (configurable in `agent/ws.py`). Checked before message processing; excess messages rejected with error message.

**Concurrency:** Per-chat lock (`agent/state.py::chat_locks`) ensures only one message is being processed per chat at a time. Global LM Studio model switch lock (`agent/llm_client.py::LMStudioClient._model_switch_lock`) prevents concurrent model loads.

---

*Architecture analysis: 2026-09-19*
