---
phase: 20-limits-verification-script-and-report-day-30
plan: 08
subsystem: verification
tags: [report, live-run, lan, limits]
requires: ["20-06", "20-07"]
provides:
  - "eval_out/day30/ live run files returned from the Mac"
  - "Day30_report.md (Russian)"
key-files:
  created: [eval_out/day30/RUN.md, eval_out/day30/verify_results.md, eval_out/day30/verify_results.json, eval_out/day30/console.txt, Day30_report.md]
requirements-completed: [VER-04, VER-05]
completed: 2026-10-12
---

# Phase 20 Plan 08: Live run and Day 30 report Summary

The user ran `verify_service.py` from a Mac against the Windows server over the LAN (exit code 0, all expectations met), and `Day30_report.md` was written from the returned files with their tables copied unchanged.

## Commits
- 35ad23c: run sheet `eval_out/day30/RUN.md`
- a27ddfb: live run files returned by the user (`verify_results.md`, `verify_results.json`, `console.txt`)
- 4cec20f: `Day30_report.md`

## Requirement status
- VER-04: satisfied: live run files returned on 2026-10-12 (url http://192.168.88.224:8001, macOS, Python 3.14.7, model qwen/qwen3.5-9b, script SHA-256 equal to the current `scripts/verify_service.py`, exit code 0, rate mode both, server limit 5 lowered on purpose).
- VER-05: recorded as confirmed by the user on 2026-10-12 on the Phase 18-19 build; no details supplied and none invented.

## Verification (observed)
- `python -m pytest tests/ -q`: 2572 passed, 11 skipped, 14 warnings.
- Report verify (every table line of `verify_results.md`, 23 lines, found unchanged in the report; no key pattern in the report or in `eval_out/day30/`): printed `ok 23`. VER-05 paragraph check: `ok2`.
- `git diff --stat -- eval_out/day30`: empty (executor did not modify the returned files). Only the key prefix `aia_mD7z` and the placeholder `--key aia_...` in RUN.md appear.
- Not verified by the executor: the live run itself (done by the user; only the returned files were read).

## Docs reconciliation (Task 1, continuation)
`20-06-SUMMARY.md` records `Behaviour changes affecting docs: None`; no document was corrected and no commit of this plan touches `docs/`. Not re-verified by `.env` hash in this continuation (the task's first half ran in the earlier session).

## Deviations from Plan
None. The rehearsal of 20-06 appears in the report only in its own labelled subsection.

## Known Stubs
None.

## Self-Check: PASSED
Day30_report.md, RUN.md, verify_results.md, verify_results.json, console.txt exist; commits 35ad23c, a27ddfb, 4cec20f exist.
