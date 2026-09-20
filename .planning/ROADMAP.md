# Roadmap: AiAdventAgentV2 — Week 3: Agent Memory & Task State

**Core Value:** The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where.

**Mode:** MVP (Vertical MVP structure) — every phase is a full end-to-end slice: backend + UI + verification, not a horizontal layer.

**Granularity note:** `config.json` sets `granularity: coarse` (typically 2-4 phases), but this milestone is coursework where each phase corresponds to a separately graded, separately branched/merged assignment day (per `PROJECT.md` Key Decisions and Constraints: "One branch per phase, named after the day"). Collapsing days together would violate that hard constraint, so this roadmap keeps the research-derived 6-phase structure (1 foundation + 5 day-phases) rather than compressing to the coarse default. Each phase remains a coherent, independently mergeable vertical slice.

## Phases

- [x] **Phase 1: Auth Foundation** — Users can log in, all accounts are equal "admin," and all data becomes scoped to the owning user (completed 2026-09-20)
- [x] **Phase 2: Memory (Day 11)** — Agent has three explicit, inspectable memory layers populated only via deliberate LLM tool calls (completed 2026-09-20)
- [x] **Phase 3: Personalization (Day 12)** — Each user's profile is injected into every request and observably shapes responses (completed 2026-09-20)
- [x] **Phase 4: Task State Machine (Day 13)** — Chats can contain multiple LLM-created tasks tracked through an explicit lifecycle (completed 2026-09-20)
- [ ] **Phase 5: Invariants (Day 14)** — Global and per-chat ground rules are injected into context and checked against agent behavior
- [ ] **Phase 6: Controlled Transitions (Day 15)** — Illegal task-state transitions are hard-rejected with a clear explanation, and pause/resume is verified correct

## Phase Details

### Phase 1: Auth Foundation

**Goal**: Users can securely log in, every account has equal "admin" capability, and all existing and new data is scoped to the owning user
**Mode:** mvp
**Branch**: `Auth`
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04
**Success Criteria** (what must be TRUE):

  1. User can log in with username/password and reach the chat UI (no external identity provider involved)
  2. User can create additional user accounts, all with the same "admin" capability
  3. The session persists across page reloads and covers both REST calls and the WebSocket chat connection, via an HTTP-only session cookie (never localStorage/JWT)
  4. Existing chats/settings/memory are scoped to a backfilled bootstrap admin user, and each subsequently created user only sees their own chats/settings/memory

**Plans**: 5 plans (5 waves)
Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Password hashing, User/Session tables, login/logout/me routes, CORS allowlist, login.html

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Nullable user_id columns, additive migration, bootstrap admin, backfill, run.py credential banner

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-03-PLAN.md — Gate and user-scope every REST route, owner-scoped settings fallback, test-suite repair

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 01-04-PLAN.md — Pre-accept session and chat-ownership gate on the chat WebSocket

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 01-05-PLAN.md — POST /api/v1/auth/users and the "Add user" modal (AUTH-02)

### Phase 2: Memory (Day 11)

**Goal**: The agent maintains three explicitly separated, inspectable memory layers, populated only through deliberate LLM tool calls — never implicit/automatic classification
**Mode:** mvp
**Branch**: `Day11`
**Depends on**: Phase 1 (Auth) — every new table needs `user_id` scoping from creation
**Requirements**: MEM-01, MEM-02, MEM-03, MEM-04, MEM-05
**Success Criteria** (what must be TRUE):

  1. A chat has three distinguishable memory layers: short-term (current dialog), working (current task data), long-term (profile/decisions/knowledge)
  2. Working and long-term memory persist in dedicated SQLite tables, independent of the message tree
  3. Content only lands in working/long-term memory as the result of a visible, explicit LLM tool call — never an automatic/background classification step
  4. User can inspect exactly what data landed in each memory layer for a given chat via a UI panel

**Plans**: 5 plans (4 waves)
Plans:
**Wave 1**

- [x] 02-01-PLAN.md — WorkingMemory/LongTermMemory tables, agent/memory.py CRUD, GET /chats/{id}/memory, sidebar Память panel (MEM-01, MEM-02, MEM-04, MEM-05)
- [x] 02-02-PLAN.md — LM Studio model load/unload migrated to the working v1 control endpoints, unblocking the tool-capable-model demo path (MEM-03)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 02-03-PLAN.md — Tool argument schemas, agent/tools.py registry + strictly sequential dispatcher, stream_chat tools param and tool_calls delta accumulation (MEM-03)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 02-04-PLAN.md — Read-only memory injection in build_system_prompt, tool-call round-trip inside the per-chat-locked WS turn, memory_writes on the done frame (MEM-01, MEM-03)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 02-05-PLAN.md — Day 11 acceptance demo against a real tool-capable model; records the resolved LM Studio and DeepSeek-routing open questions (MEM-01, MEM-03, MEM-04, MEM-05)

**UI hint**: yes

### Phase 3: Personalization (Day 12)

**Goal**: Each user's saved preferences are attached to every request and visibly shape how the agent responds
**Mode:** mvp
**Branch**: `Day12`
**Depends on**: Phase 2 (Memory) — profile is modeled as long-term memory and reuses the tool-call dispatcher/storage pattern
**Requirements**: PERS-01, PERS-02, PERS-03, PERS-04
**Success Criteria** (what must be TRUE):

  1. Each user has a profile with preferences (style, format, constraints)
  2. User can view and edit their profile/preferences in the UI
  3. The user's profile is injected into context/system prompt on every request, not just some
  4. Responses observably differ across two different profiles/preference sets for the same prompt

**Plans**: 3 plans (3 waves)
Plans:
**Wave 1**

- [x] 03-01-PLAN.md — Profile table (D-01), agent/profile.py CRUD, GET/PUT /api/v1/profile, unconditional injection in build_system_prompt (PERS-01, PERS-02)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 03-02-PLAN.md — #profile-panel sidebar block and loadProfile/renderProfilePanel/saveProfile wiring (PERS-03)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 03-03-PLAN.md — Strategy/turn-count coverage guards plus the Day 12 A/B acceptance demo against a real model (PERS-02, PERS-04)

**UI hint**: yes

### Phase 4: Task State Machine (Day 13)

**Goal**: Chats can contain multiple discrete units of work, each tracked through an explicit lifecycle the LLM creates and the user can see
**Mode:** mvp
**Branch**: `Day13`
**Depends on**: Phase 2 (Memory) — reuses the tool-call dispatcher for `create_task`/`transition_task`; sequenced after Phase 3 by branch/day order
**Requirements**: TASK-01, TASK-02, TASK-03, TASK-04, TASK-05
**Success Criteria** (what must be TRUE):

  1. A task's state is one of `planning → execution → validation → done`
  2. A single chat can hold multiple concurrent tasks, not just one
  3. The LLM can autonomously create a new task via tool call when it recognizes a new unit of work, with an open/unused field for future subagent delegation
  4. A task can be paused at any state and resumed later without the user re-explaining context
  5. UI shows the current task, its state, and the history of its state changes

**Plans**: 4 plans (4 waves)
Plans:
**Wave 1**

- [x] 04-01-PLAN.md — Task/TaskTransition tables, agent/tasks.py CRUD, create_task tool, GET /chats/{id}/tasks, Задачи sidebar panel, task_writes on the done frame (TASK-01, TASK-02, TASK-03, TASK-05)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 04-02-PLAN.md — LlmTaskState + transition_task tool with the task_id ownership guard, per-task history timeline in the panel, Task/TaskTransition cascade coverage (TASK-01, TASK-05)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 04-03-PLAN.md — pause_task/resume_task tools plus lock-protected POST /tasks/{id}/pause|resume|cancel and the Пауза/Продолжить/Отменить controls (TASK-04, TASK-05)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 04-04-PLAN.md — list_open_tasks injection into build_system_prompt (resume without re-explaining) and the Day 13 acceptance demo (TASK-01..TASK-05)

**UI hint**: yes

### Phase 5: Invariants (Day 14)

**Goal**: The agent's reasoning is grounded in explicit, layered ground rules that the user can see, extend per-chat, and that flag conflicts with agent behavior
**Mode:** mvp
**Branch**: `Day14`
**Depends on**: Phase 2 (Memory) — reuses the tool-call dispatcher and the Settings global/per-chat NULL-fallback pattern; sequenced after Phase 4 by branch/day order
**Requirements**: INV-01, INV-02, INV-03, INV-04, INV-05
**Success Criteria** (what must be TRUE):

  1. Global invariants (architecture/stack/business rules) are defined and stored separately from the dialog
  2. User can add per-chat invariants that layer on top of global invariants, with an explicit precedence rule
  3. Active invariants are injected into the agent's reasoning context on every relevant request
  4. When the agent's response/tool calls conflict with an active invariant, a dedicated check flags it and prompts the LLM to justify or retract the step
  5. UI surfaces active invariants and any detected conflicts

**Plans**: 4 plans (4 waves)
Plans:
**Wave 1**

- [x] 05-01-PLAN.md — GlobalInvariant table, agent/invariants.py CRUD, shared /api/v1/invariants routes, Инварианты sidebar tab, fold/unfold retrofit on all four panels (INV-01, INV-05)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 05-02-PLAN.md — ChatInvariant table with the overrides_id FK, resolve_active_invariants, per-chat REST routes, D-06 override labelling in build_system_prompt, overrides dropdown (INV-02, INV-03)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 05-03-PLAN.md — InvariantConflict table, self-critique complete_chat call, justify/retract round-trip, conflict persistence and log endpoint, inline amber banner + tab badge (INV-04, INV-05)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 05-04-PLAN.md — Strategy-matrix and long-history injection guards plus the Day 14 acceptance demo (INV-01..INV-05)

**UI hint**: yes

### Phase 6: Controlled Transitions (Day 15)

**Goal**: Task state transitions are strictly, verifiably enforced rather than merely suggested, and pause/resume never violates the transition graph
**Mode:** mvp
**Branch**: `Day15`
**Depends on**: Phase 4 (Task State Machine) and Phase 5 (Invariants) — hardens both: transition history from Phase 4 and conflict checks from Phase 5 get wired into transition attempts
**Requirements**: TRANS-01, TRANS-02, TRANS-03
**Success Criteria** (what must be TRUE):

  1. An illegal transition (e.g. execution before an approved plan, done before validation) is rejected rather than silently succeeding
  2. A rejected transition produces a clear, explainable message in the UI/WS response, never a silent no-op or a crash
  3. Resuming a paused task correctly continues along the valid transition graph, verified using working memory as the source of truth rather than whatever the active context-compression strategy happens to retain

**Plans**: TBD
**UI hint**: yes

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Auth Foundation | 5/5 | Complete   | 2026-09-20 |
| 2. Memory (Day 11) | 5/5 | Complete   | 2026-09-20 |
| 3. Personalization (Day 12) | 3/3 | Complete   | 2026-09-20 |
| 4. Task State Machine (Day 13) | 4/4 | Complete   | 2026-09-20 |
| 5. Invariants (Day 14) | 3/4 | In Progress|  |
| 6. Controlled Transitions (Day 15) | 0/? | Not started | - |

---
*Roadmap created: 2026-09-19*
*Last updated: 2026-09-20 — Phase 5 planned (4 plans, 4 waves)*
