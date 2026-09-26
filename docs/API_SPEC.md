# API Specification

## Agent (port 8001)
- GET  /health
- GET  /api/v1/chats
- POST /api/v1/chats
- DELETE /api/v1/chats/{chat_id}
- GET  /api/v1/chats/{chat_id}/tree
- GET  /api/v1/chats/{chat_id}/stats
- POST /api/v1/chats/{chat_id}/branch
- GET  /api/v1/settings?chat_id={id}
- PUT  /api/v1/settings
- WS   /ws/chat/{chat_id}
- WS   /ws/events (see "Scheduler")
- GET/POST/DELETE /api/v1/scheduler/... (see "Scheduler")

## LM Studio
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

User-level live channel for scheduler changes; one socket per browser tab, separate from
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
```

- `run_started`: a run was claimed (schedule) or started manually.
- `run_finished`: a run ended with `success`, `failed` or `skipped` (overlap).
- `task_updated`: the job was created, paused, resumed, cancelled, completed, or a manual run was
  started.
- `task_deleted`: the job and its runs were removed.

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
| MCP_AUTO_CONNECT | true | Connect the user's enabled, unconnected MCP servers at the start of a chat turn (failures not retried until manual reconnect/edit) |

With `MCP_AUTO_CONNECT` on, the first chat turn may wait up to `MCP_CONNECT_TIMEOUT` for a server
that hangs at handshake, and `GET /api/v1/mcp/servers` then reports auto-connected servers as
`connected`.
