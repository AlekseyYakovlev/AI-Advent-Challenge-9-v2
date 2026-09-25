---
phase: quick-260925-oya
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - agent/context_engine.py
  - tests/test_tool_trace_history.py
autonomous: true
requirements: [QUICK-260925-oya]

must_haves:
  truths:
    - "A later turn after a tool turn sends the model assistant{content:'', tool_calls:[...]} -> tool{tool_call_id, content} (one per call) -> assistant{content:<stored text>} instead of a text trace appended to the assistant content"
    - "No outbound request contains TOOL_TRACE_HEADER text built from history"
    - "Plain (no-trace) turns produce exactly today's outbound dicts: {role, content, token_count}"
    - "No compression strategy can send a tool message without its preceding assistant tool_calls message (expansion runs after the cut)"
    - "Every replayed tool_call arguments value is a valid JSON string, including legacy rows whose arguments were cut with an ellipsis"
    - "Usage %, no_compression overflow and stats count tool_calls arguments and tool results, and never crash on content '' / tool_calls-only assistant dicts"
  artifacts:
    - path: "agent/context_engine.py"
      provides: "_expand_tool_traces, _replay_arguments, _dict_tokens; _load_branch_messages returns id + raw tool_trace; render_tool_trace deleted"
      contains: "def _expand_tool_traces"
    - path: "tests/test_tool_trace_history.py"
      provides: "Tests (a)-(f) for the structured replay"
  key_links:
    - from: "agent/context_engine.py::build_llm_context"
      to: "_expand_tool_traces"
      via: "called on the output of _apply_compression_strategy"
      pattern: "_expand_tool_traces\\(compressed\\)"
    - from: "agent/context_engine.py::_message_tokens"
      to: "_dict_tokens"
      via: "sums tokens over the expanded list"
      pattern: "_expand_tool_traces\\(messages\\)"
---

<objective>
Replace the text tool-trace replay (quick 260925-nv3, DD-1/DD-3) with structured OpenAI-format replay. Real-model measurements (qwen3.5-9b, 43 tools) show the text trace suppresses tool calling (0/8 and 4/8) while OpenAI-format history restores it (8/8). This supersedes DD-1 and DD-3. DD-2 (nullable Message.tool_trace JSON, assistant rows only), DD-4/DD-5 (TOOL_USE_RULE, action-claim guard) and ocp's TraceLeakFilter wiring stay exactly as they are.

Output: modified agent/context_engine.py and rewritten tests/test_tool_trace_history.py. Full `pytest tests/` green (529 existing tests plus the new ones).
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/quick/260925-nv3-fix-llm-hallucinated-tool-actions-persis/260925-nv3-SUMMARY.md
@agent/context_engine.py
@tests/test_tool_trace_history.py

<interfaces>
Current code in agent/context_engine.py (line numbers approximate):
- L12 `from agent.tool_guard import TOOL_TRACE_HEADER` is also asserted by tests/test_tool_guard.py:144 (`context_engine.TOOL_TRACE_HEADER is tool_guard.TOOL_TRACE_HEADER`). KEEP this import even though nothing in context_engine uses it after this change; add a short comment saying it is re-exported for compatibility.
- L23-24 `TOOL_TRACE_ARGS_CHARS = 200`, `TOOL_TRACE_RESULT_CHARS = 300`.
- L30 `_message_tokens(messages)` sums `llm_client.count_tokens(msg["content"])`; fallback `len(msg["content"]) // 4`.
- L40 `_cut(text, limit)` appends "…" when cut.
- L47 `serialize_tool_trace(results) -> str | None`: JSON list of {name, arguments, ok, result}; result from `result_text` else `content`. Called only from agent/ws.py:428 (signature must not change).
- L66 `render_tool_trace(raw)` - used only by `_message_to_dict` and tests. DELETE.
- L228 `_load_branch_messages(session, chat_id)` -> list of `_message_to_dict(msg)`.
- L247 `_message_to_dict(msg)` currently appends rendered text trace and adds its tokens.
- L271 `_apply_compression_strategy(session, chat_id, all_messages)` - uses `_message_tokens(all_messages)` for threshold and the no_compression overflow check; slices by list position. Tests (tests/test_context_engine.py:176, :252) call it directly with plain {role, content, token_count} dicts (no id/tool_trace keys) - must keep working.
- L413 `build_llm_context` returns `[{"role":"system","content":...}, *compressed]`.
- L429 `compute_chat_stats`: loops llm_messages using `msg["content"]` for system and `msg.get("token_count", count_tokens(msg["content"]))` otherwise (note: the default arg is evaluated eagerly); `total_response_tokens` sums branch assistant `token_count`.

In-turn echo shape in agent/ws.py (the format to mirror; do NOT edit ws.py):
- assistant: {"role": "assistant", "content": echo_text, "tool_calls": _normalize_tool_calls_for_echo(pending_tool_calls)} where each call is {"id", "type": "function", "function": {"name", "arguments"}} and empty arguments become "{}".
- tool: {"role": "tool", "tool_call_id": result["tool_call_id"], "content": result["content"]}.
- dispatch results (agent/tools.py) carry "name", "arguments" (raw string from the model), "ok", "content", optional "result_text".

Test helpers already available in tests/test_memory_ws.py: BASE_URL, WS_ORIGIN, _get_message, _plain_content_response(text), _queue_responses(list), _send_and_drain(ws, text), _tool_calls_response([(call_id, name, raw_args_json)]). tests/conftest.py: login_test_client(client).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Structured tool-trace expansion, JSON-safe arguments, and expansion-aware token accounting in context_engine</name>
  <files>agent/context_engine.py</files>
  <behavior>
    - serialize_tool_trace: arguments that are valid JSON and at most TOOL_TRACE_ARGS_CHARS long are stored uncut; empty, invalid or over-limit arguments are stored as "{}"; result still cut to TOOL_TRACE_RESULT_CHARS with the "…" marker
    - _load_branch_messages: every dict is {role, content (stored, byte-identical), token_count, id, tool_trace (raw str or None)}
    - build_llm_context: a traced assistant becomes assistant(content "", tool_calls) + tool per call + assistant(stored text); untraced dicts are {role, content, token_count} exactly as today
    - _message_tokens / compute_chat_stats count tool_calls name+arguments and tool content, and do not raise on content "" or a missing/non-str content
  </behavior>
  <action>
All edits are in agent/context_engine.py. Do not touch agent/ws.py, agent/tool_guard.py (TOOL_TRACE_HEADER, TraceLeakFilter, TOOL_USE_RULE, the action-claim guard) or shared/models.py (requirements 5 and 6).

1. Persistence format (requirement 3). Raise TOOL_TRACE_ARGS_CHARS to 1000 (arguments are no longer shown as text; they must be complete JSON to be replayed, so a slightly larger limit keeps real write_file-style args). Add a private helper `_trace_arguments(raw: Any) -> str`: take `raw` if it is a str, otherwise `json.dumps(raw, ensure_ascii=False)`. Return "{}" when the string is empty/whitespace, when `json.loads` raises (catch `json.JSONDecodeError` and `TypeError`), or when it is longer than TOOL_TRACE_ARGS_CHARS. Otherwise return the string unchanged. Use it in serialize_tool_trace in place of the `_cut(...)` for "arguments". Leave name, ok and result (still `_cut` to TOOL_TRACE_RESULT_CHARS with "…") unchanged. Keep the signature and the None-on-empty behaviour; ws.py calls it.

2. Replay-side safety for legacy rows. Add `_replay_arguments(value: Any) -> str`: return `value` when it is a non-empty str and `json.loads(value)` succeeds; otherwise "{}". It covers rows written before this change, whose arguments were cut with "…" and are not valid JSON. Add `_parse_trace_entries(raw: str | None) -> list[dict[str, Any]]`: returns [] for None/empty; `json.loads` inside try/except (`json.JSONDecodeError`, `TypeError`) and logs `logger.warning("tool_trace_parse_failed", error=str(exc))` on failure. It keeps only list items that are dicts with a non-empty str "name", and returns [] when the top level is not a list. A row whose trace yields no entries is replayed as a plain message and must never raise.

3. Loading (requirement 1). Rewrite `_message_to_dict(msg)` to return `{"role": msg.role, "content": msg.content, "token_count": token_count, "id": msg.id, "tool_trace": msg.tool_trace}`. Content is always the stored content with nothing appended. For an assistant message whose `_parse_trace_entries` is non-empty, token_count = msg.token_count + `_trace_extra_tokens(entries)`. Otherwise token_count = msg.token_count. `_trace_extra_tokens` is the sum over entries of count_tokens(name) + count_tokens(_replay_arguments(arguments)) + count_tokens(str(result)). This keeps position-based compression cutting on stored messages while the per-message token_count already reflects the expanded size. That keeps compression thresholds and `total_response_tokens` consistent with the old DD-3 semantics. Update the docstring. Delete `render_tool_trace` entirely (requirement 5; its only non-test user was `_message_to_dict`).

4. Expansion (requirement 2). Add `_expand_tool_traces(messages: list[dict[str, Any]]) -> list[dict[str, Any]]` and a per-message helper `_expand_trace_message(msg, entries)`. For each input dict:
   - If role == "assistant" and `_parse_trace_entries(msg.get("tool_trace"))` is non-empty, emit a group. First, one assistant dict {"role": "assistant", "content": "", "tool_calls": [{"id": f"call_{msg.get('id')}_{j}", "type": "function", "function": {"name": entry["name"], "arguments": _replay_arguments(entry.get("arguments"))}} for j, entry in enumerate(entries)], "token_count": sum of count_tokens(name)+count_tokens(arguments)}. Then one {"role": "tool", "tool_call_id": <same id>, "content": str(entry.get("result", "")), "token_count": count_tokens(content)} per entry, in the same order. Then, only when `msg["content"].strip()` is non-empty, a final {"role": "assistant", "content": msg["content"], "token_count": msg["token_count"] minus the call and tool token_counts of this group, floored at 0}. Decision (requirement 2, empty text): an empty stored reply omits the trailing assistant. assistant(tool_calls) -> tool -> next user is a valid OpenAI sequence, and it avoids an empty-content assistant without tool_calls. Put this in the docstring. If the message has no id (hand-built dicts), use index-free fallback id `f"call_x_{j}"` only as a defensive default.
   - Otherwise emit a copy of the dict without the "id" and "tool_trace" keys, e.g. `{k: v for k, v in msg.items() if k not in ("id", "tool_trace")}`. This keeps today's {role, content, token_count} shape byte-identical (requirement 2, test c).
   The function must be idempotent on already-expanded lists: dicts without a tool_trace key pass through unchanged apart from key stripping.
   In `build_llm_context`, return `[{"role": "system", "content": system_prompt}, *_expand_tool_traces(compressed)]`. Expansion runs AFTER `_apply_compression_strategy`, so a window cut on stored positions can never orphan a tool message.

5. Token accounting (requirement 4). Add `_dict_tokens(msg: dict[str, Any]) -> int`: content = msg.get("content"), counted only if it is a str (else 0). Add count_tokens(function name) + count_tokens(function arguments) for every entry in msg.get("tool_calls") or [], reading defensively with .get and treating non-str values as "". Rewrite `_message_tokens(messages)` to sum `_dict_tokens` over `_expand_tool_traces(messages)`. The primary path wraps count_tokens results with `or 0` as today. The fallback path in the except branch uses the same traversal with len(text) // 4 on the same strings; implement via a small `_dict_chars(msg)` helper or a shared text-extraction helper `_dict_texts(msg) -> list[str]` used by both. The overflow check and threshold in `_apply_compression_strategy` then count expanded messages automatically, since they call `_message_tokens`. In `compute_chat_stats`, replace the per-message loop body: for system use `_dict_tokens(msg)`; otherwise use `msg["token_count"]` when the key is present and an int, else `_dict_tokens(msg)`. This removes the eager `count_tokens(msg["content"])` default that would crash on non-str content. Leave total_request_tokens/total_response_tokens computed from `_load_branch_messages` as-is. Grep the module for any remaining `msg["content"]` in token code and route it through `_dict_tokens`.

Follow CLAUDE.md conventions: type hints everywhere, `X | None`, single-line docstrings, no bare except, structlog only.
  </action>
  <verify>
    <automated>python -c "from agent.context_engine import _expand_tool_traces, _replay_arguments, _dict_tokens, serialize_tool_trace; import json; t=serialize_tool_trace([{'name':'a','arguments':'{\"k\": 1}','ok':True,'content':'r'}]); out=_expand_tool_traces([{'role':'assistant','content':'Done.','token_count':50,'id':7,'tool_trace':t}]); assert [m['role'] for m in out]==['assistant','tool','assistant'], out; assert out[0]['tool_calls'][0]['id']==out[1]['tool_call_id']=='call_7_0'; json.loads(out[0]['tool_calls'][0]['function']['arguments']); assert _replay_arguments('{\"a\": \"xx…')=='{}'; assert _dict_tokens({'role':'assistant','content':None,'tool_calls':[{'function':{'name':'a','arguments':'{}'}}]})>0; print('ok')" && grep -c "def render_tool_trace" agent/context_engine.py | grep -q "^0$" && echo "render removed"</automated>
  </verify>
  <done>render_tool_trace is gone. _load_branch_messages returns stored content unchanged plus id/tool_trace. build_llm_context expands traces after compression. Arguments are always valid JSON. Token counting handles tool_calls-only assistants. Module imports cleanly and TOOL_TRACE_HEADER is still importable from agent.context_engine.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Rewrite tests/test_tool_trace_history.py for structured replay and run the full suite</name>
  <files>tests/test_tool_trace_history.py</files>
  <behavior>
    - (a) structure + id pairing: one traced assistant with 2 calls expands to assistant(tool_calls ids call_<id>_0, call_<id>_1, content "") -> tool(call_<id>_0) -> tool(call_<id>_1) -> assistant("Done.")
    - (b) no orphan tool: with SLIDING and TRUNCATE_MIDDLE, when the recent-window start index lands exactly on a traced assistant, every tool message in build_llm_context output has, earlier in its contiguous group, an assistant whose tool_calls contain its tool_call_id
    - (c) untraced turns: build_llm_context / WS body non-system dicts equal today's {role, content, token_count}; no id/tool_trace keys leak
    - (d) stats: compute_chat_stats current_context_size for a chat with a traced turn equals the sum of expanded messages' token counts plus system tokens, is larger than the same chat without the trace, and has no "error" key; _message_tokens on a tool_calls-only assistant dict with content "" does not raise and is > 0
    - (e) legacy rows: a stored trace with arguments '{"path": "aaaa…' (old cut) replays with arguments "{}"; a corrupt trace "not json" and a trace whose entries lack "name" replay as a plain assistant message without raising
    - (f) provider payload after a tool turn (WS integration): the next turn's request messages (minus system) have roles [user, assistant, tool, assistant, user]; the tool_calls assistant has content "" and a tool_calls list; no assistant message has empty content without tool_calls; TOOL_TRACE_HEADER appears in no message content
  </behavior>
  <action>
Rewrite tests/test_tool_trace_history.py (requirement 7). Imports: drop `render_tool_trace`. Import from agent.context_engine: `TOOL_TRACE_ARGS_CHARS`, `TOOL_TRACE_RESULT_CHARS`, `_load_branch_messages`, `_message_tokens`, `build_llm_context`, `compute_chat_stats`, `serialize_tool_trace`. Keep `TOOL_TRACE_HEADER` imported (from agent.tool_guard) for the negative assertion.

Keep and adapt the existing tests:
- test_serialize_empty_results_returns_none: unchanged.
- Replace test_serialize_truncates_arguments_and_result with two tests. First: over-limit arguments (`json.dumps({"content": "a" * (TOOL_TRACE_ARGS_CHARS + 10)})`) are stored as "{}", and result "r" * 1000 is still cut to TOOL_TRACE_RESULT_CHARS + "…". Second: small valid JSON arguments are stored byte-identical, while empty "" and invalid "{bad" arguments are stored as "{}".
- test_serialize_falls_back_to_content_without_result_text: unchanged.
- Delete test_render_none_and_bad_json_are_empty and test_render_lists_each_call; the function is removed.
- Replace test_load_branch_messages_replays_trace_only_for_traced_rows. It keeps the same DB setup and asserts: history[1]["content"] == "Done." (byte-identical stored text), history[1]["tool_trace"] == trace, history[1]["id"] == traced.id, history[1]["token_count"] > 2. history[0] and history[3] contain role/content/token_count equal to the old values plus "id" and "tool_trace": None.
- test_migration_is_idempotent: unchanged.
- Rewrite test_tool_turn_persists_trace_and_next_turn_replays_it as scenario (f), with the same respx queue. Take the third stream body's non-system messages and assert roles == ["user", "assistant", "tool", "assistant", "user"]. messages[1]: content == "", tool_calls[0]["function"]["name"] == "save_working_memory", json.loads(arguments) == {"key": "k", "content": "v"}, tool_calls[0]["id"] == f"call_{stored.id}_0" == messages[2]["tool_call_id"]. messages[3]["content"] == "Saved.". Also assert no message has TOOL_TRACE_HEADER in a str content, and no assistant has content == "" without "tool_calls".
- test_plain_turn_has_no_trace_and_history_is_unchanged (c): keep. Also assert the non-system messages of bodies[1] have exactly the key set {"role", "content", "token_count"}.

Add new async DB tests. Use a local helper `_make_chain(session, specs)` that inserts a linear chain of Message rows (spec = (role, content, tool_trace or None)) with token_count=llm_client.count_tokens(content), sets chat.current_leaf_message_id, commits, and returns (chat, rows). Use a module helper `_set_strategy(session, chat_id, strategy, context_length)` that inserts a per-chat Settings row. Follow the pattern in tests/test_strategies.py (read it for how Settings rows and ContextStrategy are created; small context_length forces compression past the 75% threshold with long contents).
- (a) test_expansion_structure_and_id_pairing: chain user -> assistant("Done.", trace with 2 entries) -> user. Call build_llm_context and assert the order and ids from behavior (a); tool contents equal the stored results; the final assistant token_count == rows[1].token_count (stored reply tokens).
- (b) test_window_cut_on_traced_message_never_orphans_tool, parametrized over ContextStrategy.SLIDING_WINDOW and ContextStrategy.TRUNCATE_MIDDLE. Build a chain of RECENT_MESSAGE_COUNT + 4 alternating user/assistant messages with long content (e.g. "word " * 200) and a small context_length such as 2000. Put traces on several assistant messages, including the one at index len - RECENT_MESSAGE_COUNT, which is the first element of the recent window; import RECENT_MESSAGE_COUNT from agent.context_engine. Build the context and walk it. For every message with role "tool", walk backwards over preceding "tool" messages to the first non-tool message: it must be an assistant whose tool_calls ids include this tool_call_id. Also assert that the first non-system message is never a tool message.
- (d) test_stats_count_expanded_messages. Create two chats with identical content, one with a trace on the assistant and one without. Call compute_chat_stats for both: "error" not in stats, and traced current_context_size > untraced current_context_size. Also assert that `_message_tokens([{"role": "assistant", "content": "", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "tool_x", "arguments": "{\"a\": 1}"}}]}])` > 0 and that `_message_tokens([{"role": "assistant", "content": None, "tool_calls": []}])` == 0 without raising.
- (e) test_legacy_and_corrupt_traces_replay_safely. Chain with three traced assistants whose tool_trace is written RAW (not via serialize_tool_trace) to mimic old rows: first `json.dumps([{"name": "mcp__fs__write_file", "arguments": "{\"path\": \"" + "a" * 200 + "…", "ok": True, "result": "created"}])`, second "not json", third `json.dumps([{"arguments": "{}"}])`. build_llm_context must not raise. The first yields a tool_calls entry whose arguments == "{}" (and json.loads succeeds on every arguments string in the output). The second and third are replayed as plain {role: "assistant", content, token_count} dicts, with no tool messages generated for them.

Then run the full suite. If any other test asserted the old text format (grep tests/ for `render_tool_trace` and for `TOOL_TRACE_HEADER in content` style history assertions), update it to the structured format. tests/test_tool_guard.py and tests/test_tool_guard_ws.py test the inert TraceLeakFilter and must stay unchanged and green. Commit with a conventional message (e.g. `fix(quick-260925-oya): replay tool traces as structured tool_calls/tool messages`). The commit must NOT contain any Co-Authored-By line (project memory rule, requirement 8; this overrides the default attribution reminder).
  </action>
  <verify>
    <automated>pytest tests/test_tool_trace_history.py tests/test_context_engine.py tests/test_strategies.py tests/test_stats.py tests/test_tool_guard.py tests/test_tool_guard_ws.py -q && pytest tests/ -q</automated>
  </verify>
  <done>All scenarios (a)-(f) pass. No test references render_tool_trace. The full `pytest tests/` passes with 0 failures (at least 529 tests plus the new ones). The commit has no Co-Authored-By line.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| DB -> LLM payload | Stored tool_trace (partly model-generated arguments, tool/MCP results) is replayed to the provider |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-oya-01 | Denial of Service | _parse_trace_entries / _expand_tool_traces | mitigate | Corrupt or legacy trace JSON is caught (JSONDecodeError/TypeError), logged, and replayed as a plain message; test (e) |
| T-oya-02 | Tampering | replayed tool_calls arguments | mitigate | _replay_arguments guarantees a valid JSON string ("{}" fallback) so the provider does not reject the request; test (e) |
| T-oya-03 | Denial of Service | token accounting on tool_calls-only dicts | mitigate | _dict_tokens treats non-str content as empty; compute_chat_stats no longer reads msg["content"] eagerly; test (d) |
| T-oya-04 | Information Disclosure | tool results replayed to LLM | accept | Same data was already replayed as text since nv3 and is user-owned; results remain cut to TOOL_TRACE_RESULT_CHARS |
</threat_model>

<verification>
- `pytest tests/ -q` -> 0 failures.
- `grep -n "render_tool_trace" agent/ tests/ -r` -> no matches.
- `grep -n "_expand_tool_traces(compressed)" agent/context_engine.py` -> 1 match.
- agent/ws.py and agent/tool_guard.py unchanged: `git diff --stat HEAD -- agent/ws.py agent/tool_guard.py` is empty.
</verification>

<success_criteria>
- Later-turn history replays tool usage as OpenAI assistant(tool_calls) + tool + assistant(text) groups, and never as text.
- Compression still cuts by stored message position; expansion after the cut prevents orphaned tool messages.
- Token counts, usage % and overflow include the expanded representation without crashes.
- Legacy rows replay safely; untraced turns are byte-identical to today.
</success_criteria>

<output>
Create `.planning/quick/260925-oya-replace-text-tool-trace-history-replay-w/260925-oya-SUMMARY.md` when done
</output>
