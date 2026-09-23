---
phase: 06-controlled-transitions-day-15
plan: 01
subsystem: task-lifecycle-domain-layer
tags: [tasks, state-machine, transition-graph, migration]
requires: []
provides:
  - agent/tasks.py::IllegalTransitionError
  - agent/tasks.py::_FORWARD_EDGES
  - agent/tasks.py::_BACKWARD_EDGES
  - agent/tasks.py::_TERMINAL_STATES
  - agent/tasks.py::_legal_targets
  - agent/tasks.py::_is_legal_transition
  - agent/tasks.py::build_transition_illegal_prompt
  - shared/models.py::TaskTransition.rejected
  - shared/models.py::TaskTransition.rejection_reason
  - shared/database.py::migrate_add_task_transition_rejection_columns
affects:
  - agent/tasks.py::transition_task
  - agent/tasks.py::set_paused
  - agent/tasks.py::cancel_task
tech-stack:
  added: []
  patterns:
    - "Ownership check (_get_owned_task) always resolves before any legality check"
    - "Rejected TaskTransition rows share the accepted-row table (no new table), rejected=True + rejection_reason"
    - "set_paused rejection rows are self-loops (from_state == to_state == task.state)"
key-files:
  created: []
  modified:
    - shared/models.py
    - shared/database.py
    - agent/tasks.py
    - tests/test_database.py
    - tests/test_tasks.py
decisions:
  - "D-01/D-02: explicit one-edge-per-call transition graph enforced in transition_task; done/cancelled have no exit"
  - "D-03: set_paused rejects pause/resume on terminal tasks and resume on an already-non-paused task"
  - "D-04: cancel_task rejects once a task is already done or cancelled"
  - "D-09/D-10: rejected attempts persist as rejected=True rows in the existing TaskTransition table, no new table"
metrics:
  duration_minutes: 35
  completed: 2026-09-21
---

# Phase 6 Plan 1: Transition Graph Enforcement Summary

Made the `planning -> execution -> validation -> done` transition graph (plus the `execution -> planning` / `validation -> execution` rework moves) a hard, server-side gate in `agent/tasks.py`, so any caller — LLM tool call, REST endpoint, or a future one — gets `IllegalTransitionError` for a skip-ahead, backward-skip, self-loop, or terminal-state-exit attempt, with the task's state provably untouched and a `rejected=True` `TaskTransition` row explaining why.

## What Was Built

- **`TaskTransition.rejected`/`rejection_reason` columns** (`shared/models.py`), added idempotently to an existing `app.db` by `migrate_add_task_transition_rejection_columns` (`shared/database.py`), called from `init_db()` before `create_all`. Verified directly against a legacy SQLite table (pre-existing `tasktransition` without the new columns): first run logs `migrating_tasktransition_add_rejection_columns` and adds both columns; second run logs nothing and adds nothing, raising no error.
- **Transition graph** (`agent/tasks.py`): `_FORWARD_EDGES`, `_BACKWARD_EDGES`, `_TERMINAL_STATES`, `_legal_targets`, `_is_legal_transition`, and `IllegalTransitionError(task_id, from_state, to_state, reason)`.
- **`transition_task`** now runs the legality gate immediately after `_get_owned_task` and before any mutation. An illegal edge writes a `rejected=True` row (`from_state` = current state, `to_state` = attempted state) and raises `IllegalTransitionError` without touching `task.state`/`task.updated_at`. Legal one-edge moves, both directions, are unchanged.
- **`set_paused`** rejects pause/resume on a terminal task and rejects resume when `is_paused` is already `False`, in both cases writing exactly one self-loop (`from_state == to_state == task.state`) `rejected=True` row and raising `IllegalTransitionError`. Successful pause/resume still write no row (Phase 4 D-04 preserved).
- **`cancel_task`** rejects cancelling a task already `done` or `cancelled`, writing a `rejected=True` row (`from_state=<terminal>`, `to_state=CANCELLED`) instead of overwriting the outcome.
- **`build_transition_illegal_prompt`** mirrors `agent/invariants.py::build_justify_retract_prompt`'s tone/shape for the D-08 re-prompt; wiring it into the WS turn is Plan 02's job.

## Tests

Added to `tests/test_tasks.py` (24 new tests, TDD RED then GREEN for tasks 2 and 3):
- Legal one-edge moves both directions (`execution -> planning`, `validation -> execution`)
- Illegal: skip-ahead, two-steps-back, self-loop, exit from `done`, exit from `cancelled`
- Ownership resolves before legality (`TaskNotFoundError`, not `IllegalTransitionError`, for another user's task_id even with an illegal target state)
- `build_transition_illegal_prompt` includes task id and both state values
- `set_paused`: success on non-terminal, rejection on terminal (pause and resume), rejection on resume-no-op, no row on either success path, exactly one self-loop row per rejection path
- `cancel_task`: success on non-terminal, rejection on already-`done`, rejection on already-`cancelled`
- Resume followed by a legal `transition_task` still succeeds (TRANS-03)

`tests/test_database.py`: added `test_migrate_add_task_transition_rejection_columns_is_idempotent`, running the migration twice against the same engine and asserting each column appears exactly once.

**Verification run:** `pytest tests/ -v` — 307 passed, 0 failed (full suite, no regressions in `test_task_api.py`, `test_task_ws.py`, `test_tools.py`, or any other file).

## TDD Gate Compliance

- Task 2 (transition graph enforcement): RED commit `9add578` (`test(06-01): add failing tests for transition-graph legality enforcement`) precedes GREEN commit `c792a18` (`feat(06-01): enforce the transition graph in transition_task`). Verified RED failures were genuine `AttributeError`s for the not-yet-existing `IllegalTransitionError`/`build_transition_illegal_prompt` (3 of 9 new tests passed at RED time, correctly, since backward-move legality already worked with no gate).
- Task 3 (pause/resume/cancel guards): RED commit `b317369` (`test(06-01): add failing tests for pause/resume/cancel terminal-state guards`) precedes GREEN commit `259814b` (`feat(06-01): guard pause, resume, and cancel against terminal states and no-ops`). All 6 new rejection-path tests failed at RED time with `Failed: DID NOT RAISE IllegalTransitionError`.

No REFACTOR commit was needed — the GREEN implementation matched the plan's shape on the first pass; no follow-up cleanup was required.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 auto-fixes were needed; no architectural questions arose.

## Verification Against Plan's `<verification>` Block

- `pytest tests/ -v` passes in full: 307 passed, 0 failed.
- `grep -rn "Phase 6's job" agent/tasks.py` returns no hits (both the `transition_task` and `cancel_task` docstring sentences referencing deferred Phase 6 enforcement were replaced).
- "Running the app once against an existing `app.db`" was verified via a standalone script driving `migrate_add_task_transition_rejection_columns` directly against a legacy SQLite table (rather than starting the full two-process `python run.py` app, which isn't necessary to exercise this migration path) — first call logs `migrating_tasktransition_add_rejection_columns` and adds both columns; second call is silent and idempotent.

## What's Next

Plan 02 wires `IllegalTransitionError` (and `TaskNotFoundError`) into `agent/tools.py`'s dispatcher (`ok=False` promotion, D-05/D-06) and `agent/ws.py`'s WS turn (the D-08 justify/retract round-trip using `build_transition_illegal_prompt`). Plan 03 (or a later plan) covers the REST 409 surfacing in `agent/main.py` and the Tasks-tab history rendering in `ui/static/app.js` (D-11).

## Self-Check: PASSED

- FOUND: shared/models.py (rejected/rejection_reason fields present)
- FOUND: shared/database.py (migrate_add_task_transition_rejection_columns defined and called from init_db)
- FOUND: agent/tasks.py (IllegalTransitionError, transition graph, guards)
- FOUND: tests/test_database.py (idempotency test)
- FOUND: tests/test_tasks.py (24 new tests)
- FOUND commit f1a0a90
- FOUND commit 9add578
- FOUND commit c792a18
- FOUND commit b317369
- FOUND commit 259814b
