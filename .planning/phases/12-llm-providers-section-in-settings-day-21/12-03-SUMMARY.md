---
phase: 12-llm-providers-section-in-settings-day-21
plan: 03
subsystem: chat-provider-routing
tags: [llm-providers, websocket, routing, titles, facts, invariants]
requires: ["12-01"]
provides:
  - "Per-turn provider resolution in the WS chat turn with PROVIDER_UNAVAILABLE frame"
  - "Provider-routed auto-title job, debounced facts extraction and invariant self-critique"
affects: [12-04, 12-05]
tech-stack:
  added: []
  patterns: ["resolve client once per turn before persisting the user message", "background jobs re-resolve the client inside the task"]
key-files:
  created:
    - tests/test_llm_providers_routing.py
  modified:
    - agent/ws.py
    - agent/titles.py
    - agent/invariants.py
    - agent/context_engine.py
    - tests/test_titles.py
    - tests/test_titles_ws.py
key-decisions:
  - "_ToolTurn.client defaults to None and __post_init__ fills a keyless LM Studio client so agent/headless.py keeps working until 12-04 passes client= explicitly"
  - "Title job and facts extraction receive provider_id (not a client) and resolve it in the background task; an unavailable provider degrades to the fallback title / skipped extraction"
requirements-completed: [PROV-05, PROV-06, PROV-07]
duration: ~45 min
completed: 2026-10-02
---

# Phase 12 Plan 03: Chat-path provider routing Summary

Every chat-path LLM call (main stream, action-claim retry, tool follow-ups, rejected-transition re-prompt, justify/retract, invariant self-critique, debounced facts, auto-title) now uses the client of the provider selected for the turn; a deleted, disabled or foreign provider fails the turn with `PROVIDER_UNAVAILABLE` before any message is persisted.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Provider-routed title job + title test migration | bd32dc4 | `pytest tests/test_titles.py tests/test_titles_ws.py`: 94 passed, 1 failed at that commit (`test_first_turn_of_default_titled_chat_schedules_title` expects the provider_id argument that the ws.py change in Task 2 supplies); passes after Task 2 |
| 2 Per-turn resolution in ws.py, critique/facts routing, routing tests | 038d0c7 | `tests/test_llm_providers_routing.py`: 11 passed |

Full suite after Task 2: `pytest tests/ -q` gave 1136 passed, 0 failed (221 s). `python -c "import agent.main"` exits 0.

## Deviations from Plan

**1. [Rule 3 - Blocking] Task 1 commit leaves one WS title test red until Task 2**
- The updated expectation `(chat_id, user_id, text, answer, MODEL, None)` depends on ws.py passing provider_id, which belongs to Task 2 in the plan. Resolved by Task 2; not a code change.

**2. Test design adjustments in tests/test_llm_providers_routing.py**
- The "deleted provider" test seeds the built-in providers and sets the DeepSeek env var first, and uses a custom-named provider; otherwise lazy seeding at resolve time reused the deleted SQLite id (no AUTOINCREMENT) and the id resolved to a seeded provider.
- The 401 test checks message removal while the socket is still open (the handler deletes the user message after sending the error frame; closing the socket first cancels it). Same approach as existing tool-round tests.
- The title scheduler and facts extraction are no-op'd by an autouse fixture in that file, except in the recorder test.

**3. Acceptance grep wording**
- `grep -n "llm_client" agent/ws.py ...` still matches the module path `agent.llm_client` in import lines (`LLMClient`, `count_tokens`, `ChatCompletionResult`). No use of the `llm_client` singleton remains in ws.py, invariants.py, titles.py or context_engine.py.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-12-14 (foreign id gives unnamed PROVIDER_UNAVAILABLE), T-12-15 (401/403 text names provider and env variable only, test asserts key absent, including on the tool follow-up path), T-12-16 (explicit ids never fall back) and T-12-17 (resolution before `_persist_user_message`, test asserts empty tree) are implemented and tested.

## Not verified

- No live DeepSeek or LM Studio calls; all network behaviour is respx-mocked.
- No browser check (frontend arrives in a later plan).
- agent/headless.py still uses the transitional LM Studio default in `_ToolTurn`; routing there is 12-04's job.

## Self-Check: PASSED

tests/test_llm_providers_routing.py exists; commits bd32dc4 and 038d0c7 exist.
