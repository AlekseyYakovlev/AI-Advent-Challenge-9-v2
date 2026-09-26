---
phase: 08-scheduler-day-18
plan: 03
subsystem: scheduler
tags: [headless-runner, tool-allowlist, llm, mcp, respx]
requires:
  - phase: 07
    provides: chat tool loop in agent/ws.py, MCP toolset, save_long_term_memory tool
provides:
  - agent/headless.py run_headless_turn (socket-free LLM+tool turn for scheduled jobs)
  - dispatch_tool_calls(allowed_tools=...) dispatcher-level allowlist
  - _ToolTurn.allowed_tools passthrough
affects: [08-04 scheduler engine]
tech-stack:
  added: []
  patterns:
    - duck-typed RecordingSink stands in for a WebSocket so the chat tool loop is reused unchanged
    - allowlist enforced in the dispatcher, not only by schema filtering
key-files:
  created:
    - agent/headless.py
    - tests/test_scheduler_runner.py
  modified:
    - agent/tools.py
    - agent/ws.py
key-decisions:
  - "Reuse ws._run_tool_rounds and friends (accessed as agent.ws attributes) instead of re-implementing the loop"
  - "Long-term memory is included in the headless system prompt for chat parity; profile block is not"
  - "Unmapped exceptions (including TimeoutError) propagate to the engine untouched"
requirements-completed: [SCHED-05, SCHED-06, SCHED-14]
duration: ~25 min
completed: 2026-09-26
---

# Phase 8 Plan 03: Headless Runner Summary

**Socket-free `run_headless_turn` that drives the existing chat tool loop through a RecordingSink, with a dispatcher allowlist limiting built-in tools to `save_long_term_memory` plus the user's MCP tools.**

## Tasks

| Task | Name | Commit |
|------|------|--------|
| 1 | Dispatcher allowlist + `_ToolTurn.allowed_tools` | b4b3979 |
| 2 | `agent/headless.py` runner with respx tests | c296e1f |

## What was built

- `agent/tools.py`: `dispatch_tool_calls(..., allowed_tools: frozenset[str] | None = None)`. A built-in name outside the allowlist yields the same "unknown tool <name>" result as an unregistered name, logs `tool_call_not_allowed tool=<name>` (name only), and writes nothing. MCP bindings are checked first and are unaffected.
- `agent/ws.py`: `_ToolTurn.allowed_tools` (last field, default None) passed through `_dispatch_round`. Chat behaviour unchanged.
- `agent/headless.py`: `HEADLESS_CHAT_ID`, `HEADLESS_TOOL_ALLOWLIST`, `HEADLESS_PREFACE`, `MCP_UNAVAILABLE_NOTE`, `RecordingSink`, `HeadlessResult`, `HeadlessRunError`, `run_headless_turn`. Sampling comes from the owner's global Settings row (unsaved defaults if absent, row never created). System prompt = base prompt + preface + long-term memory + clock line + multi-step hint + tool-use rule (rule last so `strip_tool_use_rule` still works). The read transaction is committed before the LLM call.
- Failure mapping: ConnectError -> "Модель недоступна: LM Studio не запущен"; HTTPStatusError -> "Модель недоступна: HTTP <code>"; TimeoutException -> "Тайм-аут ответа модели"; empty answer with no tool results -> "Модель вернула пустой ответ" (with " (MCP-инструменты недоступны)" suffix when enabled MCP servers yielded no tools). With enabled servers and an empty toolset a successful answer gets `MCP_UNAVAILABLE_NOTE` appended. Errors raised in later tool rounds (`rounds.error`) are mapped the same way.

## Verification (actually run)

- `pytest tests/test_scheduler_runner.py tests/test_tools.py tests/test_tool_rounds_ws.py tests/test_memory_ws.py tests/test_mcp_chat_ws.py -q` after Task 1: 38 passed.
- `pytest tests/test_scheduler_runner.py -q` after Task 2: 29 passed (>= 15 required).
- Full suite `pytest tests -q`: 664 passed.
- All plan acceptance-criteria greps checked: MCP_UNAVAILABLE_NOTE 2, "MCP-инструменты" 2, HEADLESS_TOOL_ALLOWLIST 3, allowlist literal 1, chat lock/persist references 0, LM Studio message 1, prompt/content/text logging 0, `allowed_tools` field/param 1 each, `allowed_tools=turn.allowed_tools` 1.

Not verified: behaviour against a real LM Studio model (only respx-mocked streams).

## Deviations from Plan

- Worktree base was corrected with `git reset --hard 180f6e1` per the worktree branch check (worktree started from an older commit).
- TDD: the Task 1 implementation was written before its tests, so no separate RED commit exists; tests were added in the same commit and pass.
- Added tests beyond the plan list: read-timeout mapping, error in a later tool round, MCP toolset build failure, parametrized coupling guard, allowlist-constant test.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-08-10, T-08-11, T-08-13, T-08-14 are implemented as planned (dispatcher-level allowlist, log ids/names/counts only, existing round cap reused).

## Self-Check: PASSED

agent/headless.py, tests/test_scheduler_runner.py, commits b4b3979 and c296e1f exist.
