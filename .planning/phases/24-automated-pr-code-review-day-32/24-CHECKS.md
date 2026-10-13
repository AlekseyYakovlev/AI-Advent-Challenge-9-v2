# Phase 24 checks

## 1. GitHub MCP live check (A1, A2, A7)

Status: не запускалось

Date: 2026-10-13

Reason: the environment variable `GITHUB_PERSONAL_ACCESS_TOKEN` is not set in the executor session (the binary
`C:\Users\Aleksey\go\bin\github-mcp-server.exe` exists, version not queried). The token is taken only from the
environment and is never read from the user's running app database, so no live call was made. No process was
started, so none is left running. Nothing below is confirmed.

| Call | isError | items | chars | keys |
|------|---------|-------|-------|------|
| `list_pull_requests` (state all, perPage 5) | не запускалось | - | - | - |
| `pull_request_read` `get` | не запускалось | - | - | - |
| `pull_request_read` `get_files` (page 1, perPage 100) | не запускалось | - | - | - |
| `pull_request_read` `get_diff` | не запускалось | - | - | - |
| `pull_request_read` `get` for 999999 (not found) | не запускалось | - | - | - |

Unconfirmed assumptions (A1, A2, A7): the `get` JSON has `title`, `body`, `state`, `draft`, `head.ref`, `head.sha`,
`base.ref`, `base.sha`, `changed_files`; `get_files` returns a JSON array whose items carry `filename`, `status`,
`additions`, `deletions`, optional `patch`, optional `previous_filename`; `get_diff` returns one text item starting
with `diff --git`; a missing PR comes back as `isError` with a text containing `404` or `Not Found`; a diff longer
than 20 000 characters arrives complete. The first control PR of Plan 24-13 is the observation point; the
real-binary test in `tests/test_review_github.py` runs once the variable is set.

Conclusion for agent/review_github.py: rely on the field names listed above as the research states them, parse them
defensively (missing optional fields default to empty/False/0, a missing `head.sha` is a "not understood" error),
map `401`/`Bad credentials` and `404`/`Not Found` in error text to distinct codes, and treat any non-text content
item as an unrecognised answer.
