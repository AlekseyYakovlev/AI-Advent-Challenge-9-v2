---
phase: 26-project-files-assistant-day-34
plan: 01
subsystem: mcp
tags: [mcp, git, ast, read-tools]
requires: []
provides:
  - read_file, search_text, find_usages on mcp_servers/project_git.py
  - helpers pathspec_argv, visible_paths, is_binary, decode_text, split_lines, require_visible_file
affects: [26-04, 26-05, 26-06, 26-07, 26-08]
tech-stack:
  added: []
  patterns: [git-visibility (ls-files --cached --others --exclude-standard), git grep via run_command, stdlib ast visitor]
key-files:
  created: [tests/test_mcp_project_files.py]
  modified: [mcp_servers/project_git.py, tests/test_mcp_project_git.py, tests/test_docs_mcp_servers.py, docs/USER_GUIDE.md]
key-decisions:
  - "Day34 cut from main (rule 4): Day33 is already merged into main"
requirements-completed: [PFILE-01, PFILE-08, PFILE-10]
duration: 25min
completed: 2026-10-17
---

# Phase 26 Plan 01: Read tools for the git MCP server Summary

The own git MCP server now exposes eight tools: `read_file`, `search_text` and `find_usages` join the five git tools, all limited to files git sees (tracked plus untracked, not ignored).

## Branch

Rule 4 fired: `git merge-base --is-ancestor Day33 main` exited 0, so `Day34` was cut with `git switch -c Day34 main`. Base commit `3961673b88c64dcadf839a51717e3792fabdd7ba`. Only `.planning/` files were modified at that moment (carried over).

## Commits

- 6dbeec9: feat(26-01) three read tools in `mcp_servers/project_git.py`
- 29413d7: test(26-01) new tests, relaxed pins, user guide rows

## Verification (observed)

- Task 2 verify command: printed the eight tool names; `find_usages('cleanup_chat_caches')` first line: `6 usages of cleanup_chat_caches in 4 files (def 1, import 2, call 3)`.
- `pytest tests/test_mcp_project_files.py tests/test_mcp_project_git.py tests/test_mcp_project_git_core.py tests/test_docs_mcp_servers.py -n0`: 242 passed.
- Full suite `pytest tests/`: 3836 passed, 14 skipped.
- `scripts/mcp_list_tools.py python mcp_servers/project_git.py --root=.` ran; the pinned CLI test asserts `Tools (8):`.
- `print(` count in the server: 0; `@server.tool(structured_output=False)` count: 8.

## Deviations from Plan

### Deliberate pin relaxations (D-16 exception, as planned)

Three assertions that pinned exactly five tools were relaxed: `test_exactly_five_tools_with_local_description` (name list now `FIVE_TOOLS + READ_TOOLS`, new `READ_TOOLS` constant), `test_cli_lists_five_tools` (`Tools (8):`), and `test_user_guide_names_every_registered_tool` (`len(names) == 8`).

### Minor

**1. [Rule 3 - criterion slack]** The acceptance bound "at most 6 changed lines" for the two pin files is 7 by `git diff --stat` (5 + 2) because `READ_TOOLS` is one added line. No other line was touched.

**2. [Style]** `git grep` no-match message is built once and reused for exit 1 and for the unparsable-output case.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model; mitigations T-26-01..04 are implemented (git visibility, `resolve_inside_root`, `-e`/`--` argument placement and glob rejection, timeouts and size caps) and covered by tests.

## Self-Check: PASSED
