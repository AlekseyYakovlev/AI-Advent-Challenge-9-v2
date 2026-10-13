---
phase: 22-project-mcp-servers-day-31
plan: 06
subsystem: testing
tags: [mcp, github, read-only, checks]
requires: ["22-02", "22-04", "22-05"]
provides:
  - "22-CHECKS.md: GHUB-02 section (binary version, tool listing, real-binary pytest, status) and final full-suite section"
affects: [23]
key-files:
  created: []
  modified:
    - .planning/phases/22-project-mcp-servers-day-31/22-CHECKS.md
key-decisions:
  - "The token was loaded from the local .env into the test process environment only; it was never printed, logged or written"
requirements-completed: [GHUB-01, GHUB-02]
duration: 25min
completed: 2026-10-13
---

# Phase 22 Plan 06: GHUB-02 real-binary check and final suite Summary

The real `github-mcp-server` v2.0.2 in `--read-only --toolsets=repos,pull_requests` mode lists exactly the 16 expected read-only tools with no write tool, and the real-binary pytest file (3 tests, including a read call with the real token) passes; the final full suite has one unrelated failure.

## Task outcomes

- Task 1 (human-action checkpoint): already done by the user before this run (binary at `C:\Users\Aleksey\go\bin`, token in `.env`); confirmed, no token handled by this plan.
- Task 2 (c266dbe): `## GHUB-02 GitHub server` recorded.
  - Binary: version 2.0.2, commit eb47a99, SHA-256 `1BBF1CD5...77C1DF` (full hash in 22-CHECKS.md).
  - Listing: passed (exit 0, 16 tools equal to the expected set, none of the six write tools).
  - Pytest: passed (run by the executor; `3 passed, 2 warnings in 1.45s`).
  - Status: passed.
  - In-app connection in Settings: not run (the user did not report it); labelled as such. Masking is covered by the always-on test `test_github_token_value_is_never_returned_by_api`, which passed.
  - Documentation guard `tests/test_docs_mcp_servers.py`: 63 passed.
- Task 3 (f8abb8d): `## Test suite` recorded. `pytest tests/ -q -rs`: `1 failed, 2945 passed, 11 skipped, 31 warnings in 535.85s`. Suite: failed.
  - Failing test: `tests/test_kb_api.py::test_create_happy_path` (knowledge-base upload, unrelated to MCP). It passes when run alone; the failure depends on state left over in the full run. Not investigated or fixed, since this plan changes only `22-CHECKS.md`.
  - Skips: 10 real-PDF tests, 1 live DeepSeek test. The real-binary GitHub tests did not skip.

## Deviations from Plan

- The token was supplied by the orchestrator through `.env` instead of the user's own terminal, so the pytest line is `Pytest: passed (run by the executor)`; the plan's rule allows this when both real tests pass here. The value stayed in the process environment only.

## Open verification items

- Full suite: `Suite: failed` due to `tests/test_kb_api.py::test_create_happy_path` (passes in isolation; likely stale upload-directory state). Needs a look outside Phase 22.
- PGIT-04 (browser run of the local-git branch question): not run because LM Studio was not available (carried over from 22-05-SUMMARY.md). Repeat with LM Studio and a chat model loaded.
- GHUB-02 in-app connection in Settings: not run (no user report); the same path is covered by the passing automated tests.

## Self-Check: PASSED

22-CHECKS.md exists with both sections; commits c266dbe and f8abb8d exist; no token-shaped string in any file (verified by the verify command and the docs guard).
