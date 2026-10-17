---
phase: 25-support-assistant-day-33
plan: 09
subsystem: testing
tags: [retrieval, embeddings, threshold, support]
requires:
  - phase: 25-support-assistant-day-33
    provides: support_lookup.retrieval_query, docs/FAQ.md, /support command defaults
provides:
  - scripts/support_probe.py retrieval probe on an isolated copy
  - 25-RETRIEVAL-PROBE.md with measured scores and decisions
affects: [25 docs plan]
tech-stack:
  added: []
  patterns: [pure decision helpers separated from the isolated-app driver]
key-files:
  created: [scripts/support_probe.py, tests/test_support_probe.py, .planning/phases/25-support-assistant-day-33/25-RETRIEVAL-PROBE.md]
  modified: []
key-decisions:
  - "DEFAULT_SUPPORT_THRESHOLD stays 0.45 (threshold_decision: kept)"
  - "RETRIEVAL_HINT_ENABLED stays True (both ticket-aware queries rank docs/FAQ.md first)"
requirements-completed: [SUP-07, SUP-04]
duration: ~20min
completed: 2026-10-17
---

# Phase 25 Plan 09: Retrieval probe Summary

Retrieval probe measured on the real `text-embedding-bge-m3` model confirms the 0.45 /support threshold and the ticket-aware query; no source default changed.

## Results (observed)
- Probe actually ran on the isolated copy (ports 18000/18001), 351 chunks, 6 files including docs/FAQ.md.
- 6 support questions: best scores 0.621-0.780, best source docs/FAQ.md for 6 of 6.
- 2 ticket-aware queries (T-1001, T-1002): 0.759 and 0.730, FAQ rank 1.
- 2 out-of-corpus questions: 0.427 and 0.379 (docs/API_SPEC.md), below 0.45.
- Threshold decision: 0.45 kept. Hint decision: kept enabled.
- Tests: `pytest tests/test_support_probe.py` 10 passed; `pytest tests/test_support_command.py tests/test_support_lookup.py tests/test_help_command.py tests/test_docs_help.py` 132 passed. `DEFAULT_HELP_THRESHOLD` still 0.45; `SupportConfig.threshold` equals `DEFAULT_SUPPORT_THRESHOLD` (0.45).

Note: the outside margin is small (0.427 vs 0.45); the rule keeps the value because it separates the sets, but a future corpus change could narrow it.

## Task Commits
1. Task 1 probe script and tests: 4d4da7a
2. Task 2 run and record: 6201f2f

## Deviations from Plan
None. Raw output is in untracked `eval_out/day33/support_probe.json`.

## Self-Check: PASSED
