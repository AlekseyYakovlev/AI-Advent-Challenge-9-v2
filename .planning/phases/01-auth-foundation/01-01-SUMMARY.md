---
phase: 01-auth-foundation
plan: 01
subsystem: auth
tags: [auth, session-cookie, argon2, cors, login-ui]
dependency-graph:
  requires: []
  provides:
    - shared/auth.py (hash_password, verify_password, generate_session_token, hash_session_token, SESSION_COOKIE_NAME, SESSION_TTL_DAYS)
    - shared/models.py User/Session tables
    - agent/dependencies.py (get_current_user, get_current_user_ws)
    - POST /api/v1/auth/login, POST /api/v1/auth/logout, GET /api/v1/auth/me
    - ui/static/login.html
  affects:
    - agent/main.py CORS allowlist (no longer wildcard)
    - ui/static/app.js apiFetch (credentials + 401 redirect)
tech-stack:
  added:
    - "pwdlib[argon2]>=0.3.1 (argon2id password hashing)"
  patterns:
    - "DB-backed opaque session token (secrets.token_urlsafe(32)), SHA-256 hashed at rest in Session.token_hash"
    - "30-day sliding-window expiry extended on every authenticated request"
key-files:
  created:
    - shared/auth.py
    - agent/dependencies.py
    - ui/static/login.html
    - tests/test_auth.py
  modified:
    - shared/models.py
    - agent/schemas.py
    - agent/main.py
    - requirements.txt
    - tests/conftest.py
    - tests/test_cors.py
    - tests/test_database.py
    - ui/static/app.js
    - ui/static/index.html
decisions:
  - "Session tokens are SHA-256 hashed at rest (Session.token_hash); the raw token lives only in the HTTP-only cookie (resolves RESEARCH.md Open Question 1, per plan's explicit override)"
  - "Login/wrong-password and login/unknown-username return the identical 401 detail string to prevent username enumeration"
metrics:
  duration: "~25 minutes"
  completed: 2026-09-20
---

# Phase 01 Plan 01: Auth Foundation — Login/Session Vertical Slice Summary

DB-backed session-cookie authentication (argon2id password hashing via `pwdlib`, SHA-256-hashed opaque tokens, 30-day sliding expiry) wired end-to-end from a standalone `login.html` through the Agent's REST API to a real `User`/`Session` SQLite schema.

## What Was Built

- `shared/auth.py` — `hash_password`/`verify_password` (argon2id via `pwdlib.PasswordHash.recommended()`), `generate_session_token` (`secrets.token_urlsafe(32)`), `hash_session_token` (SHA-256 hex digest), and the shared `SESSION_COOKIE_NAME`/`SESSION_TTL_DAYS` constants.
- `shared/models.py` — new `User` (`username` unique/indexed, `password_hash`, `created_at`) and `Session` (`token_hash` unique/indexed, `user_id` FK CASCADE via `sa_column`, `created_at`, `expires_at`) tables.
- `agent/dependencies.py` — `get_current_user` (FastAPI `Depends`, raises 401, extends `expires_at` on every call) and `get_current_user_ws` (identical lookup, returns `None` instead of raising, for the pre-`accept()` WebSocket gate a later plan wires in).
- `agent/main.py` — `POST /api/v1/auth/login` (verifies credentials, issues an `HttpOnly`/`SameSite=Lax` cookie, generic 401 on both wrong-password and unknown-username), `POST /api/v1/auth/logout` (204, deletes the server-side `Session` row, clears the cookie), `GET /api/v1/auth/me`; CORS `allow_origins` tightened from `["*"]` to `agent.state.CORS_ORIGINS`.
- `ui/static/login.html` — standalone page (D-08) per `01-UI-SPEC.md`: `Вход в AI Agent` heading, username/password inputs, `Войти` submit, inline `text-red-400` error slot, hidden `Сессия истекла. Пожалуйста, войдите снова.` banner revealed only via `?expired=1`.
- `ui/static/app.js` — `credentials: 'include'` added to `apiFetch` and the raw health-check `fetch`; a `resp.status === 401` branch in `apiFetch` redirects to `login.html?expired=1` (D-09); new `logout()` helper.
- `ui/static/index.html` — `Выйти` logout button in the sidebar footer next to `#agent-status`, `py-2` per the UI-SPEC spacing deviation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `tests/test_database.py::test_init_db_creates_all_tables` asserted a fixed table set**
- **Found during:** Task 2 full-suite verification
- **Issue:** The test hard-coded the expected SQLite table set (`chat`, `message`, `settings`, `tokenusage`); adding the `User`/`Session` tables this plan requires broke that assertion.
- **Fix:** Updated the expected set to include `user` and `session`.
- **Files modified:** `tests/test_database.py`
- **Commit:** `5ee247a`

## Deferred Issues (not in scope, not fixed)

- `tests/test_context_engine.py::test_extract_and_update_facts_debounce_and_merge` is flaky/timing-dependent (fire-and-forget debounced fact extraction racing a fixed `asyncio.sleep`), unrelated to auth. Failed once during a full-suite run, passed on immediate re-run in isolation. Pre-existing, out of scope for this plan — not modified.

## Verification Evidence

- `python -m pytest tests/test_auth.py tests/test_cors.py -v` — 12 passed.
- `python -m pytest tests/ -q` — 80 passed (full suite, including the pre-existing flaky test passing on this run).
- `python -c "import shared.auth"` and `python -c "import agent.dependencies"` — both succeed.
- `grep -c 'allow_origins=\["\*"\]' agent/main.py` — 0.
- `grep -c "credentials: 'include'" ui/static/app.js` — 2.
- `python -c "import shared.auth as a; h=a.hash_password('x'); assert h.startswith('$argon2id$'); assert a.verify_password('x',h); assert not a.verify_password('y',h)"` — passed (prints `ok`).
- `python -c "import shared.auth as a; t=a.generate_session_token(); assert len(a.hash_session_token(t))==64 and t not in a.hash_session_token(t)"` — passed (prints `ok`).
- `grep -n 'Field(ondelete' shared/models.py` — no matches.
- `grep -n 'secure=True' agent/main.py` — no matches (local http dev, per CONTEXT.md).

## Known Stubs

None — every artifact (login/logout/me routes, DB tables, login page, cookie round-trip) is wired to real code paths, not mocked/stubbed. `POST /api/v1/auth/users` (add-user), `Depends(get_current_user)` on pre-existing routes, the bootstrap admin, and WebSocket auth are explicitly out of scope for this plan (Plans 02-05 per SKELETON.md) — not stubs, just not yet built.

## Self-Check: PASSED

- FOUND: shared/auth.py
- FOUND: agent/dependencies.py
- FOUND: ui/static/login.html
- FOUND: tests/test_auth.py
- FOUND commit a3957ee (test: RED auth suite + fixtures)
- FOUND commit 5ee247a (feat: login/logout/me backend + CORS tightening)
- FOUND commit 0bbc3b8 (feat: login page + browser round-trip)
