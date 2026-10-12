---
phase: 20-limits-verification-script-and-report-day-30
plan: 07
subsystem: documentation
tags: [docs, testing-guide, limits, source-guards]
requires: ["20-02", "20-03", "20-04", "20-05"]
provides:
  - "API_SPEC, ARCHITECTURE, USER_GUIDE and TESTING_GUIDE describe limits, queue, lockout, route guard and verify_service.py as built"
  - "tests/test_docs_day30.py guards tying the documents to the code and to the test files that exist (VER-06)"
key-files:
  modified: [docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/USER_GUIDE.md, docs/TESTING_GUIDE.md, tests/test_docs_day30.py]
requirements-completed: [VER-06]
completed: 2026-10-12
---

# Phase 20 Plan 07: Limits documentation and VER-06 guards Summary

The four documents now describe the per-chat message limit, the generation queue, the login lockout (with its known limits), the guarded `/debug/routes` and the verification script as the code builds them, the stale statements are gone, and 19 new guard tests keep the documents tied to the code.

## Commits
- fd425ea: API_SPEC (Limits section, 429 row and example, `queued`/`RATE_LIMITED` frames, environment rows, `usage_percent`), ARCHITECTURE (limits and generation queue section, known limits, stale lines), USER_GUIDE (Limits, Checking the service from another machine, 429 error, route-guard note)
- df89ca7: TESTING_GUIDE "Limits (Day 30)" section with coverage map, and the extended `tests/test_docs_day30.py`

## Verification (observed)
- `pytest tests/test_docs_day30.py -q`: 44 passed (25 before this plan, so 19 more).
- Task 1 verify one-liner (stale strings absent, needles present in all three documents): printed `ok`.
- `pytest tests/ -q`: 2572 passed, 11 skipped, 0 failed.
- `grep -rnE "aia_[A-Za-z0-9_-]{30,}" docs/`: no output.
- `git log --name-only b612663..HEAD`: only the five files of `files_modified`.
- Every flag named in the USER_GUIDE script section is checked against `build_parser()` by `test_user_guide_limits_and_script`.
- Not run: the live second-machine run and `scripts/e2e_limits_playwright.py` (plan 20-06 / 20-08). Documentation of the e2e script was written from the 20-06 plan, because `20-06-SUMMARY.md` did not exist yet.

## VER-06 audit
The coverage map in the guide lists bind host, Origin, API keys, HTTP chat and limits with their test files; `test_testing_guide_day30_files_exist` checks every `test_*.py` named in the three Day 30 sections exists. No missing test file or uncovered area was found.

## Deviations from Plan
- [Rule 2] Added `chat_id` to the `GET /stats` example in API_SPEC, because `test_api_spec_stats_keys_match_code` showed `compute_chat_stats` returns that key and it was undocumented.
- Worktree base was corrected with `git reset --hard b612663` per the startup check (the worktree started at 06402e3).
- The "Changed tests" lines in the guide were written from the actual diffs of those test files (for example `test_network_info.py` only reads `/debug/routes` with an authenticated client), not from the plan's wording.

## Assumption Drift (advisory)
- Found during: Task 2. Planned: the `fast` rate mode and `--rate-max-attempts` described only by the plan text. Actual: the guide describes `fast` as the overflow-chat sequence read from `scripts/verify_service.py`. Why: the code is the source of truth.

## Known Stubs
None.

## Behaviour changes from plan 20-06
Plan 20-06 ran in parallel and its SUMMARY was not available, so any behaviour fix it makes is not reflected here; plan 20-08 should re-check the documents against it.

## Threat Flags
None.

## Self-Check: PASSED
All five files exist; commits fd425ea and df89ca7 exist; STATE.md and ROADMAP.md not modified.
