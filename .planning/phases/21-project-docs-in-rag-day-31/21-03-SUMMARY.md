---
phase: 21-project-docs-in-rag-day-31
plan: 03
subsystem: knowledge-base
tags: [kb, faiss, background-job, incremental-index]
requires: ["21-01"]
provides:
  - "agent.kb_indexer.AddItem, spawn_add_job, run_add_job, _fail_add"
  - "recover_orphaned_kb_jobs clears stale additions on ready bases"
  - "shared.kb_storage staging_root, new_staging_dir, remove_tree, patch_index, write_index_pending, promote_index"
  - "kb_progress_frame(kb, add_summary=None)"
affects: [21-04, 21-06]
tech-stack:
  added: []
  patterns: ["incremental FAISS patch on a copy read from disk", "pending file plus os.replace after commit", "asyncio.shield around the finalising transaction"]
key-files:
  created: [tests/test_kb_add_job.py]
  modified: [agent/kb_indexer.py, shared/kb_storage.py, agent/kb_schemas.py, tests/kb_helpers.py, tests/test_kb_storage.py, tests/test_kb_lifecycle.py]
decisions:
  - "Index patched incrementally (remove_ids + add_with_ids on a copy); untouched vectors are never recomputed"
  - "Addition in progress is status=ready with phase=adding; no new column"
  - "Failed addition stores its message in KnowledgeBase.error on the ready base; the next addition clears it"
  - "Summary is transient: sent once as add_summary in the final frame"
  - "Addition shares the single-slot indexing semaphore with index jobs"
metrics:
  tasks: 3
  completed: 2026-10-13
---

# Phase 21 Plan 03: Add-files background job Summary

`run_add_job` chunks and embeds only the new files with the base's own strategy and model, then applies rows, replacements and an atomically swapped patched FAISS index in one short transaction, keeping the base `ready` and searchable throughout and leaving it byte-identical on any failure.

## Commits
- 68a3890: storage helpers (staging, patch, pending/promote) and optional `add_summary` frame key
- 952d7ec: `run_add_job`, `_commit_addition`, `_fail_add`, orphan recovery for interrupted additions
- e4511c3: job-level and lifecycle tests, helpers

## Verification (observed)
- `pytest tests/test_kb_storage.py tests/test_kb_events.py`: 20 passed
- `pytest tests/test_kb_add_job.py tests/test_kb_lifecycle.py tests/test_kb_indexer.py tests/test_kb_storage.py`: 51 passed (15 tests in test_kb_add_job.py)
- Source check: add path calls no `_fail(` and no `_parse_documents`; `AddItem` is frozen.
- Full suite (`DB_PATH=test_plan03.db pytest tests/`): 2637 passed, 11 skipped, 1 failed (`test_kb_api.py::test_create_happy_path`). The same test fails on the base commit c06323a without this plan's changes, so it is pre-existing and unrelated (see Deferred Issues).

## Deviations from Plan

**1. [Rule 1 - Bug] Failure cleanup moved out of the exception handlers**
- **Found during:** Task 3 (`test_unreadable_file_leaves_base_untouched`)
- **Issue:** Calling `_fail_add` inside the `except` block left the staging directory behind on Windows: the live traceback of the failed PDF open kept a file handle, and `shutil.rmtree(ignore_errors=True)` silently skipped it.
- **Fix:** Handlers only record the failure message (or a cancelled flag); `_fail_add` / settle-and-finish run after the handlers. Behavior is otherwise as specified.
- **Files modified:** agent/kb_indexer.py
- **Commit:** e4511c3

**2. [Rule 3 - Test environment] Isolated test DB for the full run**
- The user's app (`run.py`, ports 8000/8001) holds `test_app.db`, so the suite was run with `DB_PATH=test_plan03.db`. The user's processes were not touched.

## Deferred Issues
- `tests/test_kb_api.py::test_create_happy_path` fails identically on c06323a (before this plan). Not caused by this plan; not investigated.

## Assumption Drift (advisory)
None.

## Known Stubs
None.

## Threat Flags
None. The accepted risks T-21-12 and T-21-38 are implemented as planned (`kb_add_commit_slow` warning and `commit_seconds` in `kb_add_ready`).

## Self-Check: PASSED
- agent/kb_indexer.py, shared/kb_storage.py, agent/kb_schemas.py, tests/test_kb_add_job.py present; commits 68a3890, 952d7ec, e4511c3 found in git log.
