---
phase: 13-knowledge-base-indexing-day-21
plan: 08
subsystem: knowledge-base
tags: [e2e, playwright, golden-tests, docs, real-pdf]
requires: [13-06, 13-07]
provides:
  - tests/test_kb_real_pdfs.py (real-PDF golden tests)
  - scripts/e2e_kb_playwright.py (isolated-copy browser E2E)
  - KB sections in docs/API_SPEC.md, ARCHITECTURE.md, USER_GUIDE.md, TESTING_GUIDE.md
key-files:
  created:
    - tests/test_kb_real_pdfs.py
    - scripts/e2e_kb_playwright.py
  modified:
    - agent/kb_loaders.py
    - docs/API_SPEC.md
    - docs/ARCHITECTURE.md
    - docs/USER_GUIDE.md
    - docs/TESTING_GUIDE.md
requirements-completed: [KB-01, KB-02, KB-03, KB-04, KB-05, KB-06, KB-07, KB-08, KB-09, KB-10, KB-11]
completed: 2026-10-03
---

# Phase 13 Plan 08: Real-PDF verification, browser E2E and docs Summary

Both real PDFs (ФЗ-196, КоАП РФ) are proven through load, clean and both chunking strategies in pytest; a Playwright E2E on an isolated copy (UI :18000 / Agent :18001, scratch DB and KB dir) with the real LM Studio passed 20/20 checks; docs describe the KB API, architecture, usage and tests.

## Commits
- 74e54f1: real-PDF golden tests (+ cleaner widening, see deviations)
- d1317ee: docs (API_SPEC, ARCHITECTURE, USER_GUIDE, TESTING_GUIDE)
- d2ffd65: scripts/e2e_kb_playwright.py

## Pinned golden counts (Q3)
- КоАП РФ: 33 `Глава` headings, **907** unique article numbers (research estimate ~906, within tolerance of 30).
- ФЗ-196: **34** unique article numbers (matches the 34 `^Статья` headings).

## Verification (observed)
- `pytest tests/test_kb_real_pdfs.py tests/test_kb_loaders.py tests/test_kb_chunking.py -q` -> 50 passed (real-PDF tests ran, none skipped).
- Full suite `pytest tests/ -q` -> 1313 passed, 1 skipped (the skip is not from this plan).
- `python scripts/e2e_kb_playwright.py` final run -> `summary: 20 passed, 0 failed`, ports 18000/18001 free afterwards, scratch dir removed, user's app on :8001 untouched (health 200 before and after kill of own processes).

### E2E PASS lines (final run)
```
PASS: isolated copy patched to ports 18000/18001
PASS: scratch database and KB storage are isolated from app.db
PASS: isolated app instance is up (UI :18000 + Agent :18001)
PASS: 1 KB panel expands with an empty state
PASS: 2 chat model picker hides the embeddings model (picker had 1 option)
PASS: 3 default model is nomic; giga check fails (D-24); nomic check gives dim 768
PASS: 4 inline validation keeps the modal open (size<100, size>2000, overlap, duplicate file)
PASS: 5 ФЗ-196 fixed reaches «готово»: 1 файл · 135 чанков (5 s)
PASS: 6 ФЗ-196 structural ready (4 s); test search returns 5 cards; top1 0.830, section «Глава IV > Статья 28»; expand toggles
PASS: 7a КоАП fixed ready: 1 файл · 3222 чанка (82 s); live «индексация N из M» seen; 78 /health polls all 200 within 2 s
PASS: 7b VRAM reading with the embedder loaded
PASS: 7c КоАП structural ready: 1 файл · 2332 чанка (71 s); test search 5 cards with sections (top1 0.723, «Раздел I > Глава 3 > Статья 3.5»)
PASS: 8 scan PDF fails with the scan message
PASS: 9 giga-embeddings KB ends in «ошибка» with the D-24 message
PASS: 10 Agent killed mid-job: supervisor restarts it, KB shows «прервана перезапуском»
PASS: 11a delete during indexing removes the row and the directory
PASS: 12 second user sees no KBs, GET other user's KB -> 404
PASS: 11b delete of a ready KB: row gone, toast «База знаний удалена», directory removed
PASS: no browser page errors
PASS: isolated ports are free again
```

### Indexing durations (nomic, this machine)
| KB | Strategy | Chunks | Time |
|----|----------|--------|------|
| ФЗ-196 | fixed 1000/150 | 135 | 5 s |
| ФЗ-196 | structural | n/a | 4 s |
| КоАП | fixed 1000/150 | 3222 | 82 s |
| КоАП | structural | 2332 | 71 s |

### D-18 item (c): VRAM with embedder plus chat model co-loaded
Reading taken during the КоАП fixed indexing: `nvidia-smi 14964 MiB used / 16303 MiB total`; loaded in LM Studio: giga-embeddings-instruct-480m-0826, text-embedding-nomic-embed-text-v1.5, qwen/qwen3.5-9b. So embedder plus a 9B chat model fit together, with about 1.3 GiB headroom; note giga was also resident (preloaded by the user), so a configuration without it would have more room.

## Deviations from Plan

**1. [Rule 1 - Bug] PDF annotation cleaner missed real-corpus forms**
- Found during: Task 1. The real PDFs kept `(п. N в ред. Федерального закона ...)` (ФЗ-196) and `(Примечание ... - См. предыдущую редакцию)` and very long edition lists (КоАП).
- Fix: widened `_ANNOTATION_RE` in agent/kb_loaders.py (added `Примечание`, `п. N в ред.`, bound 400 -> 1200). Residue fell from 17 to 3 (ФЗ-196) and from 181 to 49 (КоАП).
- Remaining residue (annotations with nested parentheses or edition lists beyond the bound) is tolerated by explicit ceilings in the test (КоАП <= 60, ФЗ <= 5); page furniture (`Страница N`, `ИС «Техэксперт`, `КонсультантПлюс: примечание.`) is asserted to be exactly 0. Not a blocker; flagged for a possible future cleaner pass.
- Commit: 74e54f1

**2. [Rule 1 - Script bugs, fixed during E2E runs]** The first E2E run failed on its own selectors/timing (race with the async embedding-model list rebuilding the picker, expand toggle relabelled after click); both fixed in the script and the run repeated until 20/20.

## Assumption Drift (advisory)
- Planned: select for the embedding model keeps its default when the modal opens. Actual: on opening the create modal the select briefly shows the first option (reset by `form.reset()`, here giga) until the model list arrives, so a very fast submit can pick a non-embedding model (the first E2E run did, and the KB correctly failed with the D-24 message). Not changed (server guard handles it); the E2E waits for the default to settle.
- Retrieval note: the КоАП structural query «Какой штраф за превышение скорости?» returned Статья 3.5 (general fine definition) as top-1 rather than Статья 12.9; retrieval quality tuning is outside this plan.

## Not verified / limits
- Scenario 2 only proves the embeddings model is absent from a picker that contained a single option (only one chat model available to the copy).
- No real chat completions were exercised in the E2E (not part of this plan).

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
