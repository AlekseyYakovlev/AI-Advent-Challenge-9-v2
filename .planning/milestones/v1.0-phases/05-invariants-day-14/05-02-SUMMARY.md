---
phase: 05-invariants-day-14
plan: 02
subsystem: api
tags: [sqlmodel, fastapi, invariants, override-resolution, sidebar-ui, vanilla-js]

# Dependency graph
requires:
  - phase: 05-invariants-day-14 (plan 01)
    provides: GlobalInvariant table, agent/invariants.py CRUD module, GET/POST/PUT/DELETE /api/v1/invariants routes, #invariants-panel sidebar tab, setupFoldablePanels()
provides:
  - ChatInvariant SQLModel table with user_id/chat_id CASCADE FKs and a nullable overrides_id FK (SET NULL) into GlobalInvariant (D-05)
  - agent/invariants.py::resolve_active_invariants — the single override-resolved source of truth for active invariants
  - Four /api/v1/chats/{chat_id}/invariants REST routes, ownership-checked via _get_chat_or_404 (404 never 403)
  - build_system_prompt injects the D-06 verbatim [GLOBAL]/[CHAT] override-labelled invariants block
  - Per-chat invariants sidebar section with an overrides <select> populated from global titles
affects: [05-03-invariant-injection-and-conflict-check, 05-04-invariants-acceptance-demo]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-table nullable FK (ChatInvariant.overrides_id -> GlobalInvariant.id, ondelete=SET NULL) as the structural override link (D-05), resolved by a single read-only resolver function rather than re-queried ad hoc at each consumer"
    - "Single-source-of-truth resolver (resolve_active_invariants) consumed by build_system_prompt now and reserved for 05-03's self-critique prompt, preventing the two from drifting"

key-files:
  created:
    - tests/test_invariants_chat.py
    - tests/test_invariants_chat_api.py
    - tests/test_context_engine_invariants.py
  modified:
    - shared/models.py
    - agent/schemas.py
    - agent/invariants.py
    - agent/main.py
    - agent/context_engine.py
    - tests/test_database.py
    - tests/test_cascade_delete.py
    - ui/static/index.html
    - ui/static/app.js

key-decisions:
  - "resolve_active_invariants pairs the FIRST chat rule pointing at a given global into that global's entry; a second override of the same global degrades to a standalone chat entry rather than being dropped or silently replacing the first"
  - "Override labelling strings in build_system_prompt use the literal em dash (—) from D-06, asserted character-for-character in tests/test_context_engine_invariants.py, not built via any shared helper the implementation also uses"
  - "Per-chat invariant routes require _get_chat_or_404 first (ownership-checked), unlike the deliberately unscoped global routes from 05-01 — the one asymmetry called out in D-02 vs. everything else"

patterns-established:
  - "_get_chat_invariant_or_404(session, chat_id, invariant_id), modelled on _get_task_or_404, rejects a row whose chat_id doesn't match the path chat with 404 — reusable idiom for any future chat-owned child resource"

requirements-completed: [INV-02, INV-03]

# Metrics
duration: 25min
completed: 2026-09-21
---

# Phase 5 Plan 2: Per-Chat Invariant Overrides & Prompt Injection Summary

**ChatInvariant table with a structural overrides_id FK into GlobalInvariant, a single resolve_active_invariants() resolver consumed by build_system_prompt, and a sidebar overrides dropdown that lets a chat visibly override a global rule**

## Performance

- **Duration:** 25 min
- **Started:** 2026-09-21T14:40:00Z
- **Completed:** 2026-09-21T15:05:00Z
- **Tasks:** 3
- **Files modified:** 12 (3 created, 9 modified)

## Accomplishments
- A user can add a rule scoped to only the current chat and, via a dropdown of global titles, pick exactly one global invariant it overrides — the link is a real `overrides_id` FK set at creation time, not keyword matching (INV-02, D-05)
- `agent/invariants.py::resolve_active_invariants` is the single function that reads both the global and per-chat tables and produces the resolved, labelled active set — consumed by `build_system_prompt` now, reserved for 05-03's self-critique prompt so the two can never disagree about what's active
- Every chat turn's system prompt now carries the chat's active invariants; an overridden pair is injected as both rules, labelled verbatim per D-06: `[GLOBAL] <rule> (overridden for this chat — see below)` immediately followed by `[CHAT] <rule> (overrides the above)` (INV-03)
- All four per-chat invariant routes sit behind `_get_chat_or_404` and 404 (never 403) both for another user's chat and for an invariant id belonging to a different chat — verified by dedicated IDOR tests
- Deleting a chat cascades its `ChatInvariant` rows; deleting the overridden global leaves the overriding chat rule alive with `overrides_id` reset to `NULL` (SQLite `ON DELETE SET NULL`, verified end-to-end)
- The sidebar's "Этого чата" section lists per-chat rules with add/edit/delete and shows `переопределяет: <global title>` for any rule with an active override

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the failing tests for per-chat invariants, override resolution and prompt injection** - `74b2d86` (test)
2. **Task 2: Implement ChatInvariant storage, resolve_active_invariants, the per-chat routes and prompt injection** - `336aaef` (feat)
3. **Task 3: Add the per-chat invariants section with the overrides dropdown** - `291105b` (feat)

_No plan-metadata commit — orchestrator owns STATE.md/ROADMAP.md writes after the wave completes (parallel worktree execution)._

## Files Created/Modified
- `shared/models.py` - Added `ChatInvariant(SQLModel, table=True)`: user_id/chat_id CASCADE FKs, title, rule_text, nullable `overrides_id` FK into `globalinvariant.id` with `ondelete="SET NULL"`, created_at/updated_at
- `agent/schemas.py` - Added `ChatInvariantCreate`/`ChatInvariantUpdate`/`ChatInvariantResponse` (the latter carries `overrides_id` + resolved `overrides_title`)
- `agent/invariants.py` - Extended with `list_chat_invariants`, `get_chat_invariant`, `create_chat_invariant`, `update_chat_invariant`, `delete_chat_invariant`, and `resolve_active_invariants` (the single override-resolution algorithm: first override of a global claims it, subsequent overrides of the same global degrade to standalone)
- `agent/main.py` - `_get_chat_invariant_or_404` (chat-ownership idiom mirroring `_get_task_or_404`), `_chat_invariant_to_response` mapper, and four `/api/v1/chats/{chat_id}/invariants` routes (GET/POST/PUT/DELETE), all behind `_get_chat_or_404`; POST/PUT validate a supplied `overrides_id` against `invariants.get_global` and 404 on an unknown one
- `agent/context_engine.py` - `build_system_prompt` now calls `invariants.resolve_active_invariants` after the open-tasks block and appends the `Active invariants (always follow these; flag if you cannot):` block with D-06's verbatim override labelling
- `tests/test_invariants_chat.py` (new) - 8 CRUD/resolver unit tests covering override pairing, second-override degradation, cross-chat isolation, and the SET NULL cascade
- `tests/test_invariants_chat_api.py` (new) - 6 REST tests covering 201/overrides_title, unknown overrides_id 404, cross-user 404 (IDOR), cross-chat 404, delete, and auth gate
- `tests/test_context_engine_invariants.py` (new) - 5 prompt-assembly tests covering omission-when-empty, bare `[GLOBAL]`/`[CHAT]` lines, the exact D-06 override pair on consecutive lines, and coexistence with the profile/open-tasks blocks
- `tests/test_cascade_delete.py` - Added `ChatInvariant` import and `test_delete_chat_cascades_chat_invariants`
- `tests/test_database.py` - Added `chatinvariant` to the expected table-name set (Rule 1 fix, same pitfall 05-01 hit with `globalinvariant`)
- `ui/static/index.html` - Added the "Этого чата" section inside `#invariants-panel-body`: `#invariant-chat-list`/`#invariant-chat-count`, `#invariant-chat-title`/`#invariant-chat-rule` inputs, `#invariant-chat-overrides` `<select>`, `#btn-save-chat-invariant`
- `ui/static/app.js` - `loadChatInvariants`, `populateOverridesSelect` (rebuilds the overrides dropdown from `state.lastGlobalInvariants`, preserving the selection when still valid), `saveChatInvariant`, `deleteChatInvariant`; extended `renderInvariantsPanel` with the per-chat list/empty-state rendering; wired into `selectChat`, `handleWsMessage`'s `'done'` case, and `bindEvents`

## Decisions Made
- Followed RESEARCH.md/PATTERNS.md exactly: cross-table FK (`ChatInvariant.overrides_id -> GlobalInvariant.id`), not a self-referential FK in a single shared table
- `resolve_active_invariants` walks `chat_rows` in creation order so the *first* override of a given global wins the pairing; a later override of the same global is emitted as its own standalone `{"scope": "chat", "overridden_by": None}` entry instead of being silently dropped — matches the plan's explicit algorithm and is covered by its own test
- Kept the D-06 override-label strings as literal f-strings in both the implementation and, separately, as hardcoded literals in the test assertions (never sharing a formatting helper) so the test can't pass by construction

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_database.py::test_init_db_creates_all_tables` failed after adding the new table**
- **Found during:** Task 2 verification (`python -m pytest tests/ -q`)
- **Issue:** This pre-existing test asserts an exact, hardcoded set of table names created by `init_db()`. Adding `ChatInvariant` (table name `chatinvariant`) made the test's expected set stale, failing with `AssertionError: Extra items in the left set: 'chatinvariant'` — the exact same pitfall 05-01 hit for `globalinvariant`.
- **Fix:** Added `"chatinvariant"` to the expected table-name set in the test.
- **Files modified:** `tests/test_database.py`
- **Verification:** `python -m pytest tests/ -q` — 265 passed (was 264 passed / 1 failed before the fix)
- **Committed in:** `336aaef` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1, mechanical consequence of adding the `ChatInvariant` table in Task 2)
**Impact on plan:** Necessary for the plan's own full-suite acceptance gate to pass; no scope creep, no behavior beyond what the plan specified.

## Issues Encountered

**Worktree base mismatch (pre-existing environment issue, not a plan deviation):** The worktree was initially checked out at `b0639e3` (the post-Day13-merge point on `main`), which predates the Day14 phase-planning commits and, critically, plan 05-01's implementation commits (`GlobalInvariant` table, `agent/invariants.py`, the shared REST routes and sidebar panel this plan depends on). Verified via `git log --oneline b0639e3 -3` that this was exactly the same stale-base issue 05-01's own SUMMARY documented, and that the Day14 branch tip (`4f079f7`) was a strict fast-forward ahead of the worktree's current HEAD (`git merge-base --is-ancestor b0639e3 4f079f7` returned true). Since the worktree branch had zero unique commits yet, resolved with `git merge --ff-only 4f079f7` (a non-destructive fast-forward, not a rebase or reset) before starting Task 1. Re-verified 05-01's shipped files (`agent/invariants.py`, the `GlobalInvariant` model, the sidebar panel markup) were present afterward.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `resolve_active_invariants(session, chat_id)` is ready for 05-03's self-critique prompt builder to reuse directly — do not re-query `GlobalInvariant`/`ChatInvariant` independently there (05-RESEARCH.md Pitfall 3)
- `agent/ws.py` still has zero references to `resolve_active_invariants` — confirmed via `grep -c "resolve_active_invariants" agent/ws.py` returning 0, so 05-03 is free to add the only other consumer without a prior conflicting read path
- `#invariant-conflict-badge` (shipped empty/hidden by 05-01) is still untouched, ready for 05-03 to populate with a live conflict count
- No blockers. Manual smoke test (add a global rule, add a chat rule overriding it, confirm the agent's next answer reflects the chat rule) is deferred to 05-04 per the plan's own verification note — this plan's automated coverage confirms the prompt text is correct, not live LLM behavior

---
*Phase: 05-invariants-day-14*
*Completed: 2026-09-21*

## Self-Check: PASSED

- FOUND: shared/models.py (ChatInvariant)
- FOUND: agent/invariants.py (resolve_active_invariants)
- FOUND: tests/test_invariants_chat.py
- FOUND: tests/test_invariants_chat_api.py
- FOUND: tests/test_context_engine_invariants.py
- FOUND commit: 74b2d86 (test)
- FOUND commit: 336aaef (feat)
- FOUND commit: 291105b (feat)
