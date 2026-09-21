# Phase 6: Controlled Transitions (Day 15) - Pattern Map

**Mapped:** 2026-09-21
**Files analyzed:** 12 (8 source modules edited in-place + 4 test files)
**Analogs found:** 12 / 12 (all analogs are same-file precedent or the immediately-adjacent sibling module — this phase is 100% additive hardening of existing modules, no new files)

**Special note on this phase's shape:** Every file this phase touches already exists and already contains the pattern to imitate — usually two paragraphs above/below the insertion point, or in the immediately-preceding phase's analogous feature (Phase 4 task lifecycle / Phase 5 invariant justify-retract). So "closest analog" below is frequently *the same file, a different function* rather than a different file. Read each excerpt as "copy this shape, in this file, for the new code."

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|---------------|
| `agent/tasks.py` (edit: `_LEGAL_EDGES`/`_TERMINAL_STATES`, `IllegalTransitionError`, legality gate in `transition_task`/`set_paused`/`cancel_task`) | service (CRUD domain layer) | CRUD + domain-validation | `agent/tasks.py::_get_owned_task` + `transition_task` itself (same file) | exact |
| `agent/tools.py` (edit: `dispatch_tool_calls` `ok=` computation; `_transition_task`/`_pause_task`/`_resume_task` catch `IllegalTransitionError`) | controller (tool dispatcher) | request-response | `agent/tools.py::_transition_task`'s existing `TaskNotFoundError` catch (same file) | exact |
| `agent/ws.py` (edit: D-08 justify/retract round-trip block) | controller (WS orchestration) | streaming / event-driven | `agent/ws.py:361-386` — existing invariant justify/retract block (same file) | exact |
| `agent/main.py` (edit: pause/resume/cancel endpoints catch `IllegalTransitionError` → HTTP 409) | controller (REST endpoint) | request-response | `agent/main.py::_get_task_or_404` + `pause_task_endpoint`/`cancel_task_endpoint` (same file) | exact |
| `shared/models.py` (edit: `TaskTransition` gains `rejected`, `rejection_reason` columns) | model | CRUD | `shared/models.py::TaskTransition` (same class, additive fields) | exact |
| `shared/database.py` (new function: `migrate_add_task_transition_rejection_columns`) | migration | batch (idempotent DDL) | `shared/database.py::migrate_add_context_length` / `migrate_add_user_id_columns` | exact |
| `agent/schemas.py` (edit: `TaskTransitionResponse` gains `rejected`/`rejection_reason`) | model (Pydantic response schema) | transform | `agent/schemas.py::TaskTransitionResponse` (same class, additive fields) + `TASK_NOTE_MAX_LENGTH` constant precedent | exact |
| `ui/static/app.js` (edit: `renderTaskHistory` renders `rejected=true` rows distinctly) | component (vanilla-JS render fn) | transform (DOM render) | `ui/static/app.js::renderTaskHistory` (same function, same file) | exact |
| `tests/test_tasks.py` (new tests: illegal-transition/pause/resume/cancel rejection, dispatcher-level) | test | request-response | `tests/test_tasks.py::test_transition_task_other_users_task_is_rejected` (same file) | exact |
| `tests/test_task_ws.py` (new tests: WS `TOOL_ERROR` + justify/retract round-trip) | test | streaming / event-driven | `tests/test_invariants_ws.py::test_flagged_conflict_triggers_justify_retract_and_persists` | role-match (invariant WS test is the closest wired-multi-response respx pattern) |
| `tests/test_task_api.py` (new tests: REST 409 for cancel/pause/resume terminal-state guards) | test | request-response | existing REST task-endpoint tests in `tests/test_task_api.py` (same file, sibling tests) | exact |
| `tests/test_database.py` (new test: migration idempotency for the two new columns) | test | batch | `tests/test_database.py::test_init_db_migrates_branching_strategy` / `test_init_db_creates_all_tables` | exact |

## Pattern Assignments

### `agent/tasks.py` (service, CRUD + domain-validation)

**Analog:** same file — `_get_owned_task` (lines 79-95) and `transition_task` (lines 98-132), `set_paused` (135-158), `cancel_task` (161-197).

**Imports pattern** (lines 1-11):
```python
"""Thin CRUD layer owning all reads and writes to the task tables."""

from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import Task, TaskState, TaskTransition

logger = get_logger(__name__)
```
The new `_LEGAL_EDGES`/`_TERMINAL_STATES`/`IllegalTransitionError` need no new imports beyond what's already here.

**Existing exception pattern to mirror exactly** (lines 14-15):
```python
class TaskNotFoundError(Exception):
    """Raised when a task_id does not resolve to a row owned by the calling chat/user."""
```
`IllegalTransitionError` should follow this same one-line-docstring, no-`__init__`-boilerplate style unless it needs to carry `task_id`/`from_state`/`to_state` structured data for the D-08 re-prompt (RESEARCH.md's drafted version does carry those — keep the docstring convention, add `__init__` only because the caller needs the fields).

**Ownership-first ordering (Pitfall 3 — security-critical, must not change)** (lines 111-112, mirrored at 147, 172):
```python
async def transition_task(...) -> Task:
    ...
    task = await _get_owned_task(session, user_id, chat_id, task_id)   # MUST stay first
    previous_state = task.state
    task.state = new_state
    ...
```
Insert the new legality check **immediately after** this line, never before — an IDOR legality-oracle otherwise (see Common Pitfalls in RESEARCH.md).

**Commit/rollback scaffolding to copy verbatim for the new illegal-branch write** (lines 119-124):
```python
    try:
        await session.commit()
        await session.refresh(task)
    except Exception:
        await session.rollback()
        raise
```

**Logging pattern to mirror for the new `task_transition_rejected` event** (lines 125-131):
```python
    logger.info(
        "task_transitioned",
        task_id=task.id,
        chat_id=chat_id,
        from_state=previous_state.value,
        to_state=new_state.value,
    )
```
New rejection path: `logger.warning("task_transition_rejected", task_id=..., chat_id=..., from_state=..., to_state=...)` — `warning`, not `info`, since this is a recoverable-but-notable event per CLAUDE.md's logging-level guidance.

**`set_paused`'s existing shape (D-03 terminal/no-op guards attach here)** (lines 135-158):
```python
async def set_paused(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    task_id: int,
    is_paused: bool,
) -> Task:
    """Toggle an owned task's is_paused flag without touching its lifecycle state.
    ...
    """
    task = await _get_owned_task(session, user_id, chat_id, task_id)
    task.is_paused = is_paused
    ...
```
Insert terminal-state check and (for `is_paused=False`, i.e. resume) the no-op check right after `_get_owned_task`, before any mutation — same ordering rule as above.

---

### `agent/tools.py` (controller/tool-dispatcher, request-response)

**Analog:** same file — `dispatch_tool_calls` (lines 76-163) and `_transition_task` (228-246), `_pause_task` (256-273), `_resume_task` (282-299).

**Imports pattern** (lines 1-22) — unchanged, `tasks.IllegalTransitionError` is reached via the existing `from agent import memory, tasks` import (line 10); no new import line needed.

**The exact one-line dispatcher fix (D-05/D-06), current code** (lines 150-162):
```python
        result = await TOOL_REGISTRY[name](session, user_id, chat_id, validated_args)
        ...
        results.append(
            {
                "tool_call_id": tool_call_id,
                "name": name,
                "ok": True,                       # <- change to: result.get("status") != "error"
                "content": json.dumps(result),
                "write": {
                    "id": result.get("id"),
                    "key": result.get("key"),
                    "layer": result.get("layer"),
                },
            },
        )
```
Also gate `"write"` to `None` when `status == "error"` (RESEARCH.md Pattern 2) so a rejected task-lifecycle call never produces a phantom `task_writes`/`memory_writes` entry downstream in `ws.py`.

**Existing `TaskNotFoundError` catch shape to extend with `IllegalTransitionError`** (lines 234-246):
```python
async def _transition_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Transition a task owned by this chat/user; returns an error dict otherwise."""
    try:
        row = await tasks.transition_task(
            session,
            user_id,
            chat_id,
            args["task_id"],
            TaskState(args["new_state"]),
            args.get("note", ""),
        )
    except tasks.TaskNotFoundError:
        return {"status": "error", "error": f"task {args['task_id']} not found in this chat"}
    return {"status": "transitioned", "id": row.id, "title": row.title, "state": row.state.value}
```
Add `except tasks.IllegalTransitionError as exc:` returning `{"status": "error", "code": "illegal_transition", "error": str(exc), "task_id": exc.task_id, "from_state": exc.from_state.value, "to_state": exc.to_state.value}` — the same dict shape, with a new `"code"` discriminator field `agent/ws.py` reads for D-08. Give the existing `not_found` branch a matching `"code": "not_found"` field too, so both error families share one discriminator convention (D-06's "harmonize both error families through one path").

**Identical catch shape repeats in `_pause_task` (263-266) and `_resume_task` (289-292)** — apply the same `except tasks.IllegalTransitionError` addition to both, per D-06's "uniformly across all three task-lifecycle tools."

---

### `agent/ws.py` (controller/WS orchestration, streaming/event-driven)

**Analog:** same file — the existing invariant justify/retract block (lines 361-386), plus the existing `TOOL_ERROR` frame loop (332-346) it must be inserted after.

**Existing `TOOL_ERROR` loop — no changes needed here, only wider input via the tools.py fix above** (lines 332-346):
```python
                for result in tool_results:
                    if not result["ok"]:
                        try:
                            error_detail = json.loads(result["content"]).get(
                                "error", result["content"],
                            )
                        except json.JSONDecodeError:
                            error_detail = result["content"]
                        await websocket.send_json(
                            {
                                "type": "error",
                                "detail": error_detail,
                                "code": "TOOL_ERROR",
                            },
                        )
```

**Exact shape to mirror for the new D-08 round-trip, inserted immediately after the block above and before line 348's `active_invariants = ...`** (lines 361-386, invariant precedent):
```python
            if flagged is not None:
                llm_messages.append(
                    {
                        "role": "user",
                        "content": invariants.build_justify_retract_prompt(flagged, critique),
                    },
                )
                try:
                    async for token in llm_client.stream_chat(
                        llm_messages,
                        payload.model,
                        temperature,
                        max_tokens,
                    ):
                        justification_text += token
                        assistant_text += token
                        await websocket.send_json(
                            {"type": "token", "content": token},
                        )
                except Exception as exc:
                    logger.warning(
                        "invariant_justify_retract_failed",
                        chat_id=chat_id,
                        error=str(exc),
                    )
```
New block: build `rejected_transitions` by filtering `tool_results` for `not result["ok"] and json.loads(result["content"]).get("code") == "illegal_transition"`, call `tasks.build_transition_illegal_prompt(rejected_transitions)`, append as `role: "user"`, one more `stream_chat` loop appending straight into `assistant_text` (no separate `justification_text`-equivalent needed unless the planner wants D-13-style dedicated persistence — D-09/D-10 already persist via the `TaskTransition(rejected=True)` row written inside `tasks.py`, not via a WS-level record like `invariants.record_conflict`). Wrap in the identical `try/except Exception` + `logger.warning(..., error=str(exc))` fails-open pattern — never let this abort the turn (matches CLAUDE.md's "avoid bare except" + this file's existing convention of catching `Exception` specifically here, with a comment-worthy rationale of "never crash the WS handler").

**Where task_writes is assembled — do not add rejected transitions here** (lines 318-330):
```python
                for result in tool_results:
                    if result["ok"] and result["name"] in TASK_TOOL_NAMES:
                        try:
                            task_result = json.loads(result["content"])
                        except json.JSONDecodeError:
                            continue
                        task_writes.append(
                            {
                                "id": task_result.get("id"),
                                "title": task_result.get("title"),
                                "state": task_result.get("state"),
                            },
                        )
```
This is already gated on `result["ok"]`, so once the D-05/D-06 dispatcher fix ships, rejected calls automatically fall out of `task_writes` with zero further changes here — confirmed by the WS test pattern in RESEARCH.md (`done_frame["task_writes"] == []`).

---

### `agent/main.py` (controller/REST endpoint, request-response)

**Analog:** same file — `_get_task_or_404` (lines 112-125) and the three sibling endpoints `pause_task_endpoint`/`resume_task_endpoint`/`cancel_task_endpoint` (lines 682-724).

**IDOR-safe 404 pattern, unchanged, reused as-is** (lines 112-125):
```python
async def _get_task_or_404(
    session: AsyncSession,
    task_id: int,
    user_id: int,
) -> Task:
    """Load a task owned by user_id or raise HTTP 404 (never 403, to avoid an IDOR oracle)."""
    task = await session.get(Task, task_id)
    if task is None or task.user_id != user_id:
        logger.warning("task_access_denied", task_id=task_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return task
```

**Exact endpoint shape to extend with a `try/except tasks.IllegalTransitionError` → `HTTPException(409, ...)`** (lines 682-724, e.g. `cancel_task_endpoint`):
```python
@app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task_endpoint(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Cancel a task directly, bypassing the tool dispatcher (D-07/D-12: manual-only)."""
    task = await _get_task_or_404(session, task_id, current_user.id)
    if task.chat_id not in chat_locks:
        chat_locks[task.chat_id] = asyncio.Lock()
    async with chat_locks[task.chat_id]:
        row = await tasks.cancel_task(session, current_user.id, task.chat_id, task_id)
        return await _task_response_with_history(session, row)
```
New shape: wrap the `await tasks.cancel_task(...)` (and the `pause`/`resume` equivalents' `await tasks.set_paused(...)`) call in `try: ... except tasks.IllegalTransitionError as exc: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc`. Keep the `chat_locks[...]` acquisition exactly as-is — concurrency guarding is orthogonal to this change (per RESEARCH.md's Security Domain notes, this is already race-safe). Follow the existing `HTTPException(status_code=status.HTTP_NNN, detail="message")` convention from CLAUDE.md's Error Handling section — 409 Conflict is a new status code for this codebase (only 400/401/404/201/204 exist today) but is the semantically correct RESTful choice for "action blocked by current resource state."

---

### `shared/models.py` (model, CRUD)

**Analog:** same class — `TaskTransition` (lines 336 onward).

**Existing field-definition style to mirror for the two new columns** (lines 336-360, truncated):
```python
class TaskTransition(SQLModel, table=True):
    """Append-only state-change history for a Task (D-11)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("task.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    from_state: Optional[TaskState] = Field(
        default=None,
        sa_column=Column(
            SAEnum(
                TaskState,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=True,
        ),
    )
    to_state: TaskState = Field(
        sa_column=Column(
            SAEnum(...),
            ...
        ),
    )
```
New fields: `rejected: bool = Field(default=False)` (plain `Field`, no `sa_column` needed — matches `Task.is_paused: bool = Field(default=False)` style at line 326) and `rejection_reason: Optional[str] = Field(default=None, max_length=<TASK_NOTE_MAX_LENGTH-equivalent>)` — per CONTEXT.md's discretion note, reuse `agent/schemas.py::TASK_NOTE_MAX_LENGTH` (2,000) as the length ceiling for consistency, applied at the Pydantic-schema layer (`agent/schemas.py`) rather than as a SQLite column constraint (SQLite doesn't enforce `VARCHAR(n)` length anyway — matches how `note: str = Field(default="", max_length=5_000)` on `Task.description` is handled: SQLModel `max_length` here is Pydantic-side validation metadata only for API-facing schemas, not a DB constraint, so keep the model-layer field a plain unconstrained `Optional[str]` and enforce length in `agent/schemas.py`'s Pydantic model instead, consistent with how the codebase already separates ORM models from API schemas).

---

### `shared/database.py` (migration, batch/idempotent DDL)

**Analog:** `migrate_add_context_length` (lines 75-94) and `migrate_add_user_id_columns` (97-118) — copy this exact shape for the new function.

**Exact pattern to copy verbatim** (lines 75-94):
```python
async def migrate_add_context_length(conn: Any) -> None:
    """Add context_length column to settings when missing (idempotent)."""
    table_check = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='settings'",
        ),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(settings)"))
    columns = [row[1] for row in result.fetchall()]
    if "context_length" not in columns:
        logger.info("migrating_settings_add_context_length")
        await conn.execute(
            text(
                "ALTER TABLE settings ADD COLUMN context_length INTEGER DEFAULT 4096",
            ),
        )
```
New function: `migrate_add_task_transition_rejection_columns(conn)` — table name `tasktransition` (verified table-naming convention: lowercase class name, confirmed by `tests/test_database.py::test_init_db_creates_all_tables`'s expected table-name set, which includes `"tasktransition"`), two `ALTER TABLE tasktransition ADD COLUMN rejected BOOLEAN DEFAULT 0` / `ALTER TABLE tasktransition ADD COLUMN rejection_reason TEXT` statements, one `PRAGMA table_info(tasktransition)` check covering both columns (mirror `migrate_add_user_id_columns`'s loop-over-two-tables shape if a loop-over-two-columns reads cleaner, or two sequential `if` blocks like `migrate_add_context_length`).

**Call-site ordering — must run before `create_all`** (lines 177-183):
```python
async def init_db() -> None:
    """Create all database tables if they do not exist."""
    async with engine.begin() as conn:
        await migrate_add_context_length(conn)
        await migrate_add_user_id_columns(conn)
        await conn.run_sync(SQLModel.metadata.create_all)
        await _migrate_legacy_strategies(conn)
```
Add `await migrate_add_task_transition_rejection_columns(conn)` alongside the other two migration calls, before `create_all` (Pitfall 1 in RESEARCH.md — `create_all` never adds columns to an existing table).

---

### `agent/schemas.py` (model/Pydantic response schema, transform)

**Analog:** same class — `TaskTransitionResponse` (lines 293-299), and `TASK_NOTE_MAX_LENGTH` constant (line 25) as the length-limit precedent.

**Existing shape to extend** (lines 293-299):
```python
class TaskTransitionResponse(BaseModel):
    """Serialized task state-change history entry."""

    from_state: Optional[str] = None
    to_state: str
    note: str = ""
    created_at: datetime
```
Add `rejected: bool = False` and `rejection_reason: Optional[str] = None` fields, matching the existing `Optional[...] = None` / plain-default style already used in this exact class.

**Existing max-length constant to reuse (per CONTEXT.md discretion note)** (line 25, and its use at line 270):
```python
TASK_NOTE_MAX_LENGTH = 2_000
...
    note: str = Field(
        default="",
        max_length=TASK_NOTE_MAX_LENGTH,
        description="Optional justification or context for this transition",
    )
```
If `rejection_reason` needs a length cap surfaced anywhere at the Pydantic layer (e.g. a request/args schema, unlikely since it's server-generated, but relevant if `_task_to_response`-style mapping ever needs to truncate), reuse `TASK_NOTE_MAX_LENGTH` directly rather than introducing a new constant.

---

### `ui/static/app.js` (component, transform/DOM-render)

**Analog:** same function — `renderTaskHistory` (lines 355-372), and the `TASK_STATE_LABELS`/`TASK_STATE_BADGE_CLASSES` color-mapping precedent (lines 38-52).

**Exact function to extend** (lines 355-372):
```javascript
function renderTaskHistory(container, history) {
    container.replaceChildren();
    history.forEach((entry) => {
        const fromLabel = entry.from_state ? TASK_STATE_LABELS[entry.from_state] : 'создана';
        const line = document.createElement('div');
        line.className = 'text-slate-500';
        line.textContent =
            `${fromLabel} → ${TASK_STATE_LABELS[entry.to_state]} · ${formatTaskTimestamp(entry.created_at)}`;
        container.appendChild(line);

        if (entry.note) {
            const noteEl = document.createElement('div');
            noteEl.className = 'text-slate-600 pl-2';
            noteEl.textContent = entry.note;
            container.appendChild(noteEl);
        }
    });
}
```
D-11's "visually distinct" rejected row: branch on `entry.rejected` inside the `forEach`, using a red/strikethrough Tailwind class (matching the existing `TASK_STATE_BADGE_CLASSES.cancelled = 'text-red-400'` color precedent at line 51) and a label like `attempted → ${TASK_STATE_LABELS[entry.to_state]}: rejected (${entry.rejection_reason})`, e.g.:
```javascript
        if (entry.rejected) {
            line.className = 'text-red-400 line-through';
            line.textContent = `попытка → ${TASK_STATE_LABELS[entry.to_state]}: отклонено (${entry.rejection_reason || ''})`;
        } else {
            line.className = 'text-slate-500';
            line.textContent = `${fromLabel} → ${TASK_STATE_LABELS[entry.to_state]} · ${formatTaskTimestamp(entry.created_at)}`;
        }
```
Note the codebase's existing Tasks-tab UI copy is in Russian (`TASK_STATE_LABELS`, toast messages like `'⚠️ Обнаружен конфликт с инвариантом'` in `handleWsMessage`) — match that language for any new user-facing string, don't introduce English strings inconsistently.

**No changes needed in `handleWsMessage`'s generic error-frame handler** (lines 1173-1189) — it already handles `type: "error"` frames generically via `showToast(data.detail || 'Ошибка', 'error')`; D-05/D-06 only widens what reaches this existing branch, confirmed no new `case` needed.

---

### `tests/test_tasks.py` (test, dispatcher-level)

**Analog:** same file — `test_transition_task_other_users_task_is_rejected` and its neighbors (lines 418-451+), plus the shared `_seed_chat`/`_create_task_via_tool`/`_call` helpers (lines 15-50).

**Existing helper/fixture shape to reuse verbatim, no new helpers needed:**
```python
async def _seed_chat(user_id: int, title: str = "Tasks chat") -> int:
    """Create and return the id of a chat owned by user_id."""
    return await _create_chat(user_id, title)


def _call(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


async def _create_task_via_tool(
    user_id: int,
    chat_id: int,
    title: str = "Task",
    description: str = "Description",
    goal: str = "Goal",
) -> int:
    """Dispatch a create_task tool call and return the created task's id."""
    calls = [
        _call(
            "call_create",
            "create_task",
            json.dumps({"title": title, "description": description, "goal": goal}),
        ),
    ]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)
    return json.loads(results[0]["content"])["id"]
```
New tests (`test_transition_task_rejects_skip_ahead`, `test_transition_task_rejects_terminal_exit`, `test_pause_task_rejects_terminal_task`, `test_resume_task_rejects_when_not_paused`, `test_cancel_task_rejects_already_done`) should call `dispatch_tool_calls` the same way, then assert `results[0]["ok"] is False` (the new D-05/D-06 behavior) and query `TaskTransition` rows directly via `select(TaskTransition).where(TaskTransition.task_id == task_id)` the way existing tests already do — see RESEARCH.md's "Rejected-transition test pattern" for the full worked example (verified consistent with this file's existing `async_session_factory()`-per-block style).

---

### `tests/test_task_ws.py` (test, WS streaming/event-driven)

**Analog:** `tests/test_invariants_ws.py::test_flagged_conflict_triggers_justify_retract_and_persists` — closest existing multi-round-trip respx-queue WS test.

**Pattern to mirror (respx multi-response queue + `client.websocket_connect` + frame-draining helper)** — verified structurally present in `tests/test_invariants_ws.py`; RESEARCH.md's "WS-level test pattern for the justify/retract round-trip" is the concrete worked adaptation for this phase (queues: tool-call response → unconditional follow-up → D-08 re-prompt reply; asserts exactly one `TOOL_ERROR` frame, re-prompt reply text appears in streamed tokens, and `done_frame["task_writes"] == []`).

---

### `tests/test_task_api.py` (test, REST request-response)

**Analog:** existing sibling tests in the same file for `pause_task_endpoint`/`resume_task_endpoint`/`cancel_task_endpoint` (exact line numbers not re-read here — same file, same fixture conventions as `tests/test_tasks.py`'s `authenticated_client`).

**Pattern:** RESEARCH.md's "REST 409 test pattern" — seed a task already in a terminal state, POST to the cancel/pause/resume endpoint, assert `resp.status_code == 409` and a human-readable `detail` string, matching this codebase's existing `HTTPException(status_code=..., detail="...")` convention (CLAUDE.md Error Handling section).

---

### `tests/test_database.py` (test, migration idempotency)

**Analog:** `test_init_db_migrates_branching_strategy` (lines 28-43) and `test_init_db_creates_all_tables` (lines 46-73).

**Pattern to mirror for the new migration test** (lines 46-73):
```python
@pytest.mark.asyncio
async def test_init_db_creates_all_tables() -> None:
    """init_db() should create chat, message, settings, ... task, and invariant tables."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ),
        )
        tables = {row[0] for row in result.fetchall()}
    assert tables == { ... "tasktransition", ... }
```
New test (`test_migrate_add_task_transition_rejection_columns_is_idempotent`): call `init_db()` twice in a row against the same connection/engine (or directly invoke the new migration function twice), then run `PRAGMA table_info(tasktransition)` and assert both `rejected`/`rejection_reason` columns exist exactly once each and no `OperationalError` is raised on the second call — mirrors the "idempotent" framing already baked into every existing migration function's docstring (`"...when missing (idempotent)."`).

## Shared Patterns

### Ownership-then-legality ordering (security-critical)
**Source:** `agent/tasks.py::_get_owned_task` called first in `transition_task`/`set_paused`/`cancel_task` (lines 111, 147, 172); `agent/main.py::_get_task_or_404` (lines 112-125).
**Apply to:** Every new legality check in `agent/tasks.py`. Never reorder — a legality check running before ownership resolution creates an IDOR state-disclosure oracle (RESEARCH.md Pitfall 3 / Security Domain).
```python
task = await _get_owned_task(session, user_id, chat_id, task_id)  # always first
# legality/terminal/no-op checks go here, never above
```

### Commit/rollback scaffolding
**Source:** every write function in `agent/tasks.py` and `agent/invariants.py` (e.g. `agent/tasks.py:119-124`, `agent/invariants.py:364-369`).
**Apply to:** the new rejected-`TaskTransition` write inside `transition_task`, and any other new write path.
```python
try:
    await session.commit()
    await session.refresh(task)
except Exception:
    await session.rollback()
    raise
```

### `ok`/error-content dispatcher convention
**Source:** `agent/tools.py::dispatch_tool_calls` (lines 143-162) — see full excerpt in `agent/tools.py`'s Pattern Assignment section above.
**Apply to:** `agent/tools.py` only; downstream (`agent/ws.py`'s `TOOL_ERROR` loop) needs zero changes beyond receiving more `ok=False` results.

### Fails-open LLM re-prompt round-trip
**Source:** `agent/invariants.py::run_self_critique` (lines 285-308, "Fails open" docstring) and `agent/ws.py`'s invariant justify/retract block (361-386, `except Exception as exc: logger.warning(...)`).
**Apply to:** the new D-08 transition-illegal re-prompt block in `agent/ws.py` — a failure here (timeout, malformed stream) must never abort the WS turn or crash the handler.

### Idempotent additive-column migration
**Source:** `shared/database.py::migrate_add_context_length` / `migrate_add_user_id_columns` (lines 75-118).
**Apply to:** the new `migrate_add_task_transition_rejection_columns`, called from `init_db()` (177-183) before `create_all`.

### structlog logging conventions
**Source:** `agent/tasks.py::transition_task`'s `logger.info("task_transitioned", ...)` (125-131); `agent/invariants.py::record_conflict`'s `logger.warning("invariant_conflict_detected", ...)` (370-375).
**Apply to:** new `logger.warning("task_transition_rejected", task_id=..., chat_id=..., from_state=..., to_state=...)` call sites in `agent/tasks.py` — `snake_case` action-key first argument, `key=value` pairs after, `warning` level (not `error`) since this is a recoverable domain-rejection, not a system failure.

## No Analog Found

None. Every file this phase touches already exists in the codebase with a directly analogous function/class in the same file (or, for `agent/ws.py`'s D-08 block, an almost-identical sibling block already in the same file from Phase 5). This is expected for a "hardening" phase per RESEARCH.md's framing ("every piece of this phase is a 'widen an existing mechanism,' never 'introduce a new mechanism'").

## Conventions

Convention derivation was run via the shared deterministic tool (`gsd-tools.cjs verify conventions --derive`), repo-wide (no `--scope` given, since this phase's file set spans `agent/`, `shared/`, `ui/static/`, and `tests/` — no single subtree covers it). The tool is JS/TS-oriented (it inspects identifier casing, export style, import style using JS/TS heuristics) and this repository is overwhelmingly Python; only `ui/static/app.js` produced any signal.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | camelCase (n=1) | 0% (insufficient data) | n/a | insufficient-data |
| Identifier casing | camelCase | 100% (71/71) | 0 | named contract |
| Export style | — | 0% (no data) | n/a | insufficient-data |
| Import style | — | 0% (no data) | n/a | insufficient-data |

**Reading this table:** the tool only scanned `ui/static/app.js` (the repo's one JS file); its 100%-camelCase identifier-casing finding is a **named contract** for that file only (confirmed directly by this phase's own `renderTaskHistory` excerpt above — `fromLabel`, `noteEl`, `container`, all camelCase). File-name casing, export style, and import style are `insufficient-data` because there is only one JS file and it uses neither ES-module exports/imports nor a second file to compare naming against — not a signal to act on.

For everything else this phase touches (Python: `agent/`, `shared/`, `tests/`), the repo's actual named-contract conventions come directly from `CLAUDE.md` (checked-in, verified against every file read above) rather than this JS-oriented tool: `lowercase_with_underscores.py` filenames, `snake_case` functions/variables, `PascalCase` classes/exceptions (`...Error` suffix), `UPPER_CASE` module constants (`TASK_NOTE_MAX_LENGTH`), `_`-prefixed private helpers (`_get_owned_task`, `_legal_targets`), explicit type hints and `str | None`-style unions everywhere, and `structlog`'s `logger = get_logger(__name__)` + `snake_case_action` message-key convention. All code excerpts above already conform to these; new code in this phase should match them exactly (e.g. `IllegalTransitionError` — `PascalCase` + `Error` suffix; `_LEGAL_EDGES`/`_TERMINAL_STATES` — private module-level constants, `UPPER_CASE` per the "Constants: UPPER_CASE" convention, `_`-prefixed per the "private module-level variables" convention — CLAUDE.md's two constant-naming rules combine here since these are both private and constant, so `_UPPER_CASE` is correct).

**Contested hotspots (author's choice) note:** This repo does not contain the CJS↔SDK dual-resolver split that the `gsd-tools.cjs` prototype's own codebase uses as its canonical contested-hotspot example (`bin/lib/**` CJS vs `sdk/src/**` ESM) — that pattern is specific to the tooling repo, not this project. This project has no comparable intentional per-directory style split; its one genuine "author's choice" axis for this phase is the RESEARCH.md-flagged **sentinel-field vs exception-propagation** choice for `dispatch_tool_calls`'s `ok=` computation (Pattern 2) — RESEARCH.md's Alternatives Considered table already resolves this in favor of the sentinel-field approach for a smaller diff; treat that as locked unless the planner has a reason to prefer the exception-propagation alternative.

## Metadata

**Analog search scope:** `agent/tasks.py`, `agent/tools.py`, `agent/ws.py`, `agent/main.py`, `agent/invariants.py`, `agent/schemas.py`, `shared/models.py`, `shared/database.py`, `ui/static/app.js`, `tests/test_tasks.py`, `tests/test_database.py` — all read directly this session (RESEARCH.md's own primary-source list overlaps entirely with this).
**Files scanned:** 11 source/test files read directly; 0 new files to be created (100% additive edits to existing files, confirmed by RESEARCH.md's "Recommended Project Structure — No new files").
**Pattern extraction date:** 2026-09-21
