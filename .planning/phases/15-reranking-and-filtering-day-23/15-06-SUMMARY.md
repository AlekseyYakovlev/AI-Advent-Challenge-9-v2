---
phase: 15-reranking-and-filtering-day-23
plan: 06
subsystem: rag-evaluation
tags: [rag, eval, calibration, ablation, threshold]
requires:
  - phase: 15-05
    provides: run_retrieval_pipeline, PipelineConfig, mark_over_budget
provides:
  - draft calibration fixture (20 questions)
  - "scripts/rag_eval.py calibrate and ablate subcommands"
affects: [15-08, 15-09, 15-10, 15-13]
key-files:
  created:
    - tests/fixtures/rag/calibration_set.json
    - tests/test_rag_calibration_fixture.py
  modified:
    - scripts/rag_eval.py
    - tests/test_rag_eval.py
key-decisions:
  - "ablate defaults to max_tokens 4096 (Day 22 value) and records incomplete answers per run"
  - "baseline run = plain top-5 with no threshold (candidate_k 5); every other run uses the threshold"
requirements-completed: [RANK-07, RANK-08]
duration: 35min
completed: 2026-10-03
---

# Phase 15 Plan 06: Calibration and Ablation Tooling Summary

Calibration fixture (draft) plus `calibrate` and `ablate` subcommands of `scripts/rag_eval.py`, all offline-testable; live runs belong to plans 15-08 and 15-10.

## Tasks

1. **Calibration fixture and calibrate** (472b853): `tests/fixtures/rag/calibration_set.json` (status draft, C01..C20, 12 answerable / 8 out-of-corpus, disjoint from the control set), shape test, `compute_calibration`, `control_check`, `summarize_fts_probe`, `render_calibration_md`, `calibrate_command`. calibrate exits 2 on a non-frozen fixture before any network call.
2. **ablate** (eafaa0c): `ABLATION_RUNS` (7 keys in D-17 order), `ablation_config`, `count_skips`, `count_incomplete`, `render_ablation_md`, answers.md/csv renderers with carry-over of filled verdict/comment/judge cells, `run_ablation`, `ablate_command`. `DEFAULT_ABLATE_MAX_TOKENS = 4096`; the `run` subcommand keeps 1024.

## Verification (observed)

- `python -m pytest tests/test_rag_calibration_fixture.py tests/test_rag_eval.py tests/test_rag_fixture.py -q`: 39 passed after task 1; `tests/test_rag_eval.py` 29 passed after task 2.
- `calibrate --kb bge=2` on the draft fixture: exit 2, "not frozen" message. `calibrate --help` and `ablate --help` list the required options; `run --help` and top-level `--help` list build-kbs, run, calibrate, ablate.
- Fixture article verification: read-only sqlite3 query against eval_out/day22/eval.db (KB 2, kbchunk.section) confirmed every expected article exists (ФЗ-196: 5, 24, 25, 28, 30, 31; КоАП: 12.1, 12.2, 12.3, 12.12, 12.18, 12.26) and the fine amounts quoted in expected answers were read from the chunk text.
- Not run: live calibrate/ablate (needs LM Studio and a frozen fixture); the FTS probe and the real pipeline path were exercised only through fakes/monkeypatch.

## Deviations from Plan

- [Rule 1 - consistency] Expected sources for КоАП use `file_contains: "N_195-FZ"` (the value the control set uses) instead of `"Kodex"` from the plan text; both are substrings of the stored filename, and disjointness is by article pair.
- Commit message footer: the user's CLAUDE.md forbids a Claude Co-Authored-By line, which overrides the harness attribution reminder; none was added.
- run_command's provider preflight was not refactored; ablate/calibrate use a small shared `_lm_studio_reachable` helper plus a copy of the DeepSeek key check, to leave the Day 22 command untouched.

## Known Stubs

None. `status: draft` on the calibration fixture is intentional until plan 15-08 freezes it.

## Self-Check: PASSED
