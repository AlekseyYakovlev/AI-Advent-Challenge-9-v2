---
phase: 15-reranking-and-filtering-day-23
plan: 12
subsystem: e2e-and-docs
tags: [rag, playwright, e2e, docs]
requires:
  - phase: 15-07
    provides: search popover, details block, grey below-threshold line
  - phase: 15-09
    provides: calibrated thresholds and the amended FTS exemption
  - phase: 15-11
    provides: Day 23 eval artifacts and report
provides:
  - scripts/e2e_rag_search_playwright.py (browser E2E on the isolated copy 18000/18001)
  - docs synced with two-stage retrieval, settings API, payload v2, tests, eval commands
requirements-completed: [RANK-01, RANK-02, RANK-03, RANK-04, RANK-05, RANK-06]
status: complete
completed: 2026-10-03
---

# Phase 15 Plan 12: Browser E2E and docs sync Summary

A Playwright script drives the Phase 15 search UI in a real browser on an isolated copy of the app, and the four docs now describe the two-stage retrieval as built. The E2E passed 17 of 17 checks (S1-S11 plus setup and isolation checks) with exit code 0.

## Task 1: browser E2E (commit 471a6ad)

`scripts/e2e_rag_search_playwright.py` reuses the copy/patch/seed/login/teardown helpers of `e2e_kb_playwright.py` and the chat helpers of `e2e_rag_playwright.py`. It starts the copy on UI :18000 / Agent :18001 with a scratch database and scratch KB storage and stops only the processes it started. Nothing on 8000/8001 was touched, and app.db was not used. The ports were free before and after the run.

Run environment: LM Studio was up. Chat model `qwen/qwen3.5-9b`, embedder nomic (KB: ФЗ-196, structural, 92 chunks).

Final run, exit 0, `summary: 17 passed, 0 failed`:

| Scenario | Result | Observed |
|----------|--------|----------|
| setup | PASS | KB «готово», 92 chunks |
| S1 button only with RAG on | PASS | hidden with RAG off, visible with RAG on, text «Поиск ⚙» |
| S2 popover defaults | PASS | role dialog, aria-label «Настройки поиска», K=20, four switches off, threshold 0.00 with note «(нет калибровки)» matching the API (nomic has no calibration) |
| S3 Escape and outside click | PASS | Escape closes and focus returns to the button; outside click closes |
| S4 K and lexical persist | PASS | API: candidate_k 30, lexical true, other three false; indigo border; after reload 30 and lexical checked |
| S5 threshold override and reset | PASS | 0.99 gives threshold_source "user" and «сбросить»; reset gives threshold null and the «(нет калибровки)» note |
| S6 details block | PASS | collapsed «Детали поиска»; headers было→стало, cos, lex, Источник, Статус; 5 chips «✓ в ответе» plus 25 «вне top-K»; no yellow classes; no table cell over 120 chars |
| S7 reload | PASS | 30 rows before and after |
| S8 hybrid | PASS | «FTS» column; stages line «порог 0.00 · лексич. · FTS5 · 43→5 · 458 мс» |
| S9 threshold 0.99 | PASS | grey line «Фрагменты не прошли порог (лучший 0.80 < 0.99)», no new «Источники» block, all rows «ниже порога», no toast, no yellow warning, grey line still present after reload |
| S10 rewrite + LLM rerank | PASS | LLM rerank ran; rewrite ran (rewritten query shown in «Переписан:»); no toast, no yellow warning. No stage was skipped in this run |
| S11 RAG off | PASS | button and popover hidden |
| no browser page errors | PASS | |
| isolated ports free again | PASS | |

Test suite after the script was committed: `python -m pytest tests/ -q --ignore=tests/test_kb_real_pdfs.py` gave 1717 passed, 1 skipped.

## Task 2: docs sync (commit 9401785)

- `docs/ARCHITECTURE.md`: new "Two-stage retrieval (Day 23)" section covering stage order, module map, the `kb_chunk_fts` mirror with its two triggers and startup backfill, `CALIBRATED_THRESHOLDS = {"bge-m3": 0.67}` with the NULL-means-calibrated rule and the nomic note (no entry, not separable, resolves to 0), the amended FTS exemption (article-number match or lexical overlap >= 0.5), fail-soft skip reason codes, verdicts, and payload v2.
- `docs/API_SPEC.md`: GET/PUT `/chats/{id}/rag` rows extended; a "Search settings" subsection (field table, null reset, clamp, 422 cases, response fields); a "Payload v2" subsection with the field table.
- `docs/TESTING_GUIDE.md`: Day 23 test files and required scenarios, the eval commands (build-kbs, calibrate, ablate, rag_judge.py, frozen fixture rule, never app.db), and the new E2E script.
- `docs/USER_GUIDE.md`: «Настройки поиска (Поиск ⚙)» section with UI strings copied from the implemented popover, how to read «Детали поиска», and the grey threshold line.
- The docs verification one-liner exited 0. The threshold value in ARCHITECTURE.md equals `agent/rag.py::CALIBRATED_THRESHOLDS`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] E2E selectors written against the wrong DOM shape**
- **Found during:** Task 1, first two runs
- **Issue:** The stage switches are visually hidden (`sr-only`) inputs, so `check()` timed out on an intercepting wrapper. The details block and the sources block are rendered as siblings after the message row in `#messages`, not inside it.
- **Fix:** The script flips switches through their visible label and locates `#messages details.rag-details` directly. This was a script fix only. No product code was changed, so no product bug was found.
- **Files modified:** scripts/e2e_rag_search_playwright.py
- **Commit:** 471a6ad

## Known Stubs

None.

## Limitations

- S10 observed only the "stage ran" outcome for both rewrite and LLM rerank. The skip path («↷» line) was not exercised in the browser; it is covered by the unit tests of plan 15-05/15-06.
- Thresholds in S2, S5 and S9 were checked for nomic only (no calibration). A bge-m3 KB showing «(калибр.)» 0.67 was not driven in the browser.
- docs/TESTING_GUIDE.md and the other docs were checked by the string gate and by reading the implemented code, not by a link or markdown linter.

## Self-Check: PASSED

- scripts/e2e_rag_search_playwright.py exists; commits 471a6ad and 9401785 exist.
- STATE.md and ROADMAP.md were not modified (the orchestrator owns them).
