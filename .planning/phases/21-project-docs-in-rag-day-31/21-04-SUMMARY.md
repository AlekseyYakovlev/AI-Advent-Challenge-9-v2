---
phase: 21-project-docs-in-rag-day-31
plan: 04
subsystem: knowledge-base
tags: [kb, upload, api, incremental-index]
requires: ["21-01", "21-03"]
provides:
  - "POST /api/v1/kb/{kb_id}/files (add_files_route)"
  - "kb_api._classify_additions, _claim_kb_for_add, _release_add_claim"
affects: [21-06]
tech-stack:
  added: []
  patterns: ["conditional UPDATE with rowcount as the only admission check", "per-request staging directory removed on every failure path"]
key-files:
  created: [tests/test_kb_add_api.py]
  modified: [agent/kb_api.py, tests/test_kb_scoping.py]
decisions:
  - "Classification groups existing documents by filename: no group = added, one document with equal sha256 = unchanged, any other group = replaced and all its documents superseded"
  - "Admission is a conditional UPDATE (status ready, phase NULL) with rowcount == 1; the early in-memory check is only a fast path"
  - "Limits apply per upload request, not to the accumulated size of the base"
metrics:
  tasks: 2
  completed: 2026-10-13
---

# Phase 21 Plan 04: Add-files route Summary

`POST /api/v1/kb/{kb_id}/files` validates and stages uploads for a ready base, classifies each file as added, replaced or unchanged by source name and sha256, claims the base atomically and starts `spawn_add_job`, answering 202 with `KbOut`.

## Commits
- 7cfd379: route, classification and claim helpers in `agent/kb_api.py`
- 7bcd669: route tests (`tests/test_kb_add_api.py`, 30 cases) and scoping tests

## Verification (observed)
- Task 1 verify command (route registered 202, call order, no `remove_kb_dir`/`KbDocument(`): `ok`; `pytest tests/test_kb_api.py`: 50 passed.
- `pytest tests/test_kb_add_api.py tests/test_kb_scoping.py`: 43 passed.
- `pytest tests/ -k "kb or rag"` (after the final edit): 896 passed, 10 skipped.
- Full suite `DB_PATH=test_plan04.db pytest tests/` (run before the last `session.execute` to `session.exec` edit): 2701 passed, 11 skipped. The earlier pre-existing failure of `test_create_happy_path` did not reproduce.

## Deviations from Plan

**1. [Rule 1 - Quality] `session.exec` instead of `session.execute` for the claim updates**
- SQLModel emits a deprecation warning for `session.execute`; the two update statements use `session.exec`. Behavior unchanged, rowcount still works (tests pass).
- Commit: 7bcd669

**2. [Rule 2 - Test addition] Extra test `test_claim_released_when_spawn_fails`**
- Covers the T-21-21 mitigation (claim released and staging removed when the job cannot be started).

## Assumption Drift (advisory)
None.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
- agent/kb_api.py, tests/test_kb_add_api.py present; commits 7cfd379 and 7bcd669 in git log.
