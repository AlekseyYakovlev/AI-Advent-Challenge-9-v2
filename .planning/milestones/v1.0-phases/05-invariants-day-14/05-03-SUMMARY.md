---
phase: 05-invariants-day-14
plan: 03
subsystem: api
tags: [self-critique, llm-as-judge, sqlmodel, fastapi, websocket, vanilla-js]

# Dependency graph
requires:
  - phase: 05-invariants-day-14 (plan 01)
    provides: GlobalInvariant table, agent/invariants.py CRUD module, sidebar Инварианты tab, setupFoldablePanels()
  - phase: 05-invariants-day-14 (plan 02)
    provides: ChatInvariant table, agent/invariants.py::resolve_active_invariants, prompt injection in build_system_prompt, per-chat invariant routes
provides:
  - InvariantConflict SQLModel table — chat_id/message_id CASCADE FKs, invariant_scope discriminator (no FK on invariant_id — it can point into either invariant table), invariant_title snapshot, note
  - agent/invariants.py self-critique machinery — build_critique_prompt, parse_critique_json, run_self_critique (fail-open), match_flagged_invariant, build_justify_retract_prompt, record_conflict, list_conflicts
  - agent/ws.py — after every turn with active invariants, a non-streaming complete_chat critique call judges the full response (prose + tool calls); a flagged conflict runs one more stream_chat round-trip appending a justify/retract reply, then persists the conflict against the real assistant message id
  - GET /api/v1/chats/{chat_id}/invariant-conflicts (ownership-checked, 404 not 403)
  - Inline amber conflict banner under the flagged chat message + amber count badge on the Инварианты tab header, both sourced from the persisted conflict log
affects: [05-04-invariants-acceptance-demo]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "LLM-as-judge second call (complete_chat, temperature=0.0, fail-open) mirroring context_engine._extract_facts's lenient-parse-with-safe-fallback shape, applied to a structured conflict verdict instead of fact extraction"
    - "Citation-validated flagging: a critique's cited scope+id must match an entry already present in the resolved active set, or the flag is dropped — this is what keeps a hallucinated invariant id out of the database and the UI"
    - "Test-fixture in-memory state reset (agent.state.chat_locks/active_streams/ws_rate_limiter cleared per test) alongside the existing DB reset, to prevent a stale asyncio.Lock tied to a torn-down TestClient event loop from deadlocking a later test that reuses the same low chat_id"

key-files:
  created:
    - tests/test_invariants_ws.py
    - tests/test_invariant_conflicts_api.py
  modified:
    - shared/models.py
    - agent/schemas.py
    - agent/invariants.py
    - agent/ws.py
    - agent/main.py
    - ui/static/index.html
    - ui/static/app.js
    - tests/test_cascade_delete.py
    - tests/test_database.py
    - tests/conftest.py

key-decisions:
  - "Justify/retract reply is appended to assistant_text (not a replacement) AND captured separately into InvariantConflict.note — matches the existing post-tool-result follow-up shape and keeps the streamed bubble identical to the persisted Message.content (OQ1)"
  - "Critique always runs when the chat has at least one active invariant, regardless of whether tools fired; skipped entirely when the active set is empty — zero extra LLM cost for chats with no invariants configured (OQ2)"
  - "Conflict badge counts every conflict ever recorded for the chat — no resolved/acknowledged column; dismissal workflows are out of this phase's scope (OQ3)"
  - "InvariantConflict.invariant_id is a plain, non-FK-constrained column with invariant_scope as the discriminator, since SQLite cannot express a conditional FK into two different tables (GlobalInvariant vs ChatInvariant)"

patterns-established:
  - "match_flagged_invariant(active, critique) as the single gate between an LLM's structured verdict and anything written to the database or rendered in the UI — never trust invariant_scope/invariant_id from the model without validating it against the already-resolved active set"

requirements-completed: [INV-04, INV-05]

# Metrics
duration: 50min
completed: 2026-09-20
---

# Phase 5 Plan 3: Self-Critique Conflict Check & Conflict Surfacing Summary

**A second, non-streaming LLM call judges every turn's full response (prose + tool calls) against the chat's resolved active invariants; a flagged conflict triggers a justify/retract round-trip, and the result is persisted as an InvariantConflict row surfaced both as an inline amber chat banner and an amber badge on the Инварианты tab**

## Performance

- **Duration:** 50 min
- **Started:** 2026-09-20T22:03:00Z
- **Completed:** 2026-09-20T22:53:00Z
- **Tasks:** 3
- **Files modified:** 10 (2 created, 8 modified — including 3 deviation fixes)

## Accomplishments
- Every WebSocket turn in a chat with at least one active invariant now runs a dedicated, non-streaming self-critique (`complete_chat`, temperature=0.0) that inspects the assistant's full response — prose AND tool calls — against the exact same override-resolved invariant set `build_system_prompt` injected, never a re-derived one (INV-04, D-07, D-08)
- A flagged conflict sends the model back for one more streamed round-trip asking it to justify or retract; that reply is appended to the same answer (streamed as more `token` frames) and captured separately as the conflict's audit note (D-09, OQ1)
- The flagged conflict is persisted as an `InvariantConflict` row against the real, already-committed assistant message id, and surfaces via `GET /api/v1/chats/{chat_id}/invariant-conflicts`, an amber banner directly under the flagged message, and an amber count on the Инварианты tab header — all three reads come from the same persisted record, so they can never disagree (D-12, D-13, INV-05)
- A citation that doesn't match any entry in the resolved active set (including a hallucinated invariant id) is treated as no conflict and never reaches the database or the UI (T-05-12)
- Four independent failure modes — critique HTTP failure, unparseable critique JSON, justify/retract stream failure, and a hallucinated citation — all leave the turn intact and still reaching `done`; a chat with zero active invariants pays for zero extra LLM calls (fail-open, OQ2)

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the failing WS and REST tests for the conflict-check loop** - `abe4c3b` (test)
2. **Task 2: Implement the self-critique call, the justify/retract round-trip and conflict persistence** - `046c33a` (feat)
3. **Task 3: Surface conflicts inline in the chat and as a badge on the Инварианты tab** - `c9297ff` (feat)

_No plan-metadata commit — orchestrator owns STATE.md/ROADMAP.md writes after the wave completes (parallel worktree execution)._

## Files Created/Modified
- `shared/models.py` - Added `InvariantConflict(SQLModel, table=True)`: chat_id/message_id CASCADE FKs, invariant_scope (discriminator, no FK on invariant_id), invariant_title snapshot, note, created_at
- `agent/schemas.py` - Added `INVARIANT_CONFLICT_NOTE_MAX_LENGTH` and `InvariantConflictResponse`
- `agent/invariants.py` - Added `build_critique_prompt`, `parse_critique_json`, `run_self_critique` (fail-open), `match_flagged_invariant`, `build_justify_retract_prompt`, `record_conflict`, `list_conflicts`
- `agent/ws.py` - `_handle_chat_message` gains: critique after the tool round-trip and before `_persist_assistant_message`; conditional justify/retract `stream_chat` call whose failure path logs a warning and falls through (never deletes the user message); `record_conflict` after `_persist_assistant_message`; `invariant_conflict` key added to the `done` frame
- `agent/main.py` - `_invariant_conflict_to_response` mapper and `GET /api/v1/chats/{chat_id}/invariant-conflicts` (behind `_get_chat_or_404`)
- `ui/static/index.html` - Added `title="Конфликты с инвариантами"` to the existing (05-01-shipped) `#invariant-conflict-badge`
- `ui/static/app.js` - `loadChatConflicts`, `renderConflictBadge`, `buildConflictBanner` (title via `textContent`, LLM-generated note through the existing `renderMarkdown`/DOMPurify pipeline); wired into `renderMessages`, `selectChat`, and the WS `'done'` handler; new `state.lastConflicts`
- `tests/test_invariants_ws.py` (new) - 8 end-to-end WS tests: skip-when-empty, no-conflict, flagged-conflict-with-justify-retract, critique-HTTP-failure, critique-unparseable-output, hallucinated-id-ignored, override-resolution-in-prompt, tool-calls-in-prompt-scope
- `tests/test_invariant_conflicts_api.py` (new) - 4 REST tests: persisted-rows, empty-list, cross-user 404 (IDOR), auth-required
- `tests/test_cascade_delete.py` - Added `test_delete_chat_cascades_invariant_conflicts`
- `tests/test_database.py` - Added `"invariantconflict"` to the expected table-name set (Rule 1 fix)
- `tests/conftest.py` - `clean_test_db` fixture now also clears `agent.state`'s `chat_locks`/`active_streams`/`ws_rate_limiter` dicts (Rule 3 fix, see Deviations)

## Decisions Made
- Followed RESEARCH.md/PATTERNS.md exactly: `complete_chat` (not `stream_chat`) for the critique, mirroring `_extract_facts`'s lenient-parse-with-fail-open shape; `InvariantConflict.invariant_id` left unconstrained with `invariant_scope` as the discriminator, since SQLite can't express a conditional FK into two different tables
- The critique prompt renders the active set through the identical D-06 override-labelling `build_system_prompt` uses (verbatim `(overridden for this chat — see below)` / `(overrides the above)` strings), each line prefixed with `[SCOPE id=N]` so the model can cite a specific entry — asserted character-for-character in `test_critique_prompt_uses_the_resolved_override_set`
- `match_flagged_invariant` is the single validation gate: a citation must match an entry already in the resolved `active` list, or the flag is dropped entirely — no re-derivation, no trusting the model's scope+id blindly

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_database.py::test_init_db_creates_all_tables` failed after adding `InvariantConflict`**
- **Found during:** Task 2 verification (`python -m pytest tests/ -q`)
- **Issue:** This pre-existing test asserts an exact, hardcoded set of table names created by `init_db()`. Adding `InvariantConflict` (table name `invariantconflict`) made the test's expected set stale — the exact same pitfall 05-01 (`globalinvariant`) and 05-02 (`chatinvariant`) already documented.
- **Fix:** Added `"invariantconflict"` to the expected table-name set.
- **Files modified:** `tests/test_database.py`
- **Verification:** `python -m pytest tests/test_database.py -q` passes
- **Committed in:** `046c33a` (Task 2 commit)

**2. [Rule 3 - Blocking] Full-suite run (`python -m pytest tests/ -q`) deterministically hung for ~5-6 minutes on one WS test**
- **Found during:** Task 2's mandated full-suite verification
- **Issue:** `agent/state.py`'s module-level `chat_locks`/`active_streams`/`ws_rate_limiter` dicts are never cleared between tests — only the DB is reset (`clean_test_db` fixture). Each `with TestClient(app) as client:` block spins its own `anyio` blocking-portal event loop (confirmed via `inspect.getsource(TestClient.__enter__)`); since the DB reset restarts SQLite's chat-id sequence at 1 for nearly every test, a later test whose chat also lands on id=1 would reuse a *stale* `asyncio.Lock` object left in `chat_locks` by an earlier test's now-closed event loop, permanently deadlocking `async with chat_locks[chat_id]:`. The WS client's blocking `receive_json()` then hung until the server-side 300s idle timeout tore down the connection.
- **Root-cause isolation:** Reproduced identically with only pre-existing, unmodified files (`tests/test_memory_ws.py` + `tests/test_task_ws.py` + `tests/test_concurrent_ws.py`, none touched by this plan) run in a non-alphabetical order — confirming the defect predates this plan and isn't caused by the new invariant code. It surfaces in the *natural* full-suite run too because this plan added 8 more WS-turn tests, increasing the odds of a chat-id collision with a dangling lock.
- **Fix:** `tests/conftest.py`'s `clean_test_db` fixture now also calls `.clear()` (not reassignment, to preserve the dict object other modules already imported a reference to) on `agent.state.chat_locks`/`active_streams`/`ws_rate_limiter` alongside the existing DB reset.
- **Files modified:** `tests/conftest.py`
- **Verification:** The exact reproduction (`test_memory_ws.py` + `test_task_ws.py` + `test_concurrent_ws.py`) went from an indefinite hang to `8 passed in 4.68s`; the full suite (`pytest tests/ -q`) went from `1 failed` (a 347s hang before failing) to `278 passed in ~47s`, confirmed stable across two consecutive full runs.
- **Committed in:** `046c33a` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 Rule 1 table-name fix, 1 Rule 3 blocking test-infrastructure fix). Both are test-only changes; no production code behavior changed.
**Impact on plan:** Necessary for the plan's own full-suite acceptance gate to pass reliably. The Rule 3 fix touches a pre-existing defect outside this plan's `files_modified` list, but left unfixed it would silently reintroduce ~5-minute-long flaky hangs into every future phase's `pytest tests/ -q` run, which is a materially worse outcome than a small, well-scoped, test-only fix.

## Issues Encountered

Diagnosing the intermittent full-suite hang consumed the majority of this plan's wall-clock time: three background `pytest` invocations were started and had to be force-killed via `taskkill` after confirming (via `psutil.Process.cpu_times()`/`net_connections()`) they were alive but not progressing, because `tests/test_app.db*` file locks from the killed processes then blocked subsequent runs until removed. Root cause was isolated by bisecting with file combinations that included zero of this plan's own files, which conclusively ruled out the new invariant code as the cause before applying the `conftest.py` fix.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `agent/invariants.py`'s full critique/justify-retract/persistence surface (`run_self_critique`, `match_flagged_invariant`, `record_conflict`, `list_conflicts`) and the `GET .../invariant-conflicts` endpoint are ready for 05-04's acceptance demo (manual smoke: a "Без Docker" global rule + a Docker-suggesting prompt should end in a retraction with a visible amber banner, both surviving a reload)
- `agent/tools.py` remains byte-identical — confirmed via `git diff --stat HEAD~2 HEAD -- agent/tools.py` showing no output
- The `tests/conftest.py` in-memory-state-reset fix benefits every future phase's WS test suite, not just this one — no further action needed, it's already the new baseline behavior
- No blockers. Manual smoke test (`python run.py`, verify the amber banner + badge, confirm persistence across reload) is deferred to 05-04 per the plan's own verification note

---
*Phase: 05-invariants-day-14*
*Completed: 2026-09-20*

## Self-Check: PASSED

- FOUND: tests/test_invariants_ws.py
- FOUND: tests/test_invariant_conflicts_api.py
- FOUND: agent/invariants.py
- FOUND: agent/ws.py
- FOUND commit: abe4c3b (test)
- FOUND commit: 046c33a (feat)
- FOUND commit: c9297ff (feat)
