# Phase 22: Recorded checks

Date: 2026-10-13. Branch: Day31. Commit at the time of the run: 109743f. git 2.39.1.windows.1, Python 3.13.15.

This file is the evidence file for D-19 (D-19 names "the phase verification document"; `22-VERIFICATION.md` is written later by the phase verifier and references this file).

## PGIT-01 tool listing

Command: `python scripts/mcp_list_tools.py python mcp_servers/project_git.py --root=C:/Projects/AiAdventAgentV2`
Exit code: 0

```
Server: local-git
Version: 1.30.0
Protocol: 2025-11-25

Tools (5):

- git_current_branch: LOCAL working copy (not the GitHub remote): report the branch that is checked out now.
- git_status: LOCAL working copy (not the GitHub remote): list changed and untracked files.
- git_diff: LOCAL working copy (not the GitHub remote): show a diff of tracked files.
- git_tracked_files: LOCAL working copy (not the GitHub remote): list files tracked by git.
- git_recent_commits: LOCAL working copy (not the GitHub remote): list the most recent commits.
```

(Descriptions shortened to their first line here; the parameter documentation printed by the script is omitted. The five tool names, the server name and the count line are verbatim.)

Status: passed

## PGIT-02 rejected calls and truncation

Command: `pytest tests/test_mcp_project_git_core.py tests/test_mcp_project_git.py -q` (scratch DB_PATH)
Result: `121 passed, 3 warnings in 34.72s`

Covering tests, by name:

- path outside the root: `test_path_gate_rejects_traversal`, `test_path_gate_rejects_absolute_outside`, `test_path_gate_rejects_link_leaving_root`, `test_stdio_path_outside_root` (5 passed with the other stdio tests below)
- argument starting with `-`: `test_path_gate_rejects_leading_dash`, `test_ref_syntax_rejects`, `test_resolve_ref_never_starts_git_for_option_shape`, `test_stdio_ref_starting_with_dash`
- unknown ref: `test_resolve_ref_unknown`, `test_unknown_ref_message`, `test_stdio_unknown_ref`
- timeout with process-tree kill: `test_run_command_timeout_kills_process_tree` (3 passed together with the leading-dash tests)
- truncated diff with marker: `test_fit_text_truncates_with_marker`, `test_compose_truncated_diff_order_and_size`, `test_diff_truncates_large_patch`, `test_stdio_truncated_diff`

Live example against the real repository (`agent.mcp_client.open_session`, `git_diff` with `{"path": "../x"}`):

```
isError= True
text= Error executing tool git_diff: path must stay inside the repository root; use a relative path such as agent/ws.py
```

Status: passed

## PGIT-03 chat tool path

`tests/test_mcp_project_git.py::test_chat_dispatch_returns_branch` runs `mcp__local-git__git_current_branch` through `dispatch_tool_calls`. Observed: `5 passed, 50 deselected` for the selection `chat_dispatch or stdio_path_outside or stdio_ref_starting or stdio_unknown or stdio_truncated`, which includes this test.

Status: passed

## PGIT-04 browser run

Run 1 (2026-10-13), command: `python scripts/e2e_mcp_git_playwright.py`
Exit code: 2
Model id: none (no model could be queried)

```
BLOCKED: LM Studio not available (ConnectError)
RESULT: NOT RUN (LM Studio not available (ConnectError))
```

No check line was produced: the run stopped in preflight, before the isolated copy was started. Neither the form, the chat nor the branch switch was exercised. The script's syntax and the `tests/test_port_literals.py` guard (2 passed) were checked; the end-to-end scenario itself has never run.

Status: not run

To repeat: start LM Studio with a chat model loaded, keep ports 18000/18001 free, run `python scripts/e2e_mcp_git_playwright.py` and add the dated result below this record.

## Test suite

Status: pending (recorded by Plan 22-06)

## GHUB-02 GitHub server

Status: pending (recorded by Plan 22-06)
