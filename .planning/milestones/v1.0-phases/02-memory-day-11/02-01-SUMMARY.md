---
phase: 02-memory-day-11
plan: 01
subsystem: database
tags: [sqlmodel, fastapi, sqlite, memory, vanilla-js]

# Dependency graph
requires:
  - phase: 01-auth-foundation
    provides: User/Session tables, get_current_user dependency, session-cookie auth, _get_chat_or_404 IDOR-safe ownership check
provides:
  - WorkingMemory and LongTermMemory SQLModel tables (dedicated, not on the message tree)
  - agent/memory.py CRUD module (list/save for both layers, session-injected, upsert-on-key semantics)
  - GET /api/v1/chats/{chat_id}/memory endpoint returning all three memory layers
  - Always-visible sidebar memory panel (#memory-panel) in the chat UI
affects: [02-memory-day-11 plan 03/04 (tool-call dispatcher writes into these same tables), 03-personalization (long-term memory as profile substrate)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Key-scoped upsert with UniqueConstraint + IntegrityError-race retry-as-update (agent/memory.py)"
    - "Ownership check (_get_chat_or_404) before any chat-scoped read, IDOR-safe 404-never-403 (GET /memory)"
    - "Always-visible non-modal sidebar panel refreshed on chat-select and WS 'done' (mirrors #stats-panel)"

key-files:
  created:
    - agent/memory.py
    - tests/test_memory.py
    - tests/test_memory_api.py
  modified:
    - shared/models.py
    - agent/schemas.py
    - agent/main.py
    - ui/static/index.html
    - ui/static/app.js
    - tests/test_database.py

key-decisions:
  - "LongTermMemory has no chat_id column at all (not just an unfiltered query) - enforces D-02 at the schema level"
  - "IntegrityError on the insert path is caught and retried as an update rather than propagated, covering the UniqueConstraint race window"
  - "DOM clearing in renderMemoryEntries uses container.replaceChildren() instead of innerHTML = '' to keep the file's innerHTML assignment count from increasing (plan's explicit grep gate for the no-new-innerHTML XSS mitigation)"

patterns-established:
  - "Memory CRUD lives in agent/memory.py only - no HTTP/WS logic, always takes the caller's session, never opens its own"

requirements-completed: [MEM-01, MEM-02, MEM-04, MEM-05]

# Metrics
duration: 33min
completed: 2026-09-20
---

# Phase 02 Plan 01: Memory Substrate and Inspection Panel Summary

**Dedicated WorkingMemory/LongTermMemory SQLite tables with upsert-on-key CRUD, an ownership-checked `GET /api/v1/chats/{chat_id}/memory` endpoint, and an always-visible sidebar panel rendering all three memory layers via `textContent`.**

## Performance

- **Duration:** 33 min (commits span 09:31:39 to 09:35:09 local; reading/context-gathering preceded that)
- **Started:** 2026-09-20T09:31:39+03:00 (first task commit)
- **Completed:** 2026-09-20T09:35:09+03:00 (last task commit)
- **Tasks:** 3/3 completed
- **Files modified:** 9 (3 created, 6 modified, including one pre-existing test fixed as a direct consequence)

## Accomplishments
- `WorkingMemory` (chat-scoped) and `LongTermMemory` (user-scoped, no `chat_id` column at all) tables created automatically by `init_db()`, with FK-CASCADE and `UniqueConstraint`-backed overwrite semantics
- `agent/memory.py`: four session-injected CRUD coroutines (`list_working_memory`, `list_long_term_memory`, `save_working_memory`, `save_long_term_memory`) with get-or-create upsert and `IntegrityError` race retry
- `GET /api/v1/chats/{chat_id}/memory`: ownership-checked (404, never 403), returns this chat's working memory, the caller's full cross-chat long-term memory (D-02, verified never filtered by `chat_id`), and `short_term_message_count` from the active branch
- Sidebar `#memory-panel`: permanently visible (not a modal), three labelled layers with live counts, refreshed on chat selection and after every completed WS turn, all LLM-sourced strings inserted via `textContent`

## Task Commits

Each task was committed atomically:

1. **Task 1: Memory tables and the agent/memory.py CRUD module** - `00d189d` (feat)
2. **Task 2: Memory response schemas and the GET /api/v1/chats/{chat_id}/memory endpoint** - `c62203e` (feat)
3. **Task 3: Always-visible sidebar memory panel** - `42dd32d` (feat)

**Plan metadata:** committed alongside this SUMMARY (see final commit in this worktree)

## Files Created/Modified
- `shared/models.py` - Added `WorkingMemory` and `LongTermMemory` SQLModel tables; added `UniqueConstraint` to the existing sqlalchemy import
- `agent/memory.py` - New thin CRUD module: `list_working_memory`, `list_long_term_memory`, `save_working_memory`, `save_long_term_memory`
- `agent/schemas.py` - Added `MEMORY_KEY_MAX_LENGTH`/`MEMORY_VALUE_MAX_LENGTH` constants, `MemoryEntryResponse`, `ChatMemoryResponse`
- `agent/main.py` - Added `from agent import memory` import and `GET /api/v1/chats/{chat_id}/memory` handler, registered immediately after `get_chat_stats`
- `ui/static/index.html` - Added `#memory-panel` inside the sidebar `<aside>`, between `#chat-list` and `#agent-status`
- `ui/static/app.js` - Added `state.lastMemory`, `loadChatMemory`, `renderMemoryPanel`, `renderMemoryEntries`; wired into `selectChat` and the WS `done` branch
- `tests/test_memory.py` - New: 6 tests covering overwrite-on-same-key, per-chat isolation, cross-chat long-term visibility, per-user isolation, cascade delete, empty-list case
- `tests/test_memory_api.py` - New: 6 tests covering 401, response shape, per-chat working-memory isolation, cross-chat long-term visibility, cross-user 404 (IDOR), short-term count accuracy
- `tests/test_database.py` - Updated `test_init_db_creates_all_tables`'s hardcoded table-name assertion to include `workingmemory`/`longtermmemory` (see Deviations)

## Decisions Made
- Enforced D-02 (long-term memory is user-scoped, cross-chat) at the schema level by giving `LongTermMemory` no `chat_id` column at all, not just an unfiltered query — makes the "someone 'fixes' it to be chat-filtered" regression structurally harder, on top of the regression test in `tests/test_memory_api.py`.
- Used `container.replaceChildren()` rather than `container.innerHTML = ''` in `renderMemoryEntries` to clear rendered rows, so the file's total `innerHTML` assignment count stays exactly at its pre-task baseline (11) — satisfies the plan's literal grep-based XSS-regression gate for Task 3 without weakening the clearing behavior.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated stale table-set assertion in `tests/test_database.py`**
- **Found during:** Task 1 (full-suite regression run after adding the two new tables)
- **Issue:** `test_init_db_creates_all_tables` asserted an exact, hardcoded set of table names (`{"chat", "message", "settings", "tokenusage", "user", "session"}`). Adding `WorkingMemory`/`LongTermMemory` — an intended, in-scope schema change for this plan — made that assertion fail, since `init_db()` now legitimately creates two more tables.
- **Fix:** Added `"workingmemory"` and `"longtermmemory"` to the expected set and updated the docstring.
- **Files modified:** `tests/test_database.py`
- **Verification:** `pytest tests/ -q` — 129 passed before the fix (1 failure), 135 passed after Task 2/3 additions.
- **Committed in:** `00d189d` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix, directly caused by this plan's own schema change)
**Impact on plan:** In-scope, necessary consequence of the new tables. No scope creep.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. No new packages installed (per the plan's threat model, T-02-SC is not applicable to this plan).

## Verification Evidence

- `pytest tests/test_memory.py -v` — 6/6 passed.
- `pytest tests/test_memory_api.py -v` — 6/6 passed.
- `pytest tests/ -q` — 135/135 passed (full suite, no regressions).
- `node --check ui/static/app.js` — exit 0 (syntax valid).
- All Task 1/2/3 acceptance-criteria grep gates from the plan re-run directly and confirmed passing (table classes, `UniqueConstraint` count, no `Field(foreign_key=/ondelete=)`, four CRUD coroutines, no `async_session_factory` inside `agent/memory.py`, no `datetime.utcnow()`, `_get_chat_or_404` call count, `list_long_term_memory(session, current_user.id)` present / `list_long_term_memory(session, chat_id` absent, two new schema classes, panel markup ids, `loadChatMemory`/`renderMemoryPanel` present, `innerHTML` count unchanged from baseline).
- Route registration confirmed programmatically: `/api/v1/chats/{chat_id}/memory` is present in `app.routes` (equivalent to the plan's `curl .../debug/routes` check — the agent process was not started standalone for this worktree run).
- End-to-end response-shape smoke test via `ASGITransport` (no server process): `GET /api/v1/chats/1/memory` on a fresh chat returned `{"chat_id": 1, "short_term_message_count": 0, "working": [], "long_term": []}`, matching the plan's `<verification>` example exactly.
- **Not verified in this session:** the plan's manual/browser step ("`python run.py`, log in, select a chat: the sidebar shows the Память panel...") — this requires a live browser session and was not run. The panel's markup, ids, and refresh wiring were verified statically and via the automated grep/syntax gates above instead.
- **Not verified in this session:** literal `git rev-parse --abbrev-ref HEAD` reporting `Day11` — this plan was executed inside a per-agent git worktree (`worktree-agent-adfba0a49bbcbfc97`) forked from the `Day11` branch's history; the branch-name check applies once the orchestrator merges this worktree's commits back onto `Day11`.

## Next Phase Readiness
- The storage substrate (`WorkingMemory`, `LongTermMemory`, `agent/memory.py`) is in place and tested for Plan 03/04's tool-call dispatcher (`agent/tools.py`) to write into via the same session/lock pattern already established in `agent/ws.py`.
- No blockers. The GET endpoint and UI panel are read-only for now — Plan 03 wires the LLM tool calls (`save_working_memory`, `save_long_term_memory`) that will populate these tables during a real chat turn.

## Self-Check: PASSED

- FOUND: `agent/memory.py`
- FOUND: `tests/test_memory.py`
- FOUND: `tests/test_memory_api.py`
- FOUND: `.planning/phases/02-memory-day-11/02-01-SUMMARY.md`
- FOUND commit: `00d189d`
- FOUND commit: `c62203e`
- FOUND commit: `42dd32d`

---
*Phase: 02-memory-day-11*
*Completed: 2026-09-20*
