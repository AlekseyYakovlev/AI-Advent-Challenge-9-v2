# Quick 261006-ncq Summary

Day-20 backlog 999.1-999.3 closed: merge MCP tools gated on explicit user intent, descriptive LLM errors with the turn kept after tool rounds, and a narrower error nudge.

## Commits
- a9c9b9f fix: gate merge MCP tools on explicit user merge intent (999.1)
- 072aa95 fix: descriptive LLM errors, keep turn when LLM fails after tools (999.2)
- dbc4c6e fix: nudge only fail-only rounds without a real answer; tighten tool hint (999.3)
- 315d31c docs: remove 999.1-999.3 from ROADMAP and phases dir
- (last) test: 401-after-tool-round test updated to the new kept-turn behaviour

## Deviations
- [Rule 1] tests/test_llm_providers_routing.py::test_provider_401_on_tool_follow_up_names_provider_not_key asserted the old LLM_ERROR frame after a tool round; updated to assert a done frame and the provider/env-var note in the token text (key still absent).
- MULTI_STEP_TOOL_HINT exceeds 130 words; limit in test_reminders_and_hint_are_nonempty raised to 170 as the plan allows.

## Verification
- Task test sets passed (128, 120, 110+ at each step).
- Full `pytest tests/ -q`: first run 1949 passed, 11 skipped, 1 failed (the test above); that file re-run alone after the fix: 11 passed. The full suite was not re-run end to end after that one-test fix.

## Self-Check: PASSED
