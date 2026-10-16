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

## 2. Runner security decision

Date: 2026-10-14 (UTC)

Decision: public-safe-config
push Day32: yes

The residual risk (approval gating is not airtight; the in-file same-repository condition can be edited by a PR; a private repository is the only complete fix) was shown to the user before the answer.

Branch state checked before the answer was recorded: `Day31` = `origin/Day31` = `main` = `origin/main` = 0b53b7c; `git log Day32..Day31` is empty, so `Day32` already contains all of Day 31.

### Setup checklist (Task 2, steps by the user)

| Step | Status |
|------|--------|
| 1. Private repository | skipped (decision is public-safe-config) |
| 2. Day31 commits missing from Day32 | not needed (0 commits) |
| 3. Actions settings: approval for all external contributors, read-only token | done (user) |
| 4. App from Day32 checkout and LM Studio | done (user); /health 200 |
| 5. API key github-actions-review, MCP server github, review settings | done (user); /api/v1/reviews/config: repo_configured true, docs_kb 2, LM Studio, model qwen/qwen3.5-9b; github_server_id null |
| 6. Secret PR_REVIEW_API_KEY | done (user) |
| 7. Self-hosted runner with label pr-review | done (user); Idle, served the smoke runs |
| 8. Environment variables for the control script | done: PR_REVIEW_API_KEY and GITHUB_PERSONAL_ACCESS_TOKEN set in session, DEEPSEEK_API_KEY set in .env |

## 3. Smoke run on a real PR

### 3.1 Test suite before push (2026-10-17)

`python -m pytest tests/ -q`: 3539 passed, 12 skipped, 1 failed (685 s). The failure is `tests/test_kb_api.py::test_create_happy_path` (knowledge-base API from an earlier phase, not touched by Phase 24); the same test passes when run alone (1 passed), so it is an order-dependent flake in the full run. Reported to the user before the push.

### 3.2 Documentation base

`python scripts/review_control.py prepare-docs` exit 0: 6 files sent, 7 in the base; `eval_out/day32/docs_kb_files.json` lists `CLAUDE.md`, `README.md`, `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md` and the existing file.

### 3.3 Smoke PR (run `smoke`)

- Push: `git push -u origin Day32` (normal push), pushed commit b4cfe1d.
- PR #1 (branch `review-test/smoke`, head 6f670f0). Result `ok`.
- Comment exists with the `<!-- ai-pr-review -->` marker, the three headings, `Коммит: 6f670f0` (equals PR head) and `Модель: LM Studio/qwen/qwen3.5-9b`.
- Sources listed: 4 documentation sections, 6 code ranges (neither list empty).
- Comment length: 1756 characters (A4: подтверждено, far below any limit).
- A3: подтверждено — workflow `permissions` hold only `contents: read` and `pull-requests: write`, and the comment was posted.
- A6: python was found on the runner (the job ran and posted the comment).
- `workflow_conclusion` and `seconds` are `null` in `smoke.json` (not returned by the control script's collect); recorded as not captured.
- The review found the planted `›` instead of `->` typo.

### 3.4 Replacement check (D-14, run `smoke2`)

After `rerun` the PR head is bb09d53. `smoke2.json` has the same `comment_id` 6225967607 as `smoke.json`, `Коммит: bb09d53`, the marker appears once. The comment was replaced, not duplicated.

### 3.5 Fallbacks

None needed (no 403, no size rejection, no job failure). `python -m pytest tests/test_pr_review_ci.py -q`: 34 passed.
