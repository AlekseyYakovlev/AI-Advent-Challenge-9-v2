# Architecture Guide

## Process Communication
- UI launches Agent via asyncio.create_subprocess_exec
- Healthcheck: GET /health every 3 seconds
- Orphan cleanup: psutil to find/kill processes on port 8001

## Context Compression Strategies

The system implements 4 context compression strategies (`agent/context_engine.py::_apply_compression_strategy`)
that control what is sent to the LLM. All are deterministic slicing/dropping of messages —
**none of them summarize the dropped content**; that content simply isn't sent to the LLM this turn.
**Important:** Database history is NEVER modified - strategies only affect LLM context.

### 1. Sliding Window (sliding)
- **Behavior:** Keeps only the last `RECENT_MESSAGE_COUNT` (10) messages; everything older is dropped from the LLM context entirely
- **When triggered:** Total tokens exceed 75% of `context_length` (`SUMMARY_TRIGGER_RATIO`), or the chat already has more than 10 messages
- **Best for:** Fast chats, maintaining recent context
- **Database:** All messages preserved

### 2. Sticky Facts (sticky)
- **Behavior:** Keeps the first message of the active branch + the last 10 messages (no overlap); the middle is dropped, not summarized
- **When triggered:** Same threshold as above (75% of `context_length`)
- **Best for:** Long consultations where the original instruction/goal must stay visible, combined with the facts mechanism below
- **Database:** All messages preserved

### 3. Truncate Middle (truncate_middle)
- **Behavior:** Keeps the first 2 messages + the last 10 messages (no overlap); the middle is dropped, not summarized
- **When triggered:** Same threshold as above (75% of `context_length`)
- **Best for:** Preserving initial instructions and recent context
- **Database:** All messages preserved

### 4. No Compression (no_compression)
- **Behavior:** Sends all messages to LLM without compression
- **When triggered:** Always (no compression applied)
- **Protection:** Raises ContextOverflowError if tokens exceed context_length
- **Best for:** Short conversations, when full context is critical
- **Database:** All messages preserved

## Key-Value Facts Extraction

Independent of which compression strategy is selected, `agent/context_engine.py::extract_and_update_facts`
runs after every user message:
- Debounced 2s (`FACTS_DEBOUNCE_SECONDS`) per chat — rapid consecutive messages coalesce into one extraction call
- Calls the LLM to extract key facts (goal, constraints, preferences, decisions) as a JSON object
- Merges the result into `Settings.facts_json` (new keys overwrite old ones on conflict)
- `build_system_prompt` injects the accumulated facts into the system prompt on every turn, **for every strategy**, not just `sticky`

`Settings.summary_text` still exists in the schema and API (`PUT /api/v1/settings`) for backward
compatibility, and is injected into the system prompt when non-empty, but nothing in the current
codebase writes to it automatically — `summarize_if_needed()` is a no-op stub, and the field has no
UI control. It can only be populated by calling the settings API directly.

## Branching (message forking)

Branching is a separate feature from context compression, not a `ContextStrategy` value (an earlier
`"branching"` strategy enum value was migrated away — see `migrate_strategies.py`). It works directly
on the message tree:
- `Chat.current_leaf_message_id` marks a checkpoint; `POST /api/v1/chats/{id}/branch` repoints it to any
  earlier message without copying data
- Sending a new message after branching to an earlier point creates a sibling under that message's
  `parent_id`, forking the conversation into two independent branches
- The UI (`ui/static/app.js`) exposes "branch from message" plus prev/next sibling controls to fork and
  switch between branches

## Context Overflow Protection

When NO_COMPRESSION strategy is active and total tokens exceed context_length:
1. System raises ContextOverflowError
2. User message is deleted from database (no response generated)
3. WebSocket sends error with code "CONTEXT_OVERFLOW"
4. UI displays red banner and blocks input
5. User must change strategy or reduce conversation length

## Statistics Tracking

Each message stores token_count in database. Real-time statistics include:
- total_request_tokens: Sum of all user message tokens
- total_response_tokens: Sum of all assistant message tokens
- current_context_size: Tokens that will be sent to LLM (strategy-dependent)
- context_window_size: From settings.context_length
- context_usage_percent: Percentage of context window used

Statistics update after each message and are sent with WebSocket "done" message.

## LLM Integration

### DeepSeek (Cloud)
- OpenAI-compatible API
- Streaming via SSE
- Token counting with tiktoken (cl100k_base)

### LM Studio (Local)
- OpenAI-compatible API at /v1/
- Control API at /api/v0/ for model management
- Endpoints:
  - GET /v1/models - List available models
  - POST /api/v0/models/load - Load model into RAM/VRAM
  - POST /api/v0/models/unload - Unload model
- model_switch_lock prevents concurrent model loading
- Emergency unload (5s timeout) on load timeout
- Detection of "LM Studio not running" via ConnectError

## Chat tool calls (built-in + MCP)

Each chat turn sends the LLM one tool list: the six built-in tools (memory and task tools) plus
the tools of the current user's MCP servers that are enabled and have a live session. The list is
rebuilt per turn from the session registry (`agent/mcp_client.py::get_live_tools`), never with a
ping, so a busy server is not probed on the chat path. A server that is disconnected, disabled or
owned by another user adds nothing, and the registry is keyed by `(user_id, server_id)`.

**Auto-connect:** when `MCP_AUTO_CONNECT` is true (default), each chat turn first connects, lazily
and concurrently, the chat owner's enabled MCP servers that have no live session and no remembered
failure (`agent/mcp_client.py::ensure_connected`). It never replaces a live session, so concurrent
turns share it. A failed connect is remembered and not retried until the user presses "Подключить",
edits, or disconnects the server; disabled servers and other users' servers are never touched. The
first turn after app start may be delayed by up to `MCP_CONNECT_TIMEOUT` (10 s, plus a couple of
seconds of process teardown) when a server hangs at handshake. Set it to false for manual connect
only.

- **Naming:** MCP tools are exposed as `mcp__<server-slug>__<tool>` (only `[a-zA-Z0-9_-]`, at most
  64 characters). Duplicate server slugs get a `-<server_id>` suffix; sanitization or length
  collisions get an 8-character sha1 suffix. Built-in names are reserved, so a server cannot shadow
  one. Dispatch resolves a call only through the per-turn name-to-binding map.
- **Single tool round:** the first LLM request carries the tools; after the calls run in order, the
  follow-up request carries the results as `role=tool` messages and no tools. A second tool round
  in the same turn (for example list, then read) needs another user message.
- **Empty arguments:** a no-argument call can arrive with `arguments == ""`. MCP calls treat it as
  `{}`, and the assistant message echoed to the follow-up request carries `"{}"` (on a copy),
  because providers reject an empty string there.
- **Limits:** `MCP_TOOL_CALL_TIMEOUT` (default 30 s) bounds one call; `MCP_TOOL_RESULT_MAX_CHARS`
  (default 20000) caps the text sent to the model, with a `...[truncated N chars]` marker.
- **Failures are tool results:** a disconnected server, a dead process, a timeout, invalid
  arguments or `isError=true` all come back to the model as a tool result. The WebSocket turn still
  ends with exactly one `done` frame. MCP failures are reported through the `tool_call` frame
  (`ok=false`), not `TOOL_ERROR`. Each executed call is announced with a `tool_call` frame, which
  the UI renders as a collapsed card; cards are not stored in the database and are lost on a full
  page reload.

**Known risks:** filesystem-style servers expose destructive tools (write, edit, move, create) and
they run without a confirmation step; the server's allowed-directory list is the only bound. Tool
descriptions and results are untrusted text from an external process and can carry prompt
injection; results only reach the model as `role=tool` content and the follow-up request has no
tools, which limits but does not remove that risk.

**Child processes on Agent restart:** the supervisor stops the Agent with `terminate()`, which is a
hard kill on Windows, so the lifespan shutdown (`cleanup_all_sessions`) does not run. MCP servers
are still not orphaned: the mcp SDK (1.30, `mcp/os/win32/utilities.py`, `create_windows_process`)
starts each stdio server inside a Job Object created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
(`_create_job_object`, `_maybe_assign_process_to_job`). The Agent holds the only handle to that
job, so when Windows tears the Agent down it kills the servers too, even ones that ignore their
stdin closing. The guard depends on pywin32 (`win32job`, a Windows dependency of `mcp`); if
creating or assigning the job fails, the SDK only logs a warning and runs without it.
`tests/test_mcp_orphan.py` hard-terminates a helper process that holds a session and checks that
its server child disappears.

**Residual risk (REST origin check):** any authenticated user can register and start an arbitrary
executable through the MCP routes; that is by design under the equal-admin model. The mutating MCP
routes (create, update, delete, connect, disconnect) now reject a foreign `Origin` with 403
(`agent/dependencies.py::require_allowed_origin`, allowed origins are `CORS_ORIGINS`), and create
and update also reject a body that is not `application/json` with 415. A request without an
`Origin` header is allowed, so curl and other non-browser clients keep working. Mutating routes
outside MCP are not covered by this dependency and still rely on `SameSite=Lax` cookies plus CORS.
An origin check does not stop script running inside the app's own origin (XSS).
