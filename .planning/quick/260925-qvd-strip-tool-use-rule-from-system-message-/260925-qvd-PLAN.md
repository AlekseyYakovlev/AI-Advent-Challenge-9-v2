---
phase: quick-260925-qvd
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - agent/tool_guard.py
  - agent/ws.py
  - tests/test_tool_guard.py
  - tests/test_tool_rounds_ws.py
  - tests/test_tool_guard_ws.py
autonomous: true
requirements: [QUICK-260925-qvd]

must_haves:
  truths:
    - "The first streaming request of a tool-enabled turn still carries TOOL_USE_RULE at the end of the system message"
    - "The action-claim retry (sent before any tool dispatch) still carries TOOL_USE_RULE"
    - "Every streaming request sent after the first tool round was dispatched (follow-up with tools, empty-retry without tools, cap/loop final follow-up, rejected-transition re-prompt, invariants justify/retract) has no TOOL_USE_RULE in its system message"
    - "Apart from the removed suffix the system message is byte-identical to the first request's"
    - "Turns without tools (unowned chat) and tool-enabled turns with no tool round behave exactly as before"
  artifacts:
    - path: "agent/tool_guard.py"
      provides: "strip_tool_use_rule helper"
      contains: "def strip_tool_use_rule"
    - path: "agent/ws.py"
      provides: "single call site in _run_tool_rounds after round-1 dispatch"
      contains: "strip_tool_use_rule(turn.llm_messages)"
  key_links:
    - from: "agent/ws.py::_run_tool_rounds"
      to: "agent/tool_guard.py::strip_tool_use_rule"
      via: "call right after the first _dispatch_round, before the first follow-up stream"
      pattern: "strip_tool_use_rule\\(turn\\.llm_messages\\)"
---

<objective>
Remove TOOL_USE_RULE from the system message of the turn's LLM context as soon as the first tool round has been dispatched, so every later stream of that turn is sent without it.

Purpose: real-model measurements (qwen3.5-9b, LM Studio) show the rule ("if an action needs a tool, call it") causes empty replies after a tool round: tools-less follow-up without the rule answered 16/16, with the rule only 11/16; the qj5 empty-retry still left 2/8 turns empty because the retry still carried the rule.
Output: `strip_tool_use_rule` helper, one call site in `_run_tool_rounds`, unit + WS tests; full suite green (542 existing + new).
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/quick/260925-qj5-fix-empty-reply-when-follow-up-with-tool/260925-qj5-SUMMARY.md
@agent/tool_guard.py
@agent/ws.py

<interfaces>
agent/tool_guard.py (currently imports only `re`; no `typing` import yet):
- TOOL_USE_RULE: str  (module constant, lines 10-14)

agent/ws.py:
- line ~599-603 (in _handle_chat_message): when `tool_schemas and llm_messages and llm_messages[0]["role"] == "system"`, llm_messages[0] is REPLACED by a new dict `{**llm_messages[0], "content": llm_messages[0]["content"] + "\n\n" + TOOL_USE_RULE}`.
- First stream (tools) -> optional `_stream_action_claim_retry(websocket, llm_messages, ...)` (BEFORE dispatch) -> `_run_tool_rounds(turn, pending_tool_calls, echo_text)` only if pending_tool_calls.
- `_run_tool_rounds(turn: _ToolTurn, first_calls, first_echo_text) -> _ToolRoundsResult`: `while True: await _dispatch_round(turn, calls, echo_text, acc)` then `_stream_follow_up_with_empty_retry` / loop-detected `_stream_follow_up(turn, None)`. All use `turn.llm_messages`, which is the SAME list object as `llm_messages` in `_handle_chat_message`.
- After the rounds: `_reprompt_rejected_transitions(turn, ...)` streams `turn.llm_messages`; invariants justify/retract streams `llm_messages` (same list). `invariants.run_self_critique(active_invariants, assistant_text, pending_tool_calls, model)` does NOT receive llm_messages.

tests helpers:
- tests/test_tool_guard_ws.py: `_stream_bodies(route)` (only `"stream": true` bodies), `_system_content(body)`, `_run_turn(responses, content)`, `SAVE_CALL`, `CLAIM`; imports TOOL_USE_RULE already.
- tests/test_tool_rounds_ws.py: `_stream_queue(responses)` (non-stream facts call answered with "{}"), `_memory_call(id, name, key)`, `_tool_calls_response`, `_plain_content_response`, `_send_and_drain`, `_tool_frames`; existing tests `test_empty_followup_with_tools_retries_once_without_tools`, `test_round_cap_ends_with_one_toolless_followup`.
</interfaces>

Persistence check (requirement 3, verified during planning):
`agent/context_engine.py::build_llm_context` builds a brand-new list each turn with a fresh `{"role": "system", "content": await build_system_prompt(session, chat_id)}` dict; the rule is appended per turn in ws.py by dict replacement. The system message is never persisted: only user/assistant messages are stored (`_persist_user_message`, `_persist_assistant_message`), `tool_trace` comes from dispatch results, and `run_self_critique` gets no llm_messages. Therefore stripping the rule from `llm_messages[0]` affects only the remaining requests of the current turn; the next turn rebuilds the system prompt and re-appends the rule. The helper must still REPLACE `messages[0]` with a new dict (not mutate the existing dict) to stay safe against any shared reference.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add strip_tool_use_rule helper and call it once after round-1 dispatch</name>
  <files>agent/tool_guard.py, agent/ws.py</files>
  <behavior>
    - messages[0] system with content "BASE\n\n" + TOOL_USE_RULE -> becomes "BASE" (new dict, other keys kept)
    - system content without the suffix -> list and dict untouched (same object)
    - system content containing TOOL_USE_RULE elsewhere but not as the exact "\n\n"+rule suffix -> untouched
    - first message role "user" ending with the suffix -> untouched
    - content None / list (non-str) -> untouched, no exception
    - empty list -> no exception
    - calling twice -> second call is a no-op (only one suffix removed)
  </behavior>
  <action>
In agent/tool_guard.py add `from typing import Any` (stdlib group, after `import re`) and a public function `strip_tool_use_rule(messages: list[dict[str, Any]]) -> None` with a one-line docstring ("Drop the appended TOOL_USE_RULE suffix from a leading system message, in place."). Logic: return if `messages` is empty; take `first = messages[0]`; return unless `first.get("role") == "system"`; `content = first.get("content")`; return unless `isinstance(content, str)`; `suffix = "\n\n" + TOOL_USE_RULE`; return unless `content.endswith(suffix)`; then `messages[0] = {**first, "content": content[: -len(suffix)]}`. Replace the list slot with a new dict (mirrors how ws.py appends the rule) rather than mutating `first`. Remove exactly one suffix occurrence; do not strip whitespace or touch anything else. Choice per requirement 1: in-place list mutation returning None fits the call site because `turn.llm_messages` is the same list object `_handle_chat_message` keeps using for the rejected-transition re-prompt and invariants justify/retract streams.

In agent/ws.py extend the existing `from agent.tool_guard import (...)` block with `strip_tool_use_rule`. In `_run_tool_rounds`, immediately after `await _dispatch_round(turn, calls, echo_text, acc)` inside the `while True` loop add `if acc.rounds == 1:` then `strip_tool_use_rule(turn.llm_messages)` with a short WHY comment (e.g. "After a tool round the rule makes local models answer empty; drop it for the rest of the turn."). This is the single call site: it runs before the first follow-up stream and, because the list is shared, affects every later stream of the turn (follow-ups with and without tools, the empty-retry, cap final, loop-detected final, `_reprompt_rejected_transitions`, invariants justify/retract). Do NOT touch the rule-append code at ~line 599, the first stream, or `_stream_action_claim_retry` — those run before dispatch and keep the rule exactly as today. Turns with no pending tool calls never enter `_run_tool_rounds`, so they are unchanged. Keep type hints, no print(), structlog only if logging is added (not required).
  </action>
  <verify>
    <automated>timeout 120 python -m pytest tests/test_tool_guard.py tests/test_tool_guard_ws.py tests/test_tool_rounds_ws.py -q</automated>
  </verify>
  <done>`strip_tool_use_rule` exists in agent/tool_guard.py with the behavior above; agent/ws.py imports it and calls it exactly once (`grep -c "strip_tool_use_rule(turn.llm_messages)" agent/ws.py` == 1) right after the first dispatch in `_run_tool_rounds`; existing tool-guard and tool-round tests pass.</done>
</task>

<task type="auto">
  <name>Task 2: Unit + WS tests for rule stripping, then full suite green</name>
  <files>tests/test_tool_guard.py, tests/test_tool_rounds_ws.py, tests/test_tool_guard_ws.py</files>
  <action>
tests/test_tool_guard.py: import `TOOL_USE_RULE` and `strip_tool_use_rule` from agent.tool_guard (extend the existing import line). Add unit tests: (1) rule present -> `messages[0]["content"] == "You are helpful."` and other keys of the dict preserved, later messages untouched; (2) rule absent -> content unchanged and `messages[0] is original_dict`; (3) other system text preserved byte-identical: base text containing newlines, trailing spaces and unicode (e.g. "Line1\n  Правило  \n") + "\n\n" + TOOL_USE_RULE strips to exactly the base; also a content where TOOL_USE_RULE appears in the middle (rule + "\n\nextra") is left untouched; (4) first message role "user" whose content ends with the suffix -> untouched; (5) non-str content (None and a list of parts) -> untouched, no exception; (6) empty list -> no exception; (7) idempotence: base + suffix + suffix stripped twice via two calls only removes one suffix per call, and one call on a single-suffix message followed by a second call leaves the base unchanged.

tests/test_tool_rounds_ws.py: import `TOOL_USE_RULE` from agent.tool_guard and `_system_content` from tests.test_tool_guard_ws (extend the existing import). Add (a1) `test_tool_use_rule_dropped_after_first_dispatch_including_empty_retry`: queue `[_tool_calls_response([_memory_call("c1","save_working_memory","k1")]), _plain_content_response(""), _plain_content_response("Ответ.")]`, run one turn like `test_empty_followup_with_tools_retries_once_without_tools`; assert 3 stream bodies, `_system_content(bodies[0]).endswith("\n\n" + TOOL_USE_RULE)`, and for bodies[1] (follow-up with tools) and bodies[2] (empty-retry without tools): `TOOL_USE_RULE not in _system_content(body)` and `_system_content(body) + "\n\n" + TOOL_USE_RULE == _system_content(bodies[0])` (byte-identical remainder). Add (a2) `test_tool_use_rule_absent_from_cap_final_followup`: monkeypatch `agent.ws.MAX_TOOL_ROUNDS` to 2, queue two distinct tool-call responses (c1/k1, c2/k2) then `_plain_content_response("Финал.")`; assert 3 bodies, rule at end of bodies[0], absent from bodies[1] and bodies[2], and `"tools" not in bodies[2]` (cap final). Add (a3) `test_rule_returns_on_next_turn`: two turns in the same chat (first turn tool call + text, second turn plain text, as in `test_two_round_turn_runs_both_tools_and_replays_in_order` style); assert the second turn's first stream body system message ends with the rule again (proves no leak into stored data / next turn).

tests/test_tool_guard_ws.py: (c) in `test_claim_without_tool_triggers_single_retry_with_reminder` add `assert _system_content(bodies[0]).endswith(TOOL_USE_RULE)` and `assert _system_content(retry).endswith(TOOL_USE_RULE)`; in `test_retry_tool_call_is_dispatched` capture bodies and assert bodies[0] and bodies[1] (action-claim retry, before dispatch) end with the rule and bodies[2] (follow-up after dispatch) does not contain it. (b) the no-tools case is already covered by `test_no_tools_offered_sends_one_plain_request` (asserts `TOOL_USE_RULE not in` the system message) and the tool-enabled no-round case by `test_plain_reply_sends_one_request_and_rule_is_in_system_prompt` — keep both unchanged and confirm they still pass.

Then run the whole suite in the foreground (no background run): `timeout 400 python -m pytest tests -q`. All previously passing 542 tests plus the new ones must pass. Commit code (Task 1) and tests as atomic commits with messages like `fix(quick-260925-qvd): drop TOOL_USE_RULE from the system message after the first tool round` and `test(quick-260925-qvd): cover rule stripping after tool dispatch`. Commit messages MUST NOT contain any Co-Authored-By line (project memory rule).
  </action>
  <verify>
    <automated>timeout 400 python -m pytest tests -q</automated>
  </verify>
  <done>New unit tests (rule present/absent/byte-identical/non-system/non-str/empty/idempotent) and WS tests (a1 follow-up + empty-retry, a2 cap final, a3 next turn restores rule, c action-claim retry keeps rule) pass; the no-tools test still passes; full suite green with 0 failures; commits contain no Co-Authored-By line.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Agent -> LLM backend | System prompt text sent outbound per request |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-qvd-01 | Tampering | strip_tool_use_rule | mitigate | Strip only the exact "\n\n"+TOOL_USE_RULE suffix on a role=="system" first message with str content; replace the dict rather than mutate; unit tests assert byte-identical remainder |
| T-qvd-02 | Repudiation | model claims an action after a tool round without calling the tool | accept | The rule still governs the first request and the action-claim retry; after a real dispatch the tool trace is persisted from actual results, so claims remain auditable |
</threat_model>

<verification>
- `grep -c "strip_tool_use_rule(turn.llm_messages)" agent/ws.py` returns 1
- `grep -n "TOOL_USE_RULE" agent/ws.py` still shows the unchanged append near the first stream
- `timeout 400 python -m pytest tests -q` passes with 0 failures
</verification>

<success_criteria>
- First request and action-claim retry of a tool-enabled turn contain TOOL_USE_RULE; all streams after the first dispatch do not
- System prompt remainder byte-identical; next turn gets the rule again
- No-tools / no-round turns unchanged
- Full test suite green; commits without Co-Authored-By
</success_criteria>

<output>
Create `.planning/quick/260925-qvd-strip-tool-use-rule-from-system-message-/260925-qvd-SUMMARY.md` when done
</output>
