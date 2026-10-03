---
phase: 15-reranking-and-filtering-day-23
plan: 11
subsystem: rag-evaluation
tags: [rag, llm-judge, report, deepseek]
requires:
  - phase: 15-10
    provides: 70 answers with manual verdicts
  - phase: 15-13
    provides: scripts/rag_judge.py
provides:
  - judge_verdict / judge_comment for 70 rows (deepseek-flash)
  - eval_out/day23/judge_meta.json
  - Day23_report.md, tests/test_rag_report_day23.py
requirements-completed: [RANK-07, RANK-09]
requirements-partial: [RANK-08]
status: awaiting-user-review (Task 3 pending)
completed: 2026-10-03
---

# Phase 15 Plan 11: Judge column and Day 23 report Summary

Tasks 1 and 2 are done and committed. Task 3 (user review of verdicts and the report) is pending; nothing has been approved yet.

## Task 1: key and judge model

- `python scripts/rag_judge.py --check --model deepseek-flash` printed `check: ok model=deepseek-flash`.
- The default model `deepseek-chat` is not available on the account (the API lists `deepseek-flash` and `deepseek-v4-pro`). The user chose `deepseek-flash`.
- The key was never printed, logged or committed. `.env` was not read. `grep` for key-shaped strings in the report, judge_meta.json and answers.csv returned nothing.

## Task 2: judge run and report (commits 987b829, ab77232)

- Judge model: `deepseek-flash`, temperature 0. 70 rows judged: верно 54, частично 7, неверно 4, галлюцинация 5.
- Agreement with the manual verdicts: 61 of 70 (87%). Manual columns are unchanged (compared with the committed version: 0 differences outside judge_verdict and judge_comment).
- Disagreements (9): Q02 lexical (manual частично, judge верно), Q02 hybrid (частично vs неверно), Q03 all (верно vs частично), Q08 threshold, lexical, rewrite, all (галлюцинация vs частично), Q08 llm_rerank, hybrid (частично vs галлюцинация).
- Day23_report.md (191 lines, Russian, 11 required sections) and tests/test_rag_report_day23.py (12 tests, all pass; 29 passed together with tests/test_rag_judge.py).

### Context for the reader

- `CALIBRATED_THRESHOLDS = {"bge-m3": 0.67}`. nomic is intentionally absent (classes not separable, the rule value 0.79 was rejected): user decision option-b at 15-09 Task 1.
- D-08 FTS exemption amended (article-number match or lexical overlap >= 0.5): user decision option-b at 15-09 Task 2.
- The manual verdicts from 15-10 were written by Claude and are NOT yet reviewed by the user. The report says so.
- No stage beat the baseline on this 10-question set: baseline hit@5 0.88, stage runs 0.62 to 0.75. The 0.67 threshold cuts all chunks for Q01, Q07, Q08. Baseline Q07 is empty (token budget, finish_reason length).
- For Q01, Q02, Q07, Q08 the model input is identical across threshold, lexical, llm_rerank and hybrid, yet the verdicts differ. Part of the per-stage differences is therefore generation noise at temperature 0, not stage effect. The report states this.

## Deviations from Plan

**1. [Rule 3 - Blocking] Judge budget for a reasoning model**
- **Found during:** Task 2
- **Issue:** `deepseek-flash` is a reasoning model. With the script's 200-token reply budget, 19 of 70 replies came back empty (finish_reason length), so those rows stayed «ошибка» after the retry.
- **Fix:** added `--max-tokens` (default stays 200) to `scripts/rag_judge.py`, plus `sys.stdout.reconfigure(encoding="utf-8")` because the final print crashed on cp1252 consoles. Reran only the error rows at 2000, 4000, then 8000 tokens; all 70 rows ended with a valid verdict. The column therefore mixes budgets: 51 rows at 200 tokens, 19 at larger budgets. judge_meta.json records 8000. The report states this. `tests/test_rag_judge.py` passes (17 tests).
- **Files modified:** scripts/rag_judge.py
- **Commit:** 987b829

## Assumption Drift (advisory)

- **Found during:** Task 2. **Planned:** a general non-reasoning chat model as judge with a 200-token budget. **Actual:** `deepseek-flash` is reasoning-heavy and needed up to 8000 tokens on some rows. **Why:** deepseek-chat is not available on the account; the user picked deepseek-flash.

## Known Stubs

None.

## Task 3: pending

Checkpoint `human-verify` is not done. Awaiting the user's "approved" or corrections for answers.csv verdicts and Day23_report.md. After approval or corrections the verdict counts in the report must be re-derived from answers.csv, the tests rerun and the result committed. STATE.md and ROADMAP.md were not modified. app.db was not touched.

## Self-Check: PASSED

Commits 987b829 and ab77232 exist; Day23_report.md, tests/test_rag_report_day23.py and eval_out/day23/judge_meta.json exist.
