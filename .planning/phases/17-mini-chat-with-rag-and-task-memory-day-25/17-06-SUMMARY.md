---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 06
subsystem: rag
tags: [rag, task-memory, websocket, system-prompt]
requires:
  - phase: 17-01
    provides: update_task_memory, render_prompt_lines, chat_has_rag, load_doc
  - phase: 17-03
    provides: prepare_rag_turn(parent_id), RagTurn.with_task_memory
provides:
  - in-turn task-memory update on normal and gated RAG turns (agent/ws.py)
  - done.rag.task_memory snapshot and per-message stored snapshot, payload version 4 (agent/rag.py)
  - labelled task-memory lines in the system prompt of RAG chats (agent/context_engine.py)
affects: [17-07, 17-08]
key-files:
  created: [tests/test_task_memory_ws.py, tests/test_task_memory_prompt.py]
  modified: [agent/ws.py, agent/rag.py, agent/context_engine.py, tests/test_rag.py, tests/test_rag_turn.py, tests/test_rag_ws.py, scripts/e2e_rag_cite_playwright.py]
requirements-completed: [RCHAT-01, RCHAT-02, RCHAT-03]
completed: 2026-10-10
---

# Phase 17 Plan 06: Task memory in the live turn Summary

Every RAG turn now updates task memory synchronously before `done`, commits it together with the assistant message, ships the snapshot in `done.rag.task_memory`, stores it per message, and renders it as labelled lines into the answering model's system prompt; follow-ups pass their parent message to the history-aware retrieval pre-step.

## Tasks

1. In-turn update on normal and gated turns, snapshot in payload (version 4), history input - commit b344b28
2. Labelled task-memory lines in the system prompt - commit 98ddb21

## Behaviour

- `agent/ws.py`: `update_task_memory` runs after `finalize_rag_turn` and before `_persist_assistant_message` (RAG mode only, nothing awaited in between), so the staged row and the message share one commit. On the gated path it runs inside the existing `try` with an empty assistant text; the existing rollback discards the staged row. `_complete_gated_turn` gained a `client` parameter. `prepare_rag_turn` receives `parent_id=user_msg.parent_id`.
- `agent/rag.py`: `PAYLOAD_VERSION = 4`; `task_memory` is an additive key added only by `RagTurn.with_task_memory`.
- `agent/context_engine.py`: when `TASK_MEMORY_ENABLED` and the chat is in RAG mode, `render_prompt_lines` output is appended after the Working memory block (before long-term memory). A `SQLAlchemyError` is logged (`task_memory_prompt_failed`), the session rolled back, and the prompt built without the lines.

## Verification (run)

- Task 1 verify command (py_compile of the e2e script plus test_task_memory_ws, test_rag_ws, test_rag, test_rag_turn, test_concurrent_ws, test_tool_rounds_ws, test_invariants_ws, test_memory_ws): 121 passed. tests/test_task_memory_ws.py has 12 tests (including 3 parametrised failure modes: garbage, HTTP 500, timeout).
- Task 2 verify command (test_task_memory_prompt, test_context_engine, test_context_engine_memory, test_context_engine_tasks, test_stats, test_strategies): 54 passed. tests/test_task_memory_prompt.py has 7 tests.
- `python -m pytest tests/ -q -k "ws or rag or memory"`: 870 passed. (A first run overlapped with another pytest process and produced PermissionError setup errors on the shared test DB file; the sequential rerun was clean.)
- `grep` checks: no `== 3` payload-version literals left in tests/ or scripts/; `update_task_memory(` appears twice in agent/ws.py; no `create_task` in agent/task_memory.py.
- Not run: scripts/e2e_rag_cite_playwright.py (needs a live isolated app and LM Studio); only `py_compile` was run on it.

## Pre-existing tests edited

- tests/test_rag.py: `payload["v"] == 3` to `== 4` (payload version bump).
- tests/test_rag_turn.py (`test_payload_v3_ok_with_trace_and_no_chunk_text`): same version literal.
- tests/test_rag_ws.py (`test_done_frame_carries_search_trace_v2`): same version literal.
- tests/test_rag_ws.py (`test_rewrite_runs_through_answer_client_before_stream`): the last recorded request is now the extraction call, so the assertion `captured[-1].get("stream") is not False` was narrowed to "the last streamed request comes after the stage request"; the claim (rewrite runs before the stream) is kept.
- scripts/e2e_rag_cite_playwright.py (S4): `v == 3` to `int and v >= 3`, label `v3` to `v>=3`.

## Deviations from Plan

- [Rule 3 - Blocker] Worktree base was stale (d35a11c); reset to the required 71b1e83 per the branch check.
- [Rule 1 - Bug] In `build_system_prompt`, the rollback in the new failure handler expired the `chat` instance, which broke later `chat.user_id` reads (MissingGreenlet). The owner id is now read once up front (`owner_id`) and used for the profile and long-term memory blocks; behaviour for normal prompts is unchanged.
- The plan's behaviour bullet "a turn that ends with an LLM error frame (the user message is deleted)" did not hold for an HTTP 500 on the first request: the user message remains. The test asserts only what the plan's purpose requires (no extraction request, no memory row); the existing message handling was left as is.
- `.planning/HANDOFF.json` is modified in the working tree by tooling and was not committed.

## Known Stubs

None.

## Threat Flags

None. T-17-28 (single commit, gated rollback test), T-17-29 (fail-soft with timeout from 17-01), T-17-30 (extraction request test asserts no fragment block or citation tail) are covered by tests.

## Self-Check: PASSED

Created files exist; commits b344b28 and 98ddb21 are in git log.
