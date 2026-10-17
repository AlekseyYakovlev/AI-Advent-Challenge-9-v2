---
phase: 27-ai-release-pipeline-day-35
plan: 03
subsystem: release-pipeline
tags: [release, planner, llm, mcp]
requires:
  - phase: 27-ai-release-pipeline-day-35
    provides: release_command types and release_pipeline pure logic (27-02), MCP release tools (27-01)
provides:
  - ReleaseLlm wrapper with draft_changelog and choose_version
  - build_plan returning PlanBuild(plan | notice) through an injected ToolCaller
affects: [27-04, 27-06]
tech-stack:
  added: []
  patterns: [injected ToolCaller for read-only planning, retry-once coverage check for LLM drafts]
key-files:
  created:
    - agent/release_llm.py
    - agent/release_planner.py
    - tests/test_release_planner.py
  modified: []
key-decisions:
  - "Server keys come from RELEASE_SERVER_NAME / GITHUB_SERVER_NAME constants in release_command (the plan text said \"release\"; the constant is \"release-git\")"
patterns-established:
  - "Planner performs reads only; write tool names do not appear in its source"
requirements-completed: [D-02, D-03, D-06, D-07, D-09, D-10, D-11, D-13, D-17, D-21]
duration: 15min
completed: 2026-10-17
---

# Phase 27 Plan 03: Release planner Summary

Plan phase of `/release`: reads repo state through an injected caller, filters and groups commits, gets the Russian changelog and a candidate-restricted version from the chat's model, and returns a `ReleasePlan` or a precise notice, with no writes.

## Tasks

| Task | Commit |
| ---- | ------ |
| 1 RED llm tests | b4105c8 |
| 1 GREEN release_llm.py | 3aec6dd |
| 2 RED planner tests | 9b3bf8e |
| 2 GREEN release_planner.py | 5342027 |

## Verification (observed)

- `pytest tests/test_release_planner.py tests/test_release_pipeline.py tests/test_release_command.py -n0 -q`: 86 passed.
- `pytest tests/test_release_planner.py -n0 -q`: 33 passed (13 llm/changelog/decision, 20 planner).
- Grep for write tool names in `agent/release_planner.py`: 0 matches. `_complete` is not imported in `release_llm.py`.

## Deviations from Plan

- **[Rule 3 - Blocking] Worktree base:** HEAD started on an older commit (03c151a) without wave 1 files; ran the prescribed `git reset --hard 58bfabe` per the startup check.
- **Server key:** used `RELEASE_SERVER_NAME` ("release-git") instead of the literal "release" from the plan text, matching the constant defined in 27-02.
- **Commit trailer:** the user's global rule (no Co-Authored-By Claude line) was followed over the injected attribution reminder.
- `build_plan` is longer than the ~50-line guideline (helpers split out, but the main flow stays linear).

## Known Stubs

None.

## Self-Check: PASSED

Files agent/release_llm.py, agent/release_planner.py, tests/test_release_planner.py exist; commits b4105c8, 3aec6dd, 9b3bf8e, 5342027 exist.
