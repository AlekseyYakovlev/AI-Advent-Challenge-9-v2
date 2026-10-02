---
phase: 09-auto-rename-chats-with-llm-day-21
plan: 05
subsystem: agent
tags: [llm, titles, reasoning, gap-closure]
requires: [09-01, 09-03]
provides: [reasoning-disabled title call, chat_title_llm_unusable log event, bounded title regex input]
affects: [agent/llm_client.py, agent/titles.py, docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md]
key-files:
  created: [tests/test_llm_complete_chat.py]
  modified: [agent/llm_client.py, agent/titles.py, tests/test_titles.py, tests/test_titles_ws.py, docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md, .planning/phases/09-auto-rename-chats-with-llm-day-21/09-04-SUMMARY.md]
requirements-completed: [TITLE-01, TITLE-02, TITLE-03]
metrics:
  tasks: 3 of 3
---

# Phase 9 Plan 05: Reasoning-safe title call Summary

The title request now sends `reasoning_effort: "none"` (max_tokens 30, temperature 0), retries once without the field on HTTP 400/422, logs an unusable model answer as `chat_title_llm_unusable`, and the title helpers cut their input before any regex runs.

## Tasks

1. `LLMClient.complete_chat_detailed` + `ChatCompletionResult` + `_post_chat_completion`; `complete_chat` signature and five-key payload unchanged: 7e86554
2. `agent/titles.py` reasoning control, one-shot rejection retry, unusable-output log, CR-01 input bounds; tests adapted and added: 1a2df86
3. Docs sync and 09-04-SUMMARY UAT correction: c63a25f

## Verification (observed)

- `pytest tests/test_llm_complete_chat.py tests/test_llm_tools_stream.py tests/test_invariants_ws.py tests/test_context_engine.py -q`: 35 passed after the fix noted below.
- `pytest tests/test_titles.py tests/test_titles_ws.py tests/test_llm_complete_chat.py tests/test_cascade_delete.py -q`: 86 passed.
- `pytest tests/test_titles.py -k "reasoning_style or rejected or not_retried or source_llm or never_contain or bounds"`: 10 passed.
- Full suite `pytest tests/ -q`: `1009 passed, 9 warnings in 201.27s` (baseline 990).
- `git diff --stat 2bfbc2b -- agent/ws.py agent/state.py ui/ agent/context_engine.py agent/invariants.py`: empty.
- `grep -v '^\s*#' agent/titles.py | grep -c 'llm_client\.complete_chat('`: 0.
- 09-04-SUMMARY.md: `source=fallback` appears 7 times; correction paragraph and requirements-completed updated.

Not verified: a live `source=llm` title against LM Studio; that is plan 09-06's job. The `reasoning_effort: "none"` behaviour on the real model rests on the planner's probe documented in the plan, not on a run in this plan.

## Deviations from Plan

- Worktree base was reset to 2bfbc2b per the branch check (merge-base was 64d13b5).
- Test fix during Task 1: my first version of the Authorization-header test called `route.calls.reset()`, which failed; replaced with checking `route.calls[1]`. Test-only, no production impact.
- `tests/test_titles_ws.py::test_done_is_not_delayed_by_blocked_title_call`: the patched fake no longer needs the facts-extraction branch (those calls go through respx), so it only handles the title call.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED
