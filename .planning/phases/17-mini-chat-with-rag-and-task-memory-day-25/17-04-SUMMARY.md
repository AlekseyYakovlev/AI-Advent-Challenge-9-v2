---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 04
subsystem: rag-task-memory-api
tags: [rag, task-memory, rest, branch]
requires: [17-01]
provides:
  - GET /memory task_state; PUT goal, DELETE item, POST reset routes
  - branch restore of task memory
  - history_turns in RAG config
affects: [17-05, 17-07, 17-11]
key-files:
  created: [agent/task_memory_api.py, tests/test_task_memory_api.py]
  modified: [agent/main.py, agent/schemas.py, agent/rag_api.py, tests/test_memory_api.py, tests/test_rag_api.py]
requirements-completed: [RCHAT-02, RCHAT-03]
completed: 2026-10-10
---

# Phase 17 Plan 04: Task memory API Summary

Task memory is exposed as a separate `task_state` in the chat memory response, editable through three guarded user-scoped routes, restored atomically on a branch switch, and the RAG config gains a validated `history_turns` (0..10, default 3).

## Commits

| Task | Commit |
|------|--------|
| 1 routes, task_state, (branch restore code) | 80bbb70 |
| 2 history_turns | 7c6e295 |

## Verification (actually run)

- `pytest tests/test_task_memory_api.py tests/test_rag_api.py tests/test_memory_api.py tests/test_scoping.py tests/test_rag_ws.py -q`: 138 passed.
- Full suite: 2038 passed, 11 skipped, 1 failed (`test_supervisor.py::test_agent_restarts_within_5_seconds`, a timing test); rerun alone: 1 passed (load flake, unrelated).

## Deviations from Plan

1. **[Rule 3] Worktree base reset** to c1e5d69 at startup (HEAD was d35a11c without the phase 17 plans).
2. **[Rule 1] Existing test updated:** `tests/test_memory_api.py` asserted the exact key set of the memory response; added `task_state` (and a null assertion). `SEARCH_DEFAULTS` in `tests/test_rag_api.py` gained `history_turns: 3`.
3. **Commit grouping:** the `branch_chat` restore code is in main.py, so it landed in the Task 1 commit together with the branch tests (tests/test_task_memory_api.py is one file); Task 2 commit holds `history_turns`.

## Notes

- Branch restore tests cover older/newer snapshot, no snapshot (clears), skipping an assistant without snapshot, no-op keeps manual edit, restore failure keeps the leaf (500), foreign chat/message 404.
- `.planning/HANDOFF.json` shows as modified in the worktree and was not staged.
- No Co-Authored-By line, per the user's global instruction.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (T-17-16..21 mitigations implemented and tested).

## Self-Check: PASSED

Files and commits 80bbb70, 7c6e295 exist.
