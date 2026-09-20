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

None — plan executed exactly as written for Tasks 1 and 2. No auto-fixes, no architectural questions, no scope changes.

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
