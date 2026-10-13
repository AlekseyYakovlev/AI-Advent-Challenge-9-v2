---
phase: 21-project-docs-in-rag-day-31
plan: 01
subsystem: knowledge-base
tags: [kb, sqlite-migration, upload, source-names]
requires: []
provides:
  - "KbDocument identity by source name (relative path) instead of content hash"
  - "migrate_kbdocument_drop_sha_unique start-up migration"
  - "agent.kb_limits.MAX_FILES=200, MAX_SOURCE_NAME_CHARS=300, PHASE_ADDING"
  - "agent.kb_api._clean_source_name(s), _store_all(files, names, target)"
  - "GET /api/v1/kb/limits"
affects: [21-03, 21-04, 21-05]
tech-stack:
  added: []
  patterns: ["AUTOCOMMIT table rebuild with foreign_keys=OFF", "uuid4 stored file names"]
key-files:
  created: [tests/test_kb_migration.py]
  modified: [agent/kb_limits.py, shared/models.py, shared/database.py, agent/kb_api.py, tests/test_kb_models.py, tests/test_kb_api.py]
decisions:
  - "(kb_id, filename) uniqueness is enforced in code only; table has a non-unique index ix_kbdocument_kb_filename"
  - "Source names compared exactly and case-sensitively after NFC normalisation"
  - "sha256 stays as a column for change detection only"
metrics:
  tasks: 2
  completed: 2026-10-13
---

# Phase 21 Plan 01: Source-name identity for KB documents Summary

Knowledge-base documents are now identified by their sanitised relative source name rather than content hash, with a chunk-preserving start-up migration, 200-file limit and a `GET /api/v1/kb/limits` endpoint.

## Commits
- a648424: schema change, migration, constants, migration tests
- 80e8d02: source-name sanitiser, uuid storage, limits endpoint, API tests

## Verification (observed)
- `pytest tests/test_kb_migration.py tests/test_kb_models.py tests/test_database.py`: 25 passed
- `pytest tests/test_kb_api.py tests/test_kb_scoping.py tests/test_kb_indexer.py tests/test_kb_search.py`: 81 passed
- `pytest tests/ -k "kb or rag or database"`: 822 passed, 10 skipped
- Acceptance greps: no `_clean_filenames` / `sha in seen`; `/limits` route declared before `/{kb_id}`.

## Deviations from Plan

**1. [Rule 1 - Test adjustment] Empty file name case**
- **Found during:** Task 2
- **Issue:** A multipart part with an empty filename is parsed by Starlette as a text field, so FastAPI answers 422 with its own validation list before the handler runs; `MSG_BAD_EXTENSION` cannot be produced.
- **Fix:** `test_missing_extension_rejected[""]` asserts 422 and that nothing is left, without checking the message.
- **Commit:** 80e8d02

## Assumption Drift (advisory)
- Planned: empty file name yields `MSG_BAD_EXTENSION`; actual: framework-level 422 (see above).

## Known Stubs
None.

## Self-Check: PASSED
