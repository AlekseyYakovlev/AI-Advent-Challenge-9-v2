---
phase: 24-automated-pr-code-review-day-32
plan: 05
subsystem: code-index
tags: [git, rag, faiss, incremental-index]
requires: [24-02]
provides:
  - agent/code_git.py shell-free git reads (validate path, repo key, fetch base, list tree, read blobs)
  - agent/code_index.py incremental per-base-branch code knowledge base with refresh job and progress
affects: [24-07]
tech-stack:
  added: []
  patterns: [blob-id diffing, pending index file promoted after DB commit, per-key asyncio lock]
key-files:
  created:
    - agent/code_git.py
    - agent/code_index.py
    - tests/test_code_git.py
    - tests/test_code_index.py
  modified: []
key-decisions:
  - "Fetch happens inside the per-(user, repo, base) lock so an older tip cannot overwrite a newer one."
  - "A first build embeds before any row is created; rows, link and index are written in one transaction, so a failed first build leaves nothing."
  - "A base built with a different embedding model is dropped and rebuilt (vectors of different dimension cannot be patched)."
  - "Binary or empty changed files are recorded as documents with zero fragments so they are not re-read on every refresh."
requirements-completed: [REV-04]
duration: 40min
completed: 2026-10-14
---

# Phase 24 Plan 05: Git-based code indexer Summary

Code knowledge base built from the base branch tip fetched from `origin` and read from git objects, refreshed incrementally by blob id and committed atomically.

## Tasks

| Task | Commit | Result |
| --- | --- | --- |
| 1. agent/code_git.py | 583b900 | `python -m pytest tests/test_code_git.py -q`: 40 passed |
| 2. agent/code_index.py | 3ce55d3 | `pytest tests/test_code_index.py tests/test_code_git.py tests/test_kb_indexer.py tests/test_kb_search.py -q`: 102 passed |

Source gates were run: no `shell=True`, `os.system`, `subprocess.run` or `multiprocessing` in `agent/code_git.py` (0 matches); no `mcp_servers` reference (0); no `open(`, `read_text`, `read_bytes` in `agent/code_index.py` (0). `agent/kb_indexer.py`, `agent/kb_api.py`, `shared/kb_storage.py` are not modified.

## Deviations from Plan

**1. [Worktree base] Reset to the expected base.** The worktree started on an older commit than the required base `83ec20a` (24-02 output was missing); reset to it as the startup check prescribes.

**2. [Rule 1 - ordering] Fetch moved inside the lock.** The plan lists fetch before taking the lock; two refreshes could then install an older tip over a newer one. The repository-mismatch check still happens before any fetch.

**3. [Design] Model change drops the base.** If the stored base was built with another embedding model it is deleted and rebuilt, since vector dimensions can differ.

## Assumption Drift (advisory)

- Planned: the first build creates the knowledge base row early (as the knowledge-base job does). Actual: rows are created in the final transaction, after embedding, so a failed first build needs no cleanup beyond the directory. Why: simpler all-or-nothing guarantee.

## Known Stubs

None.

## Not verified

Real LM Studio embeddings were not used; tests use the recording fake embedder. Windows only was exercised (git 2.39.1).

## Self-Check: PASSED
