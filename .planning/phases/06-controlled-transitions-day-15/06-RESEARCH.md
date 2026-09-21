# Phase 6: Controlled Transitions (Day 15) - Research

**Researched:** 2026-09-21
**Domain:** Server-side finite-state-machine enforcement in an async FastAPI + SQLModel app; WebSocket error-frame wiring; SQLite additive schema migration without a migration tool
**Confidence:** HIGH (this phase is 100% internal-codebase work — no new libraries, no external API surface. All findings below are verified by direct reads of the actual files this phase modifies, not by external docs.)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Transition graph shape**
- **D-01:** The graph is `planning → execution → validation → done`, plus **one-step-back rework moves**: `execution → planning` and `validation → execution`. `done` and `cancelled` are terminal — **no transition out of either**, forward or backward.
- **D-02:** **No skipping.** Every `transition_task` call must move exactly one edge along the graph (one step forward or one step back) — e.g. `planning → done` directly is illegal, and so is `validation → planning` (two steps back).
- **D-03:** `pause_task`/`resume_task` are graph-guarded too: `resume_task` rejects if the task's `is_paused` is already `False` (no-op protection); both `pause_task` and `resume_task` reject if the task is already in a terminal state (`done`/`cancelled`).
- **D-04:** `cancel_task` (manual REST-only, per Phase 4's D-07/D-12) rejects once the task is already `done` or `cancelled` — cancel is no longer a no-questions-asked override from every state.

**Rejection channel**
- **D-05:** A new `IllegalTransitionError` (and the existing `TaskNotFoundError`) causes the tool dispatcher to mark that result **`ok=False`**, firing the existing WS `{type: "error", code: "TOOL_ERROR"}` frame (`agent/ws.py:332-346`) **unconditionally** — no longer swallowed into `ok=True` tool-result content that depends on the LLM choosing to mention it.
- **D-06:** This `ok=False` promotion applies uniformly to **all three task-lifecycle tools** — `transition_task`, `pause_task`, `resume_task` — for both graph-legality rejections and task-not-found rejections. This changes existing Phase 4 behavior: `TaskNotFoundError` previously stayed `ok=True` (tool-content-only).

**Phase 5 conflict-check wiring**
- **D-07:** **No new invariant-vs-transition legality check is added.** Invariants (Phase 5) do not gain the power to block a transition — `agent/invariants.py` is not called from the transition path. Reuses the **self-critique round-trip's pattern**, not its code or its authority.
- **D-08:** An illegal transition triggers a **dedicated justify/retract-style re-prompt** to the LLM — a new prompt mirroring `build_justify_retract_prompt`'s shape/wording (`agent/invariants.py:330-344`), explaining specifically why the transition was illegal (e.g. "you tried to move task #4 from `planning` to `done`, but `validation` must come first") and asking the LLM to either pick a legal transition or explain to the user why it attempted the illegal one. Distinct from a plain tool-result-content error with no further prompt engineering.

**Rejected-attempt logging**
- **D-09:** `TaskTransition` gets two new columns: `rejected: bool = False`, `rejection_reason: str | None`. A rejected attempt is written as a row with `from_state` = the task's actual current state, `to_state` = the illegal target state that was attempted, `rejected=True`, `rejection_reason=<why>`.
- **D-10:** **No new table** — accepted and rejected events share one chronological `TaskTransition` history.
- **D-11:** In the Tasks tab's history list, rejected rows render **inline with real transitions, visually distinct** (e.g. red/strikethrough) — e.g. "attempted → done: rejected (validation required first)".

### Claude's Discretion
- Exact wording of the new justify/retract-style prompt (D-08) — follow `build_justify_retract_prompt`'s tone/structure, adapted for transition-legality language.
- Exact mechanics of how the D-08 re-prompt interacts with the existing unconditional post-tool-dispatch follow-up call in `agent/ws.py:284-310` — whether it's an additional round-trip or folded into that existing call. Keep the rest of that flow (`memory_writes`/`task_writes` assembly, WS `done` frame) intact.
- Whether `pause_task`/`resume_task`'s promotion (D-06) reuses the exact `IllegalTransitionError` type or gets its own error type/message per tool — functionally equivalent, cosmetic choice.
- Exact max-length/nullability for `TaskTransition.rejection_reason` (D-09) — recommend matching the existing `note` field's `TASK_NOTE_MAX_LENGTH` precedent (`agent/schemas.py`) unless there's a reason to differ.
- Whether REST-triggered rejections (e.g. `cancel_task` blocked on terminal state, D-04) surface as `HTTPException` 4xx responses (matching the existing `_get_task_or_404` convention in `agent/main.py`) rather than the WS `TOOL_ERROR` frame.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TRANS-01 | Task states have an explicit set of allowed transitions; illegal transitions are rejected | See "Architecture Patterns → Pattern 1: Graph-legality edge table" and "Code Examples → transition_task legality gate" |
| TRANS-02 | Attempting an illegal transition produces a clear, explainable rejection rather than silently succeeding or crashing | See "Architecture Patterns → Pattern 2: ok=False promotion via `status` sentinel" and "Pattern 3: D-08 justify/retract re-prompt wiring" |
| TRANS-03 | Task execution correctly resumes from a paused state without violating the transition graph | See "Architecture Patterns → Pattern 1" (pause/resume terminal/no-op guards) and "Common Pitfalls → Pitfall 4 (working-memory vs compressed context as resume source of truth)" |
</phase_requirements>

## Summary

This phase is pure internal hardening — no new dependency, no new external API, no new UI framework. Every fact below was verified by reading the actual files this phase touches (`agent/tasks.py`, `agent/tools.py`, `agent/ws.py`, `agent/main.py`, `shared/models.py`, `shared/database.py`, `agent/schemas.py`, `ui/static/app.js`, and the existing `tests/test_tasks.py` / `tests/test_task_ws.py` / `tests/test_invariants_ws.py` suites), not from training-data assumptions about the stack.

The critical mechanical discovery is in `agent/tools.py::dispatch_tool_calls` (lines 143-162): today, **every successfully-dispatched tool call is hardcoded to `"ok": True`**, regardless of what the handler function returns. `_transition_task`/`_pause_task`/`_resume_task` already catch `TaskNotFoundError` internally and return an error-shaped dict (`{"status": "error", "error": "..."}`), but that dict still gets wrapped in `ok=True` by the dispatcher — the LLM sees it in tool-role content, but the WS layer never fires a `TOOL_ERROR` frame for it. D-05/D-06 requires flipping that to `ok=False`. The minimal-diff mechanism is to change the dispatcher's hardcoded `"ok": True` to consult the handler's own `status` field (`result.get("status") != "error"`), since all three task-lifecycle handlers already produce `{"status": "error", ...}` dicts on failure and every other handler (`create_task`, `save_working_memory`, `save_long_term_memory`) never sets `status: error`. This requires zero new exception-propagation plumbing across `dispatch_tool_calls` — only a one-line change plus a new `IllegalTransitionError` raised/caught the same way `TaskNotFoundError` is today.

The second critical discovery is that `shared/database.py::init_db()` has **no formal migration tool** — it runs a sequence of hand-written, idempotent `ALTER TABLE ... ADD COLUMN` functions (`migrate_add_context_length`, `migrate_add_user_id_columns`) *before* `SQLModel.metadata.create_all`, each guarded by a `PRAGMA table_info` column-existence check. The two new `TaskTransition` columns (`rejected`, `rejection_reason`) **must** follow this exact pattern (a new `migrate_add_task_transition_rejection_columns` function), or any developer/grader running against a pre-existing `app.db` (not the auto-deleted `test_app.db`) will get `sqlite3.OperationalError: no such column` the first time a rejected-transition write is attempted, because `create_all` never adds columns to tables that already exist.

The third discovery is that the WS handler's `TOOL_ERROR` frame block (`agent/ws.py:332-346`) already exists and requires no new WS message type — it is a `for result in tool_results: if not result["ok"]: ...` loop that already fires today for malformed-args/unknown-tool/validation-error cases. D-05/D-06 only widens which results land in that loop. The frontend's `handleWsMessage` (`ui/static/app.js:1173-1189`) already handles `type: "error"` frames generically via `showToast(data.detail, 'error')` — no new frontend WS handler is needed, only the Tasks-tab history rendering extension (D-11).

**Primary recommendation:** Add a module-level directed-edge table + terminal-state set in `agent/tasks.py`; raise a new `IllegalTransitionError` carrying `task_id`/`from_state`/`to_state` from `transition_task`, `set_paused`, and `cancel_task`; change `dispatch_tool_calls`'s hardcoded `ok=True` to `result.get("status") != "error"`; add the two `TaskTransition` columns via a new idempotent `ALTER TABLE` migration function mirroring the two that already exist; wire the D-08 re-prompt as an **additional round-trip structurally identical to the existing invariant-conflict block** (`agent/ws.py:361-386`), keyed off a new `"code": "illegal_transition"` field in the rejected tool-result content.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Transition graph legality check | API / Backend (`agent/tasks.py`) | — | Pure server-side domain logic; must be enforced regardless of what UI or LLM attempts — never trust the client/LLM to only request legal edges |
| Rejection surfacing to client | API / Backend (`agent/ws.py`, `agent/main.py`) | Browser/Client (`ui/static/app.js`) | Backend decides legality and builds the explainable message; frontend only renders what it's given (existing `showToast`/history-render pattern, no new client-side validation) |
| Rejected-attempt persistence | Database / Storage (`shared/models.py::TaskTransition`) | API / Backend (`agent/tasks.py` writes) | History must survive process restarts and be queryable for the Tasks tab; in-memory-only would violate the "database-first history" pattern already established in Phase 4 |
| LLM re-prompt on illegal attempt | API / Backend (`agent/ws.py` orchestration) | — | Mirrors the existing invariant self-critique round-trip; the LLM client itself (`agent/llm_client.py`) is a dumb streaming transport, not a decision point |
| Schema migration for new columns | Database / Storage (`shared/database.py`) | — | Must run before any code path writes the new columns; SQLite has no `ALTER TABLE IF NOT EXISTS COLUMN`, so this needs an idempotent guard function, not `create_all` alone |

## Standard Stack

No new libraries are introduced by this phase. Everything below is already installed and pinned in `requirements.txt` and verified in use by the exact files this phase touches.

### Core (already in use, unchanged)
| Library | Version (from STACK.md, verified in `requirements.txt` usage) | Purpose | Why no change needed |
|---------|---------|---------|--------------|
| FastAPI | 0.115.0+ | REST endpoints + WS route | `agent/main.py`/`agent/ws.py` patterns are followed as-is |
| SQLModel | 0.0.22+ | ORM for `Task`/`TaskTransition` | New columns are plain `Field()` additions to an existing `table=True` model |
| aiosqlite | 0.20.0+ | Async SQLite driver | No change; migration uses the same `conn.execute(text(...))` pattern already used twice in `shared/database.py` |
| pytest / pytest-asyncio / respx | 8.3.0+ / 0.24.0+ / 0.21.0+ | Test runner + HTTP mocking | New tests follow `tests/test_tasks.py` (dispatcher-level) and `tests/test_invariants_ws.py` (WS multi-response respx queue) conventions exactly |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-written `ALTER TABLE` idempotent migration (existing pattern) | Alembic | Alembic would be a new dependency and a new "no formal migration tool" contradiction — CLAUDE.md explicitly notes "no formal migration tool" is the current state; introducing one is out of scope for a 2-column addition and violates the project's stated minimalism |
| Dispatcher reading `result.get("status")` sentinel | Custom exception class propagated through `dispatch_tool_calls` up to a generic `except (TaskNotFoundError, IllegalTransitionError)` at the dispatch-loop level | Both work; the exception-propagation approach is architecturally "cleaner" (matches "domain exceptions caught in the tools.py wrapper layer" convention more literally) but requires touching `dispatch_tool_calls`'s per-call try/except structure for every registered tool, not just the three task tools — larger diff for equivalent behavior. Sentinel-field approach is lower-risk. Either is compliant with CLAUDE.md's exception-handling conventions (no bare `except:`, specific catches); pick the sentinel approach for the smaller diff unless the plan-checker prefers structural purity. |

**Installation:** None required — no new packages.

## Package Legitimacy Audit

**Not applicable.** This phase installs zero new third-party packages. All work is additive code in existing modules and an additive SQLite schema migration using tooling (`sqlalchemy.text`, `PRAGMA table_info`) already present in `shared/database.py`. The Package Legitimacy Gate protocol is skipped per its own scope ("whenever this phase installs external packages").

## Architecture Patterns

### System Architecture Diagram

```
LLM tool call (transition_task / pause_task / resume_task)
        │
        ▼
agent/tools.py :: dispatch_tool_calls
        │  (validates args via Pydantic schema)
        ▼
agent/tools.py :: _transition_task / _pause_task / _resume_task
        │  (calls into the CRUD layer)
        ▼
agent/tasks.py :: transition_task / set_paused / cancel_task
        │
        ├── _get_owned_task() ──► raises TaskNotFoundError (IDOR guard, unchanged)
        │
        ├── NEW: legality check against the directed-edge table
        │        │
        │        ├─ LEGAL  ──► mutate Task.state, write TaskTransition(rejected=False), commit
        │        │
        │        └─ ILLEGAL ──► write TaskTransition(rejected=True, rejection_reason=...), commit,
        │                        then raise IllegalTransitionError(task_id, from_state, to_state)
        ▼
agent/tools.py handler catches the raised/returned error, returns
  {"status": "error", "code": "illegal_transition"|"not_found", "error": "...", ...}
        ▼
agent/tools.py :: dispatch_tool_calls
        │  ok = result.get("status") != "error"   ◄── the single-line D-05/D-06 fix
        ▼
agent/ws.py :: _handle_chat_message
        │
        ├── existing unconditional follow-up stream_chat() call (ws.py:284-310) — LLM already
        │     sees the tool-role error content here, may narrate it, unchanged
        │
        ├── existing TOOL_ERROR frame loop (ws.py:332-346) — now ALSO fires for
        │     illegal_transition/not_found (was previously only malformed-args/unknown-tool)
        │
        ├── NEW: if any rejected result has code=="illegal_transition" ──►
        │     build_transition_illegal_prompt() (mirrors invariants.build_justify_retract_prompt)
        │     ──► append as role:"user" message ──► one more stream_chat() round-trip
        │     ──► justification/explanation tokens streamed to client, folded into assistant_text
        │
        └── existing invariant self-critique block (ws.py:348-386) — untouched, runs after
        ▼
_persist_assistant_message() ──► WS "done" frame (memory_writes / task_writes / invariant_conflict — unchanged shape)
```

### Recommended Project Structure

No new files. All changes are additive edits inside existing modules:
```
agent/
├── tasks.py       # NEW: _LEGAL_EDGES, _TERMINAL_STATES, IllegalTransitionError, legality checks in
│                  #      transition_task/set_paused/cancel_task
├── tools.py       # CHANGED: dispatch_tool_calls's ok= computation; handler catch blocks for
│                  #      IllegalTransitionError alongside existing TaskNotFoundError
├── ws.py          # NEW: build_transition_illegal_prompt() call site + one more stream_chat() round-trip
├── main.py        # CHANGED: pause/resume/cancel endpoints catch IllegalTransitionError -> HTTPException 409
shared/
├── models.py      # CHANGED: TaskTransition gains rejected: bool, rejection_reason: str | None
├── database.py    # NEW: migrate_add_task_transition_rejection_columns(), called from init_db()
agent/schemas.py   # CHANGED: TaskTransitionResponse gains rejected/rejection_reason fields
ui/static/app.js   # CHANGED: renderTaskHistory() renders rejected=true rows distinctly (D-11)
tests/
├── test_tasks.py       # NEW: illegal-transition/pause/resume/cancel rejection tests (dispatcher-level)
├── test_task_ws.py      # NEW: WS-level TOOL_ERROR + justify/retract round-trip tests
├── test_task_api.py     # NEW: REST 409 tests for cancel/pause/resume terminal-state guards
├── test_database.py     # NEW: migration idempotency test for the two new columns
```

### Pattern 1: Graph-legality edge table (TRANS-01/TRANS-02)

**What:** A module-level directed-edge lookup plus a terminal-state set, consulted before any state mutation.
**When to use:** Every call to `transition_task`, and (for the terminal-state subset) `set_paused`/`cancel_task`.

```python
# Source: derived from CONTEXT.md D-01/D-02, verified against agent/tasks.py's existing shape
from shared.models import TaskState

_FORWARD_EDGES: dict[TaskState, TaskState] = {
    TaskState.PLANNING: TaskState.EXECUTION,
    TaskState.EXECUTION: TaskState.VALIDATION,
    TaskState.VALIDATION: TaskState.DONE,
}
_BACKWARD_EDGES: dict[TaskState, TaskState] = {
    TaskState.EXECUTION: TaskState.PLANNING,
    TaskState.VALIDATION: TaskState.EXECUTION,
}
_TERMINAL_STATES = {TaskState.DONE, TaskState.CANCELLED}


class IllegalTransitionError(Exception):
    """Raised when a requested state move is not exactly one legal graph edge (D-01/D-02)."""

    def __init__(self, task_id: int, from_state: TaskState, to_state: TaskState) -> None:
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Task {task_id}: {from_state.value} -> {to_state.value} is not a legal transition",
        )


def _legal_targets(from_state: TaskState) -> list[TaskState]:
    """Return the (0, 1, or 2) states legally reachable from from_state in one edge."""
    targets = []
    if from_state in _TERMINAL_STATES:
        return targets
    if from_state in _FORWARD_EDGES:
        targets.append(_FORWARD_EDGES[from_state])
    if from_state in _BACKWARD_EDGES:
        targets.append(_BACKWARD_EDGES[from_state])
    return targets


def _is_legal_transition(from_state: TaskState, to_state: TaskState) -> bool:
    return to_state in _legal_targets(from_state)
```

**Integration into `transition_task`:** insert the check immediately after `_get_owned_task` (ownership must be resolved first, so a rejected-transition attempt against another user's task_id still surfaces as `TaskNotFoundError`, not a legality leak):

```python
task = await _get_owned_task(session, user_id, chat_id, task_id)
previous_state = task.state
if not _is_legal_transition(previous_state, new_state):
    reason = (
        f"Cannot move task {task.id} from '{previous_state.value}' to '{new_state.value}'. "
        f"Legal next states from '{previous_state.value}': "
        f"{[s.value for s in _legal_targets(previous_state)] or 'none (terminal)'}."
    )
    session.add(
        TaskTransition(
            task_id=task.id,
            from_state=previous_state,
            to_state=new_state,
            note=note,
            rejected=True,
            rejection_reason=reason[:TASK_REJECTION_REASON_MAX_LENGTH],
        ),
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.warning(
        "task_transition_rejected",
        task_id=task.id, chat_id=chat_id,
        from_state=previous_state.value, to_state=new_state.value,
    )
    raise IllegalTransitionError(task.id, previous_state, new_state)
# ... existing legal-path code unchanged below
```

**Important:** the illegal branch must NOT call `session.add(task)` or mutate `task.state`/`task.updated_at` — only the new `TaskTransition` row is written. This is what makes the rejected row distinguishable from a real transition purely by its `rejected` flag while leaving the task's actual state untouched.

**For `set_paused`/`cancel_task` (terminal-state / no-op guards only, D-03/D-04):** these do not need the edge table — they only need the terminal-state set (and, for `resume_task`, an `is_paused` no-op check):

```python
# set_paused, called for BOTH pause and resume (is_paused: bool parameter distinguishes)
task = await _get_owned_task(session, user_id, chat_id, task_id)
if task.state in _TERMINAL_STATES:
    raise IllegalTransitionError(task.id, task.state, task.state)  # self-loop signals "blocked, not a move"
if not is_paused and not task.is_paused:
    raise IllegalTransitionError(task.id, task.state, task.state)  # resume no-op guard (D-03)
# ... existing set_paused code unchanged below (no TaskTransition row written on success, D-04)
```

**Recommendation on whether pause/resume rejections also write a `TaskTransition` row (ambiguity in D-09, see Assumptions Log A1):** do **not** write a row for pause/resume rejections. D-04 (Phase 4) established that `set_paused` never writes `TaskTransition` rows even on success — pausing is orthogonal to state history. Writing a rejected row only for the failure case, but never for the (far more common) success case, would make the Tasks-tab history inconsistent (occasional stray self-loop rows appearing only when pause/resume fails). Surface pause/resume rejections purely through the `ok=False` → `TOOL_ERROR` WS frame (D-05/D-06) and the REST 409 (D-04 discretion note) — no `TaskTransition` write. Reserve `TaskTransition(rejected=True)` rows for `transition_task` and `cancel_task`, which are the two operations that already write history rows on success.

### Pattern 2: `ok=False` promotion via `status` sentinel (D-05/D-06)

**What:** `dispatch_tool_calls`'s per-call result-building block currently hardcodes `"ok": True` for every tool that isn't blocked by malformed-args/unknown-tool/schema-validation (`agent/tools.py:143-162`, verified). Task-lifecycle handlers already produce `{"status": "error", ...}` dicts for `TaskNotFoundError` (verified: `agent/tools.py:244-245, 265-266, 291-292`) but that gets wrapped in `ok=True` regardless.

**Minimal fix** — change one line in `dispatch_tool_calls`:

```python
# agent/tools.py:143-162, current:
result = await TOOL_REGISTRY[name](session, user_id, chat_id, validated_args)
...
results.append(
    {
        "tool_call_id": tool_call_id,
        "name": name,
        "ok": True,                      # <-- hardcoded today
        "content": json.dumps(result),
        "write": {...},
    },
)

# Proposed:
results.append(
    {
        "tool_call_id": tool_call_id,
        "name": name,
        "ok": result.get("status") != "error",   # <-- consults the handler's own signal
        "content": json.dumps(result),
        "write": {...} if result.get("status") != "error" else None,
    },
)
```

**Why this is safe:** every other registered handler (`create_task`, `save_working_memory`, `save_long_term_memory`) returns `status: "created"`/`"saved"` — never `"error"` — so this change is a no-op for them. The only handlers that ever produce `status: "error"` today are the three task-lifecycle ones, and only for `TaskNotFoundError`. Adding `IllegalTransitionError` handling to the same three handlers, producing the same `status: "error"` shape (plus a `code` discriminator field), requires zero change to `dispatch_tool_calls` beyond the line above.

```python
# agent/tools.py, updated _transition_task
async def _transition_task(session, user_id, chat_id, args) -> dict[str, Any]:
    try:
        row = await tasks.transition_task(
            session, user_id, chat_id, args["task_id"], TaskState(args["new_state"]), args.get("note", ""),
        )
    except tasks.TaskNotFoundError:
        return {
            "status": "error", "code": "not_found",
            "error": f"task {args['task_id']} not found in this chat",
        }
    except tasks.IllegalTransitionError as exc:
        return {
            "status": "error", "code": "illegal_transition",
            "error": str(exc),
            "task_id": exc.task_id,
            "from_state": exc.from_state.value,
            "to_state": exc.to_state.value,
        }
    return {"status": "transitioned", "id": row.id, "title": row.title, "state": row.state.value}
```

The `code` field is the discriminator `agent/ws.py` uses to decide whether to fire the D-08 justify/retract re-prompt (only for `"illegal_transition"`, never for `"not_found"` — a missing task_id is not a graph-legality question the LLM needs to justify).

### Pattern 3: D-08 justify/retract re-prompt, mirroring the invariant round-trip

**What:** `agent/invariants.py::build_justify_retract_prompt` (verified, lines 330-342) is the literal shape to mirror. The equivalent for illegal transitions:

```python
# agent/tasks.py or a new small helper — mirrors build_justify_retract_prompt's shape
def build_transition_illegal_prompt(rejected: list[dict[str, Any]]) -> str:
    """Build a re-prompt for one or more illegal-transition attempts in this turn."""
    lines = []
    for r in rejected:
        lines.append(
            f'You tried to move task #{r["task_id"]} from "{r["from_state"]}" to '
            f'"{r["to_state"]}", but that is not a legal move.',
        )
    return (
        "\n".join(lines) + "\n\n"
        "Either call transition_task again with a legal next state, or explain to the "
        "user why you attempted that move. Reply briefly, in the user's language, "
        "without repeating your whole previous answer."
    )
```

**Integration point in `agent/ws.py`:** insert as an **additional round-trip**, structurally identical to the existing invariant block (`ws.py:361-386`), placed after the `TOOL_ERROR` frame loop (`ws.py:332-346`) and before the invariant self-critique block (`ws.py:348`ff). This ordering means: (1) the model's initial unconditional follow-up reply already happened (ws.py:284-310, sees the tool-role error content, may already comment on it); (2) the client gets the hard `TOOL_ERROR` frame regardless of what the model said; (3) *then* the model gets one more nudge specifically about the illegal transition; (4) *then* the (unrelated) invariant self-critique runs as today. This keeps `memory_writes`/`task_writes`/`done`-frame assembly (which already happens after the invariant block, ws.py:416-432) completely untouched — the new block only appends more text to `assistant_text` before persistence, exactly like the invariant block does with `justification_text`.

```python
# New block in agent/ws.py, inserted after the existing TOOL_ERROR loop, before the invariants block
rejected_transitions = [
    {**json.loads(r["content"])}
    for r in tool_results
    if not r["ok"] and json.loads(r["content"]).get("code") == "illegal_transition"
]
if rejected_transitions:
    llm_messages.append(
        {"role": "user", "content": tasks.build_transition_illegal_prompt(rejected_transitions)},
    )
    try:
        async for token in llm_client.stream_chat(llm_messages, payload.model, temperature, max_tokens):
            assistant_text += token
            await websocket.send_json({"type": "token", "content": token})
    except Exception as exc:
        logger.warning("transition_illegal_reprompt_failed", chat_id=chat_id, error=str(exc))
```

This mirrors the "fails open" convention already established by `run_self_critique` (a failure here must never abort the turn — same `try/except Exception` + `logger.warning`, never crash the WS handler).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State-machine library / workflow engine | A generic FSM library or `transitions`-style package | The existing plain dict-based edge table above | Already explicitly out of scope per `.planning/REQUIREMENTS.md` § Out of Scope: "General-purpose workflow engine / external state-machine library — Massive overkill for 4-5 states; violates hard constraint of no extra infra" |
| Schema migration tool | Alembic or any migration framework | The existing hand-written idempotent `ALTER TABLE` + `PRAGMA table_info` pattern in `shared/database.py` | Two new nullable/defaulted columns on one table does not justify introducing a new dependency and a new migration-state-tracking mechanism into a project that explicitly has none today |
| New WS message type for rejections | A new `{type: "transition_rejected", ...}` frame | The existing `{type: "error", code: "TOOL_ERROR"}` frame (D-05 explicitly says "no longer swallowed... firing the existing WS TOOL_ERROR frame ... unconditionally") | The frontend already has a generic error-frame handler (`ui/static/app.js:1173-1189`); a new message type would require new frontend wiring for no behavioral gain |

**Key insight:** every piece of this phase is a "widen an existing mechanism," never "introduce a new mechanism." The single highest-leverage insight is that `agent/tools.py`'s dispatcher already has the `ok`/error-content machinery fully built — Phase 6 is about *not* special-casing task tools into a second, parallel error-reporting path.

## Common Pitfalls

### Pitfall 1: Migration must run for *existing* databases, `create_all` alone is insufficient
**What goes wrong:** Adding `rejected: bool = False` / `rejection_reason: str | None = None` to the `TaskTransition` SQLModel class and relying on `SQLModel.metadata.create_all` in `init_db()` works perfectly for a brand-new database (as every test does, since `tests/conftest.py::clean_test_db` deletes `test_app.db` before every test — verified) but silently does nothing for a pre-existing `app.db` where the `tasktransition` table already exists. `create_all` only creates *missing tables*, never adds columns to existing ones.
**Why it happens:** SQLite (and SQLAlchemy's `create_all`) has no `CREATE TABLE IF NOT EXISTS ... ADD COLUMN IF NOT EXISTS` semantics; `create_all` is a table-existence check, not a column-diff.
**How to avoid:** Add a new `migrate_add_task_transition_rejection_columns(conn)` function mirroring the *exact* shape of `migrate_add_context_length`/`migrate_add_user_id_columns` (verified in `shared/database.py:75-118`): check `sqlite_master` for table existence, `PRAGMA table_info(tasktransition)` for column existence, `ALTER TABLE tasktransition ADD COLUMN rejected BOOLEAN DEFAULT 0` / `ALTER TABLE tasktransition ADD COLUMN rejection_reason TEXT`, called from `init_db()` **before** `conn.run_sync(SQLModel.metadata.create_all)` (matching the existing call order at `shared/database.py:180-182`).
**Warning signs:** `sqlite3.OperationalError: table tasktransition has no column named rejected` the first time a rejected-transition code path runs against the developer's real `app.db` (never surfaces in the test suite, since tests always start from a fresh DB — this is exactly the trap the "Configuration Scope Blindness" pitfall category warns about).

### Pitfall 2: The WS `TOOL_ERROR` frame prematurely signals "stream ended" to the frontend mid-turn
**What goes wrong:** `ui/static/app.js::handleWsMessage` (verified, lines 1173-1189) calls `setStreaming(false)` and `removeLoadingBubble()` for **any** `type: "error"` frame — including `TOOL_ERROR`. But the WS handler (`agent/ws.py`) keeps streaming more tokens *after* sending that frame: the unconditional follow-up call already happened before it (ws.py:284-310), and — after D-08 is wired in — the justify/retract re-prompt and the (unrelated) invariant self-critique block both still run and can still stream more `type: "token"` frames afterward. This means the UI's loading indicator can visually disappear mid-turn while more assistant text is still arriving.
**Why it happens:** This is a pre-existing frontend behavior (already true today for malformed-args/unknown-tool `TOOL_ERROR` frames), not something introduced by this phase — but D-05/D-06 make it fire far more often (every illegal transition and every not-found task_id, previously silent), making the glitch newly visible/graded.
**How to avoid:** This phase's scope (per CONTEXT.md) does not include a frontend streaming-state fix — flag it as a known, pre-existing, now-more-visible quirk rather than silently "fixing" out-of-scope frontend behavior. If the planner wants a clean UX, the safe minimal fix is to *not* call `setStreaming(false)`/`removeLoadingBubble()` for `code === "TOOL_ERROR"` specifically (only for `CONTEXT_OVERFLOW`/`LLM_ERROR`, which really do end the turn) — but confirm with the user before doing so, since it's not in D-05..D-11 and changes existing behavior beyond what was asked.
**Warning signs:** Manual/acceptance testing shows the input box unblocking and the "..." indicator vanishing, followed by more tokens streaming in afterward.

### Pitfall 3: Ordering of ownership check vs. legality check must not create an IDOR legality-oracle
**What goes wrong:** If the legality check ran *before* `_get_owned_task`'s ownership check, an attacker could learn another user's task's current state (e.g., "is task #47 already done?") by observing whether a transition attempt is rejected as `IllegalTransitionError` (state-based) vs `TaskNotFoundError` (ownership-based) — without ever being told the task doesn't belong to them.
**Why it happens:** Easy to structure the new legality check as "the first thing `transition_task` does" for code-cleanliness, ahead of the existing `_get_owned_task` call.
**How to avoid:** Keep `_get_owned_task(session, user_id, chat_id, task_id)` as the very first call in `transition_task`/`set_paused`/`cancel_task` (already true in the current code, verified `agent/tasks.py:111, 147, 172`) — the legality check must only run *after* ownership is confirmed, exactly as drafted in Pattern 1 above.
**Warning signs:** A test analogous to `test_transition_task_other_users_task_is_rejected` (verified existing pattern, `tests/test_tasks.py:418-451`) returning a different error shape/message for "illegal transition on someone else's task" vs "not found."

### Pitfall 4: TRANS-03's "working memory as source of truth" requirement is a resume-time context-assembly concern, not a transition-legality concern
**What goes wrong:** TRANS-03 ("Resuming a paused task correctly continues along the valid transition graph, verified using working memory as the source of truth rather than whatever the active context-compression strategy happens to retain") is easy to misread as "add a legality check to `resume_task`" (already covered by D-03's terminal/no-op guards). The *harder* half of TRANS-03 is that resuming a task must not depend on the LLM's compressed conversational context (`sliding`/`sticky`/`truncate_middle` — verified `agent/context_engine.py` strategies) still containing the task's prior working-memory writes; it must instead be re-injected from `WorkingMemory`/`Task` rows directly.
**Why it happens:** The `resume_task` tool (`agent/tools.py:282-299`, verified) currently just flips `is_paused` and returns the task row — it does not re-inject any context into the LLM's next turn. If the chat's compression strategy is `sliding` (last-10-messages-only) and a task was paused 20 messages ago, the model literally cannot see its own task's working-memory scratchpad entries in `llm_messages` unless something explicitly re-surfaces them.
**How to avoid:** This is graph-legality-adjacent but not graph-legality-*itself* — confirm with the planner whether TRANS-03's verification is (a) purely a transition-graph correctness test (resume never produces an illegal state — already covered by D-03), or (b) requires resume to trigger an explicit re-injection of `WorkingMemory` rows for that task into context. Given CONTEXT.md's `<domain>` section says this phase's scope is graph legality + rejection channel + resume-along-the-graph, and explicitly does **not** mention new memory-injection code paths in its `<code_context>` Integration Points list, the most defensible reading is (a): TRANS-03 is satisfied by `resume_task`'s existing no-op/terminal guards (D-03) plus a test proving `resume` never leaves `is_paused=True`/never mutates `state` illegally. Flag reading (b) as a possible gap for the planner to explicitly confirm is out of scope, since Phase 4's `04-CONTEXT.md` already built `WorkingMemory` as chat-scoped (not task-scoped) — there is no `task_id` column on `WorkingMemory` today (verified `shared/models.py:138-162`), so "resume re-injects this task's working memory" isn't even directly wireable without a further schema change that CONTEXT.md never mentions.
**Warning signs:** A UAT scenario that pauses a task, sends 15 unrelated messages (enough to fall out of a `sliding` window), then resumes and asks the model "what was task #N's next step" — if this is expected to work, it needs explicit new plumbing not currently described in D-01..D-11.

## Code Examples

### Rejected-transition test pattern (dispatcher-level, mirrors `test_tasks.py`)
```python
# Source: pattern verified against tests/test_tasks.py's existing structure
@pytest.mark.asyncio
async def test_transition_task_rejects_skip_ahead(authenticated_client: AsyncClient) -> None:
    """planning -> done directly is illegal (D-02); task state and TaskTransition are untouched."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _seed_chat(user_id)
    task_id = await _create_task_via_tool(user_id, chat_id)

    calls = [_call("c1", "transition_task", json.dumps({"task_id": task_id, "new_state": "done"}))]
    async with async_session_factory() as session:
        results = await dispatch_tool_calls(session, user_id, chat_id, calls)

    assert results[0]["ok"] is False  # NEW behavior — was ok=True with status:error content before D-05

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING  # unchanged

        rows = list((await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )).all())
        assert len(rows) == 2  # creation row + rejected row
        rejected = max(rows, key=lambda r: r.id)
        assert rejected.rejected is True
        assert rejected.from_state is TaskState.PLANNING
        assert rejected.to_state is TaskState.DONE
        assert rejected.rejection_reason
```

### WS-level test pattern for the justify/retract round-trip (mirrors `test_invariants_ws.py`)
```python
# Source: pattern verified against tests/test_invariants_ws.py::test_flagged_conflict_triggers_justify_retract_and_persists
@respx.mock
def test_illegal_transition_triggers_tool_error_and_reprompt() -> None:
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "T"}).json()["id"]
        task_id = client.portal.call(_create_task_row, chat_id)  # helper: seed a task in 'planning'

        respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses([
                _tool_calls_response([("c1", "transition_task",
                    json.dumps({"task_id": task_id, "new_state": "done"}))]),
                _plain_content_response("Attempting to finish the task."),   # unconditional follow-up
                _plain_content_response("You're right, I'll validate first."),  # D-08 re-prompt reply
            ]),
        )
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _send_and_drain(ws, "mark it done")

        error_frames = [f for f in frames if f.get("type") == "error" and f.get("code") == "TOOL_ERROR"]
        assert len(error_frames) == 1

        token_texts = "".join(f.get("content", "") for f in frames if f.get("type") == "token")
        assert "validate first" in token_texts

        done_frame = frames[-1]
        assert done_frame["task_writes"] == []  # rejected transition never appears in task_writes
```

### REST 409 test pattern for terminal-state guards (mirrors `test_task_api.py`)
```python
@pytest.mark.asyncio
async def test_cancel_endpoint_rejects_already_done_task(authenticated_client: AsyncClient) -> None:
    """POST /api/v1/tasks/{id}/cancel on an already-done task returns 409, not a silent no-op."""
    # ... seed a task already in DONE state ...
    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 409
    assert "cancel" in resp.json()["detail"].lower() or "done" in resp.json()["detail"].lower()
```

## State of the Art

| Old Approach (Phase 4) | New Approach (Phase 6) | When Changed | Impact |
|--------------------|------------------|---------------|--------|
| `transition_task`/`cancel_task` docstrings explicitly say "legality enforcement is Phase 6's job" (verified, `agent/tasks.py:108-109, 169-170`) and unconditionally apply any requested state | Graph-legality gate enforced before mutation | This phase | Illegal state moves become structurally impossible, not just LLM-behavior-dependent |
| `dispatch_tool_calls` hardcodes `ok: True` for every dispatched (non-malformed) tool call | `ok` reflects the handler's own `status` field | This phase | `TaskNotFoundError` (already existed) and `IllegalTransitionError` (new) both now reach the client as hard WS errors, not just LLM-narrated tool content |
| `TaskTransition` history is 100% real (accepted) transitions | History interleaves accepted and rejected attempts, distinguished by a `rejected` flag | This phase | Tasks-tab history becomes a full audit trail of what the LLM *attempted*, not just what succeeded |

**Deprecated/outdated:** None — this is additive hardening of code that is only one phase old (Phase 4/5, same milestone).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Pause/resume rejections do **not** write a `TaskTransition` row (only `transition_task`/`cancel_task` do); D-09's "rejected attempt is written as a row" language is read as scoped to real to-state-bearing operations, since D-04 (Phase 4) established pause/resume never write history rows even on success | Architecture Patterns → Pattern 1 | If the user actually wants pause/resume rejections visible in the Tasks-tab history timeline too, the plan needs an explicit self-loop-row design (from_state == to_state, rejected=True) instead of pure `ok=False`/REST-409 surfacing. This is a genuine ambiguity in CONTEXT.md — recommend the planner surface it as a quick confirmation rather than silently picking one interpretation to build against. |
| A2 | The D-08 re-prompt is implemented as an **additional round-trip** (structurally identical to the invariant self-critique block), not folded into the existing unconditional post-tool-dispatch follow-up call | Architecture Patterns → Pattern 3 | CONTEXT.md explicitly leaves this as Claude's Discretion ("whether it's an additional round-trip or folded into that existing call"), so this is a deliberate design choice, not a hard fact — the plan-checker/planner may prefer folding it into the existing call to save one LLM round-trip's latency/cost per illegal attempt. Low risk either way since both satisfy D-08's literal wording. |
| A3 | REST-triggered terminal-state/no-op rejections (cancel/pause/resume endpoints) return HTTP 409 Conflict, not 400 Bad Request | Code Examples → REST 409 test pattern | CONTEXT.md's discretion note only says "HTTPException 4xx," not which code specifically. 409 is the semantically correct choice (conflict with current resource state) and matches REST conventions used nowhere else yet in this codebase (only 404/401/400/201/204 exist today, verified via `agent/main.py` status code usage) — low risk, but the plan-checker should confirm 409 doesn't clash with any existing frontend status-code handling in `ui/static/app.js` (a quick grep shows none — frontend error handling is generic `err.message` display via `showToast`). |
| A4 | `IllegalTransitionError` is reused (not a separate error type) for pause/resume terminal/no-op rejections, distinguishing pause/resume's "self-loop" case from transition_task's real edge-violation case only by from_state==to_state | Architecture Patterns → Pattern 1 | CONTEXT.md's discretion note says this choice is "functionally equivalent, cosmetic" — low risk, but if the planner wants clearer log/error messages, a second lightweight exception type (e.g. `TaskOperationBlockedError`) may read better in `structlog` output and in the tool-content `code` field than an `IllegalTransitionError` with identical from/to states. |

**If this table is empty:** N/A — see above.

**A1 RESOLVED (planning, 2026-09-21) — reversed:** A1's reading was NOT adopted. CONTEXT.md's discussion log answers "Should rejected attempts be persisted, or purely in-flight?" with an unqualified "Persist rejected attempts," and nothing in D-09/D-10/D-11 scopes that to `transition_task`/`cancel_task`. Plans 06-01/06-03 therefore implement the explicit self-loop-row design A1 flagged as the alternative: `set_paused`'s three rejection branches (pause-on-terminal, resume-on-terminal, resume-no-op) each write one `TaskTransition(rejected=True, rejection_reason=..., from_state == to_state == task.state)` row, in addition to `ok=False`/WS `TOOL_ERROR` and REST 409 surfacing. Successful pause/resume still write no row (Phase 4 D-04 unchanged). A2/A3/A4 stand as written.

## Open Questions (RESOLVED)

1. **Does TRANS-03 require new context re-injection on resume, or only graph-legality guards on the resume operation itself?**
   - What we know: D-03 fully specifies `resume_task`'s legality guards (terminal-state + no-op). The phase's `<code_context>` Integration Points list for `agent/tasks.py` only mentions the legality check, never a new context-injection mechanism. `WorkingMemory` (verified `shared/models.py:138-162`) is chat-scoped, not task-scoped — there is no `task_id` FK on it today.
   - What's unclear: The phase goal's exact wording ("resume ... using working memory as the source of truth rather than whatever the active context-compression strategy happens to retain") reads like it wants an explicit guarantee that resuming doesn't lose task context to compression — which would need either (a) a new task_id-scoped memory read injected at resume time, or (b) an interpretation that this is already satisfied because `WorkingMemory`/`Task` rows are always fully queryable from the database regardless of what the LLM's compressed `llm_messages` window contains (i.e., "the source of truth" already *is* the DB, unaffected by compression, and TRANS-03 is really just asserting "compression must never corrupt the Task/TaskTransition rows themselves" — which is trivially true since compression only touches `llm_messages`, never the DB).
   - Recommendation: Treat as (b) unless the user/planner explicitly asks for new resume-time context re-injection — CONTEXT.md's `<domain>` explicitly scopes this phase to "No new invariant-vs-transition legality check... No changes to create_task, task data shape" (D-07), suggesting new memory-injection plumbing is similarly out of scope. Verify this reading with a one-line confirmation during planning rather than building speculative re-injection code.
   - **RESOLVED (planning, 2026-09-21):** Interpretation (b). No new resume-time context re-injection code is added. `agent/context_engine.py::build_system_prompt` already reads `memory.list_working_memory` and `tasks.list_open_tasks` straight from the database on every request, independent of the active compression strategy, so a paused task and its working memory can never be hidden by compression of `llm_messages`. Plan 06-02 Task 3 converts that structural claim into two executable regression tests (`test_paused_task_and_working_memory_survive_sliding_window_compression`, `test_resume_after_compression_continues_along_the_graph`) and changes no production code.

2. **Should the D-08 re-prompt fire even when the turn's *only* tool call was the illegal transition (no other content)?**
   - What we know: The existing invariant block always fires when `flagged is not None`, regardless of what else happened in the turn.
   - What's unclear: Whether firing it even for a turn that produced zero prose (pure tool-call turn) creates a slightly odd conversational shape (the model's only role:assistant turn so far is empty content + the tool call, then it gets asked to "reply briefly without repeating your whole previous answer" — there is no previous prose to avoid repeating).
   - Recommendation: Fire unconditionally (matches D-08's plain reading and the invariant precedent); the wording "without repeating your whole previous answer" degrades gracefully to a no-op instruction when there was no prior prose.
   - **RESOLVED (planning, 2026-09-21):** Fire unconditionally with respect to turn shape — a pure tool-call turn with no prose still triggers the re-prompt (Plan 06-02 Task 2). One scoping qualifier was added during plan review: the re-prompt fires only for results whose tool `name` is `transition_task`. Rejected `pause_task`/`resume_task` calls carry `from_state == to_state` (the self-loop marker), so feeding them to `build_transition_illegal_prompt` would produce nonsense wording ("move task #4 from 'done' to 'done'"); those refusals still reach the user via the WS `TOOL_ERROR` frame (D-05/D-06) and a persisted `rejected=True` history row (D-09/D-11), just without the extra LLM round-trip.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No (unchanged) | Existing session-cookie auth (`agent/dependencies.py`), untouched by this phase |
| V3 Session Management | No (unchanged) | — |
| V4 Access Control | Yes | `_get_owned_task`/`_get_task_or_404` (verified, `agent/tasks.py:79-95`, `agent/main.py:112-125`) already IDOR-guard every task lookup; the new legality check must run strictly *after* these ownership checks (Pitfall 3) to avoid a state-disclosure oracle across users |
| V5 Input Validation | Yes | Pydantic `TransitionTaskArgs`/`PauseTaskArgs`/`ResumeTaskArgs` (existing, `agent/schemas.py:258-290`) already validate shape/type; the new graph-legality check is a *domain-level* input validation layer on top (a syntactically valid `TaskState` enum value can still be a semantically illegal target) |
| V6 Cryptography | No | No new secrets/crypto surface |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| IDOR via task_id across chats/users | Tampering / Information Disclosure | Existing `_get_owned_task` ownership check, unchanged and must remain first in every task-lifecycle function (verified pattern, already tested by `test_transition_task_other_users_task_is_rejected` etc.) |
| State-disclosure oracle via error-message differentiation | Information Disclosure | Legality-rejection error messages must not differ in shape/timing from "not found" errors in a way that lets a non-owner distinguish "task exists but is in state X" from "task doesn't exist" — mitigated by running the ownership check strictly first (Pitfall 3) |
| Denial of legitimate transitions via race condition (two concurrent transition attempts on the same task) | Tampering | Already mitigated at the WS layer by the existing per-chat `asyncio.Lock` (`agent/state.py::chat_locks`, verified used in `_handle_chat_message` and all three REST task endpoints) — no new concurrency primitive needed; REST endpoints already acquire `chat_locks[task.chat_id]` before calling into `tasks.py` (verified `agent/main.py:690-693, 705-708, 720-723`) |

## Sources

### Primary (HIGH confidence — direct codebase reads, this session)
- `agent/tasks.py` (full file, 198 lines) — `transition_task`/`set_paused`/`cancel_task`/`_get_owned_task` current implementation
- `agent/tools.py` (full file, 300 lines) — `dispatch_tool_calls` ok-computation, all three task-tool handlers
- `agent/ws.py` (full file, 513 lines) — `_handle_chat_message` full turn flow, TOOL_ERROR frame block, invariant justify/retract block
- `agent/invariants.py` (full file, 387 lines) — `build_justify_retract_prompt`, `run_self_critique`, `record_conflict` patterns to mirror
- `shared/models.py` (full file, 388 lines) — `Task`/`TaskTransition` current schema, table-naming convention (`tasktransition`, verified against `tests/test_database.py:69`)
- `shared/database.py` (full file, 194 lines) — `init_db()` migration-function pattern (`migrate_add_context_length`, `migrate_add_user_id_columns`)
- `agent/schemas.py` (full file, 389 lines) — `TASK_NOTE_MAX_LENGTH` precedent, `TaskTransitionResponse`/`TaskResponse` shapes
- `agent/main.py` (relevant sections, lines 100-283, 560-730) — `_get_task_or_404`, `_task_to_response`, pause/resume/cancel REST endpoints
- `ui/static/app.js` (relevant sections, lines 1-55, 330-470, 1140-1197) — `renderTaskHistory`, `TASK_STATE_LABELS`, `handleWsMessage` error-frame handling
- `tests/test_tasks.py`, `tests/test_task_api.py`, `tests/test_task_ws.py`, `tests/test_invariants_ws.py`, `tests/test_memory_ws.py`, `tests/test_database.py`, `tests/conftest.py` — existing test conventions for dispatcher-level, REST, and WS multi-response respx testing
- `.planning/phases/06-controlled-transitions-day-15/06-CONTEXT.md` — locked decisions D-01 through D-11
- `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `CLAUDE.md` — project constraints, requirement text, accumulated decisions

### Secondary / Tertiary
None — this phase required no external documentation or web research; all technical claims are grounded in direct reads of this repository's own source and test files.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new stack; every library/version claim is the existing, already-verified project stack
- Architecture: HIGH — every integration point (dispatcher ok= logic, WS frame blocks, migration function pattern, REST endpoint shape) was read directly from the files this phase modifies
- Pitfalls: HIGH for Pitfalls 1-3 (directly observable from code); MEDIUM for Pitfall 4 (TRANS-03 scope is genuinely ambiguous in CONTEXT.md and flagged as Open Question 1 rather than asserted as fact)

**Research date:** 2026-09-21
**Valid until:** No expiry concern — this research is scoped entirely to this repository's current state as of this commit; it becomes stale only if Phase 4/5 code changes before Phase 6 executes (unlikely, both already merged to `main` per git log)
