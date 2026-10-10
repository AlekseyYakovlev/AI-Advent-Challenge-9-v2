---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 09
subsystem: evaluation-runs
tags: [rag, dialog, live-run, task-memory, baseline]
requires: ["17-05", "17-06", "17-08"]
provides:
  - "eval_out/day25 raw records of both frozen scenarios, main and no-memory baseline"
  - "rendered transcripts, followups.md, dialog_summary.md, answers.csv (judge columns empty)"
affects: ["17-10", "17-11"]
key-files:
  created: [eval_out/day25/run_meta.json, eval_out/day25/raw/main_A.json, eval_out/day25/raw/main_B.json, eval_out/day25/raw/baseline_A.json, eval_out/day25/raw/baseline_B.json, eval_out/day25/followups.md, eval_out/day25/dialog_summary.md, eval_out/day25/answers.csv]
  modified: [scripts/rag_dialog.py, tests/test_dialog_eval.py, agent/task_memory.py, tests/test_task_memory.py]
requirements-completed: [RCHAT-01, RCHAT-04]
completed: 2026-10-10
---

# Phase 17 Plan 09: Live dialog runs Summary

Both frozen scenarios (12 turns each) ran end to end through the real WebSocket chat on the isolated copy (ports 18000/18001, local `qwen/qwen3.5-9b`, bge-m3 KB id 2, calibrated threshold 0.67, strict on, temperature 0), once with task memory and history-aware retrieval and once as the `TASK_MEMORY_ENABLED=false` baseline.

## Commits
- 77075cc fix(17-09): pace dialog driver under the per-chat WebSocket rate limit
- 6a69ff5 fix(17-09): accept echoed memory objects in the task-memory extraction reply
- 58f03b7 feat(17-09): run both dialog scenarios, main and no-memory baseline (eval_out/day25)

## Task 1 (preflight, observed)
- `python -m pytest tests/ -q`: 2174 passed, 11 skipped. `dialog --check`: exit 0 ("check passed: app starts, owner logs in, KB is ready", isolated ports free again). After the two fixes below the full suite was rerun: 2176 passed, 11 skipped.
## Task 2
- User replied "ready" (LM Studio started with `qwen/qwen3.5-9b` and `text-embedding-bge-m3`); `dialog --check` exit 0 was rerun at the start of Task 3.

## Fix rounds (2 of 2 used)
1. Seen in the first main A run: turns A11 and A12 returned `Rate limit exceeded`. Gated turns finish in 2-3 s, so more than 10 messages per minute reached the agent limit (`RATE_LIMIT_MAX = 10` per chat per minute). Fix: the driver paces itself under the limit (`send_delay`, at most 9 messages per 60 s) with a unit test. The agent limit was not changed. Commit 77075cc.
2. Seen in the rerun: task-memory extraction failed on 6 of 12 turns in main A (more than a third). Reproduced against LM Studio with reasoning off: the model echoes memory items as `{"id", "text"}` objects, and strict validation rejected the whole delta. Fix: `parse_delta` flattens such objects to strings before validation, with a unit test. Commit 6a69ff5.
After each fix all scenario runs already made were redone with `--force`; the final four runs come from commit 6a69ff5 (`run_meta.json` records that commit).

Not changed: the frozen fixture (`git diff HEAD --stat` is empty), threshold 0.67, strict mode, gate rules, quote verification, model, temperature.

## Results per run and scenario (from the raw records)
| Run | Turns | Done | Error | Gated | model_idk | With sources | Article ok | Memory ok | Goal kept | Extraction failed |
|---|---|---|---|---|---|---|---|---|---|---|
| main A | 12 | 12 | 0 | 4 | 2 | 8 | 4/10 | 1/3 | 0/12 | 0 |
| main B | 12 | 12 | 0 | 4 | 0 | 8 | 7/10 | 1/3 | 11/12 | 0 |
| baseline A | 12 | 12 | 0 | 12 | 0 | 0 | 0/10 | 0/3 | n/a | 0 |
| baseline B | 12 | 12 | 0 | 10 | 0 | 2 | 2/10 | 0/3 | n/a | 0 |

Canonical follow-up "а за повторное?" (turn A02): main run condensed query "Размер штрафа за повторное превышение скорости с камеры при езде 95 км/ч вместо ограничения 60 км/ч по КоАП РФ.", expected article 12.9 retrieved (verdict ok). Baseline: no condensing, gated, expected article not retrieved.

## Results worth noting for 17-10 (recorded as they happened, not fixed)
- Main A goal kept is 0/12: from A02 the extraction rewrote the goal ("... за повторное превышение скорости", later "... за проезд на красный сигнал светофора") although the user did not state a goal change. This is a real finding about the goal-adherence of the stored goal in scenario A; scenario B kept the goal in 11/12 turns.
- Memory ok is 1/3 in both main scenarios (checked turns only).
- Baseline gates 22 of 24 turns at the calibrated threshold, because bare follow-ups have low cosine without condensing.

## Deviations from Plan
- [Rule 1 - Bug] Driver exceeded the agent chat rate limit (fix round 1).
- [Rule 1 - Bug] Extraction reply with echoed memory objects rejected (fix round 2).
- `run_meta.json` is overwritten per driver invocation (only the last run's `runs` entry and timestamps survived). I edited the file by hand after the final runs: `runs` now lists main `TASK_MEMORY_ENABLED=true` and baseline `false`, `started_at`/`finished_at` span all four raw files, and an `effective_config` block records threshold source `calibrated`, effective threshold 0.67, strict, top_k 5, candidate_k 20, history_turns 3, temperature 0, max_tokens 8192, context_length 16384. The driver itself was not changed for this; `scenario_counts` and `git_commit` are driver-written. Per-run flag also appears in each raw file's `config.task_memory_enabled`.
- A temporary attempt to capture the isolated app's log was reverted; the agent subprocess log is not reachable from the driver, so the extraction failure was diagnosed by direct reproduction against LM Studio.

## Not verified
- Judge columns in `answers.csv` are empty (plan 17-10). The browser check is plan 17-11.

## Safety
- Nothing bound to ports 8000/8001 was started, connected to or stopped; `app.db` untouched; `git status --porcelain eval_out/day23` prints nothing.

## Self-Check
- Verify command from Task 3: exit 0 (4 raw files with 12 records each, 8 rendered files present). Commits 77075cc, 6a69ff5, 58f03b7 exist. Fixture diff empty. main_A has task_memory snapshots; baseline_A has none (checked via counts above: baseline Memory ok 0/3, no snapshots).
## Self-Check: PASSED
