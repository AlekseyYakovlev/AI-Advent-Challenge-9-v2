# AiAdventAgentV2 — Week 4: MCP Integration

## What This Is

A local-first, two-process AI chat application (FastAPI UI + Agent servers, vanilla JS frontend) that is being extended with an explicit agent memory model, personalization, and a formal task state machine with invariant enforcement. This is coursework for the "AI Advent Challenge" (9th cohort) — Week 3, Days 11-15 — where each day is a self-contained assignment building on the previous one's output, implemented in its own git branch and merged to `main` once its acceptance criteria pass.

## Current Milestone: v2.0 Week 4: MCP Integration

**Goal:** Connect the agent to external tools via the Model Context Protocol — starting with establishing an MCP connection and listing the server's tools (Day 16), then (later days) letting the LLM use them.

**Target features:**
- MCP server configuration in the Settings UI (per user)
- Agent-side MCP client (official `mcp` Python SDK, stdio transport) that connects and lists tools
- Visible connection status, server info, tool list, and clear errors
- Standalone CLI proof script that prints the tool list

## Core Value

The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where, rather than dumping everything into one undifferentiated context window.

## Requirements

### Validated

- ✓ Two-process UI/Agent split with WebSocket streaming chat — existing
- ✓ Message-tree data model (Chat/Message with parent_id, branching) — existing
- ✓ Global vs per-chat Settings with fallback resolution — existing
- ✓ Context compression strategies (sliding/sticky/truncate_middle/no_compression) — existing
- ✓ DeepSeek (cloud) and LM Studio (local) LLM backends — existing
- ✓ Live context/token usage stats via WebSocket `done` message and REST polling — existing
- ✓ **AUTH-01**: User can log in with username/password (simple auth, no external identity provider) — Phase 1
- ✓ **AUTH-02**: Every user has the same "admin" role and can create additional user accounts — Phase 1
- ✓ **AUTH-03**: Session is maintained via an HTTP-only session cookie, valid for both REST and WebSocket — Phase 1
- ✓ **AUTH-04**: All existing chats/settings/memory become scoped to the owning user (`user_id`) — Phase 1
- ✓ **MEM-01**: Agent has 3 explicitly separated memory layers — short-term (current dialog), working (current task data), long-term (profile, decisions, knowledge) — v1.0
- ✓ **MEM-02**: Long-term and working memory are stored in dedicated SQLite tables, not folded into the message tree — v1.0
- ✓ **MEM-03**: The LLM explicitly chooses what to save and to which layer, via tool calls (not implicit/automatic classification) — v1.0
- ✓ **MEM-04**: It's possible to inspect what data landed in each memory layer for a given chat — v1.0
- ✓ **MEM-05**: UI shows the current contents of each memory layer — v1.0
- ✓ **PERS-01**: Each user has a profile with preferences (style, format, constraints) — v1.0
- ✓ **PERS-02**: The user's profile is attached to every request (injected into context/system prompt) — v1.0
- ✓ **PERS-03**: UI lets the user view/edit their profile and preferences — v1.0
- ✓ **PERS-04**: Responses observably differ across different profiles/preferences — v1.0
- ✓ **TASK-01**: Task state is modeled as a finite state machine: `planning → execution → validation → done` — v1.0
- ✓ **TASK-02**: Tasks are granular within a chat (a chat can contain multiple tasks), not one task per chat — v1.0
- ✓ **TASK-03**: The LLM can autonomously create a new task when it recognizes a new unit of work (tool call), with the structure left open for future delegation to subagents — v1.0
- ✓ **TASK-04**: A task can be paused at any state and resumed later without re-explaining context — v1.0
- ✓ **TASK-05**: UI shows the current task, its state, and history of task state changes — v1.0
- ✓ **INV-01**: Global invariants (architecture/stack/business rules for the whole app) are defined and stored separately from the dialog — v1.0
- ✓ **INV-02**: Per-chat invariants can be added by the user, layered on top of global invariants — v1.0
- ✓ **INV-03**: Invariants are injected into the agent's reasoning context (prompt-injection) on every relevant request — v1.0
- ✓ **INV-04**: A dedicated check inspects the agent's response/tool calls for conflicts with active invariants and prompts the LLM to justify or retract the conflicting step — v1.0
- ✓ **INV-05**: UI surfaces active invariants and any detected conflicts — v1.0
- ✓ **TRANS-01**: Task states have an explicit set of allowed transitions; illegal transitions (e.g. execution before an approved plan, done before validation) are rejected — v1.0
- ✓ **TRANS-02**: Attempting an illegal transition produces a clear, explainable rejection rather than silently succeeding or crashing — v1.0
- ✓ **TRANS-03**: Task execution correctly resumes from a paused state without violating the transition graph — v1.0
- ✓ **MCP-01**: User can add, edit and delete MCP server configs (name, command, args) in Settings, stored per `user_id` — Phase 7 (v2.0)
- ✓ **MCP-02**: User can press "Connect" in Settings; Agent opens a stdio MCP session (initialize) and UI shows status + serverInfo (name, version, protocol) — Phase 7 (v2.0)
- ✓ **MCP-03**: After connecting, UI lists the server's tools (name, description, parameters from inputSchema) — Phase 7 (v2.0)
- ✓ **MCP-04**: Connection failures (bad path, server crash, timeout) are shown clearly in UI; Agent does not crash — Phase 7 (v2.0)
- ✓ **MCP-05**: `mcp` SDK pinned in requirements.txt; pytest covers connect + list_tools — Phase 7 (v2.0)
- ✓ **MCP-06**: Standalone CLI `scripts/mcp_list_tools.py <command> <args…>` prints the tool list — Phase 7 (v2.0)
- ✓ **MCP-F1**: LLM can call connected MCP tools during a chat turn; enabled servers auto-connect on first chat use — Phase 7 extension (quick tasks 260924-1ic, 260924-2n8)

- ✓ **SCHED-01..14**: Scheduler (Day 18) — user-scoped delayed/periodic jobs (once / interval / cron in machine-local time) stored in SQLite, run in the background of the Agent process (atomic slot claim, overlap skip, restart recovery, catch-up-once with `is_late`), executed by a headless LLM+MCP runner; manageable through REST `/api/v1/scheduler/*`, the "Расписание" sidebar panel with live `/ws/events` updates, and LLM chat tools with a cancel gate — Phase 8 (v2.0)

### Active

(No active requirements — start the next milestone to define more.)

### Out of Scope

- OAuth / external identity providers — simple username/password is sufficient for a handful of course-graders and peers; no need for third-party auth infra
- Fine-grained roles/permissions — every user is "admin"; no need for a permissions model beyond authenticated vs not
- Actual subagent execution of tasks — Day 13 only lays the groundwork (task records LLM can create); wiring real subagent dispatch is future work, out of scope for Week 3
- Multi-tenant data isolation beyond user_id scoping (no orgs/teams) — single flat user table is sufficient
- Long-term memory sharing across users — per-user by design; only global project invariants are shared

## Current State

Shipped **v1.0 Week 3: Agent Memory & Task State** (2026-09-23): auth, 3-layer memory, personalization, task FSM, invariants with conflict check, and hard-enforced task transitions — 6 phases / 25 plans, all merged to `main`. Known deferred item: Phase 01 VERIFICATION still flagged `human_needed` (auth exercised in every later live demo).

Phase 7 (MCP Connection, Day 16) complete (2026-09-24): user-scoped MCP server configs in Settings, connect/disconnect with serverInfo and tool list, readable connection errors, standalone CLI, and — as an approved scope extension — chat tool calling with lazy auto-connect. Known deferred review findings: WR-01, 03, 04, 05, 06, 08 (see .planning/phases/07-mcp-connection-day-16/07-REVIEW.md).

Phase 8 (Scheduler, Day 18) complete (2026-09-26): jobs stored per user, poll loop with atomic claim in the Agent lifespan, headless LLM+MCP runner, REST API, LLM tools, live sidebar panel; 928 tests; demo run end to end through Playwright with real LM Studio and MCP. Accepted limitation: the local qwen3.5-9b may execute a "через минуту …" request immediately instead of calling `schedule_task` (an explicit "запланируй …" works). Review findings CR-01, WR-01/02/03/08 fixed in gap-closure plan 08-09; WR-04..07 and INFO items parked as backlog 999.7–999.10 (see .planning/phases/08-scheduler-day-18/08-REVIEW.md).

## Context

- This extends the existing `AiAdventAgentV2` app (see `.planning/codebase/` for full architecture, stack, conventions, testing, and known concerns — notably CONCERNS.md flags stubbed summarization, context-overflow message deletion, and missing DB indexes, which are pre-existing and out of scope here unless a Week-3 phase touches that code directly).
- Prior weeks' work (Days 7-10) was implemented directly as feature branches without a `.planning/` layer; Week 3 is the first use of the GSD planning pipeline on this repo, by explicit user choice.
- Each day/phase is merged to `main` after its own acceptance criteria pass; branches are not deleted.
- Course context: solutions (git link + video) are posted to a shared spreadsheet for peer review — not something this repo automates, just background on why "done" means shippable, demonstrable increments.

## Constraints

- **Frontend**: Vanilla JS + CDN libraries only (Tailwind, Marked.js, DOMPurify) — no bundler, no npm packages. Any auth UI (login form, profile editor, memory/task panels) must follow this.
- **Process model**: No Docker, no `multiprocessing`/`os.fork`, no Redis/RabbitMQ/Celery — UI/Agent split stays `asyncio.create_subprocess_exec` only; IPC stays REST + WebSocket.
- **Auth mechanism**: HTTP-only session cookie (not JWT/localStorage) — matches local-first, single-deployment nature of the app and works uniformly for REST + WebSocket.
- **Data scope**: All new data (memory layers, profiles, tasks, per-chat invariants) is scoped by `user_id`; only global project invariants remain shared across users.
- **MCP servers**: External MCP servers run as separate stdio subprocesses spawned via the SDK (asyncio/anyio subprocess — no `multiprocessing`). Native binaries (e.g. the Go `portertech/filesystem-mcp-server` at `C:\Users\Aleksey\go\bin\filesystem.exe`) are allowed; Node/npx-based servers are not.
- **Branching**: One branch per phase, named after the day (`Day11`...`Day16`...) except the auth foundation phase, which is named `Auth`. Branches are pushed and merged to `main`, never deleted.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Full UI integration for every phase (not backend-only) | User wants each capability visibly demonstrable in the existing chat UI | ✓ Good (v1.0) |
| Memory layers as new SQLite tables, reusing existing DB | Keeps consistency with existing Chat/Message/Settings persistence rather than introducing a second storage mechanism | ✓ Good (v1.0) |
| LLM chooses what/when to save via tool calls | Day 11 explicitly requires "you explicitly choose what and where is saved" — tool calls make that choice legible and demonstrate agentic behavior | ✓ Good (v1.0) |
| Tasks are granular within a chat, auto-created by the LLM | Matches real task decomposition; leaves room for future subagent delegation per task | ✓ Good (v1.0) |
| Drop single-user constraint, add simple multi-user auth (all users = admin) | Personalization needs distinct profiles; user explicitly chose to move off single-user mode | Validated in Phase 1 |
| Auth as its own foundation phase/branch (`Auth`), before Day 11 | All later phases (memory, profile, tasks, invariants) need `user_id` scoping from day one; avoids retrofitting | Validated in Phase 1 |
| HTTP-only session cookie for auth | Simple, works uniformly across REST and WebSocket, fits local-first single-deployment model, no extra frontend library needed | Validated in Phase 1 |
| Invariants enforced via prompt-injection + explicit response-conflict check | Balances realism (LLM can still err) with a real guardrail (dedicated conflict check), appropriate for course scope | ✓ Good (v1.0) |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/bm:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/bm:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-09-26 — Phase 8 (Scheduler, Day 18) complete*
