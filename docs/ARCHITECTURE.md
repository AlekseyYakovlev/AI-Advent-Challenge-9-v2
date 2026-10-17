# Architecture Guide

## Process Communication
- UI launches Agent via asyncio.create_subprocess_exec
- Healthcheck: GET /health every 3 seconds
- Orphan cleanup: psutil to find/kill processes on port 8001
- Both processes bind to the single `BIND_HOST` setting (`127.0.0.1` default, `0.0.0.0` for LAN mode); the supervisor passes it as `--host` and keeps probing `/health` on `127.0.0.1`

## Network exposure and the Origin rule

`BIND_HOST` is validated in `shared/config.py` at import time; an unsupported value exits with code 2.

**Origin rule.** An Origin is accepted when it is `http`, its host equals the host of the request `Host` header and its port equals `UI_PORT`. `http://localhost:{UI_PORT}` and `http://127.0.0.1:{UI_PORT}` are always accepted. A missing Origin is allowed (non-browser clients, still gated by the session cookie). `Origin: null` and Agent-port origins are rejected. The rule has no mode branch and no IP or name lists.

| Enforcement point | Where |
|-------------------|-------|
| REST CORS | `HostMatchCORSMiddleware` on both apps |
| REST mutations | `require_allowed_origin` |
| WebSockets | `_validate_origin` on `/ws/chat` and `/ws/events` |

All three call `shared.origins.is_allowed_origin`. The address list for the console banner and `GET /api/v1/network` comes from `shared.network.reachable_urls`.

**Known limits**
1. DNS rebinding: a hostile domain that resolves to this machine's LAN address passes the host-match rule, but the session cookie is host-only and is not sent to that name, so only unauthenticated endpoints are reachable. No Host allowlist is implemented, by decision.
2. `/health` is open by design (the supervisor and the verification script probe it). `/debug/routes` needs authentication (session cookie or API key). Login has a lockout, see "Limits and the generation queue (Day 30)".
3. Traffic is plain HTTP, by decision.
4. The app never touches the firewall.

## API keys and HTTP chat (Day 30)

**Two credentials.** The browser and both WebSockets use the HTTP-only session cookie. REST clients may instead send `Authorization: Bearer aia_...`. Both are resolved in one place, `agent/dependencies.py::get_current_user`, which records the method in `request.state.auth_method` (`session` or `api_key`). A Bearer header alone decides: an invalid or revoked key is a 401 and the cookie is not consulted. The key is looked up by its sha256 hash on every request (no cache), so a revoked key stops working at once.

**Cookie-only routes.** `get_session_user` wraps `get_current_user` and answers 403 when the caller used a key. It guards key management (`/api/v1/api-keys`), adding a user (`POST /api/v1/auth/users`) and logout (`POST /api/v1/auth/logout`): a leaked key must not be able to mint more keys, create accounts or end the owner's session. The WebSocket endpoints accept only the cookie.

**Storage.** The `apikey` table holds `user_id`, `name`, `key_hash` (sha256), the 8-character `key_prefix`, `created_at` and `last_used_at`; the full key exists only in the creation response. `last_used_at` is written at most once per 60 seconds. Revoking deletes the row.

**HTTP chat turn** (`agent/http_chat.py`, `POST /api/v1/chats/{chat_id}/messages`):
1. The chat is loaded with an owner check (404 otherwise).
2. The per-chat lock is checked without waiting; a held lock answers `409 CHAT_BUSY` and nothing is stored. Otherwise it is acquired with no `await` in between.
3. The turn runs in a background task awaited through `asyncio.shield`, so a client disconnect does not cancel it; the task releases the lock in its outermost `finally`.
4. When `kb_id` is present in the body, the knowledge base and RAG mode are saved for the chat under the lock (`rag_api.apply_chat_kb`).
5. `ws._run_chat_turn` runs the turn with a `RecordingSink` instead of a socket.
6. The terminal error frame, if any, is mapped to a status (`CONTEXT_OVERFLOW` 413, `PROVIDER_UNAVAILABLE` and an unreachable LLM 503, other `LLM_ERROR` 502, `RAG_GATED_FAILED` and unexpected failures 500).
7. On success the reply text is read from the stored assistant message and combined with the `done` frame data.
8. A `chat_updated` event goes to the owner's `/ws/events` sockets so open browser tabs refresh.

The turn body (`_run_chat_turn`, the caller holds the lock) is shared by the WebSocket handler and the HTTP endpoint, and the headless scheduler reuses the same helpers.

**Failed turns.** On both transports `ws._discard_user_message` points `Chat.current_leaf_message_id` back at the previous leaf before deleting the user message, instead of letting `ON DELETE SET NULL` clear the leaf. `LLM_ERROR` frames carry `cause` and `provider_kind`, which the HTTP status mapping uses.

**Deliberately absent:** streaming over HTTP, a stateless chat mode, a per-request MCP switch (use the MCP routes). Rate limits and the generation queue apply to this endpoint, see the next section.

## Limits and the generation queue (Day 30)

**Where it lives.** One module, `agent/limits.py`, holds the three primitives: `check_message_rate`, `LoginThrottle` (the instance `login_throttle`) and `GenerationGate` (the instance `generation_gate`). They read the four settings (`RATE_LIMIT_MESSAGES_PER_MINUTE`, `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`, `MAX_CONCURRENT_GENERATIONS`) from `shared.config.settings` at call time. State is per process and in memory, cleared by a restart, with no table. `limits_in_force` is logged once at Agent start.

**Message window.** The per-chat timestamps are the dict `agent.state.ws_rate_limiter`. `check_message_rate` runs synchronously (no `await` between the check and the append) in the WebSocket receive loop, before payload validation, and in `post_chat_message` after the busy check (order 404, 409, 429). A refused attempt takes no slot; an accepted one does, even when the turn then fails.

**Generation gate.** `ws.run_limited_turn` wraps `_run_chat_turn` on both channels (the WebSocket handler and `http_chat`). It takes the generation slot after the per-chat lock: lock first, then slot. A turn that waits for its own chat's lock must not hold a slot meanwhile, and one global acquisition order (lock, then slot) cannot deadlock. When the semaphore is full the turn sends a `queued` frame and waits; the wait has no timeout and the queue no bound. `_QueueStatsSink` passes frames through and adds `queue_wait_seconds` to the stats of the `done` frame (the HTTP endpoint reads that frame through its `RecordingSink`). The semaphore is created lazily for the current cap and event loop, so a changed setting or a new loop gets a fresh one.

**Login throttle.** The key is `(username, request.client.host)`. The browser talks to the Agent directly and no proxy sits in front of it, so the peer address is the real client; no forwarded header is read. A wrong password for an unknown username is counted like any other, and the answer is the same.

**Supervisor readiness.** The supervisor treats the Agent as started when `/health` answers 200 and an unauthenticated `GET /debug/routes` answers 401 (`ui/supervisor.py::_agent_routes_ready`). The 401 comes from the auth dependency of the guarded route, so it proves the app, its route table and its auth layer are mounted, not only that the port is open.

**Known limits**
1. The message limit is per chat, so many chats multiply the message budget; the generation cap bounds the real concurrency.
2. The queue is unbounded, by decision.
3. A WebSocket client that disconnects while its turn is queued still gets its turn run.
4. The lockout is per username and client address, so guesses spread over many addresses are not stopped.
5. Failed logins are remembered until a successful login or a block; `LoginThrottle.record_failure` never expires a count by age. A person who mistypes on separate occasions without logging in successfully in between is blocked for the lockout time once the threshold is reached.
6. The throttle keeps at most `LoginThrottle.MAX_ENTRIES` (10,000) username+address pairs. Beyond that the unblocked pair with the oldest failure is dropped and starts again from zero.
7. Failed logins under unknown usernames are fast because they skip password hashing, so a client already on the LAN can flood them and push its own counter out of the table. The lockout slows casual guessing but does not stop a determined attacker on the LAN; this is accepted for a private home-network service without IP filtering. The flood shows as `login_failed` log lines, and the password hash cost still bounds the guess rate for real usernames.
8. A restart clears every block and counter.
9. Traffic stays plain HTTP.

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
- usage_percent: Percentage of context window used
- queue_wait_seconds: seconds the turn waited for a generation slot, only in the `done` frame and the HTTP `200` body, not in `GET /api/v1/chats/{chat_id}/stats`

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

Each chat turn sends the LLM one tool list: the nine built-in tools (six memory and task tools plus
the three scheduler tools described in "Scheduler" below) plus
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

### Bundled MCP servers

**Project git server:** `mcp_servers/project_git.py` is a separate stdio process started by the
Agent through the existing MCP client; it is not part of `agent/` or `ui/`. It registers five
read-only tools: `git_current_branch`, `git_status`, `git_diff`, `git_tracked_files` and
`git_recent_commits`. It imports only the standard library, `mcp` and `psutil` and never `shared.*`,
because `shared/logger.py` writes JSON to stdout and stdout is the protocol channel; its
diagnostics go to stderr.

**Project files tools:** the same process registers three read tools: `read_file` (numbered lines,
a window of 300 lines), `search_text` (at most 200 hits) and `find_usages` (kinds `def`, `class`,
`import`, `call`, `ref`, `assign`, `param`, found through `ast` within a time budget). Visibility
follows git: `git ls-files --cached --others --exclude-standard` for the file set and
`git grep -I --untracked --exclude-standard` for search, both under the command timeout, so ignored
files such as `.env` and `app.db` are never readable.

**Write gate:** `write_file` and `edit_file` are added with `server.add_tool` only in `main()` when
`--allow-write` is given, so without the flag the server lists 8 tools and with it 10; each write
tool also calls `require_write()`. A write is refused outside the root, inside `.git`, for `.env*`,
for git-ignored paths, for Windows device names, for directories and binary files, and above
`MAX_WRITE_BYTES` (200000). Matching in `edit_file` is done on LF-normalised text and CRLF is
preserved; the file is replaced atomically with `os.replace`.

**Diff result and card:** a successful write returns a `difflib` unified diff whose first line is
`Created <path> (+A -R)` or `Updated <path> (+A -R)`. The browser recognises it with `isUnifiedDiff`
and renders the tool-call card with a coloured diff block built with `textContent` only, never HTML.

**Run-script data flow:** `scripts/files_task.py` checks that the clone root is clean, sets the chat
settings (temperature 0), sends the goal through the HTTP chat endpoint with an API key, and then
collects the result with `git add -N .` and `git diff --binary` into `run.patch`, next to `run.json`
and `answer.md`. `scripts/files_eval.py` drives the four report scenarios the same way.

**Safety of the git calls:**
- Fixed argument lists through `asyncio.create_subprocess_exec`, no shell.
- Root confinement by real path: a path outside `--root` is refused.
- A ref is resolved to a commit hash with `git rev-parse --verify --end-of-options` before use.
- Every command runs with `--literal-pathspecs`, `--no-ext-diff`, `--no-textconv` and
  `core.fsmonitor=false`; inherited `GIT_*` variables are removed and `GIT_OPTIONAL_LOCKS=0` is set.

**Limits:** the server cuts output at 18000 characters (`--max-chars`) and stops a git command after
10 seconds (`--timeout`), while the app allows `MCP_TOOL_RESULT_MAX_CHARS` 20000 and
`MCP_TOOL_CALL_TIMEOUT` 30 seconds, so the server's own marker or error reaches the model first. The
process tree is killed on timeout because the Git for Windows launcher starts a child git.

**Start-up failures:** a missing root, git not in PATH or a folder that is not a repository end the
process with the reason as the last stderr line; the user sees `PROCESS_EXITED` with the stderr tail.
The server never runs `git init`.

**GitHub server:** the external `github-mcp-server` binary is started with
`stdio --read-only --toolsets=repos,pull_requests`. The token lives in the environment variable
`GITHUB_PERSONAL_ACCESS_TOKEN`, stored per user and masked by `agent/mcp_config.py`.

**Known risks:** read-only behaviour on the GitHub side rests on the `--read-only` flag alone; the
token itself has write rights. Text returned by git (diffs, commit subjects) reaches the model as
tool output and may carry instructions; the git tools being read-only limits the effect.

## Scheduler

The Agent runs user-defined jobs in the background: a prompt executed once, every N seconds, or on
a 5-field cron, with no chat and no user present. The REST/WS/tool contract is in
`docs/API_SPEC.md` ("Scheduler"); this section describes how it works.

**Modules:** `agent/schedule.py` (pure schedule math and validation), `agent/scheduler.py`
(`SchedulerService`: poll loop, claim, recovery, run execution), `agent/scheduler_ops.py`
(user-scoped operations shared by the REST routes and the LLM tools), `agent/scheduler_api.py`
(REST router), `agent/scheduler_schemas.py` (Pydantic shapes and WS frames),
`agent/scheduler_tools.py` (LLM tools), `agent/headless.py` (socket-free LLM turn),
`agent/events.py` (`EventHub` and `WS /ws/events`).

**Tables** (`shared/models.py`, created by `init_db`):
- `scheduledtask`: `user_id` (FK CASCADE), `origin_chat_id` (FK to `chat`, **SET NULL**, so deleting
  the chat that created a job keeps the job), title, prompt, `model`, `schedule_type`
  (`once|interval|cron`), `run_at`, `interval_seconds`, `cron_expr`, `max_runs`, `run_count`,
  `next_run_at` (UTC), `status` (`active|paused|completed|cancelled`), timestamps. Index
  `ix_scheduledtask_status_next (status, next_run_at)` serves the due-job query.
- `taskrun`: `scheduled_task_id` (FK CASCADE), `user_id` (FK CASCADE), `status`
  (`running|success|failed|skipped`), `trigger` (`schedule|manual`), `scheduled_for`, `started_at`,
  `finished_at`, `is_late`, `model`, `result_text`, `error`, `tool_trace` (JSON text). The partial
  unique index `uq_taskrun_one_running (scheduled_task_id) WHERE status = 'running'` guarantees at
  most one running run per job; any number of `skipped`/`success`/`failed` rows are allowed.
- All datetimes are stored as UTC. Cron expressions (via `cronsim`) and a naive `run_at` are
  evaluated in the Agent machine's local time and converted to UTC.

**Poll loop:** the Agent lifespan calls `scheduler.recover_orphaned_runs()` unconditionally and, when
`SCHEDULER_ENABLED` is true, `scheduler.start()`, which ticks every `SCHEDULER_POLL_INTERVAL`
(1 s). A tick selects up to 20 (`TICK_BATCH_LIMIT`) active jobs with `next_run_at <= now`, oldest
first, and claims each in its own session. A failing tick is logged and never stops the loop. On
shutdown the loop is stopped first, then in-flight runs are cancelled (each records
`Прервано: Agent остановлен`), before MCP cleanup and engine disposal. The test suite sets
`SCHEDULER_ENABLED=false` and drives `SchedulerService.tick(now)` directly.

**Atomic claim:** `claim_slot` runs `UPDATE scheduledtask SET next_run_at = <new>, run_count = ...
WHERE id = ? AND status = 'active' AND next_run_at = old` and inserts the `taskrun` row in the same
commit. Only the caller that sees `rowcount == 1` owns the slot, so two racing claimers (or a tick
racing a manual run) produce exactly one run. If the partial unique index rejects the insert (a
manual run got there first), the whole transaction is rolled back, the slot stays due, and the next
tick records it as skipped.

**Schedule rules:**
- **Catch-up once:** after downtime an overdue job fires a single time, flagged `is_late` when the
  lag exceeds `SCHEDULER_LATE_THRESHOLD_SECONDS`. Interval jobs move to the next slot on the original
  cadence strictly after now; cron jobs move to the next boundary computed from now, never
  replaying missed slots.
- **Overlap skip:** if the previous run of the job is still `running`, the slot is consumed but
  recorded as a `skipped` run (`Предыдущий запуск ещё выполнялся`) that does not count toward
  `run_count`.
- **No retry:** a failed run is final for that slot; the next slot fires normally.
- **max_runs / finalize:** a claim that would reach `max_runs` sets `next_run_at = NULL`.
  `finalize_task` then flips the job to `completed` once it has no next slot and no run in progress
  (a one-shot job completes after its run ends; a paused job with a pending slot is never
  completed). A manual run counts toward `run_count`; on a one-shot or exhausted job it clears
  `next_run_at`.
- **Pause/resume/cancel:** pause keeps `next_run_at`; resume restarts interval and cron jobs from
  now (paused time is not caught up) and keeps a one-shot `run_at` (which fires late if already
  past); cancel is soft (`status = cancelled`, `next_run_at = NULL`, history kept, an in-flight run
  still records its result); delete aborts in-flight runs first and cascades to runs.

**Startup recovery:** the supervisor stops the Agent with `terminate()`, a hard kill on Windows, so
no shutdown code runs and runs may be left `running`. On the next start `recover_orphaned_runs`
marks every `running` run `failed` (`Прервано: Agent был перезапущен`) and finalizes jobs that have
no next slot. Overdue jobs are then picked up by the first tick under the catch-up rule.

**Execution:** a claimed run is spawned as a background asyncio task (`execute_run`), held in a
strong-reference map until it ends. A semaphore (`SCHEDULER_MAX_CONCURRENT_RUNS`, 2) limits
parallel runs and is taken outside the deadline so queueing does not consume the budget; then
`asyncio.timeout(SCHEDULER_RUN_TIMEOUT)` (120 s) bounds the run. Outcomes: success stores
`result_text` and the tool trace; `HeadlessRunError`, timeout, and unexpected exceptions store a
Russian `error` (unexpected ones only expose the exception type name). Every finished run publishes
`run_finished` to its owner.

**Headless runner** (`agent/headless.py::run_headless_turn`): builds a system prompt from the user's
global settings (system prompt, long-term memory, a local-time clock line and an unattended-job
preface) and a single user message with the job prompt, then reuses the WebSocket tool loop in
`agent/ws.py` (sequential tool calls, `MAX_TOOL_ROUNDS` cap, empty-answer retry, nudges, loop
detection) by handing it a `RecordingSink` in place of the socket, so no chat, message row or chat
lock is involved. Temperature and max tokens come from the user's global `Settings` row (defaults
if absent, never created); the model is the one stored on the job. **Allowlist:** built-in tools
offered and dispatched are limited to `save_long_term_memory`; the user's live MCP tools are added
on top. Task, invariant and scheduler tools are neither offered nor executable (a hallucinated call
is rejected by the dispatcher). MCP problems never fail the run: the toolset falls back to empty and,
when servers were enabled but no tools were available, a note is appended to the answer (or named in
the empty-answer error). LM Studio being down, an HTTP error, or a read timeout map to Russian
failure messages.

**Live events:** `EventHub` keeps, per user id, a set of bounded queues (100 frames, oldest dropped
when full). Every publish targets one `user_id`; the ownerless broadcast used elsewhere is never
used for scheduler data. `WS /ws/events` validates `Origin` with the same policy as `/ws/chat`,
resolves the session cookie, subscribes the socket, and forwards frames through a single pump task.
The hub unsubscribes before any await in the `finally` block so a cancelled handler cannot leak its
queue.

**LLM tools:** `schedule_task`, `list_scheduled_tasks` and `cancel_scheduled_task` are registered
built-ins that call the same `scheduler_ops` functions as the REST routes. The chat's model is
passed through the `current_chat_model` context variable. Cancellation from chat is gated in code
(`user_asked_to_cancel` on the chat's latest user message) in addition to the model's own flag.

**Settings:**

| Variable | Default | Meaning |
|----------|---------|---------|
| SCHEDULER_ENABLED | true | Start the poll loop (recovery runs regardless) |
| SCHEDULER_POLL_INTERVAL | 1.0 | Seconds between ticks |
| SCHEDULER_RUN_TIMEOUT | 120.0 | Wall-clock limit of one run |
| SCHEDULER_MIN_INTERVAL_SECONDS | 10 | Minimum interval for interval jobs |
| SCHEDULER_LATE_THRESHOLD_SECONDS | 60.0 | Lag after which a run is flagged late |
| SCHEDULER_MAX_CONCURRENT_RUNS | 2 | Parallel runs |
| SCHEDULER_MAX_ACTIVE_TASKS_PER_USER | 50 | Active plus paused jobs per user |

**Known limits:** jobs fire only while the Agent process is up (missed slots are caught up once, not
replayed); a run interrupted by an Agent kill is not retried; there is no per-job destructive-tool
confirmation, so MCP tools run unattended with the same trust model as chat.

## Chat auto-titling

A chat created with the default title `New Chat` is renamed automatically after its first answer.
The logic lives in `agent/titles.py`; running jobs are tracked in the registry
`agent/state.py::title_tasks` (chat id -> task).

**Trigger.** `agent/ws.py::_handle_chat_message` schedules the job when the chat title is still
`New Chat` and the user message has no parent (the first turn). A failed turn never reaches the hook,
and scheduled headless runs are never titled. The job is created immediately before the `done` frame
is sent, so a client disconnect cannot skip it. It runs as a fire-and-forget task without the chat
lock and with its own DB session, so `done` is not delayed.

**Request.** The same `llm_client` and model as the turn; one system message plus one user message
wrapped in `<user_message>` / `<assistant_answer>` tags (first 500 / 300 characters, tag-breakout
sequences stripped repeatedly). The call goes through `LLMClient.complete_chat_detailed` (returns
content, `finish_reason`, a reasoning flag and the completion token count). Parameters:
non-streaming, temperature 0, max_tokens 30, `reasoning_effort: "none"`, and one 20 s
`asyncio.wait_for` around the whole attempt. An HTTP 400 / 422 answer triggers exactly one repeat
without `reasoning_effort` (event `chat_title_reasoning_control_rejected`). `complete_chat` (facts
extraction, self-critique) is unchanged and never sends the field. No tools, memory, profile or chat
system prompt are sent.

**Output sanitizing and fallback.** `clean_title` removes think-blocks, keeps the first line, strips
labels (`Title:`), quotes, HTML-like tags, angle brackets and markdown markers (paired `**`, `*`,
`` ` ``, `~~` and a leading `#` heading; the wrapped text is kept, and `C#`, `user_id`, `__init__`
pass through unchanged), and caps the result at 50 characters on a word boundary. If the output is empty, unusable or the request fails, `fallback_title`
cuts the first user message to 50 characters with `…`. An answer with no usable title is logged as
`chat_title_llm_unusable` (fields `model`, `finish_reason`, `content_empty`, `has_reasoning`,
`completion_tokens`; no text content) before the fallback is applied. `fallback_title` reads only
the first 500 characters of the message, `_snippet` only the first 4x its limit and `clean_title`
only the first 1000 characters of the model output (`max_tokens` is a request the backend may
ignore), so the regex work is bounded. Output whose closing `</think>` lies past that cut is treated
as unusable.

**Log events.** `chat_title_set` (`source` = `llm` or `fallback`), `chat_title_llm_failed`,
`chat_title_llm_unusable`, `chat_title_reasoning_control_rejected`, `chat_title_skipped`,
`chat_title_not_applied`, `chat_title_failed`. A run of `source=fallback` together with
`chat_title_llm_unusable` means the model is not producing titles.

**Race guard.** The write is `UPDATE chat SET title = ... WHERE id = ... AND title = 'New Chat'` and
the job checks `rowcount`; no extra column is needed. A renamed, deleted or already-titled chat is
left untouched, and only one job per chat runs at a time.

**Delivery.** After a successful write the job calls `hub.publish(chat.user_id, {"type":
"chat_title_updated", ...})`. `hub` is imported function-locally because `agent/events.py` imports
`agent/ws.py`. A chat without an owner produces no frame. `cleanup_chat_caches` cancels a pending job
when the chat is deleted.

**Frontend.** `ui/static/app.js::handleEventFrame` dispatches the frame to `applyChatTitleUpdate`,
which updates the sidebar and the open chat header via `textContent` only. The chat list is reloaded
when the events socket reconnects.

**Known limits.** No retry: a hard failure or an Agent restart mid-job leaves `New Chat`. Existing
chats are not retitled retroactively. A backend that accepts `reasoning_effort` but ignores it still
spends the 30 tokens on reasoning and gets the fallback title; this shows up in the log as
`chat_title_llm_unusable` with `finish_reason` `length` and `has_reasoning` true.

## Long-term memory editing

**Storage.** Table `LongTermMemory`, unique `(user_id, key)`. No schema change.

**CRUD layer.** `agent/memory.py`: `get_long_term_memory` filters by `id` AND `user_id` in one query;
`update_long_term_memory` checks the key collision before mutating and maps a commit-time `IntegrityError`
to `MemoryKeyConflictError`; `delete_long_term_memory` returns a bool. Edits never go through the upsert
`save_long_term_memory`, which would create a second row on a rename.

**Routes.** `PUT` / `DELETE /api/v1/memory/long-term/{entry_id}` are user-scoped without a chat id, answer
404 for foreign ids and 409 for a duplicate key. Both check Origin; PUT also checks the JSON content type.

**No cache, no event.** `build_system_prompt` and the headless scheduler prompt call
`list_long_term_memory` on every turn / run, so an edit applies from the next turn. Nothing is stored in
`agent/state.py` and no `/ws/events` frame is sent, so another tab shows the old list until its next
refresh.

**Frontend.** `renderMemoryEntries(..., { editable: true })` is used for the long-term list only. The
edit form is inline (no modal); the draft lives in `state.editingMemory`, so the reload after a `done`
frame does not discard typing. Memory text is written with `textContent` / `.value` only. Delete asks
with a native `confirm()` first.

**Known limits.** The model may save a deleted fact again, or re-create a renamed key, through
`save_long_term_memory`. Working memory is not editable. There is no undo.

## Knowledge base indexing

Users upload PDF/TXT/MD files into a knowledge base (KB); the Agent splits them into chunks, embeds
them with an LM Studio embedding model and stores a FAISS index. Everything is scoped by `user_id`.

**Modules.** `agent/kb_api.py` (REST router `/api/v1/kb`), `agent/kb_indexer.py` (background job,
delete, orphan recovery), `agent/kb_loaders.py` (PDF extraction with PyMuPDF, header/footer and
annotation cleaning, scan detection, UTF-8/Windows-1251 text decoding), `agent/kb_chunking.py` (fixed
and structural chunking), `agent/kb_limits.py` (all caps and constants), `agent/kb_schemas.py` (`KbOut`
and the `kb_progress` / `kb_deleted` frames), `agent/kb_search.py` (top-k search over a cached
index), `agent/embeddings.py` (LM Studio `/v1/embeddings` client, model guard, batching) and
`shared/kb_storage.py` (paths, FAISS read/write, directory removal).

**Tables.** `KnowledgeBase` (owner, name, status, strategy, chunk settings, embedding model, dim,
file/chunk counts, live progress `done_chunks`/`total_chunks`/`phase`), `KbDocument` (one row per
source file; its identity is the source name `filename`, which may be a relative path such as
`docs/API_SPEC.md`; there is no unique constraint, only the non-unique index
`ix_kbdocument_kb_filename` on `(kb_id, filename)`, uniqueness of a source name is kept in code, and
`sha256` is used only to detect an unchanged file on addition) and `KbChunk` (text, section breadcrumb, source, page,
offsets). `KbChunk.id` is the FAISS vector id, so a search hit maps back to its row without a lookup
table. Rows cascade on KB or user delete.

The old `UNIQUE (kb_id, sha256)` constraint is removed by the start-up migration
`migrate_kbdocument_drop_sha_unique`, which rebuilds `kbdocument` (copy, drop, rename, recreate
indexes) and is idempotent. It runs on an AUTOCOMMIT connection with `PRAGMA foreign_keys=OFF`,
because SQLite cannot change a table constraint in place and dropping the table with foreign keys on
would cascade into `kbchunk` and destroy the chunks; it checks `PRAGMA foreign_key_check` afterwards
and switches foreign keys back on.

**Storage layout.** `<DB_PATH stem>_kb/<user_id>/<kb_id>/{uploads/, index.faiss}`, or under
`KB_STORAGE_DIR` when set. The index is an `IndexIDMap2` over L2-normalised vectors
(inner product = cosine similarity). FAISS file I/O goes through byte buffers so non-ASCII Windows
paths work. Stored files are named `uuid4().hex + extension`, never after the source name, so a
relative-path source name cannot reach the file system. Each request first writes its files to
`uploads/_staging/<uuid>/`; on success they are moved into `uploads/`, on any failure the staging
directory is removed.

**Chunking.** `fixed`: windows of `chunk_size` with `chunk_overlap`. `structural`: splits legal text
at `Раздел`/`Глава`/`Статья` headings (article numbers with `-N` suffixes are distinct), Markdown at
headings, other text at paragraphs; every chunk is prefixed with a breadcrumb and long sections are
sub-split. No chunk exceeds 2000 characters (`MAX_EMBED_CHARS`), the safe limit of the embedder.

**Job flow.** `POST /api/v1/kb` stores the uploads, inserts the KB as `queued` and spawns a job
(one at a time, `Semaphore(1)`). Phases: `loading_model` (the embedding model is loaded in LM Studio
if needed), `parsing`, `embedding` (sequential batches of 32, progress throttled to two `kb_progress`
frames per second on `/ws/events`). CPU-bound and FAISS work runs in `asyncio.to_thread`, so
`/health` stays responsive. The job is all-or-nothing: any failure leaves the KB `failed` with a
readable message, no partial index and no chunk rows. Deleting a KB cancels its job, removes rows and
the directory, and emits `kb_deleted`.

**Addition to a ready base.** `POST /api/v1/kb/{kb_id}/files` classifies each uploaded file by source
name and SHA-256 as added, replaced or unchanged, then claims the base with a conditional `UPDATE`
(`status = ready` and `phase IS NULL` to `phase = adding`); the affected row count is the only
admission check, so two concurrent requests cannot both start. The job (`run_add_job`) chunks and
embeds only the added and replaced files with the base's own strategy and model, and embedding
finishes before anything is written. One short transaction then inserts the new document and chunk
rows first and deletes the replaced ones afterwards (SQLite reuses rowids after deletes, so new chunk
ids must exceed all existing ones). The FAISS index is patched on a copy read from disk (remove the
replaced ids, add the new vectors), written to a pending file and swapped in with `os.replace` after
the commit; the in-memory index cache is swapped at the same moment. The base stays `ready` and
searchable throughout. The failure path is non-destructive: any error or cancellation removes the
staging directory, clears `phase`, stores the message in `KnowledgeBase.error` and leaves rows, files
and index exactly as before; the next addition clears the message. The summary
(`add_summary`: added, replaced, unchanged) is sent once in the last `kb_progress` frame.

**Orphan recovery.** At Agent startup, KBs left `queued`/`indexing` by a crash or restart are marked
`failed` with a restart message; the user deletes and recreates them. A `ready` base left with a
`phase` (an addition interrupted by the restart) keeps its data: `phase` is cleared and `error` gets
a restart message asking the user to repeat the addition.

**Embedding model guard (D-24).** LM Studio's `/v1/embeddings` ignores the `model` field and answers
with whatever embedding model is loaded, so naming an `llm`-type model would silently return vectors
from a different model. The guard checks the model's type via `/api/v0/models` and rejects anything
but `embeddings` with a Russian message. Embedding models are also hidden from the chat model picker.

## Chat RAG pre-step (Day 22)

**Modules.** `agent/rag.py` (retrieval, budget, block rendering, payload), `agent/rag_turn.py`
(fail-soft pre-step `prepare_rag_turn`), `agent/rag_api.py` (REST routes). `ChatRagConfig` holds
`mode`, `kb_id`, `top_k` per chat (CASCADE on the chat, SET NULL on the knowledge base, so deleting a
KB detaches it instead of failing). `Message.rag_sources` stores the metadata-only payload of an
assistant answer (no fragment text); snippets are fetched lazily through the chunk route.

**Position in the WS turn.** After `build_llm_context` and the system-prompt suffix, before streaming:
the question is embedded with the KB's embedding model, the top-K chunks are fetched from the cached
FAISS index, and the numbered fragments plus an instruction are merged only into the outbound copy of
the last user message. The stored message and the message tree keep the raw question.

**Budget.** `min(30% of context_length, context_length - used_tokens - max_tokens)`. Fragments are
dropped lowest score first until they fit; if chunks exist but none fits, the turn continues without
fragments and reports the `context_full` warning.

**D-12 ordering note.** The decision asks for the budget to be settled before the compression
strategy. Here the budget is computed after `build_llm_context` from the exact used tokens and merged
only into the outbound copy, so RAG tokens never reach the compression strategy or the
`no_compression` overflow check. This satisfies the intent of D-12 (RAG can neither distort
compression nor trigger an overflow); see `14-04-SUMMARY.md` in the phase directory.

**Why RAG cannot reach the delete paths.** The pre-step catches every failure itself (`RagFailure` and
any other exception become a `warning` payload; only cancellation propagates), and the fragments are
added after the overflow check, so neither the `CONTEXT_OVERFLOW` nor the `LLM_ERROR` branch (which
delete the just-submitted user message) can be caused by RAG.

**Embedding dimension guard.** Before searching, the query vector size is compared with the index
dimension; a mismatch yields the `dim_mismatch` warning instead of a wrong search. A corrupt index
yields `index_corrupt`; an unloaded embedder yields `embedder_unavailable`.

**Fragment safety.** Runs of three or more `=` inside document text are collapsed so a document
cannot close the fragments block (prompt-injection guard); the frontend renders everything with
`textContent`.

## Two-stage retrieval (Day 23)

Day 22 fetched the top-K chunks by cosine and stopped. Day 23 splits retrieval into candidate search
and a filter/rerank stage; every optional step is a per-chat switch and every step fails soft.

**Stage order.** rewrite (optional) -> vector candidates per query (`candidate_k`, default 20) ->
hybrid FTS5 + RRF (optional) -> raw-cosine threshold with the FTS exemption -> lexical fusion
(optional) -> LLM rerank of the top 10 survivors (optional) -> final top-K -> the 30% context budget
of the pre-step above.

**Module map.**
- `agent/rag_pipeline.py`: `run_retrieval_pipeline` is the single pipeline used by both the chat turn
  (`agent/rag_turn.py`) and the eval script (`scripts/rag_eval.py`); it returns the final chunks and a
  metadata-only trace.
- `agent/rag_rank.py`: pure functions (lexical score, min-max fusion 0.6 cosine / 0.4 lexical, RRF with
  k=60, FTS query builder, rewrite validator, rerank parser).
- `agent/rag_fts.py`: FTS5 search scoped by `kb_id`; `agent/rag_llm.py`: the rewrite and rerank model
  calls (reasoning off, one retry on HTTP 400/422, per-stage timeout, one batched rerank call).
- `agent/kb_search.py`: `search_kb_vectors` (vectors plus row ids) and `cosine_for_ids` (exact cosine
  for FTS-only hits); `agent/rag.py` holds `CALIBRATED_THRESHOLDS` and `resolve_threshold`.
- `agent/rag_api.py`: GET/PUT of the new per-chat fields.

**FTS5 mirror.** `ensure_kb_chunk_fts` (`shared/database.py`) creates the virtual table
`kb_chunk_fts(text, section, kb_id UNINDEXED)` with the `unicode61 remove_diacritics 2` tokenizer, two
triggers (`kbchunk_fts_ai` after insert, `kbchunk_fts_ad` after delete; chunk text is never updated in
place) and, on startup, a backfill of any `kbchunk` rows missing from the mirror. It is idempotent.
The `ChatRagConfig` columns `candidate_k`, `threshold`, `lexical`, `llm_rerank`, `hybrid`, `rewrite`
are added by an idempotent `ALTER TABLE` migration.

**Threshold.** The cut is applied to the raw cosine of the original or rewritten query, never to a fused
score. `threshold = NULL` means "use the calibrated value for this embedding model":
`CALIBRATED_THRESHOLDS = {"bge-m3": 0.67}` (keys are substrings of the KB's embedding model id, from
the frozen calibration set in `eval_out/day23/calibration.json`). `nomic-embed-text` has no entry
because its answerable and out-of-corpus score distributions are not separable (any cut that removed
the out-of-corpus questions also removed answerable chunks); it resolves to threshold 0, so the UI
shows «(нет калибровки)» and the user may still set a threshold by hand. `threshold_source` is `user`,
`calibrated` or `none`.

**FTS exemption (D-08 as amended).** With hybrid on, a chunk below the threshold survives only if it is
an FTS hit and either its section or text matches an article number named in the question or its
lexical overlap with the question is at least 0.5 (`FTS_EXEMPT_MIN_LEXICAL`). Without this guard every
out-of-corpus question received an exempt chunk and `below_threshold` was unreachable. Such rows carry
`fts_exempt: true`.

**Fail-soft per stage.** A failing optional stage is skipped and recorded in `search.skipped` as
`{stage, reason}` while the rest of the pipeline continues. Reason codes: `timeout`, `http_error`,
`bad_output` (unusable model output), `no_llm`, `search_failed` (search of the rewritten query),
`fts_error`, `stage_error` (unexpected exception). A rewrite equal to the question is not a skip.

**Verdicts.** `ok`; `below_threshold` (candidates existed but none passed the cut); `kb_unavailable`
(retrieval failed, the Day 22 warning applies); `off`. On `below_threshold` the turn still calls the
LLM, with a no-fragments note instead of the fragments block (a later phase will change this
behaviour); the UI shows the grey line «Фрагменты не прошли порог (лучший X < Y)» and no «Источники».

**Payload v2.** `Message.rag_sources` and `done.rag` carry `v: 2`, `verdict` and a `search` object (see
`docs/API_SPEC.md`). It is metadata only: scores, ranks, chunk ids, file and section, never chunk text.
Candidates that do not fit the context budget are marked `over_budget` after the fact
(`mark_over_budget`).

## Citations and the «не знаю» gate (Day 24)

Day 24 adds a per-chat **strict mode** (`ChatRagConfig.strict`, on by default; existing rows become on
through the idempotent `ALTER TABLE ... DEFAULT 1` migration). With strict on, the model must answer only
from the fragments, cite them, and say «Не знаю» when they do not contain the answer; the code, not the
model, verifies the citations and decides when to refuse.

**Module.** `agent/rag_cite.py` is a pure module with no I/O. It holds the strict instruction, the fixed
refusal texts, the quote verifier and the payload fields. The chat turn (`agent/rag_turn.py`,
`agent/ws.py`) and the eval script (`scripts/rag_eval.py cite`) both import it, so the report measures
exactly what the application does.

**Turn flow.**
1. `prepare_rag_turn` runs the Day 23 pipeline. With strict on and a `below_threshold` verdict (or no
   candidates at all) it returns a **gated** turn: `reply_text` is built by `build_idk_reply` (the fixed
   sentence «Не знаю: ...» plus a clarifying question naming the nearest sections), `gated: true`.
2. `agent/ws.py::_complete_gated_turn` sends and stores that reply. **No LLM call is made**: one `token`
   frame with the whole reply, then `done`.
3. Otherwise the fragments block ends with `STRICT_INSTRUCTION` (answer only from fragments, `[N]`
   references, a «Цитаты:» tail of 1-3 lines `[N] «...»`, or a reply starting with «Не знаю»).
4. The answer streams to the client as written, so the «Цитаты:» tail is visible while it streams.
5. After the stream, `finalize_rag_turn` runs `process_answer` in a thread (`asyncio.to_thread`)
   **before** the message is persisted. It strips the tail, so `Message.content` is the clean answer, and
   adds the citation fields to the payload. The client re-renders the bubble without the tail on `done`.
6. An exception inside finalization is logged (`rag_cite_failed`) and the raw answer is kept: a citation
   failure never fails the turn.

**Verification rules.** `normalize` applies NFKC, removes soft hyphens, maps dash variants to `-`, joins
words split by a hyphen at a line break, casefolds and maps `ё` to `е`, drops quote marks, markdown
emphasis characters and runs of `=`, collapses whitespace and trims edge punctuation. `match_quote` splits
the quote at ellipses (parts shorter than 8 characters are ignored) and looks for the parts in order in
the normalized chunk text: all found is `exact`. Otherwise, when every part has at least 25 characters, at
least half of the quote's word stems occur in the chunk and a sliding `difflib` window over the chunk
reaches a ratio of 0.9 for every part, the quote is `fuzzy`; anything else is `unverified`. A quote is
checked against the fragment it names; when it fails there but is found in another fragment it is
**re-attached** to that one (`rebound: true`). A reference outside the fragment range is an invalid
reference (`bad_ref: true`; counted in `invalid_refs` together with invalid `[N]` references in the body).
When no quote is verified, up to three **auto quotes** (`auto: true`) are picked by the code from the
cited fragments, or else the top two, by word-stem overlap with the question (at most 300 characters
each).

**Outcomes.** `gated` (code refusal, no model call); `model_idk` (the reply starts with «Не знаю» and has
no quotes: the verdict becomes `model_idk` and there is no quotes block); `answer_supported` (`true` when
the body has a valid `[N]` reference or at least one quote is verified, otherwise `false`, shown as an
amber line); `answer_empty` (the model returned no text, usually because `max_tokens` was spent on
reasoning).

**Storage.** Chunk text still never enters the payload, with one documented exception to the Day 22
metadata-only rule: `quotes[].text` holds the quote string, capped at 1000 characters. It is model output
(or, for auto quotes, a sentence the code cut from a fragment). The other quote fields (`rank`,
`chunk_id`, `file`, `section`) are copied from the fragment's metadata. The payload version is 3 (see
`docs/API_SPEC.md`).

**Strict off.** The turn is the Day 23 one: the Day 22 instruction, no gate, no verification, payload with
`strict: false`, `gated: false` and empty `quotes`.

**Threshold.** The gate uses the Day 23 threshold unchanged (calibrated 0.67 for bge-m3, 0 without a
calibration, or the user's override). `Day24_report.md` measures the cost: at the calibrated threshold
some answerable questions are refused.

## Task memory and history-aware retrieval (Day 25)

Day 25 gives a RAG chat a **task memory**: a small per-chat document with the goal of the dialog, the points
the user clarified and the constraints and terms the user fixed. It is also the first time retrieval looks at
the dialog, so an elliptic follow-up («а за повторное?») can still find its fragments.

**Memory layers.** For a chat with RAG the context now holds four kinds of state, each with its own owner:
short-term dialog (the messages, trimmed by the compression strategy), working memory (key-value rows the
model saves with tools), **task memory** (this section, extracted by code from the dialog) and long-term
memory (per user, all chats). Task memory exists only for chats whose RAG mode is on; a chat without RAG has
no task state and no extraction call.

**Storage.** Table `ChatTaskMemory` (`chat_id` primary key with `ON DELETE CASCADE`, `user_id`, `doc_json`,
`updated_at`). It is a separate table rather than a working-memory row because working memory is a
free-form key-value store written by the model, while task memory has a fixed shape, ids, caps and merge
rules owned by code, is edited from its own UI, and has to follow the active branch. The document is
`{goal, clarified[{id, text}], constraints[{id, text}], next_id}`; ids are never reused.

**Module `agent/task_memory.py`.**
- Document and caps: goal up to 300 characters, an item up to 200, each list up to 12 items.
- Extraction: after the answer, one non-streaming call (`extract_delta`, `TASK_MEMORY_TIMEOUT`, 400 tokens)
  gets the current document, the user message and the answer and returns a JSON delta (`goal`,
  `goal_changed`, `clarified`, `constraints`). `parse_delta` strips code fences and rejects anything that is
  not the expected object.
- User-stated filter (`filter_user_stated`): an item or goal survives only when its word stems overlap the
  user's message (at least half) and every number it contains also occurs in the user's message. Facts that
  appear only in the model's answer never enter the memory.
- Merge (`merge_delta`): the goal is sticky, a new goal is stored when the document has none or the delta
  marks `goal_changed` (an explicit goal change replaces the old one); items are deduplicated by stem Jaccard
  (0.8) across both lists and dropped when a list is full (`task_memory_cap_hit` is logged).
- Snapshot (`build_snapshot`): the document after the turn plus `new` (`goal`, `ids` added by this turn) and
  `failed`. It is stored in the message payload and sent to the client.
- Edits by hand: `set_goal`, `remove_item` and a full reset, exposed by `agent/task_memory_api.py`.

**Turn flow.**
1. `prepare_rag_turn` loads the history context from the **active branch** (`load_history_pairs`: the last
   `history_turns` question-answer pairs, default 3, 0..10) and the rendered task memory.
2. When there is history or memory, the `history` stage runs `condense_query` (one model call) and returns a
   standalone query. Both the raw question and the condensed query are searched and the candidates merged
   (the `found_by` field tells which found them). The lexical and LLM rerank stages still score the **raw
   question**; the threshold, the gate rules and quote verification are unchanged. The gate decision is
   taken on the merged result.
3. The system prompt gets the memory block from `build_system_prompt` as labelled lines: «Память задачи (этот
   чат):», «Цель диалога: ...», «Уточнено пользователем: ...», «Ограничения и термины (соблюдай их): ...».
4. The answer streams, then `finalize_rag_turn` verifies citations as before.
5. `update_task_memory` runs synchronously after the answer, **stages** the row and does not commit; the
   assistant message and the staged memory are committed together, so a stored answer and its memory never
   disagree. `done.rag.task_memory` carries the snapshot and the sidebar updates without a reload.
6. A **gated** turn (no model call for the answer) still updates the memory from the user's message alone.

**Branches.** `branch_chat` calls `restore_from_path`: the memory becomes the snapshot stored in the newest
assistant message of the new active path, or an empty document when the path has none. A manual edit made
after the last answer is therefore lost on a branch switch (the snapshot is the source of truth for a path).

**Fail-soft.** A failed or unparsable extraction keeps the previous document and sets `failed: true`
(the UI shows «память не обновлена»); a failed condensing call is recorded as a skipped `history` stage and
the search uses the raw question; a failed memory load or prompt render is logged and the turn goes on
without it. No memory error fails a turn.

**Eval-only switch.** `TASK_MEMORY_ENABLED` (default true) and `TASK_MEMORY_TIMEOUT` (seconds, default 30) are
environment settings. Turning `TASK_MEMORY_ENABLED` off removes task memory and the `history` stage; it has
no UI and no API surface and exists for the «no memory» baseline run of `scripts/rag_eval.py dialog`.

**Measured behaviour** is in `Day25_report.md` (two dialog scenarios, memory against the baseline, goal
adherence judged by DeepSeek).

## /help command (Day 31)

`/help <question>` answers from a documentation knowledge base the user marked and from a chosen set of tools. It is a separate turn type, not a mode of the ordinary RAG turn.

**Recognition.** `agent/help_command.py::parse_help` accepts a message whose first word is `/help` (case-insensitive, leading whitespace allowed); `/helpme` is ordinary text. The branch sits at the top of `agent/ws.py::_run_chat_turn`, which both the WebSocket handler and the HTTP chat endpoint (`agent/http_chat.py`) call, so the rule exists once, on the server.

**Service notices.** `/help` without a question, and `/help <question>` without a marked ready base, produce a notice (`{"type": "help_notice", "kind": "usage" | "no_base", "stored": false}`; over HTTP a `200` body with `stored: false`). A notice is decided before the rate check, the generation queue slot and the provider lookup, so it calls no model, stores nothing and costs no slot. A real turn takes the slots as any other turn.

**Settings.** Table `HelpConfig` (one row per user, `user_id` primary key): the marked `kb_id`, the search settings (`top_k`, `candidate_k`, `threshold`, four stage flags), the stored MCP server choice and three built-in group flags. `kb_id` is a foreign key with `ON DELETE SET NULL`, so deleting the marked base clears the mark. The row is read only by `/help`; the chat's `ChatRagConfig` is never read or written, and a `kb_id` in an HTTP `/help` request is ignored (`apply_chat_kb` is skipped).

**Flow of a real turn (`agent/ws.py::_run_help_turn`).**
1. Load `HelpConfig` and the marked base with the chat owner's id; the base must be owned and ready (a base that is still adding files counts as ready).
2. Store the user message exactly as typed.
3. `build_help_tools` selects the tools; `build_help_messages` builds the whole outbound list: the `/help` system prompt, the clock line and the tool rule, then the question. No chat history, profile, invariants or chat system prompt are sent.
4. `prepare_help_turn` runs the retrieval pipeline with the `/help` settings. There is no «не знаю» gate: when nothing passes the threshold the model gets a note that nothing was found and may still answer from tools or say it does not know. Fragments are budgeted like ordinary RAG and put in strict mode, so the model must cite `[N]` and add a «Цитаты:» block.
5. `_run_turn_middle` (shared with the ordinary turn) streams the answer and runs tool rounds, with `allowed_tools` set to exactly the enabled built-in tools.
6. `finalize_help_turn` verifies the quotes; when a tool call succeeded and every remaining quote is code-picked (not written by the model), those automatic quotes are dropped so a git-only answer does not show unrelated quotes.
7. Two ordinary messages are stored (user and assistant) and `done` is sent.

**Tool selection.** MCP servers are chosen as whole servers, never as single tools. With no stored choice only the server named `local-git` is used. The three built-in groups (memory, tasks, scheduler) are off by default; `allowed_tools` is always an explicit `frozenset`, empty by default, so no built-in tool is callable unless enabled. Enabled built-in groups and write-capable MCP servers can change data during a `/help` turn.

**Failures never refuse the turn.** A chosen MCP server that is missing, disabled or did not connect becomes an entry in `rag.help.warnings` and the turn continues without its tools. A retrieval failure becomes `rag.warning`, and the turn continues without fragments.

**Skipped side jobs.** A `/help` turn updates no task memory, extracts no facts, generates no chat title and runs no invariant resolution or self-critique; `done.invariant_conflict` is always `null`. A new chat opened with `/help` keeps the default title until an ordinary message is sent.

**Storage.** The label and the warnings are stored in the `help` object inside `Message.rag_sources` of the assistant message, next to the sources and quotes, so they survive a reload. Later ordinary turns see the stored pair as ordinary history. Tool-call cards are live-only for every chat and are not restored from the message tree after a reload.

## /support command (Day 33)

`/support [#T-NNNN | @login] <question>` is a second command turn built on the same mechanism as `/help`. The answer combines three sources: documentation fragments (the base marked for `/help`, which contains `docs/FAQ.md`), the data of one ticket or user read by code from the support MCP server, and optional tools chosen by the model.

### Shared command-turn mechanism

`agent/command_turn.py` holds what both commands share. A `CommandSpec` describes one command: its name, the payload key (`help` or `support`), the default MCP server name, the texts (usage, no base, base not ready, server warning), the system prompt and the fragments instructions. The module provides the notice resolution (`resolve_command_notice`, `notice_frame`), the tool selection (`select_tools`), the outbound message builder, the retrieval step (`prepare_command_turn`, which never gates and never raises except on cancel) and the quote finalisation (`finalize_command_turn`). `agent/help_command.py` and `agent/support_command.py` are thin facades that bind their `CommandSpec` and keep their own grammar, history and settings loaders. The `/help` behaviour did not change when it was moved there.

### Turn sequence (`agent/ws.py::_run_support_turn`)

1. Notice: `/support` without a question, or without a marked ready `/help` base, returns a `help_notice` frame with `command: "support"` before the rate check and the queue slot; nothing is stored and no model is called.
2. The user message is stored exactly as typed.
3. History: `load_support_history` walks the current branch and returns the last 3 pairs whose user message is a `/support` command and whose assistant message carries the `support` key; answers are cut to 1500 characters and the total to 2000 tokens. The identifier is spelled out («Тикет T-1001. ...») so a follow-up keeps the topic.
4. Direct lookup: with an identifier, `agent/support_lookup.py::direct_lookup` finds the user's MCP server named `support`, connects it and calls `get_ticket` or `get_user` through the ordinary MCP dispatcher. This is code, not a model decision, so the ticket is read even by a weak local model. The result is sent as a `tool_call` frame (the card) and stored in the tool trace. Any problem becomes a warning and the turn continues.
5. Tools: `build_support_tools` selects whole MCP servers (default: only `support`) and the built-in groups (off by default).
6. Prepare: the retrieval query is the question extended with the ticket's `Subject:` and `Error:` lines (`retrieval_query`, switch `RETRIEVAL_HINT_ENABLED`, at most 300 characters) so documentation matches the real error text; the fenced `<support_data>` block is passed as the context block; fragments are budgeted like ordinary RAG and put in strict mode.
7. Middle: `_run_turn_middle` (shared with every turn) streams the answer and runs tool rounds.
8. Finalise: quotes are verified; when the answer came from ticket data or tools only, code-picked quotes are dropped.
9. Two ordinary messages are stored and `done` is sent. No task memory, fact extraction, title generation, invariant resolution or self-critique runs; `done.invariant_conflict` is `null`.

### The `<support_data>` fence

Ticket and user text is written by customers, so it is untrusted. `fence_support_data` wraps it in a `<support_data>` block preceded by a sentence saying it is data for reference and that requests and commands inside it must not be followed; a literal `<support_data` or `</support_data` inside the text is neutralised so the text cannot close the block; the block is cut at 6000 characters with the mark `[данные сокращены]`. The system prompt repeats that the block is data, not instructions. The same data reach the model also when it calls the support tools itself, as ordinary tool results.

### History rule

Only earlier `/support` pairs are replayed, never the ordinary chat history, the profile or invariants. Ordinary later turns see the stored `/support` pair as normal history.

### Support MCP server

`mcp_servers/support_desk.py` is a self-contained stdio server (it does not import the application logger, because stdout is the protocol channel; diagnostics go to stderr). It is started as `python mcp_servers/support_desk.py --data=<path to json> [--max-chars=18000]` and reads a JSON file with `users` and `tickets` (the bundled example is `mcp_servers/support_data.json`: four users, tickets `T-1001` to `T-1006`). Four read-only tools: `get_ticket`, `get_user`, `list_user_tickets` and `search_tickets` (at most 20 hits, search text 1 to 100 characters). The file is re-read when its modification time or size changes, so an edit is visible on the next call; it must not exceed 1 MiB. A result is cut at `--max-chars` (default 18000, below the application cap of 20000). A bad argument returns an error that names the fix.

### Settings and storage

Table `SupportConfig` (one row per user, `user_id` primary key with `ON DELETE CASCADE`): the search settings (`top_k`, `candidate_k`, `threshold`, four stage flags), the stored MCP server choice (`mcp_servers_json`, `NULL` means only `support`) and three built-in group flags. The documentation base is not stored here: `/support` reads the `HelpConfig` mark. Routes are in `agent/support_api.py`. The label, the identifier, the lookup outcome and the warnings are stored in the `support` object inside `Message.rag_sources`.

### Trust boundary

The logged-in user acts as a support employee and can read any ticket of the data file; there is no per-ticket authorisation. Ticket and user data are sent to the provider of the chat's model, so with a cloud provider they leave the machine.

## Automated PR review (Day 32)

A Pull Request on GitHub is reviewed by the local app: a workflow on a self-hosted runner asks the Agent for a review over HTTP and publishes the answer as one comment. The model sees the changed text, the project rules and the code around the change, and writes three sections: potential bugs, architecture problems, recommendations.

### Flow from the event to the comment

1. A Pull Request is opened, updated, reopened or marked ready; `.github/workflows/pr-review.yml` starts a job on the runner labelled `pr-review` (not for drafts and not for Pull Requests from forks).
2. The job runs `scripts/pr_review_ci.py`, which sends `POST /api/v1/reviews` with the API key.
3. `agent/review_service.py` loads the user's review settings (`reviewconfig`) and resolves the provider, the model and the documentation base.
4. `agent/review_github.py` reads the Pull Request through the GitHub MCP server: the tool `pull_request_read` with the methods `get`, `get_files` and `get_diff`. The calls are made by code, not by the model: no model is involved, and the tool result is never cut.
5. `agent/code_git.py` runs `git fetch` of the base branch from `origin`, and `agent/code_index.py` refreshes the code knowledge base incrementally.
6. `agent/review_pipeline.py` plans the parts, searches both bases for each part, calls the model and merges the partial answers.
7. The reply is sanitised and returned; the script builds the comment, replaces its earlier comment (found by a hidden marker) or creates a new one, and writes the job summary.

### Modules and tables

| Module | Role |
|--------|------|
| `agent/review_api.py` | Routes of the review, its settings and the code index; maps error codes to statuses; per-Pull-Request lock |
| `agent/review_service.py` | Chain settings -> GitHub -> index -> review -> final head check; provider and model resolution |
| `agent/review_github.py` | Pull Request data through the MCP server, paging of the file list |
| `agent/code_git.py` | The only place that runs `git`: repository validation, `fetch`, tree listing, blob reading |
| `agent/code_index.py` | Incremental code knowledge base per user, repository and base branch; background refresh job |
| `agent/code_chunking.py` | Splitting of code files into fragments |
| `agent/review_pipeline.py` | Budget, parts, per-base retrieval, model calls, merge call, output cleaning |
| `agent/review_prompts.py` | The prompts and the wrapping of untrusted text |

Two tables hold the state. `reviewconfig` has one row per user: the repository folder, the documentation base, provider, model, MCP server and `context_length`. `reviewcodeindex` links a user, a repository key and a base branch to its code knowledge base (`kb_id`) and stores the indexed commit and the time of the last update.

### Code knowledge base

The code base is built from the files tracked by git at the tip of the Pull Request's base branch. The files are read from the git object store with `git ls-tree` and `git cat-file`, never from the work tree, so uncommitted edits and the branch that is checked out do not change the index, and the review never reads a file from the disk of the repository. There is one base per user, repository and base branch, at most 5 per user; a sixth removes the least recently updated one. The base is an ordinary knowledge base named `Код: <repository>@<branch>`, and it is not meant to be edited by hand.

- Change detection: the git blob id of each file is stored in `KbDocument.sha256`. A refresh embeds only added and changed files and removes deleted ones; with no new commit nothing is embedded.
- Indexed: tracked regular files with the suffixes `.py`, `.js`, `.html`, `.css`, `.ini`, `.toml`, `.cfg`, `.yml`, `.yaml`, `.json`, `.txt`, up to 200000 bytes (JSON up to 20000 bytes).
- Excluded: `.planning/`, `docs/`, `eval_out/`, `tests/fixtures/`, `node_modules/`, Markdown and other documents, images, databases, keys, lock files. Documentation goes into the documentation base instead.
- Splitting (`agent/code_chunking.py`): a Python file is split by functions, classes and methods with `ast`; a long function is cut further. Other files, and a Python file that does not parse, are cut into windows of 50 lines with an overlap of 8. A fragment has at most 2000 characters; a single huge line is cut by characters. The section of a fragment is `name (Lstart-end)`, for example `delete_chat (L120-160)`, so a source can name its lines.
- Atomic swap: the vector index is written next to the current one and promoted only after the database commit succeeds; a failed refresh keeps the previous index, and a failed first build leaves nothing behind.

### Retrieval

Two bases are searched separately for each part of the diff: the project documentation base (rules, architecture, conventions) and the code base. Each has its own quota so that the large code base cannot push the rules out: 4 fragments from documentation (similarity threshold 0.35) and 6 from code (threshold 0.0, so the quota of the top 6 decides). Code fragments are always taken as the best matches because code similarity scores are low and a fixed cut-off would drop them all. The first stage takes 20 candidates; the second-stage options of the chat (keyword search, model rerank, query rewriting) are off, so no extra model call is made.

The query is built from the part, not from a user question. For documentation: the Pull Request title, the changed paths, the names from the hunk headers and a fixed phrase about project rules, architecture and code conventions. For code: the changed paths, the hunk-header names and the added lines (at most 40 lines of 200 characters). Every query is cut to 1500 characters.

### Budget and the per-file path

The context length of the review settings (default 65536) is shared: the fragments take 25 percent, the answer 3000 tokens, the fixed text of the prompt 1200 tokens; the rest is the room for the changed text of one call:

`available = context_length - 1200 - 3000 - 0.25 * context_length`

Tokens are estimated with a deliberately pessimistic count so that Russian text and code do not overflow the window. If the whole diff fits, one call is made. Otherwise the diff is split by file, the files are packed greedily into parts that each fit, and a file that is bigger than the room is split by hunks (and a single huge line by characters), each piece marked as a part of that file. Each part is reviewed with its own retrieval, then one merge call receives only the partial answers (no diff) and writes the three final sections. More than 20 parts, or a context length too small for the prompt, ends with `PR_TOO_LARGE`. A file for which GitHub gave no changed text (binary or too large) is listed in `unreviewed_files` and named in a note: it was not analysed.

### Model call

The provider and the model come from the review settings. By default this is the local LM Studio provider and the model that is loaded there; a cloud provider can be chosen, and then the changed text and the fragments leave the machine (the settings window says so). Every call is made without tools, with the reasoning effort turned off (retried once without that field when the provider rejects it), temperature 0.2 and at most 3000 answer tokens. It waits for a slot of the shared generation queue like a chat turn does, and has its own timeout of 900 seconds per call. When the answer lacks the three headings, one repair call with a reminder is made; if that fails too, the code is `REVIEW_BAD_OUTPUT`.

### Security notes

- The diff, the Pull Request text and the fragments are untrusted. They go to the model as data inside tags, with angle brackets and runs of equal signs neutralised so that the text cannot close a tag, and the system prompt tells the model to treat them as data.
- The model has no tools and the pipeline module imports none, so instructions inside a Pull Request cannot make it act.
- The reply is sanitised before it leaves the Agent: reasoning blocks, images, scripts, HTML tags and comments are removed, `@mentions` are defused, and over-long sections are truncated. The script defuses them again before publishing.
- The GitHub MCP server is read-only (`--read-only`), and the routes never take a repository folder from the request.
- Keys: the API key lives in the repository secret and in the Agent; it is never logged and is scrubbed from every line the script prints. Writing the review settings and refreshing the index are allowed only for a web session, never for an API key.
- The runner executes the workflow on the owner's machine; its safe configuration is described in `docs/USER_GUIDE.md`.

### Out of scope

Inline comments on lines, posting comments from the app itself (the workflow posts), a `/review` chat command, GitHub-hosted runners and blocking a merge on the result.
