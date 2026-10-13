---
phase: 23-help-command-verification-and-report-day-31
plan: 08
subsystem: verification
tags: [help-command, eval, judge, report, ver-10]
requires:
  - phase: 23-05
    provides: control-run driver, frozen control set, threshold
  - phase: 23-06
    provides: /help documentation sections
  - phase: 23-07
    provides: end-to-end run record
provides:
  - "scripts/help_eval.py judge and summary commands"
  - "eval_out/day31 control-run results on two models with judge verdicts"
  - "Full pytest result recorded in 23-CHECKS.md"
  - "Day31_report.md in Russian"
key-files:
  created: [Day31_report.md, eval_out/day31/run_meta.json, eval_out/day31/answers_local.json, eval_out/day31/answers_deepseek.json, eval_out/day31/judge_meta.json, eval_out/day31/summary.md, eval_out/day31/raw/]
  modified: [scripts/help_eval.py, tests/test_help_eval.py, .planning/phases/23-help-command-verification-and-report-day-31/23-CHECKS.md]
requirements-completed: [VER-10, VER-07]
completed: 2026-10-13
---

# Phase 23 Plan 08: Control run, judge, test suite record and Day 31 report

The frozen 10-question control set was run once through `/help` over HTTP on both models, graded by a DeepSeek judge, and written up in `Day31_report.md` with all answers, sources, tool calls and verdicts, failed questions included.

## Tasks

| Task | Commits | Result |
|------|---------|--------|
| 1. Judge and summary commands, control run | 892efd9, d940e08, 3716667 | `judge`, `summary` added; run, judge and summary executed; 25 tests in `tests/test_help_eval.py` pass |
| 2. Test suite record and report | 28d6c44, dcb4473 | `23-CHECKS.md` Test suite section filled; `Day31_report.md` (583 lines) |

## Results

- local `qwen/qwen3.5-9b`: status run, 10 answers, 0 request errors. Judge: верно 5, частично 2, галлюцинация 3. Source matched 9/10, expected tool 10/10, refusal on the outside question 1/1.
- deepseek `deepseek-flash`: status run, 10 answers, 0 request errors. Judge: верно 6, частично 3, галлюцинация 1. Source matched 9/10, expected tool 10/10, refusal 1/1.
- Judge: `deepseek-flash` (the default `deepseek-chat` is not served), same provider as the DeepSeek answering model, stated in the report and in `judge_meta.json` (`same_provider: true`).
- Corpus commits named in the report: threshold measured at 822acac, control run at 892efd9 (documentation last changed in c5faca8).
- Full pytest: `3150 passed, 13 skipped, 32 warnings in 577.31s (0:09:37)`, exit code 0, Status: passed. The 13 skips are environment skips (10 real-PDF tests, 1 live paid DeepSeek test, 2 real GitHub binary tests).
- Criterion 3 is worded in the report as confirmed live only; after a reload only the label, the sources and the quotes are restored.

## Deviations from Plan

**1. [Rule 1 - Bug] Judge reply budget too small for a reasoning model**
- **Found during:** Task 1, first `judge` run
- **Issue:** with the 200-token budget of `rag_judge.JUDGE_MAX_TOKENS`, `deepseek-flash` spent it on reasoning and returned an empty reply: 13 of 20 rows were `judge_error`.
- **Fix:** added `--max-tokens` to `judge` (default 8000) and ran the judge again over the same saved answers (all 20 rows parsed). The answering run was not repeated and no answer was changed. The report states both judge runs.
- **Commit:** d940e08

**2. [Rule 1 - Bug] Alternative expected sources were not recognised**
- **Found during:** Task 1, reading the first summary (source matched 6/10)
- **Issue:** `automatic_checks` split the expected source only on commas, so `README.md or docs/USER_GUIDE.md` never matched.
- **Fix:** split also on `or`; added a test; regenerated `summary.md` (now 9/10 for both models).
- **Commit:** 3716667

**3. Assumption Drift (advisory)**
- Found during: Task 1. Planned: judge via `deepseek-chat` default and the 200-token judge budget. Actual: `deepseek-flash` with an 8000-token budget. Why: the former model is no longer served and the latter is a reasoning model.

## Known Stubs

None.

## Open verification items

- Phase 21 live add and replace to a ready base (`21-HUMAN-UAT.md` items pending): repeat with `python scripts/e2e_kb_folder_playwright.py` (LM Studio and an embedding model needed). Not part of this plan, carried in the report.
- Phase 22 in-app GitHub connection by the user: not reported (`22-CHECKS.md`).
- Failed questions (kept in the report): local doc-run, doc-env, doc-arch, mixed-status, mixed-docs-files; deepseek doc-run, doc-env, doc-api, mixed-status. Repeat: `python scripts/help_eval.py run --models local,deepseek`, then `judge --max-tokens 8000`, then `summary`.
- The 13 skipped tests are not passed checks.

## Self-Check: PASSED

Files present: `Day31_report.md`, `eval_out/day31/{run_meta,answers_local,answers_deepseek,judge_meta}.json`, `eval_out/day31/summary.md`. Commits 892efd9, d940e08, 3716667, 28d6c44, dcb4473 exist. Fixture unchanged; branch Day31; no key-shaped string in outputs or report.
