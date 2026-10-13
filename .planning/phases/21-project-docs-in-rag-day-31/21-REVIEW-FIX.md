---
phase: 21-project-docs-in-rag-day-31
fixed_at: 2026-10-13T00:00:00Z
review_path: .planning/phases/21-project-docs-in-rag-day-31/21-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 3
skipped: 1
status: partial
---

# Phase 21: Code Review Fix Report

**Fixed at:** 2026-10-13
**Source review:** .planning/phases/21-project-docs-in-rag-day-31/21-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 4
- Fixed: 3
- Skipped: 1

## Fixed Issues

### WR-01: Failure after the DB commit deletes files that committed documents reference

**Files modified:** `agent/kb_indexer.py`
**Commit:** d93a2e4
**Applied fix:** `_commit_addition` tracks a `committed` flag; a failure after commit raises a new `_PostCommitError` (index cache entry dropped, pending index kept). `run_add_job` handles it (and the cancel path via `_is_post_commit_failure`) by recording the error without unlinking the moved upload files. Status: fixed, requires human verification (error-handling logic; no test exercises a post-commit failure).

### WR-02: `_move_staged` can race with cancellation cleanup

**Files modified:** `agent/kb_indexer.py`
**Commit:** 2a9fbee
**Applied fix:** The move thread is wrapped in a future awaited via `asyncio.shield`; on cancellation `run_add_job` awaits the move to finish before running cleanup, so `moved` is complete. Status: fixed, requires human verification (concurrency logic).

### WR-04: Blocking synchronous cleanup inside async handlers

**Files modified:** `agent/kb_api.py`
**Commit:** 91aa0e0
**Applied fix:** The three synchronous `remove_kb_dir`/`remove_tree` calls now use `await asyncio.shield(asyncio.to_thread(...))`.

## Skipped Issues

### WR-03: Storage grows without a per-base cap, and the new-file cap is per request only

**File:** `agent/kb_api.py:203-247, 484-546`
**Reason:** Requires a product/design decision (new per-base size or file-count limit, and moving the claim before staging with release-on-failure). It changes the add flow and limits, so it is not a safe mechanical fix.
**Original issue:** Per-request caps only; concurrent or repeated adds can each stage up to 100 MB before losing the claim with 409.

---

_Fixed: 2026-10-13_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
