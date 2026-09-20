---
phase: 04-task-state-machine-day-13
plan: 04
status: complete
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
  - tests/test_task_ws.py (WS-level regression test for the payload-shadowing crash)
  - fix: agent/ws.py payload-shadowing crash after task tool calls
affects: [context-engine, task-state-machine, day-13-acceptance-demo, ws-chat-flow]

# Tech tracking
tech-stack:
  added: []
  patterns: [single-chokepoint context assembly (no second task-read path in agent/ws.py), self-bounding prompt block (terminal tasks drop out permanently)]

key-files:
  created:
    - tests/test_context_engine_tasks.py
    - tests/test_task_ws.py
  modified:
    - agent/tasks.py
    - agent/context_engine.py
    - agent/ws.py

key-decisions:
  - "RESEARCH Open Question 1 resolved: pause/resume stay OUT of the history timeline (D-04 confirmed as-is). The live demo did not surface a need to change this."

patterns-established:
  - "Open-task injection lives only inside build_system_prompt, mirroring the existing memory/profile injection idiom — no build_task_prompt() helper, no second read path in agent/ws.py."
  - "Never reuse a function parameter name as a loop variable inside that function — agent/ws.py's `payload` shadowing bug is the concrete cautionary example now covered by a regression test."

requirements-completed: [TASK-01, TASK-02, TASK-03, TASK-04, TASK-05]

# Metrics
duration: ~2.5 hours (Tasks 1-2 automated, plus one bug found/fixed/reverified during the Task 3 checkpoint)
completed: 2026-09-20
---

# Phase 4 Plan 4: Open-task context injection + Day 13 acceptance demo

**`list_open_tasks` (excludes done/cancelled, includes paused) is injected into `build_system_prompt` as an `Open tasks in this chat:` block on every turn under every compression strategy. The Day 13 acceptance demo found and the team fixed a real crash in the WebSocket chat handler, then re-verified live. Developer approved.**

## Performance

- **Tasks completed:** 3 of 3
- **Files modified:** 3 (`agent/tasks.py`, `agent/context_engine.py`, `agent/ws.py`)
- **Files created:** 2 (`tests/test_context_engine_tasks.py`, `tests/test_task_ws.py`)

## Accomplishments

- `agent/tasks.py::list_open_tasks(session, chat_id)` — `select(Task).where(chat_id == chat_id).where(state.not_in([DONE, CANCELLED])).order_by(created_at, id)`, paused tasks always included.
- `agent/context_engine.py::build_system_prompt` appends one `Open tasks in this chat:\n- #{id} "{title}" (state={state}{ [ON PAUSE]}): goal={goal!r}` block, built from `tasks.list_open_tasks`, only when the list is non-empty. Placed immediately before the final `"\n\n".join(parts)`, so it reaches `build_llm_context` — and therefore every turn under every `ContextStrategy` — with no second read path (`agent/ws.py` untouched by this task; `grep -cE "list_open_tasks|list_tasks_for_chat" agent/ws.py` → 0).
- `tests/test_context_engine_tasks.py` — 11 tests (8 named + 4 parametrised `ContextStrategy` cases counted as one function): injection with header/id/title/state/goal, multi-task, `[ON PAUSE]` marker with state preserved, done exclusion, cancelled exclusion, empty-chat no-block, cross-chat scoping, and survival across all four compression strategies.
- `agent/ws.py` — fixed the `payload` variable-shadowing crash (see below) and added `tests/test_task_ws.py` to lock the `done`-frame contract for task-tool turns.
- **Day 13 acceptance demo completed live** against LM Studio (`qwen/qwen3.5-9b`) by the developer in a real browser: create_task, multiple tasks per chat, full planning→execution→validation→done lifecycle, pause/resume with context-carried-forward continuation, manual-only cancel (LLM correctly refused/transitioned instead of cancelling), history timelines, and chat-delete cascade.

## Task Commits

1. **Task 1: Write the failing tests for open-task context injection** - `418f84b` (test)
2. **Task 2: Inject the chat's open tasks into build_system_prompt** - `1f3d889` (feat)
3. **Task 3: Day 13 acceptance demo** - checkpoint; bug found and fixed during verification:
   - `413b805` — `fix(04-04): stop the create_task/transition_task WS handler from crashing after persist`
   - `e7477d0` — `docs(04-04): record the WS payload-shadowing bug found during the acceptance demo`
   - this commit — final SUMMARY.md rewrite after developer approval

## Files Created/Modified

- `tests/test_context_engine_tasks.py` - open-task injection test module (8 test functions, 11 collected cases with parametrisation)
- `tests/test_task_ws.py` - WS-level regression test: a `create_task` turn must end in exactly one `done` frame
- `agent/tasks.py` - added `list_open_tasks`
- `agent/context_engine.py` - extended `from agent import memory, profile` to include `tasks`; added the open-task block inside `build_system_prompt`
- `agent/ws.py` - fixed `payload` variable shadowing in `_handle_chat_message`'s task_writes loop (renamed to `task_result`)

## Decisions Made

- Tasks 1-2 followed the plan's `<action>` verbatim (query shape, injection line format, placement point).
- **RESEARCH Open Question 1 (pause/resume in the history timeline):** resolved as-is — they stay excluded (D-04). The live demo confirmed the paused-context re-injection into the system prompt already delivers TASK-04's "resume without re-explaining" guarantee; a separate timeline entry for pause/resume was not something the demo made the team want.

## Deviations from Plan

None for Tasks 1-2 — executed exactly as written. One deviation surfaced during the Task 3 acceptance demo, found, fixed, and re-verified within this same plan (see below) rather than deferred.

### Bug found and fixed during Task 3's acceptance demo

The developer's first pass through the checklist found: after the LLM created a task via `create_task`, the chat UI hung on the typing indicator forever; only a page reload (F5) revealed the completed reply and the new task card.

**Root cause** (confirmed via `logs/agent.log` — the Agent subprocess's structlog output goes there, not to the `python run.py` console, since `ui/supervisor.py` redirects the subprocess's stdout/stderr to a file): `agent/ws.py::_handle_chat_message` reused the function's `payload: MessagePayload` parameter as the loop variable while building `task_writes` (`payload = json.loads(result["content"])`), shadowing it for the rest of the function. The later `extract_and_update_facts(session, chat_id, payload.content, payload.model)` call then read `.content`/`.model` off that plain `dict` and raised `AttributeError: 'dict' object has no attribute 'content'` — after the task and assistant message were already committed to the DB, but before the WS `done` frame was sent. The dead ASGI connection was invisible to the user; the frontend's auto-reconnect opened a fresh socket ~12s later with no way to recover the orphaned turn. This fired on every `create_task`/`transition_task`/`pause_task`/`resume_task` call, deterministically — confirmed by both task-creation turns in the original demo session hitting the identical traceback.

**Fix:** renamed the shadowing loop variable to `task_result` (commit `413b805`). Added `tests/test_task_ws.py::test_create_task_turn_completes_with_done_frame`, a WS-level regression test (mirroring `test_memory_ws.py`'s respx-mocked pattern) asserting a `create_task` turn always ends in exactly one `done` frame; verified it fails against the pre-fix code (`AttributeError` surfaces, no `done` frame sent) and passes against the fix.

**Re-verification:** developer restarted the app and re-ran the full nine-step demo live (create, two tasks, full 4-state lifecycle, pause/resume, manual-only cancel, history, cascade). `logs/agent.log` for the entire retest window (fresh on restart, `agent_starting` at 17:38:58Z through the end of the session) shows `task_created`, `task_pause_toggled` ×2, `task_transitioned` ×3, `task_cancelled` ×2, and **zero `AttributeError`/`Traceback`/`ERROR` entries anywhere** — a stronger check than the narrow regression test alone, since it exercises the full lifecycle live against a real model.

## Verification Evidence

- RED phase: `python -m pytest tests/test_context_engine_tasks.py -x` → 1 failed on the first assertion (`assert 'Open tasks in this chat:' in 'Base prompt'`), confirming the injection did not yet exist.
- `python -m pytest tests/ -q --ignore=tests/test_context_engine_tasks.py` (RED-phase checkpoint) → 220 passed, zero regressions.
- GREEN phase: `python -m pytest tests/test_context_engine_tasks.py -v` → 11/11 passed, including all four parametrised `ContextStrategy` cases (`sliding`, `sticky`, `truncate_middle`, `no_compression`).
- `grep -c "async def list_open_tasks" agent/tasks.py` → 1.
- `sed -n '/async def list_open_tasks/,/return list/p' agent/tasks.py | grep -c "is_paused"` → 0 (paused tasks are not filtered out).
- `sed -n '/async def list_open_tasks/,/return list/p' agent/tasks.py` contains both `TaskState.DONE` and `TaskState.CANCELLED` (1 match each).
- `grep -c "Open tasks in this chat" agent/context_engine.py` → 1.
- `grep -c "ON PAUSE" agent/context_engine.py` → 1.
- `grep -c "tasks.list_open_tasks" agent/context_engine.py` → 1 (single injection site).
- `grep -cE "list_open_tasks|list_tasks_for_chat" agent/ws.py` → 0 (no second context path).
- `grep -c "def build_task_prompt" agent/context_engine.py` → 0.
- Bug regression test: `tests/test_task_ws.py::test_create_task_turn_completes_with_done_frame` fails on pre-fix `agent/ws.py` (`AttributeError`, no `done` frame) and passes on the fix.
- Full suite after the fix: `python -m pytest tests/ -q` → **232 passed, zero failures**.
- Live retest via `logs/agent.log`: full task lifecycle exercised (create, 2× pause/resume, 3× transition, 2× cancel), zero crashes.

### Day 13 acceptance demo — developer sign-off

- **Model/backend used:** LM Studio, local, model `qwen/qwen3.5-9b` (matches the DeepSeek/LM Studio routing precedent from `02-05-SUMMARY.md`).
- **Tool-calling reliability:** fired reliably across `create_task`, `transition_task` (×3, walking planning→execution→validation→done), `pause_task`/`resume_task` (×2), across two independent tasks in one chat. The LLM correctly refused to cancel a task directly when asked (no `cancelled` in its tool schema per D-07/D-12) — it transitioned/deferred instead, matching the plan's expectation.
- **TASK-04 confirmed live:** after pausing a task, asking "Что у нас на паузе и что дальше?" got an answer using the paused task's title/goal without the user restating them — the open-task system-prompt injection built in Task 2 is what made this work.
- **Bug found and fixed within this same checkpoint** (see Deviations above) — not deferred to a follow-up phase.
- **Developer verdict:** approved, after the fix and live re-verification.
- **RESEARCH Open Question 1:** resolved — pause/resume stay out of the history timeline (D-04 confirmed, no change wanted after seeing it live).

## Issues Encountered

One: the `payload`-shadowing WS crash (see Deviations). Found via the acceptance demo, root-caused via `logs/agent.log`, fixed, covered by a new regression test, and re-verified live before sign-off.

## Next Phase Readiness

**Day 13 (Phase 4: Task State Machine) is complete.** All five requirements (TASK-01 through TASK-05) are demonstrated end-to-end against a real tool-capable model:
- TASK-01 (lifecycle): `transition_task` walks planning→execution→validation→done, each move recorded.
- TASK-02 (multiple tasks per chat): independent cards, no implicit "current task."
- TASK-03 (LLM creates tasks): `create_task` recognized and dispatched correctly.
- TASK-04 (pause/resume without re-explaining): system-prompt re-injection carries paused-task context forward.
- TASK-05 (history): chronological, Russian-labeled timelines.

No transition-graph legality enforcement was introduced anywhere in this phase (TRANS-01/02/03 remain deferred to Phase 6, as planned) — Phase 6 inherits a clean, unhardened state machine to harden.

**Next:** per PROJECT.md convention, push branch `Day13` and merge into `main` (branch retained, never deleted).

---
*Phase: 04-task-state-machine-day-13*
*Status: COMPLETE — approved 2026-09-20*
