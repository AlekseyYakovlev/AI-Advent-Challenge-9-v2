---
phase: 27-ai-release-pipeline-day-35
plan: 06
subsystem: api
tags: [websocket, release, mcp, git]
requires:
  - phase: 27-02
    provides: release_command grammar and frames
  - phase: 27-03
    provides: build_plan and ReleaseLlm
  - phase: 27-04
    provides: ReleaseMcp, confirm_plan, tool_frames
provides:
  - /release plan and confirm over the chat WebSocket (agent/release_handler.py)
  - in-memory release_plans per chat with cleanup on chat delete
affects: [27-07, 27-08]
tech-stack:
  added: []
  patterns: [thin ws.py hook delegating to a handler module; plan and confirm under chat_locks]
key-files:
  created: [agent/release_handler.py, tests/test_release_ws.py]
  modified: [agent/state.py, agent/ws.py, agent/release_mcp.py, tests/test_cascade_delete.py]
key-decisions:
  - "ws.py change is one branch before the help/support branch plus a tool_frames import"
requirements-completed: [D-01, D-02, D-03, D-05, D-09, D-10, D-21]
duration: 40min
completed: 2026-10-17
---

# Phase 27 Plan 06: /release over WebSocket Summary

`/release` and the `release_confirm` frame are routed to `release_handler.handle_release`, which plans via the user's `release-git` MCP server with the chat's own client/model and confirms the chat's latest plan under `chat_locks`; verified end to end over WS against a temp repo with a local bare origin.

## Tasks

1. Task 1 (346f0d3): `agent/release_handler.py`, `release_plans` in `agent/state.py` (+ cleanup), thin hook in `agent/ws.py` (rate limit applied before the handler; `_preview`/`_tool_call_frame` now imported from `agent/tool_frames.py`), cascade-delete test extended. ws.py diff: 2 insertions, 11 deletions.
2. Task 2 (7e44278): `tests/test_release_ws.py`, 8 tests: plan frames/tool cards with nothing stored, confirm frame (commit CHANGELOG.md only, annotated tag `v2.0 Week 4: Тестовый релиз`, branch and tag pushed to bare origin, Release step unavailable, outcome partial), typed retry skipping done steps, untracked file blocks plan, no-plan notice, missing-server notice, stale plan id, usage notice.

## Verification (observed)

- `pytest tests/test_release_ws.py tests/test_cascade_delete.py tests/test_release_mcp.py -q`: 25 passed.
- `pytest tests/test_cascade_delete.py tests/test_help_ws.py tests/test_mcp_chat_ws.py tests/test_release_command.py -q`: 58 passed.
- Full suite `pytest tests/ -q --ignore=tests/test_mcp_real_github.py`: 4131 passed, 15 skipped, 3 errors, all in `tests/test_invariants_ws.py` (setup errors under xdist); the same file run alone with `-n0` passes 8/8, so the errors look like parallel-run flakiness unrelated to this plan.

## Deviations from Plan

**1. [Rule 1 - Bug] ReleaseMcp did not route the planner's server key**
- **Found during:** Task 2
- **Issue:** `release_planner` calls the caller with `RELEASE_SERVER_NAME` ("release-git"), `release_executor` with "release"; `ReleaseMcp` only recognised "release", so every planner read hit the github row (None) and failed with "not configured".
- **Fix:** `ReleaseMcp.__call__` treats both keys as the release server.
- **Files modified:** agent/release_mcp.py
- **Commit:** 7e44278

**2. [Rule 3] Test mock shape** — `complete_stage` is non-streaming, so the LLM mock returns a JSON chat completion instead of SSE; the test fixture also clears `release_plans` because chat ids repeat across tests.

## Known Stubs

None.

## Threat Flags

None. Plan lookup requires `plan.user_id == user_id`; stale `plan_id` is refused; errors send a generic Russian message and log only the exception class name.

## Self-Check: PASSED

Files agent/release_handler.py, tests/test_release_ws.py exist; commits 346f0d3 and 7e44278 exist.
