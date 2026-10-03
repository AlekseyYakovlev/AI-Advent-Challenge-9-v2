---
phase: 13-knowledge-base-indexing-day-21
reviewed: 2026-10-03T00:00:00Z
depth: standard
files_reviewed: 26
files_reviewed_list:
  - agent/embeddings.py
  - agent/kb_api.py
  - agent/kb_chunking.py
  - agent/kb_indexer.py
  - agent/kb_limits.py
  - agent/kb_loaders.py
  - agent/kb_schemas.py
  - agent/kb_search.py
  - agent/llm_client.py
  - agent/main.py
  - agent/providers.py
  - agent/state.py
  - shared/config.py
  - shared/kb_storage.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
  - scripts/e2e_kb_playwright.py
  - requirements.txt
  - .gitignore
  - tests/conftest.py
  - tests/kb_helpers.py
  - tests/test_kb_api.py
  - tests/test_kb_indexer.py
  - tests/test_kb_search.py
  - tests/test_kb_scoping.py
findings:
  critical: 0
  warning: 5
  info: 3
  total: 8
status: issues_found
---

# Phase 13: Code Review Report

**Reviewed:** 2026-10-03
**Depth:** standard
**Files Reviewed:** 26 (tests, e2e script, index.html and the unchanged parts of main/llm_client/app.js were only skimmed)

## Summary

The KB feature is carefully built. User scoping is consistent (404 for foreign ids, `_get_owned_kb`). Storage paths use only integer ids and server-generated names, so I found no path traversal. Commit/rollback is handled throughout. The frontend uses `textContent` and I found no `innerHTML` for KB data. No blockers. The main concerns are an ineffective request-size guard, a race between delete and a running index job, and a few robustness gaps.

## Structural Findings (fallow)

None provided.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Upload size guard runs after the whole body is already parsed

**File:** `agent/kb_api.py:109-113, 262-267`
**Issue:** `_require_content_length_within_cap` is a route dependency. FastAPI parses the multipart body (`request.form()`, spooling all files to temp storage) before it resolves dependencies. The 413 "before reading the body" check therefore fires only after the full upload is already spooled to disk. Chunked requests with no Content-Length bypass it entirely. The per-file and total checks in `_store_upload` also run only after Starlette has buffered everything, so they do not cap disk or CPU use.
**Fix:** Enforce the cap before body parsing, either in an ASGI middleware or by wrapping the route's `Request` in a custom `APIRoute` that checks Content-Length and counts streamed bytes. Alternatively, accept the limitation and document that the cap is post-hoc, and fix the docstring.

### WR-02: Delete can race a still-running index job and leave orphan files

**File:** `agent/kb_indexer.py:306-323`
**Issue:** `asyncio.wait_for(job, 10s)` cancels the job again on timeout. A cancellation cannot interrupt the worker thread in `_build_and_write_index` or `_chunk_document`. `remove_kb_dir` can then run while that thread is still writing, and the thread recreates `index.faiss` via `write_index_bytes` (`mkdir(parents=True)`). The result is an orphan directory with no DB row. If `_fail` is interrupted by the second cancel, chunk rows may also linger for a moment. On Windows, `rmtree` can raise `PermissionError` if the file is open. That exception propagates after the DB rows are already deleted, so the client gets a 500 for a delete that took effect.
**Fix:** Wrap `remove_kb_dir` in a try/except `OSError` that logs a warning, and make `write_index_bytes` not recreate parents for KBs that no longer exist. For example, check `kb_dir.exists()` in the job before writing, or re-run the dir removal after the job's thread finishes.

### WR-03: Search can cache an index for a KB being deleted

**File:** `agent/kb_search.py:39-53`, `agent/kb_indexer.py:309`
**Issue:** `search_kb` awaits a thread read and then sets `kb_index_cache[kb.id]`. If `cleanup_kb_caches` ran in between, the stale index is re-inserted and never evicted, because the KB id is gone. The cache entry only holds memory, but it is a leak that survives the delete.
**Fix:** After loading, re-check the KB still exists, or evict in `delete_kb` after the file removal as well.

### WR-04: Non-`EmbeddingError` exceptions in the search and check routes produce 500

**File:** `agent/embeddings.py:196-197`, `agent/kb_api.py:239-245, 380-387`
**Issue:** `embed_texts` raises `ValueError` for over-long input. `/embedding-check` and `/search` only catch `EmbeddingError`. `MAX_QUERY_CHARS` (2000) + prefix is below the limit today, but the invariant is implicit and split across two modules. In the indexer a `ValueError` is reported as the generic MSG_UNEXPECTED. `_validate_vectors` can also raise `TypeError` or `ValueError` for non-numeric embedding elements, which is never validated.
**Fix:** Raise `EmbeddingError` with a user message instead of `ValueError`. Validate that vector elements are numeric, or catch the `TypeError`/`ValueError` from `np.asarray`.

### WR-05: Index job's failure handler is skipped while waiting for the semaphore, and `recover_orphaned_kb_jobs` skips `updated_at`

**File:** `agent/kb_indexer.py:226, 344-354`
**Issue:** The `async with _get_semaphore()` sits outside the `try`. A task cancelled while queued never runs `_fail`. This is harmless on delete, but on shutdown the KB stays `queued` until the next restart's recovery. The recovery `update(...)` also does not refresh `updated_at`, so the field misleads.
**Fix:** Add `updated_at=datetime.now(timezone.utc)` to the recovery `.values(...)`. Optionally move the semaphore inside the `try`.

## Info

### IN-01: Duplicate EMBED_BATCH_SIZE constant

**File:** `agent/embeddings.py:31`, `agent/kb_limits.py:16`
**Issue:** The same value (32) is defined twice. The indexer batches by one and `embed_texts` re-batches by the other, so changing one silently changes behavior.
**Fix:** Import it from `agent.kb_limits` in `embeddings.py`.

### IN-02: `ensure_embedding_model` `on_loading` parameter is unused by production callers; `list_embedding_models` ignores per-provider base URL

**File:** `agent/embeddings.py:121-125, 81`
**Issue:** The callback is dead API surface in this phase. Embeddings always target `settings.LM_STUDIO_BASE_URL`, even though providers support custom LM Studio base URLs.
**Fix:** Remove the callback or use it to publish the `loading_model` phase. Document the single-host assumption.

### IN-03: Deprecated `session.exec(delete(...))` usage

**File:** `agent/kb_indexer.py:285, 314-316, 342-343`
**Issue:** SQLModel's `AsyncSession.exec` is deprecated for non-select statements, and `session.execute` is preferred.
**Fix:** Use `await session.execute(delete(...))`.

---

_Reviewed: 2026-10-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
