# Phase 1: Auth Foundation - Context

**Gathered:** 2026-09-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Users can securely log in with username/password, every account has equal "admin" capability (any user can create additional accounts), and all existing and new data (chats/settings/memory) is scoped to the owning user via `user_id`. Session is maintained via an HTTP-only session cookie valid for both REST and WebSocket. OAuth/external identity providers and fine-grained roles are explicitly out of scope.

</domain>

<decisions>
## Implementation Decisions

### Session Mechanism
- **D-01:** DB-backed `Session` table (new SQLModel table: session_id, user_id, created_at, expires_at) — not Starlette `SessionMiddleware`/signed-cookie. An opaque random token in the HTTP-only cookie maps to a session row.
- **D-02:** 30-day sliding-window expiry — `expires_at` extends on each authenticated request, not a fixed cutoff.
- **D-03:** Multiple concurrent sessions per user are allowed. Logging in on a second device/browser does NOT invalidate the first session.
- **D-04:** Logout deletes the server-side session row AND clears the cookie — real revocation, not just a client-side cookie clear.

### Account Creation & Bootstrap Admin
- **D-05:** On first startup with zero users, auto-generate a bootstrap admin account (username + random password), create it, and print the credentials once to the console/startup banner (this is the sanctioned `print()` exception already carved out in `CLAUDE.md` for `run.py`'s startup banner).
- **D-06:** No public/unauthenticated signup route. Any already-logged-in user can create additional accounts via an "Add user" action inside the app (all accounts are equal-privilege per AUTH-02).
- **D-07:** Pre-migration data (existing chats, settings, facts) is scoped to the bootstrap admin user created in D-05 — one migration path, no separate "legacy" user concept.

### Login UI Integration
- **D-08:** Separate `ui/static/login.html` page (NOT a login gate embedded inside `index.html`). User explicitly chose this over the recommended embedded-gate approach — deliberate deviation, keep it.
- **D-09:** On any 401 response mid-session, redirect the browser to `login.html` with an inline "your session expired, please log in again" banner. No proactive client-side expiry tracking/timers — react only when a real request actually fails.
- **D-10:** WebSocket auth is validated once at connection-accept time via the session cookie sent on the WS handshake (same origin as the existing `_validate_origin()` check in `agent/ws.py`) — no per-message re-validation inside the message loop.

### Password Hashing & Policy
- **D-11:** `argon2id` via `pwdlib` for password hashing. Confirmed safe: actual runtime is Python 3.13 (not the stale 3.8 floor in `STACK.md`), well past `argon2-cffi`'s 3.9+ requirement — no version bump or fallback-to-bcrypt needed.
- **D-12:** No enforced password policy — any non-empty password is accepted. User explicitly chose this over the recommended minimum-length rule — deliberate deviation for this low-stakes, trusted-user coursework context.

### Claude's Discretion
- Exact `Session`/`User` table field set and indexes beyond what D-01/D-07 require.
- Cookie attributes: `HttpOnly=True` always; `Secure` flag omitted (local dev runs over plain http); `SameSite=Lax`.
- Exact markup/styling of `login.html` and the "session expired" banner (match existing Tailwind usage in `index.html`).
- Where the "Add user" UI lives (modal vs. a settings-panel section vs. small dedicated page) — pick whatever best fits `app.js`'s existing panel patterns.
- Expired-session-row cleanup strategy: lazy deletion on next login attempt vs. any other in-request approach. No new background/scheduled process (hard constraint: no extra infra/services).

</decisions>

<specifics>
## Specific Ideas

- The Phase 1 open questions flagged in `.planning/STATE.md` are now resolved by this discussion: session mechanism → DB-backed table (D-01); Python floor vs. `argon2-cffi` → non-issue, actual runtime is 3.13 (D-11).
- The frontend does NOT proxy through the UI server (port 8000) for API/WS calls — `ui/static/app.js` talks directly to the Agent process on port 8001 for both `fetch` and `WebSocket`. This means auth (login endpoint, session cookie, session validation) is entirely an Agent-process concern; `ui/main.py` (UI server) needs no auth logic of its own, since it only serves static files including the new `login.html`.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Auth requirements & constraints
- `.planning/PROJECT.md` — Constraints section: HTTP-only session cookie (never JWT/localStorage), `user_id` data scoping, "every user is admin" hard constraint
- `.planning/REQUIREMENTS.md` §Auth — AUTH-01 through AUTH-04 acceptance criteria
- `.planning/ROADMAP.md` §Phase 1: Auth Foundation — goal, branch name (`Auth`), depends-on, success criteria
- `.planning/STATE.md` §Open Questions — Phase 1's originally-flagged session-mechanism and Python-floor questions, both resolved above
- `CLAUDE.md` §Hard constraints — auth mechanism (HTTP-only cookie), no Docker/multiprocessing/external identity providers, `print()` exception for `run.py`'s startup banner

### Existing architecture (no auth exists today)
- `.planning/codebase/ARCHITECTURE.md` — confirms current state is "Authentication: None"; documents the WebSocket origin-validation pattern (`_validate_origin()`) that the new session-cookie check extends; documents the two-process split and that the frontend talks directly to the Agent process
- `.planning/codebase/STACK.md` — current DB schema/tables, Python/dependency versions (confirms 3.13 runtime vs. stale 3.8 floor doc)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shared/models.py` SQLModel pattern (`sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`) — new `User` and `Session` tables must follow this exact FK-cascade convention, never `Field(ondelete=...)`.
- `shared/database.py::init_db()` migration pattern (`async def migrate_*()` functions called in sequence) — bootstrap-admin creation and backfilling `user_id` onto existing `Chat`/`Settings` rows should be added as new `migrate_*` steps here.
- `agent/ws.py::_validate_origin()` — existing precedent for a check performed once at WS-accept time; the session-cookie check slots in alongside it.
- `run.py`'s startup banner — the one sanctioned `print()` exception in `CLAUDE.md`; reuse it to print bootstrap admin credentials on first run.

### Established Patterns
- Global-vs-per-chat NULL-fallback pattern (`Settings.chat_id`) — not directly reused, but the same "explicit, inspectable state" philosophy should carry into how `User`/`Session` are modeled.
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers (`agent/main.py::delete_chat`/`update_settings` pattern) — required for all new `User`/`Session` writes.
- `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses — applies to any new queries.

### Integration Points
- `agent/main.py` — new REST endpoints needed: `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `POST /api/v1/auth/users` (add user).
- `agent/ws.py::ws_chat` — add session-cookie validation at connection-accept time, alongside existing origin validation.
- `shared/models.py` — new `User`, `Session` tables.
- `shared/database.py::init_db()` — new migration step(s): create tables, create bootstrap admin, backfill `user_id` onto existing `Chat`/`Settings` rows.
- `ui/static/` — new `login.html` page; `app.js` needs 401-detection → redirect-to-login-with-banner logic, and cookies are already sent automatically since fetch/WS already target the same origin (port 8001).
- `requirements.txt` — add `pwdlib[argon2]` (or `argon2-cffi` directly).

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-auth-foundation*
*Context gathered: 2026-09-19*
