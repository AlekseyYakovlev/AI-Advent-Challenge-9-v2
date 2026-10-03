---
phase: 13-knowledge-base-indexing-day-21
plan: 06
subsystem: knowledge-base
tags: [rest, multipart, faiss, search, scoping]
requires: [13-05]
provides:
  - agent/kb_search.py (search_kb, load_index_cached, KbNotReadyError, KbIndexCorruptError, MSG_NOT_READY, MSG_INDEX_CORRUPT)
  - agent/kb_api.py (APIRouter /api/v1/kb: create 202, list, get, delete, search, embedding-models, embedding-check)
affects: [13-07, 13-08]
key-files:
  created:
    - agent/kb_search.py
    - agent/kb_api.py
    - tests/test_kb_search.py
    - tests/test_kb_api.py
    - tests/test_kb_scoping.py
  modified:
    - agent/main.py
requirements-completed: [KB-01, KB-02, KB-04, KB-06, KB-09, KB-10, KB-11]
completed: 2026-10-03
---

# Phase 13 Plan 06: KB REST API and search Summary

User-scoped REST contract for knowledge bases: streamed multipart create (202, server-side validation with Russian errors, SHA-256 dedupe, size caps), list/get/delete, test search over a per-KB cached FAISS index, and embedding model list/check.

## Tasks

| Task | Commit |
| ---- | ------ |
| 1: search service + tests | d6384cb |
| 2: REST router + registration in agent/main.py | 4a251d8 |
| 3: API/scoping tests (+ fix found by tests) | 6fe2feb |

## Verification (observed)

- `pytest tests/test_kb_search.py -q` -> 7 passed.
- `pytest tests/test_kb_api.py tests/test_kb_scoping.py tests/test_kb_search.py -q` -> 49 passed.
- Route check: `app.openapi()['paths']` contains all five KB paths (see deviation 2).
- Full suite `pytest tests/ -q` -> 1303 passed, 1 skipped.

## Deviations from Plan

**1. [Rule 1 - Bug] Expired ORM attribute after rollback**
- Found by the empty-file test: after `session.rollback()` the shared-session `current_user.id` is expired and lazily loads, raising MissingGreenlet. create_kb now captures `user_id` up front. Commit 6fe2feb.

**2. [Rule 3 - Blocking] Verify command**
- The plan's `app.routes`/`.path` check fails on this FastAPI version (`_IncludedRouter` has no `path`). Verified through `app.openapi()['paths']` instead.

**3. 413 uses a literal status code** instead of a Starlette constant (the constant name differs across versions).

## Notes
- Delete mid-job: the cancelled job may publish a `failed` ("Индексация прервана.") frame just before `kb_deleted`; the REST delete path is unchanged and a test (`test_delete_mid_job_cancels_and_cleans`) confirms the delete returns 204, rows/files vanish and `kb_jobs` empties. The UI (plan 07) must tolerate that frame.
- Search maps a non-ready KB and a corrupt index both to 409; LM Studio down to 503, other embedding errors to 422.
- Create flushes the KB row first to obtain the id, then streams files; any rule violation rolls back and removes the KB directory (tests assert no rows or files remain).
- Missing/empty search query after strip returns 422 "Введите поисковый запрос.".

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
