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

Run 2 (2026-10-13, during `/bm:verify-work 22`), same command, LM Studio running
Exit code: 1
Model id: qwen/qwen3.5-9b

```
PASS: local-git added through the «MCP серверы» form
FAIL: scenario run aborted - Error: Page.evaluate: TypeError: Cannot read properties of undefined (reading 'value')
RESULT: FAIL (model qwen/qwen3.5-9b)
```

Cause: a defect in the check script, not in the app. `select_chat_model` read the `#model-select` options before `loadModels()` had filled the picker (the failure screenshot still showed the «Загрузка моделей…» placeholder). Fix: the script now waits for an option naming the model before selecting it.

Run 3 (2026-10-13), same command after the script fix
Exit code: 0
Model id: qwen/qwen3.5-9b

```
PASS: isolated copy patched to ports 18000/18001
PASS: isolated app instance is up (UI :18000 + Agent :18001)
PASS: local-git added through the «MCP серверы» form
PASS: first question: new tool-call card for the branch tool - 🔧 local-git · git_current_branch — ok Аргументы{} Результат Branch: alpha
PASS: first answer names the branch alpha - Проект находится на ветке alpha.
PASS: second question: a second new tool-call card - 🔧 local-git · git_current_branch — ok Аргументы{} Результат Branch: beta
PASS: second answer follows the switch to beta - Проект сейчас находится на ветке beta.
PASS: isolated ports are free again
RESULT: PASS (attempt 1 of 2, model qwen/qwen3.5-9b)
```

Status: passed (Run 3)

## GHUB-01 token mask in the Settings form (browser)

Date: 2026-10-13, during `/bm:verify-work 22`. One-off Playwright run on an isolated copy (ports 18000/18001, scratch database, removed afterwards); the script was a throwaway and is not in the repository. The real token was read from the local `.env` into the form only; its value was never printed.

```
PASS: github connected and lists 16 tools - github подключено ... github-mcp-server · v2.0.2 · MCP 2025-11-25 ▸ Инструменты (16)
PASS: form shows the token masked - GITHUB_PERSONAL_ACCESS_TOKEN=•••
PASS: token value absent from the page DOM
PASS: token value absent from the frontend state
PASS: token value absent from every API response body - 26 responses
PASS: re-saving the masked form keeps the stored token (still 16 tools)
PASS: token value absent from the app log
RESULT: PASS
```

Note: saving the form does not connect the server; «Подключить» was pressed after saving, as a user would.

Status: passed

## Test suite

Date: 2026-10-13. Commit at the time of the run: c266dbe (after Plans 22-04 and 22-05 were complete).
Command: `pytest tests/ -q -rs` (scratch DB_PATH; the GitHub token loaded into the process environment from the local `.env`, value never printed)
Exit code: 1

```
1 failed, 2945 passed, 11 skipped, 31 warnings in 535.85s (0:08:55)
```

Failing test: `tests/test_kb_api.py::test_create_happy_path` (`assert all("a.txt" not in n and "b.md" not in n for n in on_disk)` at line 96). The test is in the knowledge-base upload area and does not touch MCP code. Re-run alone, it passes (`1 passed`), so the failure depends on leftover state from the full run (the on-disk upload directory for user 1 / job 1 can hold files from earlier runs); the cause was not investigated further because this plan changes no test or source file.

Skipped tests (reasons as printed by `-rs`): 10 in `tests/test_kb_real_pdfs.py` (real PDF not available); 1 in `tests/test_live_deepseek_title.py` (live paid DeepSeek call; set RUN_LIVE_DEEPSEEK=1). The real-binary GitHub tests were not skipped in this run (the token was available).

Suite: failed (tests/test_kb_api.py::test_create_happy_path; unrelated to Phase 22, passes in isolation)

## GHUB-02 GitHub server

### Binary

`C:\Users\Aleksey\go\bin\github-mcp-server.exe` exists. `github-mcp-server.exe --version`: GitHub MCP Server, Version 2.0.2, Commit eb47a99ddb866ca2b8a162920e6bda9521f33ebb, Build Date 2026-10-08T11:26:40Z.
SHA-256 of the exe (Get-FileHash): `1BBF1CD5BE216D5C622CCB0DA4529D747C983FCF6BBF76F9C3E6C3B32877C1DF`.

### Tool listing (scripts/mcp_list_tools.py)

Command: `python scripts/mcp_list_tools.py C:\Users\Aleksey\go\bin\github-mcp-server.exe stdio --read-only --toolsets=repos,pull_requests` (no token passed; the server started and listed without it).
Exit code: 0

```
Server: github-mcp-server
Version: 2.0.2
Protocol: 2025-11-25

Tools (16):

- get_commit
- get_file_contents
- get_latest_release
- get_release_by_tag
- get_tag
- list_branches
- list_commits
- list_pull_requests
- list_releases
- list_repository_collaborators
- list_tags
- pull_request_read
- search_code
- search_commits
- search_pull_requests
- search_repositories
```

(Descriptions omitted here; server name, version, count and the 16 tool names are verbatim from the script output.)

16 tools listed; the set equals the 16 expected names exactly (no difference). None of the write tools `create_pull_request`, `merge_pull_request`, `update_pull_request`, `create_branch`, `create_or_update_file`, `push_files` is present.

Listing: passed

### Real-binary pytest (tests/test_mcp_real_github.py)

Command: `pytest tests/test_mcp_real_github.py -v -rs`, run by the executor with the token loaded from the local `.env` into the process environment (value never printed).

```
tests/test_mcp_real_github.py::test_github_read_only_toolset_via_app_connect PASSED [ 33%]
tests/test_mcp_real_github.py::test_github_list_branches_read_call_succeeds PASSED [ 66%]
tests/test_mcp_real_github.py::test_github_token_value_is_never_returned_by_api PASSED [100%]
======================== 3 passed, 2 warnings in 1.45s ========================
```

Pytest: passed (run by the executor)

### In-app connection (reported by the user)

not run: the user did not report an in-app connection in Settings in this session. The masking is covered automatically by the always-on test `test_github_token_value_is_never_returned_by_api` (PASSED in the run above); the read call `test_github_list_branches_read_call_succeeds` (PASSED) went through the app's connect and dispatch path with the real token.

### Status

Status: passed

For Phase 23: copy the `Listing: passed`, `Pytest: passed` and `Status: passed` lines of this section, together with the release version 2.0.2, into `Day31_report.md`.
