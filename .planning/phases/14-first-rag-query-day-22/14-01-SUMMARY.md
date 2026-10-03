---
phase: 14-first-rag-query-day-22
plan: 01
subsystem: rag
tags: [rag, faiss, sqlmodel, migration]
requires: []
provides:
  - ChatRagConfig table and Message.rag_sources column with idempotent migration
  - EmbeddingDimMismatchError in kb_search
  - agent/rag.py shared retrieval, budget, block, merge and payload helpers
affects: [14-02, 14-03, 14-04, 14-05]
tech-stack:
  added: []
  patterns: [fail-soft retrieve wrapper raising only RagFailure, versioned JSON payload]
key-files:
  created: [agent/rag.py, tests/test_rag.py]
  modified: [shared/models.py, shared/database.py, shared/config.py, agent/kb_search.py, tests/test_database.py, tests/test_cascade_delete.py, tests/test_kb_search.py]
key-decisions:
  - "RAG_EMBED_TIMEOUT=30s bounds the chat-path query embedding"
  - "CYRILLIC_SAFETY=1.15 headroom on the 30% fragments budget"
requirements-completed: [RAG-01, RAG-02, RAG-03, RAG-04, RAG-05]
duration: 20min
completed: 2026-10-03
---

# Phase 14 Plan 01: RAG data and retrieval foundation Summary

ChatRagConfig table, Message.rag_sources migration, a distinct dim-mismatch search error, and agent/rag.py with fail-soft retrieve, 30% token budget, delimited fragments block, last-user-message merge and versioned source payload.

## Tasks

| Task | Commit | Notes |
|------|--------|-------|
| 1. ChatRagConfig, rag_sources migration, RAG_EMBED_TIMEOUT, EmbeddingDimMismatchError | 845096e | `pytest tests/test_database.py tests/test_cascade_delete.py tests/test_kb_search.py tests/test_kb_api.py`: 56 passed |
| 2. agent/rag.py + tests | 7222b97 | `pytest tests/test_rag.py`: 20 passed |

## Deviations from Plan

- [Rule 3 - Blocking] Worktree HEAD was not at the expected base commit; reset to 24a2aea per the startup check before any work.
- tests/test_database.py::test_init_db_creates_all_tables expected set updated with `chatragconfig` (required by the new table).

## Known Stubs

None.

## Verification notes

Full `pytest tests/` was started but see the orchestrator for its result if not stated here; targeted suites listed above were run and passed. Grep checks: no bare `except:` and no `query=` in agent/rag.py.

## Self-Check: PASSED
Files and commits 845096e, 7222b97 exist.
