---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 10
subsystem: evaluation-report
tags: [rag, task-memory, report, llm-judge, day25]
requires: ["17-09"]
provides:
  - "Day25_report.md: both dialog scenarios, main vs no-memory baseline, goal-adherence judge column"
  - "eval_out/day25/judge_meta.json and judge columns in answers.csv"
  - "tests/test_rag_report_day25.py: report vs run outputs consistency checks"
affects: ["17-11"]
key-files:
  created: [Day25_report.md, tests/test_rag_report_day25.py, eval_out/day25/judge_meta.json]
  modified: [eval_out/day25/answers.csv]
requirements-completed: [RCHAT-05]
completed: 2026-10-10
---

# Phase 17 Plan 10: Day 25 report and judge run Summary

Day25_report.md (Russian, 159 lines, 9 sections) documents both scenarios with per-turn checks and task-memory contents, the main vs «no memory» comparison for the 13 marked follow-ups (starting with «а за повторное?»), and a DeepSeek `goal_adherence` judge column.

## Commit
- f2d6b26 docs(17-10): Day 25 report with goal-adherence judge run (report, test, answers.csv, judge_meta.json)

## Task 1 (checkpoint, resolved earlier)
- User replied "ready"; `rag_judge.py --check --model deepseek-flash` exit 0 ("check: ok model=deepseek-flash"), re-confirmed at the start of Task 2. The key was never printed or committed.

## Task 2 (observed)
- Judge: 3 invocations over the same file. `max_tokens` 200 gave 14 unparsed rows («ошибка», the reasoning judge ran out of budget); rerun with `--max-tokens 1500` left 2; rerun with 4000 left 0. Final: 48 rows, 18 judged (да 16, частично 1, нет 1), 30 «—» (gated rows, no request). `judge_meta.json` rubric `goal_adherence`, model `deepseek-flash`. `rag_eval.py dialog --render-only` rerun: judge columns survived in answers.csv.
- Verify: `python -m pytest tests/test_rag_report_day25.py tests/test_dialog_report.py tests/test_rag_judge.py -q`: 61 passed. Key-like grep (`sk-`) on report and both meta files: nothing. Fixture diff empty. `.env` not staged.

## Results as reported (all from eval_out/day25)
- Follow-ups: main answered 8/13, baseline 0/13 (all gated); «а за повторное?» (A02): main condensed query found art. 12.9 (cos 0.695, answered), baseline gated (cos 0.555).
- Gated: main 8/24 (6 false, 2 correct out-of-corpus), plus 2 model_idk (false); baseline 22/24 (20 false). Error turns 0, extraction failures 0.
- Goal kept (code proxy): A 0/12, B 11/12. Memory ok 1/3 in each scenario.
- Judge vs proxy: B agrees on 8/8; A disagrees on 6 judged rows (proxy 0, judge «да»); not investigated manually (reported as such).
- Findings reported as they are: A goal rewritten on A02 and A09; no «95»/«60» in memory after A01; B02 term agreement stored under «уточнено» instead of «ограничения», so memory check not credited.

## Deviations from Plan
- [Rule 1 - Bug] Judge `max_tokens` default (200) produced 14 unparsed verdicts with the reasoning model; reran the errored rows with larger `--max-tokens` (judge command supports re-judging error rows without `--force`). No code changed.
- The report has nine `##` sections (the plan text says "eight headings" but lists nine; all nine are present in the listed order).
- Report tables are generated from `dialog_{A,B}_main.md` with a judge column appended and the two long source file names shortened to `КоАП.pdf` / `ФЗ-196.pdf` (noted in the report). `dialog_*.md` renderer does not emit a judge column, so it is added in the report only.

## Assumption Drift (advisory)
- Found during: judge run. Planned: judge rows parse at `max_tokens` 200. Actual: needs ~1500-4000 tokens with `deepseek-flash`. Why: the judge model spends tokens on reasoning.

## Open items
- State updates: `bm-sdk state.advance-plan / update-progress / record-metric` were not attempted (they failed on this STATE.md in 17-09); STATE.md and ROADMAP.md updated through `roadmap.update-plan-progress` only (see result in the final report).
- A-scenario judge/proxy disagreement left unexplained; browser check is plan 17-11.

## Known Stubs
None.

## Self-Check: PASSED
- Day25_report.md, tests/test_rag_report_day25.py, eval_out/day25/judge_meta.json exist; commit f2d6b26 exists; verify command exit 0 (61 passed).
