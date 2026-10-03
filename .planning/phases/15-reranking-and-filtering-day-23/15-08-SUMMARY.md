---
phase: 15-reranking-and-filtering-day-23
plan: 08
subsystem: rag-evaluation
tags: [rag, calibration, threshold, nomic, bge-m3]
requires:
  - phase: 15-06
    provides: calibrate subcommand and draft calibration fixture
provides:
  - frozen calibration fixture
  - measured per-embedder score distributions and thresholds (eval_out/day23/calibration.json, .md)
affects: [15-09, 15-10]
key-files:
  created:
    - eval_out/day23/calibration.json
    - eval_out/day23/calibration.md
  modified:
    - tests/fixtures/rag/calibration_set.json
key-decisions:
  - "Rule-derived thresholds: nomic 0.79 (not separable), bge-m3 0.67 (separable)"
requirements-completed: [RANK-07]
duration: 10min
completed: 2026-10-03
---

# Phase 15 Plan 08: Live Calibration Summary

The calibration set was frozen after user approval, then calibrated live against LM Studio for both embedders: bge-m3 is separable (threshold 0.67), nomic is not (0.79, Youden fallback).

## Task 1 (checkpoint) - user approval

User answered "Approved" to the full 20-question draft (12 answerable C01-C12, 8 out-of-corpus C13-C20), no corrections.

## Task 2 - freeze and run

- Freeze commit bf42218 (status frozen, frozen_at 2026-10-03; fixture test 7 passed) made BEFORE calibrate.
- Scratch copy: eval_out/day22/eval.db(+wal/shm) and eval_kb copied to eval_out/day23/; KB 1 (nomic) and KB 2 (bge-m3) both `ready`. Day 22 and app.db untouched (`git diff --stat` empty).
- LM Studio was reachable; both embedding models answered by model field (no one-at-a-time runs needed). Single command `calibrate --kb nomic=1 --kb bge=2` exited 0.
- Commit 60243d7: calibration.json and calibration.md. Scratch DB and eval_kb stay untracked.

## Results (candidate_k 20)

fixture_sha256: calibration 9435112f...f3407, control 8c5b76a0...6ebe2 (calibration hash matches the frozen file; control matches Day 22 run_meta).

| | nomic (768d) | bge-m3 (1024d) |
|---|---|---|
| gold scores (sorted) | 0.776, 0.786, 0.788, 0.805, 0.812, 0.853 (6 of 12) | 0.674, 0.675, 0.682, 0.686, 0.693, 0.701, 0.716, 0.721, 0.745, 0.762, 0.777, 0.779 (12 of 12) |
| gold min/median/max | 0.776 / 0.797 / 0.853 | 0.674 / 0.708 / 0.779 |
| gold_missing | C02, C04, C06, C08, C10, C12 | none |
| OOC top-1 (sorted) | 0.749, 0.762, 0.765, 0.769, 0.777, 0.780, 0.787, 0.797 | 0.501, 0.513, 0.515, 0.540, 0.552, 0.591, 0.627, 0.656 |
| OOC min/median/max | 0.749 / 0.773 / 0.797 | 0.501 / 0.546 / 0.656 |
| threshold | 0.79 (youden) | 0.67 (midpoint) |
| separable | false | true (gap 0.656 to 0.674) |

Control set at the threshold (used only to show behaviour, not to choose it):
- nomic 0.79: gold is still in the survivors for Q02, Q03, Q08 (3 of the 6 questions that have a gold chunk in the candidates; Q05 and Q07 have no gold recorded, so they are not counted). Q01, Q04, Q06 lose everything (0 survivors). Both out-of-corpus questions Q09, Q10 are fully cut (0 survivors).
- bge-m3 0.67: gold survives for Q02, Q03, Q04, Q05, Q06 (5 of 8 answerable); Q01 (0.639), Q07 (0.653), Q08 (0.634) are cut. Both out-of-corpus questions Q09 (0.540), Q10 (0.517) are fully cut.

FTS probe (hybrid on): out-of-corpus calibration questions with an FTS-exempt candidate: 8/8 for both embedders (C13-C20). This means FTS exemption defeats the threshold's out-of-corpus refusal on every OOC question; input for the D-08 decision in 15-09. Gold rescued only by the exemption: nomic C01, C02, C04, C05, C06, C07, C10, C12; bge-m3 none.

Comparison with RESEARCH preliminary table: agrees with expectation (bge-m3 separable, nomic not). The measured bge-m3 gap is 0.656 to 0.674, a little above the expected 0.54-0.63 region; the midpoint 0.67 is tight (OOC max 0.656, gold min 0.674, and three control gold chunks sit just below 0.67). Reported as a finding for 15-09.

## Deviations from Plan

None - plan executed as written. The user's CLAUDE.md forbids a Claude Co-Authored-By line, so none was added to commits.

## Verification (observed)

- `python -m pytest tests/test_rag_calibration_fixture.py -q`: 7 passed (before freeze commit).
- calibrate exit 0; calibration.json has both labels, 10 control_check rows each, fts_probe each; nomic 6 gold + 6 missing = 12, bge 12 gold, 8 OOC each. Fixture hash recomputed and equal to the one in calibration.md.
- Not separately re-run after the run: tests/test_rag_eval.py (no code changed in this plan).

## Known Stubs

None.

## Self-Check: PASSED
