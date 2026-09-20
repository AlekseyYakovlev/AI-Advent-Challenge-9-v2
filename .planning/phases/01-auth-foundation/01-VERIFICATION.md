---
phase: 01-auth-foundation
verified: 2026-09-20T01:38:05Z
status: human_needed
score: 17/17 must-haves verified (technical); 1 item requires human confirmation
has_blocking_gaps: false
overrides_applied: 0
human_verification:
  - test: "Run `python run.py`, open the app, log in as the bootstrap admin, click the header 'Пользователи' button, fill in a username/password in the Add-user modal, submit, confirm a success toast appears, log out, log back in as the new account, and confirm its chat list is empty (not the first user's chats)."
    expected: "The modal opens, the form submits successfully (toast shown), and the newly created account lands on an empty chat list distinct from the creator's."
    why_human: "Visual rendering, modal open/close animation, and toast appearance cannot be confirmed by grep/static analysis. Plan 01-05's own SUMMARY explicitly flags this exact click-through as 'Not verified — no GUI/browser tooling available in this execution environment'; the backend contract and DOM/JS wiring were confirmed programmatically, but the actual browser interaction was never exercised by a human or a browser-driving tool."
---

# Phase 1: Auth Foundation Verification Report

**Phase Goal:** Users can securely log in, every account has equal "admin" capability, and all existing and new data is scoped to the owning user
**Verified:** 2026-09-20T01:38:05Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Note on MVP-mode goal format

ROADMAP.md marks this phase `Mode: mvp`, but the phase's `**Goal**:` field ("Users can securely log in, every account has equal 'admin' capability, and all existing and new data is scoped to the owning user") is not itself phrased as `As a ..., I want to ..., so that ....` (`bm-sdk query user-story.validate` returns `false` against it). However, every one of the five PLAN.md files under this phase embeds an identical, well-formed `## Phase Goal` user story: *"As a user of this local AI chat app, I want to log in with a username and password and stay logged in across page reloads, so that my chats, settings and memory are mine and not shared with everyone who opens the app."* — which validates as `true`. Rather than refusing verification outright, this report treats that plan-embedded user story as the MVP outcome to trace (User Flow Coverage below), while using ROADMAP's four Success Criteria as the authoritative, non-negotiable truths for goal-backward verification. This discrepancy (roadmap Goal field vs. plan Phase Goal field) should be reconciled by running `/gsd mvp-phase 1` if strict MVP tooling compliance is desired, but it is not a phase-goal-achievement blocker — informational only.

## User Flow Coverage (MVP framing)

User story: «As a user of this local AI chat app, I want to log in with a username and password and stay logged in across page reloads, so that my chats, settings and memory are mine and not shared with everyone who opens the app.»

| Step | Expected | Evidence | Status |
|------|----------|----------|--------|
| Open login page | Standalone `login.html` renders a username/password form | `ui/static/login.html:1-79` — heading `Вход в AI Agent`, form, `Войти` submit | ✓ |
| Submit credentials | POST to `/api/v1/auth/login` with `credentials:'include'`, cookie set on success, redirect to chat UI | `ui/static/login.html:58-71`; `agent/main.py:210-245` sets `HttpOnly`/`SameSite=Lax` cookie; `tests/test_auth.py` asserts cookie attributes | ✓ |
| Stay logged in across reload | Session persists via cookie, `GET /api/v1/auth/me` accepts it, sliding 30-day expiry extends per request | `agent/dependencies.py:22-49` (`get_current_user`, extends `expires_at`); `tests/test_auth.py` D-02 sliding-window assertion passing | ✓ |
| Chats/settings/memory are mine, not shared | Every `/api/v1/chats*` and `/api/v1/settings` route filters/checks `user_id`; cross-user access 404s; WS gated the same way | `agent/main.py` (`Chat.user_id == current_user.id`, `_get_chat_or_404` 404-not-403); `agent/ws.py:278-288` pre-accept gate; `tests/test_scoping.py`, `tests/test_ws_auth.py` — all passing | ✓ |
| Outcome: data not shared with everyone who opens the app | A second, independently created account (via the in-app Add-user flow) sees an empty, isolated chat list; old data is owned by a backfilled bootstrap admin, not left ownerless | `shared/database.py::bootstrap_admin_if_needed/backfill_user_id`; `tests/test_add_user.py` isolation + flat-privilege tests passing | ✓ |

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can log in with username/password and reach the chat UI (ROADMAP SC1) | ✓ VERIFIED | `POST /api/v1/auth/login` (`agent/main.py:210-245`) verifies against argon2id hash, issues cookie; `ui/static/login.html` posts with `credentials:'include'`, redirects to `/static/index.html` on 200; `tests/test_auth.py` full green |
| 2 | User can create additional user accounts, all with the same "admin" capability (ROADMAP SC2 / AUTH-02) | ✓ VERIFIED | `POST /api/v1/auth/users` (`agent/main.py:283-306`) requires only `Depends(get_current_user)` — no role/flag check (`grep -n "role\|is_admin\|is_superuser" agent/main.py` shows no privilege check); new account can itself create further accounts, asserted in `tests/test_add_user.py` |
| 3 | Session persists across reloads and covers both REST and WebSocket via HTTP-only cookie, never localStorage/JWT (ROADMAP SC3 / AUTH-03) | ✓ VERIFIED | Cookie set `httponly=True, samesite="lax"` (`agent/main.py:234`); REST gate `agent/dependencies.py::get_current_user`; WS gate `agent/ws.py:278-288` calls `get_current_user_ws` before `websocket.accept()`; no `localStorage`/JWT usage anywhere in `ui/static/app.js`; `tests/test_ws_auth.py` (6 tests) all green |
| 4 | Existing chats/settings/memory scoped to a backfilled bootstrap admin; new users see only their own (ROADMAP SC4 / AUTH-04) | ✓ VERIFIED | `shared/database.py::bootstrap_admin_if_needed/backfill_user_id` (nullable `Chat.user_id`/`Settings.user_id`, non-destructive backfill); `agent/main.py` filters every list/read by `current_user.id`; `tests/test_bootstrap_admin.py`, `tests/test_scoping.py` all green |
| 5 | A wrong password / unknown username returns the identical generic 401 (no enumeration) | ✓ VERIFIED | `agent/main.py:217-223` raises the same `Неверное имя пользователя или пароль` for both cases; `tests/test_auth.py` |
| 6 | Logout deletes the server-side session row and clears the cookie | ✓ VERIFIED | `agent/main.py:250-269`; `tests/test_auth.py` D-04 |
| 7 | Logging in twice yields two independent valid sessions | ✓ VERIFIED | No session invalidation on login (`agent/main.py:226-233` only inserts, never deletes prior rows); `tests/test_auth.py` D-03 |
| 8 | Every `/api/v1/*` route except `POST /api/v1/auth/login` requires a session; `/health` and `/debug/routes` stay open | ✓ VERIFIED | `grep -c "Depends(get_current_user)" agent/main.py` → 14; `health`/`debug_routes` handlers take no `current_user` param; `tests/test_scoping.py` 401-sweep + open-route tests |
| 9 | Cross-user chat/settings access (IDOR) returns 404, never 403, never leaks the other user's data | ✓ VERIFIED | `grep -n "status.HTTP_403" agent/main.py` → no matches; `_get_chat_or_404(session, chat_id, user_id)` 404s on ownership mismatch; `tests/test_scoping.py` IDOR cases |
| 10 | Global (`chat_id IS NULL`) settings are per-user, not installation-wide | ✓ VERIFIED | `_ensure_global_settings(session, user_id)` scopes on `Settings.user_id`; `agent/context_engine.py::get_effective_settings` owner-aware fallback; `tests/test_scoping.py` per-user isolation test |
| 11 | WebSocket handshake with no/invalid/expired cookie closes 1008 before `accept()`; wrong-owner also 1008; session validated once (D-10), not per message | ✓ VERIFIED | `agent/ws.py:278-288` (gate strictly before `await websocket.accept()` at line ~292); message loop untouched; `tests/test_ws_auth.py` all 6 cases green |
| 12 | Duplicate username on account creation returns 409 with the exact Russian message, no second row created | ✓ VERIFIED | `agent/main.py:291-311` pre-check + `IntegrityError` catch, both raising the identical `Пользователь с таким именем уже существует`; `tests/test_add_user.py` |
| 13 | Add-user response never contains `password_hash` | ✓ VERIFIED | `response_model=UserResponse` has no `password_hash` field; `tests/test_add_user.py` asserts key absence in JSON |
| 14 | A logged-in user can open an "Add user" modal in the chat UI, submit credentials, and see a toast/inline error | ✓ VERIFIED (wiring only — see Human Verification) | `ui/static/index.html:68-70,202-230` (`#btn-users`, `#add-user-modal`); `ui/static/app.js:741-770,822-826` (`openAddUserModal`/`createUser`, bound in `bindEvents`) — DOM/JS wiring confirmed by grep and by a manual `curl`-based backend walkthrough in Plan 05's SUMMARY, but the actual browser click-through was never exercised |
| 15 | The pre-existing test suite passes again with authenticated clients, including sync `TestClient` WebSocket tests | ✓ VERIFIED | `python -m pytest tests/ -q` — 123 passed, 0 failed, 0 errors (re-run live during this verification) |
| 16 | CORS no longer reflects arbitrary origins with credentials enabled | ✓ VERIFIED | `allow_origins=CORS_ORIGINS` (not `["*"]`); `tests/test_cors.py::test_cors_rejects_unknown_origin` passing |
| 17 | Passwords accepted with only `min_length=1`, no complexity rule | ✓ VERIFIED | `agent/schemas.py` `LoginRequest`/`CreateUserRequest` use only `Field(min_length=1, max_length=...)`; `tests/test_add_user.py` one-character-password 201 case |

**Score:** 17/17 truths technically verified via code + passing automated tests. 1 of the 17 (the Add-user modal's actual browser interaction) still requires human confirmation before being fully closed out — see Human Verification below.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `shared/auth.py` | hash/verify password, session token gen/hash, constants | ✓ VERIFIED | argon2id via `pwdlib.PasswordHash.recommended()`, SHA-256 token hashing, `SESSION_COOKIE_NAME`/`SESSION_TTL_DAYS` present |
| `shared/models.py` | `User`, `Session` tables; nullable `Chat.user_id`/`Settings.user_id` | ✓ VERIFIED | All four fields/tables present with correct `sa_column`/`ForeignKey` conventions (no `Field(ondelete=...)`) |
| `agent/dependencies.py` | `get_current_user`, `get_current_user_ws` | ✓ VERIFIED | Both present, sliding-expiry extension, naive-datetime normalization |
| `shared/database.py` | `migrate_add_user_id_columns`, `ensure_bootstrap_admin`, `backfill_user_id`, `bootstrap_admin_if_needed` | ✓ VERIFIED | All four functions present and exercised by `tests/test_bootstrap_admin.py` |
| `run.py` | Bootstrap credential banner before `uvicorn.run` | ✓ VERIFIED | `asyncio.run(bootstrap_admin_if_needed())` called before `uvicorn.run`; banner printed via `print()` only |
| `agent/main.py` | Auth routes + gated/scoped REST routes | ✓ VERIFIED | 14 `Depends(get_current_user)` occurrences; `login`/`logout`/`me`/`create_user` routes present |
| `agent/ws.py` | Pre-accept session + ownership gate | ✓ VERIFIED | `_user_owns_chat` + gate strictly before `websocket.accept()` |
| `ui/static/login.html` | Standalone login page | ✓ VERIFIED | Matches UI-SPEC copy/spacing/color exactly |
| `ui/static/app.js` | `credentials:'include'`, 401 redirect, logout, add-user modal handlers | ✓ VERIFIED | All present and wired in `bindEvents` |
| `ui/static/index.html` | Logout button, `#btn-users`, `#add-user-modal` | ✓ VERIFIED | All present |
| `tests/test_auth.py`, `test_bootstrap_admin.py`, `test_scoping.py`, `test_ws_auth.py`, `test_add_user.py` | Full behavioral coverage per plan | ✓ VERIFIED | 52 auth-specific tests, all passing; full suite 123/123 passing |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `agent/main.py` | `shared/auth.py` | `verify_password`/`generate_session_token` import | ✓ WIRED | Confirmed by grep and passing login tests |
| `agent/dependencies.py` | `shared/models.py::Session` | `select(SessionRow)` lookup | ✓ WIRED | Confirmed |
| `ui/static/login.html` | `/api/v1/auth/login` | `fetch` + `credentials:'include'` | ✓ WIRED | Confirmed |
| `ui/static/app.js` | `login.html` | 401 → `login.html?expired=1` redirect | ✓ WIRED | Confirmed |
| `run.py` | `shared/database.py` | `bootstrap_admin_if_needed()` | ✓ WIRED | Confirmed, called before `uvicorn.run` |
| `agent/main.py` | `agent/dependencies.py` | `Depends(get_current_user)` on 11+ routes | ✓ WIRED | 14 occurrences |
| `agent/main.py::list_chats` | `Chat.user_id` | `WHERE user_id == current_user.id` | ✓ WIRED | Confirmed |
| `agent/context_engine.py::get_effective_settings` | `Chat.user_id` | owner-aware global fallback | ✓ WIRED | Confirmed |
| `agent/ws.py` | `agent/dependencies.py` | `get_current_user_ws` before `accept()` | ✓ WIRED | Confirmed by source-order check |
| `agent/ws.py` | `shared/models.py::Chat.user_id` | `_user_owns_chat` | ✓ WIRED | Confirmed |
| `ui/static/app.js` | `/api/v1/auth/users` | `apiFetch` POST from add-user form | ✓ WIRED | Confirmed |
| `agent/main.py` | `shared/auth.py::hash_password` | account creation | ✓ WIRED | Confirmed |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Auth test suite passes | `pytest tests/test_auth.py tests/test_scoping.py tests/test_ws_auth.py tests/test_bootstrap_admin.py tests/test_add_user.py -q` | 52 passed | ✓ PASS |
| Full regression suite passes | `pytest tests/ -q` | 123 passed, 0 failed | ✓ PASS |
| No wildcard CORS | `grep -c 'allow_origins=\["\*"\]' agent/main.py` | 0 | ✓ PASS |
| No HTTP 403 IDOR oracle | `grep -n "status.HTTP_403" agent/main.py` | no matches | ✓ PASS |
| No role/privilege check | `grep -n "role\|is_admin\|is_superuser" agent/main.py` | only unrelated `Message.role` field mapping | ✓ PASS |
| No public signup route | `grep -rn "auth/register\|auth/signup" agent/main.py ui/static/*` | no matches | ✓ PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` convention or PLAN/SUMMARY-declared probes found for this phase. SKIPPED — no probes declared.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| AUTH-01 | 01-01 | User can log in with username/password | ✓ SATISFIED | Login route + `tests/test_auth.py` |
| AUTH-02 | 01-05 | Every user has equal "admin" role, can create accounts | ✓ SATISFIED | `POST /api/v1/auth/users`, no role check, `tests/test_add_user.py` flat-privilege test |
| AUTH-03 | 01-01, 01-03, 01-04 | Session via HTTP-only cookie, valid for REST and WebSocket | ✓ SATISFIED | Cookie flags, REST gate (Plan 03), WS gate (Plan 04), all tested |
| AUTH-04 | 01-02, 01-03, 01-04, 01-05 | Existing/new data scoped to owning user | ✓ SATISFIED | Bootstrap+backfill (Plan 02), REST scoping (Plan 03), WS ownership (Plan 04), isolation proof (Plan 05) |

No orphaned requirements — REQUIREMENTS.md maps exactly AUTH-01..04 to Phase 1, and all four appear in at least one plan's `requirements:` frontmatter field.

### Anti-Patterns Found

None. Scanned all phase-modified files (`shared/auth.py`, `shared/models.py`, `shared/database.py`, `agent/schemas.py`, `agent/dependencies.py`, `agent/main.py`, `agent/ws.py`, `agent/context_engine.py`, `run.py`, `ui/static/login.html`, `ui/static/app.js`, `ui/static/index.html`) for `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER`/stub patterns. Only matches were legitimate HTML `placeholder="..."` input attributes (Russian UI copy), not debt markers.

### Human Verification Required

### 1. Add-user modal browser click-through

**Test:** Run `python run.py`, log in as the bootstrap admin (credentials printed to the terminal on first run), click the header "Пользователи" button, fill in a username and password in the modal, submit, confirm a success toast appears, log out, log back in as the new account, and confirm its chat list is empty (not the first user's chats).
**Expected:** The modal opens on click, the form submits without error, a success toast is shown, and the new account's chat list is empty and distinct from the admin's.
**Why human:** This is a visual/interactive check (modal animation, toast rendering, click-through) that cannot be confirmed by static analysis or grep. It was explicitly flagged as unperformed in Plan 01-05's own SUMMARY.md ("Not verified: ... no GUI/browser tooling is available in this execution environment"), even though the DOM markup, JS wiring, and backend contract were all independently confirmed by this verification pass. This is a harvested deferred item from Plan 01-05's `<human-check>` block, not a new finding.

### Gaps Summary

No blocking gaps. All 17 derived truths and all 4 ROADMAP.md Success Criteria are supported by real, wired code and a fully green test suite (123/123 passing, re-run live during this verification). The only open item is a single, previously-flagged human click-through of the Add-user modal — backend and DOM/JS wiring for that flow are already verified; only the live visual interaction is outstanding.

---

*Verified: 2026-09-20T01:38:05Z*
*Verifier: Claude (gsd-verifier)*
