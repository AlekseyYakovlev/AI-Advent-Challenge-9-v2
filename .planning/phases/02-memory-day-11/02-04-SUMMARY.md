---
phase: 02-memory-day-11
plan: 04
subsystem: api
tags: [tool-calling, websocket, memory, context-injection, sqlite]

# Dependency graph
requires:
  - phase: 02-memory-day-11 (plan 01)
    provides: agent/memory.py CRUD (list_working_memory, list_long_term_memory, save_working_memory, save_long_term_memory)
  - phase: 02-memory-day-11 (plan 03)
    provides: agent/tools.py (build_tool_schemas, dispatch_tool_calls), stream_chat(..., tools=[...]) discriminated event contract
provides:
  - Read-only working/long-term memory injection in agent/context_engine.py::build_system_prompt
  - Full tool-call round-trip inside agent/ws.py::_handle_chat_message (offer tools, dispatch, feed results back, one follow-up reply)
  - "memory_writes" key on the WebSocket done frame
affects: [03-personalization (profile injection follows this same build_system_prompt append pattern), 04-tasks/05-invariants (reuse this same tool-call round-trip and done-frame reporting pattern unchanged)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Read-only memory injection appended to the single existing system-prompt assembly path (build_system_prompt), never a second context-assembly function"
    - "Exactly one tool round per chat turn: the follow-up stream_chat call omits tools entirely, structurally preventing an unbounded tool-call loop while still allowing multiple tool calls within the one round"
    - "Tool dispatch and the follow-up LLM call run synchronously inside the same chat_locks[chat_id] lock and the same session as the triggering user message - no background task, no second session"

key-files:
  created:
    - tests/test_context_engine_memory.py
    - tests/test_memory_ws.py
  modified:
    - agent/context_engine.py
    - agent/ws.py

key-decisions:
  - "Memory injection order is working memory before long-term memory, both appended after facts/summary, matching the plan's demo ordering"
  - "TOOL_ERROR detail is extracted from the dispatcher's structured {\"error\": ...} JSON content when parseable, falling back to the raw content string otherwise, rather than inventing a second error envelope"
  - "A chat with chat.user_id is None gets tools=None entirely (logged as tools_disabled_unowned_chat) since memory writes cannot be scoped without an owner - matches Task 2's explicit instruction"

patterns-established:
  - "Any future tool (Phases 3-5) that should influence the system prompt follows the same read-only list-and-append pattern in build_system_prompt; any future tool-call round-trip reuses agent/tools.py::dispatch_tool_calls unchanged inside the existing per-chat lock/session"

requirements-completed: [MEM-01, MEM-03]

# Metrics
duration: 7min
completed: 2026-09-20
---

# Phase 02 Plan 04: Close the Tool-Call Loop for Memory Summary

**Every chat turn now offers both memory tools to the LLM, executes any returned tool calls synchronously inside the existing per-chat lock and session, feeds the results back for a final reply, reports the writes on the `done` frame, and injects the resulting memory read-only into the system prompt on every subsequent turn.**

## Performance

- **Duration:** 7 min (commits span 09:51:52 to 09:58:48 local; reading/context-gathering preceded that)
- **Started:** 2026-09-20T09:51:52+03:00 (first task commit)
- **Completed:** 2026-09-20T09:58:48+03:00 (last task commit)
- **Tasks:** 3/3 completed
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- `agent/context_engine.py::build_system_prompt` now appends a working-memory block (this chat's rows) and, when the chat has an owner, an owner-scoped long-term-memory block, after the existing facts/summary parts - extended in place, no second context-assembly path, no changes to `extract_and_update_facts`/`_run_debounced_facts`/`_extract_facts`
- `agent/ws.py::_handle_chat_message` offers `build_tool_schemas()` to the LLM (skipped with a `tools_disabled_unowned_chat` warning for legacy chats with no owner), handles the discriminated `content`/`tool_calls` `stream_chat` events, and on a `tool_calls` event runs `dispatch_tool_calls` synchronously inside the same lock/session, appends the assistant + tool messages, and issues exactly one no-tools follow-up `stream_chat` call for the final reply
- Failed tool calls surface as a `{"type":"error","code":"TOOL_ERROR"}` frame without ending the turn; the `done` frame gained a `memory_writes` key (list of `{id, key, layer}`, default `[]`) while `type`, `message_id`, and `stats` are unchanged
- 12 new tests: 6 direct-function tests proving memory injection, owner-scoping, and token-accounting (`tests/test_context_engine_memory.py`), and 6 full WebSocket end-to-end tests with a mocked tool-calling LLM proving single/two tool-call writes, no-write-without-a-tool-call, malformed-argument tolerance, next-turn re-injection, and cross-user isolation (`tests/test_memory_ws.py`)

## Task Commits

Each task was committed atomically:

1. **Task 1: Read-only memory injection in build_system_prompt** - `84f407c` (feat)
2. **Task 2: Tool-call round-trip inside the per-chat-locked chat turn** - `9b9b361` (feat)
3. **Task 3: End-to-end WebSocket memory-turn tests** - `26fcbd8` (test)

**Plan metadata:** committed alongside this SUMMARY (see final commit in this worktree)

## Files Created/Modified
- `agent/context_engine.py` - `build_system_prompt` appends working-memory and owner-scoped long-term-memory blocks after facts/summary; added `from agent import memory` import
- `agent/ws.py` - `_handle_chat_message` offers tool schemas (owner-gated), handles the two-mode `stream_chat` event contract, dispatches tool calls inside the existing lock/session, issues one no-tools follow-up call, surfaces `TOOL_ERROR` frames, and adds `memory_writes` to the `done` frame; added `json` import and `from agent.tools import build_tool_schemas, dispatch_tool_calls`
- `tests/test_context_engine_memory.py` - New: 6 tests covering no-memory baseline, working-memory injection, long-term-memory injection, cross-user long-term isolation, unowned-chat safety, and stats token-accounting growth
- `tests/test_memory_ws.py` - New: 6 tests covering single tool-call write + report, no-tool-call turns writing nothing, two tool calls both persisting in order, malformed-argument tolerance (`TOOL_ERROR` + still-completing `done`), next-turn system-prompt re-injection, and cross-user write scoping

## Decisions Made
- Kept the `from agent.llm_client import llm_client` / `from agent import memory` import ordering as the plan explicitly specified (memory import below llm_client), rather than strict alphabetical order, since the plan's instruction was unambiguous about placement.
- Parsed the dispatcher's structured JSON error content (`{"error": "..."}`) to build a clean `TOOL_ERROR` detail string when possible, falling back to the raw content string on a parse failure, instead of sending the raw JSON-as-string to the client on every failure.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed a stale acceptance-criteria grep count**
- **Found during:** Task 2 verification
- **Issue:** The plan's acceptance criteria expected `grep -c 'async_session_factory' agent/ws.py` to output `2` ("unchanged from before"). The pre-existing file (before this plan touched it) already had 3 occurrences (1 import + 2 usages: one in `_handle_chat_message`, one in `ws_chat`) - confirmed via `git show HEAD:agent/ws.py | grep -c async_session_factory` before any edits. This plan's changes did not add or remove any `async_session_factory` occurrence.
- **Fix:** No code fix needed - the underlying invariant ("no third session opened") was verified directly by reading the diff and confirming no new `async_session_factory` call was introduced. Treated as a pre-existing miscount in the plan text, not a regression to fix.
- **Files modified:** None (verification-only)
- **Verification:** `git diff agent/ws.py` shows no new `async_session_factory` line added; the count stayed at 3 (1 import + 2 usages) before and after this plan.

---

**Total deviations:** 1 (a verification-count discrepancy in the plan text, not a code defect)
**Impact on plan:** None on functionality - the actual invariant (no third session/lock) holds and was confirmed directly.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. No new packages installed (per the plan's threat model, T-02-SC is not applicable to this plan).

## Verification Evidence

- `pytest tests/test_context_engine_memory.py tests/test_context_engine.py -v` - 13/13 passed.
- `pytest tests/test_strategies.py tests/test_stats.py -q` - 19/19 passed.
- `pytest tests/test_concurrent_ws.py tests/test_ws_auth.py tests/test_ws_security.py tests/test_websocket_cors.py tests/test_ws_origin_validation.py -v` - 21/21 passed.
- `pytest tests/test_memory_ws.py -v` - 6/6 passed.
- `pytest tests/ -q` - 161/161 passed (full suite, run after each task, no regressions).
- `python -c "import agent.ws"` - exit 0.
- All Task 1/2/3 acceptance-criteria grep gates re-run directly and confirmed: `Working memory (this chat` and `Long-term memory (persists across all your chats)` each appear exactly once in `agent/context_engine.py`; `async def build_system_prompt` appears exactly once; `build_tool_schemas` appears 0 times in `agent/context_engine.py`; `git diff` on `agent/context_engine.py` shows no change to the `extract_and_update_facts`/`_run_debounced_facts`/`_extract_facts` region; `dispatch_tool_calls` appears 2 times in `agent/ws.py` (import + call); 0 occurrences of `asyncio.gather`/`asyncio.create_task` in `agent/ws.py`; `"memory_writes"` appears exactly once (JSON-key form) in `agent/ws.py`; `extract_and_update_facts` appears 2 times (import + unchanged call); the `dispatch_tool_calls` call is textually inside the `async with chat_locks[chat_id]:` block at the same indentation as `_persist_user_message`; `def test_` appears 6 times in `tests/test_memory_ws.py` with `memory_writes` appearing 4 times and `respx` 14 times; `api.deepseek.com` appears 0 times in `tests/test_memory_ws.py`.
- `grep -rn "asyncio.create_task" agent/ws.py agent/tools.py agent/memory.py` - returns nothing (plan-level `<verification>` block).
- **Not verified in this session:** the plan's manual/live-LM-Studio step ("with LM Studio running `qwen/qwen3.5-9b`, send 'Запомни навсегда...' and confirm two `tool_call_dispatched` log entries, sidebar rows, and a reply referencing the tool result") - this requires a running LM Studio instance with a loaded model and was not exercised. All verification in this session is via respx-mocked SSE streams and direct-function/WebSocket tests against the real test database, matching the plan's own stated scope for automated verification. The 02-RESEARCH.md live-captured SSE sequences (reused from Plan 03's tests) give confidence the mocked shapes match the real API, but this is not a substitute for the manual check.
- **Not verified in this session:** literal `git rev-parse --abbrev-ref HEAD` reporting `Day11` - this plan was executed inside a per-agent git worktree (`worktree-agent-aeaa6e43c29dd8f01`) forked from `Day11`'s history; the branch-name check applies once the orchestrator merges this worktree's commits back onto `Day11`.

## Next Phase Readiness
- MEM-01 (three separated memory layers actually in use) and MEM-03 (LLM-explicit, tool-call-only writes) are now both real and demonstrable end-to-end: a chat turn can write working/long-term memory via tool calls, report it on `done`, and have it read back into the very next turn's system prompt.
- Phase 3 (Personalization) can extend `build_system_prompt` with a third read-only injected block (user profile) following the exact same append pattern established here, and can reuse `agent/tools.py::dispatch_tool_calls` and the `agent/ws.py` tool-call round-trip unchanged, per STATE.md's locked decision that the dispatcher is built once in Phase 2 and reused by Phases 3-5.
- No blockers.

## Self-Check: PASSED

- FOUND: `agent/context_engine.py`
- FOUND: `agent/ws.py`
- FOUND: `tests/test_context_engine_memory.py`
- FOUND: `tests/test_memory_ws.py`
- FOUND: `.planning/phases/02-memory-day-11/02-04-SUMMARY.md`
- FOUND commit: `84f407c`
- FOUND commit: `9b9b361`
- FOUND commit: `26fcbd8`

---
*Phase: 02-memory-day-11*
*Completed: 2026-09-20*
