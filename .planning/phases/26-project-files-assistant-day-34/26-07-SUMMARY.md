---
phase: 26-project-files-assistant-day-34
plan: 07
subsystem: eval-evidence
tags: [scenarios, local-model, report]
requires:
  - phase: 26-05
    provides: eval_out/day34/e2e.md
  - phase: 26-06
    provides: scripts/files_eval.py
provides:
  - eval_out/day34/{usages,adr,changelog,readme}/ run records
  - Day34_report.md (Russian)
affects: [26-08]
key-files:
  created: [Day34_report.md, eval_out/day34/summary.md, eval_out/day34/usages/, eval_out/day34/adr/, eval_out/day34/changelog/, eval_out/day34/readme/]
  modified: [eval_out/day34/e2e.md]
key-decisions:
  - "MAX_TOOL_ROUNDS stays 15: the rounds rule did not fire (max 9 tool calls in any scenario)"
requirements-completed: [PFILE-05, PFILE-09]
duration: 40min
completed: 2026-10-17
---

# Phase 26 Plan 07: Scenario runs and Day 34 report Summary

**Four D-15 scenarios run on local `qwen/qwen3.5-9b` (commit 2958292, temperature 0); recorded as measured, plus a Russian `Day34_report.md`. Results are weak: only ADR created its file.**

## Scenario results (run.json status / measured outcome)

| Scenario | status | tool calls | outcome |
|----------|--------|-----------|---------|
| usages | done | 7 | no files changed (correct); answer substantively right, but `mentions_usage_files` check false |
| adr | done | 6 | `docs/adr/001-separation-ui-agent-processes.md` created (+319), but English, partly invented rationale, answer does not list the file |
| changelog | done | 1 | FAILED in substance: stopped after `git_recent_commits`, no CHANGELOG.md, fallback text "model gave no answer" |
| readme | done | 9 | FAILED in substance: read all files, never called `write_file`, no README.md, same fallback text |

`status: done` only means the driver finished without error; the report states this explicitly.

## Rounds rule

Did not fire. No scenario reached 15 tool calls (max 9), so `MAX_TOOL_ROUNDS` stays 15; `agent/tool_guard.py` and `tests/test_tool_guard.py` are untouched. No `rounds15/` folders.

## Verification actually run

- `python scripts/files_eval.py check`: ok. `python scripts/files_eval.py run`: exit 0, summary.md with 4 rows.
- `grep -rn "aia_" eval_out/day34 Day34_report.md`: nothing.
- Scratch-path grep: `eval_out/day34/e2e.md` (from 26-05) had scratch paths; scrubbed to `<scratch>`. The only remaining case-insensitive regex hit is in `readme/run.json`, a false positive (`users\"` JSON key inside file content read by the model).
- Report structure check from the plan's verify command: ok (162 lines, 8 sections, patch links for all four).
- `pytest` on the six phase test files: 265 passed, 1 skipped. Full suite is left to 26-08.
- Not verified: the cause of the changelog/readme stop (not recorded in the logs); the report says so.

## Deviations from Plan

**[Rule 2 - privacy] Scratch paths in e2e.md.** The acceptance grep requires no absolute user paths in tracked artifacts; 26-05's `e2e.md` contained them. Scrubbed in a separate commit. No driver changes were needed.

## Commits

- c896b6e test(26-07): day 34 scenario runs on the local model
- 80ea150 test(26-07): scrub scratch paths from e2e record
- 76b3531 docs(26-07): Day34 report

## Known Stubs

None.

## Self-Check: PASSED
