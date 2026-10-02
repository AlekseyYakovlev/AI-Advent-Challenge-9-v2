# Testing Guide

## Required Tests
- test_database.py: WAL, retry_on_locked_db, CASCADE
- test_settings_fallback.py: global → per-chat inheritance
- test_cascade_delete.py: delete chat → cleanup in-memory caches (including the pending title job)
- test_lm_studio_client.py: load/unload/timeout scenarios
- test_concurrent_ws.py: 5 parallel WS messages, no IntegrityError
- test_ws_security.py: origin validation, rate limiting, idle timeout
- test_supervisor.py: agent crash → restart within 5 seconds
- test_scheduler_*.py: see "Scheduler (Day 18)" below
- test_titles.py / test_titles_ws.py: see "Chat auto-titling (Day 21)" below
- test_llm_complete_chat.py: see "Chat auto-titling (Day 21)" below

## Fixtures
- Use conftest.py for shared fixtures
- Separate test_app.db for each test session
- respx for HTTP mocking

## Strategy Tests

### test_no_compression_blocks_on_overflow
- Set strategy to "no_compression"
- Set context_length to 100
- Send 10 long messages (200+ tokens each)
- Verify ContextOverflowError is raised
- Verify user message is deleted from database
- Verify error sent to WebSocket with code "CONTEXT_OVERFLOW"

### test_sliding_window_preserves_database
- Set strategy to "sliding"
- Set context_length to 500
- Send 20 long messages
- Verify all 20 messages preserved in database
- Verify only recent messages sent to LLM (check logs)

### test_sticky_facts_creates_summary
- Set strategy to "sticky"
- Set context_length to 500
- Send 20 long messages
- Verify all messages preserved in database
- Verify summary_text field populated in settings
- Verify summary is in English (check logs)

### test_truncate_middle_preserves_structure
- Set strategy to "truncate_middle"
- Set context_length to 500
- Send 25 messages
- Verify all 25 messages preserved in database
- Verify first 5 and last 10 messages sent to LLM
- Verify middle summary generated (check logs)

### test_strategy_change_unblocks_input
- Set strategy to "no_compression" with small context_length
- Trigger overflow
- Verify input blocked
- Change strategy to "sliding"
- Verify input unblocked
- Verify new messages work normally

### test_statistics_endpoint
- Create chat with messages
- Call GET /api/v1/chats/{chat_id}/stats
- Verify all fields present
- Verify current_context_size reflects strategy
- Verify context_usage_percent calculated correctly

## Scheduler (Day 18)

`tests/conftest.py` sets `SCHEDULER_ENABLED=false`, so no poll loop runs during tests. Service tests
call `SchedulerService.tick(now)` (optionally with `spawn=False`) with an explicit clock; runs are
executed by calling `execute_run` with `run_headless_turn` patched, or by driving the headless
runner against a `respx`-mocked LM Studio. Never wait for wall-clock ticks.

### test_scheduler_schedule.py (pure schedule math)
- Cron: exactly five fields accepted and whitespace-normalized; 6 fields, garbage and over-long
  expressions rejected; next slot is strictly after the reference time in local wall time.
- Interval: future anchor kept; after downtime exactly one next slot on the original cadence;
  an exact slot moves strictly forward.
- `run_at`: naive means local time, an offset is honoured, garbage rejected, past rejected.
- `build_schedule_spec`: once needs exactly one of delay/run_at; interval minimum enforced;
  `max_runs` >= 1 (and ignored for once); unknown type rejected.
- `next_run_after_claim` (catch-up: interval keeps cadence, cron from now, once -> none) and
  `next_run_on_resume`; module must not use `zoneinfo` or `utcnow`.

### test_scheduler_models.py
- `init_db` creates both tables and the partial index `uq_taskrun_one_running`.
- A second `running` run for the same job is rejected; many `skipped` runs, running runs of
  different jobs, and a new run after the previous finished are accepted.
- Enums are stored as lowercase literals.
- Deleting a chat keeps the job with `origin_chat_id = NULL` (SET NULL); deleting a job deletes its
  runs; deleting a user deletes jobs and runs (CASCADE).

### test_scheduler_service.py (claim, recovery, execution)
- Claim atomicity: two concurrent claims of the same slot yield exactly one run; a stale claim of an
  already consumed slot returns nothing; a racing manual run rolls the claim back and the next tick
  records a skipped run.
- Catch-up: interval and cron jobs overdue after downtime run once, flagged late, and land on the
  next boundary.
- Overlap skip: a slot that comes due while a run is `running` creates a `skipped` run that does not
  count toward `run_count`.
- `max_runs`: exhausts the job (`next_run_at` cleared, then `completed`); skips do not count; a
  paused job with a pending slot is never finalized; inactive jobs are never claimed.
- Recovery: orphaned `running` runs become `failed` with the restart message and exhausted jobs are
  finalized.
- Execution: success persists result and publishes `run_finished`; a headless error, a timeout
  (`Превышено время выполнения (120 с)`) and an unexpected exception each map to a Russian error and
  the next slot still fires; a one-shot job completes after its run.
- Manual run on an interval job keeps the next slot; on a one-shot job it clears it. Aborting a
  job's runs cancels the in-flight task. The loop test waits for `_runs` to drain before `stop()`.
- Events: `run_started` goes only to the owner and carries a fresh task snapshot; datetimes are UTC.

### test_scheduler_runner.py (headless runner)
- Plain answer needs no chat or lock; a tool round saving long-term memory writes for the job owner.
- Only allowlisted built-in tools plus MCP tools are offered; a hallucinated or non-allowed built-in
  tool is rejected; the allowlist does not restrict MCP bindings; without an allowlist chat dispatch
  is unchanged.
- LM Studio unreachable, HTTP error and read timeout (including in a later round) map to Russian
  messages; an empty answer fails; the round cap bounds tool rounds.
- Sampling comes from global settings; a missing settings row uses defaults without creating it; the
  system message carries the preface, clock line and long-term memory.
- MCP unavailable: note appended (or named in the empty-answer error); a toolset failure never fails
  the run.

### test_scheduler_lifespan.py
- Loop stays off when `SCHEDULER_ENABLED=false` but orphaned runs are still recovered.
- Loop runs inside the lifespan when enabled and stops on exit, before MCP cleanup and engine
  disposal.

### test_scheduler_api.py (ops and REST)
- Create: once sets `next_run_at` and origin chat; invalid text fields rejected; per-user cap gives
  409 (Russian message); REST create returns 201 with UTC ISO datetimes; Russian 422 details; 415 for
  a non-JSON body; 403 for a foreign `Origin` on every mutating route.
- Lifecycle: pause keeps `next_run_at` and rejects a second pause; resume restarts interval jobs from
  now and keeps a once `run_at`; cancel is soft and 409 on a finished job; run-now creates a manual
  run, works when paused, 409 when finished or already running; delete aborts in-flight runs and
  removes runs.
- IDOR: another user's job or run gives 404 (never 403) on every route.
- List ordering (live first, then next run, then newest), runs newest first with `limit`, run detail.
- Mutations publish events to the owner only.
- Unauthenticated requests get 401.

### test_scheduler_tools.py (LLM tools)
- Argument validation per schedule type; cancel args require the flag.
- `schedule_task` creates once, interval and cron jobs with the chat's model and origin chat;
  `model_unknown`, `invalid_schedule` and `too_many` error codes.
- `list_scheduled_tasks` returns only the caller's active and paused jobs.
- Cancel gate: blocked when the latest message has no cancel intent, when the flag is false, when the
  chat leaf is not a user message, and without a chat; succeeds only when the user asked; foreign,
  missing and finished jobs give `not_found` / `conflict`; intent detection handles negation.
- Deleting the origin chat keeps the job. The tool list has nine tools; `schedule_task` is not
  counted as a long-term memory write.

### test_scheduler_events.py (`/ws/events` and `EventHub`)
- Rejected with 1008 without a cookie, with a garbage cookie, and with a foreign `Origin`.
- A published frame reaches the owner; users are isolated; every socket of one user gets the frame;
  the subscription is removed on close.
- Hub: delivery only to the user's queues, oldest frame dropped when a queue is full, publish without
  subscribers is a no-op, the user key is removed with the last queue.

## Chat auto-titling (Day 21)

### test_titles.py
- Sanitizer table: think-blocks, labels, quotes, markup and angle brackets, length cap.
- Fallback title; tag-breakout stripping including nested tags; request arguments.
- Fallback on error, empty output and timeout.
- Never overwrite: custom title, rename mid-flight, race of two jobs, deleted chat.
- Owner-only frame; ownerless chat produces no frame; one job per chat; cancel on cleanup.
- Reasoning-style answer (content empty, finish_reason length, reasoning_content set): the request
  carries `reasoning_effort: "none"` and max_tokens 30, `chat_title_llm_unusable` is logged, the
  fallback title is applied.
- HTTP 400 / 422 on the first call: one repeat without the field, title stored with source llm; two
  rejections or an HTTP 500: fallback, no further retry.
- Title log events never contain message or model text.
- Input bound: `fallback_title`, `build_title_messages` and `clean_title` finish under 0.5 s on
  100 000-character hostile input.

### test_titles_ws.py
- The first turn triggers the job once with the turn's model; a chat created with an empty body gets
  the default title.
- A custom title, a second turn and a failed turn do not trigger.
- Request shape: non-streaming, temperature 0, max_tokens 30, reasoning_effort none, no tools,
  system prompt only.
- Fallback on HTTP 500 and ConnectError.
- A reasoning-only title answer still ends the turn with `done` and delivers the fallback title.
- Owner-only delivery; the socket may be closed after the turn; `done` is not delayed.

### test_llm_complete_chat.py
- `complete_chat` payload is exactly the five core keys and returns a string.
- `complete_chat_detailed` sends `extra_body` fields without overriding core keys, parses
  `finish_reason`, the reasoning flag and `completion_tokens`, tolerates missing fields and null
  content, and raises `httpx.HTTPStatusError` on HTTP 400.

Log assertions replace `agent.titles.logger` with a recording stub (structlog loggers are cached on
first use).

Title tests must create chats titled `New Chat` and classify mocked LLM requests by `stream` and the
`<user_message>` tag instead of an ordered response queue.

## LLM providers (Day 21)

### test_llm_providers_config.py
- Environment variable names are validated (valid and invalid forms).
- A secret resolves from the `.env` file; a process variable wins for a declared name; an undeclared
  process variable never resolves; `DEEPSEEK_API_KEY` is the builtin name; empty values give None.
- Migration adds `ScheduledTask.provider_id` once; provider names are unique per user; deleting a user
  cascades to providers.

### test_llm_providers_service.py
- Seeding: LM Studio always, DeepSeek only with a key (also when the key appears later); idempotent,
  concurrency-safe and restart-safe; a deleted seed is not resurrected; a name collision does not
  break seeding.
- `get_provider_row` / `resolve_client`: None selects LM Studio; foreign, missing and disabled rows
  raise `ProviderUnavailableError`.
- Field validation messages (name, URL, env name) and URL normalization.
- Connection check codes: ok, bad_key, unreachable, timeout, http, bad_response, env_missing (no
  request made); redirects are not followed; no key means no `Authorization` header.
- Model groups: ok, failing and disabled providers; cache reuse without refresh; update and delete
  drop the cache.

### test_llm_providers_api.py
- List seeds LM Studio and DeepSeek without leaking the key value; create normalizes the URL and
  rejects invalid input; duplicate names give 409.
- Update keeps other fields, clears `api_key_env` on empty or null, conflicts on a taken name.
- Another user's provider is always 404; delete does not resurrect a seed.
- `check` reports failures as data; `models` groups work with `refresh`.
- Foreign origin and non-JSON content type are rejected on mutations.
- LM Studio routes honour `provider_id` (400 for another kind, 404 for foreign) and keep working
  without it.

### test_llm_providers_routing.py
- A legacy payload streams from LM Studio without auth; a payload with `provider_id` streams from
  the provider with the bearer key of its variable.
- Deleted, disabled and foreign providers give a `PROVIDER_UNAVAILABLE` frame and store no message;
  a foreign provider's name is never disclosed.
- HTTP 401 during the stream or the tool follow-up names the provider, never the key.
- The turn's provider reaches the title job, fact extraction and self-critique.

### test_scheduler_providers.py
- REST create stores its own `provider_id`, accepts none, rejects a foreign one; the
  `schedule_task` tool inherits the chat's provider.
- A headless turn calls the job's provider with its bearer key, falls back to LM Studio without one,
  and fails for deleted or disabled providers (run recorded as failed).
- A connect error on an OpenAI provider names the server; LM Studio keeps its legacy message.

### test_live_deepseek_title.py (opt-in)
- Skipped unless `RUN_LIVE_DEEPSEEK=1` and a real `DEEPSEEK_API_KEY` is in the repository `.env`.
- Checks the seeded DeepSeek provider (status ok), picks `deepseek-chat` or the first model, and
  requires a non-empty title of at most 50 characters that differs from the user text. Paid call;
  the key is never printed.

### scripts/e2e_llm_providers_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app at UI :18000 / Agent :18001 with a stub OpenAI-compatible provider
  on :18766; never touches :8000/:8001. Exit 0 all passed, 1 a check failed, 2 ports busy, 4
  Playwright missing.
- Scenarios: provider section and seeded cards; URL validation message; save with automatic check;
  wrong key variable gives the error badge; unreachable LM Studio badge; picker grouped by provider
  as "Provider · model"; chat answered by the stub with its bearer key and title routed through it;
  disabling the provider removes its entries and shows the fallback toast; delete with confirm; no
  API response contains a key value; optional DeepSeek chat when a real key exists.

