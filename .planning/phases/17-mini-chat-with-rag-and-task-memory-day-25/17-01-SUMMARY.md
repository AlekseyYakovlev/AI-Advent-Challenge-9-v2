---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 01
subsystem: rag-task-memory
tags: [rag, task-memory, sqlmodel, extraction]
requires: []
provides:
  - ChatTaskMemory table and ChatRagConfig.history_turns
  - agent/task_memory.py (document, merge, snapshot, extraction, staged update, restore)
  - rag_llm.complete_stage and rag_rank.neutralize_data_tags (public)
affects: [17-03, 17-04, 17-06]
tech-stack:
  added: []
  patterns: [code-owned merge rules, fail-soft LLM extraction, stage-without-commit]
key-files:
  created: [agent/task_memory.py, tests/test_task_memory.py, tests/test_task_memory_extract.py]
  modified: [shared/models.py, shared/database.py, shared/config.py, .env.example, agent/rag_llm.py, agent/rag_rank.py, tests/test_database.py, tests/test_cascade_delete.py]
key-decisions:
  - "Dedicated chattaskmemory table instead of a reserved WorkingMemory row"
  - "User-stated filter keeps USER_OVERLAP_MIN = 0.5 (not lowered)"
requirements-completed: [RCHAT-02]
duration: ~40 min
completed: 2026-10-10
---

# Phase 17 Plan 01: Task-memory foundation Summary

Per-chat task memory (goal, clarified points, constraints) with code-owned merge and user-stated filtering, plus a fail-soft temperature-0 extraction call that stages the update without committing.

## Tasks and commits

| Task | Commit | Result |
|------|--------|--------|
| 1 Table, column, settings | 864b584 | `ChatTaskMemory`, `history_turns` (idempotent migration), `TASK_MEMORY_ENABLED`/`TASK_MEMORY_TIMEOUT` |
| 2 Pure document logic | e62f1fa | merge, filter, parse, snapshot, rendering; 26 tests |
| 3 Shared-helper refactor | 32d53e2 | `complete_stage`, `neutralize_data_tags` public; Phase 15 tests unchanged |
| 4 Extraction and staged update | 96b407d | `update_task_memory`, `extract_delta`, `restore_from_path`, etc. |

## Verification (actually run)

- `pytest tests/test_task_memory_extract.py tests/test_task_memory.py tests/test_database.py tests/test_cascade_delete.py tests/test_rag_llm.py tests/test_rag_rank.py tests/test_rag_pipeline.py -q`: 177 passed.
- Full suite `pytest tests -q`: 2000 passed, 11 skipped.
- `python -c "import agent.task_memory, agent.rag_pipeline, agent.rag_turn, agent.context_engine"`: exit 0 (no import cycle).
- Acceptance greps: no `session.commit()`, no `print(`, no `rag_llm._call` in `agent/task_memory.py`.

## Known limit of the user-stated filter

The synonym-only rewording «Ссылаться исключительно на КоАП РФ» shares 1 of 3 stems (ratio 0.333) with the user message «Отвечай, пожалуйста, только по КоАП, ...», below `USER_OVERLAP_MIN` 0.5, so the filter rejects it. The honest paraphrases from the plan (5/5 and exactly 2/4 shared stems) are kept. This is the input for the bounded fix round of plan 17-09 if live runs show `memory_ok` failures. The threshold was not lowered and no synonym handling was added.

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree base reset.** The worktree HEAD was at d35a11c, not the expected base a96b192 (which contains the phase 17 plans); reset to a96b192 at startup per the worktree branch check.

**2. [Rule 1 - Test fix] Task 4 test data.** My first USER_TEXT sample shared no stem with the sample goal, so the filter correctly dropped the goal; the test sample was corrected (not the code).

**3. Minor additions.** `tests/test_database.py` table-set assertion extended with `chattaskmemory`. `agent/task_memory.py` imports `_DIGIT_TOKEN_RE` and `_normalise_ws` from `agent.rag_rank` (private names, as the plan said to reuse them; the acceptance grep only forbids `rag_llm._call`).

## Notes

- `.planning/HANDOFF.json` showed as modified in the worktree throughout (not by this plan); it was not staged or committed.
- Commit messages carry no Co-Authored-By line, per the user's global instruction.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (T-17-01..06 mitigations implemented: tag neutralisation, user-stated filter, caps, pydantic validation, no text in logs, `user_id` column).

## Self-Check: PASSED

Files and commits 864b584, e62f1fa, 32d53e2, 96b407d exist.
