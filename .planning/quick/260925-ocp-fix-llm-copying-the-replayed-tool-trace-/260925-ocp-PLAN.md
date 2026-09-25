---
phase: quick-260925-ocp
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - agent/tool_guard.py
  - agent/context_engine.py
  - agent/ws.py
  - tests/test_tool_guard.py
  - tests/test_tool_guard_ws.py
autonomous: true
requirements: [QUICK-260925-ocp]

must_haves:
  truths:
    - "When the model appends '[Tool calls actually executed for this reply]' + trace lines to its reply, the client never receives the header or the trace lines as token frames"
    - "The persisted assistant message content contains neither the header nor the trace lines; the reply text before it is intact, with the whitespace that preceded the header removed"
    - "Replies without the header are streamed and persisted with byte-identical concatenated text (all existing tests stay green)"
    - "Every LLM token stream in agent/ws.py (first stream both branches, action-claim retry, post-tool follow-up, rejected-transition re-prompt, invariants justify/retract) goes through its own TraceLeakFilter instance"
    - "The action-claim guard and the invariants self-critique see the filtered (accumulated) text"
  artifacts:
    - path: "agent/tool_guard.py"
      provides: "TOOL_TRACE_HEADER constant and TraceLeakFilter streaming filter"
      contains: "class TraceLeakFilter"
    - path: "agent/context_engine.py"
      provides: "Re-uses TOOL_TRACE_HEADER imported from agent.tool_guard (context_engine.TOOL_TRACE_HEADER still importable)"
      contains: "from agent.tool_guard import TOOL_TRACE_HEADER"
    - path: "agent/ws.py"
      provides: "Per-stream filter wiring via small helpers"
      contains: "TraceLeakFilter"
    - path: "tests/test_tool_guard.py"
      provides: "Unit tests for TraceLeakFilter"
      contains: "TraceLeakFilter"
    - path: "tests/test_tool_guard_ws.py"
      provides: "WS-level leak test"
      contains: "TOOL_TRACE_HEADER"
  key_links:
    - from: "agent/ws.py"
      to: "agent/tool_guard.py::TraceLeakFilter"
      via: "new filter instance per stream, feed() per token, flush() at stream end"
      pattern: "TraceLeakFilter\\(\\)"
    - from: "agent/context_engine.py"
      to: "agent/tool_guard.py::TOOL_TRACE_HEADER"
      via: "import (single source of truth, no import cycle: tool_guard imports only re)"
      pattern: "from agent.tool_guard import TOOL_TRACE_HEADER"
---

<objective>
Stop the LLM's imitation of the replayed tool-trace block from reaching the client or the database.

Purpose: quick 260925-nv3 replays `TOOL_TRACE_HEADER` + "- name(args) -> ok: result" lines on past assistant messages in the outbound context. Real-model verification (qwen3.5-9b) showed the model copying that pattern at the end of its own reply, so the user saw and the DB persisted a fake trace block. A pure streaming filter drops the header and everything after it, per stream, at every token-forwarding site in ws.py.

Output: `TraceLeakFilter` in agent/tool_guard.py (+ header constant moved there), wiring in agent/ws.py, unit tests and one WS-level test.
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/quick/260925-nv3-fix-llm-hallucinated-tool-actions-persis/260925-nv3-SUMMARY.md
@agent/tool_guard.py
@agent/ws.py
@tests/test_tool_guard.py
@tests/test_tool_guard_ws.py

<interfaces>
From agent/context_engine.py (current):
- line 24: `TOOL_TRACE_HEADER = "[Tool calls actually executed for this reply]"`
- `def render_tool_trace(raw: str | None) -> str` returns `"\n".join([TOOL_TRACE_HEADER, *lines])`
- context_engine imports `from agent import invariants, memory, profile, tasks` and `agent.llm_client`; it does NOT import tool_guard today.
- tests/test_tool_trace_history.py does `from agent.context_engine import (..., TOOL_TRACE_HEADER, ...)` — must keep working.

From agent/tool_guard.py: imports only `re`. Exports TOOL_USE_RULE, ACTION_CLAIM_REMINDER, looks_like_action_claim.

From agent/ws.py — token-forwarding sites (all must be filtered):
1. `_stream_action_claim_retry` (~line 213): `async for event in llm_client.stream_chat(..., tools=tool_schemas)`; on content: sends a `"\n\n"` token frame before the FIRST retry content, then `retry_text += event["content"]` and sends it; `tool_calls` events set retry_tool_calls. Exceptions are logged (`action_claim_reprompt_failed`) and non-fatal.
2. First stream, tools branch (~line 326): dict events; `content` → `assistant_text +=` + token frame; `tool_calls` → pending_tool_calls. Exception = fatal (error frame, delete user_msg, return).
3. First stream, no-tools branch (~line 341): plain str tokens. Same fatal except.
4. Post-tool follow-up stream (~line 422): plain str tokens, fatal except.
5. Rejected-transition re-prompt (~line 509): plain str tokens, non-fatal except (`transition_illegal_reprompt_failed`).
6. Invariants justify/retract (~line 547): plain str tokens, accumulates into BOTH `justification_text` and `assistant_text`, non-fatal except (`invariant_justify_retract_failed`).
Guard: line ~371 `looks_like_action_claim(assistant_text)`; retry merge: `assistant_text += "\n\n" + retry_text` when retry_text non-empty.

From tests/test_memory_ws.py (reused helpers): `_plain_content_response(text)` streams text as one word-plus-space per SSE chunk (so the header naturally splits across chunks); `_tool_calls_response([(id, name, args_json)])`; `_queue_responses`; `_send_and_drain`; `_get_message`. tests/test_tool_guard_ws.py already has `_run_turn(responses, content)` returning (route, frames, message), `_stream_bodies(route)` (only "stream": true bodies) and `SAVE_CALL`.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: TraceLeakFilter streaming filter + header constant move + unit tests</name>
  <files>agent/tool_guard.py, agent/context_engine.py, tests/test_tool_guard.py</files>
  <behavior>
    - Plain text (no header) fed in any chunking (whole, word chunks, 1-char chunks) → "".join(feed outputs) + flush() == input exactly, including trailing whitespace and text containing "[" or "[Tool" mid-sentence followed by other text.
    - Header split across chunks (word chunks and 1-char chunks): output == text before the header with trailing whitespace stripped (e.g. "Done.\n\n" + HEADER + "\n- x() -> ok: y" → "Done.").
    - Header at the end after "\n\n" → only the reply text before it, no trailing "\n\n".
    - Header in the middle → everything after it is dropped, including later chunks (feed returns "" once dropped; flush returns "").
    - Partial prefix never completed ("Answer [Tool" and "Answer [Tool calls") → feed withholds the tail, flush() returns it unchanged; total output == input.
    - Empty chunks ("") → feed returns "" and do not change state.
    - Two instances are independent (one in dropped state does not affect the other).
    - context_engine.TOOL_TRACE_HEADER is tool_guard.TOOL_TRACE_HEADER (same object/value).
  </behavior>
  <action>
In agent/tool_guard.py: move `TOOL_TRACE_HEADER = "[Tool calls actually executed for this reply]"` here as a module constant (tool_guard imports only `re`, so there is no cycle). In agent/context_engine.py delete the local definition and add `from agent.tool_guard import TOOL_TRACE_HEADER` in the local-imports block (keeping stdlib → third-party → local order); `render_tool_trace` and any other use keep working, and `from agent.context_engine import TOOL_TRACE_HEADER` in tests/test_tool_trace_history.py keeps working via the imported name.

Add `class TraceLeakFilter` (class docstring: streaming filter that drops a model-imitated tool-trace block) with `__init__(self) -> None` setting `self._pending: str = ""` and `self._dropped: bool = False`, and methods:
- `feed(self, chunk: str) -> str`: if dropped return "". Build `buf = self._pending + chunk`. If `TOOL_TRACE_HEADER` is found in buf at index idx: set dropped, clear pending, return `buf[:idx].rstrip()` (whitespace/newlines preceding the header are never emitted). Otherwise compute the hold start with a private helper `_hold_start(buf: str) -> int`: find the longest suffix of buf of length 1..len(HEADER)-1 that is a prefix of TOOL_TRACE_HEADER (start = len(buf) if none), then move start left over any whitespace characters (`str.isspace()`) immediately before it. Emit `buf[:start]`, keep `buf[start:]` in pending. Rationale for holding the trailing whitespace run: the whitespace before a header must be droppable, and holding it only delays bytes, never changes them.
- `flush(self) -> str`: if dropped return ""; otherwise return and clear pending (unchanged bytes, e.g. a never-completed "[Tool calls" prefix or trailing whitespace).
Once dropped, the instance stays dropped for the rest of its life (one instance per stream). Type hints and single-line docstrings on every method; no logging needed in this pure class.

In tests/test_tool_guard.py add tests covering every bullet in <behavior> (use pytest.mark.parametrize for chunk sizes 1, 3, word-split and whole). Add a small test helper `_run_filter(chunks: list[str]) -> str` returning the joined feed outputs plus flush(), and `_chunks(text: str, size: int) -> list[str]`. Import TOOL_TRACE_HEADER from agent.tool_guard. Write the tests first (RED), then implement (GREEN).
  </action>
  <verify>
    <automated>pytest tests/test_tool_guard.py tests/test_tool_trace_history.py -v</automated>
  </verify>
  <done>TraceLeakFilter exists in agent/tool_guard.py; TOOL_TRACE_HEADER is defined only in tool_guard.py (grep -v '^#' agent/context_engine.py | grep -c 'TOOL_TRACE_HEADER = ' returns 0); all new unit tests and test_tool_trace_history.py pass.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Wire a per-stream filter into every ws.py token site + WS-level leak test</name>
  <files>agent/ws.py, tests/test_tool_guard_ws.py</files>
  <behavior>
    - WS turn: first response is a tool call (SAVE_CALL), follow-up response is the text "Файл `1.txt` успешно скопирован.\n\n" + TOOL_TRACE_HEADER + "\n- mcp__filesystem_2_copy_file({}) -> ok: Successfully copied" → exactly 2 streaming requests; concatenated token frames == "Файл `1.txt` успешно скопирован."; no token frame contains the header or "mcp__filesystem_2_copy_file"; persisted message.content contains neither and equals the reply text before the header; last frame is "done".
    - All existing ws tests (tool guard retry, memory, tasks, invariants, tool trace history) stay green.
  </behavior>
  <action>
In agent/ws.py import `TraceLeakFilter` from agent.tool_guard alongside the existing names. Add two small private helpers near `_stream_action_claim_retry` (type hints + single-line docstrings):
- `async def _emit_filtered(websocket: WebSocket, trace_filter: TraceLeakFilter, token: str) -> str`: `safe = trace_filter.feed(token)`; if safe is non-empty send `{"type": "token", "content": safe}`; return safe.
- `async def _flush_filtered(websocket: WebSocket, trace_filter: TraceLeakFilter) -> str`: same with `trace_filter.flush()`.
Never send an empty-content token frame.

Wire EVERY stream site, each creating its OWN `TraceLeakFilter()` immediately before its `async for` (state must not leak between streams), accumulating only the returned safe text:
1. First stream, tools branch: filter only `event["type"] == "content"` (`assistant_text += await _emit_filtered(...)`); `tool_calls` events unchanged. After the loop, still inside the try: `assistant_text += await _flush_filtered(...)`.
2. First stream, no-tools branch: same with plain tokens, flush after the loop inside the try.
3. `_stream_action_claim_retry`: own filter; compute `safe = trace_filter.feed(event["content"])`; if safe non-empty: when retry_text is still empty first send the existing `"\n\n"` separator frame, then `retry_text += safe` and send the safe frame. Flush after the try/except (before `return`) with the same separator rule, so a non-fatal stream error still emits the held tail as today's code would have emitted it. Keep the `finally: active_streams.pop(...)`.
4. Post-tool follow-up stream: own filter, `assistant_text += await _emit_filtered(...)`, flush after the loop inside the try (the except is fatal — unchanged).
5. Rejected-transition re-prompt: own filter, emit per token, flush after the try/except block (non-fatal path keeps the held tail as today).
6. Invariants justify/retract: own filter; `safe = await _emit_filtered(...)` then add safe to BOTH justification_text and assistant_text; flush after the try/except the same way into both.
Do not change anything else: `looks_like_action_claim(assistant_text)`, `echo_text`, the `"\n\n" + retry_text` merge, `run_self_critique(..., assistant_text, ...)` and persistence already consume the accumulated (now filtered) text. Exception paths keep existing behavior (fatal sites still send LLM_ERROR, delete user_msg, return). Follow CLAUDE.md: structlog only, no print, type hints.

In tests/test_tool_guard_ws.py import TOOL_TRACE_HEADER from agent.tool_guard and add `test_leaked_trace_block_is_not_streamed_or_persisted` (decorated `@respx.mock`) using `_run_turn([_tool_calls_response([SAVE_CALL]), _plain_content_response(LEAKY_REPLY)], content="copy 1.txt")`, asserting everything in <behavior>; count requests with `_stream_bodies(route)` only. Rationale for starting with a tool call: a plain leaked claim as the FIRST response would also trigger the action-claim retry (the filtered text "…скопирован." is a claim), which would muddy the assertion; the follow-up stream is the real-world leak site from the qwen verification.

Finally run the full suite; the previous count was 514 passing and must now be 514 + the new tests, all green.
  </action>
  <verify>
    <automated>pytest tests/test_tool_guard_ws.py -v && pytest tests/ -q</automated>
  </verify>
  <done>Every `async for` over `llm_client.stream_chat` in agent/ws.py has its own `TraceLeakFilter()` (grep -c 'TraceLeakFilter()' agent/ws.py returns 6); no remaining direct `{"type": "token", "content": token}` / `event["content"]` sends bypass the filter (only the retry "\n\n" separator and the helpers send token frames); new WS test passes; `pytest tests/` is fully green.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| LLM output → client/DB | Model-generated text is untrusted; it can imitate internal context markers |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-ocp-01 | Spoofing | agent/ws.py token streams | mitigate | TraceLeakFilter drops any model-emitted TOOL_TRACE_HEADER block so a fabricated "executed tool calls" record is never shown or persisted (and therefore never replayed as if real) |
| T-ocp-02 | Tampering | persisted Message.content | mitigate | Filter is applied before accumulation into assistant_text, so the DB only stores filtered text; real traces live only in Message.tool_trace written from dispatch results |
| T-ocp-03 | Denial of service | TraceLeakFilter buffering | accept | Pending buffer holds at most len(HEADER)-1 chars plus a trailing whitespace run; flushed at stream end |
</threat_model>

<verification>
- `pytest tests/test_tool_guard.py tests/test_tool_trace_history.py tests/test_tool_guard_ws.py -v` passes.
- `pytest tests/ -q` fully green (514 previous + new tests).
- `grep -n "TOOL_TRACE_HEADER = " agent/*.py` shows only agent/tool_guard.py.
</verification>

<success_criteria>
- A reply ending in "\n\n[Tool calls actually executed for this reply]\n- …" reaches the client and DB as only the text before the header.
- Replies without the header are unchanged in concatenated streamed text and persisted content.
- Each of the six stream sites uses its own filter instance with a flush on completion.
- Commits contain no Co-Authored-By line (project memory rule).
</success_criteria>

<output>
Create `.planning/quick/260925-ocp-fix-llm-copying-the-replayed-tool-trace-/260925-ocp-SUMMARY.md` when done
</output>
