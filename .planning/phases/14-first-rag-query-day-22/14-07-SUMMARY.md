---
phase: 14-first-rag-query-day-22
plan: 07
status: paused
subsystem: rag-eval
tags: [rag, eval, report, lm-studio]
requires: ["14-02", "14-05"]
provides:
  - live eval outputs under eval_out/day22
  - Day22_report.md
key-files:
  created:
    - eval_out/day22/retrieval.md
    - eval_out/day22/answers.md
    - eval_out/day22/answers.csv
    - eval_out/day22/run_meta.json
    - eval_out/day22/raw/
    - Day22_report.md
    - tests/test_rag_report.py
metrics:
  tasks_complete: 2
  tasks_total: 3
---

# Phase 14 Plan 07: Live RAG eval and Day 22 report Summary (PAUSED at user review)

Live run of qwen/qwen3.5-9b over the frozen control set: no-RAG vs RAG, nomic vs bge-m3 (bge-m3 hit@1/3/5 = 0.75/0.88/0.88, nomic = 0.12/0.25/0.25); verdicts and report written, awaiting user approval (Task 3).

## Tasks

| Task | Name | Status | Commit |
|---|---|---|---|
| 1 | D-16 probe, build KBs, run eval live | done | b84313d |
| 2 | Verdicts, Day22_report.md, structure test | done | dfc42f1 |
| 3 | User reviews verdicts and report | AWAITING | - |

## Verification observed

- D-16 probe: nomic returned 768-dim vector, bge-m3 1024-dim; one combined build-kbs + run worked (no fallback).
- build-kbs: nomic KB 1, bge KB 2, 2424 chunks each, structural 1000/150.
- raw/: 30 files (10 off_none, 10 rag_nomic, 10 rag_bge); answers.csv 30 rows with filled verdicts.
- `pytest tests/test_rag_report.py tests/test_rag_fixture.py -q`: 16 passed.
- `git ls-files eval_out | grep -c "\.db"`: 0.

## Deviations from Plan

1. [Rule 1 - Bug] max_tokens: first run with default 1024 gave empty answers on ~22 of 30 runs (qwen3.5 reasoning exhausted the budget, finish_reason=length). Re-ran with the script's existing `--max-tokens 4096`. Two answers are still truncated: off Q10 (empty) and bge Q07 (cut mid-sentence); documented in the report as limitations. The invalid 1024-token outputs were overwritten before the first eval commit (b84313d holds only the 4096 run).
2. fixture_sha256 mismatch: run_meta.json holds `8c5b76a0...` (CRLF working-copy bytes, Windows autocrlf) while 14-02-SUMMARY records `e1f2641c...` (LF/git blob). Content is identical (hash of CRLF-stripped file equals the recorded one). Report quotes both; test checks run_meta value. scripts/rag_eval.py left unchanged.

## Pending

Task 3 (checkpoint:human-verify): user approval of verdicts in eval_out/day22/answers.csv and Day22_report.md. After "approved" (or corrections), update this SUMMARY (status complete, record approval) and re-run tests/test_rag_report.py.

STATE.md / ROADMAP.md not touched (orchestrator-owned).
