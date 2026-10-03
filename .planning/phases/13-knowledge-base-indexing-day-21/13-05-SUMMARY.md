---
phase: 13-knowledge-base-indexing-day-21
plan: 05
subsystem: knowledge-base
tags: [indexing, faiss, asyncio, events, lifecycle]
requires: [13-01, 13-02, 13-03, 13-04]
provides:
  - agent/kb_schemas.py (KbOut, kb_out, kb_progress_frame, kb_deleted_frame)
  - agent/kb_indexer.py (spawn_index_job, run_index_job, delete_kb, recover_orphaned_kb_jobs, shutdown_kb_jobs, reset_state, MSG_* and PHASE_* constants)
  - lifespan wiring in agent/main.py
affects: [13-06, 13-07]
key-files:
  created:
    - agent/kb_schemas.py
    - agent/kb_indexer.py
    - tests/kb_helpers.py
    - tests/test_kb_indexer.py
    - tests/test_kb_lifecycle.py
    - tests/test_kb_events.py
  modified:
    - agent/main.py
    - agent/kb_chunking.py
    - tests/conftest.py
    - tests/test_kb_chunking.py
requirements-completed: [KB-06, KB-07, KB-08, KB-09, KB-11]
completed: 2026-10-03
---

# Phase 13 Plan 05: Background indexing job Summary

Semaphore(1) background job that parses, chunks, embeds (sequential batches of 32) and writes one FAISS IndexIDMap2 file per KB, with all-or-nothing failure, delete-with-cancel, startup orphan recovery and throttled `kb_progress` events.

## Tasks

| Task | Commit |
| ---- | ------ |
| Pre-task: article regex fix (see deviations) | 593c741 |
| 1: schemas + indexing job + tests | a504bb3 |
| 2: delete/recovery/shutdown + lifespan + tests | 53d0d67 |
| 3: events/health tests | (test commit after 53d0d67) |

## Verification (observed)

- `pytest tests/test_kb_chunking.py -q` -> 22 passed.
- `pytest tests/test_kb_indexer.py -q` -> 8 passed.
- `pytest tests/test_kb_lifecycle.py tests/test_scheduler_lifespan.py -q` -> 10 passed.
- `pytest tests/test_kb_events.py tests/test_kb_indexer.py tests/test_kb_lifecycle.py -q` -> 20 passed.
- Full suite `pytest tests/ -q` -> 1254 passed, 1 skipped.
- `grep -c to_thread agent/kb_indexer.py` -> 5; no `faiss.write_index`/`read_index` in the indexer.
- The health test uses a loader that does `time.sleep(1.0)`; five `/health` calls during the job all returned 200 in < 0.5 s.

## Deviations from Plan

**1. [Rule 1 - Bug] Structural chunker article regex (known issue from wave 1)**
- `ARTICLE_RE` did not match real КоАП suffix articles such as `Статья 14.1_1-1.`. Changed the number pattern to `\d+(?:[._-]\d+)*`; added a test; existing tests still pass.
- Also, when a heading is merged with its first body paragraph on one line (long "title"), the body would have been lost into a truncated breadcrumb. Added `_split_merged_heading` (cut at first sentence end, else last space before 150 chars) and a test. Headings are located by regex, not by first line.
- Files: agent/kb_chunking.py, tests/test_kb_chunking.py. Commit 593c741.

**2. [Rule 3 - Blocking] Shared test helper module**
- Added tests/kb_helpers.py (seeding, FakeEmbedder, wait_until) reused by all three test files; tests/ has no `__init__.py`, so it is imported as `kb_helpers`.

**3. Worktree base** was reset to e1c535c at start per the branch check.

## Implementation notes
- On cancellation the job runs `_fail(MSG_INTERRUPTED)` then re-raises; `delete_kb` awaits the cancelled job (10 s bound) before deleting rows, so a spurious `failed` frame can precede `kb_deleted` for a KB deleted mid-job.
- `_fail` also resets chunk_count/total_chunks to 0 and removes the index file (all-or-nothing).
- Progress DB writes happen after every batch; only frames are throttled (0.5 s, last batch always sent).

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
