---
phase: 14-first-rag-query-day-22
plan: 08
subsystem: e2e-and-docs
tags: [rag, playwright, docs]
requires: [14-06]
provides: [rag-browser-e2e, rag-docs]
key-files:
  created: [scripts/e2e_rag_playwright.py]
  modified: [docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md, docs/USER_GUIDE.md]
requirements-completed: [RAG-01, RAG-02, RAG-03, RAG-04, RAG-05]
metrics:
  completed: 2026-10-03
---

# Phase 14 Plan 08: RAG browser E2E and docs Summary

Headless-Chromium E2E of chat RAG on an isolated copy (UI :18000 / Agent :18001, scratch DB and KB storage) against the real LM Studio, plus docs sync. ROADMAP SC1-SC3 proven in a browser.

## Tasks

1. `scripts/e2e_rag_playwright.py` (commit eabd79e). Reuses the copy/seed/login/teardown helpers of `e2e_kb_playwright.py` by import, own preflight (nomic embedder + loaded chat model), own KB creation that pins the nomic embedder.
2. Docs (commit see git log, `docs(14-08)`): API_SPEC (routes, `rag_sources`, `done.rag`, seven warning codes), ARCHITECTURE (pre-step, budget, D-12 ordering note, ChatRagConfig, delete-path argument, dim guard), TESTING_GUIDE (8 test files + E2E), USER_GUIDE (Russian user flow, `rag_eval.py`).

## Verification (observed)

`python scripts/e2e_rag_playwright.py` exit 0, chat model `qwen/qwen3.5-9b`, 12 PASS / 0 FAIL:

- PASS: isolated copy patched to ports 18000/18001
- PASS: scratch database and KB storage are isolated from app.db
- PASS: isolated app instance is up (UI :18000 + Agent :18001)
- PASS: 1 ФЗ-196 structural KB reaches «готово» (1 файл · 92 чанка, nomic, 4s)
- PASS: 2 new chat: RAG switch disabled and badge «без RAG»
- PASS: 3 attach KB, switch to «с RAG», K=5, badge «RAG: fz196-rag»; state identical after reload
- PASS: 4 RAG answer: «с RAG · K=5», «Источники (5)», snippet loaded lazily (Глава IV > Статья 26...), stored user message equals the raw question, no «=== Фрагменты» in history
- PASS: 5 «без RAG» answer labelled, no new sources block (1 -> 1)
- PASS: 6 KB deleted: answer arrived, warning «⚠ База знаний удалена...» (role=status), toasts «База знаний удалена» and «Поиск по базе знаний не удался — ответ дан без RAG», label «без RAG (сбой поиска)», warning persists after reload
- PASS: 7 no console errors (snippet 404s excepted)
- PASS: no browser page errors
- PASS: isolated ports are free again

`pytest tests/ -q`: 1469 passed, 1 skipped. Docs acceptance greps pass (`/rag`, `done.rag`, `context_full` in API_SPEC; ChatRagConfig and D-12 in ARCHITECTURE).

Not verified: the evaluation script `rag_eval.py` itself was not rerun (covered by 14-07).

## Deviations from Plan

- [Rule 3 - Blocking] The KB modal's default embedder is the first eligible loaded model; LM Studio now has `text-embedding-bge-m3` loaded alongside nomic, so `kb.open_modal` (which waits for nomic to be the default) timed out. The new script pins nomic explicitly via its own `create_kb_with_nomic`. No product change.
- [Info] The E2E reuses helpers from `e2e_kb_playwright.py` by import instead of copying them, so the script is shorter than a full copy. Scenario 4's «Статья 19» header check is satisfied through the file-name alternative (`196`): the top result for the age question is Статья 26, which is the correct article for the question.

## Deferred Issues

`scripts/e2e_kb_playwright.py` (Phase 13) still assumes nomic is the modal default (`open_modal` wait) and will time out while bge-m3 is also loaded. Pre-existing and out of scope; not fixed here.

## Known Stubs

None.

## Cleanup

Scratch copy removed by the script on success; ports 18000/18001 verified free; the user's app on 8000/8001 and `app.db` untouched.

## Self-Check: PASSED
