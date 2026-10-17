---
phase: 26-project-files-assistant-day-34
plan: 05
subsystem: e2e
tags: [playwright, e2e, mcp, diff-card]
requires:
  - phase: 26-02
    provides: buildToolCallCard diff region
  - phase: 26-04
    provides: write_file/edit_file and --allow-write
provides:
  - "scripts/e2e_project_files_playwright.py: isolated-copy browser check of the project files assistant"
  - "eval_out/day34/e2e.md: recorded run (RESULT: PASS)"
affects: [26-06, 26-08]
key-files:
  created:
    - scripts/e2e_project_files_playwright.py
    - eval_out/day34/e2e.md
  modified:
    - scripts/e2e_kb_playwright.py
key-decisions:
  - "Model part unavailable (LM Studio or no loaded chat model) is recorded as NOT RUN with exit 2; render checks still run"
requirements-completed: [PFILE-05, PFILE-06, PFILE-08]
completed: 2026-10-17
---

# Phase 26 Plan 05: Project files e2e Summary

Isolated-copy Playwright check (UI :18000, Agent :18001, scratch DB, write root is a `git clone --local --no-hardlinks` of the repo) that adds `project-files` with `--root=<clone>` and `--allow-write` through the real form, runs a goal in an ordinary chat on a real LM Studio model and checks write cards with diff regions, plus deterministic `buildToolCallCard` render checks.

## Tasks

1. Write the e2e script - commit b28c4db
2. Run it and record the result (plus one script fix) - commit 9447fe7

## Verification (observed)

`python scripts/e2e_project_files_playwright.py` ended with `RESULT: PASS (model qwen/qwen3.5-9b)` on the second run. Observed PASS lines: 9 render checks (meta, hunk, removed, `-- ` removed-line case, added, `· +2 −2` suffix, no region for ok:false and plain text, no img from `<img onerror>`); form add with `--allow-write`; write card visible (6 new cards); diff region in the write card; file `docs/notes/e2e-files-check.md` present in the clone; read goal produced a search card (not required); diff card present after switching to another chat and back; ports free after teardown; user's working copy `git status --porcelain` unchanged. `eval_out/day34/e2e.md` has exactly one RESULT line and 0 matches for `aia_|password`. Full-page reload persistence was not checked (out of scope per Research Pitfall 3).

## Deviations from Plan

**1. [Rule 3 - Blocking] Port guard in the copy rejected a non-port constant**
- **Found during:** Task 2 (first run, FAIL before the app started)
- **Issue:** `kb.prepare_copy` refuses any literal `8000`/`8001` in the copy; `mcp_servers/project_git.py:45` has `BINARY_SNIFF_BYTES = 8000`.
- **Fix:** added `mcp_servers/project_git.py` to the guard's skip list in `scripts/e2e_kb_playwright.py`.
- **Files modified:** scripts/e2e_kb_playwright.py
- **Commit:** 9447fe7

## Known Stubs

None.

## Self-Check: PASSED

Script, e2e.md and commits b28c4db, 9447fe7 exist.
