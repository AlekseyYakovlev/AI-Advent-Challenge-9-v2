---
phase: 19-api-keys-and-http-chat-day-30
plan: 01
subsystem: auth
tags: [api-keys, bearer-auth, fastapi, sqlmodel]
requires: []
provides:
  - ApiKey table (apikey) and key helpers in shared/auth.py
  - Bearer branch in get_current_user and cookie-only get_session_user
  - REST router /api/v1/api-keys (create, list, revoke)
affects: [19-03, 19-04, 19-05, 19-06]
tech-stack:
  added: []
  patterns: [header-decides auth with no cookie fallback, hash-only credential storage]
key-files:
  created:
    - agent/api_keys_api.py
    - tests/test_api_keys.py
    - tests/test_bearer_auth.py
  modified:
    - shared/auth.py
    - shared/models.py
    - shared/database.py
    - agent/schemas.py
    - agent/dependencies.py
    - agent/main.py
    - tests/test_database.py
key-decisions:
  - "sha256 (not argon2) for key hashes: 256-bit random secret checked on every request"
  - "last_used_at written at most once per 60 seconds"
  - "Non-Bearer Authorization schemes are ignored and the cookie path runs"
requirements-completed: [KEY-01, KEY-02, KEY-03, KEY-04, KEY-05]
duration: ~40min
completed: 2026-10-12
---

# Phase 19 Plan 01: API keys and Bearer auth Summary

Per-user API keys (`aia_` + 43 URL-safe chars, sha256-hashed at rest) with `Authorization: Bearer` authenticating every REST route through the single `get_current_user` dependency, and cookie-only key management.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1. ApiKey table, helpers, schemas, Bearer branch | add4d08 | done |
| 2. /api/v1/api-keys router, cookie-only logout/add-user | 8865880 | done |
| 3. Tests (test_api_keys.py, test_bearer_auth.py) | see git log (test(19-01)) | done |

## Verification (observed)

- Task 1 verify (`test_auth, test_add_user, test_database, test_cors`): 39 passed.
- Task 2 verify (`test_auth, test_add_user, test_mcp_logout, test_llm_providers_api`): 46 passed.
- Task 3 verify (`test_api_keys.py test_bearer_auth.py`): 25 passed (10 + 15 incl. parametrized).
- Full suite: 2325 passed, 11 skipped, 1 failed (`tests/test_supervisor.py::test_agent_restarts_within_5_seconds`, see Deferred Issues).

## Deviations from Plan

**1. [Rule 1 - Bug] test_database table list** - `tests/test_database.py::test_init_db_creates_all_tables` asserts the exact set of tables; added `"apikey"`. File was not in `files_modified`. Commit add4d08.

**2. [Rule 3 - Blocking] Route-registration acceptance check** - the plan's one-liner that scans `app.routes` for `/api/v1/api-keys` cannot see included routers in this FastAPI version (included routes appear with no `path`; the existing `/api/v1/llm-providers` is likewise not listed). Route registration was instead proven by the passing HTTP tests.

**3. Minor signature addition** - `_user_from_api_key(db, token, path)` takes an extra `path` argument so the `api_key_rejected` warning can log the request path without passing `Request`.

## Deferred Issues

- `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` fails with a 5 s health timeout after a crash (initial 20 s start succeeds). It spawns a real agent subprocess whose startup (faiss/model imports) exceeds 5 s on this machine, with the user's app also running on 8000/8001. It touches no code changed here; not verified against the base commit.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model.

## Self-Check: PASSED

All created files exist and commits add4d08, 8865880 and the test commit are in `git log`.
