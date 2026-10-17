# Phase 24 checks

## 1. GitHub MCP live check (A1, A2, A7)

Status: passed

Date: 2026-10-17 (re-run during verify-work)

Binary `C:\Users\Aleksey\go\bin\github-mcp-server.exe` version 2.0.2, args `stdio --read-only
--toolsets=repos,pull_requests`, read-only token from `GITHUB_PERSONAL_ACCESS_TOKEN` in `.env`.
Target: control PR #5 (`review-test/large`, closed, 12 changed files).

| Call | isError | items | chars | keys |
|------|---------|-------|-------|------|
| `list_pull_requests` (state all, perPage 5) | false | 5 | 4226 | base, body, closed_at, created_at, draft, head, html_url, merged, number, state, title, updated_at, user |
| `pull_request_read` `get` (#5) | false | - | 922 | additions, base, body, changed_files, closed_at, comments, commits, created_at, draft, head (ref, repo, sha), html_url, mergeable_state, merged, number, state, title, updated_at, user |
| `pull_request_read` `get_files` (page 1, perPage 100) | false | 12 | 231481 | additions, changes, filename, patch, status |
| `pull_request_read` `get_diff` | false | - | 221860 | one text item starting with `diff --git` |
| `pull_request_read` `get` for 999999 (not found) | true | - | 132 | text `failed to get pull request: GET .../pulls/999999: 404 ...` |

Confirmed: A1 (field names of `get` and `get_files` match the parser; `changed_files` equals the `get_files`
count), A2 (a missing PR is `isError` with `404` in the text), A7 (a diff of 221 860 characters arrives complete,
far over the 20 000-character tool-result cut). No `previous_filename` in this PR (no renames); the parser treats
it as optional. Real-binary test `tests/test_review_github.py::test_real_binary_reads_a_pull_request` with
`GITHUB_REVIEW_TEST_PR=5`: 1 passed.

### Earlier record (2026-10-13, superseded)

Status then: не запускалось. Reason: the environment variable `GITHUB_PERSONAL_ACCESS_TOKEN` is not set in the executor session (the binary
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

## 4. Control run

### 4.1 Preconditions (2026-10-17)

- `Day32` = `origin/Day32` = 44680c7; section 2 holds `push Day32: yes`.
- `GET /api/v1/reviews/config`: `effective.provider_kind` = `lm_studio`, provider LM Studio, model `qwen/qwen3.5-9b`, context_length 65536.
- Runner: Idle after the smoke runs; it picked up all five jobs.
- Manifest `tests/fixtures/review/control_set.json` last changed in commit fd72730da1e4954ac4c9fe787a41d217d48ca1a6 (frozen from here on).

### 4.2 Local-model run (run `local`)

Provider/model in force: LM Studio / `qwen/qwen3.5-9b`. Base of every PR: `Day32`.

| id | PR | status | model line | parts note | reason |
|----|----|--------|------------|------------|--------|
| bug | #2 | ok | LM Studio/qwen/qwen3.5-9b | no | - |
| rules | #3 | ok | LM Studio/qwen/qwen3.5-9b | no | - |
| arch | #4 | ok | LM Studio/qwen/qwen3.5-9b | no | - |
| large | #5 | ok | LM Studio/qwen/qwen3.5-9b | Ревью выполнено по частям: 2. | - |
| clean | #6 | ok | LM Studio/qwen/qwen3.5-9b | no | - |

Duration per PR was not captured (`seconds` and `workflow_conclusion` are `null` in the records, as in the smoke run). The `large` PR was reviewed in 2 parts (per-file path confirmed). Review texts are saved in `eval_out/day32/local/`.

### 4.3 Switch to DeepSeek (Task 2)

User answer: "switched". `GET /api/v1/reviews/config` (read-only): `effective.provider_kind` = `openai`, provider DeepSeek (id 2), model `deepseek-flash`.

### 4.4 DeepSeek run (run `deepseek`)

Provider/model in force: DeepSeek / `deepseek-flash`. Re-run via `rerun --after-run local` (one new commit with unchanged tree per test branch).

| id | PR | status | model line | comment_id same as local | new head_sha | parts note |
|----|----|--------|------------|--------------------------|--------------|------------|
| bug | #2 | ok | DeepSeek/deepseek-flash | yes | 530eef5 | no |
| rules | #3 | ok | DeepSeek/deepseek-flash | yes | f399dd4 | no |
| arch | #4 | ok | DeepSeek/deepseek-flash | yes | cd3c65f | no |
| large | #5 | ok | DeepSeek/deepseek-flash | yes | 045bcc1 | Ревью выполнено по частям: 2. |
| clean | #6 | ok | DeepSeek/deepseek-flash | yes | 3d37e0a | no |

Duration per PR not captured (`seconds` null). No `.md` is byte-identical to the local run. Secret grep over `eval_out/day32`: nothing found.

## 5. Judge

Date: 2026-10-17

### 5.1 Preflight

- `python scripts/review_judge.py --check` (default model `deepseek-chat`): `check: model deepseek-chat is not available; available: ['deepseek-flash', 'deepseek-v4-pro']`, exit code 2. The default judge model id no longer exists on the DeepSeek API, so the model was passed explicitly with `--model`.
- `python scripts/review_judge.py --check --model deepseek-v4-pro`: `check: ok model=deepseek-v4-pro`, exit 0.
- `python scripts/review_judge.py --check --model deepseek-flash`: `check: ok model=deepseek-flash`, exit 0.

### 5.2 Judging passes

- Pass 1 with `deepseek-v4-pro`: 9 of 10 records `ошибка разбора`, pass 2 with `deepseek-flash`: 6 of 10 `ошибка разбора`. Cause found with a one-off debug call (not kept): with `JUDGE_MAX_TOKENS = 900` the reasoning DeepSeek models spend the whole budget on reasoning and return an empty reply, so the parser had nothing to parse. This is a defect of the judge script, not a verdict of the judge.
- Fix: `JUDGE_MAX_TOKENS` in `scripts/review_judge.py` raised from 900 to 8000. The whole judging pass was then repeated once for both runs (all ten PR records, not selected ones), judge model `deepseek-flash`. Result: all ten records `judge_status: ok`. The earlier passes were overwritten; no record was re-judged to improve a verdict.
- The judge and the author of the DeepSeek run are the same provider and even the same model id (`deepseek-flash`); the sentence about it is in `tables.md`.

### 5.3 Spot-check (reading the review texts, verdicts not changed)

| Run | PR | Key | Judge | Executor reading |
|-----|----|-----|-------|------------------|
| local | bug | B1 | найден | agree (end = start + page_size - 1 named, fix given) |
| local | arch | A2 | найден | agree (multiprocessing.Process named as wrong for the async server) |
| local | bug | B3 | не найден | agree (no mention of `_chunk_rows` arguments) |
| local | arch | A1 | не найден | agree (no mention of the `ui` -> `agent.state` import) |
| deepseek | bug | B3 | найден | agree (swapped `_chunk_rows` arguments named) |
| deepseek | arch | A1 | найден | agree (`ui` imports `agent.state`) |
| deepseek | arch | A2 | не найден | agree with reservation: `multiprocessing.Process` is mentioned, but only for the garbage-collection and join problem, not as a project-forbidden mechanism |
| deepseek | large | L1 | не найден | agree (nothing about the removed disabled-provider check) |

No disagreement with the judge on the sampled items. Note on the local B2: the review text puts the empty-list division in the recommendations section with a wrong explanation (`len/len` gives 1.0); the judge counted it as found, which is generous.

### 5.4 Observation on false remarks

Most false remarks of the DeepSeek run (and several of the local run) claim a syntax error from the symbols `-›`, `‹`, `›` in the changed code. The code has `->`, `<`, `>`. `agent/review_prompts.py` (line 65) replaces `<` and `>` with `‹` and `›` in every text put into the prompt, so the models saw the substituted symbols. This is a property of the pipeline prompt-injection guard and is shown in the report; it is not changed by this plan.
