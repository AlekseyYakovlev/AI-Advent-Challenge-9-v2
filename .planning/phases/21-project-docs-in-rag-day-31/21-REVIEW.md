---
phase: 21-project-docs-in-rag-day-31
reviewed: 2026-10-13T00:00:00Z
depth: standard
files_reviewed: 21
files_reviewed_list:
  - agent/kb_api.py
  - agent/kb_indexer.py
  - agent/kb_limits.py
  - agent/kb_schemas.py
  - shared/database.py
  - shared/kb_storage.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
  - scripts/e2e_kb_folder_playwright.py
  - scripts/e2e_kb_playwright.py
  - tests/test_kb_add_api.py
  - tests/test_kb_add_job.py
  - tests/test_kb_migration.py
  - tests/test_docs_kb_upload.py
  - tests/kb_helpers.py
  - README.md
  - docs/API_SPEC.md
  - docs/ARCHITECTURE.md
  - docs/USER_GUIDE.md
  - docs/TESTING_GUIDE.md
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 21: Code Review Report

**Reviewed:** 2026-10-13
**Depth:** standard
**Files Reviewed:** 21 (tests, scripts, docs and index.html skimmed or spot-checked; the core logic files were read in full)

## Summary

The phase is generally sound. Path sanitisation is safe: uploaded names are display-only and files are stored under server-generated uuid names. The app.js code renders source names through `textContent`, with no `innerHTML` on file names. The migration runs with FKs off, rolls back on error and is idempotent. The add job claims the base atomically and commits the DB and index swap together. I found no BLOCKERs. The remaining problems are post-commit failure handling, cancellation races and some duplication.

## Structural Findings (fallow)

None provided.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Failure after the DB commit deletes files that committed documents reference

**File:** `agent/kb_indexer.py:488-498, 628-646`
**Issue:** `_commit_addition` commits the DB, then calls `promote_index`. If `promote_index` (or anything after the commit) raises, the exception propagates to `run_add_job`. That calls `_fail_add`, which runs `_unlink_files(moved)` and deletes the newly moved upload files. The new `KbDocument` and `KbChunk` rows are already committed and still point at them. The index file is still the old one. This leaves a base with chunk rows missing from the index and documents whose stored files are gone. `rollback()` cannot undo a committed transaction.
**Fix:** Track a `committed` flag. When the failure happens after `commit()`, do not unlink `moved`. Either retry `promote_index` or mark the base failed and repair it. For example, make `_commit_addition` wrap only the post-commit swap in its own handler that raises a distinct error, and have `run_add_job` skip the file cleanup for that error.

### WR-02: `_move_staged` can race with cancellation cleanup

**File:** `agent/kb_indexer.py:616-622, 638-644`
**Issue:** `_move_staged` runs in a worker thread and appends to `moved` as it goes. If the task is cancelled during `await asyncio.to_thread(_move_staged, ...)` (a KB delete or shutdown), the thread keeps running. `_fail_add` then unlinks and removes the staging tree concurrently with the thread. Files can be moved into uploads after the unlink pass, and they are never cleaned up. `commit_task` is still `None` on this path. Orphans remain until the next restart sweep, which only covers bases left in the adding phase.
**Fix:** Shield the move (`await asyncio.shield(asyncio.to_thread(...))`) and settle it before cleanup. Alternatively, have the cancel path re-run `_unlink_files(moved)` after the thread finishes.

### WR-03: Storage grows without a per-base cap, and the new-file cap is per request only

**File:** `agent/kb_api.py:203-247, 484-546`
**Issue:** `MAX_TOTAL_BYTES` and `MAX_FILES` apply to a single request. Repeated adds to a ready base are unbounded in disk use and index size. Files rejected after a full upload are not rate-limited. Each failing add stages up to 100 MB before a 422 or 409. The claim (`_claim_kb_for_add`) happens only after all uploads are stored and classified, so concurrent adds to the same base all stage up to 100 MB each before the losers get 409.
**Fix:** Run a cheap pre-check (the `kb.phase` read is already there) and, ideally, claim before staging with a release on failure. Consider a per-base total-size or file-count ceiling.

### WR-04: Blocking synchronous cleanup inside async handlers

**File:** `agent/kb_api.py:443, 527, 533`
**Issue:** `remove_kb_dir` and `remove_tree` are called synchronously (`shutil.rmtree` on up to 100 MB of staged data) on the event loop in the `except HTTPException` and `except BaseException` branches. The `UploadRejectedError` branch correctly uses `asyncio.to_thread`. This is inconsistent and blocks every WebSocket stream while it runs. On Windows `rmtree(ignore_errors=True)` can also silently leave directories behind.
**Fix:** Use `await asyncio.shield(asyncio.to_thread(remove_tree, staging))` in all three places.

## Info

### IN-01: Duplicated chunk-row construction

**File:** `agent/kb_indexer.py:159-174` and `411-426`
**Issue:** The `KbChunk(...)` construction and the batch insert loop are copy-pasted between `_insert_chunks` and `_commit_addition`. Any change to chunk fields (for example a new `section` rule) has to be made twice, and the copies can drift.
**Fix:** Extract `_chunk_rows(kb_id, doc, drafts) -> list[KbChunk]`.

### IN-02: Client and server source-name normalisation differ

**File:** `ui/static/app.js:4314-4316`, `agent/kb_api.py:165-173`
**Issue:** The client only converts `\` to `/`. The server also NFC-normalises, strips control characters and `.`/`..` segments, and trims segments. Two entries that look distinct to the client dedupe check (`a/../b.txt` and `b.txt`, or NFC and NFD forms of the same name) collapse to one on the server. The user then gets a "Файл … уже добавлен." 422 after a full upload.
**Fix:** Mirror the normalisation in `kbSourceName` (`.normalize('NFC')`, drop `.`/`..`/empty segments) so the pending list matches what the server will compare.

### IN-03: Starlette multipart limits are not reflected in the 200-file cap

**File:** `agent/kb_api.py:386-395, 176-181`
**Issue:** Starlette's multipart parser enforces its own `max_files` (default 1000) and rejects larger requests with a generic 400 before the handler's friendly "too many files" message. This is an edge case only for clients that bypass the UI (the UI caps at 200). It also means `MSG_TOO_MANY_FILES` cannot be reached above 1000 files.
**Fix:** Document the behaviour, or accept `max_files` via a custom request parser. Low priority.

---

_Reviewed: 2026-10-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
