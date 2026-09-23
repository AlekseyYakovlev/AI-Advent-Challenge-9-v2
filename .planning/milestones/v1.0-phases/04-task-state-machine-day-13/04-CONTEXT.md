# Phase 4: Task State Machine (Day 13) - Context

**Gathered:** 2026-09-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Chats can contain multiple discrete units of work (tasks), each tracked through an explicit lifecycle (`planning → execution → validation → done`, plus a manual `cancelled` terminal state) that the LLM creates and progresses via tool calls, and that the user can observe (and pause/resume/cancel) in the UI. A task can be paused at any point and resumed later without the user re-explaining context.

**Explicitly out of this phase's scope (carried forward from `STATE.md`):** hard enforcement of illegal transitions (TRANS-01/02/03) is deferred to Phase 6. This phase builds the state enum, the transition history, and the tool/REST surface — it does not need to reject illegal transitions; Phase 6 hardens what's built here. Real subagent dispatch is also out of scope (per `PROJECT.md`/`REQUIREMENTS.md` Out of Scope) — this phase only adds the unused `delegate_to` field that future work will fill in.

</domain>

<decisions>
## Implementation Decisions

### Task data & tool surface
- **D-01:** `create_task` captures **title + description + goal** (not title+description alone). User explicitly chose the richer shape — the goal field gives the validation state something concrete to check against without relying on chat history.
- **D-02:** A **separate `transition_task(task_id, new_state, note)` tool** handles state changes — `create_task` does not double as an update. Keeps "new task" and "state change" as distinct, clearly logged events, which TASK-05's history view builds directly from.
- **D-03:** The TASK-03 "open field for future subagent delegation" is a single **nullable `delegate_to` column** (str, always null today) on the Task table — not a generic JSON metadata blob. Visible in the schema, ready for future work to fill in without a migration.

### Pause & cancel model
- **D-04:** `is_paused` is an **orthogonal boolean flag**, not a 5th enum state. The task keeps its `planning`/`execution`/`validation`/`done` value while paused — TASK-01's FSM stays exactly the 4 states it names; Phase 6's transition graph only needs to reason about those 4 plus a pause toggle.
- **D-05 (Claude's discretion — recommended default applied):** Pause/resume are handled by **separate `pause_task(task_id)` / `resume_task(task_id)` LLM tool calls**, mirroring `create_task`/`transition_task`'s one-tool-per-action pattern — user delegated this choice; recommended option applied since it's consistent with the rest of the tool surface.
- **D-06 (Claude's discretion — recommended default applied):** Resume relies on the **task's own persisted fields (title/description/goal/state) plus the chat's existing `WorkingMemory` table (Phase 2)** as the source of truth for in-progress task data — no new memory mechanism is introduced. User delegated this choice; this matches `STATE.md`'s already-locked Phase 6 decision to verify resume "using working memory as the source of truth."
- **D-07:** `cancelled` is an **explicit 5th state value** on the Task enum (extending beyond TASK-01's literal 4 states) — a terminal state distinct from `done`, so history/UI can show "abandoned" vs "successfully completed." User explicitly chose this over folding cancel into a boolean flag. Cancel is reachable **only via manual UI action**, never an LLM tool (see D-12).

### Multiplicity & addressing
- **D-08:** **No single "active"/"current" task per chat** — tasks form a flat list, each tracked and displayed independently. User took TASK-02's "a chat can hold multiple concurrent tasks" literally, rejecting a `current_leaf_message_id`-style single-focus pointer.
- **D-09:** Every task-referencing tool call (`transition_task`, `pause_task`, `resume_task`) **requires an explicit `task_id`** — no implicit "most recently touched task" default. Mirrors `save_working_memory`/`save_long_term_memory`'s explicit-key pattern (Phase 2); avoids silently acting on the wrong task once several are concurrent.

### UI placement & manual control
- **D-10:** The task panel is a **new sidebar tab**, next to the existing Memory and Profile tabs — same always-reachable placement precedent as `02-CONTEXT.md` D-04 and `03-CONTEXT.md` D-04.
- **D-11:** State-change history renders as a **chronological list/timeline per task** (state → state, timestamp) — mirrors the Memory panel's simple entry-list rendering rather than a compact/expandable summary.
- **D-12:** The UI includes **manual pause/resume/cancel controls**, reached via a **plain REST endpoint** (e.g. `POST /api/v1/tasks/{id}/pause`, `/resume`, `/cancel`) that **bypasses the tool-call dispatcher entirely** — mirroring Phase 3's `PUT /api/v1/profile` split (a UI-owned write path, separate from `agent/tools.py`). User explicitly scoped this to pause/resume/cancel only: **manual arbitrary state transitions are NOT included** — the `planning → execution → validation → done` progression stays LLM-tool-only via `transition_task`; the user's REST-reachable actions are pause, resume, and cancel.

### Claude's Discretion
- Exact wording/format of the injected system-prompt hint (if any) that tells the LLM about task tools — follow the existing pattern from memory/profile injection in `agent/context_engine.py::build_system_prompt()`.
- Whether `delegate_to` (D-03) is a plain `str | None` column or a small `str`-backed enum with no values defined yet — either is fine since it's unused this phase; default to `str | None` for simplicity, matching `LongTermMemory.value`'s plain-string precedent.
- Exact Tailwind markup/styling of the new sidebar Task tab — match existing patterns in `index.html`/`app.js` used for the Memory and Profile tabs.
- Whether `note` on `transition_task` is required or optional — recommend optional (empty string default), since not every transition needs justification text.

</decisions>

<specifics>
## Specific Ideas

- User specifically wants **cancel** as a manual, UI-only escape hatch distinct from the LLM's normal lifecycle tools — not something the LLM can trigger itself, and not folded into the pause flag. This came up as a deliberate addition beyond the original question scope (see D-07/D-12).

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Task state machine requirements & constraints
- `.planning/PROJECT.md` §Requirements (TASK-01..05), §Constraints (`user_id` data scoping, vanilla-JS-only frontend)
- `.planning/REQUIREMENTS.md` §Task State — TASK-01 through TASK-05 acceptance criteria; §Out of Scope — real subagent execution explicitly excluded this phase
- `.planning/ROADMAP.md` §Phase 4: Task State Machine (Day 13) — goal, branch (`Day13`), depends-on (Phase 2/Memory), success criteria
- `.planning/STATE.md` §Accumulated Context — locked cross-phase decisions: `agent/tools.py` dispatcher built once in Phase 2, reused unchanged by Phases 3-5; writes synchronous/per-chat-locked/tool-call-only; multiple tool calls in one turn execute strictly sequentially (relevant if `create_task` and `transition_task` are called in the same LLM turn); **TRANS-01/02/03 hard enforcement deliberately deferred to Phase 6** — this phase builds the state enum, transition history, and machinery Phase 6 hardens, not hard rejection
- `CLAUDE.md` §Hard constraints — `user_id` data scoping, no Docker/multiprocessing, vanilla-JS-only frontend, HTTP-only session cookie auth already in place (Phase 1)

### Prior phase precedent (patterns this phase must follow)
- `.planning/phases/02-memory-day-11/02-CONTEXT.md` — D-01 (named-tool-per-action pattern, directly precedent for D-02/D-05 here), D-04 (sidebar tab placement, precedent for D-10)
- `.planning/phases/03-personalization-day-12/03-CONTEXT.md` — D-02 (UI-only REST edit path bypassing the tool-call dispatcher, direct precedent for D-12's manual pause/resume/cancel controls), D-04 (sidebar tab placement)
- `.planning/codebase/CONVENTIONS.md` — SQLModel FK-cascade convention, structlog logging pattern, naming conventions the new `Task`/`TaskTransition` tables and tools must follow
- `.planning/codebase/ARCHITECTURE.md` — documents `agent/tools.py`'s registry/dispatcher, the per-chat lock pattern (`agent/state.py::chat_locks`), and `build_system_prompt()`'s existing injection assembly

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/tools.py` — the existing tool registry/dispatcher (`register_tool` decorator, `TOOL_REGISTRY`/`TOOL_SCHEMAS`/`TOOL_DESCRIPTIONS`, strictly-sequential `dispatch_tool_calls`) — `create_task`/`transition_task`/`pause_task`/`resume_task` register here exactly like `save_working_memory`/`save_long_term_memory` (`agent/tools.py:158-190`).
- `agent/schemas.py` (`SaveWorkingMemoryArgs`, `SaveLongTermMemoryArgs`) — Pydantic args-model pattern to follow for the new tools' argument schemas.
- `shared/models.py` SQLModel FK-cascade pattern (`sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`, as used by `WorkingMemory`/`LongTermMemory`/`Profile`) — the new `Task` table must follow this exact convention, never `Field(ondelete=...)`.
- `agent/memory.py` — CRUD module pattern (list/save functions, commit/rollback try-except, `logger.info` on write) to mirror for a new `agent/tasks.py`.
- `agent/main.py`'s `update_settings`/`PUT /api/v1/profile` endpoints — direct precedent for the new UI-only REST pause/resume/cancel endpoints (D-12), including commit/rollback handling.
- `ui/static/app.js`'s `loadProfile`/`renderProfilePanel` and `renderMemoryPanel` (`ui/static/app.js:291-334`) — direct pattern to mirror for the new Task tab (load-on-select, render-into-container, button-triggered REST calls for pause/resume/cancel).

### Established Patterns
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses.
- Type hints everywhere; `structlog` logging (`logger = get_logger(__name__)`) — no `print()`.
- Tool-call writes are synchronous, per-chat-locked (`agent/state.py::chat_locks`), never fire-and-forget.

### Integration Points
- `shared/models.py` — new `Task` table (id, chat_id, user_id, title, description, goal, state enum, is_paused, delegate_to, created_at, updated_at) and a `TaskTransition` history table (task_id, from_state, to_state, note, created_at) for TASK-05.
- New `agent/tasks.py` — CRUD + transition/pause/resume/cancel logic, mirroring `agent/memory.py`'s structure.
- `agent/tools.py` — four new registered tools: `create_task`, `transition_task`, `pause_task`, `resume_task`.
- `agent/main.py` — new REST endpoints: `GET /api/v1/chats/{chat_id}/tasks` (list, for UI), `POST /api/v1/tasks/{id}/pause`, `/resume`, `/cancel` (manual UI control, bypassing the tool dispatcher per D-12).
- `ui/static/index.html` / `ui/static/app.js` — new sidebar "Tasks" tab: list of tasks with state, pause/resume/cancel buttons, and an expandable chronological history per task.

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. (The "cancel" addition was folded into this phase's scope as D-07/D-12, not deferred, since it's a natural extension of the pause/resume UI control the user was already describing.)

</deferred>

---

*Phase: 04-task-state-machine-day-13*
*Context gathered: 2026-09-20*
