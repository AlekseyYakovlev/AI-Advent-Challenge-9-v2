---
phase: 15-reranking-and-filtering-day-23
plan: 13
subsystem: rag-evaluation
tags: [llm-judge, deepseek, eval, scripts]
requires: []
provides:
  - "scripts/rag_judge.py: DeepSeek LLM-judge writing judge_verdict / judge_comment, with --check preflight"
affects: [15-11]
tech-stack:
  added: []
  patterns: ["sequential per-row judge calls", "key read only from settings, never printed"]
key-files:
  created: [scripts/rag_judge.py, tests/test_rag_judge.py]
  modified: []
key-decisions:
  - "Judge model default is the literal deepseek-chat, overridable with --model"
  - "Manual verdict/comment columns are never written by the script"
requirements-completed: [RANK-09]
duration: 15min
completed: 2026-10-03
---

# Phase 15 Plan 13: LLM-judge script Summary

DeepSeek LLM-judge (`scripts/rag_judge.py`) that fills only the judge columns of answers.csv, exits 2 without network when the key is empty, and offers `--check` to prove key and model acceptance.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1. Judge core (prompt, parser, row loop, CSV round-trip) | 1205b8e | 10 offline tests pass |
| 2. Command flow (preflight, --check, metadata, no key leak) | 3bed4df | 17 offline tests pass in total |

## Verification (actually run)

- `python -m pytest tests/test_rag_judge.py -q`: 17 passed.
- `python scripts/rag_judge.py --help`: lists --check, --model, --force.
- grep gates: "DEEPSEEK_API_KEY is not set" count 1; asyncio.gather count 0; key-print gate count 0.
- Not run: no live DeepSeek call (needs a real key); the live run belongs to plan 15-11.

## Deviations from Plan

- [Rule 3 - Blocking] The worktree base was 87d51ec rather than the expected a0f2e3a; reset to a0f2e3a per the branch check before starting.
- check_access initialises `available` up front so a 400/404 on the completion can report the model list.

## Known Stubs

None.

## Self-Check: PASSED
