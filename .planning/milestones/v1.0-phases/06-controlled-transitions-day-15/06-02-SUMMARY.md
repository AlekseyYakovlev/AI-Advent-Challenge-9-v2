---
phase: 06-controlled-transitions-day-15
plan: 02
subsystem: task-lifecycle-ws-surfacing
tags: [tasks, websocket, transition-graph, tool-dispatcher, re-prompt]
requires:
  - agent/tasks.py::IllegalTransitionError
  - agent/tasks.py::build_transition_illegal_prompt
provides:
  - agent/tools.py::dispatch_tool_calls (ok computed from handler status, write forced None on error)
  - agent/tools.py::_transition_task/_pause_task/_resume_task (code discriminator: illegal_transition/not_found)
  - agent/ws.py (D-08 justify/retract re-prompt scoped to transition_task rejections)
affects:
  - agent/tools.py::dispatch_tool_calls
  - agent/ws.py::_handle_chat_message
tech-stack:
  added: []
  patterns:
    - "ok is computed once from result.get('status') != 'error', never hardcoded True"
    - "Rejected calls force write=None so a phantom write can never reach memory_writes/task_writes"
    - "D-08 re-prompt filtered by name == 'transition_task' AND code == 'illegal_transition'; pause/resume rejections get the TOOL_ERROR frame but no extra round trip"
    - "Re-prompt round trip wrapped in try/except, fails open like the invariant justify/retract block"
key-files:
  created: []
  modified:
    - agent/tools.py
    - agent/ws.py
    - tests/test_tools.py
    - tests/test_task_ws.py
    - tests/test_context_engine_tasks.py
decisions:
  - "D-05/D-06: dispatcher ok=False promotion and code discriminators (illegal_transition/not_found) on all three task-lifecycle handlers"
  - "D-08: justify-or-retract re-prompt fires only for transition_task + illegal_transition, never for pause/resume self-loop rejections (wording-bug scoping note in the plan)"
metrics:
  duration_minutes: 30
  completed: 2026-09-21
---

# Phase 6 Plan 2: WS Surfacing of Task Rejections Summary

Closed the loop between Plan 01's domain-layer `IllegalTransitionError`/`TaskNotFoundError` and what the user actually sees during a chat turn: `agent/tools.py::dispatch_tool_calls` now computes `ok` from the handler's own status instead of hardcoding `True`, and `agent/ws.py` streams a one-shot justify-or-retract re-prompt after any illegal `transition_task` call.

## What Was Built

- **`agent/tools.py::dispatch_tool_calls`**: `ok` is computed once as `result.get("status") != "error"`; `write` is forced to `None` whenever `ok` is `False`, so a rejected call can never leak a phantom entry into `memory_writes`/`task_writes` downstream. This is a no-op for `create_task`/`save_working_memory`/`save_long_term_memory`, whose `status` values are never `"error"`.
- **`_transition_task`, `_pause_task`, `_resume_task`**: each now catches `tasks.IllegalTransitionError` alongside the existing `tasks.TaskNotFoundError` branch, returning `{"status": "error", "code": "illegal_transition", "error": str(exc), "task_id", "from_state", "to_state"}`. The existing `TaskNotFoundError` branches gained `"code": "not_found"` so both error families share one discriminator convention (D-06). For `_pause_task`/`_resume_task`, `from_state == to_state` (Plan 01's self-loop marker) is passed through unchanged.
- **`agent/ws.py`**: imports `tasks` alongside `invariants`. Immediately after the existing `TOOL_ERROR` frame loop and before `resolve_active_invariants`, a new block collects `tool_results` entries that are `ok is False`, `name == "transition_task"`, and `code == "illegal_transition"` (via a `try/except json.JSONDecodeError: continue` guard). When any exist, it appends `tasks.build_transition_illegal_prompt(rejected_transitions)` as a `role: user` message and streams one more `stream_chat` call into `assistant_text`/`token` frames, wrapped in `try/except Exception` logging `transition_illegal_reprompt_failed` so an LLM failure fails open exactly like the invariant justify/retract block. Pause/resume rejections are deliberately excluded by the `name` filter (their `from_state == to_state` self-loop would otherwise produce a nonsensical "moved from X to X" prompt).
- No changes to the `TOOL_ERROR` loop, `task_writes`/`memory_writes` assembly, the invariant self-critique block, `_persist_assistant_message`, or the `done` frame shape.

## Tests

Added to `tests/test_tools.py` (5 new tests): illegal `transition_task` returns `ok=False`/`write=None`/`code=illegal_transition` with `task_id`/`from_state`/`to_state`/non-empty `error`; cross-chat `transition_task` id returns `code=not_found`; pausing a `DONE` task returns `code=illegal_transition` with a self-loop; resuming a non-paused task returns the same with `name == "resume_task"`; a rejected call in a multi-call turn does not stop a later call from dispatching. Existing dispatcher/task tests (`test_tasks.py`'s cross-chat/cross-user rejection tests) already asserted on `result_payload.get("status") == "error"` rather than `ok is True`, so no inversion was needed there.

Added to `tests/test_task_ws.py` (4 new tests, mirroring `test_invariants_ws.py`'s respx queue pattern): an illegal `transition_task` turn emits exactly one `TOOL_ERROR` frame and streams a third queued LLM response into the token frames, with `done_frame["task_writes"] == []`; a rejected `resume_task` turn emits one `TOOL_ERROR` frame with exactly 2 LLM calls (`route.call_count == 2`, no third response consumed); a legal `transition_task` turn emits no `TOOL_ERROR` frame, makes no extra round trip, and reports the task in `task_writes`; a re-prompt `stream_chat` failure (mocked `httpx.Response(500)`) still completes the turn with a `done` frame and leaves the user message persisted.

Added to `tests/test_context_engine_tasks.py` (2 new tests, TRANS-03's resume-context acceptance criterion): `test_paused_task_and_working_memory_survive_sliding_window_compression` creates a task via `tasks.create_task`, writes a working-memory row, pauses the task, lowers `context_length` to 300 and inserts 15 long messages so sliding-window compression actually discards history (asserted via `len(non_system_messages) < len(contents)`), then asserts the system prompt still contains the task title, `[ON PAUSE]`, and the working-memory key/value. `test_resume_after_compression_continues_along_the_graph` resumes the task (`is_paused` False, state unchanged), transitions it `PLANNING -> EXECUTION` (legal), asserts `EXECUTION -> DONE` raises `IllegalTransitionError`, and asserts a second no-op resume raises `IllegalTransitionError` while appending exactly one new `rejected=True` self-loop `TaskTransition` row (compared by count delta, not total-table count, since the earlier illegal-transition attempt also wrote a rejected row).

**Verification run:** `pytest tests/ -v` — 318 passed, 0 failed (full suite, no regressions).

## TDD Gate Compliance

- Task 1 (dispatcher `ok=False` promotion): RED commit `cfa2c57` (`test(06-02): add failing tests for dispatcher ok=False promotion`) — all 5 new tests failed with unhandled `IllegalTransitionError` propagating out of `dispatch_tool_calls` (no `except` clause existed yet). GREEN commit `5384d7d` (`feat(06-02): promote task-lifecycle rejections to ok=False in dispatcher`) — same 5 tests pass, plus the full `test_tools.py`/`test_tasks.py`/`test_memory_ws.py` suite (59 tests).
- Task 2 (D-08 re-prompt): RED commit `e4a7c1c` (`test(06-02): add failing test for the justify-or-retract re-prompt`) — `test_illegal_transition_triggers_reprompt_and_empty_task_writes` failed because the turn stopped after the unconditional follow-up call and the queued third respx response was never consumed (`route.call_count == 2` when the test needed the re-prompt text). The other 3 new tests in this commit passed even at RED time because they assert behavior that was already correct and unaffected by the missing feature (no-reprompt for pause/resume, legal-transition no-op, and re-prompt-failure fail-open all hold with or without the re-prompt block) — this is expected per the TDD reference's guidance that only the test targeting the missing behavior must genuinely fail. GREEN commit `0441aa9` (`feat(06-02): stream a justify-or-retract re-prompt after an illegal transition_task call`) — all 4 tests pass, plus the full `test_task_ws.py`/`test_invariants_ws.py`/`test_memory_ws.py` suite (19 tests).

Task 3 has no `tdd="true"` attribute (test-only task, no production code changes) and correctly has no RED/GREEN commit pair — verified by `git log` showing a single `test(06-02)` commit for it.

No REFACTOR commit was needed for either TDD task — the GREEN implementation matched the plan's shape on the first pass.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 auto-fixes were needed; no architectural questions arose. One planning correction was caught before it reached a commit: my first draft of Task 3's Test B set up the task directly in `EXECUTION` state (skipping `tasks.create_task`'s `PLANNING` start), which would have made the plan's specified `transition_task(..., EXECUTION)` call illegal instead of legal. Caught during test authoring (before running RED) and rewritten to match the plan's exact setup (create via `tasks.create_task`, pause while still in `PLANNING`, resume, then `PLANNING -> EXECUTION`) — not logged as a deviation since no incorrect code was ever committed.

## Verification Against Plan's `<verification>` Block

- `pytest tests/ -v` passes in full: 318 passed, 0 failed, with particular attention to `tests/test_task_ws.py` (9 tests), `tests/test_memory_ws.py` (6 tests), `tests/test_concurrent_ws.py`, and `tests/test_llm_tools_stream.py`, all of which exercise `dispatch_tool_calls` results and pass unchanged.
- `grep -o '"type": "[a-z_]*"' agent/ws.py | sort -u` shows exactly `done`, `error`, `model_event`, `token` — the same set of WS frame types as before this plan; no new frame type was introduced.

## Acceptance Criteria Spot-Checks

- `grep -n '"ok": True' agent/tools.py` → no hits inside `dispatch_tool_calls`.
- `grep -c "illegal_transition" agent/tools.py` → 3 (one per task-lifecycle handler).
- `grep -c '"code": "not_found"' agent/tools.py` → 3.
- `grep -c '"name": name' agent/tools.py` → 5, unchanged from before this task.
- `grep -n "build_transition_illegal_prompt" agent/ws.py` → exactly 1 hit, at line 364, which is greater than the `"code": "TOOL_ERROR"` line (344) and less than the `resolve_active_invariants` line (387).
- `grep -n "transition_illegal_reprompt_failed" agent/ws.py` → exactly 1 hit.
- `grep -n "from agent import" agent/ws.py` → `from agent import invariants, tasks`.

## What's Next

Plan 03 (parallel, different worktree) covers the REST 409 surfacing in `agent/main.py`/`agent/schemas.py` and the Tasks-tab history rendering in `ui/static/app.js` (D-11) — no file overlap with this plan.

## Self-Check: PASSED

- FOUND: agent/tools.py (ok computation, write=None on error, illegal_transition/not_found code discriminators)
- FOUND: agent/ws.py (tasks import, D-08 re-prompt block, transition_illegal_reprompt_failed log)
- FOUND: tests/test_tools.py (5 new tests)
- FOUND: tests/test_task_ws.py (4 new tests)
- FOUND: tests/test_context_engine_tasks.py (2 new tests)
- FOUND commit cfa2c57
- FOUND commit 5384d7d
- FOUND commit e4a7c1c
- FOUND commit 0441aa9
- FOUND commit 82916bb
