---
phase: 24-automated-pr-code-review-day-32
plan: 04
subsystem: ci
tags: [github-actions, pr-review, self-hosted-runner, stdlib]
requires: ["24-01"]
provides:
  - ".github/workflows/pr-review.yml"
  - "scripts/pr_review_ci.py (exports MARKER, BOT_LOGIN, COMMENT_MAX_CHARS, REVIEW_TIMEOUT_SECONDS, GITHUB_TIMEOUT_SECONDS)"
key-files:
  created:
    - .github/workflows/pr-review.yml
    - scripts/pr_review_ci.py
    - tests/test_pr_review_ci.py
requirements-completed: [REV-01]
metrics:
  completed: 2026-10-13
---

# Phase 24 Plan 04: PR review workflow and caller Summary

Standard-library caller script plus a least-privilege workflow that asks the local app for a PR review and publishes it as one replaceable comment.

## What was built

- `scripts/pr_review_ci.py`: validates env (`REPO` regex, `PR_NUMBER` int), POSTs `/api/v1/reviews` with a Bearer key, builds the Russian comment (marker, title, commit, model, three sections, collapsed sources), truncates (sources first, then sections proportionally, always <= 60000 chars), maps every failure to one UI-SPEC S3 line, finds its own earlier comment (marker AND `github-actions[bot]`, newest id, paginated at 100) and PATCHes it or POSTs a new one, writes the job summary. `defuse` neutralises `@mentions` and removes Markdown images. Secrets are scrubbed from all output, summary and published text.
- `.github/workflows/pr-review.yml`: `pull_request` (opened, synchronize, reopened, ready_for_review), permissions `contents: read` + `pull-requests: write`, concurrency without cancel, job guarded by non-draft and same-repository, `runs-on: [self-hosted, windows, pr-review]`, checkout pinned to SHA `3d3c42e5...` with `persist-credentials: false`, event data only via `env:`, no `continue-on-error`.

## Verification (actually run)

- `python -m pytest tests/test_pr_review_ci.py tests/test_verify_service.py -q`: 60 passed.
- `python scripts/pr_review_ci.py` with empty environment: prints `Ревью не выполнено: не задана переменная API_KEY.`, exit 1.
- grep gates: no `pull_request_target` in non-comment lines (0), no `${{` in `run:` lines (0), pinned checkout SHA present (covered by tests).
- Not verified: a real run on GitHub Actions / a real self-hosted runner and the real route (route built in Plan 24-07); comment size limit on GitHub (A4, Plan 24-12).

## Deviations from Plan

- [Rule 2] `GITHUB_TOKEN` is also required in the preflight (missing gives `не задана переменная GITHUB_TOKEN.`), so a review is not wasted when publishing would be impossible. Also `REPO`/`PR_NUMBER` format errors give their own Russian line.
- [Rule 3] `main()` reconfigures stdout/stderr to UTF-8 because Russian output crashed under the Windows cp1252 console.
- Worktree base was reset to 74c66fd per the startup check (HEAD was an ancestor of the expected base).
- Task 2's workflow tests live in the same test file created in Task 1, so Task 1's commit contains workflow tests that pass only once Task 2's file is committed.
- The Co-Authored-By line was omitted from commits per the project rule.

## Known Stubs

None.

## Self-Check: PASSED
