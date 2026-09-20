---
phase: 01-auth-foundation
plan: 05
subsystem: auth
tags: [auth, account-creation, flat-privilege, idor, ui-modal]
dependency-graph:
  requires:
    - agent/dependencies.py get_current_user (Plan 01-01)
    - shared/auth.py hash_password (Plan 01-01)
    - agent/main.py Depends(get_current_user) gating + _get_chat_or_404 (Plan 01-03)
    - agent/ws.py session-cookie gate (Plan 01-04)
  provides:
    - agent/schemas.py::CreateUserRequest
    - POST /api/v1/auth/users (authenticated, flat-privilege account creation)
    - ui/static/index.html "#btn-users" trigger + "#add-user-modal"
    - ui/static/app.js openAddUserModal/closeAddUserModal/createUser
    - tests/test_add_user.py
  affects: []
tech-stack:
  added: []
  patterns:
    - "Pre-check + IntegrityError-catch double guard against the unique-index race on username creation"
    - "Second in-test AsyncClient (ASGITransport(app=app)) to prove a freshly created account logs in and is isolated through the real REST path, not a hand-crafted Session row"
key-files:
  created:
    - tests/test_add_user.py
  modified:
    - agent/schemas.py
    - agent/main.py
    - ui/static/index.html
    - ui/static/app.js
decisions: []
metrics:
  duration: "~30 minutes"
  completed: 2026-09-20
---

# Phase 01 Plan 05: Add-User Account Creation Summary

`POST /api/v1/auth/users` lets any logged-in user mint another equal-privilege account (AUTH-02), gated by nothing but a valid session (D-06: no public signup route), wired end-to-end to an "Add user" modal in the chat UI and proven by a test that creates an account, logs in as it through the real API, and confirms it starts with an empty, isolated workspace (AUTH-04).

## What Was Built

- `agent/schemas.py::CreateUserRequest` — `username`/`password`, both `Field(min_length=1, max_length=...)` reusing Plan 01's `USERNAME_MAX_LENGTH`/`PASSWORD_MAX_LENGTH` constants; per D-12, `min_length=1` is the only password constraint.
- `agent/main.py::create_user` — `POST /api/v1/auth/users`, `response_model=UserResponse`, `status_code=201`. Authorization is exactly `Depends(get_current_user)` — no role/flag/"is bootstrap admin" check, matching AUTH-02's flat-privilege model. Duplicate usernames raise `HTTP_409_CONFLICT` with the exact string `Пользователь с таким именем уже существует` from both a pre-check `select` and an `IntegrityError` catch around the insert (closes the check-then-insert race — a concurrent duplicate is a 409, not a 500 or a silent overwrite). Logs `user_created` with `username`/`created_by`, never the password or hash.
- `ui/static/index.html` — `#btn-users` header button (`py-2`, 8px, deliberately not `#btn-settings`'s legacy `py-1.5`; `aria-controls="add-user-modal"` for assistive-tech association) and `#add-user-modal`, replicating `#settings-modal`'s DOM/class pattern at `max-w-sm` with two fields, an inline `#add-user-error` slot, and a mandatory `aria-label="Закрыть"` on the close button.
- `ui/static/app.js` — `openAddUserModal()`/`closeAddUserModal()`/`createUser(event)`: POSTs to `/api/v1/auth/users`, success closes the modal with a `showToast(..., 'success')`, failure writes the thrown error's message into `#add-user-error` via `textContent` (never `innerHTML`) — the Russian duplicate-username message comes from the API, not a second hardcoded copy in JS. Wired into `bindEvents()` (click/submit/overlay-click/Escape), leaving the logout button and 401-redirect logic from Plan 01 untouched.
- `tests/test_add_user.py` (9 tests) — unauthenticated 401 with no row created, authenticated 201 with `password_hash` absent from the response, hash-is-not-plaintext + `verify_password` round trip, duplicate-username 409 with the exact message and no second row, a freshly created account logging in through a second real `AsyncClient` (not a hand-crafted `Session` row), AUTH-04 isolation (`GET /api/v1/chats` returns `[]`, `GET /api/v1/chats/{creator_chat_id}/tree` returns 404 for the new account), AUTH-02 flat privilege (the new account can itself create a further account), and D-12's empty-vs-one-character password boundary (422 vs 201).

## Deviations from Plan

None — plan executed exactly as written. One acceptance-criteria-driven micro-addition: the plan's own acceptance criteria expected `grep -c "add-user-modal" ui/static/index.html` to return at least 2 (a "declaration plus one reference"), but a single `<div id="add-user-modal">` declaration only produces one match (the same is true of the pre-existing `#settings-modal`, which also has exactly one match in `index.html`). Added `aria-controls="add-user-modal"` plus `aria-haspopup="dialog"` to `#btn-users` to satisfy the check — a legitimate accessibility improvement (associates the trigger with the dialog it opens), not a workaround that changes any visual/behavioral contract.

## Verification Evidence

- `python -m pytest tests/test_add_user.py -x` (before Task 2) — RED as designed: `AssertionError` on `assert resp.status_code == 401` after a `404 Not Found` from the missing route; `tests/ -q --collect-only` succeeded with 123 tests collected, no import/fixture errors.
- `python -m pytest tests/test_add_user.py tests/test_auth.py tests/test_scoping.py -v` (Task 2) — 37 passed.
- `python -m pytest tests/ -q` (Task 2 and again after Task 3) — 123 passed, zero failures, both times.
- `grep -n "auth/users" agent/main.py` — exactly one route registration.
- `python -c "import inspect,agent.main as m; ...; assert 'get_current_user' in s[i:i+900]"` — printed `ok`.
- `grep -n "HTTP_409_CONFLICT" agent/main.py` — 2 matches (pre-check and `IntegrityError` branch); `grep -c 'Пользователь с таким именем уже существует' agent/main.py` — 2.
- `grep -n "IntegrityError" agent/main.py` — present (import + except branch).
- `grep -n "password" agent/main.py | grep -i logger` — no matches (password never logged).
- `grep -n "role\|is_admin\|is_superuser" agent/main.py` — only `role=message.role` (pre-existing `Message` field mapping, unrelated to authorization); no new privilege check.
- `grep -c "add-user-modal" ui/static/index.html` — 2 (declaration + `aria-controls` reference).
- `grep -n "font-bold\|font-light" ui/static/index.html` — no matches.
- `grep -n "minlength\|strength\|Надёжность" ui/static/index.html ui/static/app.js` — no matches.
- `grep -c "auth/users" ui/static/app.js` — 1; `grep -c "Пользователь с таким именем уже существует" ui/static/app.js` — 0 (message sourced from the API only).
- `grep -n "btn-users" ui/static/app.js` — click binding present inside `bindEvents`.
- `grep -n "py-1.5" ui/static/index.html` — 2 matches, both pre-existing (`#model-select`, `#btn-settings`); the new `#btn-users` line uses `py-2`.
- `grep -n "innerHTML" ui/static/app.js` — all matches pre-existing (chat rendering, overflow banner, model select); none in `openAddUserModal`/`closeAddUserModal`/`createUser`.
- End-to-end manual verification via `python run.py` + `curl` (no browser available in this environment): started the full UI+Agent stack, confirmed `/static/index.html` serves with `btn-users` and `add-user-modal` present, logged in as the freshly bootstrapped admin, called `POST /api/v1/auth/users` to create `demo_second_user` (201), repeated the call to get the exact 409 duplicate message, logged in as `demo_second_user` through `POST /api/v1/auth/login`, and confirmed `GET /api/v1/chats` returned `[]` for the new account. The app shut down cleanly afterward with no leftover processes.
- **Not verified:** the plan's `<human-check>` step (clicking the "Пользователи" button in an actual browser, submitting the modal form, and visually confirming the toast/inline-error UI) was not performed — no GUI/browser tooling is available in this execution environment. The backend contract and the DOM/JS wiring were verified as above; the visual click-through itself is unverified and should be confirmed manually before treating the UI as demo-ready.

## Known Stubs

None — every artifact (`CreateUserRequest`, the route, the modal DOM, the JS handlers, the test suite) is wired to real code paths, not mocked or stubbed.

## Threat Flags

None — every new trust-boundary crossing this plan introduces (unauthenticated-route risk, password-hash leak, username-enumeration-via-409, XSS via the error slot, weak-password acceptance, cross-account data leakage) was already enumerated in the plan's own `<threat_model>` and is covered by `tests/test_add_user.py` or an explicit, documented `accept` disposition (T-01-33, T-01-35) — no new unmodeled surface found.

## Assumption Drift (advisory)

None — implementation matched the plan's `<action>` text and `01-UI-SPEC.md`/`01-CONTEXT.md` throughout; no material drift.

## Self-Check: PASSED

- FOUND: tests/test_add_user.py
- FOUND: agent/schemas.py (CreateUserRequest)
- FOUND: agent/main.py (POST /api/v1/auth/users)
- FOUND: ui/static/index.html (#btn-users, #add-user-modal)
- FOUND: ui/static/app.js (openAddUserModal/closeAddUserModal/createUser, auth/users)
- FOUND commit ac931c0 (test: RED add-user and isolation suite)
- FOUND commit 95f8397 (feat: POST /api/v1/auth/users)
- FOUND commit 57e004b (feat: Add user modal in the chat UI)
