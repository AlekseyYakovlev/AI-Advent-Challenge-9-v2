---
phase: 13-knowledge-base-indexing-day-21
plan: 01
subsystem: database
tags: [faiss, pymupdf, sqlmodel, rag, storage]
requires: []
provides:
  - KbStatus, KbStrategy, KnowledgeBase, KbDocument, KbChunk tables with CASCADE FKs
  - shared/kb_storage.py (FAISS bytes round trip, atomic write, Cyrillic-safe paths)
  - agent.state kb_jobs, kb_index_cache, cleanup_kb_caches
  - KB_STORAGE_DIR and KB_EMBED_TIMEOUT settings
affects: [13-04, 13-05, 13-06, 13-08]
tech-stack:
  added: [faiss-cpu==1.15.1, pymupdf==1.28.2, numpy>=2.0, python-multipart==0.0.32]
  patterns: [serialize_index bytes + .tmp -> os.replace, sync storage helpers wrapped by callers in to_thread]
key-files:
  created: [shared/kb_storage.py, tests/test_kb_storage.py, tests/test_kb_models.py]
  modified: [requirements.txt, .gitignore, shared/config.py, shared/models.py, agent/state.py, tests/conftest.py, tests/test_database.py]
key-decisions:
  - "Index bytes written through Python I/O, never faiss.write_index(str(path))"
requirements-completed: [KB-07, KB-09, KB-11]
duration: 25min
completed: 2026-10-03
---

# Phase 13 Plan 01: KB Foundation Summary

Pinned RAG dependencies, three CASCADE-linked KB tables, a Cyrillic-safe atomic FAISS storage helper, KB in-memory job state and per-test KB isolation.

## Tasks

| Task | Commit |
| ---- | ------ |
| 1: deps, config keys, KB tables | 02e0bc3 |
| 2: storage helper, state, conftest, tests | a58b195 |

## Verification (observed)

- Task 1 verify command printed `120.0 knowledgebase kbdocument kbchunk`; `grep -c "Field(ondelete"` returned 0; test_database + test_cascade_delete: 14 passed.
- Full suite `pytest tests/ -q -x`: 1168 passed, 1 skipped (includes the new storage, cascade, unique-sha and cleanup tests).

## Deviations from Plan

**1. [Rule 1 - Bug] test_database table-set assertion**
- **Found during:** Task 1
- **Issue:** test_init_db_creates_all_tables asserts the exact table set; new tables broke it.
- **Fix:** added knowledgebase, kbdocument, kbchunk to the expected set.
- **Files modified:** tests/test_database.py
- **Commit:** 02e0bc3

Minor: `import fitz` emits a pymupdf deprecation warning; later plans should use `import pymupdf`.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED
