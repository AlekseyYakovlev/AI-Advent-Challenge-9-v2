---
phase: 24-automated-pr-code-review-day-32
plan: 03
subsystem: agent
tags: [mcp, github, pull-request, review]
requires:
  - phase: 24-01
    provides: REV requirements
provides:
  - agent/review_github.py (fetch_pull_request, fetch_head_sha, resolve_github_server, PullRequestData, PrFile, GithubReviewError)
  - 24-CHECKS.md section 1 (live check record, status not run)
affects: [24-06, 24-07, 24-13]
key-files:
  created:
    - agent/review_github.py
    - tests/test_review_github.py
    - .planning/phases/24-automated-pr-code-review-day-32/24-CHECKS.md
key-decisions:
  - "Direct session.call_tool, never call_mcp_tool, so the diff is never cut at 20 000 characters"
requirements-completed: [REV-03]
duration: 15min
completed: 2026-10-13
---

# Phase 24 Plan 03: GitHub PR reader Summary

In-code PR reader over the user's GitHub MCP server: metadata, paged file list and the whole uncut diff, with three distinct error codes and Russian detail texts.

## Tasks

1. Live check (commit e810333): NOT RUN. `GITHUB_PERSONAL_ACCESS_TOKEN` is not set in the executor environment (binary exists). `24-CHECKS.md` section 1 records status "не запускалось" with the reason; A1, A2, A7 are unconfirmed and the field names follow the research. Observation points: the real-binary test (set `GITHUB_REVIEW_TEST_PR` plus the token) and the first control PR of Plan 24-13.
2. `agent/review_github.py` + tests (commit 204399a): `python -m pytest tests/test_review_github.py tests/test_mcp_tools.py -q` gave 39 passed, 1 skipped (the real-binary test). Includes a 300 000-character diff returned unchanged, 237-file paging over 3 pages, PR_TOO_LARGE, 401/404 mapping, timeout/McpError mapping, not-understood answers, repo validation, one-call `fetch_head_sha`. Source gates: no `call_mcp_tool`/`MCP_TOOL_RESULT_MAX_CHARS`, no `print(`, no token prefixes in CHECKS.

## Deviations from Plan

- [Rule 2 - Security] `repo` components `.` and `..` are rejected in addition to the regex (the regex alone accepts `../x`).
- The real-binary test needs a PR number; since 24-CHECKS.md has none (live check not run), it reads it from env `GITHUB_REVIEW_TEST_PR` and skips otherwise.
- Worktree base was behind the expected base; reset to 74c66fd as the startup check prescribes.

## Known Stubs

None.

## Self-Check: PASSED
