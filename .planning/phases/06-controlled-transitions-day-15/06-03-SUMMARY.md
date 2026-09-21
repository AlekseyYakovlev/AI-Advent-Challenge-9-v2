---
phase: 06-controlled-transitions-day-15
plan: 03
subsystem: task-lifecycle-rest-ui
tags: [tasks, state-machine, transition-graph, fastapi, rest, vanilla-js]

requires:
  - phase: 06-controlled-transitions-day-15 (06-01)
    provides: agent/tasks.py::IllegalTransitionError, TaskTransition.rejected/.rejection_reason, self-loop pause/resume rejection rows
provides:
  - agent/schemas.py::TaskTransitionResponse.rejected / .rejection_reason
  - agent/main.py 409 Conflict handling on pause/resume/cancel manual task endpoints
  - agent/main.py::_task_to_response passing rejected/rejection_reason through to the client
  - ui/static/app.js::renderTaskHistory inline rendering of refused attempts (edge-move and pause/resume self-loop)
affects: [ui, tasks-tab, rest-api]

tech-stack:
  added: []
  patterns:
    - "409 Conflict on manual task endpoints via try/except tasks.IllegalTransitionError, inside the per-chat lock, after ownership resolves"
    - "Rejected TaskTransition rows serialize with rejected/rejection_reason, self-loop rows (from_state == to_state) included inline, not filtered"
    - "renderTaskHistory branches on entry.rejected; from_state === to_state distinguishes a blocked operation (pause/resume) from a blocked edge move"

key-files:
  created: []
  modified:
    - agent/schemas.py
    - agent/main.py
    - ui/static/app.js
    - tests/test_task_api.py

key-decisions:
  - "Followed the plan's literal Russian wording for both rejected-row shapes (попытка → label: отклонено for edge moves; операция отклонена (label) for pause/resume self-loops) even though this produces only 1, not 2, literal hits for grep \"отклонено\" (see Deviations)."

patterns-established:
  - "Manual task control endpoints (pause/resume/cancel) resolve ownership (404) before catching IllegalTransitionError (409), preserving the 404-before-409 IDOR-safe ordering"
  - "Tasks-tab history rendering never filters rejected/self-loop rows out of the chronological timeline"

requirements-completed: [TRANS-01, TRANS-02, TRANS-03]

duration: 8min
completed: 2026-09-21
---

# Phase 6 Plan 3: Manual Rejection Surfacing (REST 409 + Tasks Tab) Summary

**Manual pause/resume/cancel now return HTTP 409 with an explained `detail` on illegal attempts, and the Tasks tab renders every refused attempt inline, red and struck through, right alongside accepted transitions.**

## Performance

- **Duration:** ~8 min (commit-to-commit; base commit `ab31044` to final task commit `eb8d758`)
- **Tasks:** 2/2 completed
- **Files modified:** 4

## Accomplishments
- `TaskTransitionResponse` now carries `rejected: bool` and `rejection_reason: Optional[str]`, and `_task_to_response` passes both through from every `TaskTransition` ORM row, including pause/resume self-loop rejections.
- `pause_task_endpoint`, `resume_task_endpoint`, and `cancel_task_endpoint` catch `tasks.IllegalTransitionError` and return `409 Conflict` with a human-readable `detail`; ownership (`_get_task_or_404`) still resolves first so a non-owner always gets 404, never 409, even when the underlying operation would also be illegal.
- `renderTaskHistory` in the Tasks tab now branches on `entry.rejected`: rejected rows render `text-red-400 line-through`, with edge-move refusals worded as `попытка → <label>: отклонено` and pause/resume self-loop refusals worded as `операция отклонена (<label>)`, both appending the server's `rejection_reason` in parentheses and the existing timestamp. Rejected rows stay inline in the chronological list; `textContent` only, no `innerHTML`.
- Added 10 new REST tests in `tests/test_task_api.py` covering terminal-state 409s (cancel-on-done, cancel-on-cancelled, pause-on-done), resume-no-op 409, resume-on-paused-non-terminal still 200, cross-user 404-not-409 precedence, and rejected/self-loop history serialization via `GET /api/v1/chats/{chat_id}/tasks`.

## Task Commits

1. **Task 1: Refuse manual pause/resume/cancel with an explained 409 and serialize rejected history** - `f0c8c82` (feat)
2. **Task 2: Render refused attempts inline in the Tasks tab history** - `eb8d758` (feat)

## Files Created/Modified
- `agent/schemas.py` - Added `rejected`/`rejection_reason` fields to `TaskTransitionResponse`
- `agent/main.py` - `_task_to_response` passes rejection fields through; pause/resume/cancel endpoints catch `IllegalTransitionError` and raise 409
- `ui/static/app.js` - `renderTaskHistory` renders rejected rows inline, distinct styling and wording for edge-move vs. self-loop refusals
- `tests/test_task_api.py` - 10 new tests for 409 behavior, 404-precedence, and rejected-history serialization

## Decisions Made
- Reused `TASK_NOTE_MAX_LENGTH` for `rejection_reason`'s max length rather than introducing a new constant, matching the plan's instruction and the existing `note` field's precedent.
- Implemented the two rejected-row text shapes exactly as specified in the plan's `<action>` prose (literal Russian wording), rather than adjusting wording to satisfy the acceptance criteria's grep count — see Deviations.

## Deviations from Plan

None functionally — both tasks were implemented as written. Two plan-authored acceptance-criteria greps don't literally match, both traced to pre-existing/plan-internal inconsistencies rather than incorrect implementation:

**1. [Documentation note, not a fix] `grep -c "HTTP_409_CONFLICT" agent/main.py` returns 5, not 3**
- **Found during:** Task 1
- **Issue:** The plan's acceptance criteria expected exactly 3 hits (one per new endpoint). `agent/main.py` already contained 2 pre-existing `HTTP_409_CONFLICT` usages in `create_user` (username-already-exists), unrelated to this plan's task endpoints.
- **Resolution:** No fix needed — the 3 new hits (one per pause/resume/cancel endpoint) are present and correct; the total count in the file is 5 because of the pre-existing, unrelated usages. Out of scope per the deviation rules' scope boundary (pre-existing code, different feature).

**2. [Documentation note, not a fix] `grep -n "отклонено" ui/static/app.js` returns 1 hit, not "at least 2"**
- **Found during:** Task 2
- **Issue:** The plan's `<action>` text specifies the edge-move wording literally as `: отклонено` (neuter form) and the blocked-operation wording literally as `операция отклонена` (feminine form, grammatically agreeing with the feminine noun `операция`). These are two different word endings, not substrings of each other, so a literal grep for `отклонено` only matches the edge-move wording once; the acceptance criteria's expectation of 2 hits for that exact grep does not hold given the plan's own literal wording.
- **Resolution:** Implemented the wording exactly as written in `<action>` (both shapes are present and distinguishable — see `grep -n "отклонено\|отклонена"` for both). No code change made to force a second `отклонено` hit, since that would mean deviating from the explicit literal text the plan specified for the blocked-operation wording.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- REST and Tasks-tab UI surfaces for controlled-transitions rejections are complete; this closes out the human-driven half of TRANS-02 for Phase 6.
- Full test suite (`pytest tests/ -v`) passes: 315 passed, 0 failed, no regressions.
- `node --check ui/static/app.js` exits 0.
- No blockers for merge; orchestrator owns STATE.md/ROADMAP.md updates after all wave 2 worktree agents complete.

---
*Phase: 06-controlled-transitions-day-15*
*Completed: 2026-09-21*
