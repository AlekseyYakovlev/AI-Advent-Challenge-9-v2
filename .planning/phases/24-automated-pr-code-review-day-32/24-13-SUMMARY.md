---
phase: 24-automated-pr-code-review-day-32
plan: 13
subsystem: pr-review-evidence
tags: [control-run, github-action, lm-studio, deepseek]
requires: ["24-10", "24-11", "24-12"]
provides:
  - eval_out/day32/local/ (five review texts and records, LM Studio qwen/qwen3.5-9b)
  - eval_out/day32/deepseek/ (five review texts and records, DeepSeek deepseek-flash)
  - eval_out/day32/prs.json
affects: [24-14]
key-files:
  created: [eval_out/day32/]
  modified: [.planning/phases/24-automated-pr-code-review-day-32/24-CHECKS.md]
requirements-completed: [REV-07]
duration: n/a
completed: 2026-10-17
---

# Phase 24 Plan 13: Control run on two models Summary

The five control PRs (#2-#6: bug, rules, arch, large, clean) were reviewed by the real GitHub Action on the self-hosted runner twice, on local LM Studio `qwen/qwen3.5-9b` and then on DeepSeek `deepseek-flash`, and all ten outcomes are saved.

## Tasks
1. Open PRs and collect the local run: commit 9735a2e. All five `ok`; `large` was reviewed in 2 parts.
2. Checkpoint: user answered "switched". Read-only config check showed provider DeepSeek (kind `openai`), model `deepseek-flash`.
3. Re-run via `rerun --after-run local`, `wait`, `collect`: commit 6cb75c0. All five `ok`; `Модель:` lines name DeepSeek/deepseek-flash; `comment_id` is the same as in the local run and `head_sha` is new for every PR (D-14); no `.md` is byte-identical to the local one; `large` again reviewed in 2 parts. Secret grep over `eval_out/day32` found nothing.

## Deviations from Plan
None. Per-PR duration is not captured by the control script (`seconds` is null in the records); noted in 24-CHECKS.md.

## Notes
Review settings still point at DeepSeek; switching back to the local provider is the user's choice. Day32 was not pushed by this plan (24-14 does that); the runner should be stopped when the runs are over.

## Self-Check: PASSED
Both runs have five `.json`, five `.md` and `run.json`; commits 9735a2e and 6cb75c0 exist.
