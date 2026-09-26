# Phase 8: Scheduler (Day 18) - Research

**Researched:** 2026-09-26
**Domain:** In-process asyncio job scheduler (once / interval / cron) inside a FastAPI + SQLModel + aiosqlite Agent, with a headless LLM+MCP tool-loop runner, per-user WebSocket event push, and a vanilla-JS sidebar panel
**Confidence:** HIGH for everything about this codebase (code read line by line; the headless-runner, atomic-claim, partial-unique-index and cron-library behaviours were each **executed** in scratch prototypes on this machine, Windows 11 / Python 3.13.15 / SQLAlchemy 2.0.52 / SQLModel 0.0.42 / aiosqlite 0.22.1). MEDIUM for DST behaviour of local-time cron (the dev machine's zone has no DST, so that path could not be exercised).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Phase Boundary:** A user (via UI form) or the LLM (via chat tools) can schedule delayed (one-shot) and periodic (interval / cron) jobs. The Agent process runs them in the background with an asyncio poll loop, persists each job's status and **every run's result**, and the UI shows scheduled and completed jobs with live updates. Jobs survive Agent restarts. Written from scratch in Python inside the Agent process (not a fork of `C:\Projects\mcp-cron`).

Fixed by ROADMAP (not re-discussed): `ScheduledTask` + `TaskRun` SQLModel tables scoped by `user_id`; poll loop started in the agent lifespan with optimistic claim (`UPDATE ... WHERE next_run_at = old`); REST `/api/v1/scheduler/*`; LLM tools `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`; vanilla-JS UI panel. Branch `Day18`.

**Not in this phase:** direct (LLM-less) MCP tool-call jobs, posting run results into chats, per-job timezones, retries, non-Agent-process execution, notifications outside the app UI. Requirement IDs are still "TBD" in the roadmap — the planner should define `SCHED-xx` requirements in REQUIREMENTS.md.

**Job execution (what fires)**
- **D-01:** A job's payload is an **LLM prompt** (natural-language text stored on the job). At fire time the Agent runs a **headless LLM turn** — no WebSocket, no chat. There is no `kind` field and no direct MCP tool-call job type.
- **D-02:** A run's final answer text and tool trace are stored **on the `TaskRun` row only**. Nothing is written to the message tree / any chat; no chat lock is taken.
- **D-03:** The **model id is stored on the job** at creation (default = model in use / global default). Sampling settings (temperature, max_tokens) resolve from the user's **global** Settings at fire time. If the model is unavailable (e.g. LM Studio not running), the run ends `failed` with a clear error rather than falling back.
- **D-04:** Tools available in a headless run: the user's **MCP tools** (lazy auto-connect exactly as in chat, `MCP_AUTO_CONNECT`) **plus `save_long_term_memory`** (user-scoped, needs no chat). **Excluded:** `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` (jobs must not spawn or cancel jobs) and all chat-bound tools — task FSM (`create_task`, `transition_task`, `pause_task`, `resume_task`; `Task.chat_id` is NOT NULL) and `save_working_memory` (keyed by `chat_id`).
- **D-05:** Each run has an **overall timeout (~120 s, overridable via a new `shared/config.py` setting)** plus the existing `MAX_TOOL_ROUNDS` cap; hitting the timeout ends the run as `failed`. Tool calls stay strictly sequential (existing project rule).

**Schedule kinds & reliability**
- **D-06:** Three schedule types: **once**, **interval**, **cron** (standard 5-field).
- **D-07:** **Cron is interpreted in the Agent machine's local timezone** (the same local time the LLM sees in the system-prompt clock line); **all stored timestamps (`next_run_at`, run times) are UTC**. No per-job timezone field.
- **D-08:** **Missed runs after downtime: catch-up once.** A missed one-shot runs at startup (run flagged as late/missed); a periodic job gets a single compensating run, then `next_run_at` advances to the next future slot — no storm of N missed runs.
- **D-09:** **Overlap policy: skip.** If the previous run of a job is still running when the next slot arrives, the new slot is recorded/skipped (run status `skipped`), not run in parallel.
- **D-10:** **No retries.** A `failed` run is a recorded result; the next scheduled slot fires normally.
- **D-11:** Periodic jobs support an **optional `max_runs`** — after N runs the job auto-completes.

**Creating jobs from chat (LLM tools)**
- **D-12:** `schedule_task` args: `schedule_type` (`once` | `interval` | `cron`) plus per-type fields — once → `delay_seconds` **or** `run_at` (ISO local time); interval → `interval_seconds`; cron → `cron` (5-field string) — plus `prompt`, `title`, optional `max_runs`. The model computes absolute times itself from the clock line; no free-form "in 5m / every 1h" string parsing.
- **D-13:** The job stores **`origin_chat_id`** (nullable FK to `chat.id`, **`ondelete="SET NULL"`**) for provenance only; `NULL` for jobs created from the UI. Deleting a chat does **not** delete its jobs (they are user-scoped).
- **D-14:** `list_scheduled_tasks` returns the **user's** active/paused jobs (id, title, schedule, next_run_at, last run status). `cancel_scheduled_task` takes an **explicit `task_id`** (no implicit "current job"), is a **soft cancel** (status → cancelled; run history kept), and is **gated**: the LLM may cancel only when the user directly asked to — enforced by tool description/prompt rule **and** a code-side guard (mechanism at planner's discretion; analogous to backlog 999.1's intent for `merge_merge_request`).

**UI panel & result display**
- **D-15:** A new foldable **`#scheduler-panel` in the sidebar**, in the same style as `#memory-panel` / `#task-panel` / `#invariants-panel` (`ui/static/index.html`). Job list with status badges; clicking a job expands its **run history**; clicking a run opens its **full result (Markdown, via Marked + `DOMPurify.sanitize()`) in a modal** so the narrow sidebar isn't overloaded. Labels in Russian.
- **D-16:** UI actions (all four): **create job via form** (title, prompt, schedule type + fields, model, max_runs), **Pause / Resume**, **Run now** (immediate off-schedule run), **Cancel / Delete** (Cancel = soft, history kept; Delete = removes the job and its runs).
- **D-17:** **Live updates: WebSocket push + polling fallback.** Live events (`run_started`, `run_finished`, job status change) go over a **new user-level WebSocket `/ws/events`** with the same origin + session-cookie checks as `ws_chat`, and a registry mapping `user_id → sockets` so events reach **only the owner**. A rare REST poll (`GET /api/v1/scheduler/tasks`) re-syncs on panel open and on WS reconnect.

### Claude's Discretion
- Cron implementation: small in-house 5-field parser vs a dependency (e.g. `croniter`); researcher decides (pin in `requirements.txt` if a dependency).
- Poll-loop tick interval, exact timeout value, table/column names, status enum values (suggested: job `active|paused|completed|cancelled`; run `running|success|failed|skipped`, plus a missed/late marker), REST shapes, WS frame shapes, and how the headless runner is built (see integration points).
- Whether "Run now" shares the claim/lock path with scheduled runs; how `save_long_term_memory`'s `chat_id` handler argument is satisfied in a headless run.
- Mechanism of the cancel gate (D-14).
- Tailwind markup, Russian label wording, empty-state copy.

### Deferred Ideas (OUT OF SCOPE)
- Direct MCP tool-call jobs (no LLM) — a possible later `kind: tool_call`.
- Posting run results into the originating chat / a dedicated chat per job.
- Per-job timezone, retries with backoff, catch-up of every missed slot.
- Chat-bound tools (task FSM, working memory) inside scheduled runs, bound to `origin_chat_id`.
- Guarding `merge_merge_request` (backlog 999.1) and other Day 20 backlog items — unchanged, still deferred to Day 20.
</user_constraints>

<phase_requirements>
## Phase Requirements

REQUIREMENTS.md has no IDs for this phase yet. The planner should add the following `SCHED-xx` block (new "Scheduler (Day 18)" section in `.planning/REQUIREMENTS.md`, plus Traceability rows -> Phase 8). Candidates are derived 1:1 from the locked decisions.

| ID | Description | Source | Research Support |
|----|-------------|--------|------------------|
| SCHED-01 | User can create a scheduled job (title, prompt, model, schedule type once/interval/cron, optional `max_runs`) through the UI form / REST; jobs are stored in SQLite scoped by `user_id` | D-06, D-11, D-16 | Table design, validation rules, `agent/schedule.py`, REST shapes (Sections "Data model", "REST") |
| SCHED-02 | The LLM can create, list and cancel jobs through chat tools `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`; job keeps `origin_chat_id` (`ON DELETE SET NULL`) | D-12, D-13, D-14 | Tool arg models, model-in-context ContextVar, `_collect_memory_writes` pitfall |
| SCHED-03 | The Agent runs due jobs in the background (asyncio poll loop started in the lifespan, atomic optimistic claim `UPDATE ... WHERE next_run_at = old`); jobs survive Agent restarts | ROADMAP, D-08 | Poll loop + claim pattern (verified) |
| SCHED-04 | After downtime a missed job gets exactly one compensating run flagged `is_late`; `next_run_at` advances to the next future slot; runs left `running` by a dead process are marked `failed` at startup | D-08 | Startup recovery + catch-up-by-first-tick design |
| SCHED-05 | A run executes a headless LLM turn (no chat, no WS) with the user's MCP tools + `save_long_term_memory`, sequential tool calls, `MAX_TOOL_ROUNDS` cap and an overall timeout (`SCHEDULER_RUN_TIMEOUT`, default 120 s); unavailable model => `failed` with a clear error | D-01..D-05 | Headless runner via `RecordingSink` + reused `_run_tool_rounds`; allowlist guard in dispatcher |
| SCHED-06 | Every run's status, timings, final answer, error and tool trace are stored on a `TaskRun` row; nothing is written to any chat | D-02, D-10 | `TaskRun` columns |
| SCHED-07 | Overlap policy: a slot arriving while the previous run is still running is recorded as a `skipped` run, never run in parallel (DB-enforced by a partial unique index) | D-09 | Partial unique index (verified), claim algorithm |
| SCHED-08 | Periodic jobs with `max_runs` auto-complete after N runs; one-shot jobs complete after their run | D-11 | Finalize invariant |
| SCHED-09 | Cron expressions (5 fields) are interpreted in the Agent machine's local time, all stored timestamps are UTC | D-07 | `cronsim` + naive-local conversion helpers |
| SCHED-10 | REST `/api/v1/scheduler/*` lets the owner list/create/get jobs, pause, resume, run now, cancel (soft), delete (job + runs), and read run history / a run's full result; all routes are session-authenticated and user-scoped (foreign ids -> 404) | D-16, D-13 | REST section, `test_scoping.py` pattern |
| SCHED-11 | `cancel_scheduled_task` may only cancel when the user's latest message asked for it (tool description + code-side guard) | D-14 | Cancel gate design |
| SCHED-12 | A user-level WebSocket `/ws/events` (origin + session-cookie checks like `ws_chat`) pushes `run_started` / `run_finished` / task-status events **only to the owner**; REST re-sync on connect | D-17 | `EventHub` design |
| SCHED-13 | The sidebar `#scheduler-panel` lists jobs with status badges, expandable run history, full result in a Markdown modal, create form, pause/resume/run-now/cancel/delete, live updates with polling fallback (per 08-UI-SPEC.md) | D-15, D-16, D-17 | UI notes |
| SCHED-14 | pytest covers cron next-run math, claim atomicity, catch-up, overlap skip, max_runs, headless runner (success / tool round / timeout / model down / disallowed tool), user scoping and event isolation, cancel gate, chat-delete SET NULL | CONTEXT integration points | Test strategy |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

Treated as locked. Research does not recommend anything that violates them.

- **Hard constraints:** no Docker/npm/Node/Redis/RabbitMQ/Celery; **no `multiprocessing`/`os.fork`**; IPC between UI and Agent only REST + WebSocket; in-memory state per process; frontend vanilla JS + CDN only (Tailwind, Marked, DOMPurify) — no bundler, no npm packages. A pure-Python pip dependency (`cronsim`) is consistent with this (the project already pins `mcp==1.30.0` etc.).
- **Auth/scoping:** session HTTP-only cookie; all new data scoped by `user_id`; cross-user access answers **404, never 403** (see `_get_chat_or_404`).
- **Conventions:** type hints everywhere (`str | None`, `list[...]`), `async/await` for all I/O, `structlog` via `logger = get_logger(__name__)` (never `print`), `snake_case_action` log keys with `key=value` pairs, **never log secrets / prompts / full tracebacks** (log `str(exc)` / `type(exc).__name__` only), `datetime.now(timezone.utc)` (never `utcnow`), import order stdlib -> third-party -> local, single-line module docstrings, no bare `except:`.
- **SQLModel:** FK cascade only via `sa_column=Column(Integer, ForeignKey(..., ondelete=...))` — **never** `Field(ondelete=...)`; `.is_(None)` not `== None` in `where()`; always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- **WebSocket:** wrap `websocket.receive_json()`/`receive_text()` in `asyncio.wait_for(..., timeout)`.
- **Frontend:** every dynamic HTML through `DOMPurify.sanitize()`; Russian UI text; no `innerHTML` with server data except via `renderMarkdown()`.
- **Testing:** pytest + pytest-asyncio (`asyncio_mode = auto`), `respx` for HTTP mocks, separate test DB (`tests/conftest.py`); check `docs/TESTING_GUIDE.md` before adding tests; update `docs/API_SPEC.md` / `docs/ARCHITECTURE.md` for new endpoints.
- **Workflow:** work goes through GSD commands (this research is inside `/bm:plan-phase`).

## Summary

The scheduler fits the existing architecture with **small, additive changes**. The hard part — running an LLM+MCP tool loop with no WebSocket — is already 95% solved by `agent/ws.py`: `_run_tool_rounds` / `_stream_follow_up_with_empty_retry` / `_pick_nudge` / `_stream_nudge` touch the socket **only** through `websocket.send_json(...)` and read only `turn.chat.user_id`, `turn.chat_id`, `turn.payload.model`, `turn.session`, `turn.toolset`, `turn.tool_schemas`, `turn.llm_messages`, temperature and max_tokens. Passing a tiny `RecordingSink` (an object with `async send_json`) plus a `SimpleNamespace` for `chat`/`payload` gives a fully working headless runner **without touching chat behaviour, and without moving any symbol that tests monkeypatch** (`agent.ws.MAX_TOOL_ROUNDS`). I prototyped exactly this against the real modules with a mocked LLM: a model-issued `save_long_term_memory` call was dispatched with `chat_id=0`, the row landed in `LongTermMemory` for the right user, the follow-up text was captured, and `tool_call` frames were recorded `[VERIFIED: local prototype]`. The one thing the shared dispatcher lacks is an **allowlist**: `dispatch_tool_calls` executes any name in the global `TOOL_REGISTRY` even if that schema was never offered, so a hallucinated `create_task` (whose FK to `chat` would fail with `chat_id=0`) or `schedule_task` would run. A default-`None` `allowed_tools` parameter (threaded through `_ToolTurn` -> `_dispatch_round` -> `dispatch_tool_calls`) closes that hole for D-04 without changing chat behaviour.

Scheduling reliability is mostly about **one atomic transaction per due slot**: `UPDATE scheduledtask SET next_run_at = <next future slot> WHERE id=? AND status='active' AND next_run_at=<old>` (check `rowcount == 1`) and `INSERT taskrun` in the same commit. Catch-up (D-08) then needs no special code: the first tick after a restart finds `next_run_at` in the past, fires one run flagged `is_late`, and the claim already advanced `next_run_at` to the next future slot. A **partial unique index** `UNIQUE(scheduled_task_id) WHERE status='running'` makes overlap-skip (D-09) a database invariant and also protects the "Run now" race — verified to work through `SQLModel.metadata.create_all` `[VERIFIED: local prototype]`. Because `ui/supervisor.py` stops the Agent with `Popen.terminate()` (a hard kill on Windows), lifespan shutdown code will often **not** run, so marking orphaned `running` runs as `failed` at **startup** is mandatory, not optional.

For cron, use **`cronsim==2.7`** (zero dependencies, BSD-3, Python >= 3.10, healthchecks.io author, released 2025-10-21) fed **naive local datetimes**, converting to UTC with `naive.astimezone(timezone.utc)`. This deliberately avoids `zoneinfo`, which fails on this Windows machine without the `tzdata` package (`ZoneInfoNotFoundError: Europe/Berlin` — verified). Gotchas found by execution: cronsim happily accepts **6-field** expressions (with seconds) and rejects `@daily`, so the code must enforce exactly 5 whitespace-separated fields itself.

**Primary recommendation:** Add `agent/schedule.py` (pure next-run math + validation, `cronsim`), `agent/scheduler.py` (poll loop, atomic claim, recovery, executor, `EventHub`), `agent/headless.py` (RecordingSink runner reusing `agent.ws._run_tool_rounds`), `agent/scheduler_api.py` (APIRouter) and `agent/scheduler_tools.py` (3 LLM tools); add `ScheduledTask`/`TaskRun` to `shared/models.py` (tables are created by the existing `create_all` — no ALTER migration needed); make **three surgical edits to existing chat code** (allowlist parameter, `_collect_memory_writes` exclusion of scheduler tool names, one-line `current_chat_model` ContextVar set) and gate the loop behind `SCHEDULER_ENABLED` so the ~40 `TestClient(app)` lifespan tests are unaffected.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Job/run persistence, cascade, overlap invariant | Database / Storage (SQLite) | API / Backend | FK cascades (`user` CASCADE, `origin_chat_id` SET NULL), partial unique index enforce invariants below the app |
| Schedule math (once/interval/cron, local->UTC) | API / Backend (`agent/schedule.py`, pure) | — | Server clock is the only authoritative time; browser never computes `next_run_at` |
| Poll loop, atomic claim, catch-up, orphan recovery | API / Backend (Agent lifespan task) | Database | Single Agent process; DB `UPDATE ... WHERE` is the concurrency primitive |
| Headless LLM+MCP execution | API / Backend | External (LM Studio, MCP stdio servers) | Reuses `llm_client`, `mcp_client` registry, `dispatch_tool_calls` |
| LLM tools (`schedule_task` etc.) | API / Backend (`TOOL_REGISTRY`) | — | Same dispatcher as memory/task tools |
| Cancel gate (D-14) | API / Backend (tool handler + description) | — | Must be code-side; the model is not trusted |
| Event fan-out per user | API / Backend (`EventHub` + `/ws/events`) | Browser (client socket) | In-memory registry keyed by `user_id`; never reuse ownerless `active_connections` |
| Panel, forms, modals, toasts, live re-render | Browser / Client (vanilla JS) | — | Per 08-UI-SPEC.md; talks REST + `/ws/events` directly to Agent :8001 like the rest of `app.js` |
| Static serving of panel markup | Frontend Server (UI process :8000) | — | Unchanged: only `index.html`/`app.js` edits |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `cronsim` | `==2.7` (PyPI upload 2025-10-21) | 5-field cron next-occurrence iteration | Zero dependencies, tiny, Python >= 3.10, used by healthchecks.io; naive-datetime mode sidesteps Windows tz database problem `[VERIFIED: pip index + local execution; PyPI JSON]` |
| FastAPI / Starlette | already installed (0.141.1 / 1.6.0) | REST router + `/ws/events` | Existing stack |
| SQLModel + SQLAlchemy 2.x + aiosqlite | 0.0.42 / 2.0.52 / 0.22.1 installed (requirements floor `sqlmodel>=0.0.22`) | Tables, atomic claim, partial index | Existing stack; `session.exec(update(...))` returns a `CursorResult` with `.rowcount` and emits **no** deprecation warning `[VERIFIED: local execution]` |
| stdlib `asyncio` (`asyncio.timeout`, `Semaphore`, `Queue`, `create_task`) | Python 3.11+ (dev 3.13.15) | Poll loop, run timeout, per-connection outbound queue | Project rule: asyncio only; `requirements.txt` already documents 3.11+ |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `respx` | installed 0.23.1 | Mock LM Studio `/v1/chat/completions` SSE in headless-runner tests | Reuse `tests/test_memory_ws.py::_plain_content_response/_tool_calls_response` and `tests/test_tool_rounds_ws.py::_stream_queue` |
| `starlette.testclient.TestClient` | installed | `/ws/events` auth/origin/isolation tests | Same pattern as `tests/test_ws_auth.py` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `cronsim` | `croniter` 6.2.4 (pallets-eco; released 2026-07-10; requires `python-dateutil`) | The Dec-2024 "unmaintained, may be removed from PyPI" notice (Buildbot, Salt, Airflow discussions) is stale — the project has since had new releases — but its governance is unclear and it adds `python-dateutil`. Viable fallback if `cronsim` is rejected `[CITED: github.com/pallets-eco/croniter, pypi.org/project/croniter]` |
| `cronsim` | In-house 5-field parser (~60-80 lines) | No dependency, but owns DOM/DOW OR-semantics, ranges/steps/names, Sunday 0/7, "never fires" detection; correctness risk for a demo-critical path. Not recommended |
| Naive-local + `astimezone()` | `zoneinfo` / `tzlocal` | `zoneinfo` needs `tzdata` on Windows (fails here); `tzlocal` is one more dependency to map Windows zone -> IANA. Naive-local uses the OS zone rules directly |
| `RecordingSink` reuse of `_run_tool_rounds` | Extract a socket-free core out of `ws.py` | Extraction touches ~250 lines of tested chat code and would move symbols tests monkeypatch (`agent.ws.MAX_TOOL_ROUNDS`); higher regression risk for zero functional gain |
| `RecordingSink` reuse | Dedicated small loop in the new module | Re-implements the empty-retry / nudge / loop-detection / cap logic that was added over 8 quick tasks after real-model failures; would silently regress on local models |

**Installation:**
```bash
# requirements.txt (add under a new "SCHEDULER" heading, pinned like mcp==1.30.0)
cronsim==2.7              # 5-field cron next-occurrence (Day 18 scheduler)
pip install -r requirements.txt
```

**Version verification:** `pip index versions cronsim` -> latest 2.7; PyPI JSON: `requires_python >=3.10`, no `requires_dist`, upload 2025-10-21 `[VERIFIED: pip index / PyPI JSON, this session]`.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| cronsim | PyPI | ~5 yrs (0.1.1 on 2021-05-16; 2.7 on 2025-10-21) | not retrieved (PyPI JSON no longer exposes counts) | github.com/cuu508/cronsim (healthchecks.io author) | [OK] (`slopcheck scan requirements.txt`) | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none
Note: `cronsim` has no `postinstall`-equivalent (pure-Python sdist/wheel, no dependencies). Package name was discovered via WebSearch/cronsim GitHub README, then confirmed by registry + slopcheck; per the provenance rule it is tagged `[ASSUMED]`-adjacent for *popularity claims* but slopcheck-`[OK]` and inspected by execution, so the planner needs no `checkpoint:human-verify` beyond the user reviewing the `requirements.txt` diff (see Assumption A1).

## Architecture Patterns

### System Architecture Diagram

```
                     ┌──────────────── Browser (ui/static/app.js) ────────────────┐
                     │ #scheduler-panel  create modal  run-result modal  toasts    │
                     └───────┬──────────────────────────────┬─────────────────────┘
       REST /api/v1/scheduler/*  (cookie)        WS /ws/events (cookie+origin)  ▲ frames: run_started,
                     │                                       │                  │ run_finished, task_updated,
                     ▼                                       ▼                  │ task_deleted
   ┌──────────────────────────── Agent process (:8001) ─────────────────────────┴───────────────┐
   │  scheduler_api.py ──create/pause/resume/cancel/delete/run-now──┐                             │
   │  scheduler_tools.py (LLM tools via TOOL_REGISTRY/dispatcher) ──┤                             │
   │                                                                ▼                             │
   │                                              ┌──────── scheduler.py ────────┐                │
   │  lifespan: init_db → recover_orphaned_runs ─▶│ poll loop (tick ~1 s)        │                │
   │            → start loop (if SCHEDULER_ENABLED)│  SELECT due (active, next<=now)               │
   │                                              │  per due task, ONE transaction:                │
   │                                              │   overlap? = EXISTS running run                │
   │                                              │   UPDATE next_run_at WHERE next_run_at=old ─rowcount==1?──no→ skip (someone else)
   │                                              │   INSERT TaskRun(running | skipped)            │
   │                                              │  running → asyncio.create_task(execute_run)    │
   │                                              └───────────────┬───────────────┘                │
   │                                                              ▼                                │
   │   execute_run: Semaphore → asyncio.timeout(SCHEDULER_RUN_TIMEOUT)                             │
   │     headless.py: system prompt + user prompt                                                  │
   │       tools = allowlist{save_long_term_memory} + build_mcp_toolset(lazy auto-connect)         │
   │       first stream → ws._run_tool_rounds(_ToolTurn(RecordingSink, allowed_tools=…))           │
   │                         │                                  │                                  │
   │            llm_client.stream_chat ──▶ LM Studio    dispatch_tool_calls ─▶ MCP sessions (mcp_client registry)
   │     finalize: UPDATE TaskRun(status,result,error,tool_trace) ; task finalize ; hub.publish(user_id, frame)
   │                                                                                               │
   │   EventHub: dict[user_id → set[Queue]] ◀── publish (never blocks; drop-oldest)                │
   └───────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                   ▼
                       SQLite (WAL, busy_timeout=5000, FK ON): scheduledtask, taskrun
                       (+ partial UNIQUE index: one running run per task)
```

### Recommended Project Structure
```
agent/
├── schedule.py          # pure: ScheduleSpec validation, compute_next_run(), to/from local naive (cronsim)
├── scheduler.py         # SchedulerService: tick(), claim, recover_orphaned_runs(), execute_run(), start/stop, run_now()
├── headless.py          # run_headless_turn(): RecordingSink + reused ws helpers; HEADLESS_TOOL_ALLOWLIST
├── events.py            # EventHub (user_id → queues), ws_events() handler
├── scheduler_api.py     # APIRouter(prefix="/api/v1/scheduler") + response mappers (UTC-aware ISO)
├── scheduler_tools.py   # register_tool: schedule_task / list_scheduled_tasks / cancel_scheduled_task + cancel gate
├── schemas.py           # + ScheduleTaskArgs, CancelScheduledTaskArgs, ListScheduledTasksArgs, Scheduler* request/response models
├── tools.py             # dispatch_tool_calls(..., allowed_tools=None)   [surgical]
├── ws.py                # _ToolTurn.allowed_tools; _collect_memory_writes exclusion; current_chat_model set  [surgical]
└── main.py              # include_router; app.websocket("/ws/events"); lifespan start/stop
shared/
├── models.py            # + ScheduledTask, TaskRun (+ enums, partial index)
└── config.py            # + SCHEDULER_* settings
ui/static/{index.html,app.js}   # panel, two modals, /ws/events client
tests/                   # test_scheduler_{schedule,service,runner,api,tools,events}.py
```

### Data model (Q5) — no ALTER migration needed

`init_db()` runs `SQLModel.metadata.create_all`, and `shared/database.py` already imports `shared.models`, so **new tables appear automatically at startup** (existing `migrate_*` helpers exist only for adding columns to *pre-existing* tables) `[VERIFIED: shared/database.py:224-232]`. Default table names are the lowercased class names: `scheduledtask`, `taskrun` — no clash with the existing `task` / `tasktransition`. With `PRAGMA foreign_keys=ON` set on every connection (`_set_sqlite_pragma`), `ondelete` cascades work.

Suggested columns (names are discretionary):

- **`ScheduledTask`**: `id`; `user_id` (Integer, FK `user.id` **CASCADE**, NOT NULL, index); `origin_chat_id` (Integer NULL, FK `chat.id` **SET NULL**); `title` (<=200); `prompt` (Text, <=4000); `model` (<=200); `schedule_type` (`once|interval|cron`); `run_at` (UTC, once), `interval_seconds` (int), `cron_expr` (str<=100) — only the field of the type is set; `max_runs` (int NULL), `run_count` (int, default 0); `next_run_at` (UTC NULL); `status` (`active|paused|completed|cancelled`); `created_at`, `updated_at`. Composite index `(status, next_run_at)` for the poll query.
- **`TaskRun`**: `id`; `scheduled_task_id` (FK `scheduledtask.id` **CASCADE**, NOT NULL, index); `user_id` (FK `user.id` CASCADE — denormalised so events/list queries are user-scoped without a join); `status` (`running|success|failed|skipped`); `trigger` (`schedule|manual`); `scheduled_for` (UTC slot), `started_at`, `finished_at` (UTC NULL); `is_late` (bool); `model`; `result_text` (Text NULL), `error` (Text NULL), `tool_trace` (Text NULL, JSON from `serialize_tool_trace`).
- **Invariant index** on `TaskRun` (works with `create_all`, verified):
  ```python
  __table_args__ = (
      Index("uq_taskrun_one_running", "scheduled_task_id", unique=True,
            sqlite_where=text("status = 'running'")),
  )
  ```
  Follow the repo's enum convention for `status`/`schedule_type` (`SAEnum(..., values_callable=...)` like `TaskState`) **or** plain `str` columns validated in Python — both work; `SAEnum` keeps consistency, plain `str` avoids future enum-migration friction. Pick one and be consistent. Timestamps: SQLite stores naive UTC (`'2026-09-26 10:58:43.329737'`); binding an aware UTC datetime and comparing an aware value to a stored naive one both work `[VERIFIED: local prototype]`.

### Pattern 1: Atomic slot claim + run insert in ONE transaction (Q3)
**What:** For each due task, decide `running` vs `skipped`, advance `next_run_at`, bump `run_count`, and insert the run row in a single commit. `rowcount != 1` means another actor already consumed the slot.
**When:** every scheduled tick; "Run now" uses the same run-insert path but does **not** touch `next_run_at`.
```python
# Source: prototype verified on SQLModel 0.0.42 / SQLAlchemy 2.0.52 / aiosqlite 0.22.1
from sqlalchemy import update
from sqlmodel import select

async def claim_slot(session: AsyncSession, task: ScheduledTask, now: datetime) -> TaskRun | None:
    old_next = task.next_run_at
    overlap = (await session.exec(
        select(TaskRun.id).where(TaskRun.scheduled_task_id == task.id, TaskRun.status == RunStatus.RUNNING)
    )).first() is not None
    counts = not overlap
    new_next = compute_next_after_claim(task, old_next, now, counts_toward_max=counts)  # None => job finished
    result = await session.exec(
        update(ScheduledTask)
        .where(ScheduledTask.id == task.id,
               ScheduledTask.status == TaskStatus.ACTIVE,
               ScheduledTask.next_run_at == old_next)          # optimistic guard
        .values(next_run_at=new_next,
                run_count=ScheduledTask.run_count + (1 if counts else 0),
                updated_at=now)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    lag = (now - _aware(old_next)).total_seconds()
    run = TaskRun(scheduled_task_id=task.id, user_id=task.user_id, trigger="schedule",
                  status=RunStatus.SKIPPED if overlap else RunStatus.RUNNING,
                  scheduled_for=old_next, started_at=now,
                  finished_at=now if overlap else None,
                  is_late=lag > settings.SCHEDULER_LATE_THRESHOLD_SECONDS,
                  error="Предыдущий запуск ещё выполнялся" if overlap else None, model=task.model)
    session.add(run)
    try:
        await session.commit()      # partial unique index is the backstop against a racing manual run
    except IntegrityError:
        await session.rollback()
        return None                 # caller re-tries once as `skipped`
    return run
```
`compute_next_after_claim`: `once` -> `None`; `interval` -> **anchored** `old + interval * (floor((now-old)/interval) + 1)` (keeps cadence, collapses a downtime backlog into one compensating run — D-08); `cron` -> next match strictly after `now` (never after `old`, so no storm); if `counts` and `max_runs` and `run_count + 1 >= max_runs` -> `None`.
**Finalize invariant:** an `active` task whose `next_run_at IS NULL` and which has no `running` run is `completed`. After each run finishes (and in startup recovery) apply
`UPDATE scheduledtask SET status='completed' WHERE id=? AND status='active' AND next_run_at IS NULL` (guarded by `NOT EXISTS running`). This single rule covers one-shot completion, `max_runs` completion, and a crash between claim and finish.
**Multiple due tasks per tick:** claim each in its own short transaction; keep the tick's SELECT `LIMIT` modest (e.g. 20) so a backlog cannot starve the loop.

### Pattern 2: Poll loop lifecycle (Q3)
```python
class SchedulerService:
    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._run_loop(), name="scheduler-loop")
    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:                       # loop must never die
                logger.error("scheduler_tick_failed", error=type(exc).__name__)
            await asyncio.sleep(settings.SCHEDULER_POLL_INTERVAL)
    async def stop(self) -> None:
        self._loop_task.cancel(); await asyncio.gather(self._loop_task, return_exceptions=True)
        for t in list(self._runs): t.cancel()              # in-flight runs
        await asyncio.gather(*self._runs, return_exceptions=True)
```
- Keep **strong references** to every `asyncio.create_task` (a `set` with `add_done_callback(discard)`) or tasks can be garbage-collected mid-run.
- `lifespan` order: `init_db()` -> `recover_orphaned_runs()` (always) -> `scheduler.start()` if `SCHEDULER_ENABLED`; shutdown: `scheduler.stop()` -> `mcp_client.cleanup_all_sessions()` -> `engine.dispose()` (scheduler **before** dispose).
- Tick interval: `SCHEDULER_POLL_INTERVAL = 1.0` s. Cost is one indexed SELECT/s on a local SQLite — negligible. Optionally an `asyncio.Event` "wake" set by create/resume so a "через 5 секунд" job does not wait up to a tick (nice-to-have).
- Make time injectable: `tick(now: datetime | None = None)` so tests never sleep.

### Pattern 3: Startup recovery (Q3, D-08)
`recover_orphaned_runs()` runs **before** the loop starts:
1. `UPDATE taskrun SET status='failed', finished_at=:now, error='Прервано: Agent был перезапущен' WHERE status='running'` (single process => any `running` row at startup is orphaned).
2. Apply the finalize invariant to all tasks (one-shot claimed but killed => `completed`; no retries per D-10).
3. Do **not** special-case catch-up: overdue `next_run_at` rows are picked up by the first tick; the claim advances them to the next *future* slot and flags `is_late` (D-08). `ui/supervisor.py:115-124` uses `terminate()` then `kill()`; on Windows `terminate()` is a hard `TerminateProcess`, so shutdown hooks are best-effort only `[ASSUMED: Python subprocess semantics; code path VERIFIED in ui/supervisor.py]`.
Resume of a **paused** periodic job should recompute `next_run_at` from now (no catch-up for pause time); resume of a paused **once** job with a past `run_at` fires on the next tick flagged late (recommendation, planner may adjust).

### Pattern 4: Headless runner — `RecordingSink` + reused chat helpers (Q2, Q6)
**Decision:** do **not** extract a core and do **not** write a parallel loop. Reuse `agent.ws` helpers through a duck-typed sink. Verified prototype (real `agent.ws`, mocked LM Studio via `respx`): first stream returned a `save_long_term_memory` tool call, `_run_tool_rounds` dispatched it, the `LongTermMemory` row appeared for the correct `user_id`, and the follow-up produced the final text.
```python
# agent/headless.py  (sketch; Source: verified prototype, agent/ws.py internals)
from types import SimpleNamespace
from agent import ws
from agent.mcp_tools import McpToolset, build_mcp_toolset
from agent.tools import TOOL_REGISTRY, build_tool_schemas

HEADLESS_CHAT_ID = 0                                   # save_long_term_memory ignores chat_id (agent/tools.py:284-292)
HEADLESS_TOOL_ALLOWLIST = frozenset({"save_long_term_memory"})   # allowlist, not denylist: new tools are excluded by default

class RecordingSink:
    """Duck-typed stand-in for a WebSocket: records frames instead of sending them."""
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
    async def send_json(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

async def run_headless_turn(session, user_id, prompt, model, temperature, max_tokens) -> HeadlessResult:
    toolset = await _safe_mcp_toolset(session, user_id)              # try/except like ws._load_mcp_toolset
    schemas = [s for s in build_tool_schemas() if s["function"]["name"] in HEADLESS_TOOL_ALLOWLIST] + toolset.schemas
    messages = [{"role": "system", "content": _system_prompt(...)},   # see below
                {"role": "user", "content": prompt}]
    await session.commit()                                            # end read txn: don't hold a pooled connection/snapshot for up to 120 s
    turn = ws._ToolTurn(websocket=RecordingSink(), session=session,
                        chat=SimpleNamespace(user_id=user_id), chat_id=HEADLESS_CHAT_ID,
                        payload=SimpleNamespace(model=model), llm_messages=messages,
                        tool_schemas=schemas, toolset=toolset,
                        temperature=temperature, max_tokens=max_tokens,
                        allowed_tools=HEADLESS_TOOL_ALLOWLIST)        # new field, default None in chat
    acc = ws._ToolRoundsResult()
    text, calls = await ws._stream_follow_up_with_empty_retry(turn, schemas or None, acc)   # first LLM call
    if not calls and schemas:
        kind = ws._pick_nudge(acc, text, [])                           # one "announce" nudge, same as chat
        if kind is not None:
            text, calls = await ws._stream_nudge(turn, schemas, acc, text, kind)
    rounds = ws._ToolRoundsResult()
    if calls:
        rounds = await ws._run_tool_rounds(turn, calls, text)          # MAX_TOOL_ROUNDS, empty-retry, error nudge, loop detection
        if rounds.error is not None: raise rounds.error
    final = (acc.text + rounds.text).strip()
    if not final and rounds.results:
        final = build_tool_fallback_summary(rounds.results, prompt)    # same fallback as chat
    return HeadlessResult(text=final, results=rounds.results, trace=serialize_tool_trace(rounds.results))
```
Why this satisfies "without changing chat behavior":
- `_run_tool_rounds` and friends are **untouched**; the only differences are three default-preserving additions: `_ToolTurn.allowed_tools: frozenset[str] | None = None`, `_dispatch_round` passing `allowed_tools=turn.allowed_tools`, and `dispatch_tool_calls(..., allowed_tools=None)` treating a non-matching non-MCP name as `unknown tool` (`ok=False`). No existing test fakes `dispatch_tool_calls`, so the extra kwarg is safe `[VERIFIED: grep tests]`.
- `agent.ws.MAX_TOOL_ROUNDS` is still the module constant the loop reads (tests monkeypatch it).
- Type annotations: optionally widen `_ToolTurn.websocket` to a small `Protocol` with `send_json` (typing only).
- Streaming with tools is the **only** tool-capable path: `LLMClient.complete_chat` takes no `tools=`. So the runner consumes `stream_chat` events (already handled inside `_stream_follow_up`).
- Using underscore-prefixed names across modules is a deliberate, commented coupling; add a module-level comment in `headless.py` and a unit test that fails loudly if a symbol disappears. (Alternative: re-export them from `agent/tool_guard.py`-style public names — not needed.)

**Headless system prompt** (English like the other prompts): `Settings.system_prompt` of the user's **global** row (fallback `"You are a helpful assistant."`; read-only — do **not** create the row) + a preface `"You are running as an unattended scheduled job. No user is present: never ask questions or wait for confirmation. Complete the task with the available tools, then finish with a concise report of the result."` + (recommended for parity) the user's long-term memory JSON (`memory.list_long_term_memory`) + `build_clock_line(datetime.now(timezone.utc).astimezone())` + (if tools) `MULTI_STEP_TOOL_HINT` + `TOOL_USE_RULE` **last** (so `strip_tool_use_rule` still works after round 1). `build_system_prompt(session, chat_id)` cannot be reused — it needs a chat.
**Sampling:** temperature/max_tokens from the user's global `Settings` (`chat_id IS NULL AND user_id == uid`); fall back to `Settings()` defaults if no row.
**Errors -> `failed` messages** (Russian, shown verbatim in the UI): `httpx.ConnectError` -> `Модель недоступна: LM Studio не запущен`; `httpx.HTTPStatusError` -> `Модель недоступна: HTTP <code>` (LM Studio returns 4xx when the model id is unknown/unloaded); `httpx.TimeoutException` -> `Тайм-аут ответа модели`; `TimeoutError` from `asyncio.timeout` -> `Превышено время выполнения (120 с)` (exact copy from 08-UI-SPEC); anything else -> `Ошибка выполнения: <ExcType>` (never dump tracebacks/prompts into logs; the stored `error` may contain `str(exc)`).
**Empty answer:** if there is no final text and no tool results, mark the run `failed` (`Модель вернула пустой ответ`) rather than `success` with nothing to show (recommendation).
**Session hygiene:** the run gets its **own** `async_session_factory()` session; bookkeeping (insert/finish run rows) uses separate short sessions so a slow LLM call never holds a write lock. `expire_on_commit=False` is already configured.

### Pattern 5: Per-user event hub + `/ws/events` (Q4)
```python
# agent/events.py
class EventHub:
    """user_id -> outbound queues; publish never blocks the scheduler."""
    def __init__(self) -> None: self._subs: dict[int, set[asyncio.Queue[dict]]] = {}
    def subscribe(self, user_id: int) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        self._subs.setdefault(user_id, set()).add(q); return q
    def unsubscribe(self, user_id: int, q) -> None:
        s = self._subs.get(user_id); 
        if s: s.discard(q); 
        if s is not None and not s: self._subs.pop(user_id, None)
    def publish(self, user_id: int, frame: dict) -> None:
        for q in list(self._subs.get(user_id, ())):
            if q.full(): q.get_nowait()          # drop oldest; client re-syncs via REST anyway
            q.put_nowait(frame)
hub = EventHub()
```
Handler (mirrors `ws_chat` pre-accept checks; reuse `ws._validate_origin` and `get_current_user_ws`):
```python
async def ws_events(websocket: WebSocket) -> None:
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed"); return
    async with async_session_factory() as db:
        user = await get_current_user_ws(websocket.cookies.get(SESSION_COOKIE_NAME), db)
    if user is None:
        await websocket.close(code=1008, reason="Unauthorized"); return
    await websocket.accept()
    queue = hub.subscribe(user.id)
    pump = asyncio.create_task(_pump(websocket, queue))
    try:
        while not pump.done():
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=60)   # CLAUDE.md rule; client pings every ~25 s
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel(); hub.unsubscribe(user.id, queue)
```
- **Why a per-connection queue + writer task:** concurrent `send_json` on one Starlette socket from the scheduler task, a REST handler and a tool handler is not something to rely on; one writer per socket serialises sends, and `put_nowait` means a slow/dead browser can never stall `execute_run`. `_pump` catches send errors and ends.
- Do **not** touch `ws.active_connections` / `broadcast_model_event` (ownerless).
- Register in `main.py`: `@app.websocket("/ws/events")` -> `ws_events`.
- **Frames** (all `type`-discriminated JSON; run objects never carry `result_text`, to keep frames small — the modal fetches `GET /runs/{id}`):
  - `{"type":"run_started","task_id":7,"run":{"id":31,"status":"running","trigger":"schedule","started_at":"…Z","is_late":false}}`
  - `{"type":"run_finished","task_id":7,"run":{"id":31,"status":"success","started_at":"…Z","finished_at":"…Z","duration_ms":12034,"is_late":false},"task":{<TaskOut snapshot>}}` (`title` is inside `task`, used for the toast)
  - `{"type":"task_updated","task":{<TaskOut>}}` (created / paused / resumed / cancelled / completed / run_count changed)
  - `{"type":"task_deleted","task_id":7}`
  - `{"type":"ping"}` from server on idle if desired.
  Publish from: executor (`run_started`/`run_finished`), REST handlers and tool handlers (`task_updated`/`task_deleted`), skip events (`run_finished` with `status:"skipped"` — UI shows no toast).
- Session revocation: the socket authenticates at handshake only (same as chat). Optional hardening: on logout with no live web session, close that user's event sockets alongside `mcp_client.cleanup_user_sessions`.
- **UI:** reuse `STOP_RECONNECT_CODES` (1008 stops reconnect), exponential backoff capped at `MAX_RECONNECT_DELAY`; do **not** call `showToast('Ошибка WebSocket')` from this socket's `onerror` (chat socket does; UI-SPEC says no offline banner); on every `onopen` call `GET /api/v1/scheduler/tasks`; start the 10 s poll only while disconnected **and** panel expanded. Open the socket in `init()` after `checkAgentHealth()`; the panel's own fold toggle already works through `setupFoldablePanels()`.

### Pattern 6: REST surface (Q4/Q5) — `APIRouter` in `agent/scheduler_api.py`
`main.py` is 1233 lines and uses no routers; a router keeps the phase self-contained (`app.include_router(scheduler_router)`). Conventions to copy from MCP routes: `Depends(get_current_user)`, `Depends(get_session)`, mutating routes with `dependencies=[Depends(require_allowed_origin)]`, JSON POST/PUT also `Depends(require_json_content_type)`, foreign id -> **404**.

| Method + path | Body | Response |
|---|---|---|
| `GET /api/v1/scheduler/tasks` | — | `list[TaskOut]` (all non-deleted, active/paused first; each with `last_run` summary + `is_running`) |
| `POST /api/v1/scheduler/tasks` | `title, prompt, model, schedule_type, delay_seconds \| run_at \| interval_seconds \| cron, max_runs?` | 201 `TaskOut` (422 with Russian `detail` for the UI copy cases) |
| `GET /api/v1/scheduler/tasks/{id}` | — | `TaskOut` |
| `POST /tasks/{id}/pause` \| `/resume` \| `/cancel` | — | `TaskOut` (409 on illegal status) |
| `POST /tasks/{id}/run` | — | 202 `RunSummary` (409 if a run is already `running`) |
| `DELETE /tasks/{id}` | — | 204 (aborts an in-flight run first, then deletes; runs cascade) |
| `GET /tasks/{id}/runs?limit=20` | — | `list[RunSummary]` newest first (no `result_text`) |
| `GET /runs/{run_id}` | — | `RunDetail` (`result_text`, `error`, `tool_trace` parsed) |

Validation (server is the source of truth; matches UI-SPEC copy): title 1-200, prompt 1-4000; `interval_seconds >= SCHEDULER_MIN_INTERVAL_SECONDS (10)`; `delay_seconds >= 1`; `run_at` must be in the future (`Время запуска уже прошло`); cron must be exactly 5 fields **and** have a next occurrence (`Некорректное cron-выражение. Нужно 5 полей: минута час день месяц день_недели.`); `max_runs >= 1`, ignored for `once`; cap active+paused jobs per user (`SCHEDULER_MAX_ACTIVE_TASKS_PER_USER`, e.g. 50). **Serialise every datetime as UTC-aware ISO** (`dt.replace(tzinfo=timezone.utc).isoformat()` when naive): existing responses return naive strings, and `new Date("2026-09-26T10:58:43")` in JS parses as *local* time — the UI-SPEC requires UTC ISO. Add the new routes to `tests/test_scoping.py::UNAUTH_ROUTES`.

### Pattern 7: LLM tools (Q6)
Register in `agent/scheduler_tools.py` via `agent.tools.register_tool` (import that module from `agent/main.py` so the registry is populated wherever the app is). Arg models live in `agent/schemas.py` next to `SaveLongTermMemoryArgs`; use `Field(description=...)` so the model sees guidance, `Literal["once","interval","cron"]` for `schedule_type`, and a `model_validator(mode="after")` enforcing exactly the per-type fields (once: exactly one of `delay_seconds` / `run_at`; interval: `interval_seconds`; cron: `cron`). Handlers return `{"status": "scheduled", "id":…, "next_run_at": <UTC iso>, …}` or `{"status":"error","code":…,"error":…}` (the dispatcher treats `status == "error"` as failure).
- **Model in context (D-03):** the handler signature `(session, user_id, chat_id, args)` does not carry the chat's model and no DB field stores it (`Message`/`Settings` have no model column). Add `current_chat_model: ContextVar[str | None]` (in `agent/state.py`) and set it in `_handle_chat_message` (`current_chat_model.set(payload.model)`, one line, near the top). `_run_tool_rounds` is awaited inline in the same task, so the value is visible to the handler. If unset (e.g. direct `dispatch_tool_calls` in tests) the handler returns a clear error; tests set the var. The UI form passes `model` explicitly.
- `origin_chat_id = chat_id` from the handler arg (D-13). `run_at` accepted as ISO: naive -> machine-local, with offset -> honoured; converted to UTC.
- **Pitfall — `memory_writes`:** `dispatch_tool_calls` gives **every** successful non-MCP result a `write` dict, and `ws._collect_memory_writes` keeps all of them except `TASK_TOOL_NAMES`; scheduler results would appear in the `done` frame's `memory_writes` with `layer: None`. Extend the exclusion to the three scheduler tool names (`NON_MEMORY_TOOL_NAMES = TASK_TOOL_NAMES + SCHEDULER_TOOL_NAMES`). `app.js` does not consume `memory_writes` today, but the field is part of the WS contract.
- **Existing tests that will change:** `tests/test_mcp_tools.py:333` asserts `len(build_tool_schemas()) == 6` -> becomes 9. `tests/test_tasks.py::test_cancel_is_not_an_llm_tool` asserts no tool property `enum` contains `"cancelled"` -> do **not** give `list_scheduled_tasks` a `status` enum with `cancelled`. `test_mcp_chat_ws.py` compares tool names to `set(TOOL_REGISTRY)` dynamically, so it adapts.
- **Cancel gate (D-14), recommended mechanism (three layers):** (1) tool description: "Cancel a scheduled job ONLY when the user explicitly asked to cancel/stop/delete it in their latest message; never cancel on your own initiative; requires the explicit numeric task_id (call list_scheduled_tasks first if unknown)". (2) Required arg `user_requested_cancellation: bool` (must be `true`; a cheap speed-bump the model must consciously set). (3) **Code guard**: the handler loads the chat's current leaf (`Chat.current_leaf_message_id`, which at tool time is the just-persisted **user** message — `_persist_user_message` commits before the LLM call), takes its text and requires a cancel-intent match (stems such as `отмен`, `останов`, `прекрат`, `удал`, `убер`, `выключ`, `cancel`, `stop`, `delete`, `remove`, `disable`; same style as the project's `looks_like_action_claim` heuristics in `agent/tool_guard.py`). No match -> `{"status":"error","code":"cancel_not_requested","error":"The user did not ask to cancel a job in their latest message."}` so the model relays it. Also verify the task belongs to `user_id` and is `active|paused` (else `not_found`). UI/REST cancel bypasses the gate (user action). This heuristic is intentionally the same trade-off backlog 999.1 will make for `merge_merge_request`; keep the helper (`user_asked_to_cancel(text)`) small and unit-tested so Day 20 can reuse it.

### Pattern 8: `chat_id` for `save_long_term_memory` (Q6)
The handler ignores `chat_id` entirely (`memory.save_long_term_memory(session, user_id, key, value)`), and the dispatcher only uses it in a log line — verified by reading `agent/tools.py:284-292` and by the prototype (`chat_id=0`). Use a module constant `HEADLESS_CHAT_ID = 0`. Safety net is the allowlist, because any chat-bound handler reached with `chat_id=0` would violate `chat.id` FKs.

### Anti-Patterns to Avoid
- **Reusing `ws.active_connections` / `broadcast_model_event` for job events** — ownerless; would leak run results across users.
- **Filtering only the schema list and trusting the model** — the dispatcher executes any registered name; use the `allowed_tools` guard too.
- **Holding one `AsyncSession` (open transaction) for the whole run** — with WAL a long read snapshot blocks checkpointing and ties up a pooled connection for up to 120 s; commit before the LLM call and use short sessions for bookkeeping.
- **Computing next cron time with an aware system-tz `datetime` + `zoneinfo`** — no tz database on Windows; use naive local + `astimezone()`.
- **Advancing `next_run_at` from the old slot by one step after downtime** — produces a storm of N catch-ups (violates D-08); always compute from `now`.
- **Serialising naive datetimes to the browser** — JS treats them as local time.
- **Starting the poll loop unconditionally** — `with TestClient(app):` (≈40 tests) runs the lifespan; a live loop in those tests is noise and a flake source.
- **`asyncio.create_task` without keeping a reference** — tasks can be GC'd; keep a set.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cron field parsing / next occurrence (lists, ranges, steps, names, DOM/DOW OR rule, `L`) | Custom parser | `cronsim==2.7` | Verified: DOM/DOW OR semantics, `7`==Sunday, `L`, bad-field errors, "never fires" -> `StopIteration` |
| Tool loop with empty-retry, nudges, loop detection, round cap | A second copy in `headless.py` | `agent.ws._run_tool_rounds` + helpers via `RecordingSink` | Those behaviours came from real-model regressions (quick tasks 260925-q0s…260926-38j) |
| MCP toolset + lazy connect + sequential dispatch | New MCP plumbing | `build_mcp_toolset`, `dispatch_tool_calls(mcp_bindings=…)` | Registry, locks, timeouts already exist |
| One-running-run-per-job exclusion | In-memory locks/dicts | Partial unique index + `IntegrityError` handling | Survives races between tick and "Run now"; no state to clean up |
| Atomic "claim" | Select-then-update in app code | `UPDATE … WHERE next_run_at = old` + `rowcount` | Standard optimistic concurrency; verified on aiosqlite |
| Run timeout | Manual timers | `asyncio.timeout()` (3.11+) | Cancels the inner stream cleanly; `LLMClient.stream_chat` already closes the response on `CancelledError` |
| WS auth + origin | New checks | `ws._validate_origin`, `get_current_user_ws`, `SESSION_COOKIE_NAME` | Identical trust model to `ws_chat` |
| Markdown rendering / sanitising in UI | Custom renderer | `renderMarkdown()` (Marked + DOMPurify) | Project rule |
| Toasts, fold toggles, confirm dialogs | New widgets | `showToast`, `data-fold-toggle` + `setupFoldablePanels`, native `confirm()` | Per UI-SPEC |

**Key insight:** every hard piece already exists in the repo in a tested form; the phase is mostly *wiring* plus three small invariants (atomic claim, overlap index, allowlist). New code that re-implements tool-loop or MCP behaviour is where regressions will come from.

## Common Pitfalls

### Pitfall 1: Allowlisted schemas but unrestricted dispatch
**What goes wrong:** A local model emits a native `tool_calls` entry for `create_task` / `schedule_task` (or the text-call recovery invents one); `dispatch_tool_calls` runs it because the name is in `TOOL_REGISTRY`. With `chat_id=0` it raises an FK `IntegrityError` mid-run, or a job spawns jobs (violates D-04).
**How to avoid:** `allowed_tools` param (default `None` in chat) + test that a hallucinated `create_task` yields `ok=False` "unknown tool" and writes nothing.
**Warning signs:** `IntegrityError` in scheduler logs; `TaskRun` with tool trace containing `schedule_task`.

### Pitfall 2: Agent killed hard -> runs stuck `running` forever
**What goes wrong:** Supervisor `terminate()` on Windows = hard kill; lifespan shutdown never runs; the partial unique index then blocks every future run of that job (overlap forever).
**How to avoid:** `recover_orphaned_runs()` at every startup **before** the loop (single-process assumption holds: one uvicorn worker, no `--workers`, per `ui/supervisor.py:97-106`).

### Pitfall 3: Catch-up storm / drift
**What goes wrong:** `next = old + interval` after a 1-day outage yields ~8640 immediate runs.
**How to avoid:** compute from `now` (anchored formula for interval, cron from `now`), one compensating run, `is_late = lag > SCHEDULER_LATE_THRESHOLD_SECONDS (60)`.

### Pitfall 4: `cronsim` accepts 6 fields and mis-reads shortcuts
**What goes wrong:** `* * * * * *` (seconds) parses and would fire every second; `@daily` raises. `0 0 31 2 *` raises `CronSimError`; a syntactically valid but never-matching expression raises `StopIteration` after scanning 50 years `[VERIFIED: local execution]`.
**How to avoid:** `len(expr.split()) == 5` check first; call `next(CronSim(...))` inside `try/except (CronSimError, StopIteration)` at create time and reject.

### Pitfall 5: Local-time semantics and DST
**What goes wrong:** cron in machine-local time across DST gaps/overlaps.
**How to avoid:** compute in naive local wall-clock, convert with `naive.astimezone(timezone.utc)` (system zone rules, no tz database). Documented rule: nonexistent local times shift per OS conversion; repeated hour fires once (cronsim advances from the post-fire time). Provide an injectable `tz` parameter in helpers so tests use a fixed-offset `timezone(timedelta(hours=3))`. DST correctness of `naive.astimezone()` on Windows is `[ASSUMED]` (dev zone has no DST).

### Pitfall 6: Naive datetimes to the browser
SQLite returns naive datetimes; Pydantic serialises them without `Z`; JS `new Date()` reads them as local. Always emit aware UTC (`+00:00`/`Z`). Add a response-mapper unit test asserting the suffix.

### Pitfall 7: The lifespan runs in ~40 existing tests
`with TestClient(app):` executes `lifespan`; a poll loop in a per-test event loop, or one that outlives `engine.dispose()` and the deleted test DB, is a flake source. **Fix:** `SCHEDULER_ENABLED` setting (default `True`), and in `tests/conftest.py` add `os.environ.setdefault("SCHEDULER_ENABLED", "false")` next to `DB_PATH`. Scheduler tests call `tick(now)` / `execute_run(run_id)` directly or `monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)` for the one lifespan-wiring test. ASGITransport-based fixtures (`client`, `authenticated_client`) do not run lifespan at all.

### Pitfall 8: MCP servers may be unavailable at 3 a.m.
Logout with no live web session closes the user's MCP sessions (`main.py` logout handler). A later scheduled run relies on lazy auto-connect (`MCP_AUTO_CONNECT`) — but `_auto_connect_missing` skips servers with a **recorded failure** (`has_recorded_failure`), so a run can start with zero MCP tools and the model can only say it cannot do the task. Record `mcp_tool_count` (and a note when zero) in the run's tool trace/error text or log so the failure is diagnosable; treat clearing recorded failures as out of scope (open question Q2).

### Pitfall 9: Deleting a job with an in-flight run
The executor would later `UPDATE` a run row that no longer exists. Keep `run_id -> asyncio.Task` in the service; `DELETE` aborts the task (`cancel()` + await) before deleting; the finalizer must tolerate a missing row (`session.get(...) is None` -> return). Soft **cancel** (D-14/D-16) does *not* abort an in-flight run (recommendation: let it finish and record its result; only future slots stop).

### Pitfall 10: `_collect_memory_writes` treats scheduler tool results as memory writes
See Pattern 7 — extend the exclusion tuple in `agent/ws.py`.

### Pitfall 11: Existing tool-count/enum tests
`test_mcp_tools.py:333` (`== 6`) and `test_tasks.py::test_cancel_is_not_an_llm_tool` (see Pattern 7).

### Pitfall 12: DeepSeek vs LM Studio
`agent/llm_client.py` builds one module-level `llm_client` with `base_url=settings.LM_STUDIO_BASE_URL` (API key from `DEEPSEEK_API_KEY` only as a bearer header). There is no per-model backend routing today, so "model unavailable" in practice means LM Studio down / model id unknown. Do not invent routing in this phase.

## Code Examples

### Local-time cron -> UTC (Q1)
```python
# agent/schedule.py — Source: cronsim README (github.com/cuu508/cronsim) + local execution 2026-09-26
from datetime import datetime, timedelta, timezone, tzinfo
from cronsim import CronSim, CronSimError

def _to_local_naive(dt_utc: datetime, tz: tzinfo | None = None) -> datetime:
    return dt_utc.astimezone(tz).replace(tzinfo=None)          # tz=None -> OS local zone

def _from_local_naive(naive: datetime, tz: tzinfo | None = None) -> datetime:
    aware = naive.astimezone() if tz is None else naive.replace(tzinfo=tz)   # naive.astimezone(): "presumed system tz"
    return aware.astimezone(timezone.utc)

def validate_cron(expr: str) -> str:
    fields = expr.split()
    if len(fields) != 5:                                        # cronsim would accept 6 (seconds)
        raise ValueError("cron must have exactly 5 fields")
    normalized = " ".join(fields)
    try:
        next(CronSim(normalized, datetime(2000, 1, 1)))
    except (CronSimError, StopIteration) as exc:                # StopIteration: never fires within 50 years
        raise ValueError("invalid cron expression") from exc
    return normalized

def next_cron_run(expr: str, after_utc: datetime, tz: tzinfo | None = None) -> datetime:
    base = _to_local_naive(after_utc, tz)
    return _from_local_naive(next(CronSim(expr, base)), tz)      # strictly after `after_utc`

def next_interval_run(anchor_utc: datetime, interval_seconds: int, now_utc: datetime) -> datetime:
    steps = int((now_utc - anchor_utc).total_seconds() // interval_seconds) + 1
    return anchor_utc + timedelta(seconds=interval_seconds * steps)   # keeps cadence, collapses backlog
```
Observed: `next(CronSim("*/5 * * * *", datetime(2026,9,26,10,5,0)))` -> `10:10:00` (strictly after); `0 0 13 * fri` -> Fridays **and** 13ths (Vixie OR rule); `0 12 * * 7` == `0 12 * * 0`; `L` supported; `61 * * * *`/`0 25 * * *`/`5-1 * * * *`/`*/0 * * * *` -> `CronSimError`.

### Overlap invariant + tests hooks
```python
# shared/models.py
from sqlalchemy import Index, text
class TaskRun(SQLModel, table=True):
    __table_args__ = (
        Index("uq_taskrun_one_running", "scheduled_task_id", unique=True,
              sqlite_where=text("status = 'running'")),
    )
```
Emitted DDL (verified): `CREATE UNIQUE INDEX uq_taskrun_one_running ON taskrun (scheduled_task_id) WHERE status = 'running'`; two `skipped` rows and `running` rows of other tasks insert fine; a second `running` row for the same task raises `IntegrityError`. If `status` is an `SAEnum`, confirm the stored literal is the lowercase value (use `values_callable`) so the `WHERE status = 'running'` predicate matches.

### Execute one run (skeleton)
```python
async def execute_run(self, run_id: int) -> None:
    async with self._semaphore:                                   # SCHEDULER_MAX_CONCURRENT_RUNS (default 2)
        outcome = await self._run_with_timeout(run_id)            # inside: async with asyncio.timeout(settings.SCHEDULER_RUN_TIMEOUT)
    await self._finish_run(run_id, outcome)                       # short session: UPDATE run, finalize task, hub.publish
```
Acquire the semaphore **before** starting the timeout so queue wait does not eat the 120 s budget (local LM Studio serves one request at a time; unlimited parallel runs only queue up on the GPU).

### New settings (`shared/config.py`)
```python
SCHEDULER_ENABLED: bool = True
SCHEDULER_POLL_INTERVAL: float = 1.0
SCHEDULER_RUN_TIMEOUT: float = 120.0
SCHEDULER_MIN_INTERVAL_SECONDS: int = 10
SCHEDULER_LATE_THRESHOLD_SECONDS: float = 60.0
SCHEDULER_MAX_CONCURRENT_RUNS: int = 2
SCHEDULER_MAX_ACTIVE_TASKS_PER_USER: int = 50
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `croniter` as the default Python cron lib | Ecosystem split: `croniter` (Dec 2024 abandonment notice, later new releases under pallets-eco, 6.2.4 on 2026-07-10) vs. `cronsim` (healthchecks.io, 2.7 on 2025-10-21) | Dec 2024 - 2026 | Governance of `croniter` unclear; `cronsim` is dependency-free — prefer it |
| `session.execute(update(...))` on SQLModel sessions | `session.exec(update(...))` (no deprecation warning, `CursorResult.rowcount`) | SQLModel 0.0.4x | Use `exec` for the claim `[VERIFIED on 0.0.42; floor 0.0.22 unverified]` |
| `asyncio.wait_for` for every timeout | `asyncio.timeout()` context manager | Python 3.11 | Already the project's declared floor (`requirements.txt` header) |

**Deprecated/outdated:** `datetime.utcnow()` (forbidden by CLAUDE.md), `Field(ondelete=...)` (ignored by SQLModel).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `cronsim` is an acceptable new dependency for this project (vs in-house parser / `croniter`) — verified technically, but adding a package is a user-visible `requirements.txt` change | Standard Stack | Low: swap to `croniter==6.2.4` or in-house behind the same `agent/schedule.py` API |
| A2 | `naive_local.astimezone()` yields correct DST handling on Windows (OS C-runtime rules); dev zone (UTC+3, no DST) could not exercise it | Pitfall 5 | Low-Medium: wrong run time on DST transition days for DST-observing users |
| A3 | On Windows `Process.terminate()` is a hard kill, so shutdown hooks may not run (code path in `ui/supervisor.py` verified; OS semantics from training) | Pattern 3 | Low: recovery-at-startup is needed either way |
| A4 | Cancel-intent stems (`отмен`, `останов`, `прекрат`, `удал`, `убер`, `выключ`, `cancel`, `stop`, `delete`, `remove`, `disable`) are a good-enough code-side gate; heuristic can false-negative on unusual phrasing (user then retries) or false-positive on "не отменяй" | Pattern 7 | Medium: user-facing behaviour of D-14; needs user acceptance. Add a negation window like `_NEGATION_OR_FUTURE_RE` |
| A5 | "Run now" counts toward `run_count`/`max_runs`, does not move `next_run_at`, and completes a `once` job; soft cancel does not abort an in-flight run; resuming a periodic job recomputes the next slot from now | Pattern 1/3, Pitfall 9 | Low-Medium: product semantics the CONTEXT left open |
| A6 | Headless prompt may include the user's long-term memory (and optionally profile) for parity with chat | Pattern 4 | Low |
| A7 | `session.exec(update(...))` works on the oldest allowed `sqlmodel>=0.0.22` | State of the Art | Low: installed 0.0.42 verified; use `session.execute` fallback if the floor matters |
| A8 | Global concurrency cap (2) for runs is desirable given single-GPU LM Studio | Code Examples | Low |

## Open Questions

1. **Should a scheduled run re-attempt an MCP server that has a *recorded* connect failure?**
   - What we know: `_auto_connect_missing` skips such servers ("no retry after failure", quick task 260924-2n8); after logout the sessions are closed, so an unattended run may see zero MCP tools.
   - What's unclear: whether the demo flow ("через минуту прочитай файл через MCP") can hit this (only if the server failed to connect earlier in the same Agent lifetime).
   - Recommendation: do not change MCP behaviour; log `mcp_tool_count` in the run and surface "MCP-инструменты недоступны" in the run's error/trace when the toolset is empty and the prompt is likely to need it. Revisit in Day 20 rechecks.
2. **Should the headless prompt include profile + long-term memory?** Recommended yes for long-term memory, optional for profile (A6); planner decides.
3. **Run-now on `once` jobs and `max_runs` accounting** (A5) — confirm with the user during plan review or accept the recommendation.
4. **Cancel gate strictness** (A4) — accept the heuristic, or add an explicit two-step (model asks user, user confirms) later with Day 20's 999.1.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Everything | yes | 3.13.15 (project floor 3.11+) | — |
| `cronsim` | Cron schedules | not yet in project; installable (checked into scratch dir) | 2.7 | `croniter==6.2.4` or in-house |
| SQLModel / SQLAlchemy / aiosqlite / respx / pytest | Implementation + tests | yes | 0.0.42 / 2.0.52 / 0.22.1 / 0.23.1 / 9.1.1 | — |
| `mcp` SDK | MCP tools in headless runs | yes | 1.30.0 | — |
| LM Studio (`localhost:1234`) | Real-model demo only | **no** (no response during research) | — | Unit/integration tests use `respx`; start LM Studio only for the manual demo |
| Local Go filesystem MCP server (`C:\Users\Aleksey\go\bin\filesystem.exe`) | Demo / `test_mcp_real_filesystem.py` | not probed (per 07-RESEARCH it exists) | — | Test fixture MCP server in `tests/fixtures` |
| `slopcheck` | Package audit | yes (installed this session) | — | — |
| `tzdata` / IANA zones on Windows | (avoided) | **no** (`ZoneInfoNotFoundError`) | — | Design avoids `zoneinfo` |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** LM Studio (tests mock it; demo needs it running).

## Test Strategy (Q7)

`workflow.nyquist_validation` is `false` in `.planning/config.json`, so the formal "Validation Architecture" table is omitted; this is the practical test plan following `tests/conftest.py` and `docs/TESTING_GUIDE.md`.

**Framework:** pytest 9.1.1 + pytest-asyncio 1.4.0 (`asyncio_mode = auto`), respx 0.23.1. Baseline: 635 tests passing per STATE.md (quick 260926-38j). Quick run: `pytest tests/test_scheduler_*.py -q`. Full: `pytest tests/ -q` (takes minutes; >100 s).

| File | Covers | Technique |
|------|--------|-----------|
| `tests/test_scheduler_schedule.py` | SCHED-01/09: `validate_cron` (5 fields only, bad fields, never-fires), `next_cron_run` with fixed-offset `tz` param, `next_interval_run` anchoring/backlog collapse, `run_at` parsing (naive/offset), min-interval | pure unit, no DB |
| `tests/test_scheduler_service.py` | SCHED-03/04/07/08: `tick(now)` claims due once/interval/cron; **two concurrent claims -> exactly one run** (start both selects before either update, e.g. `asyncio.gather` with a barrier); catch-up = one late run + next future slot; overlap => `skipped` run; partial unique index blocks 2nd `running`; `max_runs` completion; once completion; `recover_orphaned_runs`; pause/resume semantics | DB via fixtures; inject `now`; stub `execute_run` |
| `tests/test_scheduler_runner.py` | SCHED-05/06: headless success text; tool round with `save_long_term_memory` (row for right user, no chat/message rows created); MCP tool via existing fixture server; **timeout** (`monkeypatch SCHEDULER_RUN_TIMEOUT` tiny + respx async side-effect sleeping) -> `failed` + exact Russian text; `httpx.ConnectError` -> `Модель недоступна: LM Studio не запущен`; hallucinated `create_task` / `schedule_task` -> tool error, nothing written; empty answer -> failed; no `chat_locks` entry created | respx + helpers from `tests/test_memory_ws.py` (`_plain_content_response`, `_tool_calls_response`) and `tests/test_tool_rounds_ws.py::_stream_queue` |
| `tests/test_scheduler_api.py` | SCHED-10: CRUD + lifecycle status codes, 422 copy strings, 409 run-now while running, delete cascades runs, **UTC-aware ISO** in responses, user cap, `authenticated_client` + `second_authenticated_client` IDOR -> 404 | httpx `AsyncClient` (no lifespan) |
| `tests/test_scoping.py` (edit) | add new routes to `UNAUTH_ROUTES` -> 401 | parametrised |
| `tests/test_scheduler_tools.py` | SCHED-02/11: `schedule_task` via `dispatch_tool_calls` sets `origin_chat_id`, per-type arg validation errors, `list_scheduled_tasks` user-scoped, **chat delete keeps job and NULLs `origin_chat_id`** (style of `test_cascade_delete.py`), cancel gate (no intent -> `cancel_not_requested`; intent -> cancelled; foreign id -> `not_found`), schema regression (`len(build_tool_schemas()) == 9`, no `cancelled` enum), `done` frame `memory_writes` excludes scheduler results | direct dispatcher + `TestClient` WS for the frame check |
| `tests/test_scheduler_events.py` | SCHED-12: handshake rejects no cookie / garbage cookie / bad origin with 1008 (pattern of `tests/test_ws_auth.py`); **two users, event for A never reaches B**; queue drop-oldest; hub unsubscribe on disconnect; ping tolerance | `TestClient` sync + `client.portal.call(hub.publish, …)` |
| `tests/test_scheduler_lifespan.py` (small) | loop starts only when `SCHEDULER_ENABLED`; stop cancels loop and in-flight runs before `engine.dispose()`; recovery runs at start | `TestClient(app)` with monkeypatched setting |
| `tests/test_mcp_tools.py` (edit) | `== 6` -> `== 9` | — |
| `tests/conftest.py` (edit) | `os.environ.setdefault("SCHEDULER_ENABLED", "false")`; clear hub subscriptions in `clean_test_db` like other module-level dicts | — |

**Wave 0 gaps:** none blocking — test infrastructure exists. Docs: add a "Scheduler" block to `docs/TESTING_GUIDE.md` (required scenarios above), endpoints to `docs/API_SPEC.md`, process description to `docs/ARCHITECTURE.md`.
**Manual/e2e (demo):** create job from chat ("через минуту прочитай файл X через MCP и перескажи") -> sidebar card -> live `running` -> `success` -> modal; periodic job with `max_runs`; "Запустить сейчас". Not automatable without real LM Studio + browser; mark as human UAT.

## Security Domain

`security_enforcement` is not disabled in config (absent = enabled).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes (existing) | Session cookie via `get_current_user` / `get_current_user_ws`; no new auth code |
| V3 Session Management | yes | Reuse; `/ws/events` authenticates at handshake (same as chat); optional close on last-session logout |
| V4 Access Control | **yes — core** | Every query filtered by `user_id`; foreign task/run id -> 404; `EventHub` keyed by `user_id`; `TaskRun.user_id` denormalised for scoped reads |
| V5 Input Validation | yes | Pydantic models with `min_length`/`max_length`/`ge`; cron validated (5 fields + next occurrence); `run_at` parsed and range-checked; enum-typed `schedule_type` |
| V6 Cryptography | no | Nothing new (no secrets stored) |
| V13 API / WebSocket | yes | `require_allowed_origin` + `require_json_content_type` on mutating REST; `_validate_origin` on `/ws/events`; `asyncio.wait_for` on receives |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Cross-user job/run access (IDOR) | Information disclosure / Tampering | Scoped queries + 404; scoping tests with two users |
| Event leakage across users | Information disclosure | Per-user hub; isolation test; never use ownerless broadcast |
| Unattended tool use (prompt injection from file content read by MCP -> destructive tool) | Elevation of privilege | Headless allowlist (no scheduling/cancel/chat tools); per-run round cap + timeout; jobs are user-authored; destructive-tool gating stays backlog 999.1 (Day 20) — note as accepted risk |
| Job/DoS amplification (tiny interval, huge prompt, many jobs) | Denial of service | `SCHEDULER_MIN_INTERVAL_SECONDS`, prompt <= 4000, per-user job cap, overlap skip, concurrency semaphore, run timeout |
| Stored XSS via run result / titles | Tampering | Result via `renderMarkdown()` (DOMPurify); titles/errors/trace via `textContent` |
| CSRF on mutating REST | Tampering | `require_allowed_origin` + JSON content-type check (existing pattern) |
| LLM cancelling jobs unprompted | Tampering | Description + required flag + code guard (D-14) |
| Secret/prompt leakage in logs | Information disclosure | Log ids/status/duration only; never prompt text, tool args, or `str(exc)` from MCP (log `type(exc).__name__`) |
| Orphan/late run after delete | Integrity | Abort in-flight task on delete; tolerant finalizer |

## Sources

### Primary (HIGH confidence)
- Local codebase, read in full where relevant: `agent/ws.py`, `agent/tools.py`, `agent/tool_guard.py`, `agent/mcp_tools.py`, `agent/llm_client.py`, `agent/main.py` (lifespan, auth, MCP routes, WS route), `agent/dependencies.py`, `agent/state.py`, `agent/memory.py`, `agent/context_engine.py` (settings/system prompt/tool trace), `shared/database.py`, `shared/models.py`, `shared/config.py`, `ui/supervisor.py`, `ui/static/app.js` (WS client, fold panels, toast, apiFetch, init), `tests/conftest.py` and representative tests.
- Executed prototypes (scratchpad, this session): (a) `RecordingSink` + `_ToolTurn` + `_run_tool_rounds` with real `agent.ws`, mocked LM Studio, real DB -> `save_long_term_memory` saved for correct user with `chat_id=0`; (b) `UPDATE … WHERE next_run_at = old` via `session.exec(update(...))` -> `CursorResult.rowcount`, aware/naive datetime comparison, storage format; (c) partial unique index via `create_all` + `IntegrityError` behaviour; (d) `cronsim` 2.7 behaviour matrix incl. 6-field acceptance, error classes, DOM/DOW OR rule, `zoneinfo` failure on this Windows box.
- PyPI JSON / `pip index versions` for `cronsim` (2.7, requires_python >=3.10, upload 2025-10-21) and `croniter` (6.2.4, upload 2026-07-10, requires `python-dateutil`); `slopcheck scan` -> `cronsim` [OK].
- `.planning/phases/08-scheduler-day-18/08-CONTEXT.md`, `08-UI-SPEC.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `CLAUDE.md`.

### Secondary (MEDIUM confidence)
- github.com/cuu508/cronsim README (purpose, DST behaviour "matches Debian cron", `next()` API, Python 3.10+, BSD-3-Clause) — fetched via WebFetch.
- WebSearch on croniter status: [dt-croniter](https://pypi.org/project/dt-croniter/), [saltstack/salt#67134](https://github.com/saltstack/salt/issues/67134), [buildbot#8371](https://github.com/buildbot/buildbot/issues/8371), [pallets-eco/croniter](https://github.com/corpusops/croniter/issues/144) — the Dec-2024 unmaintained notice; superseded by 2026 releases per PyPI.

### Tertiary (LOW confidence)
- Windows `terminate()` = hard kill and DST behaviour of `naive.astimezone()` on Windows (training knowledge; see A2/A3).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified against installed packages/PyPI; cron lib exercised locally.
- Architecture: HIGH — every integration point read in source; the riskiest one (headless runner) and the two DB mechanisms (atomic claim, partial unique index) prototyped and run.
- Pitfalls: HIGH for code-derived ones (allowlist gap, `memory_writes`, tool-count tests, naive datetimes, lifespan-in-tests, supervisor kill); MEDIUM for DST and Windows process semantics.
- UI: MEDIUM — follows the approved UI-SPEC and existing `app.js` patterns; not browser-verified.

**Research date:** 2026-09-26
**Valid until:** 2026-10-26 (stack is stable; re-check `cronsim`/`croniter` releases if planning slips)
