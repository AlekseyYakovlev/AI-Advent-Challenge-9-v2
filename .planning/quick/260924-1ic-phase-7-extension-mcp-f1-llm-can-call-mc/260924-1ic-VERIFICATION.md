---
phase: quick-260924-1ic
verified: 2026-09-24T00:00:00Z
status: human_needed
score: 9/9 must-haves verified (code + pytest); 1 browser-only item deferred to human
has_blocking_gaps: false
overrides_applied: 0
re_verification: false
gaps: []
human_verification:
  - test: "Run the chat in a browser with filesystem.exe connected and send 'используй инструмент list_allowed_directories'"
    expected: "A collapsed details.tool-call-card appears (summary 'Filesystem · list_allowed_directories — ok'), stays collapsed after the done re-render, opens on click and shows the arguments and a result containing C:\\Projects\\AiAdventAgentV2; the assistant follow-up bubble reflects the result"
    why_human: "Playwright run was blocked (exit 2, ports 8000/8001 held by the user's app). Card rendering, collapsed default and survival across renderMessages were verified by code reading and WS-frame tests only, never in a browser."
  - test: "Run scripts/e2e_mcp_chat_playwright.py once ports 8000/8001/18765 are free"
    expected: "Exit 0 with PASS lines; ports freed; pre-existing filesystem.exe PIDs still alive"
    why_human: "Cannot be run while the user's own app is up (must not kill it). Script only py_compile-checked and its stub app exercised in-process by the executor."
---

# Quick Task 260924-1ic: MCP-F1 Verification Report

**Goal:** The LLM can call MCP tools (from the current user's connected + enabled MCP servers) during a chat turn, results are fed back to the model, and a collapsed tool-call card is shown in the chat UI.
**Branch:** Day16 (HEAD fff5f4a). **Re-verification:** No.
**Status:** human_needed. All code-level truths are verified against the actual files; the only open item is the browser render of the cards, which the caller already accepted as not browser-verified.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Turn tools = built-ins + current user's connected AND enabled MCP tools; others add nothing | VERIFIED | `agent/ws.py:261-267` builds `build_tool_schemas() + toolset.schemas` from `chat.user_id`. `agent/mcp_tools.py:161-169` filters `list_servers(session, user_id)` (per-user), `row.enabled`, and `get_live_tools(...) is not None`. Tests: `test_toolset_empty_without_servers_or_connection`, `test_toolset_skips_disabled_connected_server`, `test_disconnected_server_adds_no_tools_and_unknown_call_is_handled`. |
| 2 | Namespaced `mcp__<slug>__<tool>`, regex-valid, collision-safe, maps to one binding | VERIFIED | `mcp_tools.py:56-80` (_sanitize, _exposed_name with sha1 fallback, None on hash collision), `:105-152` (taken seeded with `reserved`, duplicate slug gets `-<id>`). Tests `test_exposed_names_never_shadow_builtins_and_match_pattern` (incl. tool named `save_working_memory`), `test_duplicate_slugs...`, `test_long_tool_name_is_hashed_within_limit`. |
| 3 | Namespaced call runs `call_tool` on the user's live session; text + is_error returned as role=tool; model then answers | VERIFIED | `tools.py:98-101,189-258`, `mcp_tools.py:202-260`, `ws.py:323-355`. E2E `test_mcp_tool_call_turn_returns_result_to_model` asserts tool_call frame, role=tool message with matching `tool_call_id` and "ping-42", one done. |
| 4 | Echoed assistant tool_calls never carry empty/whitespace arguments (copy only) | VERIFIED | `ws.py:52-67` returns copies, replaces missing/None/non-str/whitespace with `"{}"`; used at `ws.py:337`. `pending_tool_calls` passed to dispatch (`ws.py:327`) and to `run_self_critique` (`:462`) stays unmutated. E2E `test_empty_arguments_are_echoed_as_empty_object` asserts `"{}"` in second request. Only whitespace/None variants of the echo helper are untested (logic is straightforward). |
| 5 | Every failure becomes a visible tool result; exactly one done; Agent survives | VERIFIED | `mcp_tools.py:204-240`: not-connected, `TimeoutError` (py3.13 alias of asyncio's), `McpError`, and generic `Exception` (CancelledError is BaseException and propagates) all return `_error(...)`. `tools.py:222-231` handles malformed / non-object args. `ws.py:97-103` swallows toolset-build failures. Tests: is_error, invalid types, disconnect, die_after, timeout, and E2E fail case (no error frame, one done). |
| 6 | Results capped at MCP_TOOL_RESULT_MAX_CHARS with marker | VERIFIED | `mcp_tools.py:243-248`; `test_result_is_capped_with_marker` (truncated=True, marker present). |
| 7 | No-MCP path and built-in tools unchanged | VERIFIED | `dispatch_tool_calls` built-in branch is the same code (only extra `arguments`/`mcp` keys); `build_tool_schemas()` still 6; `mcp_bindings` defaults None. `test_builtin_dispatch_unchanged_with_bindings_and_mixed_order`. I re-ran test_memory_ws, test_task_ws, test_tools: pass. TOOL_ERROR still sent for built-ins (`ws.py:400`). |
| 8 | UI: one collapsed card per call, textContent only, survives done re-render | VERIFIED in code / needs browser | `app.js:941-988` builds `<details>` (no `open` set) with createElement + textContent only; grep confirms no `innerHTML` between `buildToolCallCard` and `removeLoadingBubble`. `handleWsMessage` `tool_call`/`done` (`:1245-1253`) moves pending cards to `state.toolCallsByMessage[message_id]`; `renderMessages` (`:188-192`) re-attaches them after `container.innerHTML = ''`. app.js has 0 bare LF (CRLF preserved). Not executed in a browser (see human_verification). |
| 9 | Real filesystem.exe port-free check | VERIFIED | `tests/test_mcp_real_filesystem.py` ran and passed (not skipped, `-rs` shows no skips): empty-arguments dispatch of `mcp__filesystem__list_allowed_directories`, no leftover PID, protected PIDs alive; test never kills (grep: no kill/terminate). |
| 10 | Playwright real-server run | UNCERTAIN (unverifiable_runtime, accepted) | Exit 2 BLOCKED (ports busy) per SUMMARY; SUMMARY states honestly that browser flow did not run. Recorded under human_verification, not a failure per caller instruction. |

**Score:** 9/9 code-verifiable truths verified; #10 deferred to human.

## Targeted scrutiny items

1. **Per-user isolation - VERIFIED.** No path lets user A's chat reach B's session. Chain: WS connect requires `_user_owns_chat(db, user.id, chat_id)` (`ws.py:572`); toolset built from `chat.user_id` (`ws.py:263`); rows come from `list_servers(session, user_id)`; registry keyed `(user_id, server_id)` (`mcp_client.py:273-296`); binding carries `user_id` from the toolset and `call_mcp_tool` does `get_live_session(binding.user_id, binding.server_id)` (`mcp_tools.py:204`). `test_cross_user_isolation` covers B seeing zero tools, A's name unknown under B's bindings, and a forged `(B, A_server)` binding reporting "not connected". No WS-level cross-user test exists, but the code path above is unambiguous. Unowned chats (`user_id is None`) get no tools at all (`ws.py:265-267`).
2. **Name spoofing/collision - VERIFIED.** Built-ins reserved via `set(TOOL_REGISTRY)` (`ws.py:100`); dispatch uses only the per-turn `mcp_bindings` dict and never parses the name (`tools.py:98`), with an `isinstance(name, str)` guard against unhashable names. A server exposing a tool literally named `save_working_memory` becomes `mcp__..__save_working_memory`. Model-invented `mcp__*` names not in bindings fall through to "unknown tool". A server display name containing text is only used in the description (capped 1024) and slug (sanitized).
3. **Empty arguments - VERIFIED** both at dispatch (`tools.py:222-231`, `test_empty_arguments_treated_as_empty_object[""/"   "]`, plus real-binary test) and in the echo (`ws.py:52-67, 337`, E2E asserts `"{}"`). Note: built-in tools with empty arguments still hit `json.loads("")` -> "malformed arguments" (unchanged pre-existing behaviour, out of scope).
4. **Timeout / size cap / isError / disconnect never crash the turn - VERIFIED** (see truth 5, each has a test). Two corner cases not tested but safe by code: `raw_arguments` non-string (llm_client always builds strings, `llm_client.py:162-170`); many calls in one turn each capped individually (aggregate not capped).
5. **Hot path never calls get_status/_is_alive - VERIFIED.** grep over `agent/mcp_tools.py`, `agent/ws.py`, `agent/tools.py` returns nothing; only callers are the REST handlers in `agent/main.py` (948, 1001, 1074). Trade-off: a silently dead server keeps its cached tools until its session closes, and a call to it then fails via exception or the 30s timeout (returned as a tool result).
6. **Frontend - VERIFIED in code.** textContent only, `dataset.toolCallId` only; survives re-render via `toolCallsByMessage`. Cards vanish on full page reload (documented). Browser render unverified.
7. **No regressions for built-ins - VERIFIED** (see truth 7).
8. **Logging - VERIFIED.** `mcp_tool_call_dispatched` (info): user_id, server_id, tool, ok, duration_ms, result_chars. `mcp_tool_call_failed` (warning): ids, tool, error_type only. `mcp_tool_name_collision`/`mcp_tools_capped`: ids and tool name. No arguments, results or env logged in the new code. `mcp_toolset_failed` logs `str(exc)` only (project convention).

## Behavioral spot-checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| MCP tools/WS/real-binary/built-in suites | `pytest tests/test_mcp_tools.py tests/test_mcp_chat_ws.py tests/test_mcp_real_filesystem.py tests/test_memory_ws.py tests/test_task_ws.py tests/test_tools.py -q -rs` | 50 passed, 0 skipped | PASS |
| No get_status/_is_alive on chat path | grep in mcp_tools/ws/tools | no matches | PASS |
| No innerHTML in card builder | script over app.js | False | PASS |
| app.js CRLF | bare-LF count | 0 | PASS |
| Full suite | (not re-run; orchestrator independently reproduced 399 passed) | - | Accepted |

## Requirements coverage

MCP-F1: SATISFIED in code (per above); `.planning/REQUIREMENTS.md` marks it `[x]`, removes the Future and Out-of-Scope rows, adds the traceability row, MCP-01..06 untouched. Docs: `docs/ARCHITECTURE.md` section "Chat tool calls (built-in + MCP)" and `docs/API_SPEC.md` `tool_call` frame + env table present. Destructive-tool risk stated in SUMMARY and ARCHITECTURE.md.

## Anti-patterns

No TBD/FIXME/XXX, no stubs, no hollow props in changed files. Debt-marker gate: clear.

## Warnings / minor observations (non-blocking)

- W1: A hallucinated or stale `mcp__*` name (not in bindings) is treated as a built-in unknown tool, so it also sends a `TOOL_ERROR` frame (`ws.py:400`) in addition to the tool_call card; the UI then does `setStreaming(false)`/toast just before `done`. Pre-existing behaviour, the disconnected-server test does not assert error frames. Cosmetic.
- W2: `docs/API_SPEC.md` tool_call example contains a literal line break and an unescaped backslash inside the JSON string, so the example is not valid JSON. Cosmetic.
- W3: If the follow-up LLM stream fails after an MCP tool ran (`ws.py:360-375`), the user message is deleted although the tool's side effect (for example write_file) already happened. Follows the existing LLM_ERROR pattern; worth knowing alongside the accepted "no confirmation for destructive tools" risk.
- W4: Per-call cap only; total tool-result volume across many calls in a turn is bounded by call count x 20000 chars.

## Human verification required

1. **Browser check of cards.** With filesystem.exe connected, send "используй инструмент list_allowed_directories". Expect a collapsed card that persists after the response completes, opens to show arguments and a result containing `C:\Projects\AiAdventAgentV2`, and a follow-up assistant answer. Why human: the Playwright run was blocked by the user's running app.
2. **Playwright script** `scripts/e2e_mcp_chat_playwright.py` when ports are free (expect exit 0).

## Gaps summary

None. No must-have failed; no blocking gaps. Only the browser-level confirmation remains.

---
_Verified: 2026-09-24_
_Verifier: Claude (gsd-verifier)_
