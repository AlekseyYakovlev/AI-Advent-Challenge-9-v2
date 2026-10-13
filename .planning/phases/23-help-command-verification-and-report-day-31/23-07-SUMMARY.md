---
phase: 23-help-command-verification-and-report-day-31
plan: 07
subsystem: verification
tags: [e2e, playwright, help-command, ver-08]
requires:
  - phase: 23-03
    provides: Settings section, badge, label and notice in the UI
  - phase: 23-04
    provides: /help over WebSocket and HTTP
  - phase: 23-05
    provides: control-run driver and threshold
provides:
  - "scripts/e2e_help_playwright.py: VER-08 run on an isolated copy, browser plus HTTP"
  - "23-CHECKS.md: recorded outcome of the end-to-end run"
key-files:
  created: [scripts/e2e_help_playwright.py, .planning/phases/23-help-command-verification-and-report-day-31/23-CHECKS.md]
requirements-completed: [VER-08]
completed: 2026-10-13
---

# Phase 23 Plan 07: /help end-to-end run Summary

An isolated-copy script drives the whole Day 31 path with the real model qwen/qwen3.5-9b and the embedding model text-embedding-bge-m3: folder upload through the real form, the git MCP server through the real form, the base marked in Settings, and `/help` in the browser and over HTTP with an API key.

## VER-08 status (as recorded in 23-CHECKS.md)

Status: passed. Run 2: exit code 0, model id qwen/qwen3.5-9b, `RESULT: PASS (model qwen/qwen3.5-9b)`, 22 checks all PASS, no retry needed. Run 1 (before the fix below) was recorded as failed, exit code 1.

Criterion 3 line exactly as written: `Criterion 3: confirmed live; after reload only label, sources and quotes are restored`

## Commits
- 6170ac8: add the end-to-end script
- 357d09a: fix the tool-call card assertion
- 54328bb: record the run in 23-CHECKS.md

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Tool-call card assertion too strict**
- **Found during:** Task 2, run 1
- **Issue:** the script required the full name `mcp__local-git__git_current_branch` in the card text; the card summary shows `local-git · git_current_branch` (server and tool). Only the check `git answer: tool card` failed; the card was present and showed `Branch: alpha`.
- **Fix:** the card is matched by server and tool names via the Phase 22 helper; the full name is asserted over HTTP in `tool_calls[].name`.
- **Files modified:** scripts/e2e_help_playwright.py
- **Commit:** 357d09a

**2. [Layout] Folder chosen as in the earlier folder script**
- The plan said to choose `project-docs`; the script picks `README.md` through the files input and the `project-docs/docs` folder through the folder control, so the pending list shows exactly `README.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md` (the chosen folder name is part of the relative path). The check passed on exactly those three paths.

## Notes
- The script reuses `git.preflight()` for ports, git and the chat model, and adds the embedding-model check.
- The temporary scratch directory of each run was removed by hand afterwards (the script's own removal left it on Windows).
- The full `pytest tests/` suite was not run here; the plan assigns it to 23-08. `tests/test_port_literals.py`: 2 passed.

## Known Stubs
None.

## Open verification items
None.

## Self-Check: PASSED
