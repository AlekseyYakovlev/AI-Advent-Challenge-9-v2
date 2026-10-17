---
phase: 25-support-assistant-day-33
plan: 04
subsystem: agent
tags: [support, command, rag, history]
requires: ["25-01"]
provides:
  - "SupportConfig table (per-user /support settings, no base)"
  - "agent/support_command.py: parse_support, SupportCommand, SUPPORT_SPEC, notices, tools, history, messages, payload, prepare/finalize"
affects: [25-05, 25-06, 25-07, 25-08, 25-09]
tech-stack:
  added: []
  patterns: ["facade over agent/command_turn.py with a CommandSpec", "history replay filtered by the support key in Message.rag_sources"]
key-files:
  created:
    - agent/support_command.py
    - tests/test_support_command.py
  modified:
    - shared/models.py
    - tests/test_database.py
key-decisions:
  - "Identifier only directly after /support; tokens failing the strict charset stay in the question"
  - "History replays at most 3 earlier /support pairs, answers cut to 1500 chars, 2000-token cap dropping oldest first"
requirements-completed: [SUP-01, SUP-03, SUP-04, SUP-10, SUP-11, SUP-12]
duration: 25min
completed: 2026-10-17
---

# Phase 25 Plan 04: /support command module Summary

`agent/support_command.py` and the `SupportConfig` table: `/support` grammar (ticket `#T-1042` forms, user `@login`), Russian texts and spec, notices with `"command": "support"`, default-`support`-server tool selection, branch history of earlier `/support` pairs, message building, additive payload and prepare/finalize wrappers.

## Tasks
1. SupportConfig, grammar, texts, notices, tools: commit 2f1dff4
2. History, messages, payload, prepare, finalize: commit 8542b59

## Verification (observed)
- Task 1 selection: 28 passed; `tests/test_help_command.py tests/test_help_api.py`: 50 passed.
- `pytest tests/test_support_command.py tests/test_command_turn.py tests/test_help_command.py tests/test_help_turn.py`: 86 passed.
- Full suite `pytest tests/`: 3625 passed, 14 skipped.
- Acceptance greps: no message text in log calls (count 0).

## Deviations from Plan

**1. [Rule 1 - Bug] test_database table list**
- **Found during:** Task 2 full-suite run
- **Issue:** `test_init_db_creates_all_tables` enumerates all tables; new `supportconfig` made it fail.
- **Fix:** added `"supportconfig"` to the expected set.
- **Files modified:** tests/test_database.py
- **Commit:** 8542b59

Also: one history test initially used text that stayed under the 2000-token cap because answers are capped at 1500 chars; the test now uses digit-heavy text.

## Known Stubs
None.

## Self-Check: PASSED
agent/support_command.py, tests/test_support_command.py exist; commits 2f1dff4 and 8542b59 exist.
