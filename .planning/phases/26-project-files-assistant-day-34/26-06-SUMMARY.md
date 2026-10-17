---
phase: 26-project-files-assistant-day-34
plan: 06
subsystem: eval-tooling
tags: [scenarios, isolated-copy, mcp, local-model]
requires:
  - phase: 26-03
    provides: scripts/files_task.py (run_goal, collect_patch, save_artifacts)
  - phase: 26-04
    provides: project_git.py --allow-write write tools
provides:
  - scripts/files_eval.py with check, run and summary commands
  - tests/test_files_eval.py (9 tests)
affects: [26-07]
tech-stack:
  added: []
  patterns: [isolated app copy on 18000/18001, scratch clone as write root, honest not-run records]
key-files:
  created: [scripts/files_eval.py, tests/test_files_eval.py]
  modified: []
key-decisions:
  - "Unexpected scenario errors are saved as status failed and the driver continues; exit 1 then, 2 for blocked/not run"
  - "Scratch paths are scrubbed from every saved record in addition to the clone path"
requirements-completed: [PFILE-07, PFILE-08, PFILE-09]
duration: 20min
completed: 2026-10-17
---

# Phase 26 Plan 06: Scenario driver Summary

**`scripts/files_eval.py` runs the four fixed D-15 scenarios (usages, adr, changelog, readme) on an isolated app copy against a reset-per-scenario git clone, local model only, saving run.json/run.patch/answer.md with automatic checks per scenario.**

## Tasks

1. Tests (RED) - commit 494dca0. Collection failed before the module existed.
2. Driver (GREEN) - commit 4ebfed9.

## Verification actually run

- `pytest tests/test_files_eval.py tests/test_files_task.py -n0`: 22 passed.
- `python scripts/files_eval.py --help`: exit 0.
- `python scripts/files_eval.py check`: printed "isolated run: ok" (ports free, git present, LM Studio chat model loaded), exit 0, no app started.
- Greps: `"--allow-write"` once, no "deepseek", 0 `shell=True`, `files_task.run_goal` and `root=clone` present.
- NOT run: `python scripts/files_eval.py run` (a live four-scenario local-model run); the live path (app start, server registration, run_goal, saved artifacts) is therefore verified only by the unit tests of its pure parts and code review. The run belongs to plan 26-07.

## Deviations from Plan

None in behavior. Added helper `save_not_run` and `save_failed` (small wrappers around `save_artifacts`) so not-run and failed records are scrubbed of the scratch path.

## Known Stubs

None.

## Self-Check: PASSED
