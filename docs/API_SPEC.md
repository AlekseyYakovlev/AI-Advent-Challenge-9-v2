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

Since Day 25 the response also carries `task_state` (see "Task memory" below): the task memory of the chat
as `{ "goal": str or null, "clarified": [{"id": int, "text": str}], "constraints": [{"id": int, "text": str}] }`,
or `null` when the chat has no RAG (mode `off`, no knowledge base) or `TASK_MEMORY_ENABLED` is off.

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

## Chat RAG (Day 22)

A chat can answer with fragments retrieved from one of the caller's ready knowledge bases. The
setting is per chat and stored in `ChatRagConfig`; the knowledge base stays the Day 21 one. A chat
with no row behaves as `off`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/chats/{chat_id}/rag` | Current setting: `{chat_id, mode, kb_id, kb_name, kb_status, top_k, candidate_k, threshold, calibrated_threshold, effective_threshold, threshold_source, lexical, llm_rerank, hybrid, rewrite, strict}`; defaults `off`, `null`, `5`, `20`, `null`, `null`, `0.0`, `none`, four `false`, `strict` `true` |
| PUT | `/api/v1/chats/{chat_id}/rag` | Body `{"mode": "off" or "rag", "kb_id": int or null, "top_k": 1..20 = 5, "candidate_k"?, "threshold"?, "lexical"?, "llm_rerank"?, "hybrid"?, "rewrite"?, "strict"?}`; answers the same object |
| GET | `/api/v1/kb/{kb_id}/chunks/{chunk_id}?file=` | Text of one chunk: `{chunk_id, source, section, title, text}` |

- `PUT` requires an allowed `Origin` and `Content-Type: application/json`. A chat or knowledge base
  owned by another user answers `404` (never `403`). A knowledge base that is not `ready` answers
  `422` ("База знаний ещё не готова"); `mode` or `top_k` outside the allowed values answer `422`.
  `kb_id: null` forces `mode` to `off`.
- A deleted knowledge base is detached (`kb_id` becomes `null`, the row keeps its `mode`).
- The chunk route answers `404` when the knowledge base is not the caller's, the chunk does not
  exist, or `file` is given and differs from the chunk's source (guards a stale citation against a
  reused `kb_id`).

### Search settings (Day 23)

All search fields of `PUT` are optional; a field that is omitted stays unchanged.

| Field | Type | Meaning |
|-------|------|---------|
| `candidate_k` | int 1..50 | Candidates fetched before the cut and rerank; clamped to at least `top_k`; default 20 |
| `threshold` | float 0..1 or `null` | Raw-cosine cut-off. An explicit `null` resets to the calibrated value; omitted leaves it as is |
| `lexical` | bool | Lexical rerank fused with cosine |
| `llm_rerank` | bool | One batched LLM rerank of the top 10 survivors (one extra model call) |
| `hybrid` | bool | FTS5 keyword search merged by RRF, with the FTS exemption |
| `rewrite` | bool | Query rewrite by the chat model (one extra model call) |
| `strict` | bool, default `true` | Strict mode (Day 24): quotes verified by code and the «не знаю» gate; optional in `PUT`; a non-boolean value answers `422`; omitted leaves it unchanged. Existing chats read `true` after the migration |

Response fields: `candidate_k`; `threshold` (the stored override or `null`); `calibrated_threshold`
(the value for the KB's embedding model or `null` when the model has no calibration);
`effective_threshold` (what the next turn uses); `threshold_source` (`user`, `calibrated` or `none`);
the four flags and `strict`. Non-boolean flags, `candidate_k` outside 1..50 and `threshold` outside 0..1
answer `422`. Changes apply from the next message.

### Message.rag_sources

`MessageResponse` (history, `GET /api/v1/chats/{id}/tree`) carries `rag_sources`: `null` for user
messages and for messages written before this feature, otherwise the payload below. Only metadata is
stored, never fragment text; the stored user message is always the raw question.

### done.rag

The final WebSocket `done` frame carries the same payload under `rag`:

```json
{
  "v": 1,
  "mode": "rag",
  "kb_id": 3,
  "kb_name": "fz196",
  "top_k": 5,
  "sources": [
    {"rank": 1, "chunk_id": "c-12", "file": "FZ_196.pdf", "section": "Глава IV > Статья 26", "page": "1-74", "score": 0.804}
  ],
  "dropped": 0,
  "context_tokens": 1450,
  "warning": null
}
```

- `mode` is `off` or `rag`; with `off` the source list is empty and `warning` is `null`.
- `dropped` counts fragments removed (lowest score first) to fit the RAG budget.
- `warning` is `{code, text}` when retrieval failed and the answer was produced without fragments
  (the turn never fails because of RAG). Codes: `kb_deleted`, `kb_not_ready`, `embedder_unavailable`,
  `dim_mismatch`, `index_corrupt`, `context_full`, `retrieval_failed`. `text` is a Russian message.

### Payload v2 (Day 23)

Messages written by Day 23 carry `"v": 2`, a `verdict` and a `search` trace next to the fields above
(`sources` keeps its shape and lists the final answer chunks):

```json
{
  "v": 2, "mode": "rag", "kb_id": 3, "kb_name": "fz196", "top_k": 5,
  "sources": [{"rank": 1, "chunk_id": "c-12", "file": "FZ_196.pdf", "section": "Статья 26", "page": "1-74", "score": 0.804}],
  "dropped": 0, "context_tokens": 1450, "warning": null,
  "verdict": "ok",
  "search": {
    "query": "…", "rewritten": null, "rewrite_cosine": null,
    "config": {"candidate_k": 20, "top_k": 5, "threshold": 0.67, "threshold_source": "calibrated",
               "lexical": true, "llm": false, "hybrid": false, "rewrite": false},
    "stages": ["threshold", "lexical"], "stage_ms": {"lexical": 3}, "skipped": [],
    "latency_ms": 412, "best_cosine": 0.804,
    "candidates": [{"chunk_id": "c-12", "file": "FZ_196.pdf", "section": "Статья 26", "page": "1-74",
                    "rank_before": 2, "rank_after": 1, "cos": 0.804, "lex": 0.71, "fts_rank": null,
                    "llm": null, "found_by": "original", "fts_exempt": false, "status": "in_answer"}]
  }
}
```

| Field | Meaning |
|-------|---------|
| `verdict` | `ok`, `below_threshold`, `kb_unavailable` or `off` |
| `search.query` / `rewritten` / `rewrite_cosine` | The question, the accepted rewrite (or `null`) and the cosine between the two query vectors |
| `search.config` | Effective settings of the turn (`llm` is the `llm_rerank` flag) |
| `search.stages` | Stages that ran, in order: `threshold` always, then `rewrite`, `hybrid`, `lexical`, `llm` as applicable |
| `search.skipped` | `{stage, reason}` for each optional stage that was skipped; reasons `timeout`, `http_error`, `bad_output`, `no_llm`, `search_failed`, `fts_error`, `stage_error` |
| `search.latency_ms` / `stage_ms` | Total and per-stage time |
| `search.best_cosine` | Highest raw cosine among the candidates (`null` when there are none) |
| `candidates[].rank_before` / `rank_after` | Position before the cut/rerank and in the final list (`null` when not in it) |
| `candidates[].cos` / `lex` / `fts_rank` / `llm` | Raw cosine, lexical score, FTS5 rank, LLM score (`null` when the stage did not run) |
| `candidates[].found_by` | `original`, `rewritten` or `both` |
| `candidates[].fts_exempt` | `true` when the chunk is below the threshold but kept as an FTS keyword match |
| `candidates[].status` | `in_answer`, `below_threshold`, `outside_top_k`, `over_budget` |

The trace never contains chunk text. Payloads of older messages are `v: 1` with no `verdict` or
`search`; the UI shows them as before, without a details block.

### Payload v3 (Day 24)

Messages written by Day 24 carry `"v": 3`: everything of v2 plus the keys below. Payloads of version 1 and
2 stay valid for old messages and the UI renders them as before.

```json
{
  "v": 3, "mode": "rag", "kb_id": 3, "kb_name": "fz196", "top_k": 5,
  "sources": [{"rank": 1, "chunk_id": "c-12", "file": "FZ_196.pdf", "section": "Статья 26", "page": "1-74", "score": 0.804}],
  "verdict": "ok", "search": {"...": "as in v2"},
  "strict": true, "gated": false,
  "quotes": [{"text": "…", "state": "exact", "rank": 1, "chunk_id": "c-12", "file": "FZ_196.pdf",
              "section": "Статья 26", "rebound": false, "bad_ref": false, "auto": false}],
  "cited_ranks": [1], "invalid_refs": 0, "answer_supported": true, "answer_empty": false
}
```

| Field | Meaning |
|-------|---------|
| `strict` | Strict mode was on for this turn |
| `gated` | `true` when the code refused without calling the model (`verdict` is `below_threshold`); the stored reply is the fixed «Не знаю: ...» text with a clarifying question |
| `quotes[]` | Quotes of the answer; empty when strict is off, the turn was gated or the model answered «не знаю» |
| `quotes[].text` | Quote string, capped at 1000 characters (model output, or a sentence the code picked for an auto quote); the only text stored in the payload |
| `quotes[].state` | `exact`, `fuzzy` (similarity at least 0.9) or `unverified` |
| `quotes[].rank` / `chunk_id` / `file` / `section` | The fragment the quote belongs to; `null` when the reference is invalid. Metadata is copied from the fragment, so `file` equals the `file` of the source with that rank |
| `quotes[].rebound` | The quote was not in the named fragment but was found in another one and was re-attached |
| `quotes[].bad_ref` | The named fragment number does not exist |
| `quotes[].auto` | Picked by the code because no model quote was verified |
| `cited_ranks` | Ranks referenced by `[N]` in the body or by a quote |
| `invalid_refs` | Count of references to a non-existent fragment |
| `answer_supported` | `true` when the body has a valid `[N]` reference or a quote is verified, `false` otherwise, `null` when not evaluated |
| `answer_empty` | The model returned no text |

The `verdict` set gains `model_idk` (the model itself answered «Не знаю» without quotes). A gated turn
sends over the WebSocket exactly one `token` frame with the whole reply, followed by `done` with the same
payload under `rag`; no completion request is made to the model. The streamed text of a normal strict
turn still contains the «Цитаты:» tail; the stored `Message.content` and the history do not.

### History settings (Day 25)

`GET` and `PUT /api/v1/chats/{chat_id}/rag` carry one more field, `history_turns`: how many of the last
question-answer pairs of the active branch the search sees when it condenses a follow-up into a standalone
query. Integer 0..10, default 3; `0` means the condensing step sees the task memory only. It is optional in
`PUT` (omitted leaves it unchanged); a value outside 0..10 or a non-integer answers `422`. Changes apply from
the next message.

### Task memory (Day 25)

Task memory belongs to chats with RAG on. The three routes below need the session cookie and an owned chat
(another user's chat answers `404`). Mutating routes check the `Origin`.

| Method | Path | Body | Response |
|--------|------|------|----------|
| PUT | `/api/v1/chats/{chat_id}/task-memory/goal` | `{"goal": "..."}`, 1..300 characters after trimming | the new `task_state` |
| DELETE | `/api/v1/chats/{chat_id}/task-memory/items/{item_id}` | none | the new `task_state` without that item |
| POST | `/api/v1/chats/{chat_id}/task-memory/reset` | `{}` | an empty `task_state` (`goal: null`, empty lists) |

Status codes: `200` success; `401` no session; `403` origin not allowed; `404` chat of another user or
unknown, and (for the item route) no item with that id; `409` the chat has no RAG ("Память задачи доступна
только в чатах с включённым RAG"); `415` `PUT` or `POST` without `Content-Type: application/json`; `422` an
empty or too long goal, or a malformed body. Each route runs under the per-chat lock, so it never interleaves
with an answer being written.

`POST /api/v1/chats/{chat_id}/branch` also restores the task memory from the new active path: the memory
becomes the snapshot of the newest assistant message on that path (an empty memory when there is none). A
manual edit made after the last answer is replaced by the snapshot.

The WebSocket `done` frame carries the memory after the turn as `rag.task_memory` (the snapshot described in
payload v4), so the client updates its sidebar without a reload.

### Payload v4 (Day 25)

Messages written by Day 25 carry `"v": 4`: everything of v3 plus the key `task_memory` and two trace fields.
Payloads of versions 1 to 3 stay valid for old messages and the UI renders them as before.

```json
{
  "v": 4, "mode": "rag", "kb_id": 3, "kb_name": "kb", "top_k": 5,
  "verdict": "ok",
  "search": {"query": "а за повторное?", "rewritten": "штраф за повторное превышение скорости",
             "condensed": true, "history_pairs": 2, "stages": ["threshold", "history", "lexical"], "...": "as in v2"},
  "task_memory": {
    "goal": "Подготовить памятку о штрафах за превышение скорости",
    "clarified": [{"id": 1, "text": "только физические лица"}],
    "constraints": [{"id": 2, "text": "отвечать только по КоАП"}],
    "new": {"goal": true, "ids": [1, 2]},
    "failed": false
  }
}
```

| Field | Meaning |
|-------|---------|
| `task_memory` | Snapshot of the memory after the turn; absent on messages of chats without RAG and on messages written before Day 25; a gated turn carries it too |
| `task_memory.new` | What this turn added: `goal` (the goal was set or replaced) and the `ids` of new items |
| `task_memory.failed` | The extraction failed; the memory is the previous state |
| `search.condensed` | `true` when the accepted standalone query came from history condensing (it is then also `search.rewritten`) |
| `search.history_pairs` | Number of question-answer pairs the condensing step saw |
| `search.stages` | Gains the stage name `history` when condensing ran; a skipped condensing is listed in `search.skipped` as `{stage: "history", reason}` |

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
| TASK_MEMORY_ENABLED | true | Eval-only switch: `false` turns off task memory and history-aware condensing (the «no memory» baseline of `rag_eval.py dialog`); no UI or API surface |
| TASK_MEMORY_TIMEOUT | 30.0 | Seconds allowed for the task-memory extraction call after an answer |
| MCP_AUTO_CONNECT | true | Connect the user's enabled, unconnected MCP servers at the start of a chat turn (failures not retried until manual reconnect/edit) |

With `MCP_AUTO_CONNECT` on, the first chat turn may wait up to `MCP_CONNECT_TIMEOUT` for a server
that hangs at handshake, and `GET /api/v1/mcp/servers` then reports auto-connected servers as
`connected`.
