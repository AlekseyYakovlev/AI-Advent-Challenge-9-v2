---
phase: 04-task-state-machine-day-13
plan: 04
status: paused
subsystem: task-state-machine
tags: [tasks, context-engine, system-prompt-injection]

# Dependency graph
requires:
  - phase: 04-task-state-machine-day-13
    provides: Task/TaskTransition tables, agent/tasks.py CRUD layer (04-01), transition_task (04-02), pause/resume/cancel (04-03)
provides:
  - agent/tasks.py::list_open_tasks (non-terminal tasks, paused included, oldest first)
  - Open-task block injected into agent/context_engine.py::build_system_prompt
  - tests/test_context_engine_tasks.py (11 passing cases incl. 4 parametrised strategy cases)
affects: [context-engine, task-state-machine, day-13-acceptance-demo]

# Tech tracking
tech-stack:
  added: []
  patterns: [single-chokepoint context assembly (no second task-read path in agent/ws.py), self-bounding prompt block (terminal tasks drop out permanently)]

key-files:
  created:
    - tests/test_context_engine_tasks.py
  modified:
    - agent/tasks.py
    - agent/context_engine.py

key-decisions: []

patterns-established:
  - "Open-task injection lives only inside build_system_prompt, mirroring the existing memory/profile injection idiom — no build_task_prompt() helper, no second read path in agent/ws.py."

requirements-completed: []  # TASK-01..05 remain incomplete until Task 3's human-verify checkpoint is approved

# Metrics
duration: partial (Tasks 1-2 only; Task 3 checkpoint pending)
completed: null
---

# Phase 4 Plan 4 (PARTIAL): Open-task context injection — paused at Day 13 acceptance checkpoint

**`list_open_tasks` (excludes done/cancelled, includes paused) is now injected into `build_system_prompt` as an `Open tasks in this chat:` block on every turn under every compression strategy — Tasks 1-2 complete and automated; Task 3's live human-verify acceptance demo has not yet run.**

## Performance

- **Tasks completed:** 2 of 3 (Task 3 is `checkpoint:human-verify`, `gate="blocking"`)
- **Files modified:** 2 (`agent/tasks.py`, `agent/context_engine.py`)
- **Files created:** 1 (`tests/test_context_engine_tasks.py`)

## Accomplishments

- `agent/tasks.py::list_open_tasks(session, chat_id)` — `select(Task).where(chat_id == chat_id).where(state.not_in([DONE, CANCELLED])).order_by(created_at, id)`, paused tasks always included.
- `agent/context_engine.py::build_system_prompt` appends one `Open tasks in this chat:\n- #{id} "{title}" (state={state}{ [ON PAUSE]}): goal={goal!r}` block, built from `tasks.list_open_tasks`, only when the list is non-empty. Placed immediately before the final `"\n\n".join(parts)`, so it reaches `build_llm_context` — and therefore every turn under every `ContextStrategy` — with no second read path (`agent/ws.py` untouched; `grep -cE "list_open_tasks|list_tasks_for_chat" agent/ws.py` → 0).
- `tests/test_context_engine_tasks.py` — 11 tests (8 named + 4 parametrised `ContextStrategy` cases counted as one function): injection with header/id/title/state/goal, multi-task, `[ON PAUSE]` marker with state preserved, done exclusion, cancelled exclusion, empty-chat no-block, cross-chat scoping, and survival across all four compression strategies.

## Task Commits

1. **Task 1: Write the failing tests for open-task context injection** - `418f84b` (test)
2. **Task 2: Inject the chat's open tasks into build_system_prompt** - `1f3d889` (feat)

**Plan metadata:** this commit (docs: partial summary, checkpoint pause)

_Task 3 is a `checkpoint:human-verify` gate — no commit exists for it yet; SUMMARY.md will be rewritten to its final form once the developer's acceptance-demo observations are recorded._

## Files Created/Modified

- `tests/test_context_engine_tasks.py` - RED-phase test module for open-task injection (8 test functions, 11 collected cases with parametrisation)
- `agent/tasks.py` - added `list_open_tasks`
- `agent/context_engine.py` - extended `from agent import memory, profile` to include `tasks`; added the open-task block inside `build_system_prompt`

## Decisions Made

None — Tasks 1-2 followed the plan's `<action>` verbatim (query shape, injection line format, placement point).

## Deviations from Plan

None for Tasks 1-2 — executed exactly as written. One deviation surfaced during the Task 3 acceptance demo (see below).

### Bug found and fixed during Task 3's acceptance demo

The developer's first pass through the checklist found: after the LLM created a task via `create_task`, the chat UI hung on the typing indicator forever; only a page reload (F5) revealed the completed reply and the new task card.

Root cause (confirmed via `logs/agent.log`, which is where the Agent subprocess's structlog output actually goes — `ui/supervisor.py` redirects its stdout/stderr there, not to the `python run.py` console): `agent/ws.py::_handle_chat_message` reused the function's `payload: MessagePayload` parameter as the loop variable while building `task_writes` (`payload = json.loads(result["content"])`), shadowing it for the rest of the function. The later `extract_and_update_facts(session, chat_id, payload.content, payload.model)` call then read `.content`/`.model` off that plain `dict` and raised `AttributeError: 'dict' object has no attribute 'content'` — after the task and assistant message were already committed to the DB, but before the WS `done` frame was sent. The dead ASGI connection was invisible to the user; the frontend's auto-reconnect opened a fresh socket ~12s later with no way to recover the orphaned turn. This fired on every `create_task`/`transition_task`/`pause_task`/`resume_task` call, deterministically, not intermittently — confirmed by both task-creation turns in the session hitting the identical traceback.

Fix: renamed the shadowing loop variable to `task_result` (commit `413b805`). Added `tests/test_task_ws.py::test_create_task_turn_completes_with_done_frame`, a WS-level regression test (mirroring `test_memory_ws.py`'s respx-mocked pattern) asserting a `create_task` turn always ends in exactly one `done` frame; verified it fails against the pre-fix code (`AttributeError` surfaces, no `done` frame sent) and passes against the fix. Full suite: 232 passed after the fix.

## Verification Evidence (Tasks 1-2 only)

- RED phase: `python -m pytest tests/test_context_engine_tasks.py -x` → 1 failed on the first assertion (`assert 'Open tasks in this chat:' in 'Base prompt'`), confirming the injection did not yet exist.
- `python -m pytest tests/ -q --ignore=tests/test_context_engine_tasks.py` (RED-phase checkpoint) → 220 passed, zero regressions.
- GREEN phase: `python -m pytest tests/test_context_engine_tasks.py -v` → 11/11 passed, including all four parametrised `ContextStrategy` cases (`sliding`, `sticky`, `truncate_middle`, `no_compression`).
- `python -m pytest tests/ -q` (full suite, after Task 2) → 231 passed, zero failures.
- `grep -c "async def list_open_tasks" agent/tasks.py` → 1.
- `sed -n '/async def list_open_tasks/,/return list/p' agent/tasks.py | grep -c "is_paused"` → 0 (paused tasks are not filtered out).
- `sed -n '/async def list_open_tasks/,/return list/p' agent/tasks.py` contains both `TaskState.DONE` and `TaskState.CANCELLED` (1 match each).
- `grep -c "Open tasks in this chat" agent/context_engine.py` → 1.
- `grep -c "ON PAUSE" agent/context_engine.py` → 1.
- `grep -c "tasks.list_open_tasks" agent/context_engine.py` → 1 (single injection site).
- `grep -cE "list_open_tasks|list_tasks_for_chat" agent/ws.py` → 0 (no second context path).
- `grep -c "def build_task_prompt" agent/context_engine.py` → 0.

### Pre-checkpoint automation (per Task 3's `<action>`)

- `python -m pytest tests/ -q` → 231 passed, zero failures (green immediately before the checkpoint).
- `python run.py` started successfully: `GET http://localhost:8001/health` returned `{"status":"ok"}`; `GET http://localhost:8000/` returned HTTP 200. Both processes were then stopped cleanly (`taskkill` on the listening PIDs for 8000/8001) — the app was NOT left running.

## Issues Encountered

None during Tasks 1-2 or the pre-checkpoint automation.

## Next Phase Readiness — BLOCKED on Task 3

**This plan is paused at Task 3, a `checkpoint:human-verify` gate (`gate="blocking"`).** Tasks 1 and 2 are complete, tested, and committed. Task 3 requires a real human, using a real browser, against a real tool-capable model (DeepSeek or LM Studio) — it cannot be completed by an autonomous agent. See the executor's final report / the orchestrator's relay for the exact nine-step verification checklist reproduced from the plan's `<how-to-verify>`.

Once the developer completes the nine steps and either types "approved" or reports deviations, this SUMMARY.md must be rewritten to its final form recording:
- the model/backend used
- whether tool-calling fired reliably
- the resolution of RESEARCH Open Question 1 (whether pause/resume should also appear in the history timeline — this build deliberately omits them per D-04)

Only after that: push branch `Day13` and merge into `main` (per PROJECT.md convention), and `requirements-completed` can be populated with TASK-01 through TASK-05.

---
*Phase: 04-task-state-machine-day-13*
*Status: PAUSED at Task 3 checkpoint — Tasks 1-2 completed 2026-09-20*
