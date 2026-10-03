---
phase: 13
fixed_at: 2026-10-03T00:00:00Z
review_path: .planning/phases/13-knowledge-base-indexing-day-21/13-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 13: Code Review Fix Report

**Source review:** 13-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5
- Fixed: 5
- Skipped: 0

## Fixed Issues

### WR-01: Upload size cap relies on the Content-Length header

**Files modified:** `agent/kb_api.py`
**Commit:** 7480359 (shared with WR-02, same file)
**Applied fix:** Missing Content-Length now returns 411 and a non-numeric one returns 400. A streaming byte counter was not added, so chunked bodies are rejected rather than counted. Status: fixed, requires human verification (the 411 behavior is new).

### WR-02: Client disconnect or cancellation during upload leaks files

**Files modified:** `agent/kb_api.py`
**Commit:** 7480359
**Applied fix:** The cleanup handler now catches `BaseException`, shields the rollback, removes the KB directory synchronously and re-raises.

### WR-03: Delete timeout lets the indexing job keep writing after removal

**Files modified:** `agent/kb_indexer.py`
**Commit:** 206d2c2
**Applied fix:** If the job is still not done after the delete, a done-callback re-runs `remove_kb_dir`. A worker thread that outlives its task is still not covered. Status: fixed, requires human verification.

### WR-04: Bare-number lines are dropped from every PDF as page numbers

**Files modified:** `agent/kb_loaders.py`
**Commit:** f26dbb2
**Applied fix:** The bare-number and page-number filter now applies only to header and footer positions. The commentary filter still applies everywhere. Status: fixed, requires human verification.

### WR-05: Stored query_prefix/doc_prefix may not match what embeddings use

**Files modified:** `agent/embeddings.py`, `agent/kb_search.py`
**Commit:** fa86c36
**Applied fix:** `embed_query` takes an optional `prefix` argument, and `search_kb` passes the stored `kb.query_prefix`.

## Not in scope

IN-01 was not attempted because fix_scope is critical_warning.

---

_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
