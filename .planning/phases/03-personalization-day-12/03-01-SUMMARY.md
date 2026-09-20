---
phase: 03-personalization-day-12
plan: 01
subsystem: api
tags: [sqlmodel, fastapi, personalization, profile, context-injection]

# Dependency graph
requires:
  - phase: 01-auth-foundation
    provides: "get_current_user session-cookie auth dependency, User table with user_id scoping convention"
  - phase: 02-memory-day-11
    provides: "agent/memory.py CRUD-module precedent, Settings/LongTermMemory FK-cascade shape, build_system_prompt's existing injection-block pattern"
provides:
  - "Profile SQLModel table (id, user_id unique+CASCADE, style, format, constraints, updated_at)"
  - "agent/profile.py: get_profile (read-only), get_or_create_profile, update_profile"
  - "GET/PUT /api/v1/profile REST endpoints, owner-scoped via current_user.id only"
  - "build_system_prompt unconditionally injects non-empty profile fields as a leading directive"
affects: [03-02-personalization-ui, phase-04-tasks, phase-05-invariants]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy get-or-create singleton row per user (Profile mirrors _ensure_global_settings)"
    - "Read-only CRUD split: get_profile never writes, used exclusively on the context-build path; get_or_create_profile/update_profile are the only write paths, used exclusively by the REST layer"

key-files:
  created: [agent/profile.py, tests/test_profile.py, tests/test_profile_api.py, tests/test_context_engine_profile.py]
  modified: [shared/models.py, agent/schemas.py, agent/main.py, agent/context_engine.py, tests/test_database.py]

key-decisions:
  - "Profile injection is placed immediately after the base system prompt and before facts/summary/working-memory/long-term-memory, so preferences read as a stable standing directive rather than being buried under rolling conversational context (CONTEXT.md discretion call)."
  - "Directive wording is 'User's stated preferences (always follow these): ...' -- deliberately assertive rather than a soft suggestion, so PERS-04's observable-difference requirement (later plan) has a real behavioral lever to test against."
  - "PROFILE_FIELD_MAX_LENGTH = 2000 chars per field (~500 tokens): bounds per-request context growth since the profile is injected into every request unconditionally, protecting the no_compression strategy's hard-overflow behavior from unbounded preference text."
  - "get_profile (pure read, no insert) is used on the context-build path so build_system_prompt never writes to the database mid-turn while the per-chat lock is held; get_or_create_profile/update_profile (the only writing functions) are reserved for the REST layer."

patterns-established:
  - "Profile CRUD module (agent/profile.py) mirrors agent/memory.py's commit/rollback/refresh try-except and structlog logging exactly -- future user-scoped singleton tables should follow the same shape."

requirements-completed: [PERS-01, PERS-02]

# Metrics
duration: 12min
completed: 2026-09-20
---

# Phase 3 Plan 1: Personalization Backend Vertical Summary

**User-scoped `Profile` table with owner-only `GET`/`PUT /api/v1/profile` and unconditional injection of non-empty style/format/constraints into every assembled system prompt.**

## Performance

- **Duration:** ~12 min (14:33:47 - 14:38:57 UTC+3, commit-to-commit)
- **Started:** 2026-09-20T11:33:47Z
- **Completed:** 2026-09-20T11:38:57Z
- **Tasks:** 3
- **Files modified:** 9 (4 new, 5 modified)

## Accomplishments
- `Profile` table with a unique, `ondelete="CASCADE"` `user_id` FK and three 2000-char freeform fields (D-01, D-03) -- no enums, no per-chat scope.
- `agent/profile.py` CRUD module: `get_profile` (pure read), `get_or_create_profile` (lazy singleton), `update_profile` (partial update), all following the project's commit/rollback/structlog conventions.
- `GET`/`PUT /api/v1/profile` derive identity exclusively from `current_user.id` -- no `user_id`/`chat_id` field exists anywhere in `ProfileUpdate`/`ProfileResponse`, closing the IDOR/spoofing surface by construction.
- `build_system_prompt` now injects the caller's non-empty preferences as the first directive after the base system prompt, on every call including a chat's very first turn, with exactly one `Chat` lookup and zero writes on the read path.
- `agent/tools.py` and `TOOL_REGISTRY` untouched (D-02 verified via `git diff --stat` and `git log -- agent/tools.py`).
- 19 new tests across three files (8 + 4 + 7); full suite is green at 180 tests.

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the failing end-to-end profile test (RED)** - `44e03ce` (test)
2. **Task 2: Profile table, CRUD module, and the owner-scoped REST endpoints** - `7c9bfaf` (feat)
3. **Task 3: Inject the profile into every assembled system prompt** - `7ee0cdd` (feat)

_All three tasks were `tdd="true"`; Task 1 is the RED commit for the whole plan's end-to-end assertion, Tasks 2 and 3 are the GREEN commits that make each layer pass in turn (Task 2 intentionally leaves the injection assertion red until Task 3)._

## Files Created/Modified
- `shared/models.py` - Adds `Profile(SQLModel, table=True)` (unique+CASCADE `user_id` FK, three 2000-char fields, `updated_at`)
- `agent/schemas.py` - Adds `PROFILE_FIELD_MAX_LENGTH = 2000`, `ProfileUpdate`, `ProfileResponse` (no identity fields)
- `agent/profile.py` - New CRUD module: `get_profile`, `get_or_create_profile`, `update_profile`
- `agent/main.py` - Adds `from agent import memory, profile` (table auto-registration), `Profile` import, `_profile_to_response`, `GET`/`PUT /api/v1/profile`
- `agent/context_engine.py` - Hoists the single `Chat` lookup in `build_system_prompt`, adds `_format_profile`, injects profile block before facts/summary/memory
- `tests/test_profile_api.py` - 8 end-to-end REST + injection + cross-user isolation tests
- `tests/test_profile.py` - 4 CRUD-level tests (lazy-create idempotence, partial update, cascade)
- `tests/test_context_engine_profile.py` - 7 system-prompt injection unit tests
- `tests/test_database.py` - Updated the exact-table-set assertion to include the new `profile` table

## Decisions Made
- Injection placement: right after the base system prompt, before facts/summary/working-memory/long-term-memory (see `key-decisions` above).
- Directive wording kept assertive ("always follow these") per RESEARCH.md's guidance, not softened.
- 2000-char field cap chosen to bound per-request context growth given unconditional injection.
- Strict read/write split (`get_profile` vs `get_or_create_profile`) to keep the context-build path write-free.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated `test_init_db_creates_all_tables`'s exact table-name set**
- **Found during:** Task 2 (Profile table, CRUD module, and REST endpoints)
- **Issue:** `tests/test_database.py::test_init_db_creates_all_tables` asserts an exact `set` of table names created by `init_db()`. Adding the new `Profile` table (required by this task) made the actual set a strict superset of the expected one, failing this pre-existing test.
- **Fix:** Added `"profile"` to the expected set and updated the docstring to mention the profile table.
- **Files modified:** `tests/test_database.py`
- **Verification:** `pytest tests/test_database.py -q` -> 7 passed; full suite re-run afterward confirmed no other regressions.
- **Committed in:** `7c9bfaf` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix directly caused by this task's schema addition)
**Impact on plan:** Necessary correctness fix for a pre-existing test whose exact-set assertion was invalidated by design (a new table was always going to be added). No scope creep -- the fix touches only the expected-value literal.

## Issues Encountered

**Acceptance-criteria grep mismatch (non-blocking, documented for transparency):** Task 3's acceptance criteria include a literal-text check: `grep -n "User's stated preferences\|Known facts:" agent/context_engine.py` should show the preferences line at a *lower* line number than the facts line. As implemented (following the plan's own instructed structure -- `_format_profile` defined next to `_parse_facts_json`, i.e. *after* `build_system_prompt`), the literal string `"User's stated preferences"` only exists inside `_format_profile`'s `return` statement, which is textually positioned after `build_system_prompt`'s `"Known facts: "` append call. The grep therefore reports the preferences line *after* the facts line by file position, even though the actual runtime append order inside `build_system_prompt` is correct (profile block appended at what is now line ~68-72, before the facts block at line 77). This was confirmed both by direct code reading and by `tests/test_context_engine_profile.py::test_saved_profile_fields_appear_in_prompt` and `test_only_non_empty_fields_are_injected` passing, plus a manual check of the substring's position within a `build_system_prompt` return value (profile text always precedes `"Known facts:"` in the assembled prompt when both are present). Not fixed because "fixing" it would require inlining the profile-formatting logic directly into `build_system_prompt` (contradicting the plan's own explicit instruction to extract `_format_profile` as a helper next to `_parse_facts_json`) -- this is a limitation of the grep-based verification technique specified in the plan, not a functional defect.

## User Setup Required

None - no external service configuration required. The `Profile` table is created automatically by `init_db()`/`SQLModel.metadata.create_all()` the next time the Agent process starts, confirmed via a standalone `init_db()` invocation during verification (see below).

## Next Phase Readiness

- Backend vertical is fully functional and tested end-to-end (`PUT` a profile -> `build_system_prompt` for any chat owned by that user contains the saved text) without needing a browser.
- Plan 03-02 can now build the UI panel directly against `GET`/`PUT /api/v1/profile` with no backend changes required.
- `agent/tools.py` is confirmed untouched, preserving D-02 for any later phase that reuses the tool dispatcher.

---
*Phase: 03-personalization-day-12*
*Completed: 2026-09-20*
