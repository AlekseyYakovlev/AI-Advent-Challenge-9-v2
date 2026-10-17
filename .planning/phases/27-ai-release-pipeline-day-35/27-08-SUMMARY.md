# 27-08 Summary: sandbox control runs and Day35 report

**Status:** complete. All four D-18 scenarios passed in the final run (19:56 UTC, `qwen/qwen3.5-9b`, scratch repo `release-sandbox`, isolated copy on 18000/18001). Full suite: 4134 passed, 15 skipped.

## What was done
- Ran the four scenarios; results in `27-CONTROL-RUNS.json` (token-free, `model_retries` empty).
- Wrote `Day35_report.md` (Russian): pipeline, A1 tag message assumption, D-04/D-20 deviation, scenarios, changelog quality, not-run items, video preparation, tests.

## Deviations (fixed during the control runs)
1. `agent/release_executor.py`: the release step was routed to a non-existent `github` server; `github_create_draft_release` lives on `release-git`. Regression assertion added to `tests/test_release_executor.py`.
2. `agent/release_llm.py`: qwen3.5-9b spends ~6000 reasoning tokens; the 1800 changelog budget returned empty replies. Limits raised to 8192 / 4096.
3. `README.md`: Cyrillic in the 27-07 text broke `test_readme`.
4. `scripts/e2e_release_playwright.py`: merge conflict in scratch setup, planning-only fixture commits, wrong notice frame type (`help_notice`), token re-applied before scenario 1, bounded retry on the model-draft notice (recorded as `model_retries`), failure details in check messages.

## Not done here
The real release of the project repository (done by the user on video, D-15). Preparation steps are in the report.
