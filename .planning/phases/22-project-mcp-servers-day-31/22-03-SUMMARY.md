---
phase: 22-project-mcp-servers-day-31
plan: 03
subsystem: mcp
tags: [mcp, git, fastmcp, stdio, tools]
requires: ["22-01"]
provides:
  - "Five read-only git tools in mcp_servers/project_git.py (git_current_branch, git_status, git_diff, git_tracked_files, git_recent_commits)"
  - "tests/test_mcp_project_git.py: direct, stdio and chat-dispatch coverage"
affects: [22-04]
tech-stack:
  added: []
  patterns:
    - "every tool returns a one-line summary then git text, bounded by --max-chars with an explicit marker"
    - "errors raised as ToolInputError, surfaced by FastMCP as isError"
key-files:
  created:
    - tests/test_mcp_project_git.py
  modified:
    - mcp_servers/project_git.py
key-decisions:
  - "Tool names carry a git_ prefix; status body is porcelain v1; ref accepts ranges"
  - "git_diff runs at most two fixed commands (patch, then stat only when truncation is needed)"
requirements-completed: [PGIT-01, PGIT-02, PGIT-03]
duration: 35min
completed: 2026-10-13
---

# Phase 22 Plan 03: Project git MCP tools Summary

Five read-only async git tools on top of the Plan 22-01 helpers, with summary-first output, marker-based truncation within `--max-chars`, and clear MCP errors for bad arguments; proven by 55 tests directly, over real stdio and through the app's chat dispatch.

## What was built

- Task 1 (0107577): the five tools. `git_diff` is one tool with `staged`/`ref`/`path`; `git_tracked_files` falls back to a per-top-level-folder count table plus a `showing N of M files` marker; `git_recent_commits` clamps `count` to 1..50 and reports the clamp.
- Task 2 (81c426c): `tests/test_mcp_project_git.py` (55 tests): behaviour on a temporary repository (Cyrillic names, spaces, detached HEAD, empty repo), rejections (no git start for `--` arguments), CLI listing, live branch over stdio, `GIT_DIR` isolation, start-up failures (`PROCESS_EXITED` with reason), chat dispatch of `mcp__local-git__git_current_branch`, and the app cap check.

## Verification (observed)

- `pytest tests/test_mcp_project_git.py tests/test_mcp_project_git_core.py tests/test_mcp_cli.py tests/test_mcp_tools.py -q`: 149 passed.
- `pytest tests/ -q -k mcp`: 292 passed, 2 skipped.
- `python scripts/mcp_list_tools.py python mcp_servers/project_git.py "--root=C:\Projects\AiAdventAgentV2"`: `Server: local-git`, `Tools (5):`, all five tool names, description starts with `LOCAL working copy (not the GitHub remote):`.
- Forbidden-string grep on the server file: 0; required git flags present. No project_git.py process left after the run. `agent/`, `ui/`, `shared/` untouched.
- Not run: an LLM-driven chat turn (only dispatch through `dispatch_tool_calls`).

## Deviations from Plan

**1. [Rule 1 - Test fix] App-cap test uses `--max-chars=2000`.** A 400-file listing is about 7.6k characters, so it fits the default 18000 budget with no marker; the test launches the server with `--max-chars=2000` so the server's own marker is exercised and verified to stay below the app cap with `truncated` false.

**2. [Rule 3 - Blocking] Scratch DB.** Ran pytest with `DB_PATH=test_mcp_git_tools.db` (deleted afterwards) to avoid a lock on `test_app.db`.

**3. TDD order.** Tools were implemented before their tests were run; no separate RED commit.

**4. Note.** `call_mcp_tool` returns the text under the `text` key (not `content`); the test uses that. `git_diff` truncated output repeats the stat totals line (as summary and as last stat line), a cosmetic duplicate from the plan's design.

## Known Stubs

None.

## Self-Check: PASSED

Files `mcp_servers/project_git.py`, `tests/test_mcp_project_git.py` exist; commits 0107577 and 81c426c exist.
