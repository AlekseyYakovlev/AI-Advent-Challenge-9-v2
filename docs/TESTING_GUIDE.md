# Testing Guide

## Required Tests
- test_database.py: WAL, retry_on_locked_db, CASCADE
- test_settings_fallback.py: global → per-chat inheritance
- test_cascade_delete.py: delete chat → cleanup in-memory caches (including the pending title job)
- test_lm_studio_client.py: load/unload/timeout scenarios
- test_concurrent_ws.py: 5 parallel WS messages, no IntegrityError
- test_ws_security.py: origin validation, the configurable shared rate limit (see "Limits (Day 30)"), idle timeout
- test_supervisor.py: agent crash → restart within 5 seconds; start reaches ready without a forced restart
- test_scheduler_*.py: see "Scheduler (Day 18)" below
- test_titles.py / test_titles_ws.py: see "Chat auto-titling (Day 21)" below
- test_llm_complete_chat.py: see "Chat auto-titling (Day 21)" below
- test_modal_close_policy.py: modals have no backdrop-click or Escape closer; every "*-modal" overlay has a bound "btn-close-*" button
- test_memory.py / test_memory_api.py / test_context_engine_memory.py / test_memory_panel_ui.py: see "Long-term memory editing (Day 21)" below
- test_bind_host.py / test_supervisor_host.py / test_origins.py / test_network*.py / test_run_banner.py / test_port_literals.py: see "LAN exposure (Day 30)" below
- test_limits_config.py / test_limits_core.py / test_rate_limit.py / test_generation_queue.py / test_login_lockout.py / test_limits_ui.py / test_verify_service.py: see "Limits (Day 30)" below

## Fixtures
- Use conftest.py for shared fixtures
- Separate test_app.db for each test session
- respx for HTTP mocking
- conftest pins the four limit variables (`RATE_LIMIT_MESSAGES_PER_MINUTE`, `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`, `MAX_CONCURRENT_GENERATIONS`) and resets the login throttle and the generation gate before every test
- a test changes a limit with `monkeypatch.setattr(settings, NAME, value)` and moves time with `monkeypatch.setattr(limits, "_now", ...)` instead of sleeping

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
- Verify usage_percent calculated correctly

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


## Knowledge bases (Day 21)

- `test_kb_models.py`: table creation and cascade deletes of KB, document and chunk rows.
- `test_kb_storage.py`: storage paths, FAISS byte-buffer round trip, in-memory state.
- `test_kb_loaders.py`: golden cleaning of КоАП and ФЗ-196 excerpts, page offsets, de-hyphenation, scan detection, broken PDF, text decoding.
- `test_kb_chunking.py`: fixed and structural chunking, breadcrumbs, 2000-char cap, parameter validation messages.
- `test_kb_embeddings.py`: LM Studio embeddings client, model guard (D-24), batching, prefixes, error mapping.
- `test_kb_indexer.py`: background job, progress, all-or-nothing failure.
- `test_kb_lifecycle.py`: delete with cancel, orphan recovery, shutdown, lifespan wiring.
- `test_kb_events.py`: `kb_progress` / `kb_deleted` frames and `/health` responsiveness during indexing.
- `test_kb_search.py`: top-k search, not-ready and corrupt-index handling.
- `test_kb_api.py`: create/validate/list/get/delete/search/embedding endpoints and Russian messages.
- `test_kb_scoping.py`: authentication and per-user isolation (404 for foreign ids).
- `test_kb_real_pdfs.py`: real ФЗ-196 and КоАП РФ PDFs from `C:\Projects\RAG`; skipped when the files are absent. Pins the unique article counts (КоАП 907, ФЗ-196 34), asserts both strategies keep every chunk within 2000 characters and that the pipeline finishes in under 30 s.

### Folder upload and addition (Day 31)

- `test_kb_migration.py`: the start-up migration drops the sha256 unique constraint, chunks survive the table rebuild, it is idempotent and leaves no foreign key violations.
- `test_kb_add_job.py`: the add job indexes only new files; the base stays `ready` and byte-identical after a failed addition; replacement by source name; summary frame; orphan recovery of an interrupted addition.
- `test_kb_add_api.py`: `POST /api/v1/kb/{kb_id}/files` statuses (202, 404, 409, 413, 422), classification by name and SHA-256, claim exclusivity, limits leave nothing behind (no rows, staging removed).
- `test_kb_folder_ui.py`: source guard of the create form: folder input, pending list, taken/skipped report, limits read from the server, upload under source names.
- `test_kb_add_ui.py`: source guard of the add modal and the card states (adding progress, summary, failure line).
- `test_readme.py`: README sections, directories and the absence of secrets.
- `test_docs_kb_upload.py`: the documents carry no stale upload rule, list the new routes and messages, and state the limit number equal to `MAX_FILES`.
- New cases were added to `test_kb_api.py` (relative-path names, identical content under two names, limits), `test_kb_models.py`, `test_kb_storage.py` (staging, patched index swap), `test_kb_lifecycle.py` and `test_kb_scoping.py` (the add route for foreign bases).

### scripts/e2e_kb_folder_playwright.py (browser UAT, not part of pytest)
- Runs the same isolated copy as `e2e_kb_playwright.py` (UI :18000 / Agent :18001, scratch database and storage); never touches :8000/:8001 and stops only processes it started. Exit 0 all passed, 1 a check failed, 2 nothing ran because the isolated ports are busy, 3 part A passed and part B was skipped because LM Studio or the embedding model is unavailable, 4 Playwright missing.
- Part A (no LM Studio): the limits hint equals `GET /api/v1/kb/limits`; file and folder choice give one pending list with `docs/...` names and the taken/skipped report; choosing the folder again adds nothing; removal and re-adding; the multipart request carries `filename="docs/a/README.md"`; a folder over the limit turns the header red and disables submit.
- Part B (LM Studio and the nomic embedding model): the base created from the list stores both `docs/a/README.md` and `docs/b/README.md`; test search finds the marker; adding the changed folder gives `Добавлено: 1, заменено: 1, без изменений: 4` while the card stays `готово`; the old marker is no longer found.

### scripts/e2e_kb_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app at UI :18000 / Agent :18001 with a scratch database and KB storage
  against the real LM Studio and both real PDFs; never touches :8000/:8001 and stops only processes it
  started. Exit 0 all passed, 1 a check failed, 2 blocked (ports busy, LM Studio, embeddings model or
  PDFs missing), 4 Playwright missing. Takes about 20-30 minutes.
- Scenarios: empty panel, embeddings model hidden from the chat picker, embedding check (giga rejected,
  nomic dim 768), inline validation (choosing the same file twice keeps one list entry), both PDFs with both strategies, live progress with `/health` polling,
  test search cards, scan PDF and giga failures, Agent killed mid-job (restart message), delete during
  and after indexing (directory removed), second-user isolation.

## Chat RAG (Day 22)

- `test_rag.py`: retrieval wrapper, budget formula, block rendering and delimiter neutralizing, payload versioning and parsing, failure-to-warning mapping.
- `test_rag_turn.py`: `prepare_rag_turn` outcomes (off, no row, KB missing or foreign, not ready, context_full, retrieval failure) and that it never raises except on cancellation.
- `test_rag_ws.py`: WS turn with RAG: fragments only in the outbound last user message, raw question stored, `done.rag` payload, `Message.rag_sources` persisted, RAG failure never deletes the user message or breaks the answer.
- `test_rag_api.py`: GET/PUT `/chats/{id}/rag` (defaults, validation, 404 for foreign chat/KB, 422 for not-ready KB, `kb_id` null forces off) and the chunk snippet route (404 rules, `file` guard).
- `test_rag_static.py`: frontend source guard: RAG controls, labels, sources block and warnings use `textContent`/DOM builders only, no `innerHTML` with server text.
- `test_rag_fixture.py`: control question set of the evaluation (counts, expected sources, validity).
- `test_rag_eval.py`: `scripts/rag_eval.py` scoring (hit@k), run directory outputs, preflight failures, scratch DB never `app.db`.
- `test_rag_report.py`: Day 22 report generation from the evaluation outputs.

### scripts/e2e_rag_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app at UI :18000 / Agent :18001 with a scratch database and KB storage
  against the real LM Studio (nomic embedder and a chat model, `qwen/qwen3.5-9b` preferred) and the
  real ФЗ-196 PDF; never touches :8000/:8001. Exit 0 all passed, 1 a check failed, 2 blocked, 4
  Playwright missing. Takes a few minutes (reasoning-model answers dominate).
- Scenarios: KB reaches «готово»; new chat has a disabled switch and «без RAG»; attach KB, «с RAG»,
  K=5, badge, state survives reload; RAG answer with «с RAG · K=5», «Источники (N)», lazy snippet,
  stored question equals the raw one with no fragments block in history; «без RAG» answer without
  sources; KB deleted mid-chat: answer with the yellow warning, toast and «без RAG (сбой поиска)»,
  warning persists after reload; no console errors.
- The script switches strict mode off for its chat (PUT `/rag`) because it checks the Day 22 behaviour; strict-on behaviour is covered by `scripts/e2e_rag_cite_playwright.py`.

## Two-stage retrieval (Day 23)

- `test_rag_rank.py`: lexical score, cosine/lexical fusion, RRF, FTS query builder safety (no raw operators reach FTS5), rewrite validator rules, rerank output parser, threshold choice (`choose_threshold`).
- `test_rag_fts.py`: FTS5 search scoped to one KB, error mapping to a skip.
- `test_rag_llm.py`: reasoning-off request with one retry on HTTP 400/422, timeout becomes a reason code, a single batched rerank call.
- `test_rag_pipeline.py`: threshold on raw cosine, FTS exemption (article number or lexical overlap >= 0.5), merge of original and rewritten results, per-stage skip, all stages failing soft, no chunk text in the trace.
- `test_rag_turn.py` / `test_rag_ws.py` additions: payload v2, the `below_threshold` path (LLM still called with the no-fragments note), `over_budget` marking, client and model pass-through to the stages.
- `test_rag_api.py` additions: a partial PUT keeps the search settings, `threshold: null` resets, `candidate_k` clamped to `top_k`, 422 cases.
- `test_database.py` / `test_kb_lifecycle.py` additions: column migration idempotency, FTS5 triggers, backfill, FK cascade.
- `test_rag_search_static.py`: frontend source guard for the popover, details block and grey line (DOM builders only).
- `test_rag_calibration_fixture.py`: the calibration set is frozen and well formed.
- `test_rag_eval.py` additions: `calibrate` and `ablate` outputs and preflight; the scratch DB is never `app.db`.
- `test_rag_judge.py`: `scripts/rag_judge.py` verdict handling and idempotency; `test_rag_report_day23.py`: structure of `Day23_report.md`.

### Day 23 eval commands (not part of pytest)
- `python scripts/rag_eval.py build-kbs` reuses the Day 22 per-embedder evaluation KBs (separate DB, never `app.db`).
- `python scripts/rag_eval.py calibrate --kb LABEL=ID ...` measures the score distributions on `tests/fixtures/rag/calibration_set.json` and derives a threshold per embedder; the calibration fixture must be frozen (`status: frozen`) before a real run.
- `python scripts/rag_eval.py ablate --kb LABEL=ID ...` compares `baseline`, `threshold`, `lexical`, `llm_rerank`, `hybrid`, `rewrite` and `all`.
- `python scripts/rag_judge.py` fills the judge column of the answers table with a DeepSeek verdict.
- Outputs land in `eval_out/day23/`; the report is `Day23_report.md`.

### scripts/e2e_rag_search_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app at UI :18000 / Agent :18001 with a scratch database and KB storage against the real LM Studio (nomic embedder and a chat model, `qwen/qwen3.5-9b` preferred) and the real ФЗ-196 PDF; never touches :8000/:8001. Exit codes as for `e2e_rag_playwright.py`.
- Scenarios: S1 «Поиск ⚙» only with RAG on; S2 popover defaults (K=20, threshold and note from the API, switches off); S3 Escape and outside click; S4 K and lexical persist across reload; S5 threshold override and «сбросить»; S6 «Детали поиска» table with status chips and no chunk text; S7 details survive reload; S8 hybrid adds the FTS column; S9 threshold 0.99 gives the grey line, no sources, no toast; S10 rewrite and LLM rerank ran or show a «↷» skip line; S11 RAG off hides the button.
- The script switches strict mode off for its chat in the setup because it checks the Day 23 behaviour; strict-on behaviour is covered by `scripts/e2e_rag_cite_playwright.py`.

## Citations and gate (Day 24)

- `test_rag_cite.py`: the pure module `agent/rag_cite.py`: normalization, exact and fuzzy quote matching, tail parsing, re-attachment, invalid references, auto quotes, «не знаю» detection, the code-built refusal reply, payload fields.
- `test_rag_turn.py` / `test_rag_ws.py` strict cases: the gate on `below_threshold` and on no candidates (including the assertion that the gate makes no streaming completion request and sends one `token` frame then `done`), strict instruction in the fragments block, `finalize_rag_turn` before persist (clean `Message.content`, payload v3), strict off keeps the Phase 15 turn, fail-soft on a citation error.
- `test_rag_cite_static.py`: frontend source guard for the quotes block, chips, amber and grey lines and the strict switch (DOM builders and `textContent` only).
- `test_rag_eval_cite.py`: `scripts/rag_eval.py cite` run structure, output files and preflight; the scratch DB is never `app.db`.
- `test_rag_judge.py` faithfulness cases: the `faithfulness` rubric skips rows without quotes and keeps relevance results.
- `test_rag_report_day24.py`: structure of `Day24_report.md`.

### Day 24 eval commands (not part of pytest)
- `python scripts/rag_eval.py cite` runs the control questions in `strict`, `strict_off` and `strict_baseline` modes and writes `eval_out/day24/`; `--render-only` rebuilds the tables from the stored raw results without calling the models.
- `python scripts/rag_judge.py --rubric faithfulness --answers eval_out/day24/answers.csv --meta eval_out/day24/judge_meta.json` fills the judge column with a DeepSeek faithfulness verdict.
- The report is `Day24_report.md`.

### scripts/e2e_rag_cite_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app at UI :18000 / Agent :18001 with a scratch database and KB storage against the real LM Studio (nomic embedder and a chat model) and the real ФЗ-196 PDF; never touches :8000/:8001. Exit codes as for `e2e_rag_playwright.py` (0 passed, 1 a check failed, 2 blocked by preflight, 4 Playwright missing). The nomic embedder has no calibrated threshold, so the gate is forced with a threshold of 0.99.
- Scenarios: S1 setup (KB «готово», chat with the KB, RAG on); S2 the strict switch is first in «Поиск ⚙», on by default, no indigo border; S3 strict answer without the «Цитаты:» tail, open «Цитаты (N)» with status chips, collapsed «Источники» and «Детали поиска», in that order (an empty or «не знаю» model answer is retried up to three times because the model is not deterministic); S4 stored payload v3 with quote states and files matching the source of their rank; S5 «цитируется» on the first source row (skipped when nothing is cited); S6 gate at threshold 0.99: «Не знаю:» with «Уточните», grey line, no quotes or sources, `gated` and `below_threshold` in the payload; S7 the same blocks after a reload; S8 strict off: model text, `strict: false`, no quotes block, then threshold reset and strict back on; S9 no page errors and no script or img in quote rows.

## Task memory and dialog scenarios (Day 25)

- `test_task_memory.py`: the pure document logic: sticky goal, an explicit goal change replaces the goal, growing ids that are never reused, stem-Jaccard deduplication, list caps, clipping, the user-stated filter (items and numbers must come from the user's message), delta parsing, snapshot and prompt lines.
- `test_task_memory_extract.py`: `complete_stage`, the extraction messages (data tags neutralised) and call parameters, `update_task_memory` staging a row without committing it, fail-soft results (`failed: true`, previous document kept), timeout, cancellation, `restore_from_path`.
- `test_task_memory_ws.py`: the WebSocket turn: the first turn updates memory and `done.rag.task_memory` carries the snapshot; the second turn condenses and marks only new items; extraction failure keeps the turn; a gated turn still updates memory without an answer request; a non-RAG chat makes no extraction call; the eval-only flag off makes no extraction or condensing call; an LLM error turn makes no extraction call.
- `test_task_memory_prompt.py`: the labelled memory lines between the other prompt blocks; empty document, RAG off, flag off and a load failure add nothing.
- `test_task_memory_api.py`: `task_state` in `GET /memory` (null without RAG), the three `task-memory` routes with 404, 409, 403 and 415, per-user isolation, branch restore from an older or newer snapshot, branch without a snapshot clears memory.
- `test_task_memory_ui.py`: frontend source guard for the sidebar block, the goal form, `×` without a confirm and «Сбросить» with one, memory text never reaching HTML sinks, the `done` frame refresh, branch reloads, the per-message block and the «Уточнён:» label.
- `test_rag_history.py`: history condensing: message building, output validation, both queries searched and merged, the gate on the merged result, fallbacks to the raw question, `load_history_pairs` on the active branch, `history_turns` 0 and the flag off.
- `test_dialog_fixture.py`: the frozen two-scenario fixture: counts, turn ids, kinds, goal checks and the canonical repeat follow-up.
- `test_dialog_eval.py`: the dialog runner: per-turn checks, memory and goal proxies, isolation guards (never the live ports or `app.db`), baseline run with task memory off, argument parsing and exit code 2 cases.
- `test_dialog_report.py`: the rendered transcripts, side-by-side follow-ups, summary, `answers.csv` columns and judge columns surviving `--render-only`.
- `test_rag_report_day25.py`: structure and consistency of `Day25_report.md` against the run outputs.
- `test_rag_judge.py` goal-adherence cases: the `goal_adherence` rubric is registered, judges only answered turns and keeps other rubric results.

### Day 25 eval commands (not part of pytest)
- `python scripts/rag_eval.py dialog --check` starts the isolated app, logs in and verifies the knowledge base without sending a chat message.
- `python scripts/rag_eval.py dialog --runs main --scenarios A` runs one scenario with task memory on; `python scripts/rag_eval.py dialog --runs baseline --scenarios A` is the same scenario with task memory off (`TASK_MEMORY_ENABLED=false` in the copy); without `--runs` and `--scenarios` both runs and both scenarios are executed. `--force` repeats scenarios whose raw file exists.
- `python scripts/rag_eval.py dialog --render-only` rebuilds all outputs in `eval_out/day25/` from the stored raw files without calling any model.
- `python scripts/rag_judge.py --rubric goal_adherence --answers eval_out/day25/answers.csv --meta eval_out/day25/judge_meta.json` fills the judge column with a DeepSeek verdict on whether each answer follows the goal and the task memory; `--check` verifies the key and the model first; a reasoning judge model needs `--max-tokens` of 1500 or more.
- Exit codes of both scripts: 0 ok, 1 a run error, 2 preflight failure (LM Studio, models, key, fixture or source database missing).
- Needs LM Studio with `qwen/qwen3.5-9b` and `text-embedding-bge-m3` loaded. The runs always use an isolated copy of the app on ports 18000/18001 and a copy of `eval_out/day23/eval.db`; the live app on 8000/8001 and `app.db` are never touched.
- The report is `Day25_report.md`.

### scripts/e2e_rag_dialog_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app at UI :18000 / Agent :18001 with a scratch database and KB storage against the real LM Studio (nomic embedder and a chat model) and the ФЗ-196 PDF from `C:\Projects\RAG` (the traffic rules `ПДД.pdf` from the same folder when the ФЗ-196 file is absent); never touches :8000/:8001. Screenshots go to `eval_out/day25/screens/`. Exit codes as for `e2e_rag_cite_playwright.py` (0 passed, 1 a check failed, 2 blocked by preflight, 4 Playwright missing). Do not run it while a `rag_eval.py dialog` run uses the same ports.
- Scenarios: S1 setup and the empty sidebar block (hidden before RAG, three labels and «Пока пусто» after); S2 first turn: sources, the sidebar goal without a reload, API goal, payload v4, a collapsed per-message «Память задачи (N)» block with «новое» (one retry in a fresh chat when the extraction was unusable); S3 elliptic follow-up: `history_pairs` at least 1 and «Уточнён:» with «история» (or the skipped «Уточнение запроса» row), history kept; S4 goal edit (Escape restores, «Сохранить» stores); S5 delete an item with «×» without a dialog; S6 branch restore through «↩ отсюда»; S7 «Сбросить» with the confirm text, accept clears, dismiss keeps; S8 «Ходов истории»: default 3, 0 kept, 25 clamped to 10; S9 a chat without RAG shows no task memory; S10 reload, no page errors, no script or img inside the blocks.

## Long-term memory editing (Day 21)

### test_memory.py (CRUD helpers)
- owner-only get; update keeps `created_at` and refreshes `updated_at`
- rename keeps one row; the same key is not a conflict
- rename conflict and the commit race both raise `MemoryKeyConflictError` and change nothing
- another user gets None / False; delete removes only the own row

### test_memory_api.py (REST)
- 401 without a session
- value-only and key+value updates; key stripped, value stored verbatim
- 422 table: empty body, both null, blank key, whitespace-only value, over-long key / value; maximum lengths accepted
- 409 on a duplicate key with both rows unchanged; own current key accepted
- 404 for a foreign and an unknown id with the same detail; delete 204 then 404
- working memory is never reachable through the long-term routes
- 403 for a foreign Origin; 415 for a non-JSON PUT

### test_context_engine_memory.py (prompt effect)
- the prompt shows the edited key/value and not the old one
- the long-term label disappears after the last entry is deleted

### test_memory_panel_ui.py (frontend source guard)
- button labels; no HTML insertion in the memory region; user-scoped routes
- `confirm()` before the DELETE request; only the long-term list is editable
- the form is seeded from the full value; no modal and no key handler

Rows are seeded with `memory.save_long_term_memory`; cross-user cases use `second_authenticated_client`.

## LAN exposure (Day 30)

Tests never bind real LAN sockets. LAN requests are simulated with `Host` and `Origin` header overrides. `tests/conftest.py` pins `BIND_HOST=127.0.0.1` so a developer `.env` with LAN mode does not change results; mode-dependent tests set `settings.BIND_HOST` with `monkeypatch`.

The scenarios of `tests/test_ws_origin_validation.py` and `tests/test_websocket_cors.py` changed: `null` and Agent-port origins are now rejections.

### test_bind_host.py
- loopback values (`127.0.0.1`, `localhost`, empty) normalise; `0.0.0.0` enables LAN mode
- any other value is refused; the message is ASCII and explains the options
- importing `shared.config` or `run` with an invalid host exits with code 2 (subprocess checks) before startup work
- the `settings` singleton reflects the environment

### test_supervisor_host.py
- the `--host` argument: default is loopback, a given host is passed to the Agent launch
- health probes stay on `127.0.0.1`

### test_origins.py
- the Origin rule table: host match on the UI port, loopback origins always accepted, no mode branch
- REST CORS: LAN origin reflected on preflight and GET (also with a case-different machine name); foreign origin, missing Origin and `null` get no CORS header
- REST mutations: host-matched Origin accepted, missing Origin allowed, non-matching rejected
- `/ws/events`: LAN pair accepted; `null` and foreign origins rejected

### test_network.py
- address filtering: interfaces that are down, loopback, non-IPv4 and those without stats are excluded; VPN and link-local are included
- duplicates listed once; discovery failure returns an empty list; hostname errors omit the name; URL order

### test_run_banner.py
- golden loopback output equality
- LAN banner layout and bootstrap block ordering
- the UI supervisor uses `BIND_HOST`

### test_network_info.py
- `GET /api/v1/network` returns 401 without a session
- loopback mode returns empty `addresses`; LAN mode matches `shared.network.reachable_urls`
- foreign origin gets no CORS header; the route is registered

### test_network_ui.py (frontend source guard)
- the «Сеть» section is read-only and placed last; no HTML insertion in its region
- the loader uses the endpoint and handles errors; both modes are rendered
- the clipboard helper has an HTTP fallback and all copy callers use it
- Russian copy is present; host derivation is unchanged

### test_port_literals.py
- no default-port literals in application code; e2e scripts do not patch agent state

### scripts/e2e_lan_playwright.py (browser UAT, not part of pytest)
- Runs a temporary copy of the app with `BIND_HOST=0.0.0.0` at UI :18000 / Agent :18001, a scratch database, a stub OpenAI-compatible provider on :18766 and a foreign-page server on :18770. Command: `python scripts/e2e_lan_playwright.py`. It never touches :8000/:8001, the repository `.env` or the firewall and stops only the processes it started. Windows may show a Security Alert when the copy binds `0.0.0.0`; the checks work without answering it because the connections come from the same machine.
- Exit 0 all passed, 1 a check failed, 2 blocked (a port busy or no non-loopback IPv4 address), 4 Playwright missing. The first LAN address whose Agent `/health` answers is used as `LAN_IP`.
- Network checks (httpx): 1 startup banner and LAN URL; 2 both sockets listen on `0.0.0.0`; 3 UI root and `/health` on the LAN address; 4 preflight reflects the LAN origin, not a foreign one; 5 machine-name origin reflected; 6 login and `GET /api/v1/network` in `lan` mode (401 without cookie); 7 own-origin mutation succeeds, foreign and Agent-port origins get 403.
- Browser scenarios (Chromium opened on the LAN address only): L1 login in a non-secure context; L2 «Сеть» list equals the API list; L3 copying an address over plain HTTP; L4 streamed stub reply over `ws://<LAN_IP>:18001/ws/chat/...`; L5 a foreign page on another port with the session cookie cannot read or mutate over REST and cannot open `/ws/events` or `/ws/chat` (Agent log has `ws_origin_rejected`); L6 a page on another host cannot open the events socket; L7 no page errors.

#### Manual check from a second device
Automation cannot prove reachability from another machine or the Windows Firewall rule (tracked as VER-05 for the live Day 30 report). On the real app: set `BIND_HOST=0.0.0.0` in `.env` and restart; confirm the warning block and address list in the console; open ports 8000 and 8001 for the Private profile as in `docs/USER_GUIDE.md` ("LAN mode"); from a second device open one printed `http://<address>:8000`, log in, send a message and see the reply stream; open Settings -> «Сеть» and tap an address («Скопировано: ...»); finally restore `BIND_HOST=127.0.0.1` and confirm the old single-line banner.

## API keys and HTTP chat (Day 30)

Quick command: `pytest tests/test_api_keys.py tests/test_bearer_auth.py tests/test_http_chat.py tests/test_http_chat_rag.py -q`. The live check of the HTTP endpoint from a second machine belongs to the Day 30 report of Phase 20 (VER-05).

### test_api_keys.py
- the key is returned once and only its sha256 hash and prefix are stored
- name validation (blank, too long, trimmed); cap of 20 keys per user (409)
- revoke deletes the row; a foreign or missing key is 404
- key routes, add-user and logout answer 403 to a key (cookie-only)
- a body-less `DELETE` without `Content-Type` returns 204, while a `POST` without a JSON content type returns 415

### test_bearer_auth.py
- a key authenticates as its owner; sample of routes including `/api/v1/kb` and `/api/v1/mcp/servers`
- an MCP server toggle with a key; another user's objects are 404
- 401 for missing-token, empty, unknown and revoked keys, with `WWW-Authenticate: Bearer`
- no cookie fallback when a Bearer header is present; non-Bearer schemes are ignored
- `last_used_at` is refreshed at most once per 60 seconds
- the WebSocket endpoints reject a key

### test_ws_turn_failures.py
- the chat leaf is restored after a context overflow and after LLM errors
- the next turn continues the same branch (checked through the tree)
- `cause` values `unreachable`, `timeout`, `upstream` and `provider_kind` in `LLM_ERROR`
- a failed first turn leaves an empty chat; edge cases of the helper
- the WebSocket handler delegates to the lock-free `_run_chat_turn`, which also runs with a `RecordingSink`

### test_http_chat.py
- the success body, the tree and the current leaf after a turn; chat settings and compression apply
- authentication (key and cookie), 404 for a foreign chat, request validation
- `409 CHAT_BUSY` with a pre-acquired lock and nothing stored; the lock is released after a turn
- `413 CONTEXT_OVERFLOW` with `suggested_strategy` and the leaf restored
- mapping of 503 (`LM_STUDIO_UNAVAILABLE`, `LLM_UNREACHABLE`, `PROVIDER_UNAVAILABLE`) and 502 (`LLM_ERROR`)
- the `chat_updated` event on `/ws/events`; the turn completes after a client disconnect
- background actions (title, facts) run; the endpoint has no model management

### test_http_chat_rag.py
- `kb_id` as an id, `null` and omitted; the other saved search settings stay unchanged
- a foreign or not-ready knowledge base is rejected; the knowledge base list is readable with a key
- tools are available over HTTP

### test_api_keys_ui.py (frontend source guard)
- the «API ключи» section, the one-time key modal, the curl example and the `chat_updated` handling in `ui/static`

### test_docs_day30.py (documentation source guard)
- the Day 30 sections of `docs/API_SPEC.md` in order; the curl scenario strings; the error codes; the status table rows
- every route of the curl scenario exists in the Agent app
- this testing guide lists every Day 30 test file; the architecture and user guide sections exist
- no document uses the early placeholder path of the HTTP chat endpoint

### scripts/e2e_api_keys_playwright.py (browser UAT, not part of pytest)
- Starts an isolated copy of the app at UI :18000 / Agent :18001 with a scratch database, a stub OpenAI-compatible provider and a closed LM Studio port. Command: `python scripts/e2e_api_keys_playwright.py`. It never touches :8000/:8001, the repository `.env` or `app.db`.
- Exit 0 all checks passed, 1 a check failed, 2 blocked (a port is busy), 4 Playwright missing. A run that ends with exit 2 or 4 is BLOCKED: it is not recorded as passed and the live browser refresh check stays an open manual UAT item.
- Checks: K1-K7 (the Settings section, name validation, the one-time key modal and copy, closing rules, the key row, the last-used date), H1-H5 (a key alone authenticates REST routes, key creation with a key is 403, a chat turn over HTTP, the tree, a wrong key is 401), L1-L3 (an HTTP exchange appears in the open browser without a reload and leaves the input enabled), R1-R2 (revoke with confirm; a revoked key gets 401), Z (no uncaught page errors).

## Limits (Day 30)

Quick command: `pytest tests/test_limits_config.py tests/test_limits_core.py tests/test_rate_limit.py tests/test_generation_queue.py tests/test_login_lockout.py tests/test_limits_ui.py tests/test_verify_service.py -q`. No test uses a real network: `verify_service.py` is tested against a local stub server on loopback, and the limits are tested with an in-process client and a replaceable clock.

### Coverage map for Day 30

| Area | Test files | Documented in |
|------|-----------|---------------|
| Bind host | `test_bind_host.py`, `test_supervisor_host.py`, `test_run_banner.py` | "LAN exposure (Day 30)" |
| Origin | `test_origins.py`, `test_ws_origin_validation.py`, `test_network_info.py` | "LAN exposure (Day 30)" |
| API keys | `test_api_keys.py`, `test_bearer_auth.py`, `test_api_keys_ui.py` | "API keys and HTTP chat (Day 30)" |
| HTTP chat | `test_http_chat.py`, `test_http_chat_rag.py`, `test_ws_turn_failures.py` | "API keys and HTTP chat (Day 30)" |
| Limits | `test_limits_config.py`, `test_limits_core.py`, `test_rate_limit.py`, `test_generation_queue.py`, `test_login_lockout.py`, `test_limits_ui.py`, `test_verify_service.py` | this section |

Every file in the table exists in `tests/`; `test_docs_day30.py` fails when a file named in the three Day 30 sections is missing.

### test_limits_config.py
- defaults of the four settings (30, 5, 300, 2); `0` switches a limit off
- negative and non-numeric values are refused with an ASCII message that names the setting; `LOGIN_LOCKOUT_SECONDS=0` is refused
- importing `shared.config` with a bad value exits with code 2 (subprocess); valid values are read from the environment; the bind-host failure still exits 2

### test_limits_core.py
- message window: blocks after the limit, reports the exact wait in whole seconds, frees after a minute, a refused call records nothing, chats are independent, limit `0` is off, deleting a chat empties its window
- login throttle: the third failure blocks, the block time shrinks and expires, a success resets the count, pairs are independent, threshold `0` never blocks, slow guessing still reaches the block, the count restarts after an expired block, eviction keeps the table within `MAX_ENTRIES` and spares blocked keys
- generation gate: cap 1 serialises in order, cap 0 is uncapped, a cancelled waiter leaves the gate usable, a failing body frees its slot, a changed cap takes effect, new event loops get a fresh semaphore; `limits_in_force` reports the settings

### test_rate_limit.py
- the HTTP 429 contract: body, `Retry-After`, nothing stored, no model called
- what takes a slot (an accepted attempt, a context overflow, a model error) and what does not (404, 409, 429); 409 is answered before 429
- the window is shared between WebSocket and HTTP in both directions and both report the same seconds
- the window expires, the limit `0` switches it off, the default is 30, the limit is per chat

### test_generation_queue.py
- excess turns wait and all complete; the `queued` frame goes only to waiters; `queue_wait_seconds` is reported in the `done` frame and in the HTTP body
- cap 0 means no cap; cap 2 runs two and queues two; a turn waiting for the chat lock holds no slot
- a failing turn frees its slot; a cancelled waiter does not block the queue; the wrapper has no queue timeout; `LLM_TIMEOUT` counts only after the queue
- `_QueueStatsSink` passes frames through without mutating them; background work (titles, facts, scheduler) is outside the cap

### test_login_lockout.py
- threshold and block, the 429 contract (`LOGIN_BLOCKED`, `Retry-After`, `retry_after`), a correct password refused during the block, no username enumeration
- counted per username and per client address; a forwarded header is ignored; success resets; the block expires and its remaining time shrinks; threshold `0` disables
- nothing is persisted; CORS exposes `Retry-After`; `limits_in_force` is logged at start; no route returns the limits; slow guessing is still blocked

### test_limits_ui.py (frontend source guard)
- the rate-limit copy «Слишком много сообщений. Повторите через N с», the handling restores the typed text without locking the input, and the branch is wired before the generic fallback
- the queue caption «В очереди, ожидание свободного места…» and its wiring; no limit values on screen; no stop control
- the login lockout copy and wiring; the error is an alert; the inline script is balanced and the port constant is untouched

### test_verify_service.py
- the script runs against a local stub server: both output files are written; reachability and authentication; an unreachable server, a refused key and no usable model exit 2
- model choice, parallel run, overflow demonstration, rate-limit modes `chat` and `fast`, the expected-limit note; a missed expectation exits 1
- cleanup deletes only the script's own chats (also after a failed run), `--keep` leaves them
- key hygiene (the key is absent from output and files), no proxy use, standard library only, Python 3.11 floor

### Changed tests
- `test_ws_security.py`: the rate-limit test uses the configurable shared limit and expects a `RATE_LIMITED` frame with `retry_after` instead of the old fixed 10 messages per minute
- `test_http_chat.py`: the HTTP stats carry `queue_wait_seconds` (0.0) while `GET /api/v1/chats/{id}/stats` does not
- `test_scoping.py`: `/health` stays open and `/debug/routes` needs a session cookie or an API key (401 without credentials); it used to assert the route was open
- `test_network_info.py`: the route check reads `/debug/routes` with an authenticated client because the route is now guarded
- `test_supervisor_host.py`: the readiness probe accepts only the 401 of the guarded route and start waits for it
- `test_supervisor.py`: a real-process test that start reaches ready without a forced restart

### scripts/e2e_limits_playwright.py (isolated copy, not part of pytest)
- Starts a temporary copy of the app at UI :18000 / Agent :18001 with a scratch database, low demo limits and a deliberately slow stub model. Command: `python scripts/e2e_limits_playwright.py`. It never touches :8000/:8001, the repository `.env` or the firewall, stops only the processes it started, and never writes into `eval_out/day30/`.
- Exit 0 all passed, 1 a check failed, 2 blocked (a needed port busy), 4 Playwright missing.
- Group S (network): supervisor readiness without a forced restart, the `limits_in_force` log line, `/health` open and `/debug/routes` guarded, a key that opens `/debug/routes`.
- Group V: the real `verify_service.py` run as a separate process against the copy.
- Group B (browser): login lockout message, the rate-limit toast with the text kept in the input, the queue caption, no limit values on screen.

### Live run from a second machine (manual)
Automation cannot prove reachability from another machine, so the live run is manual. Follow "Checking the service from another machine" in `docs/USER_GUIDE.md`: LAN mode on, a low demo limit, an API key, then `python3 verify_service.py --url http://<lan-ip>:8001 --n 6` on the other machine. The result files go to `eval_out/day30/` and are summarised in `Day30_report.md`.

## Project MCP servers (Day 31)

### test_mcp_project_git_core.py
- command line parsing: defaults, explicit values, rejected values
- the git environment drops inherited `GIT_*` variables; the base argument list is fixed
- command runner: CRLF normalised, stderr kept apart, output capped, process tree killed on timeout
- start-up failures: missing root, git not installed, folder that is not a repository
- the module source is clean of forbidden calls
- path gate: accepted variants, traversal, Windows escapes, leading dash, colon forms, control characters, links leaving the root
- ref syntax and resolution (`HEAD`, branch, ranges, unknown refs, option-shaped refs never start git)
- output fitting: unchanged when it fits, marker with exact numbers, lower bound, truncated diff composition

### test_mcp_project_git.py
- eight tools by default (five git tools plus read_file, search_text, find_usages); the git descriptions say the working copy is local
- each tool on a temporary repository: branch (main, checkout, detached, no commits), status (dirty, clean, Cyrillic names, empty repository), diff (unstaged, staged, against HEAD, path, range, truncation, at most two commands), tracked files (all, counts, path, glob not expanded, truncation table), recent commits (default, count clamp, ref, path, no history)
- bad arguments and option smuggling are refused without starting git
- over real stdio: live branch, path outside the root, dash ref, unknown ref, wrong argument type, truncated diff, root from the working directory, `GIT_DIR` ignored
- start-up failures end with `PROCESS_EXITED`
- chat dispatch of `mcp__local-git__git_current_branch`; a long listing stays below the app cap

### test_mcp_project_files.py
- read tools on a temporary repository: windowed `read_file`, `search_text` hits, `find_usages` kinds, git visibility (ignored files hidden)
- read-side containment: `.env*` and `.git` refused by `read_file` and hidden from `search_text`/`find_usages`; symlinks to the ignored `.env` or outside the root never leak content (real symlinks skip without the privilege; a monkeypatched `os.path.realpath` simulates them)
- the tool list is 8 without `--allow-write` and 10 with it
- write gate: refusals for paths outside the root, `.git`, `.env*`, ignored paths, device names, directories, binary files and content above 200000 bytes
- `write_file` and `edit_file` results: `Created`/`Updated` first line with `(+A -R)`, single-occurrence rule, CRLF preserved

### test_files_task.py
- root checks: not a work tree, dirty tree, own repository refused, clean tree accepted
- patch collection includes a new file and applies cleanly; patch statistics
- full run statuses: done, blocked, connection error, empty content

### test_files_eval.py
- the scenario table and the automatic checks of the four scenarios
- clone reset refuses wrong paths and restores a clean status
- the "not run" record and the summary

### test_diff_card_ui.py
- the diff functions exist in the frontend and never use HTML sinks
- the diff block uses `textContent`, accessibility attributes and line classes
- the tool-call card uses the diff branch and the summary suffix contract

### scripts/e2e_project_files_playwright.py (browser UAT, not part of pytest)
- Runs an isolated copy of the app at UI :18000 / Agent :18001 with a scratch clone as the write root; it never touches :8000/:8001.
- The model part needs LM Studio and is not run without it. Exit 0 pass, 1 fail, 2 not run, 4 Playwright missing.

### scripts/files_task.py and scripts/files_eval.py
- `files_task.py` runs one goal against a clean clone through the HTTP chat endpoint and writes `run.json`, `run.patch`, `answer.md`; covered by `test_files_task.py`.
- `files_eval.py` (`check`, `run`, `summary`) runs the four report scenarios into `eval_out/day34/`; covered by `test_files_eval.py`.

### test_mcp_real_github.py
- the read-only toolset through the app connect: read tools present, write tools absent
- a real read call (`list_branches`) succeeds, which a handshake alone cannot prove for a bad token
- the token value is never returned by the API; only `env_keys` is
- the two real-binary tests are skipped unless `github-mcp-server.exe` exists (path overridable with `GITHUB_MCP_SERVER_EXE`) and `GITHUB_PERSONAL_ACCESS_TOKEN` is set in the environment of the pytest process; a skipped test is not a passed check

### test_docs_mcp_servers.py
- the user guide section exists before `## Tips`, names every registered tool and gives the form values for both servers
- documented limits equal the module constants and the app settings; `go install` is not documented
- the architecture, API and testing documents contain the new subsections
- no token-shaped string in `docs/`, `mcp_servers/`, `scripts/`, the MCP tests and the phase directory

### scripts/e2e_mcp_git_playwright.py (browser UAT, not part of pytest)
- Starts an isolated copy of the app at UI :18000 / Agent :18001 with a real LM Studio model and a temporary repository with branches `alpha` and `beta`. It never touches :8000/:8001.
- Exit 0 all checks passed, 1 a check failed, 2 blocked (recorded as "not run"), 4 Playwright missing.

## /help command (Day 31)

### Coverage map for Day 31

| Area | Test files |
|------|-----------|
| Folder and accumulated upload | `test_kb_folder_ui.py`, `test_kb_add_ui.py` |
| Addition to an existing base and replacement of an updated file | `test_kb_add_job.py`, `test_kb_add_api.py` |
| Git MCP server: tools, root confinement, bad arguments | `test_mcp_project_git_core.py`, `test_mcp_project_git.py` |
| `/help` over WebSocket | `test_help_ws.py` |
| `/help` over HTTP | `test_help_http.py` |

### test_help_command.py
- recognition: question extracted, empty question, `/helpme` and other text rejected, raw payload check
- the built-in tool groups match the tool registry
- notices: usage for an empty question; no base for no row, no mark, a foreign base, a base that is not ready, no owner; no notice for a ready base, also while files are being added
- default tools: only `local-git`; a missing default server gives a warning; an enabled built-in group exposes only that group
- stored server choice: chosen servers used, missing ones flagged, a disabled server warns without raising, a toolset failure falls back to a warning, `only_server_ids` filters before connecting

### test_help_api.py
- defaults of `GET /api/v1/help/config`, default server is `local-git`
- PUT: marking a ready base, `null` clears and omission keeps, a foreign base is 404, a base that is not ready is 422, threshold `null` versus unchanged, `candidate_k` raised to `top_k`, validation 422, flags, MCP server choice, another user's server is 404, a deleted server is reported missing
- 401 without a session, a foreign origin is rejected, another user does not see the mark
- deleting the marked base clears the mark; the chat's RAG config is untouched

### test_help_turn.py
- the fragments instruction override and the exact wording of the prompts
- the outbound list has no chat history, with and without tools
- retrieval: fragments kept, below the threshold the model is still called (no gate), an embedder failure continues, the chat's RAG config is ignored
- quotes: verified model quotes kept, a git-only answer drops code-picked quotes, a failed tool call keeps strict behaviour

### test_help_ws.py
- usage and no-base notices store nothing and call no model; the not-ready notice names the base
- a real turn answers with sources and the label; the outbound request holds only the `/help` prompt, fragments and question
- `ChatRagConfig` is neither read nor changed; nothing found still calls the model
- default tools are only `local-git`; a missing server gives a warning; the tool call frame and trace; a built-in write tool is rejected by default; the task group offers task tools only
- side jobs do not run; the tree route returns label, sources and quotes; the next ordinary message sees the pair
- `/helpme` is an ordinary turn; notices take no rate slot, a real turn takes one, an invalid payload takes none; an upstream failure discards the user message

### test_help_http.py
- usage and no-base notice bodies (`200`, `stored: false`); the success body with `help`
- the tool call is reported; a missing default server warns
- `kb_id` is ignored for `/help` (an id, a missing id and `null`); the chat config is kept
- notices take no rate slot, need no chat lock and emit no `chat_updated`; a real turn takes a rate slot; no credentials is 401; an ordinary body has `help: null`

### test_help_ui.py (frontend source guard)
- the Settings section, its element ids and position; numeric ranges; stage labels equal the chat popover labels
- the notice is transient and never enters the message state; builders use `textContent` only
- label and knowledge base badge markers; sending does not inspect the command; the required Russian strings

### test_help_eval.py
- control set validation, frozen set loading, secret scrubbing, git snapshot, expected answers, question records
- threshold decision rule, model choice, the isolation guard refuses live ports and `app.db`, the default threshold is read from the source

### test_docs_help.py (documentation source guard)
- heading order of the `/help` sections, required strings, the default threshold and the usage text equal the constants in code
- the Phase 21 and 22 descriptions stay; every test file named here exists; no key-shaped string in `docs/`

### scripts/e2e_help_playwright.py (isolated copy, not part of pytest)
- Starts an isolated copy of the app at UI :18000 / Agent :18001 with a scratch database, a real LM Studio model and a temporary repository; it never touches :8000/:8001. Command: `python scripts/e2e_help_playwright.py`.
- Exit 0 all checks passed, 1 a check failed, 2 blocked (recorded as `not run`, never as passed), 4 Playwright missing. The outcome is recorded in `.planning/phases/23-help-command-verification-and-report-day-31/23-CHECKS.md`.

### scripts/help_eval.py (control run, not part of pytest)
- `python scripts/help_eval.py check|measure|spike|run` works on an isolated copy at :18000 / :18001 with a scratch database and never opens `app.db` for writing. `check` prints what can run; `measure` records the best score per control question; `spike` tries the combined prompt; `run` asks the frozen control set of ten questions on the local and the DeepSeek model.
- Exit 0 done, 1 aborted, 2 blocked by the preflight. A model that could not run is recorded as `not run`.
- Results are written to `eval_out/day31/` (`threshold.json`, `spike.json`, `CALIBRATION.md`, the run records).

## /support command (Day 33)

### Coverage map for Day 33

| Area | Test files |
|------|-----------|
| Shared command-turn mechanism (`/help` and `/support`) | `test_command_turn.py`, `test_help_command.py`, `test_help_turn.py` |
| Support MCP server | `test_mcp_support_desk.py` |
| Grammar, notices, history, prompts | `test_support_command.py` |
| Direct lookup, data fence, ticket-aware query | `test_support_lookup.py` |
| Settings routes | `test_support_api.py` |
| `/support` over WebSocket and HTTP | `test_support_ws.py`, `test_support_http.py` |
| Settings section of the frontend | `test_support_ui.py` |
| FAQ and documentation guards | `test_docs_faq.py`, `test_docs_support.py` |
| Retrieval probe and end-to-end script helpers | `test_support_probe.py`, `test_e2e_support_script.py` |

### test_command_turn.py
- the notice frame carries the `command` key; usage and no-base notices resolve for a spec
- tool selection warns when the default server is missing; a toolset builder failure becomes a warning
- the outbound message list with history; the context block comes after the fragments block, also below the threshold; the retrieval query override is used

### test_mcp_support_desk.py
- `get_ticket`: found, id normalisation (`T-1001`, `t1001`, `#1001`), not found and malformed ids
- `get_user` by login and by client id, unknown user; `list_user_tickets` with and without a status, unknown status
- `search_tickets`: hits and filters, zero hits is not an empty reply, text length 1 to 100; echoed input is truncated
- the size cap truncates with a marker; an edit of the data file is picked up and invalid JSON recovers; a missing file after start
- startup refusals: missing file, invalid JSON, oversized file, missing `--data`; stdio round trip; exactly four read tools

### test_support_command.py
- grammar: plain question, ticket forms, user, an invalid identifier stays in the question, identifier only right after the command, case-insensitive command, other text rejected, empty question
- label; the default threshold equals `DEFAULT_SUPPORT_THRESHOLD`
- notices: usage, no base, base not ready, none for a ready base
- settings defaults; default tools are only the `support` server, a missing server warns
- history: only `/support` pairs, pairs without the payload skipped, limits of pairs and answer length, token budget, no parent
- outbound messages have no profile; payload extra; retrieval with fragments and context; below the threshold the model is still called with a note

### test_support_lookup.py
- the fence wraps data, neutralises inner tags and caps long data
- the ticket-aware query adds subject and error lines, is capped, and can be switched off
- no identifier makes no call; ticket and user found; ticket and user not found
- server not added, disabled, cannot start, dispatch failure each give a warning and no exception
- hostile ticket text stays inside the fence; server rows are per user

### test_support_api.py
- defaults of `GET /api/v1/support/config`, default server is `support`; the `/help` base fields follow the `/help` mark and another user's base is hidden
- PUT: partial update keeps other fields, threshold `null` versus unchanged, `candidate_k` raised to `top_k`, validation 422, server choice, foreign or unknown server is 404, a deleted server is reported missing
- `Content-Type` is required, a cross-origin PUT is rejected, 401 without a session; `/help` settings are untouched; deleting the user cascades

### test_support_ws.py
- the ticket card arrives before tokens; an unknown ticket and a missing server warn and still answer
- usage and no-base notices store nothing and take no rate slot
- history carries only `/support` pairs; side jobs do not run; a `/help` turn is unchanged

### test_support_http.py
- the success body with `support`; usage and no-base notice bodies (`200`, `stored: false`)
- `kb_id` is ignored for `/support`; the unknown ticket warning is in the body
- notices take no rate slot; a busy chat is `409`; ordinary and `/help` bodies have `support: null`

### test_support_ui.py (frontend source guard)
- the Settings section, its element ids and position; no base selector; numeric ranges; stage labels equal the `/help` labels
- the required Russian strings; the `support` payload feeds the label and the threshold line; builders use `textContent` only; saving uses PUT with a rollback; sending does not inspect the command; app.js is balanced

### test_support_probe.py and test_e2e_support_script.py
- the FAQ rank is one-based or absent; the ticket-aware query stays enabled only when it is not worse; the written record contains the table and the decisions, and a `not run` reason
- the server arguments are one per line; the keyword check is case-insensitive; the live verdict is `NOT RUN` when LM Studio is down; the scratch ticket status edit changes only the copy

### test_docs_faq.py and test_docs_support.py (documentation source guards)
- the FAQ values equal the code, sections start from the symptom, no keys, no seed data copied into the docs
- heading order of the `/support` sections, the form values of the support server, the usage example, the default threshold, the four tool names, every test file and script named here exists, no key-shaped string in `docs/`, no video script under `docs/`

### scripts/support_probe.py (not part of pytest)
- `python scripts/support_probe.py [--out eval_out/day33]` copies the tree to a scratch directory, starts it on :18000 / :18001, indexes the README and `docs/` (with `FAQ.md`) and records the best score and best source of support questions, ticket-aware queries and outside questions.
- It decides whether the threshold stays and whether the ticket-aware query is kept; the result is in `25-RETRIEVAL-PROBE.md`. Exit 0 done, 1 a step aborted, 2 blocked by the preflight (recorded as `not run`).

### scripts/e2e_support_playwright.py (isolated copy, not part of pytest)
- `python scripts/e2e_support_playwright.py` starts an isolated copy at UI :18000 / Agent :18001 with a scratch database and a scratch copy of `support_data.json`; it never touches :8000/:8001. It adds the support server through the «MCP серверы» form, checks the «Поддержка (/support)» section, the usage notice, the ticket card and label of `T-1001` and `T-1002`, the label after a reload, the HTTP body, a follow-up and a status change in the scratch JSON.
- Exit 0 all checks passed, 1 a check failed, 2 blocked (ports busy), 4 Playwright missing. Checks that need a chat model are recorded as `NOT RUN`, never as passed, when LM Studio has no loaded chat model.
- The recorded run (`25-E2E.md`) passed its non-model checks and left seven model-dependent checks as NOT RUN because no chat model was loaded; they have not been verified.

### Regression gate for /help

The shared command-turn mechanism must keep `/help` unchanged. Run before and after any change to it:

```bash
pytest tests/test_command_turn.py tests/test_help_command.py tests/test_help_api.py tests/test_help_turn.py tests/test_help_ws.py tests/test_help_http.py tests/test_help_ui.py tests/test_docs_help.py -q
```

## Automated PR review (Day 32)

### Coverage map for Day 32

| Area | Test files |
|------|-----------|
| Review route, settings routes, code index routes | `test_review_api.py` |
| Chain of the review and provider/model/base resolution | `test_review_service.py` |
| Pull Request data through the GitHub MCP server | `test_review_github.py` |
| Git access to the base branch | `test_code_git.py` |
| Incremental code knowledge base | `test_code_index.py`, `test_code_chunking.py` |
| Budget, parts, retrieval, model calls | `test_review_pipeline.py`, `test_review_prompts.py` |
| Workflow and comment script | `test_pr_review_ci.py` |
| Settings section in the browser source | `test_review_settings_ui.py` |
| Control set, control script, judge | `test_review_control_set.py`, `test_review_control.py`, `test_review_judge.py` |
| Documentation | `test_docs_day32.py` |

### test_review_api.py
- authentication: 401 without credentials; success with an API key and with a session cookie
- body validation (unknown field, bad repository name, bad number or sha); wrong content type and foreign origin are rejected
- every error code maps to its status; an unknown code is 502; an unexpected error is a fixed 500 text without the exception
- the busy guard answers `REVIEW_BUSY` for the same Pull Request; the lock is released after an error and after a cancelled request
- settings GET: defaults for a session and for a key; the repository path is hidden from an API key
- settings PUT: session only and the row stays unchanged for a key; omitted keeps and `null` clears; bad repository path, field bounds, a base that is not ready is 422, foreign ids are 404; two users never see each other
- code index: the state; refresh without a saved path, session-only refresh, the base branch rules, an invalid branch and the busy answer

### test_review_service.py
- no row or an empty path is `REVIEW_NOT_CONFIGURED`; the documentation base falls back from the review setting to the `/help` mark; a foreign or not-ready base counts as absent
- model choice: a stored model needs no LM Studio call; a loaded chat model skips embedding models; no loaded model; LM Studio not answering; a disabled provider
- call order and arguments; the embedding model follows the documentation base
- the head in the reply is the one read from GitHub; new commits during the review add a note; a failed final head check does not fail the review; the parts note
- domain errors become service errors, other exceptions propagate; not configured stops before GitHub is asked

### test_review_github.py
- metadata, files and diff are read; files are paged and a missing patch is `None`; too many pages is `PR_TOO_LARGE`; a huge diff is returned unchanged; a null body becomes an empty string
- error results are mapped (not found, token rejected); timeout and MCP errors are `GITHUB_MCP_UNAVAILABLE`; unparseable replies are not understood
- an invalid repository name raises before any call; the final head check makes one call; server resolution errors
- the real-binary test reads a Pull Request through the real `github-mcp-server.exe`; it is skipped unless the binary exists and `GITHUB_PERSONAL_ACCESS_TOKEN` and `GITHUB_REVIEW_TEST_PR` are set

### test_code_git.py
- repository validation (top-level folder, unusable text, missing folder, not a repository, no `origin`); repository key from the `origin` URL formats
- branch name rules; `fetch` ignores the checked-out branch and local edits; an unknown branch does not reveal the `origin` URL; concurrent branches do not mix; a detached HEAD has no branch
- the tree lists regular files only; blobs are read byte-exact; an unknown id raises; an empty list is empty; a timeout kills the process

### test_code_chunking.py
- Python structure and section names; async and nested functions; a long function is split and covers every line; a single huge line is cut
- Cyrillic text, empty file, other files by line windows, CRLF normalisation; a Python file that does not parse falls back to windows
- first line and name of a fragment, the line range parser, character offsets, the 2000-character cap over the real repository files

### test_code_index.py
- indexable file rules (suffixes, size, excluded folders)
- the first refresh builds a ready base; an unpushed edit does not change the index; no new commit embeds nothing; edit, add and delete update only the difference; a delete-only commit
- an embedder failure keeps the previous index; a failed first build leaves nothing; a failure inside the commit rolls back
- a repository that does not match is refused before any fetch (case-insensitive); an unknown branch is `BASE_UNAVAILABLE`; a different branch gets its own base; the sixth base removes the oldest
- concurrent refreshes stay consistent; two users are isolated; the job reports progress and refuses a second start; a failure is stored as a readable text; the module never opens files from disk

### test_review_pipeline.py
- token estimate headroom; diff splitting by file (renames, spaces); one part when the diff fits; a split covers every file; a huge file is split by hunks with a note; missing patches use the diff segment; too many parts and a too small context are `PR_TOO_LARGE`
- queries are bounded and hold the right parts for documentation and code
- parsing of the model reply (headings, think blocks, bad format); sanitising of mentions, images and HTML; code is kept; truncation
- one part is one call with both sources; separate quotas and no second-stage options; three parts make four calls and the merge call has no diff; every call is tool-less with reasoning off
- no documentation base and a documentation failure still produce a review; an embedder failure stops it; a code index failure is reported; model errors are mapped and carry only the status; a rejected reasoning field is retried; a bad reply gets one repair call; unreviewed files are named; the output is sanitised; the module imports no tool modules

### test_review_prompts.py
- neutralising of angle brackets and equals runs; the shape and text of the review messages
- a closing tag inside the diff cannot escape the diff element; a missing documentation block is announced; the part note and a long description; the merge messages wrap partial answers

### test_pr_review_ci.py
- comment structure, empty source lists, notes, defusing of mentions and images, truncation order (sources first, then sections proportionally)
- failure lines for each cause and the 300-character cut; finding the script's own comment (marker and bot login); create when absent, follow pages and patch
- `main`: success, a failed review with echoed secrets scrubbed, a forbidden comment, missing or malformed variables, the default app address
- the script imports only the standard library; the workflow pins the action by commit, has the right triggers, guards, runner label and permissions, and has no expression inside `run:` and no literal secret

### test_review_settings_ui.py (frontend source guard)
- the section position and its copy; labels on the controls; live regions; the accent only on the save button; the cloud note
- no `innerHTML` in the section functions; routes and wiring; balanced braces

### test_review_control_set.py
- the manifest ids and control flags; the expected items; the sources exist and parse; every edit matches once; new paths do not collide with the repository; fixtures stay out of knowledge bases; the large Pull Request forces the per-file path

### test_review_control.py
- the Pull Request tree is built with the edits and an ambiguous edit is refused; `open` pushes branches and exits 4 without a token, is idempotent, and creates or reuses Pull Requests with a token
- `wait`: ok when the comment names the head, other commits and authors are ignored, no workflow run or a queued run is `не запускалось`
- `collect`, `rerun` (keeps the tree), `close` (only patches the state); `git` refuses foreign refs and commands; `prepare-docs`; a missing key or a dead app end as not run; no secret reaches the output

### test_review_judge.py
- splitting a review at the sources block; judge messages and a hostile review that cannot close a tag; parsing of the judge reply with tolerance and rejection
- only control Pull Requests are judged; a failed review is not judged and calls no model; a missing run, an unparsable reply retried once, a transport failure is `не запускалось`
- the `--check` command; the tables for several runs, a clean Pull Request row, escaping of pipes and newlines, no network for `--tables`

### test_docs_day32.py (documentation source guard)
- the user guide names the secret, the variable, the runner label, the approval setting, `--ephemeral`, `CLAUDE.md`, and the private-repository option; no key-shaped string anywhere in the docs
- the API reference lists every code of `STATUS_BY_CODE`; the secret and variable names equal the ones in the workflow; every failure line of the script is explained in the guide; the README mentions the workflow

### scripts/e2e_review_settings_playwright.py (isolated copy, not part of pytest)
- Starts an isolated copy of the app at UI :18000 / Agent :18001 with a scratch database and checks the «Ревью Pull Request» section in a browser; it never touches :8000/:8001. Command: `python scripts/e2e_review_settings_playwright.py`. The result is written to `eval_out/day32/e2e_review_settings.md`.
- A step that needs LM Studio and could not run (for example the successful index refresh) is recorded as `не запускалось`, never as passed.

### Control run on real Pull Requests (manual, not part of pytest)
- `scripts/review_control.py` opens the frozen control set of Pull Requests (`prepare-docs`, `open`, `wait`, `collect`, `rerun`, `close`, `status`); `scripts/review_judge.py` asks a DeepSeek judge which planted defects each review found and how many remarks were false. The control run of Plan 24-13 uses the local model and then DeepSeek; the records are in `eval_out/day32/`.
- Exit codes of the control script: 0 done, 1 failed, 3 not run, 4 a manual step is needed. A run that could not be made (no runner, no token, no model) is recorded as `не запускалось`, never as a result.
