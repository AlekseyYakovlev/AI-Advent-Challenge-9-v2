---
phase: 08-scheduler-day-18
plan: 06
subsystem: scheduler
tags: [llm-tools, cancel-gate, contextvar, tool-dispatch]
requires:
  - phase: 08-05
    provides: scheduler_ops (create/list/cancel, SchedulerNotFoundError, SchedulerConflictError)
  - phase: 08-04
    provides: build_task_out (last run status)
provides:
  - "agent/scheduler_tools.py: schedule_task, list_scheduled_tasks, cancel_scheduled_task registered in TOOL_REGISTRY"
  - "agent/tool_guard.py::user_asked_to_cancel (reusable by backlog 999.1)"
  - "agent/state.py::current_chat_model ContextVar, set per chat turn"
affects: [08-07, 08-08]
tech-stack:
  added: []
  patterns:
    - "Three-layer cancel gate: tool description, required user_requested_cancellation flag, code guard on the chat's current leaf user message"
    - "Tool handlers get the chat's model from a ContextVar set in ws._handle_chat_message"
key-files:
  created:
    - agent/scheduler_tools.py
  modified:
    - agent/schemas.py
    - agent/tool_guard.py
    - agent/state.py
    - agent/ws.py
    - agent/main.py
    - tests/test_scheduler_tools.py
    - tests/test_mcp_tools.py
key-decisions:
  - "Cancel gate reads the chat's current leaf message; it requires role == user, chat owned by the caller and user_asked_to_cancel(content); chat_id 0 or a foreign chat always blocks"
  - "SCHEDULER_TOOL_NAMES are excluded from done-frame memory_writes via NON_MEMORY_TOOL_NAMES; _collect_task_writes stays on TASK_TOOL_NAMES"
  - "ScheduleTaskArgs title/prompt length caps are module constants in schemas.py (200 / 4000), equal to the scheduler_ops limits"
patterns-established:
  - "Negation window of two words before a cancel verb for the intent heuristic"
requirements-completed: [SCHED-02, SCHED-11, SCHED-14]
metrics:
  tasks: 2
  files: 8
  completed: 2026-09-26
---

# Phase 8 Plan 06: Scheduler LLM tools Summary

The LLM can create (once, interval, cron), list and, only on an explicit user request, cancel scheduled jobs from a chat turn, using the chat's model and recording the chat as job origin.

## Tasks

| Task | Name | Commit |
|------|------|--------|
| 1 | Arg models, cancel-intent heuristic, ContextVar and ws.py exclusions | c58f090 |
| 2 | Tool handlers, registration, integration tests, tool count 6 -> 9 | 8921239 |

## What was built

- `agent/schemas.py`: `ScheduleTaskArgs` (with an after-validator requiring exactly one of delay_seconds/run_at for once, interval_seconds for interval, cron for cron), `ListScheduledTasksArgs` (no fields), `CancelScheduledTaskArgs` (task_id > 0, mandatory `user_requested_cancellation`). Every field has a description; no enum contains "cancelled".
- `agent/tool_guard.py`: `user_asked_to_cancel(text)`: whole-word Russian/English cancel verbs (bare stems are not matched, so "удалённый" and "stopwatch" do not trigger) with a two-word negation window ("не", "нельзя", "don't", "not", "never").
- `agent/state.py`: `current_chat_model` ContextVar; `agent/ws.py`: `current_chat_model.set(payload.model)` as the first statement of `_handle_chat_message`, `SCHEDULER_TOOL_NAMES`, `NON_MEMORY_TOOL_NAMES`, and `_collect_memory_writes` excludes both task and scheduler tools.
- `agent/scheduler_tools.py`: three `@register_tool` handlers calling `scheduler_ops`. Error codes: `model_unknown`, `invalid_schedule`, `too_many`, `not_found`, `cancel_not_requested`, `conflict`. Timestamps are returned both UTC and local. Nothing logs titles or prompts; a blocked cancel logs `scheduler_cancel_gate_blocked user_id= task_id=`.
- `agent/main.py`: import that registers the tools. `tests/test_mcp_tools.py`: tool count assertion 6 -> 9.

## Verification (actually run)

- `pytest tests/test_scheduler_tools.py tests/test_tasks.py tests/test_tool_guard.py -q`: 147 passed (Task 1).
- `pytest tests/test_scheduler_tools.py tests/test_mcp_tools.py tests/test_tasks.py tests/test_mcp_chat_ws.py tests/test_memory_ws.py tests/test_cascade_delete.py -q`: 131 passed (Task 2).
- Full suite `pytest tests -q -x`: 865 passed, 0 failed.
- `tests/test_scheduler_tools.py`: 52 tests: 26 heuristic cases (13 true, 13 false), schema validation, once/interval/cron creation with model and origin, model_unknown, invalid_schedule, too_many, list scoping and last-run status, the full cancel gate matrix (no intent, flag false, assistant leaf, success with runs kept, foreign/missing/already-cancelled, chat id 0, another user's chat), chat delete leaving the job with origin_chat_id NULL, 9-tool schema count, and a full WebSocket turn (respx) showing an empty `memory_writes` and a job stored with the payload model.
- Acceptance greps: `@register_tool` 3, `origin_chat_id=chat_id` 1, `scheduler_tools` in main.py 1, `build_tool_schemas()) == 9` 1, `current_chat_model.set(payload.model)` 1, `NON_MEMORY_TOOL_NAMES` in ws.py 2.

Not verified: behaviour with a real LLM choosing these tools (the WS test uses a mocked model), and the headless run path exclusion of scheduler tools (owned by 08-03, its allowlist only contains `save_long_term_memory`).

## Deviations from Plan

- **[Rule 3 - Blocking]** Worktree HEAD (82a17ff) was an ancestor of the expected base; `git reset --hard 53e62f2` per the startup check (mechanical, no work lost).
- TDD: tests were written together with the implementation in each task commit; no separate RED commits.
- The plan text asks for cap-to-message mapping only; the `too_many` test additionally patches `SCHEDULER_MAX_ACTIVE_TASKS_PER_USER` to 1 to exercise it.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-08-27 (three-layer cancel gate, unit and integration tests), T-08-28 (user_id from the dispatcher, ownership via scheduler_ops, foreign id is not_found), T-08-29 (shared validation, cap and minimum interval), T-08-30 (scheduler tools are not in the headless allowlist) and T-08-31 (origin_chat_id provenance, ids-only logs) are implemented.

## Notes

- `.planning/HANDOFF.json`, STATE.md and ROADMAP.md were not touched (orchestrator-owned in worktree mode).

## Self-Check: PASSED

Files exist: agent/scheduler_tools.py, tests/test_scheduler_tools.py. Commits found: c58f090, 8921239.
