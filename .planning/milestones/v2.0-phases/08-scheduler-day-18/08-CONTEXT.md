# Phase 8: Scheduler (Day 18) - Context

**Gathered:** 2026-09-26
**Status:** Ready for planning

<domain>
## Phase Boundary

A user (via UI form) or the LLM (via chat tools) can schedule delayed (one-shot) and periodic (interval / cron) jobs. The Agent process runs them in the background with an asyncio poll loop, persists each job's status and **every run's result**, and the UI shows scheduled and completed jobs with live updates. Jobs survive Agent restarts. Written from scratch in Python inside the Agent process (not a fork of `C:\Projects\mcp-cron`).

Fixed by ROADMAP (not re-discussed): `ScheduledTask` + `TaskRun` SQLModel tables scoped by `user_id`; poll loop started in the agent lifespan with optimistic claim (`UPDATE ... WHERE next_run_at = old`); REST `/api/v1/scheduler/*`; LLM tools `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`; vanilla-JS UI panel. Branch `Day18`.

**Not in this phase:** direct (LLM-less) MCP tool-call jobs, posting run results into chats, per-job timezones, retries, non-Agent-process execution, notifications outside the app UI. Requirement IDs are still "TBD" in the roadmap — the planner should define `SCHED-xx` requirements in REQUIREMENTS.md.
</domain>

<decisions>
## Implementation Decisions

### Job execution (what fires)
- **D-01:** A job's payload is an **LLM prompt** (natural-language text stored on the job). At fire time the Agent runs a **headless LLM turn** — no WebSocket, no chat. There is no `kind` field and no direct MCP tool-call job type.
- **D-02:** A run's final answer text and tool trace are stored **on the `TaskRun` row only**. Nothing is written to the message tree / any chat; no chat lock is taken.
- **D-03:** The **model id is stored on the job** at creation (default = model in use / global default). Sampling settings (temperature, max_tokens) resolve from the user's **global** Settings at fire time. If the model is unavailable (e.g. LM Studio not running), the run ends `failed` with a clear error rather than falling back.
- **D-04:** Tools available in a headless run: the user's **MCP tools** (lazy auto-connect exactly as in chat, `MCP_AUTO_CONNECT`) **plus `save_long_term_memory`** (user-scoped, needs no chat). **Excluded:** `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` (jobs must not spawn or cancel jobs) and all chat-bound tools — task FSM (`create_task`, `transition_task`, `pause_task`, `resume_task`; `Task.chat_id` is NOT NULL) and `save_working_memory` (keyed by `chat_id`).
- **D-05:** Each run has an **overall timeout (~120 s, overridable via a new `shared/config.py` setting)** plus the existing `MAX_TOOL_ROUNDS` cap; hitting the timeout ends the run as `failed`. Tool calls stay strictly sequential (existing project rule).

### Schedule kinds & reliability
- **D-06:** Three schedule types: **once**, **interval**, **cron** (standard 5-field).
- **D-07:** **Cron is interpreted in the Agent machine's local timezone** (the same local time the LLM sees in the system-prompt clock line); **all stored timestamps (`next_run_at`, run times) are UTC**. No per-job timezone field.
- **D-08:** **Missed runs after downtime: catch-up once.** A missed one-shot runs at startup (run flagged as late/missed); a periodic job gets a single compensating run, then `next_run_at` advances to the next future slot — no storm of N missed runs.
- **D-09:** **Overlap policy: skip.** If the previous run of a job is still running when the next slot arrives, the new slot is recorded/skipped (run status `skipped`), not run in parallel.
- **D-10:** **No retries.** A `failed` run is a recorded result; the next scheduled slot fires normally.
- **D-11:** Periodic jobs support an **optional `max_runs`** — after N runs the job auto-completes.

### Creating jobs from chat (LLM tools)
- **D-12:** `schedule_task` args: `schedule_type` (`once` | `interval` | `cron`) plus per-type fields — once → `delay_seconds` **or** `run_at` (ISO local time); interval → `interval_seconds`; cron → `cron` (5-field string) — plus `prompt`, `title`, optional `max_runs`. The model computes absolute times itself from the clock line; no free-form "in 5m / every 1h" string parsing.
- **D-13:** The job stores **`origin_chat_id`** (nullable FK to `chat.id`, **`ondelete="SET NULL"`**) for provenance only; `NULL` for jobs created from the UI. Deleting a chat does **not** delete its jobs (they are user-scoped).
- **D-14:** `list_scheduled_tasks` returns the **user's** active/paused jobs (id, title, schedule, next_run_at, last run status). `cancel_scheduled_task` takes an **explicit `task_id`** (no implicit "current job"), is a **soft cancel** (status → cancelled; run history kept), and is **gated**: the LLM may cancel only when the user directly asked to — enforced by tool description/prompt rule **and** a code-side guard (mechanism at planner's discretion; analogous to backlog 999.1's intent for `merge_merge_request`).

### UI panel & result display
- **D-15:** A new foldable **`#scheduler-panel` in the sidebar**, in the same style as `#memory-panel` / `#task-panel` / `#invariants-panel` (`ui/static/index.html`). Job list with status badges; clicking a job expands its **run history**; clicking a run opens its **full result (Markdown, via Marked + `DOMPurify.sanitize()`) in a modal** so the narrow sidebar isn't overloaded. Labels in Russian.
- **D-16:** UI actions (all four): **create job via form** (title, prompt, schedule type + fields, model, max_runs), **Pause / Resume**, **Run now** (immediate off-schedule run), **Cancel / Delete** (Cancel = soft, history kept; Delete = removes the job and its runs).
- **D-17:** **Live updates: WebSocket push + polling fallback.** Live events (`run_started`, `run_finished`, job status change) go over a **new user-level WebSocket `/ws/events`** with the same origin + session-cookie checks as `ws_chat`, and a registry mapping `user_id → sockets` so events reach **only the owner**. A rare REST poll (`GET /api/v1/scheduler/tasks`) re-syncs on panel open and on WS reconnect.

### Claude's Discretion
- Cron implementation: small in-house 5-field parser vs a dependency (e.g. `croniter`); researcher decides (pin in `requirements.txt` if a dependency).
- Poll-loop tick interval, exact timeout value, table/column names, status enum values (suggested: job `active|paused|completed|cancelled`; run `running|success|failed|skipped`, plus a missed/late marker), REST shapes, WS frame shapes, and how the headless runner is built (see integration points).
- Whether "Run now" shares the claim/lock path with scheduled runs; how `save_long_term_memory`'s `chat_id` handler argument is satisfied in a headless run.
- Mechanism of the cancel gate (D-14).
- Tailwind markup, Russian label wording, empty-state copy.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & requirements
- `.planning/ROADMAP.md` §Phase 8: Scheduler (Day 18) — goal, branch `Day18`, from-scratch decision, scope sketch
- `.planning/REQUIREMENTS.md` — v2.0 requirement format; define `SCHED-xx` here during planning
- `.planning/PROJECT.md` §Constraints — no Docker/Node/broker/`multiprocessing`; vanilla JS + CDN only; `user_id` scoping; IPC = REST + WS only; in-memory state per process

### Prior-phase decisions to stay consistent with
- `.planning/phases/07-mcp-connection-day-16/07-CONTEXT.md` — MCP session registry keyed `(user_id, server_id)`, lazy auto-connect, Russian labels, Settings/REST conventions
- `.planning/STATE.md` §Decisions — tool calls run strictly sequentially; tool-call dispatcher built once and reused; task/memory writes are tool-call-only

### Codebase conventions & architecture
- `CLAUDE.md` — hard constraints, SQLModel FK cascade rule (`sa_column=Column(ForeignKey(..., ondelete=...))`), `.is_(None)`, commit/rollback pattern, structlog, `datetime.now(timezone.utc)`, `asyncio.wait_for` timeouts
- `.planning/codebase/ARCHITECTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`, `STRUCTURE.md`, `STACK.md`, `INTEGRATIONS.md` — layering, naming, test fixtures
- `docs/TESTING_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/API_SPEC.md` — check before adding tests/endpoints

### Backlog with related intent
- `.planning/ROADMAP.md` §Backlog 999.1 — "block unprompted destructive tool use" (same intent as the cancel gate, D-14); deferred to Day 20, do not implement here

No external spec files — behavior is fully captured in the decisions above.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/tools.py::register_tool` / `dispatch_tool_calls` — registry for `schedule_task` etc.; handler signature `(session, user_id, chat_id, args)` gives `chat_id` for `origin_chat_id`. `TOOL_REGISTRY` is global, so headless runs must **filter** scheduler and chat-bound tools out of the schema list (D-04).
- `agent/mcp_tools.py::build_mcp_toolset` / `call_mcp_tool` / `_auto_connect_missing` — per-user MCP toolset with lazy auto-connect; `agent/mcp_client.py` session registry keyed by `(user_id, server_id)`.
- `agent/llm_client.py::LLMClient.stream_chat` / `complete_chat` — streaming and non-streaming completions with `tools=`.
- `agent/tool_guard.py` — `MAX_TOOL_ROUNDS`, `TOOL_USE_RULE`, `MULTI_STEP_TOOL_HINT`, `build_clock_line`, `build_tool_fallback_summary`; reuse for the headless loop.
- `agent/main.py::lifespan` (~line 385) — start the poll loop there; cancel it and await shutdown before `engine.dispose()` (alongside `mcp_client.cleanup_all_sessions()`).
- `shared/models.py` — `Profile`/`McpServerConfig` show the user-scoped table pattern with cascade FK; `shared/database.py` holds migrations/`init_db`.
- `shared/config.py` — add run-timeout / poll-interval settings here.
- `agent/dependencies.py::get_current_user` (REST auth) and `get_current_user_ws` + `ws.py::_validate_origin` (WS auth/origin) — reuse for REST and `/ws/events`.
- UI: `ui/static/index.html` sidebar panels (`#memory-panel` ~L48, `#task-panel` ~L105, `#invariants-panel` ~L115), `data-fold-toggle` folding pattern, `ui/static/app.js` (WS client at ~L1118, `setInterval(checkAgentHealth, 5000)` at ~L2035).

### Established Patterns
- User-scoped tables via `sa_column=Column(ForeignKey("user.id", ondelete="CASCADE"))`; REST with try/except → `await session.rollback()`; `HTTPException` with specific codes.
- In-memory per-process state lives in `agent/state.py`; deleting a user/chat must clean related caches.
- The word "task" is taken: existing `Task` = chat-scoped FSM unit (`shared/models.py`). Name the new model `ScheduledTask` / `TaskRun` and keep tool/route names distinct to avoid confusion with `create_task` etc.
- Frontend: vanilla JS, Tailwind CDN, all dynamic HTML through `DOMPurify.sanitize()`, Russian UI text.

### Integration Points / Pitfalls
- **The chat tool loop is WebSocket-bound** (`ws.py::_run_tool_rounds`, `_dispatch_round`, `_handle_chat_message` send frames and take `chat_locks`). A headless runner needs either an extracted socket-free core or a small dedicated loop reusing the same helpers/nudges; the planner must decide, without changing chat behavior.
- **`ws.py::active_connections` is an ownerless set** and `broadcast_model_event` sends to *all* sockets. The new `/ws/events` registry must be keyed by `user_id`; never reuse the ownerless broadcast for scheduler events.
- Poll-loop claim must be safe under SQLite single-writer + WAL (`busy_timeout=5000`); the Agent is a single process, but the optimistic `UPDATE ... WHERE next_run_at = old` claim is still required (ROADMAP).
- Agent lifecycle: the supervisor restarts the Agent on crash — startup must run the catch-up pass (D-08) and mark runs left `running` by a dead process as `failed`/interrupted.
- New tests follow `tests/conftest.py` fixtures; add scheduler tests (claim, catch-up, overlap skip, max_runs, user scoping, WS event isolation, cancel gate) and cascade/scoping checks in the style of `test_cascade_delete.py` / `test_scoping.py`.

</code_context>

<specifics>
## Specific Ideas

- "Run now" and `max_runs` exist mainly so the demo video can show a scheduled job firing and finishing without waiting long.
- Demo path to keep in mind: create a job from chat ("через минуту прочитай файл X через MCP и перескажи") → see it in the sidebar → live `running` → `success` with the result in the modal; then a periodic job with `max_runs`.
- The user explicitly wanted live WS updates even though polling would have been simpler (D-17) — don't downgrade to polling-only.

</specifics>

<deferred>
## Deferred Ideas

- Direct MCP tool-call jobs (no LLM) — a possible later `kind: tool_call`.
- Posting run results into the originating chat / a dedicated chat per job.
- Per-job timezone, retries with backoff, catch-up of every missed slot.
- Chat-bound tools (task FSM, working memory) inside scheduled runs, bound to `origin_chat_id`.
- Guarding `merge_merge_request` (backlog 999.1) and other Day 20 backlog items — unchanged, still deferred to Day 20.

### Reviewed Todos (not folded)
None — no pending todos matched this phase.

</deferred>

---

*Phase: 08-scheduler-day-18*
*Context gathered: 2026-09-26*
