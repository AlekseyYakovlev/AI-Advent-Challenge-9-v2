---
phase: 13-knowledge-base-indexing-day-21
reviewed: 2026-10-03T00:00:00Z
depth: standard
files_reviewed: 29
files_reviewed_list:
  - agent/kb_api.py
  - agent/kb_chunking.py
  - agent/kb_indexer.py
  - agent/kb_limits.py
  - agent/kb_loaders.py
  - agent/kb_schemas.py
  - agent/kb_search.py
  - agent/main.py
  - scripts/e2e_kb_playwright.py
  - tests/conftest.py
  - tests/kb_helpers.py
  - tests/test_kb_api.py
  - tests/test_kb_chunking.py
  - tests/test_kb_events.py
  - tests/test_kb_indexer.py
  - tests/test_kb_lifecycle.py
  - tests/test_kb_loaders.py
  - tests/test_kb_real_pdfs.py
  - tests/test_kb_scoping.py
  - tests/test_kb_search.py
  - tests/test_modal_close_policy.py
  - ui/static/app.js
  - ui/static/index.html
  - docs/API_SPEC.md
  - docs/ARCHITECTURE.md
  - docs/TESTING_GUIDE.md
  - docs/USER_GUIDE.md
  - tests/fixtures/kb/fz196_excerpt_pages.txt
  - tests/fixtures/kb/koap_excerpt_pages.txt
findings:
  critical: 0
  warning: 5
  info: 1
  total: 6
status: issues_found
---

# Phase 13: Code Review Report

**Depth:** standard
**Scope note:** The agent/kb_*.py modules were read in full. main.py was checked only at the KB wiring points. app.js was grepped for unsafe innerHTML, and the one hit is the markdown render path at line 2746, outside this phase. Tests, docs, fixtures, index.html and the e2e script were not read in detail.

## Summary

Ownership scoping is consistent (404 for foreign ids), uploads are stored under server-generated names, and indexing is all-or-nothing. I found no security blockers. The issues below are robustness and correctness gaps.

## Warnings

### WR-01: Upload size cap relies on the Content-Length header

**File:** `agent/kb_api.py:109-113`
**Issue:** `_require_content_length_within_cap` only checks the declared header. A chunked-transfer request has no header and bypasses the check. FastAPI/Starlette parses the whole multipart body (spooled to temp files) before `create_kb` runs, so the per-file and total limits in `_store_upload` take effect only after the full body has been received. A client can make the server buffer arbitrarily large bodies. A non-numeric header also passes silently.
**Fix:** Reject requests with no Content-Length (411) or enforce a streaming byte counter in middleware. Treat a non-digit header as 400.

### WR-02: Client disconnect or cancellation during upload leaks files

**File:** `agent/kb_api.py:321-325`
**Issue:** The cleanup handler is `except Exception`. `asyncio.CancelledError` is a `BaseException`, so a client disconnect or server shutdown mid-store skips `remove_kb_dir`. The `.part` files and stored uploads are then orphaned on disk. The DB row is rolled back, but the `kb_id` can still be reused by SQLite and may collide with the leftover directory.
**Fix:** Catch `BaseException` (or use `try/finally` with a success flag), clean up, then re-raise.

### WR-03: Delete timeout lets the indexing job keep writing after removal

**File:** `agent/kb_indexer.py:309-322`
**Issue:** `asyncio.TimeoutError` from `wait_for(job, 10s)` is suppressed. In that case `wait_for` has cancelled the task but has not necessarily awaited its completion. Cancellation is not guaranteed to be prompt, for example during `asyncio.to_thread(_build_and_write_index)` or `_parse_documents`, and a worker thread cannot be cancelled. The thread can finish `write_index_bytes` after `remove_kb_dir`, which recreates an index file for a deleted KB. `_fail` also can rewrite or republish for a KB that is already gone.
**Fix:** Run `remove_kb_dir` again after the job has actually finished. Alternatively, have the job re-check that the KB row exists before writing the index file.

### WR-04: Bare-number lines are dropped from every PDF as page numbers

**File:** `agent/kb_loaders.py:33,123-125`
**Issue:** `_PAGE_NUMBER_RE` matches any line consisting only of digits, regardless of position on the page. Numbers on a line of their own in the body (a PDF table cell, numbered-list marker text broken onto its own line, an article number split from its heading) are silently deleted from the indexed text. Edge-line detection is only used for repeated headers, not for this filter.
**Fix:** Apply the bare-number filter only to lines in `_edge_indexes` (header and footer positions).

### WR-05: Stored `query_prefix`/`doc_prefix` may not match what embeddings use

**File:** `agent/kb_indexer.py:246-258`, `agent/kb_search.py:63`
**Issue:** `search_kb` calls `embed_query(kb.embedding_model, query)` and never reads `kb.query_prefix`. Prefixes are computed again from the model name at indexing time and stored, but the search path does not use the stored values. If the prefix table in `agent.embeddings` changes after an index is built, queries and passages silently diverge, and the stored columns are dead. I did not read `agent/embeddings.py`, so this assumes `embed_query` re-derives the prefix itself.
**Fix:** Pass the stored prefix into the embed calls, or drop the columns.

## Info

### IN-01: Unused or implicit-contract helpers

**File:** `agent/kb_loaders.py:249`
**Issue:** `load_document` raises a bare `ValueError` for an unsupported suffix. This is unreachable via the API, which validates extensions. If it were hit, it would surface as the generic MSG_UNEXPECTED. Consider raising `KbLoadError` for consistency.
**Fix:** `raise KbLoadError(MSG_BAD_TYPE.format(name=filename))`.

---

_Reviewed: 2026-10-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
