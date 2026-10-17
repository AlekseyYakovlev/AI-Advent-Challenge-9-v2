---
phase: 24-automated-pr-code-review-day-32
plan: 11
subsystem: evaluation
tags: [judge, deepseek, review, tables]
requires:
  - phase: 24-01
    provides: REV requirements
provides:
  - scripts/review_judge.py (check, judge, tables)
  - tests/test_review_judge.py
affects: [24-report]
key-files:
  created: [scripts/review_judge.py, tests/test_review_judge.py]
metrics:
  completed: 2026-10-13
---

# Phase 24 Plan 11: Review judge Summary

DeepSeek judge for the PR review control set, with strict line-format reply parsing, honest `не запускалось` records and four report tables.

## What was built
- `split_review`, `build_judge_messages` (angle brackets neutralised, data tags), `parse_judge_reply(text, keys)` (tolerates `**`, backticks, dashes, `<think>`; `None` if a key or the count line is missing).
- `judge_run` / `judge_command`: one call per control PR; `smoke` skipped; reviews with `status != ok` or unsplittable body are recorded `не запускалось` / `ревью не получено` with no model call; unparsable reply retried once then `ошибка разбора`; transport failure and preflight failure recorded `не запускалось` with the reason.
- `--check` (preflight only, one line, no files; works without the manifest), `--tables` (no network).
- `render_tables`: Найденные дефекты, Итог по PR, Вклад поиска, Ложные замечания, the not-run reasons, and the self-judging sentence when the run was authored by DeepSeek. Outputs: `judge/<run>.json`, `verdicts.csv`, `tables.md`.

## Verification (actually run)
- `python -m pytest tests/test_review_judge.py tests/test_rag_judge.py -q`: 54 passed (23 new).
- `python scripts/review_judge.py --check` with no key set: prints `preflight: DEEPSEEK_API_KEY is not set`, exit 2.
- Not verified: a live judge call against DeepSeek (no key in this environment) and the real `control_set.json` / collected runs from Plan 24-10 (tests use their own fixtures).

## Deviations from Plan
- Tasks 1 and 2 touch the same two files and were committed together in one commit (ceddb47) rather than two.
- `--check` is handled before the manifest is loaded, so it works before the manifest exists.
- Tables: the "Вклад поиска" cells count only judged defects and add `(не оценено: N)` for the rest, so nothing is estimated.

## Known Stubs
None.

## Self-Check: PASSED
