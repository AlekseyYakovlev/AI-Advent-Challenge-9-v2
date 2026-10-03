---
phase: 15-reranking-and-filtering-day-23
plan: 02
subsystem: rag-storage-settings
tags: [rag, sqlite, fts5, migration, settings-api]
requires: []
provides:
  - ChatRagConfig candidate_k/threshold/lexical/llm_rerank/hybrid/rewrite columns
  - kb_chunk_fts FTS5 table with kbchunk_fts_ai / kbchunk_fts_ad triggers and backfill
  - agent.rag CALIBRATED_THRESHOLDS, calibrated_threshold, resolve_threshold, DEFAULT_CANDIDATE_K
  - settings.RAG_LLM_STAGE_TIMEOUT (45.0)
  - partial PUT and extended GET on /api/v1/chats/{id}/rag
affects: [15-03, 15-04, 15-05, 15-06, 15-07, 15-09]
tech-stack:
  added: []
  patterns: [idempotent PRAGMA-guarded ALTER migration, trigger-synced FTS5 mirror, model_fields_set partial update]
key-files:
  created: []
  modified:
    - shared/models.py
    - shared/database.py
    - shared/config.py
    - agent/rag.py
    - agent/rag_api.py
    - tests/test_database.py
    - tests/test_kb_lifecycle.py
    - tests/test_rag.py
    - tests/test_rag_api.py
key-decisions:
  - "CALIBRATED_THRESHOLDS ships empty: unknown model resolves to 0.0 (no cut) until calibration values are written"
  - "No UPDATE trigger on kbchunk: chunks are deleted and re-inserted, never updated in place"
requirements-completed: [RANK-01, RANK-04, RANK-07]
duration: ~25min
completed: 2026-10-03
---

# Phase 15 Plan 02: Search settings storage, FTS5 mirror and threshold lookup Summary

Per-chat two-stage retrieval settings (candidate_k, nullable threshold, four independent stage flags) with idempotent migration, a trigger-synced FTS5 index over kbchunk with backfill, per-model calibrated threshold lookup and a partial-update settings API.

## Tasks

| Task | Commit | Notes |
|------|--------|-------|
| 1 + 2: columns, migration, stage timeout, FTS5 table/triggers/backfill | 8ae6490 | Both touch shared/database.py, so committed together |
| 3: threshold lookup and partial-update API | e80fcd0 | |

## Verification (actually run)
- `pytest tests/test_database.py tests/test_kb_lifecycle.py tests/test_kb_indexer.py tests/test_rag.py tests/test_rag_api.py tests/test_rag_turn.py tests/test_rag_ws.py -q`: 101 passed.
- Full suite `pytest tests -q -x`: 1158 passed, 1 skipped, 1 failed (`tests/test_supervisor.py::test_agent_restarts_within_5_seconds`, timeout). The same test fails on the base commit a0f2e3a, so it is pre-existing and unrelated (agent subprocess spawn in this worktree). `-x` stopped the run there, so tests after it alphabetically were not executed in that run.
- Not run: the "copy of a pre-Phase-15 database, init_db twice" check as a separate manual step; idempotence is covered by the migration and FTS tests.

## Deviations from Plan

**1. [Rule 3 - Blocking] Merged commits for Tasks 1 and 2.** Both add functions to shared/database.py; splitting hunks was not worth the risk. Tests for each were still written per task.

**2. [Rule 1 - Bug] Updated `test_init_db_creates_all_tables`.** It asserted the exact table set; the FTS5 virtual table and its shadow tables (`kb_chunk_fts*`) now exist, so the test excludes that prefix.

**3. [Rule 1 - Bug] Updated `test_config_defaults`.** It asserted the exact GET body; it now includes the new search-setting fields (required by the extended contract).

**4. Worktree base.** HEAD merge-base was 87d51ec; reset --hard to a0f2e3a per the base check before any work.

## Assumption Drift (advisory)
None material.

## Known Stubs
`CALIBRATED_THRESHOLDS = {}` is intentional (plan note): values come from plan 15-08/15-09 calibration.

## Threat Flags
None beyond the plan's threat model (bounds on candidate_k 1..50, threshold 0..1, StrictBool flags; owner checks unchanged; DDL static).

## Self-Check: PASSED
Commits 8ae6490 and e80fcd0 exist; modified files present.
