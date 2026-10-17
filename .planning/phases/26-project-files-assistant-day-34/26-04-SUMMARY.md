---
phase: 26-project-files-assistant-day-34
plan: 04
subsystem: mcp-servers
tags: [mcp, file-write, security, diff]
requires:
  - phase: 26-01
    provides: read_file/search_text helpers, pathspec_argv, decode_text, fit_text
provides:
  - "--allow-write launch flag and ServerConfig.allow_write"
  - "write_file and edit_file tools (registered only under --allow-write) returning a unified diff"
  - "write gate: check_write_target, require_write, is_git_ignored"
affects: [26-05, 26-06, 26-08]
tech-stack:
  added: []
  patterns: ["tools registered in main() via server.add_tool only when a flag is set", "atomic write: temp file + os.replace"]
key-files:
  created: []
  modified:
    - mcp_servers/project_git.py
    - tests/test_mcp_project_files.py
key-decisions:
  - "write_file/edit_file are plain async defs added with server.add_tool in main(), so import-time list_tools stays at eight read tools"
  - "Write gate runs on the realpath-normalised rel path, so a symlink into .git is caught"
requirements-completed: [PFILE-02, PFILE-03, PFILE-04, PFILE-05, PFILE-08]
duration: ~25min
completed: 2026-10-17
---

# Phase 26 Plan 04: Write tools Summary

Own git MCP server gains `--allow-write`: `write_file`/`edit_file` save atomically behind a strict write gate (.git, .env*, ignored paths, device names, binary, oversize refused) and return `Created|Updated <path> (+A -R)` plus a unified diff.

## Tasks

1. Write gate, flag and the two write tools - commit 53f7c5b
2. Write, gate and forbidden-target tests (direct and stdio) - commit 8b11481

## Verification (observed)

- Task 1 verify command printed `ok` (8 tools at import, no writers, flag parsed).
- `pytest tests/test_mcp_project_files.py -n0`: 79 passed, 1 skipped.
- Scoped suite (project_files, project_git, project_git_core, docs_mcp_servers): 281 passed, 1 skipped.
- `scripts/mcp_list_tools.py` with `--allow-write`: "Tools (10):" including write_file and edit_file; without the flag: "Tools (8):".
- Acceptance greps: `list every file you created or changed` count 2, `print(` count 0.
- Full suite not run (gated in 26-08 per plan).

## Deviations from Plan

None - plan executed as written. Implementation was written before the tests (both tasks marked tdd) and committed as feat then test, matching the plan's commit instructions; no separate RED commit exists.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (T-26-13..T-26-18 mitigated and covered by tests).

## Self-Check: PASSED
