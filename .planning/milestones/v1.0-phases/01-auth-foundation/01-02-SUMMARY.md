---
phase: 01-auth-foundation
plan: 02
subsystem: auth
tags: [auth, migration, bootstrap-admin, data-ownership, sqlite]
dependency-graph:
  requires:
    - shared/auth.py hash_password/verify_password (Plan 01-01)
    - shared/models.py User/Session tables (Plan 01-01)
  provides:
    - shared/database.py (migrate_add_user_id_columns, ensure_bootstrap_admin, backfill_user_id, bootstrap_admin_if_needed)
    - shared/models.py Chat.user_id / Settings.user_id nullable FK columns
    - run.py bootstrap-admin credential banner
  affects:
    - run.py main() startup sequence (now calls bootstrap_admin_if_needed() before uvicorn.run)
    - tests/test_run_cleanup.py (assertion narrowed to its real intent)
tech-stack:
  added: []
  patterns:
    - "Idempotent additive migration: sqlite_master table-exists check + PRAGMA table_info column check, mirroring migrate_add_context_length"
    - "Nullable FK column added via ALTER TABLE (no NOT NULL) because SQLite forbids NOT NULL on an ADD COLUMN with a REFERENCES clause under PRAGMA foreign_keys=ON"
    - "Bootstrap admin created via get-or-create + secrets.token_urlsafe(18) password, printed once via run.py's sanctioned print() banner, never logged"
key-files:
  created:
    - tests/test_bootstrap_admin.py
  modified:
    - shared/models.py
    - shared/database.py
    - run.py
    - tests/test_run_cleanup.py
decisions: []
metrics:
  duration: "~30 minutes"
  completed: 2026-09-20
---

# Phase 01 Plan 02: Bootstrap Admin & Data Ownership Migration Summary

Nullable `user_id` FK columns added to `Chat`/`Settings` via an idempotent `ALTER TABLE` migration, a bootstrap admin account auto-created on first run with credentials printed once from `run.py`'s terminal-visible banner, and every pre-existing chat/settings row backfilled to that admin — closing the AUTH-04 data-continuity gap ahead of Plan 03 turning on auth enforcement.

## What Was Built

- `shared/models.py` — `Chat.user_id: Optional[int]` and `Settings.user_id: Optional[int]`, both nullable `sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=True)`.
- `shared/database.py`:
  - `migrate_add_user_id_columns(conn)` — idempotent, per-table (`chat`, `settings`) existence + `PRAGMA table_info` guarded `ALTER TABLE ... ADD COLUMN user_id INTEGER REFERENCES user(id)` (no `NOT NULL` — SQLite forbids it on a `REFERENCES` column added via `ADD COLUMN`).
  - `ensure_bootstrap_admin() -> tuple[str, str] | None` — get-or-create against an empty `user` table; generates `username="admin"`, `password=secrets.token_urlsafe(18)`; returns credentials only on the creating run, `None` on every subsequent run.
  - `backfill_user_id(admin_id)` — parameter-bound `UPDATE chat/settings SET user_id = :admin_id WHERE user_id IS NULL`, non-destructive to already-owned rows.
  - `bootstrap_admin_if_needed()` — `init_db()` → `ensure_bootstrap_admin()` → resolve owner (newly-created admin, or oldest existing `User` by `created_at` for D-07's single-migration-path) → `backfill_user_id()`.
  - `init_db()` extended to call `migrate_add_user_id_columns(conn)` before `create_all`.
- `run.py` — `asyncio.run(bootstrap_admin_if_needed())` runs after port cleanup and before `uvicorn.run`; when it returns credentials, prints a `"=" * 60`-bounded banner with the username/password once, using the sanctioned `print()` exception. `agent/main.py`'s `lifespan()` is untouched (its stdout is redirected to `logs/agent.log` by the supervisor, so it would never reach the developer).
- `tests/test_bootstrap_admin.py` — 9 tests covering bootstrap idempotency, password hash/entropy, non-destructive backfill (including a second-user-owned-row case), `init_db()` re-runnability, and the combined `bootstrap_admin_if_needed()` flow.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `tests/test_run_cleanup.py::test_run_py_no_asyncio_run_error` asserted a blanket absence of `"asyncio.run("` anywhere in `run.py`**
- **Found during:** Task 3 verification (`python -m pytest tests/test_run_cleanup.py tests/test_bootstrap_admin.py -v`)
- **Issue:** The test's docstring states its intent as "run.main must not wrap uvicorn in asyncio.run()", but its implementation did a literal substring search for `"asyncio.run("` across the entire file — which would reject *any* legitimate `asyncio.run()` call anywhere in `run.py`, including the one this task's plan explicitly instructs adding (`asyncio.run(bootstrap_admin_if_needed())`, called before `uvicorn.run`, never wrapping it).
- **Fix:** Narrowed the assertion to the test's actual intent — `"asyncio.run(uvicorn.run" not in source` — and mocked `run.bootstrap_admin_if_needed` with `AsyncMock(return_value=None)` in the same test so it stays focused on the `uvicorn.run`-call-count assertion it was already making.
- **Files modified:** `tests/test_run_cleanup.py`
- **Commit:** `3214ab0`

### Deferred Issues (not in scope, not fixed)

- `tests/test_context_engine.py::test_extract_and_update_facts_debounce_and_merge` failed once during a full-suite run (`89 passed` for Task 2's run, `1 failed` for the post-Task-3 full-suite run), then passed immediately in isolation. This is the same pre-existing timing-dependent flake already documented as a deferred issue in Plan 01-01's SUMMARY (fire-and-forget debounced fact extraction racing a fixed `asyncio.sleep`) — unrelated to this plan's changes, not modified.

## Verification Evidence

- `python -m pytest tests/test_bootstrap_admin.py -x` (before Task 2) — collection error / `ImportError: cannot import name 'backfill_user_id'` (RED, as designed).
- `python -m pytest tests/ -q --collect-only` (before Task 2) — 80 tests collected across the rest of the suite, confirming no other file broke on collection.
- `python -m pytest tests/test_bootstrap_admin.py tests/test_database.py -v` — 16 passed.
- `python -m pytest tests/test_run_cleanup.py tests/test_bootstrap_admin.py -v` — 13 passed (after the Rule 1 fix above).
- `python -m pytest tests/ -q` — 88 passed, 1 failed (the documented pre-existing flake), 1 error (same test's teardown); re-run of that single test in isolation — 1 passed.
- `grep -vn '^\s*#' shared/database.py | grep -c "ADD COLUMN user_id INTEGER REFERENCES user(id)"` — 1 (statement built from a loop variable, as anticipated by the acceptance criteria's alternate branch).
- `grep -n "user_id INTEGER NOT NULL" shared/database.py` — no matches.
- `grep -n "Field(ondelete" shared/models.py` — no matches.
- `grep -n "password=" shared/database.py | grep -i "logger"` — no matches.
- `DB_PATH=test_app.db python -c "import asyncio,shared.database as d; print(asyncio.run(d.bootstrap_admin_if_needed()) is not None)"` — printed `True` on first run, `False` on second run against the same DB file.
- `grep -n "bootstrap_admin_if_needed" run.py` — present, called before `uvicorn.run`.
- `grep -n "bootstrap_admin_if_needed\|Bootstrap admin" agent/main.py` — no matches.
- `grep -c '"=" \* 60' run.py` — 2 (opening and closing separator lines).
- `grep -n "logger.*password" run.py` — no matches.
- `DB_PATH=test_app.db python -c "import asyncio,shared.database as d; c=asyncio.run(d.bootstrap_admin_if_needed()); print('username' if c else 'none')"` — printed `username`.
- `grep -n "user_id" shared/models.py` — present on `Chat`, `Settings`, and `Session.user_id` (the pre-existing non-nullable FK from Plan 01-01).
- `grep -n "NOT NULL" shared/database.py` — no matches.
- `grep -n "bootstrap" agent/main.py` — no matches (confirms bootstrap logic never touches the Agent process, per the `logs/agent.log` redirection constraint).

## Known Stubs

None — every artifact (migration, bootstrap-admin creation, backfill, credential banner) is wired to real code paths and exercised by the test suite and the manual CLI proofs above.

## Self-Check: PASSED

- FOUND: shared/models.py (Chat.user_id / Settings.user_id)
- FOUND: shared/database.py (migrate_add_user_id_columns, ensure_bootstrap_admin, backfill_user_id, bootstrap_admin_if_needed)
- FOUND: run.py (bootstrap_admin_if_needed invocation + credential banner)
- FOUND: tests/test_bootstrap_admin.py
- FOUND commit 3465a8a (test: RED bootstrap-admin/backfill/migration suite)
- FOUND commit 426040f (feat: user_id columns + migration + bootstrap functions)
- FOUND commit 3214ab0 (feat: run.py credential banner + test_run_cleanup.py fix)
