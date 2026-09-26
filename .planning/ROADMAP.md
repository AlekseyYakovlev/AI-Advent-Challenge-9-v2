# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- 🚧 **v2.0 Week 4: MCP Integration** — Phase 7+ (in progress)

## Phases

<details>
<summary>✅ v1.0 Week 3: Agent Memory & Task State (Phases 1-6) — SHIPPED 2026-09-23</summary>

- [x] Phase 1: Auth Foundation (5/5 plans) — completed 2026-09-20
- [x] Phase 2: Memory (Day 11) (5/5 plans) — completed 2026-09-20
- [x] Phase 3: Personalization (Day 12) (3/3 plans) — completed 2026-09-20
- [x] Phase 4: Task State Machine (Day 13) (4/4 plans) — completed 2026-09-20
- [x] Phase 5: Invariants (Day 14) (4/4 plans) — completed 2026-09-20
- [x] Phase 6: Controlled Transitions (Day 15) (4/4 plans) — completed 2026-09-21

Full details: [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

### 🚧 v2.0 Week 4: MCP Integration (In Progress)

- [x] **Phase 7: MCP Connection (Day 16)** — The agent connects to an MCP server configured in Settings and shows the server's tool list (completed 2026-09-23)
- [ ] **Phase 8: Scheduler (Day 18)** — Delayed and periodic jobs with persisted status/results, run by the agent and shown in the UI

## Phase Details

### Phase 7: MCP Connection (Day 16)

**Goal**: A user can configure an MCP server in the Settings UI, connect to it, and see the list of tools the server exposes — proven against the locally installed Go filesystem MCP server
**Branch**: `Day16`
**Depends on**: Phase 1 (Auth) — server configs are scoped by `user_id`
**Requirements**: MCP-01, MCP-02, MCP-03, MCP-04, MCP-05, MCP-06
**Success Criteria** (what must be TRUE):

1. User adds a server in Settings with command `C:\Users\Aleksey\go\bin\filesystem.exe` and an allowed-directory arg; the config survives an app restart and is invisible to other users
2. Pressing "Connect" shows status "connected" with serverInfo `filesystem-mcp-server` / version / protocol, and lists all 17 tools with descriptions and parameters
3. A bad command path or a server that exits immediately yields a readable error in the UI, and the Agent's `/health` stays OK
4. `python scripts/mcp_list_tools.py C:\Users\Aleksey\go\bin\filesystem.exe <dir>` prints serverInfo and the tool list
5. `pytest tests/ -v` passes, including new MCP connect/list_tools tests (success + failure)

**Plans**: 6 plans
**UI hint**: yes

Plans:
**Wave 1**

- [x] 07-01-PLAN.md — MCP stdio client core: pin mcp, timeout config, result schemas, fixture server, owner-task registry + error classification (wave 1)
- [x] 07-02-PLAN.md — McpServerConfig table + user-scoped CRUD service with env masking/merge (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 07-03-PLAN.md — REST endpoints /api/v1/mcp/servers (CRUD, connect/disconnect/status) + lifespan cleanup + API tests (wave 2)
- [x] 07-04-PLAN.md — CLI scripts/mcp_list_tools.py reusing connect_once_and_list + tests (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 07-05-PLAN.md — "MCP серверы" section in the Settings modal (wave 3)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 07-06-PLAN.md — End-to-end acceptance vs filesystem.exe + human UI walkthrough (wave 4, checkpoint)

### Phase 8: Scheduler (Day 18)
**Goal**: A user (or the LLM via chat tools) can schedule delayed (one-shot) and periodic (interval/cron) jobs; the agent runs them in the background, stores each job's status and every run's result, and the UI shows scheduled and completed jobs
**Depends on**: Phase 7
**Requirements**: SCHED-01, SCHED-02, SCHED-03, SCHED-04, SCHED-05, SCHED-06, SCHED-07, SCHED-08, SCHED-09, SCHED-10, SCHED-11, SCHED-12, SCHED-13, SCHED-14
**Branch**: `Day18`
**Decision**: Written from scratch in Python inside the Agent process (not a fork of `C:\Projects\mcp-cron`: Go, cron-only, in-memory status, no REST, no user scoping, duplicate agent loop, AGPL)
**Scope sketch** (to be refined in discuss/plan):
- `ScheduledTask` + `TaskRun` SQLModel tables, scoped by `user_id`
- asyncio poll loop started in the agent lifespan; optimistic claim (`UPDATE ... WHERE next_run_at = old`); tasks survive Agent restarts
- Executor reuses `mcp_client` / `llm_client` under `tool_guard`
- REST `/api/v1/scheduler/*`; LLM tools `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`
- UI panel (vanilla JS): scheduled and completed tasks with status and run result
**Success Criteria** (what must be TRUE):

1. From chat ("через минуту прочитай файл X через MCP и перескажи") the LLM creates a job; it appears in the sidebar panel and goes `выполняется` -> `успешно` live, with the result opening in a Markdown modal
2. Once / interval / cron jobs (cron in machine-local time) fire exactly once per slot; overlapping slots are recorded as `skipped`; `max_runs` and one-shot jobs auto-complete
3. After an Agent restart a missed job runs once flagged late, and runs interrupted by the restart are marked failed
4. REST `/api/v1/scheduler/*` and `/ws/events` are user-scoped (404 / owner-only events); the LLM cannot cancel a job unless the user asked
5. `pytest tests/ -q` passes, including the new scheduler tests

**Plans**: 8 plans
**UI hint**: yes

Plans:
**Wave 1**

- [x] 08-01-PLAN.md — ScheduledTask/TaskRun models (partial unique index, CASCADE/SET NULL), SCHEDULER_* settings, cronsim pin, pure schedule math (wave 1)
- [x] 08-02-PLAN.md — Per-user EventHub + WS /ws/events (origin + cookie auth), conftest guards (wave 1)
- [x] 08-03-PLAN.md — Headless LLM+MCP runner reusing the ws tool loop via RecordingSink + dispatcher allowlist (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 08-04-PLAN.md — Scheduler engine: atomic claim, poll loop, catch-up/overlap/max_runs, startup recovery, executor with timeout, lifespan wiring (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 08-05-PLAN.md — User-scoped scheduler ops + REST /api/v1/scheduler/* + scoping tests (wave 3)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 08-06-PLAN.md — LLM tools schedule_task / list_scheduled_tasks / cancel_scheduled_task with cancel gate (wave 4)
- [ ] 08-07-PLAN.md — "Расписание" sidebar panel, create/result modals, /ws/events live client (wave 4)

**Wave 5** *(blocked on Wave 4 completion)*

- [ ] 08-08-PLAN.md — Docs sync (API_SPEC, ARCHITECTURE, TESTING_GUIDE), full suite, human demo walkthrough (wave 5, checkpoint)

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Auth Foundation | v1.0 | 5/5 | Complete | 2026-09-20 |
| 2. Memory (Day 11) | v1.0 | 5/5 | Complete | 2026-09-20 |
| 3. Personalization (Day 12) | v1.0 | 3/3 | Complete | 2026-09-20 |
| 4. Task State Machine (Day 13) | v1.0 | 4/4 | Complete | 2026-09-20 |
| 5. Invariants (Day 14) | v1.0 | 4/4 | Complete | 2026-09-20 |
| 6. Controlled Transitions (Day 15) | v1.0 | 4/4 | Complete | 2026-09-21 |
| 7. MCP Connection (Day 16) | v2.0 | 6/6 | Complete   | 2026-09-23 |
| 8. Scheduler (Day 18) | v2.0 | 5/8 | In Progress|  |

## Backlog

> **Deferred to Day 20 (2026-09-26):** items 999.1–999.3 below, plus the remaining Day 16 leftovers: manual interactive Ctrl+C check with `filesystem.exe` connected, and browser/real-model rechecks for quick tasks 260924-1ic, 260924-2n8, 260925-oya, 260925-q0s, 260925-qj5, 260925-qvd. No Day 20 phase exists in the roadmap yet.

### Phase 999.1: Guard merge_merge_request: run only when the user explicitly asks to merge (BACKLOG)

**Goal:** block or require explicit user request for merge_merge_request; on 'сделай MR' the model created MR !1 and merged it unprompted (quick 260926-38j real GitLab run)
**Deferred to:** Day 20
**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.2: Descriptive LLM timeout error instead of empty 'LLM error:' (BACKLOG)

**Goal:** llm_stream_timeout yields empty detail and the whole turn (user message) is rolled back even though tools already ran; use type name/'timeout' and keep executed tool results
**Deferred to:** Day 20
**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.3: Trim wasted tool rounds and stray second answer after MCP error nudge (BACKLOG)

**Goal:** model burns rounds on list_allowed_directories/get_file_info and commit_files 'update' on missing files; TOOL_ERROR_REMINDER can append a second answer after an already good final answer
**Deferred to:** Day 20
**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.4: Auto-rename chats with LLM instead of 'New Chat' (BACKLOG)

**Goal:** every chat is currently titled 'New Chat'; the LLM should generate a short title per chat
**Chosen approach (2026-09-26): title after first Q&A turn.** Trigger once when the chat has 2 messages and the title is still the default 'New Chat' (never overwrite a user-edited title); input = first user message + short summary of the first answer; 3–8 words / ~50 chars, temperature 0, max_tokens ~30, plain text (not JSON, for small LM Studio models), user text wrapped in tags against prompt injection; title in the user's language; fallback = truncated first user message if the LLM call fails; run after the `done` event as a non-blocking extra call and push a WebSocket event so the sidebar updates. Open: which model generates the title (default: the chat's current model)
**Refs:** ChatOllama blog (2025-09-09), OpenSearch-Dashboards PR #12786, NodeSpace issue #1698, LibreChat PR #13395, open-webui discussion #9567
**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.5: Modal windows close only via 'x' button (BACKLOG)

**Goal:** change modal behavior: a modal closes only when its 'x' is clicked; clicking outside the modal (on the backdrop) must not close it
**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.6: Edit and delete long-term memory fields via UI (BACKLOG)

**Goal:** the user must be able to edit long-term memory fields through the UI ("Редактировать" and "Удалить" buttons per entry)
**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /bm:review-backlog when ready)

---
*Roadmap created: 2026-09-19*
*Last updated: 2026-09-23 — v1.0 milestone archived*
