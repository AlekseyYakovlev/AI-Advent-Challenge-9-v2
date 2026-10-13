---
phase: 22-project-mcp-servers-day-31
plan: 05
subsystem: testing
tags: [e2e, playwright, mcp, git, checks]
requires: ["22-03"]
provides:
  - "scripts/e2e_mcp_git_playwright.py: PGIT-04 browser run on an isolated copy (ports 18000/18001)"
  - "22-CHECKS.md: recorded evidence for PGIT-01..03 and the PGIT-04 outcome"
affects: [22-06, 23]
tech-stack:
  added: []
  patterns:
    - "e2e script reuses the isolated-copy helpers of e2e_kb_playwright.py"
key-files:
  created:
    - scripts/e2e_mcp_git_playwright.py
    - .planning/phases/22-project-mcp-servers-day-31/22-CHECKS.md
  modified: []
key-decisions:
  - "22-CHECKS.md is the evidence file for D-19 (D-19 names the phase verification document); 22-VERIFICATION.md is written later by the phase verifier and references 22-CHECKS.md"
requirements-completed: [PGIT-03]
duration: 25min
completed: 2026-10-13
---

# Phase 22 Plan 05: local-git browser check and recorded checks Summary

A Playwright script that adds `local-git` through the «MCP серверы» form, asks a real local model for the branch, switches the branch in a scratch repository and requires the answer to follow; it was written and syntax-checked, but the end-to-end run is NOT RUN because LM Studio is not running.

## What was built

- Task 1 (109743f): `scripts/e2e_mcp_git_playwright.py` (359 lines). Reuses `prepare_copy`, `seed_scratch_users`, `wait_for_app`, `login`, `teardown`, `port_is_free` from `e2e_kb_playwright.py`. Scratch repository helpers assert the path is outside `REPO_ROOT`. Up to two attempts, `RESULT: PASS/FAIL/NOT RUN` final line, exit codes 0/1/2/4.
- Task 2 (ee94f57): `22-CHECKS.md` with PGIT-01 (listing), PGIT-02 (pytest and a live `../x` rejection), PGIT-03, PGIT-04 and two sections left pending for Plan 22-06.

## Observed results

- `python scripts/mcp_list_tools.py ...`: exit 0, server `local-git`, 5 tools.
- `pytest tests/test_mcp_project_git_core.py tests/test_mcp_project_git.py -q`: 121 passed.
- `pytest tests/test_port_literals.py -q`: 2 passed.
- `python scripts/e2e_mcp_git_playwright.py`: exit 2, `BLOCKED: LM Studio not available (ConnectError)`, `RESULT: NOT RUN (...)`.
- PGIT-04 status as recorded: `not run` (exit code 2, no model id available).
- Working copy still on `Day31`; nothing touched on ports 8000/8001.
- The full `pytest tests/` run is recorded by Plan 22-06, not by this plan.

## Deviations from Plan

**1. [Rule 1 - Bug in plan assumption] Card text check.** The UI card summary shows `local-git · git_current_branch — ok` (server and tool separately), not the string `mcp__local-git__git_current_branch`. The script accepts the full name or the server and tool name together in the card text; the full name constant remains in the script.

**2. [Rule 3] Helper reuse by import.** The script imports the helpers from `e2e_kb_playwright.py` (as `e2e_rag_playwright.py` does) instead of copying them; the port constants are declared locally.

## Assumption Drift (advisory)

- Found during: Task 1. Planned: card named `mcp__local-git__git_current_branch`. Actual: card shows server and tool names separately. Why: `buildToolCallCard` in `ui/static/app.js`.

## Open verification items

- PGIT-04 and ROADMAP success criterion 3 are NOT confirmed. Reason: `BLOCKED: LM Studio not available (ConnectError)`. The scenario code itself has never run against a real model, so selectors and the card check are untested in a live browser.
- Follow-up: start LM Studio with a chat model loaded, keep ports 18000/18001 free, run `python scripts/e2e_mcp_git_playwright.py`, and add the dated result below the earlier record in the `## PGIT-04 browser run` section of `22-CHECKS.md` (earlier record is kept).

## Known Stubs

None.

## Self-Check: PASSED

Both files exist; commits 109743f and ee94f57 exist.
