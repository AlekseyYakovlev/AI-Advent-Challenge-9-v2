---
phase: 27-ai-release-pipeline-day-35
plan: 02
subsystem: agent
tags: [release, pure-logic, websocket-frames, tdd]
requires: []
provides:
  - agent/release_command.py (grammar, ReleasePlan, StepState, ToolOutcome/ToolCaller/FrameSender, frame builders, Russian copy)
  - agent/release_pipeline.py (versions, MCP result parsers, noise filter, grouping, prompts, LLM output parsers, coverage, section)
affects: [27-03, 27-04, 27-05, 27-06]
tech-stack:
  added: []
  patterns: [pure I/O-free modules, TDD RED/GREEN per task]
key-files:
  created:
    - agent/release_command.py
    - agent/release_pipeline.py
    - tests/test_release_command.py
    - tests/test_release_pipeline.py
  modified: []
key-decisions:
  - "Assumption A1: minor releases use tag message 'vX.Y: <title>' without Week N (single function tag_message)"
  - "DayNN merges are reported in day_merges and not counted as dropped or kept"
requirements-completed: [D-01, D-02, D-06, D-07, D-08, D-11, D-12, D-13, D-14, D-17, D-21]
duration: ~25 min
completed: 2026-10-17
---

# Phase 27 Plan 02: Pure release core Summary

Pure, I/O-free `/release` core: grammar and WebSocket frame contract plus every deterministic rule (versions, noise filter, grouping, prompts, strict LLM output parsing, coverage check, section assembly).

## Tasks and commits

| Task | Commits |
|------|---------|
| 1. release_command.py | 39ee9e5 (RED), cd5b731 (GREEN) |
| 2. versions, parsers, filter, grouping, signals | 7ab72db (RED), c9a989c (GREEN) |
| 3. prompts, LLM parsing, coverage, section | RED test commit (test(27-02): add failing prompt and parser tests), 5fe92ac (GREEN) |

## Verification (observed)

`pytest tests/test_release_pipeline.py tests/test_release_command.py -q -n0`: all pass (53 tests: 22 command, 31 pipeline). Acceptance greps: no `print(` in either module; pure-module import grep prints 0.

## Deviations from Plan

**1. [Rule 1 - Bug] parse_owner_repo accepted a Windows drive path.** `C:/repos/bare.git` was parsed as scp-like and yielded `repos/bare`. Fixed: scp-like form rejects a one-letter host and paths starting with `/`. Included in commit c9a989c.

Otherwise the plan was executed as written. The repo-level rule (no Co-Authored-By Claude line) overrode the attribution reminder, so commits carry no such line.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-27-08..T-27-12 implemented: `<commits>` delimiter with tag neutralisation, candidate-only versions, sanitized titles, allowed-heading-only changelog, range grammar, logs carry lengths only.

## Self-Check: PASSED
