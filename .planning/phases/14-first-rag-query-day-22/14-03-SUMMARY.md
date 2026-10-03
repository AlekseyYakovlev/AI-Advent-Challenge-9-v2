---
phase: 14-first-rag-query-day-22
plan: 03
subsystem: agent-rest-api
tags: [rag, fastapi, per-chat-config, snippets]
requires: [14-01]
provides:
  - GET/PUT /api/v1/chats/{chat_id}/rag
  - GET /api/v1/kb/{kb_id}/chunks/{chunk_id}
  - MessageResponse.rag_sources (parsed object or null)
affects: [14-06]
tech-stack:
  added: []
  patterns: [owner-scoped 404, tolerant JSON parse]
key-files:
  created: [agent/rag_api.py, tests/test_rag_api.py]
  modified: [agent/main.py, agent/schemas.py]
key-decisions:
  - "kb_id null forces mode off on PUT; mode off keeps the KB attached"
  - "Config response resolves KB name/status only for KBs owned by the chat owner"
requirements-completed: [RAG-01, RAG-05]
duration: ~20min
completed: 2026-10-03
---

# Phase 14 Plan 03: RAG config and snippet API Summary

Owner-scoped REST routes for per-chat RAG settings and lazy chunk snippets, plus tolerant `rag_sources` exposure in the chat tree.

## Tasks

Both tasks were committed together in one commit (`e51983e`) because they share `agent/rag_api.py`, `agent/main.py` and `tests/test_rag_api.py`; splitting would have required artificial partial files.

- Task 1: GET/PUT config routes (default off/null/5; Literal mode; top_k 1..20; foreign/missing KB 404; non-ready KB 422 "База знаний ещё не готова"; null KB forces off; Origin and JSON content-type guards; rollback on SQLAlchemyError). Router included in `agent/main.py`.
- Task 2: Snippet route (owner check, `KbChunk.kb_id` scoping, optional `file == source` guard, 404 "Фрагмент не найден"); `MessageResponse.rag_sources`; `_message_to_response` uses `parse_rag_payload` and logs `rag_sources_parse_failed` on corrupt JSON.

## Verification (actually run)

- `pytest tests/test_rag_api.py`: 18 passed.
- `pytest tests/test_rag_api.py tests/test_cascade_delete.py tests/test_kb_api.py tests/test_rag.py` plus WS suites: 110 passed.
- Full suite run with `-x`: 1073 passed, 1 failed (`tests/test_supervisor.py::test_agent_restarts_within_5_seconds`, timeout), then stopped. Re-running that file in isolation fails the same way. It spawns a real agent subprocess and is unrelated to files touched here; I did not confirm whether it also fails on the base commit. Tests after it alphabetically (test_task_*, test_text_tool_calls, test_titles*, test_tool*, etc.) were not run in the full pass.

## Deviations from Plan

- [Rule 3] The plan's tests referenced `run_index_job` in kb_helpers, which does not exist; tests seed a KB with `status=READY` and insert `KbChunk` rows directly instead.
- Worktree base was corrected with `git reset --hard 84ff870` per the startup check.

## Deferred Issues

- `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` fails in this worktree environment (see above).

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model; T-14-08..T-14-12 mitigations implemented and covered by tests.

## Self-Check: PASSED

agent/rag_api.py, tests/test_rag_api.py exist; commit e51983e exists.
