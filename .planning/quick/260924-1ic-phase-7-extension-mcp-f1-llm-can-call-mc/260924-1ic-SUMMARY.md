---
phase: quick-260924-1ic
plan: 01
subsystem: agent-chat-tools
tags: [mcp, tool-calling, websocket, chat-ui]
requires:
  - phase: 07-mcp-connection-day-16
    provides: persistent per-(user_id, server_id) MCP session registry, McpServerConfig CRUD
provides:
  - MCP tools of the user's connected and enabled servers offered to the LLM in every chat turn
  - namespaced tool names mcp__<slug>__<tool> with collision safety and a name-to-binding map
  - tool_call WebSocket frame and collapsed tool-call cards in the chat UI
affects: [agent/ws.py, agent/tools.py, ui/static/app.js, docs/API_SPEC.md, docs/ARCHITECTURE.md]
tech-stack:
  added: []
  patterns:
    - per-turn toolset built from the session registry (no ping on the chat path)
    - MCP failures returned as tool results, never raised
key-files:
  created:
    - agent/mcp_tools.py
    - tests/test_mcp_tools.py
    - tests/test_mcp_chat_ws.py
    - tests/test_mcp_real_filesystem.py
    - scripts/e2e_mcp_chat_playwright.py
  modified:
    - agent/mcp_client.py
    - agent/tools.py
    - agent/ws.py
    - agent/schemas.py
    - shared/config.py
    - .env.example
    - ui/static/app.js
    - tests/fixtures/mcp_stdio_server.py
    - docs/ARCHITECTURE.md
    - docs/API_SPEC.md
    - .planning/REQUIREMENTS.md
key-decisions:
  - "MCP names resolve only through the per-turn bindings map; the name is never parsed"
  - "MCP failures are reported through tool_call (ok=false), not TOOL_ERROR, so the client does not stop streaming mid-turn"
  - "Empty tool-call arguments are echoed to the follow-up request as {} on a copy"
requirements-completed: [MCP-F1]
duration: not tracked
completed: 2026-09-24
---

# Quick Task 260924-1ic: MCP-F1 - LLM can call MCP tools from a chat turn

The chat LLM now receives the built-in tools plus the tools of the current user's connected and enabled MCP servers under names like `mcp__filesystem__list_allowed_directories`. Calls run on the live session with a timeout and a result cap, every failure comes back as a tool result, and each call is shown as a collapsed card in the chat.

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | c054d25 | feat(quick-260924-1ic): expose connected MCP tools to the tool dispatcher |
| 2 | f8231f9 | feat(quick-260924-1ic): run MCP tool calls in chat turns and show tool-call cards |
| 3 | 82424f4 | docs(quick-260924-1ic): MCP-F1 docs and requirement |
| 4 | 8974f19 | test(quick-260924-1ic): real filesystem.exe verification (pytest + Playwright) |

No commit carries a Co-Authored-By line. Nothing was pushed or merged.

## What was built

- `agent/mcp_tools.py` (new): `server_slug`, `build_toolset_from_servers` (pure, unit-test seam), `build_mcp_toolset` (DB rows + `get_live_tools`), `call_mcp_tool` (`asyncio.wait_for` with `MCP_TOOL_CALL_TIMEOUT`, `McpError` and generic exception mapping, text flattening, cap with `...[truncated N chars]` marker). It does not import `agent.tools` and never calls `get_status` or `_is_alive` (checked with grep).
- `agent/mcp_client.py`: read-only `get_live_tools(user_id, server_id)`.
- `agent/tools.py`: `dispatch_tool_calls(..., *, mcp_bindings=None)`; result entries now also carry `arguments` and `mcp`. Built-in handling is unchanged. Empty or whitespace MCP arguments are treated as `{}`; non-object JSON gives "arguments must be a JSON object".
- `agent/ws.py`: per-turn toolset merge, one `tool_call` frame per executed call, `_normalize_tool_calls_for_echo` (empty arguments become `"{}"` on a copy only), MCP failures excluded from `TOOL_ERROR` frames. The single tool round and the follow-up without tools are unchanged.
- `agent/schemas.py`: `ToolCallEvent`, preview limits 4000 / 2000. `shared/config.py` and `.env.example`: `MCP_TOOL_CALL_TIMEOUT=30.0`, `MCP_TOOL_RESULT_MAX_CHARS=20000`.
- `ui/static/app.js`: `tool_call` case, `buildToolCallCard` (createElement and textContent only, no innerHTML), cards re-attached in `renderMessages` from `state.toolCallsByMessage`. CRLF preserved (0 bare LF).
- Docs and requirements: ARCHITECTURE section "Chat tool calls (built-in + MCP)", API_SPEC `tool_call` frame and env table, MCP-F1 marked complete.

## Verification actually run

| Check | Result |
|-------|--------|
| `pytest tests/test_mcp_tools.py tests/test_tools.py tests/test_mcp_client.py tests/test_mcp_api.py` | 64 tests, all passed (after fixing a miscount in my own test, see below) |
| `pytest tests/test_mcp_chat_ws.py tests/test_memory_ws.py tests/test_task_ws.py tests/test_invariants_ws.py tests/test_concurrent_ws.py` | 24 passed |
| Full `pytest tests/ -q` (after Task 3 and again after Task 4) | 398 passed, then 399 passed (the extra test is the real filesystem one) |
| app.js bare-LF check | 0 bare LF |
| No innerHTML inside `buildToolCallCard` | confirmed by the verify script |
| REQUIREMENTS / API_SPEC content checks | passed |

### Real-server checks - what ran and what did not

- **Ran and PASSED:** `tests/test_mcp_real_filesystem.py` against the real `C:\Users\Aleksey\go\bin\filesystem.exe` (not skipped: reported PASSED). It connected the server, built the toolset, dispatched `mcp__filesystem__list_allowed_directories` with EMPTY arguments through `dispatch_tool_calls` with MCP bindings, got `ok=True` with a result containing `AiAdventAgentV2`, and found no filesystem.exe from this run left alive. It binds no ports and kills nothing. The user's filesystem.exe PIDs (10608, 26256) and app PIDs (28112, 7332) were still alive after all runs.
- **BLOCKED (exit 2):** `python scripts/e2e_mcp_chat_playwright.py` printed "BLOCKED: port(s) [8000, 8001] are in use" because the user's own app is running. This is the expected outcome, and I did not work around it. As a consequence **the browser flow was never executed: the tool-call cards, their collapsed default, and their survival across the done re-render were NOT browser-verified.** The script itself compiles (`py_compile`), and I verified only its stub-LLM app in-process (models, load/unload, tool-call SSE, follow-up SSE, non-streaming completion). The card logic in app.js is covered only by code review, the innerHTML grep and the CRLF check, plus the WS tests that assert the `tool_call` frames.
- Task 4 verify command semantics: pytest passed, `py_compile` passed, Playwright script exit 2 (accepted as blocked per plan).

## Known risks and limitations (for the user to decide on)

1. **Destructive filesystem tools are unconfirmed.** `write_file`, `edit_file`, `move_file`, `create_directory` and any delete-style tool exposed by filesystem.exe are offered to the model like any other tool and run with NO confirmation step. The server's allowed-directory list is the only bound. Documented in ARCHITECTURE.md. A confirm-before-write gate would be the natural follow-up.
2. **Single tool round per turn.** The first request carries the tools; the follow-up request carries the results and no tools. A flow such as "list, then read" needs a second user message.
3. **Tool cards are not persisted.** They live in page state (`state.toolCallsByMessage`) and survive re-renders within the session, but disappear after a full page reload or a chat reload.
4. **Prompt injection.** Tool descriptions and results are untrusted text from an external process; residual risk (the model following injected text in its prose) is documented.
5. **Log isolation choice for the Playwright script.** `ui/supervisor.py` resolves `logs/agent.log` relative to the process cwd, and launches the Agent with `python -m uvicorn agent.main:app`, so the script runs `run.py` by absolute path with `cwd=<scratch dir>` and `PYTHONPATH=<repo root>`. Agent logs therefore land in the scratch directory, not in the repo's `logs/agent.log` (and `.env` in the repo is not read; the needed env values are passed explicitly). This path was designed from code reading only, since the script could not run here.

## Deviations from Plan

### Auto-fixed / judgment calls

**1. [Rule 3 - Blocking] Worktree base reset.** The worktree HEAD (83f1399) was an ancestor of the required base, so per the startup check I ran `git reset --hard 68cd1d2` before doing any work.

**2. [Minor] Toolset loading in a helper.** The plan described an inline try/except in `_handle_chat_message`. I put it in a private `_load_mcp_toolset` helper in `agent/ws.py` to respect the function-length convention. Behavior is identical (warning `mcp_toolset_failed`, falls back to an empty toolset). The frame construction lives in `_tool_call_frame` and `_preview` for the same reason.

**3. [Minor] Playwright import check precedes preflight.** `main()` checks that Playwright imports (exit 4) before running the port/binary preflight (exit 2/3). Because Playwright is installed here, the observable order is as planned.

**4. [Minor] Scratch dir kept on failure.** The script removes its scratch directory only when every check passed; on any failure it keeps it and prints the path so `app.log` and `e2e.png` can be inspected (the plan said to remove it with a retry on lock errors, which it still does on success).

**5. [Own test error, no product impact]** My first version of `test_duplicate_slugs_and_colliding_tool_names_get_distinct_names` asserted 5 names where the input has 4 tools; corrected to 4 before the Task 1 commit.

### Regressions from Tasks 1-2 fixed in Task 3

None; the full suite was green when run for Task 3.

## Assumption Drift (advisory)

- **Found during:** Task 1 (test authoring). **Planned:** tools "a.b" and "a_b" on one server get distinct names. **Actual:** both sanitize to `a_b`, so the second one gets the hashed form `mcp__fixture__a_b-e4da20d5`; behavior matches the intent, only the exact names differ from what a reader might expect.

## Known Stubs

None. (The Playwright script's stub LLM is a deliberate test double, not product code.)

## Threat Flags

None beyond the plan's threat model. New surface is limited to the `tool_call` WebSocket frame (same authenticated socket, same user) and MCP tool calls routed through the existing per-user registry.

## Self-Check: PASSED

- Files present: agent/mcp_tools.py, tests/test_mcp_tools.py, tests/test_mcp_chat_ws.py, tests/test_mcp_real_filesystem.py, scripts/e2e_mcp_chat_playwright.py.
- Commits present: c054d25, f8231f9, 82424f4, 8974f19.
