---
phase: quick-260925-qvd
plan: 01
subsystem: agent/tool-rounds
tags: [tools, system-prompt, local-models]
key-files:
  modified:
    - agent/tool_guard.py
    - agent/ws.py
    - tests/test_tool_guard.py
    - tests/test_tool_guard_ws.py
    - tests/test_tool_rounds_ws.py
metrics:
  completed: 2026-09-25
---

# Quick Task 260925-qvd: Strip TOOL_USE_RULE after the first tool round

TOOL_USE_RULE is removed from the system message as soon as the first tool round is dispatched, so every later stream of that turn (follow-up, empty-retry, cap/loop final, rejected-transition re-prompt, invariants justify/retract) is sent without it.

## Changes

- `agent/tool_guard.py`: new `strip_tool_use_rule(messages)`. Removes exactly one `"\n\n" + TOOL_USE_RULE` suffix from a leading system message with str content, replacing `messages[0]` with a new dict (no mutation of the original). No-op for empty list, non-system first message, non-str content, or rule not at the end.
- `agent/ws.py`: one call, `strip_tool_use_rule(turn.llm_messages)`, in `_run_tool_rounds` right after the first `_dispatch_round` (`acc.rounds == 1`). `turn.llm_messages` is the same list `_handle_chat_message` keeps using, so later re-prompts are covered. The rule append, the first stream, and the action-claim retry are unchanged and still carry the rule.
- The system message is rebuilt each turn in `build_llm_context` and never persisted, so the next turn gets the rule again (covered by a test).

## Commits

- `73bc0ed` fix(quick-260925-qvd): drop TOOL_USE_RULE from the system message after the first tool round
- `0514344` test(quick-260925-qvd): cover rule stripping after tool dispatch

## Tests

- 10 new unit cases in `tests/test_tool_guard.py` (rule present, dict replaced not mutated, absent, byte-identical remainder with newlines/unicode, rule mid-content, non-system first message, non-str content x2, empty list, one suffix per call).
- 3 new WS tests in `tests/test_tool_rounds_ws.py`: follow-up + empty-retry omit the rule with a byte-identical remainder; cap final follow-up (tools-less) omits it; the next turn's first request carries it again.
- `tests/test_tool_guard_ws.py`: action-claim retry and its predecessor keep the rule; in `test_retry_tool_call_is_dispatched` the follow-up after dispatch does not.
- Mutation check: with the call replaced by `pass`, 4 tests failed (`test_retry_tool_call_is_dispatched` and the 3 new WS tests); the fix was then restored.
- Full suite, run in the foreground: `timeout 550 python -m pytest tests -q` -> 555 passed, 0 failed (542 existing + 13 new), 144s.

## Deviations from Plan

- [Rule 1 - Bug] My first version of the helper was committed with a broken `"\n\n"` literal (a literal newline inside the string, SyntaxError) because of a shell heredoc escape. Caught by the immediately following test run, fixed and amended into the same commit before any other work; final commit `73bc0ed` is correct.
- The worktree was created on a different base than `1ec929b`; `git reset --hard 1ec929b` was performed as the startup check required.
- Test files use CRLF line endings; edits preserved them.

## Known Stubs

None.

## Self-Check: PASSED

- `grep -c "strip_tool_use_rule(turn.llm_messages)" agent/ws.py` == 1
- Commits 73bc0ed and 0514344 exist; no Co-Authored-By line in them.
