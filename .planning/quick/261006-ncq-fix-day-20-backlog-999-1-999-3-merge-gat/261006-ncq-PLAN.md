---
phase: quick-261006-ncq
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - agent/tool_guard.py
  - agent/tools.py
  - agent/mcp_tools.py
  - agent/ws.py
  - agent/headless.py
  - tests/test_tool_guard.py
  - tests/test_tools.py
  - tests/test_tool_rounds_ws.py
  - tests/test_mcp_chat_ws.py
  - .planning/ROADMAP.md
autonomous: true
requirements: [BACKLOG-999.1, BACKLOG-999.2, BACKLOG-999.3]

must_haves:
  truths:
    - "An MCP tool named merge_merge_request (or accept_merge_request) is NOT executed unless the user's latest message explicitly asks to merge; 'сделай MR' / 'create a merge request' never authorises a merge"
    - "A blocked merge returns a tool result telling the model the merge was not performed and must be confirmed by the user; it does not trigger the TOOL_ERROR_REMINDER nudge"
    - "An LLM failure with an empty str(exc) (e.g. httpx.ReadTimeout('')) produces an error text naming 'timeout' and the exception type, never a bare 'LLM error:'"
    - "When the LLM fails AFTER tool rounds already ran, the user message is kept, an assistant message with the tool trace and an error note is persisted, and the turn ends with a done frame"
    - "When the LLM fails before any tool ran, the old behaviour stays: LLM_ERROR frame and the user message is deleted"
    - "TOOL_ERROR_REMINDER is sent only when the last round had an MCP failure, NO successful call, and the follow-up text is short (no substantial final answer) — a good final answer is never followed by a second nudged answer"
    - "MULTI_STEP_TOOL_HINT tells the model to call list_allowed_directories / get_file_info at most once per turn and to use commit_files action 'create' (not 'update') for files missing from the remote branch"
    - "ROADMAP Backlog no longer lists 999.1-999.3 and .planning/phases/999.1-*, 999.2-*, 999.3-* are deleted; 999.10, 999.11, 999.17 stay"
  artifacts:
    - path: "agent/tool_guard.py"
      provides: "user_asked_to_merge, MERGE_GATED_MCP_TOOLS, should_nudge_after_tool_error, build_llm_failure_note, updated MULTI_STEP_TOOL_HINT"
      contains: "def user_asked_to_merge"
    - path: "agent/tools.py"
      provides: "merge intent gate in dispatch_tool_calls (user_text kwarg)"
      contains: "user_asked_to_merge"
    - path: "agent/ws.py"
      provides: "descriptive _llm_error_detail, partial-turn persistence after tool rounds, gated error nudge"
      contains: "build_llm_failure_note"
  key_links:
    - from: "agent/ws.py::_dispatch_round"
      to: "agent/tools.py::dispatch_tool_calls"
      via: "user_text=turn.user_text"
      pattern: "user_text=turn\\.user_text"
    - from: "agent/tools.py::dispatch_tool_calls"
      to: "agent/tool_guard.py::user_asked_to_merge"
      via: "gate before _dispatch_mcp_call for MERGE_GATED_MCP_TOOLS"
      pattern: "user_asked_to_merge\\("
    - from: "agent/ws.py::_pick_nudge"
      to: "agent/tool_guard.py::should_nudge_after_tool_error"
      via: "nudge decision"
      pattern: "should_nudge_after_tool_error\\("
---

<objective>
Close Day-20 backlog items 999.1, 999.2, 999.3 in the chat tool loop:
- 999.1: code-level intent gate so the GitLab MCP `merge_merge_request` tool runs only when the user explicitly asked to merge (in the style of `user_asked_to_cancel` / `user_asked_to_schedule`).
- 999.2: descriptive LLM error text (type name / "timeout" instead of empty "LLM error:") and keep the turn (user message + tool results) when the LLM fails after tools already ran.
- 999.3: stop the error nudge from appending a second answer after a good final answer, and steer the model away from redundant discovery calls and `commit_files` 'update' on missing files.

Purpose: real GitLab run (quick 260926-38j) merged MR !1 unprompted, lost a whole turn on a stream timeout, and wasted rounds.
Output: code changes in agent/tool_guard.py, agent/tools.py, agent/mcp_tools.py, agent/ws.py, agent/headless.py; tests; backlog cleanup.
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/STATE.md
@agent/tool_guard.py
@agent/ws.py
@agent/tools.py

<interfaces>
From agent/tool_guard.py (existing, reuse):
- `_sentence_requests(text: str, pattern: re.Pattern[str]) -> bool` — per-sentence match, rejects negation ("не", "don't", "not", "never" within 2 preceding words) and non-polite questions. `user_asked_to_cancel` / `user_asked_to_schedule` are one-liners over it.
- `TOOL_ERROR_REMINDER`, `MULTI_STEP_TOOL_HINT` (tests/test_tool_guard.py:330 asserts hint has <= 130 words), `_CYRILLIC_RE`, `build_tool_fallback_summary(results, user_text)`.

From agent/tools.py:
- `async def dispatch_tool_calls(session, user_id, chat_id, tool_calls, *, mcp_bindings=None, allowed_tools=None) -> list[dict]`; MCP names resolved via `binding = mcp_bindings.get(name)` then `_dispatch_mcp_call(binding, tool_call_id, raw_arguments)`.
- `_dispatch_mcp_call` inner `_failure(text)` builds `{tool_call_id, name, ok: False, content: json{server, tool, is_error, error}, write: None, arguments, mcp: {server_id, server_name, tool}, result_text, truncated}`.
- tools.py must NOT import agent.scheduler_tools (scheduler_tools imports tools -> circular). Importing agent.tool_guard is fine (no agent imports there).

From agent/mcp_tools.py:
- `@dataclass class McpToolBinding: exposed_name, user_id, server_id, server_name, tool_name`
- `_describe(server_name: str, tool: McpToolInfo) -> str` builds the exposed tool description.
- `call_mcp_tool(binding, arguments)` is imported into agent.tools as `call_mcp_tool` (monkeypatch `agent.tools.call_mcp_tool` in tests).

From agent/ws.py:
- `_llm_error_detail(exc, provider_row) -> str` (line ~344) returns `f"LLM error: {str(exc)}"` — empty for httpx.ReadTimeout('').
- `@dataclass _ToolTurn(websocket, session, chat, chat_id, payload, llm_messages, tool_schemas, toolset, temperature, max_tokens, client=None, provider=None, allowed_tools=None)`.
- `_dispatch_round(turn, calls, echo_text, acc)` calls `dispatch_tool_calls(..., mcp_bindings=turn.toolset.bindings, allowed_tools=turn.allowed_tools)`.
- `_pick_nudge(acc, text, last_results)` — announce nudge first, then error nudge when `any(not r["ok"] and r.get("mcp") is not None for r in last_results)`.
- `_run_tool_rounds` catches any exception into `acc.error` and returns acc (acc.results always non-empty by then).
- `_handle_chat_message` lines ~996-1013: `if rounds.error is not None:` sends LLM_ERROR, deletes user_msg, commits, returns. First-stream failure (lines ~928-943) also deletes user_msg — keep that one.
- `invariants.run_self_critique` and the justify/retract stream already swallow exceptions.

From agent/headless.py (~line 248): builds `ws._ToolTurn(..., payload=SimpleNamespace(model=model), ...)` for scheduled jobs; `prompt` (the job prompt, user-authored and schedule-gated at creation) is in scope.

From agent/llm_client.py:173: `except httpx.TimeoutException as exc: logger.warning("llm_stream_timeout", error=str(exc)); raise` — str(exc) is often empty.

Test helpers: tests/test_tool_rounds_ws.py (`_stream_queue`, `_memory_call`, `_drain_until_terminal`, `_tool_frames`, imports from tests.test_memory_ws: BASE_URL, WS_ORIGIN, `_tool_calls_response`, `_plain_content_response`, `_send_and_drain`); tests/test_mcp_chat_ws.py (`_setup` registers fixture MCP server in mode `tools_extra` exposing echo/add/fail/slow/big/noargs as `mcp__fixture__<tool>`, `_request_body(route, i)`, `_run_turn`).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: 999.1 — intent gate for merge_merge_request MCP calls</name>
  <files>agent/tool_guard.py, agent/tools.py, agent/mcp_tools.py, agent/ws.py, agent/headless.py, tests/test_tool_guard.py, tests/test_tools.py</files>
  <behavior>
    - user_asked_to_merge True: "сделай MR и смержи его", "смержи MR !1", "замерджи ветку Test в main", "слей ветку Test в main", "merge MR !1 into main", "Please merge it.", "Could you merge MR !1?"
    - user_asked_to_merge False: "сделай MR", "создай merge request из Test в main", "открой мерж-реквест", "create a merge request", "не мержи пока", "don't merge it", "How do I merge a branch?", ""
    - dispatch_tool_calls with an MCP binding whose tool_name is "merge_merge_request" and user_text "сделай MR": call_mcp_tool is NOT called; result ok False, "blocked" True, mcp info present, content error text mentions the merge was not performed
    - same with user_text "смержи MR !1": call_mcp_tool IS called once; result ok True
    - same with user_text None (default): blocked (fail closed)
    - a non-gated MCP tool (e.g. tool_name "create_merge_request") with user_text "сделай MR" is dispatched normally
  </behavior>
  <action>
In agent/tool_guard.py, after `user_asked_to_schedule`, add `_MERGE_INTENT_RE` and `def user_asked_to_merge(text: str) -> bool` returning `_sentence_requests(text, _MERGE_INTENT_RE)` (reuses the existing negation/question handling). The regex (case-insensitive, `(?<!\w)...(?!\w)` word-bounded like the others) must match Russian merge verbs: optional prefix с/за + мер[д]ж + verb ending (и, ни, ите, ните, ить, нуть, with optional "те") — e.g. смержи, смерджи, замержи, замерджить, мержни; plus слей(те)/слить/влей(те)/влить; and English `merge` as a whole word NOT followed by optional space/hyphen + "request(s)" (use a negative lookahead after the word boundary so "merge request" / "merge-request" never match). The noun "мерж-реквест" / "мердж реквест" must not match (it has no verb ending). Also add `MERGE_GATED_MCP_TOOLS: frozenset[str] = frozenset({"merge_merge_request", "accept_merge_request"})` and a constant `MERGE_BLOCKED_TEXT` (English, addressed to the model): the merge was NOT performed because the user's latest message did not explicitly ask to merge; do not retry it; tell the user the merge request is ready and ask them to confirm the merge.

In agent/tools.py: add keyword-only parameter `user_text: str | None = None` to `dispatch_tool_calls` (document it in the docstring). Before calling `_dispatch_mcp_call`, if `binding.tool_name in MERGE_GATED_MCP_TOOLS and (user_text is None or not user_asked_to_merge(user_text))`: log `logger.warning("mcp_merge_gate_blocked", tool=binding.tool_name, user_id=user_id, chat_id=chat_id)` and append a blocked result without touching the MCP session. Build it with a small private helper `_blocked_mcp_result(binding, tool_call_id, raw_arguments, text)` shaped exactly like `_dispatch_mcp_call`'s `_failure` output plus `"blocked": True` (keep `mcp` info so the UI tool card shows the server/tool). Fail closed on None so any caller that forgets user_text cannot merge.

In agent/mcp_tools.py `_describe`: when `tool.name` is in MERGE_GATED_MCP_TOOLS, append " Runs ONLY when the user's latest message explicitly asks to merge; creating a merge request is not a request to merge." to the description (import the constant from agent.tool_guard).

In agent/ws.py: add field `user_text: str | None = None` to `_ToolTurn`; pass `user_text=turn.user_text` in `_dispatch_round`; set `user_text=payload.content` where `_handle_chat_message` builds the `_ToolTurn`. In `_pick_nudge`, exclude blocked results from the MCP-failure check (`r.get("blocked")` results never count) so the model is not told to "try a different approach" after a gate refusal. In agent/headless.py set `user_text=prompt` on the headless `_ToolTurn` (a scheduled job prompt is the user's own instruction, already intent-gated at schedule time).

Tests: add parametrised `user_asked_to_merge` cases (behavior list) to tests/test_tool_guard.py. Add async tests to tests/test_tools.py that call `dispatch_tool_calls` directly with `mcp_bindings={"mcp__gitlab__merge_merge_request": McpToolBinding(...)}` (and one non-gated binding), monkeypatching `agent.tools.call_mcp_tool` with an async recorder returning `{"ok": True, "is_error": False, "text": "merged", "truncated": False}`; reuse the existing session fixture style of that file. No real GitLab.
  </action>
  <verify>
    <automated>pytest tests/test_tool_guard.py tests/test_tools.py tests/test_mcp_chat_ws.py tests/test_mcp_tools.py -q</automated>
  </verify>
  <done>Gated merge tools never reach call_mcp_tool without explicit merge intent in the latest user message (or job prompt); blocked results carry "blocked": True and do not trigger the error nudge; all listed test files pass.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: 999.2 — descriptive LLM error text and keep the turn when tools already ran</name>
  <files>agent/tool_guard.py, agent/ws.py, tests/test_tool_guard.py, tests/test_tool_rounds_ws.py</files>
  <behavior>
    - _llm_error_detail(httpx.ReadTimeout(""), None) contains "timeout" and "ReadTimeout", is not "LLM error:" / "LLM error: "
    - _llm_error_detail(RuntimeError(""), None) == "LLM error: RuntimeError"
    - _llm_error_detail(RuntimeError("boom"), None) == "LLM error: boom" (unchanged); 401/403 provider message unchanged
    - build_llm_failure_note("LLM error: timeout (ReadTimeout)", "сделай") is Russian and contains the detail; with English user text it is English
    - WS turn: round 1 tool call succeeds, round-2 follow-up raises httpx.ReadTimeout -> no LLM_ERROR terminal frame, a done frame arrives, tool frame count 1, the user message AND an assistant message persist, assistant content contains the failure note with "timeout", assistant tool_trace is non-empty
    - WS turn: the very first stream fails (no tools ran) -> unchanged: LLM_ERROR frame, user message deleted
  </behavior>
  <action>
In agent/ws.py `_llm_error_detail`: keep the 401/403 provider branch. Then if `isinstance(exc, httpx.TimeoutException)` return `f"LLM error: timeout ({type(exc).__name__}) — the model did not respond in time"`; otherwise use `message = str(exc).strip() or type(exc).__name__` and return `f"LLM error: {message}"`. Add `error_type=type(exc).__name__` to both `llm_stream_failed` logger.error calls (never log the full traceback).

In agent/tool_guard.py add `def build_llm_failure_note(detail: str, user_text: str) -> str`: Russian when `_CYRILLIC_RE` matches user_text ("Модель не завершила ответ после выполнения инструментов: {detail}"), else English ("The model did not finish the reply after the tools ran: {detail}").

In agent/ws.py `_handle_chat_message`, replace the `if rounds.error is not None:` block: if `rounds.results` is empty keep the existing behaviour (LLM_ERROR frame, delete user_msg, commit, return). Otherwise (tools already ran — the no-lost-history invariant means executed tool effects must stay visible): log `logger.error("llm_stream_failed_after_tools", chat_id=..., error_type=..., rounds=rounds.rounds)`, set a local `llm_failed = True`, append `rounds.text` to assistant_text, compute tool_trace / memory_writes / task_writes and send `_send_tool_error_frames` exactly as the success path, then if `rounds.text` is blank send `_send_fallback_summary(...)`, then stream one token frame with "\n\n" + build_llm_failure_note(_llm_error_detail(exc, turn.provider), payload.content) and append it to assistant_text. Skip `_reprompt_rejected_transitions` and the invariant self-critique when `llm_failed` (the provider just failed; another call would only wait out a second timeout) — set `pending_tool_calls = rounds.calls` as usual. Then fall through to the normal persist/stats/done path so the assistant message is saved under user_msg with the tool trace and a done frame is sent. Restructure so the success and partial paths share one block (e.g. a guard `if rounds.error is not None and not rounds.results:` early return, then the common block with `llm_failed = rounds.error is not None`); keep the function readable — extract a small private helper if the block grows past ~25 lines. Keep the first-stream failure path (before any tool) unchanged apart from the improved detail.

Tests: add `_llm_error_detail` and `build_llm_failure_note` unit tests to tests/test_tool_guard.py (import `_llm_error_detail` from agent.ws). In tests/test_tool_rounds_ws.py rewrite `test_round_two_llm_failure_deletes_user_message` into `test_round_two_llm_failure_keeps_turn_and_tool_results` (HTTP 500 in round 2 now yields done + persisted user/assistant messages, assistant content contains "LLM error", 2 tool frames) and add `test_followup_timeout_keeps_turn_with_timeout_note` where the stream side effect raises `httpx.ReadTimeout("")` for the second streaming request (wrap `_stream_queue` or write a side-effect function; non-stream facts requests still get the empty JSON). Assert via the chat tree/messages API as the existing tests do. Add/keep a test that a failure of the very first stream still deletes the user message (reuse one if it already exists elsewhere — grep "LLM_ERROR" in tests first).
  </action>
  <verify>
    <automated>pytest tests/test_tool_guard.py tests/test_tool_rounds_ws.py tests/test_multistep_tools_ws.py tests/test_tool_guard_ws.py -q</automated>
  </verify>
  <done>No error text is ever a bare "LLM error:"; timeouts say "timeout (ReadTimeout)"; a failure after tool rounds persists the turn with tool trace and an error note and ends with done; a failure before any tool still rolls the user message back.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: 999.3 — trim the error nudge and redundant tool rounds; clean up backlog</name>
  <files>agent/tool_guard.py, agent/ws.py, tests/test_tool_guard.py, tests/test_mcp_chat_ws.py, .planning/ROADMAP.md</files>
  <behavior>
    - should_nudge_after_tool_error("The tool failed.", [mcp failure]) -> True
    - should_nudge_after_tool_error("", [mcp failure]) -> True
    - should_nudge_after_tool_error(<final answer of >= TOOL_ERROR_NUDGE_MAX_CHARS chars>, [mcp failure]) -> False
    - should_nudge_after_tool_error("Short.", [mcp failure, mcp success]) -> False (round had a success)
    - should_nudge_after_tool_error("Short.", [blocked merge result]) -> False
    - should_nudge_after_tool_error("Short.", [native tool failure, mcp None]) -> False
    - WS: round with [mcp__fixture__fail, mcp__fixture__echo] then a short text -> exactly 2 streaming LLM requests (no nudge), one done
    - WS: fail-only round then a long final answer (>= threshold) -> exactly 2 streaming LLM requests, stored message has no second answer
    - existing tests test_mcp_tool_failure_is_a_tool_result_not_an_error_frame and test_mcp_failure_gets_one_error_nudge still pass (short apology after a fail-only round is still nudged once)
  </behavior>
  <action>
In agent/tool_guard.py add `TOOL_ERROR_NUDGE_MAX_CHARS = 300` (comment why: a reply this long is treated as a real final answer, so the nudge would only append a second answer) and `def should_nudge_after_tool_error(text: str, last_results: list[dict[str, Any]]) -> bool`: True only when (a) some result in last_results has `ok` False, `mcp` not None and is not `blocked`, (b) no result in last_results has `ok` True, and (c) `len(text.strip()) < TOOL_ERROR_NUDGE_MAX_CHARS`. In agent/ws.py `_pick_nudge` replace the inline `mcp_failed` check with `should_nudge_after_tool_error(text, last_results)` (keep the one-nudge-per-turn `acc.error_nudge_used` flag and the announce-nudge precedence; the blocked exclusion from Task 1 now lives inside the helper).

Update `MULTI_STEP_TOOL_HINT` with two short sentences: call list_allowed_directories and get_file_info at most once per turn and reuse their results instead of repeating discovery; in commit_files use action "create" for files that do not exist on the target branch yet and "update" only for files that already exist there (if commit_files reports a missing file, retry with "create"). Keep the hint at or under 130 words if possible by tightening existing wording; if not possible, raise the limit in tests/test_tool_guard.py `test_reminders_and_hint_are_nonempty` to 170 and note why in the test.

Tests: unit tests for `should_nudge_after_tool_error` (behavior list) in tests/test_tool_guard.py; add the two WS tests to tests/test_mcp_chat_ws.py using the fixture server (`_setup`, `_run_turn`, `_tool_calls_response` with two calls in one round, `len(route.calls)` counting only streaming requests — filter by `json.loads(call.request.content).get("stream") is True` because the facts-extraction call is non-streaming). Update the hint assertion if the hint text is asserted literally anywhere (grep MULTI_STEP_TOOL_HINT in tests).

Backlog cleanup (final step): in .planning/ROADMAP.md delete the three sections "### Phase 999.1: ...", "### Phase 999.2: ...", "### Phase 999.3: ..." (each through its "- [ ] TBD ..." line), and rewrite the "Deferred to Day 20" note so it no longer mentions 999.1–999.3 (keep the Day 16 leftovers sentence: manual Ctrl+C check and the browser/real-model rechecks list, and "No Day 20 phase exists in the roadmap yet."). Do not touch 999.10, 999.11, 999.17. Delete only the directories .planning/phases/999.1-block-unprompted-mr-merge, .planning/phases/999.2-descriptive-llm-timeout-error, .planning/phases/999.3-trim-wasted-tool-rounds (they are empty; use `rm -rf` on those exact paths, and `git rm -r` if tracked). Then run the full suite.
  </action>
  <verify>
    <automated>pytest tests/ -q</automated>
  </verify>
  <done>Error nudge fires only for fail-only rounds with no substantial answer; hint covers discovery and commit_files create/update; ROADMAP has no 999.1/999.2/999.3 headings (`grep -cE "Phase 999\.[123]:" .planning/ROADMAP.md` == 0) while 999.10/999.11/999.17 remain; the three phase dirs are gone; full pytest suite passes.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| LLM -> tool dispatcher | Model-chosen tool calls (untrusted intent) reach real GitLab via MCP |
| user message -> intent gate | Latest user text decides whether an irreversible merge may run |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-ncq-01 | Elevation of privilege | agent/tools.py dispatch_tool_calls (merge_merge_request) | mitigate | Code-level gate: MERGE_GATED_MCP_TOOLS require user_asked_to_merge(latest user text); fail closed when user_text is None; "merge request" noun excluded by regex |
| T-ncq-02 | Tampering | gate bypass via model retry loop | mitigate | Blocked result excluded from TOOL_ERROR_REMINDER nudge; blocked text tells the model not to retry; existing repeated-call loop detection stays |
| T-ncq-03 | Repudiation | blocked merges invisible | mitigate | logger.warning("mcp_merge_gate_blocked", tool, user_id, chat_id) and tool card with ok False |
| T-ncq-04 | Information disclosure | LLM error detail | accept | Detail is exception type name / provider error text already shown today; no keys or tracebacks logged |
| T-ncq-05 | Elevation of privilege | headless scheduled jobs | accept | user_text = job prompt, which the user authored and which was schedule-intent-gated at creation |
</threat_model>

<verification>
- `pytest tests/ -q` passes.
- `grep -n "user_asked_to_merge" agent/tools.py agent/tool_guard.py` shows definition and use.
- `grep -n "user_text=turn.user_text" agent/ws.py` matches.
- `grep -cE "Phase 999\.[123]:" .planning/ROADMAP.md` prints 0; `ls .planning/phases | grep -E "^999\.[123]-"` prints nothing; 999.10/999.11/999.17 dirs still exist.
</verification>

<success_criteria>
- Unprompted merge after "сделай MR" is impossible at code level; explicit "смержи" still works.
- Timeout errors are named; tool work done before an LLM failure is persisted with the turn.
- No second nudged answer after a substantial final answer or a partially successful round; hint discourages redundant discovery and wrong commit_files action.
- Backlog 999.1-999.3 removed from ROADMAP and phases dir.
</success_criteria>

<output>
Create `.planning/quick/261006-ncq-fix-day-20-backlog-999-1-999-3-merge-gat/261006-ncq-SUMMARY.md` when done
</output>
