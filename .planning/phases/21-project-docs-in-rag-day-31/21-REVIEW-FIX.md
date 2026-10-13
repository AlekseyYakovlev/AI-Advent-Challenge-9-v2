---
phase: 21-project-docs-in-rag-day-31
fixed_at: 2026-10-13T00:00:00Z
review_path: .planning/phases/21-project-docs-in-rag-day-31/21-REVIEW.md
iteration: 2
findings_in_scope: 7
fixed: 6
skipped: 1
status: partial
---

# Phase 21: Code Review Fix Report

**Fixed at:** 2026-10-13
**Source review:** .planning/phases/21-project-docs-in-rag-day-31/21-REVIEW.md
**Iteration:** 2 (covers pass 1: WR-01..WR-04, pass 2: IN-01..IN-03)

**Summary:**
- Findings in scope: 7
- Fixed: 6
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

### IN-01: Duplicated chunk-row construction

**Files modified:** `agent/kb_indexer.py`
**Commit:** b3d0837
**Applied fix:** Extracted `_chunk_rows(kb_id, doc, drafts)`; both `_insert_chunks` and `_commit_addition` now use it. The per-caller batch insert loops stay separate because they collect different outputs (pairs vs ids). Per-file test runs pass.

### IN-02: Client and server source-name normalisation differ

**Files modified:** `ui/static/app.js`
**Commit:** abde9cd
**Applied fix:** `kbSourceName` now mirrors the server's `_clean_source_name`: NFC normalisation, backslash to slash, control-character removal, segment trim, and dropping empty/`.`/`..` segments. The Windows drive-prefix strip is not mirrored (browser-provided names never carry one). `node --check` and the UI/upload tests pass.

### IN-03: Starlette multipart limits are not reflected in the 200-file cap

**Files modified:** `agent/kb_api.py`
**Commit:** 3a1949a
**Applied fix:** Documentation-only. Added a comment at the `MAX_FILES` check noting that Starlette's own `max_files` (default 1000) answers a generic 400 first, so the friendly message is only reachable below that.

## Skipped Issues

### WR-03: Storage grows without a per-base cap, and the new-file cap is per request only

**File:** `agent/kb_api.py:203-247, 484-546`
**Reason:** Requires a product/design decision (new per-base size or file-count limit, and moving the claim before staging with release-on-failure). It changes the add flow and limits, so it is not a safe mechanical fix.
**Original issue:** Per-request caps only; concurrent or repeated adds can each stage up to 100 MB before losing the claim with 409.

---

_Fixed: 2026-10-13_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2_
