# API Specification

## Agent (port 8001)
- GET  /health
- GET  /api/v1/chats
- POST /api/v1/chats
- DELETE /api/v1/chats/{chat_id}
- GET  /api/v1/chats/{chat_id}/tree
- GET  /api/v1/chats/{chat_id}/stats
- POST /api/v1/chats/{chat_id}/branch
- GET  /api/v1/chats/{chat_id}/memory (see "Long-term memory")
- PUT/DELETE /api/v1/memory/long-term/{entry_id} (see "Long-term memory")
- GET  /api/v1/settings?chat_id={id}
- PUT  /api/v1/settings
- WS   /ws/chat/{chat_id}
- WS   /ws/events (see "Scheduler" and "Chat titles")
- GET/POST/DELETE /api/v1/scheduler/... (see "Scheduler")

## LM Studio
Each route accepts an optional `provider_id` (query for models and unload-model, body for load-model)
that must reference the user's LM Studio provider; without it the configured `LM_STUDIO_BASE_URL`
host is used. A foreign or missing id gives 404 `Провайдер не найден`, a provider of another kind
gives 400 `Провайдер не является LM Studio`.

- GET  /api/v1/lm-studio/models
- POST /api/v1/lm-studio/load-model
- POST /api/v1/lm-studio/unload-model/{model_id}

## Statistics Endpoint

### GET /api/v1/chats/{chat_id}/stats

Returns real-time statistics for a chat.

**Response:**
```json
{
  "total_request_tokens": 2450,
  "total_response_tokens": 5120,
  "current_context_size": 3500,
  "context_window_size": 16384,
  "context_usage_percent": 85.4,
  "message_count": 20
}
```

**Notes:**
- current_context_size reflects active compression strategy
- Updates automatically after each message
- Can be polled via REST or received via WebSocket "done" message

## Settings Schema

### Fields:
- id: int (primary key)
- chat_id: int | null (null for global settings)
- system_prompt: str
- temperature: float (0.0-2.0)
- context_length: int (512-32768, default 16384)
- max_tokens: int (256-128000)
- strategy: ContextStrategy (sliding | sticky | truncate_middle | no_compression)
- facts_json: str (JSON object with extracted facts)
- summary_text: str (accumulated conversation summary)

### ContextStrategy Values:
- sliding: Sliding Window
- sticky: Sticky Facts
- truncate_middle: Truncate Middle
- no_compression: No Compression

## WebSocket Messages

### Server → Client

**done:**
```json
{
  "type": "done",
  "message_id": 123,
  "stats": {
    "total_request_tokens": 2450,
    "total_response_tokens": 5120,
    "current_context_size": 3500,
    "context_window_size": 16384,
    "context_usage_percent": 85.4
  }
}
```

**tool_call** (one frame per executed tool call, sent before the follow-up tokens):
```json
{
  "type": "tool_call",
  "tool_call_id": "call_1",
  "name": "mcp__filesystem__list_allowed_directories",
  "server": "Filesystem",
  "tool": "list_allowed_directories",
  "arguments": "{}",
  "ok": true,
  "result": "Allowed directories:\nC:\\Projects\\AiAdventAgentV2",
  "truncated": false
}
```
- `name`: the exposed name the model used; `server` is `null` and `tool` equals `name` for built-in tools.
- `arguments` is capped at 2000 characters and `result` at 4000; `truncated` is true when the
  MCP result or the preview was cut.
- MCP failures (disconnected server, timeout, `isError`) are reported only here with `ok: false`;
  no `TOOL_ERROR` frame is sent for them. Built-in tool failures still send `TOOL_ERROR`.

**Client -> Server (chat message):** the payload may carry an optional `provider_id` (integer, the id
from `GET /api/v1/llm-providers`) next to `model`. Without it the turn goes to the user's LM Studio
provider (legacy behaviour). The same client serves the answer, tool follow-ups, the auto-title and
fact extraction of that turn.

**error (provider unavailable):**
```json
{
  "type": "error",
  "code": "PROVIDER_UNAVAILABLE",
  "detail": "Провайдер «Stub» недоступен. Выберите другую модель или проверьте настройки провайдера."
}
```
Sent when the provider is deleted, disabled or belongs to another user (a foreign provider's name is
never disclosed) and also when the provider answers HTTP 401/403 during the turn (the detail names
the provider, never the key). No user message is stored for such a turn.

**error (context overflow):**
```json
{
  "type": "error",
  "code": "CONTEXT_OVERFLOW",
  "detail": "Context size (5200 tokens) exceeds context window (4096 tokens). Please change compression strategy or reduce conversation length."
}
```

## Scheduler (Day 18)

Background jobs that run a prompt through the LLM (and the user's MCP tools) later, once or
periodically, with nobody in the chat. All routes require the session cookie (`401
{"detail": "Not authenticated"}` otherwise) and are scoped to the current user: another user's job
or run is reported as `404`, never `403`. Mutating routes (POST/DELETE) reject a foreign `Origin`
with `403` (`Cross-origin request rejected`; a missing `Origin` is allowed). `POST /tasks` also
rejects a body that is not `application/json` with `415`. Every datetime is serialized as UTC ISO
8601 with an explicit offset (`2026-09-26T11:05:00Z` / `+00:00`); cron expressions and naive
`run_at` values are interpreted in the Agent machine's local time.

### Routes

| Method | Path | Success | Errors |
|--------|------|---------|--------|
| GET | `/api/v1/scheduler/tasks` | 200 `ScheduledTaskOut[]` (live jobs first, then by `next_run_at`, then newest) | 401 |
| POST | `/api/v1/scheduler/tasks` | 201 `ScheduledTaskOut` | 401, 403, 415, 409, 422 |
| GET | `/api/v1/scheduler/tasks/{task_id}` | 200 `ScheduledTaskOut` | 401, 404 `Задание не найдено` |
| POST | `/api/v1/scheduler/tasks/{task_id}/pause` | 200 `ScheduledTaskOut` | 401, 403, 404, 409 `Задание не активно` |
| POST | `/api/v1/scheduler/tasks/{task_id}/resume` | 200 `ScheduledTaskOut` | 401, 403, 404, 409 `Задание не на паузе` |
| POST | `/api/v1/scheduler/tasks/{task_id}/cancel` | 200 `ScheduledTaskOut` (soft cancel, history kept) | 401, 403, 404, 409 `Задание уже завершено` |
| POST | `/api/v1/scheduler/tasks/{task_id}/run` | 202 `RunSummary` (off-schedule run) | 401, 403, 404, 409 `Задание уже завершено` / `Задание уже выполняется` |
| DELETE | `/api/v1/scheduler/tasks/{task_id}` | 204 (job and its runs removed; an in-flight run is aborted first) | 401, 403, 404 |
| GET | `/api/v1/scheduler/tasks/{task_id}/runs?limit=20` | 200 `RunSummary[]`, newest first, `limit` 1-100 | 401, 404, 422 (bad `limit`) |
| GET | `/api/v1/scheduler/runs/{run_id}` | 200 `RunDetail` | 401, 404 `Запуск не найден` |

`POST /tasks` body (`ScheduledTaskCreate`, all fields optional at the schema level, validated in
the service):

```json
{
  "title": "Daily digest",
  "prompt": "Read notes.txt through MCP and summarise it",
  "model": "qwen2.5-7b-instruct",
  "schedule_type": "once | interval | cron",
  "delay_seconds": 60,
  "run_at": "2026-09-26T14:05:00",
  "interval_seconds": 20,
  "cron": "0 9 * * 1-5",
  "max_runs": 2
}
```

- `once`: exactly one of `delay_seconds` (>= 1) or `run_at` (ISO 8601, naive = local time, must be
  in the future). `max_runs` is ignored for one-shot jobs.
- `interval`: `interval_seconds` >= `SCHEDULER_MIN_INTERVAL_SECONDS` (10); optional `max_runs` >= 1.
- `cron`: exactly five fields (`minute hour day month weekday`); optional `max_runs` >= 1.
- The job stores the `model` it was created with; sampling settings (temperature, max tokens,
  system prompt) come from the user's global settings at fire time.
- Semantic validation failures return `422` with a Russian `detail` string, for example:
  `Заполните название и промпт`, `Название не длиннее 200 символов`, `Промпт не длиннее 4000 символов`,
  `Выберите модель`, `Укажите корректное расписание`, `Время запуска уже прошло`,
  `Минимальный интервал — 10 секунд`, `Максимум запусков должен быть не меньше 1`,
  `Некорректное cron-выражение. Нужно 5 полей: минута час день месяц день_недели.`
  Structurally invalid JSON (for example a string where a number is expected) returns the standard
  FastAPI `422` list of errors instead.
- `409` on create: `Слишком много активных заданий (максимум 50)` (`SCHEDULER_MAX_ACTIVE_TASKS_PER_USER`,
  counting active and paused jobs).

### Response shapes

`ScheduledTaskOut`:

```json
{
  "id": 7,
  "title": "Daily digest",
  "prompt": "Read notes.txt through MCP and summarise it",
  "model": "qwen2.5-7b-instruct",
  "schedule_type": "interval",
  "run_at": null,
  "interval_seconds": 20,
  "cron": null,
  "max_runs": 2,
  "run_count": 1,
  "next_run_at": "2026-09-26T11:05:20Z",
  "status": "active",
  "origin_chat_id": 3,
  "created_at": "2026-09-26T11:04:40Z",
  "updated_at": "2026-09-26T11:05:00Z",
  "last_run": { "...": "RunSummary or null" },
  "is_running": false
}
```

- `status`: `active | paused | completed | cancelled`. `next_run_at` is `null` once the job has no
  further slot (one-shot fired, `max_runs` reached, or cancelled). `origin_chat_id` is set for jobs
  created by the chat tool and becomes `null` when that chat is deleted (the job survives).
- `last_run` is the newest run by id; `is_running` is true while a run has status `running`.

`RunSummary` (never carries the result text):

```json
{
  "id": 41,
  "task_id": 7,
  "status": "running | success | failed | skipped",
  "trigger": "schedule | manual",
  "scheduled_for": "2026-09-26T11:05:00Z",
  "started_at": "2026-09-26T11:05:01Z",
  "finished_at": "2026-09-26T11:05:09Z",
  "duration_ms": 8000,
  "is_late": false,
  "model": "qwen2.5-7b-instruct"
}
```

`scheduled_for` is `null` for manual runs; `finished_at` and `duration_ms` are `null` while running;
`is_late` is true when the run started more than `SCHEDULER_LATE_THRESHOLD_SECONDS` (60) after its
slot (for example after the Agent was down).

`RunDetail` extends `RunSummary` with `task_title`, `result_text` (Markdown, `null` unless the run
succeeded), `error` (Russian text, `null` unless the run failed or was skipped) and `tool_trace`
(list of tool-call objects, empty when no tools ran). Run `error` values include
`Превышено время выполнения (120 с)`, `Модель недоступна: LM Studio не запущен`,
`Модель недоступна: HTTP <code>`, `Тайм-аут ответа модели`, `Модель вернула пустой ответ`,
`Предыдущий запуск ещё выполнялся` (skipped overlap), `Прервано: Agent был перезапущен`,
`Прервано: Agent остановлен`, `Прервано: задание удалено`.

### WS /ws/events

User-level live channel for scheduler changes and chat title updates; one socket per browser tab, separate from
`/ws/chat/{chat_id}`.

- Handshake: the `Origin` header must be an allowed app origin (same policy as `/ws/chat`) and the
  session cookie must resolve to a user. On failure the server closes with code `1008` before
  accepting (`Origin not allowed` / `Unauthorized`).
- Frames go only to the sockets of the job's owner; other users never receive them.
- The client sends the text `ping` every 25 s to keep the connection alive; the server ignores
  client text and never replies. Slow clients lose their oldest queued frame rather than blocking
  the scheduler (per-socket queue of 100).
- The UI reloads the job list on every (re)connect, since frames sent while disconnected are not
  replayed.

Server -> client frames (`run_started` and `run_finished` carry a fresh post-commit `task`
snapshot so the UI can update the card without a refetch):

```json
{ "type": "run_started",  "task_id": 7, "run": { "...": "RunSummary" }, "task": { "...": "ScheduledTaskOut" } }
{ "type": "run_finished", "task_id": 7, "run": { "...": "RunSummary" }, "task": { "...": "ScheduledTaskOut" } }
{ "type": "task_updated", "task": { "...": "ScheduledTaskOut" } }
{ "type": "task_deleted", "task_id": 7 }
{ "type": "chat_title_updated", "chat_id": 12, "title": "Настройка WebSocket в FastAPI" }
```

- `run_started`: a run was claimed (schedule) or started manually.
- `run_finished`: a run ended with `success`, `failed` or `skipped` (overlap).
- `task_updated`: the job was created, paused, resumed, cancelled, completed, or a manual run was
  started.
- `task_deleted`: the job and its runs were removed.
- `chat_title_updated`: sent once per chat after its first successful question/answer turn when the
  title was still `New Chat`; `title` is plain text, one line, at most 50 characters; delivered only
  to the chat owner's sockets; not replayed after a reconnect (the UI reloads the chat list on
  reconnect).

### LLM tools

Three built-in chat tools (tool list is now nine: six memory/task tools plus these). They act only on
the chatting user's jobs.

| Tool | Arguments | Result |
|------|-----------|--------|
| `schedule_task` | `schedule_type` (`once`/`interval`/`cron`), `title`, `prompt`, and per type `delay_seconds` or `run_at` / `interval_seconds` / `cron`; optional `max_runs` | `{"status": "scheduled", "id", "title", "schedule_type", "next_run_at", "next_run_at_local", "max_runs"}` |
| `list_scheduled_tasks` | none | `{"status": "ok", "tasks": [{"id", "title", "schedule_type", "schedule", "next_run_at", "next_run_at_local", "status", "last_run_status"}]}` (active and paused jobs only) |
| `cancel_scheduled_task` | `task_id` (> 0), `user_requested_cancellation` (bool) | `{"status": "cancelled", "id", "title"}` |

- `schedule_task` uses the chat's model as the job's model and records the chat as `origin_chat_id`.
  Error results (`{"status": "error", "code", "error"}`): `model_unknown`, `invalid_schedule`
  (message from the schedule validator), `too_many` (per-user cap reached). A `schedule_task` call is
  not counted as a long-term memory write.
- `cancel_scheduled_task` is gated in code, not only by the model: it is rejected with
  `cancel_not_requested` unless `user_requested_cancellation` is true AND the chat's latest message
  is the user's own message that expresses a cancel intent without negation
  (`agent/tool_guard.py::user_asked_to_cancel`, Russian and English verbs). Other error codes:
  `not_found` (missing or foreign job) and `conflict` (job already finished). There is no implicit
  "current job"; the id must be explicit.

## LLM providers (Day 21)

User-scoped OpenAI-compatible providers. Every route needs the session cookie; mutating routes also
require an allowed `Origin` and `Content-Type: application/json` (DELETE and `check` need only the
origin). A missing or foreign id is always 404 `Провайдер не найден` (never 403).

- GET    /api/v1/llm-providers
- POST   /api/v1/llm-providers
- PUT    /api/v1/llm-providers/{provider_id}
- DELETE /api/v1/llm-providers/{provider_id}
- POST   /api/v1/llm-providers/{provider_id}/check
- GET    /api/v1/llm-providers/models?refresh=false

### Request and response shapes

Create body: `{"name": "OpenRouter", "base_url": "https://openrouter.ai/api", "api_key_env": "OPENROUTER_API_KEY", "enabled": true}`.
Update body: any subset of the same fields; an explicit empty or null `api_key_env` clears the key
reference. Response (`LlmProviderOut`):

```json
{
  "id": 3, "name": "Stub", "base_url": "http://127.0.0.1:18766", "kind": "openai",
  "api_key_env": "STUB_KEY", "enabled": true,
  "created_at": "2026-10-02T10:00:00Z", "updated_at": "2026-10-02T10:00:00Z",
  "check": {"status": "ok", "model_count": 1, "checked_at": "2026-10-02T10:00:01Z"}
}
```

`check.status` is `not_checked`, `ok` or `error`; on `error` the body adds `code` and a Russian
`message`. `GET .../models` returns one group per enabled provider:
`[{"provider_id": 3, "name": "Stub", "kind": "openai", "models": [{"id": "stub-model", "loaded": null}], "error": null}]`;
a failing provider has an empty `models` list and a non-null `error`. `refresh=true` re-fetches every
provider instead of using the cached check.

### Errors
- 404 `Провайдер не найден`
- 409 duplicate name for the same user (names are unique per user)
- 422 with a Russian `detail` shown verbatim by the UI: empty or too long name, a base URL that does
  not start with `http://` or `https://`, a malformed environment variable name

A connection check never raises an HTTP error: failures are returned in `check` with one of the codes
`env_missing` (variable not resolvable, no request made), `bad_key` (HTTP 401/403), `unreachable`,
`timeout`, `http` (any other status) and `bad_response` (the model list is not an OpenAI-style
`data` list). Redirects are not followed.

### Keys are referenced by variable name
A provider stores only `api_key_env`, the name of a variable; the key value is never stored,
returned or logged. A name resolves when it is declared in the `.env` file (`LLM_PROVIDER_ENV_FILE`)
or is `DEEPSEEK_API_KEY`; a process variable overrides the file value for a declared name. Undeclared
process variables cannot be referenced. An empty `api_key_env` means no `Authorization` header.

### Seeding and URLs
On first access each user gets an "LM Studio" provider (kind `lm_studio`, from `LM_STUDIO_BASE_URL`)
and, when a `DEEPSEEK_API_KEY` is configured, a "DeepSeek" provider (`https://api.deepseek.com`).
A seeded provider that the user deleted is never created again. Base URLs are normalized: surrounding
whitespace, trailing slashes and a trailing `/v1` are removed.

### Scheduler
`POST /api/v1/scheduler/tasks` accepts an optional `provider_id` (a foreign or missing id gives 422
`Провайдер не найден`) and the task output carries it; a run uses that provider and fails with a recorded error
when it was deleted or disabled. Tasks created by the `schedule_task` tool inherit the chat's provider.

## Chat titles (Day 21)

`POST /api/v1/chats` creates a chat titled `New Chat` unless a title is given. The Agent replaces the
default title automatically (there is no rename endpoint). The title comes from the model that
answered the turn (non-streaming call, temperature 0, max_tokens 30, body field
`reasoning_effort: "none"` so reasoning models answer directly, 20 s timeout covering the whole
attempt). If the backend answers HTTP 400 or 422 the call is repeated once without
`reasoning_effort`. When the call fails, times out or returns no usable text, the title is derived
from the first user message (at most 50 characters, `…` when cut); if that is empty the chat keeps
`New Chat`. A non-default title is never
overwritten. The `done` frame of `/ws/chat/{chat_id}` is unchanged and is not delayed; the new title
arrives separately as a `chat_title_updated` frame on `/ws/events`. The title request goes through the
same provider as the turn (`provider_id`).

## Knowledge bases (Day 21)

User-scoped document collections indexed into a FAISS vector index with an LM Studio embedding model.
All routes require the session cookie; a knowledge base owned by another user answers `404` (never
`403`). Mutating routes (`POST`, `DELETE`) also require an allowed `Origin`; JSON routes require
`Content-Type: application/json`. Error messages are Russian and shown inline by the UI.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/kb` | List the caller's knowledge bases, newest first (`KbOut[]`) |
| POST | `/api/v1/kb` | Create from `multipart/form-data`; answers `202` with `KbOut` and indexes in the background |
| GET | `/api/v1/kb/{kb_id}` | One knowledge base (`KbOut`) |
| DELETE | `/api/v1/kb/{kb_id}` | Cancel a running job, delete rows and the storage directory; `204` |
| POST | `/api/v1/kb/{kb_id}/search` | Test search: body `{"query": str, "top_k": 1..20 = 5}`; `{"results": [...]}` |
| GET | `/api/v1/kb/embedding-models` | LM Studio models with `type`, loaded state and embedding eligibility |
| POST | `/api/v1/kb/embedding-check` | Body `{"model": str}`; runs the indexing guard and returns `{"model", "dim"}` |

### Create (multipart fields)

`name` (1-200 chars), `strategy` (`fixed` | `structural`), `chunk_size` (100-2000, default 1000),
`chunk_overlap` (default 150, less than the size and at most half of it; both ignored for the
`structural` strategy except as the upper bound), `embedding_model` (required) and one or more `files`.

Caps (`agent/kb_limits.py`): up to 10 files, 50 MB per file, 100 MB in total, extensions `.pdf`, `.txt`,
`.md` only. Files are SHA-256 de-duplicated per knowledge base. Validation runs on the server before
anything is kept; any violation answers `422` (`413` for a declared request above the cap) and leaves
no rows or files behind. Messages: `Введите название базы знаний.`, `Выберите хотя бы один файл.`,
`Можно загрузить не больше 10 файлов.`, `Файл {name}: поддерживаются только PDF, TXT и MD.`,
`Файл {name} больше 50 МБ.`, `Общий размер файлов больше 100 МБ.`, `Файл {name} пустой.`,
`Файл {name} уже добавлен.`, `Размер чанка должен быть не меньше 100`,
`Размер чанка не должен превышать 2000`, `Перекрытие должно быть меньше размера чанка и не больше его половины`.

### KbOut

```json
{
  "id": 3, "name": "ФЗ-196", "status": "indexing", "error": null,
  "strategy": "structural", "chunk_size": 1000, "chunk_overlap": 150,
  "embedding_model": "text-embedding-nomic-embed-text-v1.5", "dim": 768,
  "file_count": 1, "chunk_count": 0, "done": 320, "total": 1450,
  "phase": "embedding", "created_at": "2026-10-03T10:00:00Z"
}
```

`status` is `queued`, `indexing`, `ready` or `failed`; `phase` is `loading_model`, `parsing` or
`embedding` while indexing. `error` is a readable Russian message when `failed`.

### Search result item

`{"rank", "score", "chunk_id", "source", "section", "page_start", "text"}` - `score` is cosine
similarity, `section` is the structural breadcrumb (for example `Глава 5 > Статья 5.1`) or null.
Status codes: `409` the knowledge base is not ready or its index is corrupt, `422` empty query or an
embedding error, `503` LM Studio is not running.

### Embedding models

Only models LM Studio reports as `type: embeddings` are eligible. LM Studio's `/v1/embeddings`
ignores the requested model name and answers with whichever embedding model is loaded, so an `llm`
model such as `giga-embeddings-instruct-480m-0826` would silently produce vectors from another model.
The guard (D-24) therefore rejects non-embedding models with a message containing
`не поддерживает эмбеддинги`; `POST /embedding-check` and the indexer apply the same guard.

### Events on /ws/events

```json
{ "type": "kb_progress", "kb": { "...": "KbOut" } }
{ "type": "kb_deleted", "kb_id": 3 }
```

- `kb_progress`: status or progress changed (throttled to about two frames per second while indexing).
  A job cancelled by a delete may publish a final `failed` frame (`Индексация прервана.`) just before
  `kb_deleted`.
- `kb_deleted`: the knowledge base was removed. Frames go only to the owner's sockets.
- After an Agent restart, jobs left in `queued` or `indexing` become `failed` with
  `Индексация прервана перезапуском агента. Удалите базу и создайте её заново.`

## Long-term memory (Day 21)

Long-term memory is stored per user and shared by all of the user's chats. All routes below need the
session cookie. Ownership is enforced in SQL by `user_id`: another user's entry is indistinguishable
from a missing one.

### GET /api/v1/chats/{chat_id}/memory

Response: `{ "chat_id": 1, "short_term_message_count": 4, "working": [entry], "long_term": [entry] }`
where `entry = { "id": 7, "key": "...", "value": "...", "updated_at": "..." }`. `long_term` is the same
list from every chat of the user. Status codes: 200, 401 (no session), 404 (chat of another user or
unknown).

### PUT /api/v1/memory/long-term/{entry_id}

Request body (JSON, at least one field):

```json
{ "key": "example_key", "value": "example text" }
```

- `key` (optional): stripped, 1-200 characters.
- `value` (optional): not whitespace-only, at most 50 000 characters, stored verbatim.

Response 200: the updated entry `{ id, key, value, updated_at }`.

| Status | Meaning |
|--------|---------|
| 200 | Entry updated |
| 401 | No session |
| 403 | Origin not allowed |
| 404 | `Запись памяти не найдена` (unknown id or another user's entry) |
| 409 | `Запись с таким ключом уже существует` (another entry of the same user has this key; nothing is changed) |
| 415 | Content-Type is not application/json |
| 422 | Validation: no field, blank key, whitespace-only value, key over 200, value over 50 000 |

### DELETE /api/v1/memory/long-term/{entry_id}

Status codes: 204 (deleted), 401, 403, 404 (unknown id or another user's entry).

Notes: `updated_at` is refreshed and `created_at` is kept on update. There is no create route (entries are
created by the LLM tool `save_long_term_memory`). No WebSocket frame is sent for edits; the change is
visible to the model from the next turn.

## Environment Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| SCHEDULER_ENABLED | true | Start the background poll loop in the Agent lifespan (orphan recovery runs regardless) |
| SCHEDULER_POLL_INTERVAL | 1.0 | Seconds between ticks that look for due jobs |
| SCHEDULER_RUN_TIMEOUT | 120.0 | Overall wall-clock limit of one run; exceeding it fails the run |
| SCHEDULER_MIN_INTERVAL_SECONDS | 10 | Smallest accepted interval for interval jobs |
| SCHEDULER_LATE_THRESHOLD_SECONDS | 60.0 | A run started later than this after its slot is flagged `is_late` |
| SCHEDULER_MAX_CONCURRENT_RUNS | 2 | Runs executing at the same time (others wait for a slot) |
| SCHEDULER_MAX_ACTIVE_TASKS_PER_USER | 50 | Cap on active plus paused jobs per user |
| MCP_TOOL_CALL_TIMEOUT | 30.0 | Seconds allowed for one MCP tool call made from a chat turn |
| MCP_TOOL_RESULT_MAX_CHARS | 20000 | Maximum characters of an MCP tool result sent to the model |
| LLM_PROVIDER_CHECK_TIMEOUT | 10.0 | Seconds allowed for a provider connection check or model-list request (not `LLM_TIMEOUT`) |
| LLM_PROVIDER_ENV_FILE | .env | File whose declared variable names provider `api_key_env` references may resolve |
| KB_STORAGE_DIR | empty | Root for knowledge-base uploads and indexes; empty means `<DB_PATH stem>_kb` next to the database |
| KB_EMBED_TIMEOUT | 120.0 | Seconds allowed for one embeddings request to LM Studio |
| MCP_AUTO_CONNECT | true | Connect the user's enabled, unconnected MCP servers at the start of a chat turn (failures not retried until manual reconnect/edit) |

With `MCP_AUTO_CONNECT` on, the first chat turn may wait up to `MCP_CONNECT_TIMEOUT` for a server
that hangs at handshake, and `GET /api/v1/mcp/servers` then reports auto-connected servers as
`connected`.
