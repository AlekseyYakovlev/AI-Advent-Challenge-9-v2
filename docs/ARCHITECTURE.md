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
uploaded file, unique on `(kb_id, sha256)`) and `KbChunk` (text, section breadcrumb, source, page,
offsets). `KbChunk.id` is the FAISS vector id, so a search hit maps back to its row without a lookup
table. Rows cascade on KB or user delete.

**Storage layout.** `<DB_PATH stem>_kb/<user_id>/<kb_id>/{uploads/, index.faiss}`, or under
`KB_STORAGE_DIR` when set. The index is an `IndexIDMap2` over L2-normalised vectors
(inner product = cosine similarity). FAISS file I/O goes through byte buffers so non-ASCII Windows
paths work.

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

**Orphan recovery.** At Agent startup, KBs left `queued`/`indexing` by a crash or restart are marked
`failed` with a restart message; the user deletes and recreates them.

**Embedding model guard (D-24).** LM Studio's `/v1/embeddings` ignores the `model` field and answers
with whatever embedding model is loaded, so naming an `llm`-type model would silently return vectors
from a different model. The guard checks the model's type via `/api/v0/models` and rejects anything
but `embeddings` with a Russian message. Embedding models are also hidden from the chat model picker.
