---
phase: 26-project-files-assistant-day-34
plan: 03
subsystem: scripts
tags: [cli, http, git, reproducibility, respx]
requires: [26-01]
provides:
  - scripts/files_task.py (run_goal, collect_patch, patch_stats, build_run_record, save_artifacts, scrub, scrub_paths, check_root, amain)
affects: [26-06]
tech-stack:
  added: []
  patterns: [list-argv git, intent-to-add patch capture, scrub of key and absolute path]
key-files:
  created:
    - scripts/files_task.py
    - tests/test_files_task.py
  modified: []
key-decisions:
  - "scrub_paths also replaces the JSON-escaped (doubled backslash) form, because run.json is scrubbed after json.dumps"
  - "check_root compares git top-levels, so a subfolder of the script's own repository is also treated as the own repository"
requirements-completed: [PFILE-07, PFILE-08]
duration: 15min
completed: 2026-10-17
---

# Phase 26 Plan 03: Reproducible goal run script Summary

`scripts/files_task.py` sends a goal to a running app over `POST /api/v1/chats/{id}/messages` at temperature 0 and saves `run.json`, `run.patch` (git add -N + git diff --binary) and `answer.md`, with the API key and the absolute root path scrubbed.

## Tasks

1. Task 1 (RED): `tests/test_files_task.py`, 13 tests (respx-mocked endpoint, real temp git repos). Commit 8646c14. The run failed at collection because the script did not exist.
2. Task 2 (GREEN): `scripts/files_task.py`. Commit f885666.

## Verification (observed)

- `pytest tests/test_files_task.py -n0`: 13 passed.
- `pytest tests/`: 3865 passed, 14 skipped.
- `python scripts/files_task.py --help` prints `--allow-own-repo`; `shell=True` count is 0.
- Not verified: a run against a live app and a real model (no running instance used); only the mocked endpoint was exercised.

## Deviations from Plan

None. Added an optional `timeout_s` parameter to `run_goal` (default `REQUEST_TIMEOUT_S`) so the `--timeout` flag has effect.

## Known Stubs

None.

## Self-Check: PASSED
