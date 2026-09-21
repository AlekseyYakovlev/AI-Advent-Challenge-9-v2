# Phase 6: Controlled Transitions (Day 15) - Context

**Gathered:** 2026-09-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Task state transitions (built loosely in Phase 4, `agent/tasks.py`) become strictly, verifiably enforced rather than merely suggested. Illegal transitions — skipping states, moving out of a terminal state, misusing pause/resume — are rejected with a clear, explainable message that reaches the UI/WS layer, never a silent no-op or crash. Resuming a paused task must correctly continue along the valid transition graph. This phase hardens Phase 4's transition history and reuses (but does not extend the power of) Phase 5's self-critique/justify-retract pattern.

**Explicitly out of this phase's scope:** No new invariant-vs-transition legality check (invariants still cannot block a transition — see D-07). No changes to `create_task`, task data shape, or the sidebar tab structure beyond the history-rendering extension in D-11.

</domain>

<decisions>
## Implementation Decisions

### Transition graph shape
- **D-01:** The graph is `planning → execution → validation → done`, plus **one-step-back rework moves**: `execution → planning` and `validation → execution`. `done` and `cancelled` are terminal — **no transition out of either**, forward or backward.
- **D-02:** **No skipping.** Every `transition_task` call must move exactly one edge along the graph (one step forward or one step back) — e.g. `planning → done` directly is illegal, and so is `validation → planning` (two steps back).
- **D-03:** `pause_task`/`resume_task` are graph-guarded too: `resume_task` rejects if the task's `is_paused` is already `False` (no-op protection); both `pause_task` and `resume_task` reject if the task is already in a terminal state (`done`/`cancelled`).
- **D-04:** `cancel_task` (manual REST-only, per Phase 4's D-07/D-12) rejects once the task is already `done` or `cancelled` — cancel is no longer a no-questions-asked override from every state.

### Rejection channel
- **D-05:** A new `IllegalTransitionError` (and the existing `TaskNotFoundError`) causes the tool dispatcher to mark that result **`ok=False`**, firing the existing WS `{type: "error", code: "TOOL_ERROR"}` frame (`agent/ws.py:332-346`) **unconditionally** — no longer swallowed into `ok=True` tool-result content that depends on the LLM choosing to mention it. Matches TRANS-02's literal wording ("clear, explainable rejection...in the UI/WS response").
- **D-06:** This `ok=False` promotion applies uniformly to **all three task-lifecycle tools** — `transition_task`, `pause_task`, `resume_task` — for both graph-legality rejections and task-not-found rejections. **This changes existing Phase 4 behavior**: `TaskNotFoundError` previously stayed `ok=True` (tool-content-only); the user explicitly chose to harmonize both error families through one path rather than keep two conventions side by side.

### Phase 5 conflict-check wiring
- **D-07:** **No new invariant-vs-transition legality check is added.** Invariants (Phase 5) do not gain the power to block a transition — `agent/invariants.py` is not called from the transition path. ROADMAP.md's "conflict checks from Phase 5 get wired into transition attempts" resolves to reusing the **self-critique round-trip's pattern**, not its code or its authority.
- **D-08:** An illegal transition triggers a **dedicated justify/retract-style re-prompt** to the LLM — a new prompt mirroring `build_justify_retract_prompt`'s shape/wording (`agent/invariants.py:330-344`), explaining specifically why the transition was illegal (e.g. "you tried to move task #4 from `planning` to `done`, but `validation` must come first") and asking the LLM to either pick a legal transition or explain to the user why it attempted the illegal one. This is distinct from a plain tool-result-content error with no further prompt engineering.

### Rejected-attempt logging
- **D-09:** `TaskTransition` gets two new columns: `rejected: bool = False`, `rejection_reason: str | None`. A rejected attempt is written as a row with `from_state` = the task's actual current state, `to_state` = the illegal target state that was attempted, `rejected=True`, `rejection_reason=<why>`.
- **D-10:** **No new table** — accepted and rejected events share one chronological `TaskTransition` history, consistent with Phase 4's D-11 rendering pattern. Mirrors Phase 5's D-13 precedent of persisting flagged/blocked events rather than treating them as transient WS-only.
- **D-11:** In the Tasks tab's history list, rejected rows render **inline with real transitions, visually distinct** (e.g. red/strikethrough) — e.g. "attempted → done: rejected (validation required first)" — so the full sequence of what the LLM tried, including failures, stays one readable timeline.

### Claude's Discretion
- Exact wording of the new justify/retract-style prompt (D-08) — follow `build_justify_retract_prompt`'s tone/structure, adapted for transition-legality language rather than invariant-conflict language.
- Exact mechanics of how the D-08 re-prompt interacts with the existing unconditional post-tool-dispatch follow-up call in `agent/ws.py:284-310` — whether it's an additional round-trip or folded into that existing call for the illegal-transition case. Keep the rest of that flow (`memory_writes`/`task_writes` assembly, WS `done` frame) intact.
- Whether `pause_task`/`resume_task`'s promotion (D-06) reuses the exact `IllegalTransitionError` type or gets its own error type/message per tool — functionally equivalent, cosmetic choice.
- Exact max-length/nullability for `TaskTransition.rejection_reason` (D-09) — recommend matching the existing `note` field's `TASK_NOTE_MAX_LENGTH` precedent (`agent/schemas.py`) unless there's a reason to differ.
- Whether REST-triggered rejections (e.g. `cancel_task` blocked on terminal state, D-04) surface as `HTTPException` 4xx responses (matching the existing `_get_task_or_404` convention in `agent/main.py`) rather than the WS `TOOL_ERROR` frame — natural consequence of manual pause/resume/cancel being plain REST, not LLM tool calls; not something the user needs to lock in explicitly.

</decisions>

<specifics>
## Specific Ideas

No specific product references beyond the decisions above — user answered each gray area directly, including one explicit correction to the initial "one step back" framing to clarify that `done`/`cancelled` are hard terminal states with no exit (folded into D-01).

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Controlled transitions requirements & constraints
- `.planning/PROJECT.md` §Requirements (TRANS-01..03), §Constraints (`user_id` data scoping, vanilla-JS-only frontend)
- `.planning/REQUIREMENTS.md` §Controlled Transitions — TRANS-01 through TRANS-03 acceptance criteria; §Traceability (lines 122-124)
- `.planning/ROADMAP.md` §Phase 6: Controlled Transitions (Day 15) — goal, branch (`Day15`), depends-on (Phase 4 + Phase 5), success criteria
- `.planning/STATE.md` §Accumulated Context — TRANS-01/02/03 hard enforcement deliberately deferred to Phase 6; Day 14 demo note that a well-behaved model may self-censor at the primary-answer stage, so a "flagged conflict" may be rarer than assumed — relevant context for why D-07 keeps invariants and transitions as separate concerns
- `CLAUDE.md` §Hard constraints — `user_id` data scoping, no Docker/multiprocessing, vanilla-JS-only frontend, HTTP-only session cookie auth

### Prior phase precedent (patterns this phase hardens/reuses)
- `.planning/phases/04-task-state-machine-day-13/04-CONTEXT.md` — D-01..D-12: task data shape, pause/resume/cancel tool+REST split (D-05/D-12), chronological history rendering (D-11) — this phase's direct foundation; the file's own docstrings in `agent/tasks.py` explicitly flag legality enforcement as "Phase 6's job"
- `.planning/phases/05-invariants-day-14/05-CONTEXT.md` — D-07/D-08/D-09: self-critique + justify/retract round-trip this phase's D-08 reuses the *shape* of (not the invariant-checking authority, per D-07); D-13: `InvariantConflict` persistence precedent this phase's D-09/D-10 follows

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/tasks.py::transition_task`/`set_paused`/`cancel_task` (`agent/tasks.py:98-197`) — exact functions to add graph-legality checks into; each already has ownership-checking (`_get_owned_task`) and commit/rollback scaffolding in place, and each docstring explicitly says legality enforcement is deferred to this phase.
- `agent/tools.py::dispatch_tool_calls` (`agent/tools.py:76-163`) — the `ok`/`ok=False` branch point already exists (malformed-args/unknown-tool/validation errors are `ok=False` today; domain errors like `TaskNotFoundError`, caught inside `_transition_task`/`_pause_task`/`_resume_task` at lines 244-245/265-266/291-292, currently stay `ok=True`). D-05/D-06 change that domain-error handling to also produce `ok=False`.
- `agent/invariants.py::build_justify_retract_prompt` (`agent/invariants.py:330-344`) — direct pattern to mirror for the new transition-illegal justify/retract prompt (D-08).
- `agent/ws.py`'s existing `TOOL_ERROR` WS frame block (`agent/ws.py:332-346`) — no new WS message type needed; D-05/D-06 just route more results into it.
- `agent/ws.py`'s existing invariant justify/retract round-trip (`agent/ws.py:361-386`) — structural precedent for wiring D-08's new re-prompt into the WS handler.

### Established Patterns
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- Domain exceptions are caught in the `agent/tools.py` wrapper layer, not `agent/ws.py` — see `TaskNotFoundError` precedent; `IllegalTransitionError` should follow the same layering.
- `structlog` logging (`logger = get_logger(__name__)`) — new rejection paths should log similarly to `task_transitioned` (e.g. `task_transition_rejected`).

### Integration Points
- `shared/models.py` — `TaskTransition` gains two new columns (D-09): `rejected: bool = False`, `rejection_reason: str | None`. `Task`'s state enum/columns are unchanged.
- `agent/tasks.py` — new graph-legality check (e.g. a module-level set of allowed `(from_state, to_state)` edges) consulted by `transition_task`, `set_paused`, and `cancel_task` before mutating state; raises `IllegalTransitionError` (new exception, alongside `TaskNotFoundError`) on violation instead of unconditionally applying the change, and writes the D-09 rejected `TaskTransition` row.
- `agent/tools.py` — `_transition_task`/`_pause_task`/`_resume_task` handlers catch `IllegalTransitionError` (alongside existing `TaskNotFoundError`) and signal `ok=False` to the dispatcher (D-05/D-06) instead of returning a `status: error` content dict.
- `agent/ws.py` — the illegal-transition case additionally builds and sends the D-08 justify/retract prompt, following the existing invariant-conflict round-trip's structural shape (lines 361-386).
- `agent/main.py` — `cancel_task_endpoint`/`pause_task_endpoint`/`resume_task_endpoint` gain the same terminal-state/no-op guards as their tool-call counterparts, surfaced via the existing REST error convention.
- `ui/static/app.js` — Tasks tab's history rendering (Phase 4's D-11) extended to render `rejected=True` rows inline, visually distinct (D-11 here).

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 06-controlled-transitions-day-15*
*Context gathered: 2026-09-21*
