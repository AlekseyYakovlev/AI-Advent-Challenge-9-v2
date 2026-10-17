---
phase: 25-support-assistant-day-33
plan: 01
subsystem: agent
tags: [slash-commands, rag, refactor, help]
requires: []
provides:
  - "agent/command_turn.py: shared CommandSpec-driven command-turn mechanism"
affects: [25-support-assistant-day-33]
tech-stack:
  added: []
  patterns: ["spec-driven shared command turn with injected MCP toolset builder"]
key-files:
  created:
    - agent/command_turn.py
    - tests/test_command_turn.py
  modified:
    - agent/help_command.py
key-decisions:
  - "help_command keeps build_mcp_toolset as its own module global and injects it into select_tools, so the existing monkeypatch seam still works"
  - "Context block is merged into the RAG block (not prepended afterwards) so the question label appears once"
requirements-completed: [SUP-02, SUP-12]
duration: 25min
completed: 2026-10-17
---

# Phase 25 Plan 01: Shared command-turn mechanism Summary

Extracted the generic /help turn logic into `agent/command_turn.py` (CommandSpec, notices, tool selection, messages with optional history, retrieval with optional context block / separate retrieval query / payload extras, quote cleanup), leaving `agent/help_command.py` a thin facade with every public name unchanged.

## Tasks

| Task | Commit |
|------|--------|
| 1. command_turn.py + tests/test_command_turn.py | 48f0146 |
| 2. help_command.py facade + regression gate | 879ebeb |

## Verification (observed)

- `pytest tests/test_command_turn.py -q`: 8 passed.
- Regression gate (7 test_help_* files, test_docs_help.py, test_command_turn.py): 195 passed, no existing test edited.
- `help_eval.default_help_threshold()` prints 0.45.
- Full `pytest tests/ -q -x`: 3555 passed, 14 skipped.
- Acceptance greps for the threshold literal, `McpToolset, build_mcp_toolset` import and `toolset_builder=build_mcp_toolset` each matched once.

## Deviations from Plan

None - plan executed as written. A `# noqa` import line used while generating the facade was removed before commit.

## Known Stubs

None.

## Self-Check: PASSED

agent/command_turn.py, tests/test_command_turn.py, commits 48f0146 and 879ebeb exist.
