---
phase: 15-reranking-and-filtering-day-23
plan: 09
subsystem: rag
tags: [rag, calibration, threshold, fts, hybrid]
requires:
  - phase: 15-08
    provides: eval_out/day23/calibration.json (measured distributions, FTS probe)
provides:
  - CALIBRATED_THRESHOLDS = {"bge-m3": 0.67}
  - keyword-gated FTS exemption (D-08 amended)
affects: [phase-16]
key-files:
  modified:
    - agent/rag.py
    - agent/rag_pipeline.py
    - tests/test_rag.py
    - tests/test_rag_pipeline.py
key-decisions:
  - "Task 1 = option-b: threshold stored only for separable embedders"
  - "Task 2 = option-b: D-08 amended, FTS exemption requires keyword match"
requirements-completed: [RANK-07, RANK-04]
completed: 2026-10-03
---

# Phase 15 Plan 09: Calibrated thresholds and FTS exemption Summary

bge-m3 gets a measured default cosine cut of 0.67; nomic (not separable) gets no cut; the hybrid FTS exemption now requires an article-number or lexical match.

## Decisions (human checkpoints, answered by the user on 2026-10-03)

**Task 1 - option-b** (rule-derived value only for separable embedders). Mapping:
- `bge-m3` -> 0.67 (separable, midpoint rule)
- `nomic` -> NO entry (not separable; the rule-derived value 0.79 was rejected; resolves to 0.0 / source "none")

**Task 2 - option-b** (amend D-08): with hybrid on, an FTS hit below the threshold survives only when it matches an article number from the question or has lexical overlap >= 0.5 (`FTS_EXEMPT_MIN_LEXICAL = 0.5`).
D-08 amendment record: D-08 amended at the 15-09 checkpoint (user decision, 2026-10-03); not written into CONTEXT.md.

Probe numbers shown to the user: bge-m3 and nomic both: 8/8 out-of-corpus calibration questions got an FTS-exempt chunk with hybrid on; nomic: 8 gold chunks rescued only by the exemption (C01,C02,C04,C05,C06,C07,C10,C12); bge-m3: none. Control set at threshold: bge-m3 0.67 keeps gold for 5/8 answerable, cuts both OOC; nomic 0.79 keeps gold for 3/8.

## Final CALIBRATED_THRESHOLDS

```python
CALIBRATED_THRESHOLDS: dict[str, float] = {"bge-m3": 0.67}
```
The comment above it names nomic as intentionally absent (not separable) and the rejected 0.79.

## Task commits

- Task 3: e172c25 (constants, keyword-gated exemption, tests). Tasks 1 and 2 were decision-only (no code).

## What changed

- `agent/rag.py`: constants and comment only.
- `agent/rag_pipeline.py`: `FTS_EXEMPT_MIN_LEXICAL`, `_earns_fts_exemption`; `_apply_threshold` takes the question. Candidates at or above the threshold are unaffected; a failing FTS hit gets `below_threshold`, `fts_exempt` False, and keeps `fts_rank`.
- `tests/test_rag.py`: `test_shipped_thresholds_match_calibration_report` (equality for separable labels, absence for non-separable, via calibration.json); existing range test covers 0..1.
- `tests/test_rag_pipeline.py`: `test_fts_exempt_requires_keyword_cuts_weak_overlap` (out-of-corpus-style question yields verdict below_threshold with hybrid on) and `test_fts_exempt_requires_keyword_passes_on_article_number`. The existing `test_fts_exempt_survives_threshold` still passes.

## Verification (observed)

- `python -m pytest tests/ -q --ignore=tests/test_kb_real_pdfs.py --deselect tests/test_supervisor.py::test_agent_restarts_within_5_seconds`: 1703 passed, 1 skipped, 1 failed (see below). The known supervisor test was deselected, not run.
- Acceptance one-liners (constants valid, unknown model -> (0.0, "none"), bge-m3 resolves to 0.67, nomic resolves to None): passed.
- No existing test broke from the non-empty constants; none needed edits.

## Deviations / Deferred Issues

None to the plan. Out-of-scope pre-existing failure, not fixed: `tests/test_rag_eval.py::test_calibrate_refuses_draft_fixture` fails because the calibration fixture was frozen in 15-08 (commit bf42218), so `calibrate` no longer exits 2 on a draft fixture and proceeds to preflight ("knowledge base bge=2 does not exist"). It does not involve CALIBRATED_THRESHOLDS or the pipeline. Fix would be to make the test build an explicit draft fixture.

## Self-Check: PASSED

Commit e172c25 exists; modified files present.
