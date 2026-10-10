---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 08
subsystem: evaluation-scripts
tags: [rag, dialog, report, judge, goal-adherence]
requires: ["17-05"]
provides:
  - "pure renderers for dialog runs (transcript, follow-ups, summary, CSV) in scripts/rag_dialog.py"
  - "rag_eval.py dialog --render-only"
  - "goal_adherence rubric in scripts/rag_judge.py"
affects: ["17-09", "17-10"]
key-files:
  created: [tests/test_dialog_report.py]
  modified: [scripts/rag_dialog.py, scripts/rag_eval.py, scripts/rag_judge.py, tests/test_rag_judge.py]
requirements-completed: [RCHAT-05]
completed: 2026-10-10
---

# Phase 17 Plan 08: Dialog report renderers and goal_adherence judge Summary

Russian per-turn transcript tables, a main-vs-baseline follow-up comparison, a summary and an answers CSV rendered from raw dialog files without a model (`--render-only`), plus a DeepSeek `goal_adherence` rubric that fills `judge_goal` / `judge_goal_reason`.

## Commits
- 6218515 feat(17-08): dialog report renderers and `--render-only`
- 7a9f042 feat(17-08): goal_adherence rubric

## What was built
- `scripts/rag_dialog.py`: `render_transcript_md`, `render_followups_md`, `render_summary_md`, `render_answers_csv`, `dialog_metrics`, plus `load_raws`, `load_existing_judge`, `_render_outputs`, `render_only`. Seven outputs: four `dialog_{scenario}_{run}.md`, `followups.md`, `dialog_summary.md`, `answers.csv`. Gated, error, failed-memory ("память не обновлена") and baseline (no snapshot, "—") turns are all rendered; items added in a turn are marked "(новое)". Judge columns are carried over from an existing `answers.csv` on re-render. `dialog_command` also renders after a live run. No manual-verdict column exists.
- `scripts/rag_eval.py`: `--render-only` flag on `dialog`; routed before any preflight needing LM Studio or ports; exits 2 when there are no raw files.
- `scripts/rag_judge.py`: `goal_adherence` in `RUBRICS`, `build_goal_adherence_messages` (four neutralised tags, system prompt says tags are data), `judge_goal_rows`, `_goal_command`. Only `ok` / `model_idk` turns with a non-empty answer are sent; other rows get "—" without a request. Existing `judge_goal` is skipped unless `--force` (errors are retried). Default answers `eval_out/day25/answers.csv`, meta `judge_meta.json` beside it; meta has no key. Does not need the Day 23 control fixture.

## Verification (observed)
- `python -m pytest tests/test_dialog_report.py tests/test_dialog_eval.py tests/test_rag_judge.py tests/test_rag_eval_cite.py -q`: 93 passed.
- `tests/test_dialog_report.py`: 18 tests; 7 new `goal_adherence` tests in `tests/test_rag_judge.py`; no existing test lines removed.
- `python scripts/rag_eval.py dialog --help` lists `--render-only`; `python scripts/rag_judge.py --help` lists `goal_adherence`.
- Not verified: a live DeepSeek judge run and a render over real raw data (those belong to plans 17-09 / 17-10).

## Deviations from Plan
- [Rule 3 - Blocking] The worktree HEAD was behind the required base commit; reset to 71b1e83 as the startup check prescribes, so the plan file became available.
- The CSV `quotes` column holds quote counts (model / auto / unverified), not quote text, since the raw record keeps counts in `checks`.
- `.planning/HANDOFF.json` showed as modified in the worktree without my touching it; it was not staged or committed.

## Known Stubs
None.

## Self-Check: PASSED
