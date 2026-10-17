---
phase: 27-ai-release-pipeline-day-35
plan: 07
subsystem: testing
tags: [e2e, playwright, release, docs, github-scratch]
requires:
  - phase: 27-05
    provides: release plan card in the UI
  - phase: 27-06
    provides: /release over WebSocket
provides:
  - scripts/e2e_release_playwright.py (isolated-copy browser e2e of /release against a scratch GitHub repo)
  - docs for /release, the release-git entry, frames, tools, tests and the D-20 deviation
affects: [27-08]
tech-stack:
  added: []
  patterns: [env-only secrets with shared scrub, safety preflight before any write, per-scenario not-run recording]
key-files:
  created: [scripts/e2e_release_playwright.py]
  modified: [docs/USER_GUIDE.md, docs/ARCHITECTURE.md, docs/API_SPEC.md, docs/TESTING_GUIDE.md, README.md]
key-decisions:
  - "Token is registered in review_control's scrubber list so no console line or results file can contain it"
  - "A missing token records scenarios 1 and 4 (and the retry half of 2) as 'не запускалось' with reason code no_token, which does not fail the exit code"
requirements-completed: [D-04, D-10, D-15, D-16, D-17, D-18, D-20]
duration: 45min
completed: 2026-10-17
---

# Phase 27 Plan 07: Release e2e script and documentation Summary

An isolated-copy Playwright script drives the four D-18 /release scenarios against a scratch GitHub repository with the local model, refuses unsafe targets before any write, and writes a token-free results JSON; the documentation set now describes the pipeline, setup, frames, tools, tests and the D-20 deviation.

## Tasks

| Task | Commit |
|------|--------|
| 1. Contract, safety preflight, scratch repo and app helpers | 5b76946 |
| 2. Four scenarios, D-17 code check, results JSON | d3b9ac9 |
| 3. Documentation | bc1f789 |

## Verification (actually run)

- `python -m py_compile scripts/e2e_release_playwright.py`: OK.
- `--dry-check` with `RELEASE_SCRATCH_REMOTE` unset: exit 2. With the project repository as remote: exit 2 ("указывает на репозиторий проекта"). With an `https://user:pass@...` remote: exit 2.
- Acceptance greps: the three helper definitions present; 4 `def scenario_`; `changelog_coverage`, `scrub(` and `не запускалось` present; the force-push grep prints 0.
- Docs greps: `--allow-release` and `GITHUB_PERSONAL_ACCESS_TOKEN` in USER_GUIDE, `github_create_draft_release` in ARCHITECTURE and API_SPEC, `release_plan`/`release_result` in API_SPEC, `test_release_ws.py` in TESTING_GUIDE, `/release` in README; the token-prefix grep over docs and README prints nothing.
- `pytest tests/test_docs_help.py -q -n0`: 49 passed. `pytest tests/ -q -n6 -k docs`: 340 passed.
- NOT verified: the e2e scenarios themselves. Nothing was run against GitHub, LM Studio or the browser (no scratch remote or token exists yet; that is plan 27-08). Selectors, frame fields and timings are written from the app source and have never been exercised, so first-run fixes are likely.

## Deviations from Plan

None in scope. Notes:

- Task 1 `--dry-check` also requires Playwright to be importable after the safety checks (exit 4 otherwise), because the same preflight serves the full run.
- `setup_scratch_repo` creates the taken next-major tag on a short-lived side branch that is deleted locally after tagging, so the tag stays on a commit outside the release branch (D-13).
- The user's global rule (no Co-Authored-By Claude line) was followed over the attribution reminder.

## Assumption Drift (advisory)

- Found during: Task 3. Planned: step statuses described as in the plan text. Actual: the UI words are «ожидает», «выполняется», «выполнено», «пропущено», «недоступно», «ошибка»; the guide uses these. No behavior impact.

## Known Stubs

None.

## Threat Flags

None. T-27-33..T-27-36 mitigated in code: remote preflight (marker, origin owner/repo, HTTPS), env-only token with scrubbing of every written string, new `e2e-<timestamp>` branch with explicit refspecs and no force, isolated ports with only own processes stopped.

## Self-Check: PASSED

Files scripts/e2e_release_playwright.py and 27-07-SUMMARY.md exist; commits 5b76946, d3b9ac9, bc1f789 exist.
