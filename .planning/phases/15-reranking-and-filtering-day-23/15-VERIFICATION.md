---
phase: 15-reranking-and-filtering-day-23
verified: 2026-10-03T21:00:00Z
status: passed
score: 4/4 roadmap truths verified (9/9 requirements satisfied)
has_blocking_gaps: false
overrides_applied: 0
---

# Phase 15: Reranking and filtering (Day 23) Verification Report

**Phase Goal:** retrieval quality improves through two-stage candidate selection, a calibrated relevance cut-off, optional rerankers and query rewrite, with every step inspectable
**Status:** passed
**Re-verification:** No, initial verification

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Candidate top-K, final top-K and threshold are configurable per chat; low-scoring chunks are cut before the LLM | VERIFIED | `ChatRagConfig` has candidate_k, threshold, top_k and the four flags (`shared/models.py` 738-743). `agent/rag_api.py` has a partial-update API (candidate_k 1-50, threshold 0-1, explicit null resets). `_apply_threshold` in `agent/rag_pipeline.py` cuts on raw cosine. `rag_turn.py` calls `config_from_row` then `run_retrieval_pipeline`. Below-threshold chunks never reach `build_rag_block`; when all are cut the verdict is `below_threshold` and a no-fragments note is merged. The popover (`rag-search-popover`) has the candidate-K and threshold inputs. |
| 2 | Lexical, LLM rerank, hybrid FTS5 and rewrite are independent toggles; rewrite falls back on bad output | VERIFIED | Four independent booleans in config, API and popover. Pipeline stages are separate and fail-soft (`run.skip`). `validate_rewrite` returns `bad_output` or `unchanged`, and `_rewrite_stage` then keeps the original query (rewritten=None). FTS5 mirror, triggers and backfill are in `shared/database.py::ensure_kb_chunk_fts`. `rrf_order` is used in `_hybrid_stage`. |
| 3 | A collapsible "Детали поиска" block under each RAG answer shows the (rewritten) query, candidates with scores, cut reasons and final chunks | VERIFIED | `buildRagDetailsBlock` in `ui/static/app.js` renders a `<details>` with "Детали поиска". It shows the query, the rewritten query, stages, skipped stages, and a candidates table (cos, lex, fts_rank, llm, status). It is wired at app.js:206. The backend trace is built in `run_retrieval_pipeline`, sent through `_payload(search=trace)` and `mark_over_budget`. `scripts/e2e_rag_search_playwright.py` exists, and browser E2E passed 17/17 per the caller. |
| 4 | Threshold calibrated per embedding model on the control set; `Day23_report.md` compares configs; optional LLM-judge column, manual verdict primary | VERIFIED | `CALIBRATED_THRESHOLDS = {"bge-m3": 0.67}` in `agent/rag.py:81` (nomic intentionally absent, user-approved). `resolve_threshold` is used by the API and the pipeline. `eval_out/day23/` has calibration.md/json, ablation.md, answers.md/csv and judge_meta.json (70 rows, deepseek-flash, 61/70 agreement). `Day23_report.md` (191 lines) has sections for calibration, 7-config comparison, threshold, rerankers, hybrid, rewrite, judge and limitations. Manual verdicts are primary and the judge is a separate column. `scripts/rag_judge.py` exists. |

**Score:** 4/4

## Requirements Coverage

All nine IDs appear in PLAN frontmatter (RANK-01..09) and in REQUIREMENTS.md. No orphans.

| Req | Status | Evidence |
|-----|--------|----------|
| RANK-01 | SATISFIED | Two-stage retrieval; per-chat K values and threshold in the model, API and UI. |
| RANK-02 | SATISFIED | `lexical_score`, `lexical_rerank`, `article_numbers`; `_lexical_stage` fuses with cosine. |
| RANK-03 | SATISFIED | `rag_llm.llm_rerank`, a batched prompt over `RERANK_TOP_N`; `parse_rerank_scores`. |
| RANK-04 | SATISFIED | `rag_fts.fts_search`, FTS5 table, `rrf_order`. |
| RANK-05 | SATISFIED | `rewrite_query` plus `validate_rewrite` with fallback. |
| RANK-06 | SATISFIED | `buildRagDetailsBlock`, `buildRagThresholdLine`. |
| RANK-07 | SATISFIED | `choose_threshold`, `rag_eval.py calibrate`, calibration.md; bge-m3 0.67 stored; nomic documented as non-separable. |
| RANK-08 | SATISFIED | `Day23_report.md` plus a structure test (`test_rag_report_day23.py`). |
| RANK-09 | SATISFIED | `scripts/rag_judge.py`, judge column in answers.csv. |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase-15 test modules (rank, pipeline, fts, judge, report, static, calibration fixture, api) | `pytest` on 8 files | 216 passed | PASS |
| Full suite | reported by the caller | 1716 passed | PASS (not re-run) |

## Anti-Patterns

No TBD, FIXME or XXX markers in the phase files or `Day23_report.md`. The five warnings in 15-REVIEW.md are advisory and none blocks a must-have:

- WR-01: FTS exemption uses a raw substring match for article numbers, which is a small precision weakness in the amended D-08.
- WR-02: the `"ответ"` prefix in `CHATTY_PREFIXES` over-rejects some rewrites.
- WR-03: the judge loop does not catch `ValueError`.
- WR-04: the FTS5 migration is unguarded at startup.
- WR-05: there is no overall deadline on the optional stages.

## Notes (non-blocking)

- REQUIREMENTS.md still shows RANK-01..09 as `[ ]` and "Pending" in the traceability table, although ROADMAP.md marks Phase 15 complete. This is a tracking update for the orchestrator. It is not a code gap.
- The user decisions at 15-09 are honored as accepted: the bge-m3-only calibrated threshold and the amended D-08 FTS exemption (article number or lexical overlap >= 0.5).
- The report is candid that no stage beat the baseline on the 10 control questions (hit@5 0.88 baseline against 0.62-0.75). The phase goal is about building and inspecting the mechanism and measuring it, and that is delivered.

## Human Verification Required

None outstanding. Browser E2E (17/17) was already done on an isolated copy.

## Gaps Summary

No gaps. The phase goal is achieved.

---

_Verified: 2026-10-03_
_Verifier: Claude (gsd-verifier)_
