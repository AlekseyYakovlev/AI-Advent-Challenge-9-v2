---
phase: 22-project-mcp-servers-day-31
plan: 02
subsystem: mcp
tags: [mcp, github, tests, read-only, token-masking]
requires: []
provides:
  - "tests/test_mcp_real_github.py: real github-mcp-server read-only check (skipif gated) and always-on token masking check"
affects: [22-06]
tech-stack:
  added: []
  patterns:
    - "per-test skipif marker instead of module pytestmark so one test always runs"
key-files:
  created:
    - tests/test_mcp_real_github.py
  modified: []
key-decisions:
  - "Gate variable is GITHUB_PERSONAL_ACCESS_TOKEN, the same name the server reads; binary path overridable via GITHUB_MCP_SERVER_EXE"
requirements-completed: [GHUB-01, GHUB-02]
duration: 10min
completed: 2026-10-13
---

# Phase 22 Plan 02: GitHub MCP real-binary check Summary

`tests/test_mcp_real_github.py` drives the official `github-mcp-server.exe` (`stdio --read-only --toolsets=repos,pull_requests`) through the app's connect and dispatch path, and always proves that a saved token value is never returned by the API.

## What was built

- Commit 0d6d15b, Task 1:
  - `test_github_read_only_toolset_via_app_connect` asserts CONNECTED, read tools present, no write tools.
  - `test_github_list_branches_read_call_succeeds` dispatches `mcp__github__list_branches` and requires `ok`.
  - `test_github_token_value_is_never_returned_by_api` (no skip): creates a disabled server with a fake token and checks only `env_keys` is returned on create and list.
  - Token comes only from the environment; assertion messages are fixed strings.

## Verification (observed)

- `pytest tests/test_mcp_real_github.py -q -rs`: 1 passed, 2 skipped (reason: `github-mcp-server.exe or GITHUB_PERSONAL_ACCESS_TOKEN is missing`).
- `pytest tests/test_mcp_api.py tests/test_mcp_real_filesystem.py -q`: 23 passed.
- Token-shape regex check printed `ok`; no `pytestmark`, `print(`, `ghp_`, `github_pat_` in the file.
- NOT verified: the two real-binary tests were skipped (no binary or token here). They are run in Plan 22-06.

## Deviations from Plan

None in code. Environment note: another pytest process (a KB test run) held `test_app.db` open, so verification runs used `DB_PATH=scratch_gh_test.db` (removed afterwards).

## Self-Check: PASSED

- tests/test_mcp_real_github.py: FOUND
- commit 0d6d15b: FOUND
