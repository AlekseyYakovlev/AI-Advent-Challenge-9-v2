# Phase 4: Task State Machine (Day 13) - Research

**Researched:** 2026-09-20
**Domain:** Explicit task-lifecycle state machine (SQLModel tables + tool-call dispatcher extension + UI-only REST control path) added to an already-shipped FastAPI/SQLModel/SQLite two-process chat app with an established tool-call dispatcher (Phase 2) and profile-style UI-only REST write path (Phase 3)
**Confidence:** HIGH — this phase is a structural extension of two already-proven, in-codebase patterns (the tool dispatcher and the UI-only REST bypass path); no new libraries, no new wire protocols, no unverified external behavior. The only genuinely new design questions (concurrency between the REST bypass path and the WS tool-call path; whether pause/resume appear in the history table) are resolved below with an explicit recommendation, not left open.

## Summary

Phase 4 adds two new SQLite tables (`Task`, `TaskTransition`), four new tool-call handlers (`create_task`, `transition_task`, `pause_task`, `resume_task`) registered in the **already-built, unmodified** `agent/tools.py` dispatcher from Phase 2, three new UI-only REST endpoints (`POST /api/v1/tasks/{id}/pause|resume|cancel`) that deliberately bypass that dispatcher (mirroring Phase 3's `PUT /api/v1/profile` split), one new read endpoint (`GET /api/v1/chats/{chat_id}/tasks`), and a new stacked sidebar panel (`#task-panel`) that follows the exact same always-visible, non-tab-switching structure already used by `#memory-panel`/`#profile-panel`.

Every architectural primitive this phase needs already exists in the codebase and was proven live in Phase 2/3: the `register_tool`/`TOOL_REGISTRY`/`dispatch_tool_calls` dispatcher (strictly sequential, per-chat-locked, Pydantic-arg-validated), the `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` table convention, the `build_system_prompt()` read-injection chokepoint, and the `_get_chat_or_404`-style IDOR-safe ownership-check idiom. The one genuinely new architectural wrinkle is that this phase introduces the **first UI-only REST write path for chat-scoped data** — Phase 3's `PUT /api/v1/profile` bypasses the dispatcher but is *user*-scoped, so it never had to share `agent/state.py::chat_locks` with the WS handler. Task rows are *chat*-scoped, so a manual "Cancel" click and an in-flight LLM `transition_task` tool call on the same task can race unless the REST endpoints also acquire the chat's lock. This research's primary recommendation resolves that gap explicitly (see Concurrency section) — bypass the *dispatcher*, not the *lock*.

**Primary recommendation:** Add `Task`/`TaskTransition` to `shared/models.py` using the exact `WorkingMemory`/`Settings` FK-cascade and `SAEnum(..., values_callable=...)` idioms already in the file; build `agent/tasks.py` as a thin CRUD/lifecycle module mirroring `agent/memory.py` and `agent/profile.py`; register the four new tools in `agent/tools.py` unchanged in shape from `save_working_memory`/`save_long_term_memory`; have the three manual REST pause/resume/cancel endpoints acquire `agent/state.py::chat_locks[task.chat_id]` before writing (bypassing only the tool-schema/dispatcher layer, never the per-chat serialization guarantee); inject each chat's non-terminal tasks (title/description/goal/state/is_paused) into `build_system_prompt()` as a new read-only block so resumed tasks never require the user to re-explain context.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Task/TaskTransition schema definition | Database/Storage | API/Backend | New SQLModel tables, no relation to `Message`; owned entirely by `shared/models.py` per the existing memory-table precedent |
| `create_task`/`transition_task`/`pause_task`/`resume_task` tool schemas + dispatch | API/Backend | — | Registered in `agent/tools.py`, invoked only from inside `agent/ws.py`'s per-chat-locked flow — never the browser, never a background task |
| Manual pause/resume/cancel (UI button click) | API/Backend | Browser/Client (button + REST call) | New REST endpoints in `agent/main.py`, deliberately outside `agent/tools.py`'s registry (D-12) but still inside the chat's lock (this research's concurrency recommendation) |
| Task list + history read-injection into LLM context | API/Backend | — | Extends `agent/context_engine.py::build_system_prompt()`, the single existing context-assembly chokepoint — do not fork a second injection path |
| Task inspection UI (sidebar panel, pause/resume/cancel buttons) | Browser/Client | API/Backend (new GET/POST endpoints) | Pure read/render + button-triggered REST calls in vanilla JS; no business logic in the browser (task creation itself has no UI button — LLM-tool-only per D-03) |
| Illegal-transition rejection (TRANS-01/02/03) | — | — | **Explicitly out of this phase's scope** — Phase 6 hardens what this phase builds; do not implement transition-graph validation logic here beyond the one D-07/D-12 carve-out below |

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** `create_task` captures **title + description + goal** (not title+description alone). User explicitly chose the richer shape — the goal field gives the validation state something concrete to check against without relying on chat history.
- **D-02:** A **separate `transition_task(task_id, new_state, note)` tool** handles state changes — `create_task` does not double as an update. Keeps "new task" and "state change" as distinct, clearly logged events, which TASK-05's history view builds directly from.
- **D-03:** The TASK-03 "open field for future subagent delegation" is a single **nullable `delegate_to` column** (str, always null today) on the Task table — not a generic JSON metadata blob. Visible in the schema, ready for future work to fill in without a migration.
- **D-04:** `is_paused` is an **orthogonal boolean flag**, not a 5th enum state. The task keeps its `planning`/`execution`/`validation`/`done` value while paused — TASK-01's FSM stays exactly the 4 states it names; Phase 6's transition graph only needs to reason about those 4 plus a pause toggle.
- **D-05 (Claude's discretion — recommended default applied):** Pause/resume are handled by **separate `pause_task(task_id)` / `resume_task(task_id)` LLM tool calls**, mirroring `create_task`/`transition_task`'s one-tool-per-action pattern — user delegated this choice; recommended option applied since it's consistent with the rest of the tool surface.
- **D-06 (Claude's discretion — recommended default applied):** Resume relies on the **task's own persisted fields (title/description/goal/state) plus the chat's existing `WorkingMemory` table (Phase 2)** as the source of truth for in-progress task data — no new memory mechanism is introduced. User delegated this choice; this matches `STATE.md`'s already-locked Phase 6 decision to verify resume "using working memory as the source of truth."
- **D-07:** `cancelled` is an **explicit 5th state value** on the Task enum (extending beyond TASK-01's literal 4 states) — a terminal state distinct from `done`, so history/UI can show "abandoned" vs "successfully completed." User explicitly chose this over folding cancel into a boolean flag. Cancel is reachable **only via manual UI action**, never an LLM tool (see D-12).
- **D-08:** **No single "active"/"current" task per chat** — tasks form a flat list, each tracked and displayed independently. User took TASK-02's "a chat can hold multiple concurrent tasks" literally, rejecting a `current_leaf_message_id`-style single-focus pointer.
- **D-09:** Every task-referencing tool call (`transition_task`, `pause_task`, `resume_task`) **requires an explicit `task_id`** — no implicit "most recently touched task" default. Mirrors `save_working_memory`/`save_long_term_memory`'s explicit-key pattern (Phase 2); avoids silently acting on the wrong task once several are concurrent.
- **D-10:** The task panel is a **new sidebar tab**, next to the existing Memory and Profile tabs — same always-reachable placement precedent as `02-CONTEXT.md` D-04 and `03-CONTEXT.md` D-04.
- **D-11:** State-change history renders as a **chronological list/timeline per task** (state → state, timestamp) — mirrors the Memory panel's simple entry-list rendering rather than a compact/expandable summary.
- **D-12:** The UI includes **manual pause/resume/cancel controls**, reached via a **plain REST endpoint** (e.g. `POST /api/v1/tasks/{id}/pause`, `/resume`, `/cancel`) that **bypasses the tool-call dispatcher entirely** — mirroring Phase 3's `PUT /api/v1/profile` split (a UI-owned write path, separate from `agent/tools.py`). User explicitly scoped this to pause/resume/cancel only: **manual arbitrary state transitions are NOT included** — the `planning → execution → validation → done` progression stays LLM-tool-only via `transition_task`; the user's REST-reachable actions are pause, resume, and cancel.

### Claude's Discretion

- Exact wording/format of the injected system-prompt hint (if any) that tells the LLM about task tools — follow the existing pattern from memory/profile injection in `agent/context_engine.py::build_system_prompt()`.
- Whether `delegate_to` (D-03) is a plain `str | None` column or a small `str`-backed enum with no values defined yet — either is fine since it's unused this phase; default to `str | None` for simplicity, matching `LongTermMemory.value`'s plain-string precedent.
- Exact Tailwind markup/styling of the new sidebar Task tab — match existing patterns in `index.html`/`app.js` used for the Memory and Profile tabs.
- Whether `note` on `transition_task` is required or optional — recommend optional (empty string default), since not every transition needs justification text.

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope. (The "cancel" addition was folded into this phase's scope as D-07/D-12, not deferred, since it's a natural extension of the pause/resume UI control the user was already describing.)

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TASK-01 | Task state is modeled as a finite state machine: `planning → execution → validation → done` | `Task.state` column, `TaskState` SQLAlchemy enum (SAEnum with `values_callable`), 4 core values + `cancelled` (D-07). No hard transition-graph enforcement this phase — see Architecture Patterns / Anti-Patterns. |
| TASK-02 | Tasks are granular within a chat (a chat can contain multiple tasks), not one task per chat | `Task.chat_id` FK, no `UniqueConstraint` on `(chat_id, ...)` and no `current_task_id` pointer on `Chat` (D-08) — flat list, queried via `SELECT * FROM task WHERE chat_id = ?`. |
| TASK-03 | The LLM can autonomously create a new task when it recognizes a new unit of work (tool call), with the structure left open for future delegation to subagents | `create_task` tool registered in `agent/tools.py` exactly like `save_working_memory`; `Task.delegate_to: str \| None` column (D-03), never exposed as a settable tool argument this phase. |
| TASK-04 | A task can be paused at any state and resumed later without re-explaining context | `is_paused` boolean (D-04), `pause_task`/`resume_task` tools (D-05) + manual REST equivalents (D-12); context restored via `build_system_prompt()` task-list injection (new Pattern below) + Phase 2's `WorkingMemory` (D-06) — no new memory mechanism. |
| TASK-05 | UI shows the current task, its state, and history of task state changes | `GET /api/v1/chats/{chat_id}/tasks` returns tasks + embedded `TaskTransition` history (D-11); new `#task-panel` sidebar section mirroring `#memory-panel`'s stacked, always-visible structure. |

</phase_requirements>

## Project Constraints (from CLAUDE.md)

These are binding, not suggestions — the plan-checker will verify compliance:

- Type hints everywhere; `async`/`await` for all I/O. No bare `except:`.
- `structlog` (`get_logger(__name__)`), never `print()`.
- `datetime.now(timezone.utc)`, never `datetime.utcnow()`.
- Import order: stdlib → third-party → local.
- SQLModel FK cascade: `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` — **never** `Field(ondelete=...)` (silently ignored).
- `.is_(None)` instead of `== None` in SQLAlchemy `where()`.
- Always `await session.commit()` after writes, `await session.rollback()` in exception handlers.
- No Docker/npm/Node/Redis/RabbitMQ/Celery/`multiprocessing`/`os.fork`.
- Frontend: vanilla JS + CDN libraries only (Tailwind, Marked.js, DOMPurify) — no bundler, no npm packages.
- All new data scoped by `user_id` (Week-3 addendum: "memory, tasks, invariants, profiles" explicitly named).
- Task writes must be synchronous, per-chat-locked (reuse `agent/state.py::chat_locks`), never fire-and-forget/debounced.
- Multiple tool calls in one LLM turn execute strictly sequentially, never `asyncio.gather`d — a same-turn `create_task` + `transition_task(task_id=<the new id>)` pair must work.
- Branch: `Day13`, pushed and merged to `main`, never deleted.
- `agent/tools.py`'s dispatcher was built once in Phase 2 and is reused **unchanged** — this phase only adds registrations to it, never modifies `dispatch_tool_calls`/`register_tool`/`build_tool_schemas` themselves.

## Standard Stack

### Core

No new pip packages are required for this phase. [VERIFIED: direct read of `requirements.txt` and every module this phase touches] Every capability needed — SQLModel tables, Pydantic tool-arg schemas, the tool-call wire format, the per-chat lock, the REST/WS split — is already a proven, pinned dependency exercised by Phases 1-3.

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|---------------|
| SQLModel / SQLAlchemy 2.x | already pinned (`sqlmodel>=0.0.22`) | `Task`/`TaskTransition` table definitions, `SAEnum` for `TaskState` | Same idiom as `ContextStrategy`/`Settings.strategy`, already proven in this exact codebase |
| Pydantic 2.x | already pinned (`pydantic>=2.9.0`) | `CreateTaskArgs`/`TransitionTaskArgs`/`PauseTaskArgs`/`ResumeTaskArgs` tool-arg schemas, `model_json_schema()` | Identical to Phase 2's `SaveWorkingMemoryArgs`/`SaveLongTermMemoryArgs` pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| None new | — | — | REQUIREMENTS.md's own Out of Scope table explicitly excludes "General-purpose workflow engine / external state-machine library" as "massive overkill for 4-5 states; violates hard constraint of no extra infra" |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled `TaskState` enum + linear application logic (no transition-graph enforcement this phase) | `python-statemachine`, `transitions` (PyPI state-machine libraries) | Rejected — explicitly out of scope per REQUIREMENTS.md; would also front-run Phase 6's own TRANS-01/02/03 design, which is supposed to hardened *on top of* this phase's plain enum, not replace it |
| One `Task` table + one `TaskTransition` audit table | A single table with a JSON `history` blob column | Rejected — a relational audit table is queryable/orderable/filterable without JSON parsing, matches the existing `Message`-tree-as-relational-not-blob precedent, and cascade-deletes cleanly with SQLite FK enforcement |
| REST pause/resume/cancel endpoints acquiring `chat_locks[chat_id]` | Leaving them lock-free (fully independent of the WS flow) | Rejected — see Concurrency section below; Task rows are chat-scoped (unlike `Profile`, which is user-scoped and correctly has no lock), so an unlocked REST write can race an in-flight `transition_task` tool call in the same chat |

**Installation:**
```bash
# No new packages — this phase uses only already-pinned dependencies.
```

**Version verification:** [VERIFIED: `requirements.txt` read directly this session]

| Package | Pinned Floor |
|---------|--------------|
| sqlmodel | >=0.0.22 |
| pydantic | >=2.9.0 |
| fastapi | >=0.115.0 |

No `requirements.txt` change needed for this phase.

## Package Legitimacy Audit

**Not applicable — this phase installs zero new external packages.** slopcheck/registry verification is skipped per the protocol's scope (only required "whenever this phase installs external packages").

## Architecture Patterns

### System Architecture Diagram

```
Browser (vanilla JS, ui/static/app.js)
    |
    |-- (A) WS send {content, model} -- UNCHANGED wire contract
    |         v
    |    agent/ws.py :: ws_chat() -> _handle_chat_message()
    |         |-- per-chat lock (agent/state.py::chat_locks) -- EXISTING, reused not duplicated
    |         |-- build_llm_context() -> build_system_prompt()   (EXTENDED)
    |         |     `-- NEW: injects this chat's non-terminal tasks (title/description/goal/
    |         |               state/is_paused) as a read-only block, same idiom as
    |         |               working/long-term memory injection (Phase 2 Pattern 3)
    |         |-- stream_chat(..., tools=build_tool_schemas())    -- UNCHANGED call shape,
    |         |     tool schema LIST now includes 4 new entries automatically (registry-driven)
    |         |-- IF tool_calls present:
    |         |     agent/tools.py :: dispatch_tool_calls()        -- UNCHANGED function,
    |         |       routes to 4 NEW handlers registered this phase:
    |         |         create_task       -> agent/tasks.py::create_task()
    |         |         transition_task   -> agent/tasks.py::transition_task()  (+ ownership check)
    |         |         pause_task        -> agent/tasks.py::set_paused(True)   (+ ownership check)
    |         |         resume_task       -> agent/tasks.py::set_paused(False)  (+ ownership check)
    |         |       each write commits synchronously, inside the SAME per-chat lock + session
    |         `-- WS `done` message -- EXTENDED with a lightweight task_writes: [...] summary
    |                                   (id/title/state only, mirrors memory_writes)
    |
    `-- (B) POST /api/v1/tasks/{id}/pause | /resume | /cancel  -- NEW, D-12 manual control
              v
         agent/main.py -- ownership check via a new _get_task_or_404-style helper
              |-- NEW: acquire agent/state.py::chat_locks[task.chat_id] BEFORE writing
              |         (bypasses the TOOL DISPATCHER, not the per-chat lock -- see
              |          Concurrency section; this is the one deviation from Phase 3's
              |          PUT /api/v1/profile precedent, which needs no lock because
              |          Profile is user-scoped, not chat-scoped)
              `-- agent/tasks.py::set_paused()/cancel_task() -- SAME functions path (A) calls

New REST (pure GET, read path, mirrors GET /api/v1/chats/{id}/memory):
    GET /api/v1/chats/{chat_id}/tasks
        -> list of tasks for this chat (flat, no "current" pointer per D-08), each with
           its embedded TaskTransition history ordered oldest-first (D-11)

SQLite (app.db)
    Existing: Chat, Message, Settings, TokenUsage, User, Session, WorkingMemory,
              LongTermMemory, Profile
    NEW: Task (user_id, chat_id, title, description, goal, state, is_paused,
               delegate_to, created_at, updated_at)
    NEW: TaskTransition (task_id, from_state[nullable], to_state, note, created_at)
```

A reader can trace the primary use case end to end: the LLM recognizes a new unit of work mid-conversation, calls `create_task`, the dispatcher persists a `Task` row plus a creation `TaskTransition` (from_state=null), later turns call `transition_task`/`pause_task` which each append one more `TaskTransition` row, `build_system_prompt()` re-injects the chat's open tasks on every subsequent turn so a paused task's context survives without the user repeating themselves, and the sidebar Task panel reads the same rows back through a dedicated GET endpoint — completely decoupled from both write paths, satisfying TASK-05 structurally.

### Recommended Project Structure

```
agent/
├── tasks.py             # NEW: Task/TaskTransition CRUD + lifecycle logic, mirroring
│                         #      agent/memory.py's and agent/profile.py's thin-CRUD shape
├── tools.py              # EXTENDED: 4 new @register_tool entries (create_task,
│                          #           transition_task, pause_task, resume_task) --
│                          #           dispatch_tool_calls() itself is NOT modified
├── context_engine.py       # EXTENDED: build_system_prompt() injects open tasks
├── schemas.py                # EXTENDED: CreateTaskArgs/TransitionTaskArgs/PauseTaskArgs/
│                             #           ResumeTaskArgs, TaskResponse, TaskTransitionResponse
└── main.py                     # EXTENDED: GET /api/v1/chats/{chat_id}/tasks,
                                 #           POST /api/v1/tasks/{id}/pause|resume|cancel

shared/
└── models.py            # EXTENDED: Task, TaskTransition, TaskState SQLModel/enum additions

ui/static/
├── index.html           # EXTENDED: #task-panel stacked sidebar section (D-10), after
│                         #           #profile-panel, same border-t/bg-slate-900 idiom
└── app.js               # EXTENDED: loadChatTasks/renderTaskPanel + pause/resume/cancel
                          #           button handlers; 'done' WS case gains loadChatTasks call
```

### Pattern 1: Registering four new tools without touching the dispatcher

**What:** `agent/tools.py::dispatch_tool_calls`, `register_tool`, and `build_tool_schemas` are already generic — Phase 4 adds new `@register_tool(...)` decorated handlers exactly like `save_working_memory`, and the dispatcher automatically picks them up because `build_tool_schemas()` iterates `TOOL_SCHEMAS` (populated by the decorator), and `agent/ws.py` already calls `build_tool_schemas()` fresh on every turn.

**When to use:** Every new tool this project adds (Phases 4 and 5 both reuse this dispatcher per STATE.md's locked decision).

**Example (follows the exact registered shape already in `agent/tools.py`):**
```python
# agent/tools.py -- additions only, imports/registry/dispatch_tool_calls unchanged
from agent import tasks
from agent.schemas import (
    CreateTaskArgs,
    PauseTaskArgs,
    ResumeTaskArgs,
    TransitionTaskArgs,
)


@register_tool(
    "create_task",
    CreateTaskArgs,
    "Create a new task when you recognize a distinct, trackable unit of work in this "
    "chat. Starts in the 'planning' state.",
)
async def _create_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create a task scoped to the current chat (never trust an LLM-supplied chat_id)."""
    row = await tasks.create_task(
        session, user_id, chat_id, args["title"], args["description"], args["goal"],
    )
    return {"status": "created", "id": row.id, "state": row.state.value}


@register_tool(
    "transition_task",
    TransitionTaskArgs,
    "Move an existing task to a new lifecycle state (planning, execution, validation, "
    "or done). Cannot be used to cancel a task -- cancellation is a manual user action.",
)
async def _transition_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Transition a task owned by this chat/user; raises a tool-level error otherwise."""
    row = await tasks.transition_task(
        session, user_id, chat_id, args["task_id"], args["new_state"], args.get("note", ""),
    )
    return {"status": "transitioned", "id": row.id, "state": row.state.value}
```
`pause_task`/`resume_task` follow the identical shape, calling `tasks.set_paused(session, user_id, chat_id, task_id, is_paused=True/False)`.

**Critical: ownership check inside the tool handler, not just at the REST layer.** Unlike `save_working_memory` (which trusts the dispatcher's own `chat_id` and never receives one from the LLM), `transition_task`/`pause_task`/`resume_task` receive an LLM-supplied `task_id` (D-09). `agent/tasks.py`'s functions must verify `task.chat_id == chat_id and task.user_id == user_id` before writing and raise/return a tool-level error otherwise — this is the direct task-scoped analog of Phase 2's Security Domain finding ("never accept a bare `user_id`/`chat_id` from the client").

### Pattern 2: Manual REST bypass that shares the chat lock (the one new architectural decision this phase requires)

**What:** D-12 requires `POST /api/v1/tasks/{id}/pause|resume|cancel` to bypass `agent/tools.py`'s registry/schema validation entirely (mirroring `PUT /api/v1/profile`). But **"bypass the dispatcher" is not the same claim as "bypass the per-chat lock."** Task rows are chat-scoped (unlike `Profile`, which is user-scoped and therefore correctly lock-free). If a user clicks "Cancel" in the UI in the same moment the LLM's `transition_task` tool call is mid-write for the same task inside `_handle_chat_message`'s lock, an unlocked REST write can interleave with the WS turn's session commit, producing a lost update or a stale `TaskTransition` ordering.

**When to use:** Every manual REST mutation this phase adds to a chat-scoped row (pause/resume/cancel). Does **not** apply to `PUT /api/v1/profile` (already shipped, user-scoped, correctly unlocked) or to any future *read* endpoint.

**Example:**
```python
# agent/main.py -- new REST endpoint, D-12
from agent.state import chat_locks

async def _get_task_or_404(session: AsyncSession, task_id: int, user_id: int) -> Task:
    """Load a task owned by user_id or raise HTTP 404 (never 403, IDOR-safe -- same idiom
    as _get_chat_or_404)."""
    task = await session.get(Task, task_id)
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found")
    return task


@app.post("/api/v1/tasks/{task_id}/pause", response_model=TaskResponse)
async def pause_task_endpoint(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Manually pause a task (D-12) -- bypasses agent/tools.py, but NOT the chat's lock."""
    task = await _get_task_or_404(session, task_id, current_user.id)
    if task.chat_id not in chat_locks:
        chat_locks[task.chat_id] = asyncio.Lock()
    async with chat_locks[task.chat_id]:
        row = await tasks.set_paused(session, current_user.id, task.chat_id, task_id, True)
    return _task_to_response(row)
```
`resume`/`cancel` endpoints follow the identical lock-then-write shape; `cancel` calls `tasks.cancel_task(...)` instead of `set_paused(...)`.

### Pattern 3: Task-list read-injection, extending `build_system_prompt` (the mechanism that satisfies TASK-04)

**What:** Extend `build_system_prompt()` (already assembling profile + facts + summary + working memory + long-term memory) to also list this chat's non-terminal tasks (`state != done` and `state != cancelled`), each with title/description/goal/state/is_paused. This is the concrete mechanism by which "resumed later without the user re-explaining context" (TASK-04) is satisfied: the task's own persisted fields plus this injection block give the LLM everything it had when the task was paused, every single turn, with zero extra memory-layer work (D-06).

**When to use:** Every turn, for every chat that has at least one non-terminal task.

**Example (extends the exact function already shown live in Phase 2's RESEARCH.md, same file, same idiom):**
```python
# agent/context_engine.py -- extend build_system_prompt (existing function, do not fork)
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    ...  # existing profile/facts/summary/memory blocks unchanged
    open_tasks = await tasks.list_open_tasks(session, chat_id)
    if open_tasks:
        lines = []
        for t in open_tasks:
            paused_note = " [ON PAUSE]" if t.is_paused else ""
            lines.append(
                f"- #{t.id} \"{t.title}\" (state={t.state.value}{paused_note}): "
                f"goal={t.goal!r}",
            )
        parts.append("Open tasks in this chat:\n" + "\n".join(lines))
    return "\n\n".join(parts)
```
**Design note for the planner:** `list_open_tasks` should exclude `done`/`cancelled` states (they're historical, not "in progress") but should NOT exclude paused tasks — a paused task is exactly the case TASK-04 needs surfaced on every subsequent turn so the LLM can offer to resume it.

### Pattern 4: `TaskTransition` history — creation writes a row with `from_state=NULL`

**What:** `create_task` should write one `TaskTransition` row at creation time (`from_state=None`, `to_state="planning"`) so the history view (D-11) always has a first entry, not an empty list until the first real transition. `transition_task`/`cancel_task` write `from_state=<current state>`, `to_state=<new state>`. **Pause/resume do NOT write `TaskTransition` rows** — TASK-05 asks for "history of task **state** changes," and `is_paused` is explicitly an orthogonal flag (D-04), not a state; logging it into the same audit trail would blur exactly the distinction D-04 draws. This is a recommendation, not a locked decision — flag it to the user during plan review if pause/resume auditability turns out to matter for the demo.

**Example:**
```python
# agent/tasks.py
async def create_task(
    session: AsyncSession, user_id: int, chat_id: int, title: str, description: str, goal: str,
) -> Task:
    task = Task(
        user_id=user_id, chat_id=chat_id, title=title, description=description,
        goal=goal, state=TaskState.PLANNING,
    )
    session.add(task)
    await session.flush()  # get task.id before writing the transition row
    session.add(TaskTransition(task_id=task.id, from_state=None, to_state=TaskState.PLANNING, note=""))
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(task)
    logger.info("task_created", chat_id=chat_id, task_id=task.id)
    return task
```

### Anti-Patterns to Avoid

- **Implementing transition-graph legality checks in this phase.** `transition_task` must accept any of the 4 LLM-reachable states (`planning`/`execution`/`validation`/`done`) without validating the *previous* state was a legal predecessor — that is explicitly TRANS-01/02/03, deferred to Phase 6. The **one exception** is D-07/D-12's carve-out: `transition_task`'s Pydantic arg model must not even accept `"cancelled"` as a value (see Code Examples) — this is a scope boundary explicitly stated by the user, not a transition-legality check, so it is in-scope even though general enforcement is not.
- **Exposing `delegate_to` as a settable tool argument.** D-03 requires the column to exist and stay null; `CreateTaskArgs`/`TransitionTaskArgs` must not include a `delegate_to` field, or the LLM could start "delegating" before TASK-06's real subagent dispatch exists in a future milestone.
- **A single "current task" pointer anywhere** (on `Chat`, in `agent/state.py`, or in the UI's selected-task state). D-08 explicitly rejects this; every tool call and REST call must carry an explicit `task_id`.
- **Letting the REST pause/resume/cancel endpoints skip `chat_locks`.** See Pattern 2 — "bypass the dispatcher" (D-12) is not license to skip the concurrency guarantee that protects every other chat-scoped write in this codebase.
- **A second, parallel context-assembly path that reads tasks directly in `agent/ws.py` instead of through `build_system_prompt()`.** Would duplicate token accounting exactly the way Phase 2's RESEARCH.md already warned against for memory.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Finite-state-machine enforcement | A `python-statemachine`/`transitions`-style graph validator this phase | A plain `TaskState` enum column + linear application code, with **zero** legality checks this phase (Phase 6's job) | REQUIREMENTS.md's Out of Scope table explicitly names this: "massive overkill for 4-5 states; violates hard constraint of no extra infra/message broker" |
| Generating JSON Schema for the 4 new tool arguments | Hand-written JSON schema dicts | Pydantic `BaseModel.model_json_schema()` via the existing `TOOL_SCHEMAS` registry (already wired, zero new code needed in `agent/tools.py::build_tool_schemas`) | Already the established pattern from Phase 2; `build_tool_schemas()` needs no changes at all |
| Chat-scoped write serialization for the new REST endpoints | A bespoke lock/mutex per task ID | Reuse `agent/state.py::chat_locks[task.chat_id]` (see Pattern 2) | A second lock dictionary keyed by `task_id` would let a task-level lock and a chat-level lock (WS flow) both be held for the same underlying chat's data with no ordering guarantee between them — a second, parallel locking domain is worse than sharing the existing one |
| Task list read-injection | A second `build_task_prompt()` function called independently from `agent/ws.py` | Extend `build_system_prompt()` in place (Pattern 3) | Exactly the anti-pattern Phase 2's own RESEARCH.md warned against for memory — do not repeat it for tasks |

**Key insight:** Nothing in this phase is a novel design problem. The dispatcher, the lock, the FK-cascade convention, and the UI-only-REST-bypass convention are all already proven in this exact codebase by Phases 2 and 3. The only discipline required is applying the *right* existing pattern to each new piece (dispatcher for LLM-initiated writes, locked-REST for manual writes, injection-extension for context continuity) rather than inventing a new one.

## Common Pitfalls

### Pitfall 1: REST pause/resume/cancel racing an in-flight `transition_task` tool call on the same task
**What goes wrong:** A user clicks "Отменить" (Cancel) in the UI at the same moment the LLM's response for that turn includes a `transition_task` call for the same `task_id`. Without shared locking, both writes can commit in either order, and the `TaskTransition` history can show a nonsensical sequence (e.g. `execution → done` appearing *after* `done → cancelled` was already recorded).
**Why it happens:** Phase 3's `PUT /api/v1/profile` precedent (D-12's stated analog) is correctly unlocked because `Profile` is user-scoped, not chat-scoped — copying that precedent verbatim for a chat-scoped resource silently drops a needed guarantee.
**How to avoid:** REST pause/resume/cancel endpoints acquire `agent/state.py::chat_locks[task.chat_id]` before writing (Pattern 2). This is additive, not a new locking primitive.
**Warning signs:** A `TaskTransition` history whose `created_at` ordering doesn't match its `from_state`/`to_state` chain (e.g. a row starting from a state no earlier row ended in).

### Pitfall 2: Trusting an LLM-supplied `task_id` without an ownership check
**What goes wrong:** [CITED: this codebase's own Phase 2 Security Domain finding, directly applicable] `transition_task`/`pause_task`/`resume_task` receive `task_id` as a tool argument (D-09) — unlike `chat_id`/`user_id`, which the dispatcher already supplies from the trusted WS session. If `agent/tasks.py`'s functions don't verify `task.chat_id == chat_id and task.user_id == user_id`, a hallucinated or adversarially-prompted `task_id` from a different chat could be silently transitioned.
**Why it happens:** The dispatcher's existing `_SCOPE_KEYS` guard (`agent/tools.py`, lines 22/127-133) only warns-and-ignores if the LLM tries to pass `chat_id`/`user_id` directly in `arguments` — it does **not** protect against a legitimate-looking `task_id` argument that happens to point at another chat's row, because `task_id` isn't in `_SCOPE_KEYS`.
**How to avoid:** Every `agent/tasks.py` function that receives a `task_id` must load the row and check ownership before mutating (Pattern 1's "Critical" note).
**Warning signs:** A task's `chat_id`/`TaskTransition` history references a chat other than the one the tool call actually ran in.

### Pitfall 3: `SAEnum` storing enum *names* instead of *values* (silent enum drift)
**What goes wrong:** [VERIFIED: direct read of `shared/database.py::_migrate_legacy_strategies`, which exists specifically because a legacy `ContextStrategy` value ("branching") drifted and had to be migrated] SQLAlchemy's `Enum` type defaults to storing the Python enum *member name* unless `values_callable=lambda enum_cls: [m.value for m in enum_cls]` is passed — exactly the pattern already used for `ContextStrategy` in `shared/models.py` line 92-97.
**Why it happens:** Easy to omit `values_callable` when adding a new `SAEnum` column, since it "just works" for reads/writes within the same session and only breaks when comparing raw stored strings (e.g. a raw SQL migration, or a REST response that serializes `.state` before the ORM round-trips it).
**How to avoid:** Copy `Settings.strategy`'s exact `sa_column=Column(SAEnum(TaskState, values_callable=lambda enum_cls: [m.value for m in enum_cls]))` idiom verbatim for `Task.state` and `TaskTransition.from_state`/`to_state`.
**Detection:** `sqlite3 app.db "SELECT DISTINCT state FROM task"` should show lowercase values (`planning`, `execution`, ...), never `PLANNING`/`EXECUTION`.

### Pitfall 4: `from_state` on the first `TaskTransition` row needs an explicitly nullable enum column
**What goes wrong:** `Task.state`/`TaskTransition.to_state` are always non-null, but `TaskTransition.from_state` must be `None` for the creation event (Pattern 4). If the `Column(SAEnum(...))` definition omits `nullable=True`, the creation-row insert will raise an `IntegrityError` at the exact moment `create_task` is first exercised — likely to be caught only in manual/demo testing, not by a naive happy-path unit test that always transitions an already-created task.
**How to avoid:** `from_state: Optional[TaskState] = Field(default=None, sa_column=Column(SAEnum(TaskState, values_callable=...), nullable=True))` — explicit `nullable=True`, matching `Chat.current_leaf_message_id`'s existing nullable-FK pattern for the "no prior value" case.
**Detection:** A test that creates a task and immediately reads back its single `TaskTransition` row, asserting `from_state is None`.

### Pitfall 5: Cascade-delete coverage for two new tables
**What goes wrong:** Deleting a chat (`DELETE /api/v1/chats/{chat_id}`, already implemented) must cascade to `Task` and `TaskTransition` rows. If `Task.chat_id`'s FK uses `Field(foreign_key=...)` instead of `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`, or if `TaskTransition.task_id`'s FK is missing `ondelete="CASCADE"`, orphaned rows survive chat deletion (SQLite's `PRAGMA foreign_keys=ON` enforces the constraint but does not itself imply cascade — cascade is a property of the FK definition, not the pragma).
**How to avoid:** Both FKs (`Task.chat_id -> chat.id`, `Task.user_id -> user.id`, `TaskTransition.task_id -> task.id`) must use the `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` form, following `WorkingMemory`'s exact precedent.
**Detection:** Extend `tests/test_cascade_delete.py` with a case that creates a task + at least 2 transitions, deletes the owning chat, and asserts zero surviving `Task`/`TaskTransition` rows for that `chat_id`/`task_id`.

### Pitfall 6: The "sidebar tab" terminology in D-10/UI-SPEC does not match the codebase's actual UI structure
**What goes wrong:** [VERIFIED: direct read of `ui/static/index.html` lines 40-100] `#memory-panel` and `#profile-panel` are **not** a click-to-switch tab bar — they are two always-visible, vertically-stacked `<div>` sections inside the same `<aside>` sidebar, separated by `border-t border-slate-800`. There is no tab-switching JS anywhere in `app.js`. A planner or implementer who takes "new sidebar tab" literally and builds actual tab-click-to-show/hide UI would be introducing new interaction complexity this phase's CONTEXT.md never asked for and the UI-SPEC's own "Registry Safety"/"Design System" sections don't describe.
**How to avoid:** Build `#task-panel` as one more stacked `<div>` section, placed after `#profile-panel` and before `#agent-status`, using the identical `border-t border-slate-800 p-3 text-xs text-slate-400` container class already used by both existing panels. No tab-switching logic needed.
**Warning signs:** A plan or implementation that introduces `.tab-button`/`.tab-content` show/hide classes or `aria-selected` tab semantics that don't exist anywhere else in `index.html`.

## Code Examples

### `Task` / `TaskTransition` SQLModel schema

```python
# shared/models.py -- additions, following the exact ContextStrategy/Settings idiom
class TaskState(str, Enum):
    """Lifecycle state of a task (TASK-01 + D-07's cancelled extension)."""

    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(SQLModel, table=True):
    """A discrete, LLM-created unit of work tracked through an explicit lifecycle."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    chat_id: int = Field(
        sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False),
    )
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=5_000)
    goal: str = Field(default="", max_length=2_000)
    state: TaskState = Field(
        default=TaskState.PLANNING,
        sa_column=Column(
            SAEnum(TaskState, values_callable=lambda enum_cls: [m.value for m in enum_cls]),
            nullable=False,
        ),
    )
    is_paused: bool = Field(default=False)
    delegate_to: Optional[str] = Field(default=None, max_length=200)  # D-03: unused this phase
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskTransition(SQLModel, table=True):
    """Append-only audit trail of a task's state changes (TASK-05)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(
        sa_column=Column(Integer, ForeignKey("task.id", ondelete="CASCADE"), nullable=False),
    )
    from_state: Optional[TaskState] = Field(
        default=None,
        sa_column=Column(
            SAEnum(TaskState, values_callable=lambda enum_cls: [m.value for m in enum_cls]),
            nullable=True,
        ),
    )
    to_state: TaskState = Field(
        sa_column=Column(
            SAEnum(TaskState, values_callable=lambda enum_cls: [m.value for m in enum_cls]),
            nullable=False,
        ),
    )
    note: str = Field(default="", max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```
No migration function is required — both are brand-new tables, created by `shared/database.py::init_db()`'s existing unconditional `SQLModel.metadata.create_all` call (same reasoning as Phase 2's `WorkingMemory`/`LongTermMemory`).

### Tool argument schemas (excludes `cancelled` and `delegate_to` per D-07/D-03)

```python
# agent/schemas.py -- additions
class LlmTaskState(str, Enum):
    """States the LLM may transition a task into via transition_task -- excludes
    'cancelled' (D-07: cancel is a manual-only UI action, never an LLM tool)."""

    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DONE = "done"


TASK_TITLE_MAX_LENGTH = 200
TASK_DESCRIPTION_MAX_LENGTH = 5_000
TASK_GOAL_MAX_LENGTH = 2_000
TASK_NOTE_MAX_LENGTH = 2_000


class CreateTaskArgs(BaseModel):
    """Tool-call arguments for create_task."""

    title: str = Field(min_length=1, max_length=TASK_TITLE_MAX_LENGTH)
    description: str = Field(min_length=1, max_length=TASK_DESCRIPTION_MAX_LENGTH)
    goal: str = Field(min_length=1, max_length=TASK_GOAL_MAX_LENGTH)


class TransitionTaskArgs(BaseModel):
    """Tool-call arguments for transition_task."""

    task_id: int = Field(gt=0)
    new_state: LlmTaskState
    note: str = Field(default="", max_length=TASK_NOTE_MAX_LENGTH)


class PauseTaskArgs(BaseModel):
    """Tool-call arguments for pause_task."""

    task_id: int = Field(gt=0)


class ResumeTaskArgs(BaseModel):
    """Tool-call arguments for resume_task."""

    task_id: int = Field(gt=0)
```

### `GET /api/v1/chats/{chat_id}/tasks` response shape (single fetch, embedded history)

```python
class TaskTransitionResponse(BaseModel):
    from_state: Optional[str] = None
    to_state: str
    note: str = ""
    created_at: datetime


class TaskResponse(BaseModel):
    id: int
    title: str
    description: str
    goal: str
    state: str
    is_paused: bool
    delegate_to: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    history: list[TaskTransitionResponse]  # oldest-first, D-11
```
Recommend one combined GET (tasks + embedded history) rather than a separate `/history` endpoint per task — mirrors `GET /api/v1/chats/{chat_id}/memory`'s single-fetch shape and avoids N+1 REST calls when the sidebar panel loads.

## State of the Art

Not applicable in the "old vs. new library" sense — this phase adds a wholly new capability rather than replacing an existing approach. The one relevant "old approach to avoid" is architectural, not a library version:

| Old Approach (in this codebase, different feature) | Current Approach (this phase) | Why It Matters Here |
|---|---|---|
| `extract_and_update_facts`'s debounced, lock-free, fire-and-forget background task (`agent/context_engine.py`) | Task writes are synchronous, inside the same per-chat lock/session as the triggering WS turn (mirroring Phase 2's memory-write discipline, not the older facts-extraction pattern) | The codebase contains two different precedents for "an LLM-driven side effect fires from `ws.py`" — only one of them (Phase 2's memory dispatcher) is safe to copy for tasks; the older one is a documented anti-pattern, not a model to follow |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Pause/resume events should NOT be written to `TaskTransition` (only real state changes and creation are) | Architecture Patterns / Pattern 4 | Low-medium — if the graded demo wants to show "task was paused at 14:32, resumed at 14:40" in the same timeline as state changes, this is a small additive change (either a nullable `event_type` column on `TaskTransition`, or a second lightweight log); no schema redesign needed, just an extra column/table if the planner decides otherwise during review |
| A2 | A single combined `GET /api/v1/chats/{chat_id}/tasks` (tasks + embedded history) is sufficient for MVP, vs. a separate per-task history endpoint | Code Examples | Low — trivial to split into two endpoints later if the task list grows large enough that eager-loading full history becomes wasteful; not a concern at this project's scale (course demo, single user's chats) |
| A3 | REST pause/resume/cancel endpoints should acquire the chat's lock before writing | Architecture Patterns / Pattern 2, Common Pitfalls #1 | Medium if wrong in the other direction (i.e. if the planner decides NOT to lock) — a demo that never triggers the race condition would look fine, but the race is real and matches exactly the kind of subtle concurrency bug this project's own STATE.md repeatedly calls out as a hard constraint to get right (synchronous, per-chat-locked writes) |
| A4 | `transition_task`'s Pydantic arg model should hard-exclude `"cancelled"` as a valid `new_state`, enforced by a narrower `LlmTaskState` enum distinct from the DB's `TaskState` | Code Examples | Low — this is a direct, unambiguous reading of D-07 ("Cancel is reachable only via manual UI action, never an LLM tool"); the risk of NOT doing this is a hard requirement violation, not a design tradeoff |

**If this table is empty:** N/A — see entries above; none of these are compliance-critical claims (no retention policy, no security standard, no external API contract), all are internal architecture calls the planner can revise with low blast radius.

## Open Questions (RESOLVED)

1. **Should pause/resume be visible in the task's history timeline, distinct from state transitions?**
   - What we know: D-11 says history renders "state → state, timestamp" (state-transition-shaped). D-04 says pause is explicitly orthogonal to state.
   - What's unclear: Whether the graded demo/UI reviewer expects to *see* "paused" and "resumed" as timeline events even though they're not state changes.
   - Recommendation: Default to NOT logging pause/resume in `TaskTransition` (Pattern 4, Assumption A1). If the planner or a later `/bm:discuss-phase` pass on UI polish wants it, the cheapest addition is a nullable `TaskTransition.event_type: str = Field(default="transition")` column with values `"transition"`/`"pause"`/`"resume"`, added additively without touching the creation/transition code paths already built.
   - **RESOLVED:** Plan 04-03 follows the recommendation — pause/resume do not write `TaskTransition` rows; only real state changes and creation appear in the history timeline.

2. **Does `Task.updated_at` need to be bumped on every transition/pause/resume, or only left as the row's own last-write timestamp?**
   - What we know: Every other timestamped table in this codebase (`WorkingMemory`, `LongTermMemory`, `Profile`) updates `updated_at` on every write.
   - What's unclear: Nothing genuinely open here — this is a straightforward "match existing convention" call, listed only so the planner explicitly sets `task.updated_at = datetime.now(timezone.utc)` inside `transition_task`/`set_paused`/`cancel_task`, not just at creation.
   - Recommendation: Bump `updated_at` on every write to `Task`, matching every other table's convention. Not risky either way, just easy to forget.
   - **RESOLVED:** Plans 04-02 and 04-03 bump `Task.updated_at` on every write (transition, pause, resume, cancel), matching the existing convention.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|----------------|---------|--------------------|
| V2 Authentication | No (new to this phase) | Already handled by Phase 1 — `get_current_user`/`get_current_user_ws`, reused unchanged |
| V4 Access Control | Yes | Every task read/write MUST verify `task.user_id == current_user.id` (REST) or `task.user_id == user_id and task.chat_id == chat_id` (tool handlers, since the LLM supplies `task_id` per D-09) — reuse the `_get_chat_or_404`/`_get_task_or_404` IDOR-safe 404 pattern, never a bare 403 |
| V5 Input Validation | Yes | `title`/`description`/`goal`/`note` are LLM-generated (tool path) or user-supplied via no-input REST buttons (pause/resume/cancel take no free-text body) — validate all LLM-writable string fields via Pydantic length limits, following the existing `MEMORY_KEY_MAX_LENGTH`-style constant idiom in `agent/schemas.py` |
| V6 Cryptography | No | No new secrets/crypto surface introduced by this phase |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|------------------------|
| Cross-chat/cross-user task manipulation via an LLM-supplied `task_id` that doesn't belong to the calling chat/user (Pitfall 2) | Tampering / Information Disclosure | `agent/tasks.py`'s transition/pause/resume functions load the row and check `task.user_id`/`task.chat_id` before any write, mirroring `_get_chat_or_404` |
| Race between the manual REST bypass path and the WS tool-call path on the same task (Pitfall 1) | Tampering (data-integrity race, not an external attacker) | REST pause/resume/cancel acquire `chat_locks[task.chat_id]` before writing (Pattern 2) |
| Unbounded task-list growth inflating every future system prompt via the new task-injection block (Pattern 3), eventually contributing to a `no_compression`-strategy `ContextOverflowError` | Denial of Service (self-inflicted) | Not a hard requirement for MVP, but worth noting for the planner: `list_open_tasks` already naturally bounds itself by excluding `done`/`cancelled` tasks — only genuinely open work is ever injected, unlike `WorkingMemory`/`LongTermMemory`, which have no such natural ceiling |

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection this session: `shared/models.py`, `agent/tools.py`, `agent/memory.py`, `agent/profile.py`, `agent/schemas.py`, `agent/context_engine.py`, `agent/ws.py`, `agent/main.py`, `agent/state.py`, `shared/database.py`, `ui/static/index.html`, `ui/static/app.js`, `requirements.txt`
- `.planning/phases/02-memory-day-11/02-RESEARCH.md` and `02-PATTERNS.md` — direct precedent for the tool-call dispatcher, live-verified tool-calling wire format, and SQLModel FK-cascade idiom this phase extends unchanged
- `.planning/phases/04-task-state-machine-day-13/04-CONTEXT.md` and `04-UI-SPEC.md` — locked decisions and UI design contract, both read verbatim this session

### Secondary (MEDIUM confidence)
- None — no external web sources were needed this session; every claim traces to direct inspection of this repository's own already-verified prior research and source code.

### Tertiary (LOW confidence)
- None used.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new dependencies; every library already pinned and proven in this exact codebase
- Architecture (dispatcher extension, SQLModel schema, injection point, REST-bypass-with-lock): HIGH — direct extension of proven in-codebase conventions (Phase 2's dispatcher, Phase 3's UI-only-REST split), with the one new decision (Pattern 2's locking requirement) derived from a concrete, code-verified difference between `Profile` (user-scoped) and `Task` (chat-scoped)
- Pitfalls: HIGH — 5 of 6 pitfalls are directly verified against this codebase's own source (`SAEnum` idiom, cascade FK convention, existing `_SCOPE_KEYS` guard, actual `index.html` structure); 1 (the REST/WS race) is a reasoned architectural inference from the codebase's own locking convention, not empirically reproduced this session

**Research date:** 2026-09-20
**Valid until:** 2026-10-20 (30 days) — this phase's research is entirely internal-codebase-derived (no external API/library behavior that could drift), so the standard 30-day window applies rather than Phase 2's shortened window (which was tied to a specific local LM Studio installation's live behavior).
