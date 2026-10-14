---
phase: 24-automated-pr-code-review-day-32
plan: 10
subsystem: testing
tags: [pr-review, control-set, github, git-worktree, httpx]
requires:
  - phase: 24-04
    provides: comment marker, bot login and comment layout in scripts/pr_review_ci.py
  - phase: 24-07
    provides: review pipeline budget constants and code index exclusions
provides:
  - Frozen control set of six test PRs with an answer key (manifest and planted files)
  - scripts/review_control.py with prepare-docs, open, wait, collect, rerun, close, status
affects: [24-11, 24-12, 24-13, 24-14]
tech-stack:
  added: []
  patterns: [temporary detached worktree for pushes, allowlisted git sub-commands, injectable httpx client factory]
key-files:
  created:
    - tests/fixtures/review/control_set.json
    - tests/fixtures/review/prs/
    - tests/test_review_control_set.py
    - scripts/review_control.py
    - tests/test_review_control.py
key-decisions:
  - "All planted sources are stored with a .txt suffix (including .py ones), so pytest and the code index never see them"
  - "The git helper uses an allowlist of sub-commands and accepts push only to refs/heads/review-test/"
  - "A rerun/open update on an existing branch adds a commit on top of the remote head, never a forced push"
requirements-completed: [REV-07]
duration: 40min
completed: 2026-10-14
---

# Phase 24 Plan 10: Control set and control script Summary

The control set of five control PRs (plus a smoke PR) is frozen with its answer key, and one script opens them as real Pull Requests, waits for the Action, collects review texts, re-triggers and closes them without merging.

## Tasks

1. Frozen control set - commit fd72730
   - Manifest with ids smoke, bug, rules, arch, large, clean and keys B1-B3, R1-R4, A1-A2, L1-L2; every item has `needs` diff/docs/code.
   - `large` copies 11 existing modules into `agent/review_demo_big/` (estimated 61748 tokens against a one-call limit of 44952), with two edits (L1 disabled-provider check removed in providers copy, L2 `promote_index` moved before the commit in the kb_indexer copy). Each `find` occurs exactly once.
   - Guard tests: manifest shape, sources exist and parse, no defect-hinting words, edits match once, new paths use demo prefixes and do not exist in the repo, no fixture is `is_indexable` or `.md` or a collectable test file, large PR size and part count (<= MAX_PARTS).
2. Control script - commit 403a1f4
   - Subcommands, exit codes 0/1/3/4, scrubbing of both secrets, honest `не запускалось` outcomes with reasons.
   - 20 offline tests with a temporary bare origin, a dirty clone and a faked HTTP layer.

## Verification (actually run)

- `python -m pytest tests/test_review_control.py tests/test_review_control_set.py tests/test_pr_review_ci.py -q`: 61 passed.
- Forbidden-command gate (`shell=True`, `push --delete`, quoted stash/reset/switch/checkout, `/merge`) on the script: count 0.
- `python scripts/review_control.py status` with no `prs.json`: exits 3 with `не запускалось`, no traceback.
- Test with dirty files proves `open` and `rerun` leave `git status --porcelain` and file bytes unchanged; `open` without a token exits 4 and prints compare URLs.

Not verified: nothing was run against real GitHub or a real app (no token, no live Action); that belongs to the later run plans.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Windows console encoding**
- **Found during:** Task 2, running `status`
- **Issue:** cp1252 stdout raised UnicodeEncodeError on Russian text
- **Fix:** `main` reconfigures stdout to UTF-8
- **Commit:** 403a1f4

**2. [Rule 1 - Plan inconsistency] Plan's collect-only check**
- The plan's task 1 verify pipes `--collect-only | grep -c "fixtures/review"` expecting 0. One existing test id (`tests/test_code_index.py::test_is_indexable_rejects[tests/fixtures/review/prs/a.py-10]`, from an earlier plan) contains that string, so the literal grep prints 1. No test is collected from the `tests/fixtures/review/` folder; a guard test asserts that.

**3. Fixture suffix**
- All fixture sources use `.py.txt` rather than only the clean PR's test file, for uniformity; the manifest maps them to the `.py` paths.

## Known Stubs

None.

## Threat Flags

None. The script talks to GitHub only as verification tooling outside the app.

## Self-Check: PASSED

Files and commits (fd72730, 403a1f4) exist; tests above passed.
