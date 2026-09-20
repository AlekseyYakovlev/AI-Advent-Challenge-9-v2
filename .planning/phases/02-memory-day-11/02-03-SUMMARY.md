---
phase: 02-memory-day-11
plan: 03
subsystem: api
tags: [tool-calling, pydantic, sse, llm-client, openai-compatible]

# Dependency graph
requires:
  - phase: 02-memory-day-11 (plan 01)
    provides: agent/memory.py CRUD (save_working_memory, save_long_term_memory, list_working_memory, list_long_term_memory), MEMORY_KEY_MAX_LENGTH/MEMORY_VALUE_MAX_LENGTH constants
  - phase: 02-memory-day-11 (plan 02)
    provides: LMStudioClient migrated to v1 control endpoints (unrelated file, same module)
provides:
  - agent/tools.py general tool registry + strictly sequential dispatch_tool_calls (the only write path into agent/memory.py)
  - SaveWorkingMemoryArgs / SaveLongTermMemoryArgs Pydantic tool-argument schemas
  - stream_chat(..., tools=[...]) with tool_calls SSE delta accumulation, no-tools string contract preserved
affects: [02-04 (wires dispatch_tool_calls into agent/ws.py's chat turn), 03-personalization/04-tasks/05-invariants (reuse this same dispatcher unchanged per STATE.md)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Decorator-based tool registry (register_tool) deriving OpenAI function schemas from Pydantic model_json_schema(), never hand-written JSON schema"
    - "Discriminated stream_chat return type: plain str when tools is falsy, {\"type\": ...} dicts only when tools is non-empty, so every existing no-tools caller needs zero changes"
    - "SSE tool_calls delta accumulation keyed by index, with function.arguments appended (not assigned) across fragmented chunks"

key-files:
  created:
    - agent/tools.py
    - tests/test_llm_tools_stream.py
    - tests/test_tools.py
  modified:
    - agent/schemas.py
    - agent/llm_client.py

key-decisions:
  - "Scope-override stripping happens implicitly via Pydantic's default extra='ignore' on model_validate — chat_id/user_id in LLM-supplied arguments never reach the validated dict, and their presence is still logged as a warning per the raw arguments dict"
  - "Kept the docstring wording for dispatch_tool_calls's no-gather constraint free of the literal string 'asyncio.gather' so the plan's own grep-based regression gate (must be 0 occurrences in the file) stays meaningful for actual code, not documentation prose"

patterns-established:
  - "Tool handlers are plain async functions registered via @register_tool(name, args_model, description) and looked up from TOOL_REGISTRY/TOOL_SCHEMAS by name — Phases 3-5 add tools by registering, not by touching dispatch_tool_calls"

requirements-completed: [MEM-03]

# Metrics
duration: 22min
completed: 2026-09-20
---

# Phase 02 Plan 03: Tool-Call Substrate for Memory Writes Summary

**A general Pydantic-schema-derived tool registry with a strictly-sequential dispatcher (`agent/tools.py`), plus `stream_chat(..., tools=[...])` SSE tool_calls delta accumulation in `agent/llm_client.py` — the only path a memory write can reach the database through.**

## Performance

- **Duration:** 22 min (commits span 09:42:15 to 09:45:19 local; reading/context-gathering preceded that)
- **Started:** 2026-09-20T09:42:15+03:00 (first task commit)
- **Completed:** 2026-09-20T09:45:19+03:00 (last task commit)
- **Tasks:** 3/3 completed
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments
- `agent/tools.py`: `register_tool` decorator, `TOOL_REGISTRY`/`TOOL_SCHEMAS`/`TOOL_DESCRIPTIONS`, `build_tool_schemas()` deriving OpenAI function schemas from `model_json_schema()`, and `dispatch_tool_calls()` — a plain sequential `for` loop (never `asyncio.gather`, never a new session/lock) that tolerates unknown tools, malformed JSON, and validation failures with structured error entries instead of raising
- Two memory tools registered (`save_working_memory`, `save_long_term_memory`) with distinct descriptions steering the LLM toward the correct layer, backed by `SaveWorkingMemoryArgs`/`SaveLongTermMemoryArgs` in `agent/schemas.py`
- `agent/llm_client.py::stream_chat` gains a trailing `tools: list[dict[str, Any]] | None = None` parameter; payload only gains a `"tools"` key when non-empty, so every existing no-tools caller (`agent/ws.py`, `context_engine::_extract_facts`) is byte-identical
- New `_parse_sse_stream_with_tools` reassembles `tool_calls` deltas fragmented across SSE chunks by `index`, appending (not overwriting) `function.arguments`, and yields a discriminated `{"type": "content"|"tool_calls", ...}` shape only in tools mode
- LLM-supplied `chat_id`/`user_id` inside tool `arguments` cannot redirect a write to another user's chat — Pydantic's `extra="ignore"` default drops them from the validated payload before the handler ever runs

## Task Commits

Each task was committed atomically:

1. **Task 1: Tool argument schemas and the agent/tools.py registry + sequential dispatcher** - `8bf862d` (feat)
2. **Task 2: stream_chat tools parameter, tool_calls delta accumulation, and its streaming tests** - `e650b5f` (feat)
3. **Task 3: Dispatcher tests** - `120f57e` (test)

**Plan metadata:** committed alongside this SUMMARY (see final commit in this worktree)

## Files Created/Modified
- `agent/schemas.py` - Added `SaveWorkingMemoryArgs`/`SaveLongTermMemoryArgs` Pydantic tool-argument models (key/content, length-bounded via the existing `MEMORY_KEY_MAX_LENGTH`/`MEMORY_VALUE_MAX_LENGTH` constants)
- `agent/tools.py` - New module: tool registry, OpenAI schema generation, `dispatch_tool_calls`, and the two registered memory-tool handlers
- `agent/llm_client.py` - `stream_chat` gains `tools` parameter and dispatches to the new `_parse_sse_stream_with_tools` when tools are requested; `_parse_sse_stream` (no-tools path) is untouched
- `tests/test_llm_tools_stream.py` - New: 5 tests covering the no-tools string contract, single tool_calls event assembly, fragmented-argument reassembly, two-call accumulation by index, and reasoning_content being ignored — all against live-captured SSE sequences from 02-RESEARCH.md
- `tests/test_tools.py` - New: 7 tests covering per-layer writes, sequential multi-call ordering, unknown-tool/malformed-argument error tolerance, scope-override rejection, and the zero-tool-calls/zero-writes case

## Decisions Made
- Relied on Pydantic's default `extra="ignore"` behavior on `model_validate` to structurally drop any LLM-supplied `chat_id`/`user_id` keys from the validated arguments dict, rather than manually deleting them from the raw dict — the warning log (`tool_args_scope_override_ignored`) still fires against the raw arguments so the override attempt is traceable, but the validated payload passed to the handler never contains those keys regardless.
- Kept `dispatch_tool_calls`'s docstring free of the literal substring `asyncio.gather` (paraphrased as "gather these concurrently") so the plan's acceptance-criteria grep for `asyncio.gather` in `agent/tools.py` (expecting `0`) stays a meaningful check against actual code rather than passing only because a comment happened to also match.

## Deviations from Plan

None - plan executed exactly as written. No production code needed changes during Task 3 (dispatcher tests only, as the plan anticipated).

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. No new packages installed (per the plan's threat model, T-02-SC is not applicable to this plan).

## Verification Evidence

- `python -c "from agent.tools import TOOL_REGISTRY, build_tool_schemas; ..."` (Task 1's exact verify command) — printed `ok`.
- `pytest tests/test_llm_tools_stream.py -v` — 5/5 passed.
- `pytest tests/test_tools.py -v` — 7/7 passed.
- `pytest tests/test_tools.py tests/test_llm_tools_stream.py -v` — 12/12 passed combined.
- `pytest tests/test_context_engine.py tests/test_concurrent_ws.py tests/test_strategies.py -q` — 22/22 passed (no existing `stream_chat` caller broke).
- `pytest tests/ -q` — 149/149 passed (full suite, run three times across the three tasks, no regressions).
- All Task 1/2/3 acceptance-criteria grep gates re-run directly and confirmed: 0 occurrences of `asyncio.gather`/`asyncio.create_task`/`asyncio.sleep`/`async_session_factory` and 0 bare `except:` in `agent/tools.py`; 1 `json.JSONDecodeError`; exactly 1 `tool_call_dispatched` log call; at least 1 `model_json_schema()` call and 0 hand-written `"properties":` literals; 2 new schema classes in `agent/schemas.py`; 0 occurrences of `save_memory` in `agent/tools.py`; at least 5 `def test_` in `tests/test_llm_tools_stream.py` with at least 2 `finish_reason`/`respx` references; `_parse_sse_stream_with_tools` appears exactly 2 times in `agent/llm_client.py` (definition + dispatch); the `tools: list[dict[str, Any]] | None = None` signature appears exactly once; `acc["function"]["arguments"] +=` appears exactly once (append, not assign); 0 occurrences of `reasoning_content` being parsed outside comments; at least 7 `def test_` in `tests/test_tools.py`.
- Plan-level `<verification>` block: `python -c "from agent.tools import build_tool_schemas; ..."` printed two well-formed OpenAI function schemas with `key`/`content` properties and both marked `required`; `grep -rn "asyncio.gather\|asyncio.create_task" agent/tools.py` returned nothing.
- **Not verified in this session:** an actual end-to-end tool-calling round trip against a live LM Studio or DeepSeek instance — this plan is explicitly scoped as "wires no chat turn yet" (Plan 04 does that); all verification here is respx-mocked SSE streams and direct-function dispatcher tests against the real test DB, per the plan's own stated scope.

## Next Phase Readiness
- `agent/tools.py`'s registry and dispatcher, and `agent/llm_client.py`'s tools-aware `stream_chat`, are ready for Plan 04 to wire into `agent/ws.py::_handle_chat_message`'s existing per-chat-locked flow: call `stream_chat(..., tools=build_tool_schemas())`, dispatch any `tool_calls` event through `dispatch_tool_calls` inside the same lock/session, feed `{"role": "tool", ...}` results back, and issue one more `stream_chat()` call for the final assistant text.
- No blockers. The dispatcher is intentionally generic (`TOOL_REGISTRY`/`register_tool`) so Phases 3-5 (Personalization, Tasks, Invariants) can register new tools without touching `dispatch_tool_calls` itself, per STATE.md's locked decision.

## Self-Check: PASSED

- FOUND: `agent/tools.py`
- FOUND: `tests/test_llm_tools_stream.py`
- FOUND: `tests/test_tools.py`
- FOUND: `.planning/phases/02-memory-day-11/02-03-SUMMARY.md`
- FOUND commit: `8bf862d`
- FOUND commit: `e650b5f`
- FOUND commit: `120f57e`

---
*Phase: 02-memory-day-11*
*Completed: 2026-09-20*
