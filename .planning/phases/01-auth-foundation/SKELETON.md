# Walking Skeleton — AiAdventAgentV2 (Week 3: Agent Memory & Task State)

**Phase:** 1 — Auth Foundation
**Generated:** 2026-09-19

## Capability Proven End-to-End

A real person opens `login.html`, submits the bootstrap admin credentials printed once by `python run.py`, receives an HTTP-only DB-backed session cookie, lands on the chat UI, and from there every REST call and the chat WebSocket are gated by that cookie and scoped to that user's own rows — and they can create a second account that sees an empty, isolated workspace.

This is the thinnest slice that touches the whole stack: **terminal → SQLite migration → password hash → login page → REST route → DB write → cookie → authenticated REST read → authenticated WebSocket handshake → owner-filtered data**.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Process model | Unchanged: UI (`:8000`, static files + `AgentSupervisor`) spawns Agent (`:8001`, all API/DB/LLM) via `asyncio.create_subprocess_exec` | Hard constraint (CLAUDE.md): no Docker, no `multiprocessing`/`os.fork`, no broker. Auth adds no new process. |
| Where auth lives | Entirely in the Agent process (`agent/`), never in `ui/main.py` | The frontend talks directly to `:8001` for both `fetch` and `WebSocket`; the UI server only serves static files, so it needs no auth logic (CONTEXT.md Specifics). |
| Session mechanism | DB-backed `Session` table + opaque `secrets.token_urlsafe(32)` token in an HTTP-only cookie (`session_id`), 30-day sliding expiry | D-01/D-02/D-04. Signed-cookie/JWT sessions cannot be revoked server-side, which logout requires. |
| Session token at rest | Only `sha256(token)` is stored in `Session.token_hash`; the raw token exists solely in the cookie | Resolves RESEARCH.md Open Question 1. A 256-bit token needs no key-stretching, and lookup stays a single indexed equality query. |
| Password hashing | `argon2id` via `pwdlib[argon2]`, wrapped in `shared/auth.py` | D-11; OWASP's current first choice. Never hand-rolled (`Don't Hand-Roll`, RESEARCH.md). |
| Password policy | Any non-empty password (`min_length=1`), no complexity rule, no strength UI | D-12 — deliberate, documented deviation for this local, trusted-user coursework scope. |
| Cookie attributes | `HttpOnly=True`, `SameSite=Lax`, `Path=/`, `Secure` deliberately omitted | Local `http://127.0.0.1` dev; `Secure` would make the cookie undeliverable. Revisit only if exposed beyond localhost. |
| Cross-origin transport | `credentials: 'include'` on every Agent-bound `fetch`, plus an explicit CORS allowlist (`agent/state.py::CORS_ORIGINS`) replacing `allow_origins=["*"]` | `:8000` → `:8001` is cross-origin; without the opt-in the cookie is silently dropped (Pitfall 1), and wildcard-with-credentials reflects any Origin (Pitfall 2). |
| Data ownership | Nullable `user_id` FK columns on `chat` and `settings`, backfilled to the bootstrap admin; non-nullness enforced at the application layer | SQLite refuses `NOT NULL` on an FK column added via `ALTER TABLE ADD COLUMN` (Pitfall 3). The 12-step table rebuild is explicitly out of scope. |
| Access control model | Flat: authenticated + row ownership. No roles, no RBAC. Ownership mismatch returns **404, never 403** | AUTH-02 ("every user is admin") and the IDOR mitigation — a distinguishable 403 would confirm another user's ids exist. |
| Global settings semantics | A `Settings` row with `chat_id IS NULL` is now that **user's** global default (`Settings.user_id`); the per-chat → global fallback order is unchanged | Preserves the project's `_resolve_settings` invariant while preventing one user's system prompt, facts, and summary from leaking into another's LLM context. |
| WebSocket auth | Validated **once**, before `websocket.accept()`, alongside the existing `_validate_origin()` gate; both failure modes close with `1008 Unauthorized` | D-10. Cookies ride the WS handshake automatically — no `credentials` opt-in exists for WebSocket. |
| Bootstrap admin | Created and printed **from `run.py`**, never from the Agent's `lifespan()` | `ui/supervisor.py` redirects the Agent's stdout to `logs/agent.log`, and CLAUDE.md's `print()` exception is scoped to `run.py`'s banner (Pitfall 4). |
| Login UI | Standalone `ui/static/login.html`, not a gate embedded in `index.html`; a 401 anywhere redirects to `login.html?expired=1` | D-08/D-09. No client-side expiry timers — the app reacts only to a real 401. |
| Frontend stack | Unchanged: vanilla JS + Tailwind/Marked/DOMPurify from CDN, no bundler, no npm | Hard constraint. `login.html` and the add-user modal reuse `index.html`'s existing class vocabulary. |
| Directory layout | `shared/auth.py` (new, importable by both processes), `agent/dependencies.py` (new), everything else extends existing modules | Keeps the existing `shared/` ↔ `agent/` ↔ `ui/` split intact; no new package, no new layer. |

## Stack Touched in Phase 1

- [x] Project scaffold — pre-existing; one dependency added (`pwdlib[argon2]`, slopcheck `[OK]`, audited in RESEARCH.md)
- [x] Routing — real routes: `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `POST /api/v1/auth/users`, plus `Depends(get_current_user)` on all eleven pre-existing `/api/v1` handlers
- [x] Database — real write (`User` + `Session` inserts, `user_id` backfill, additive migration) AND real read (session lookup by `token_hash` on every request, owner-filtered chat/settings queries)
- [x] UI — interactive: `login.html` submits credentials and stores the cookie; `app.js` sends `credentials: 'include'`, redirects on 401, logs out, and creates users through a modal
- [x] WebSocket — the handshake is authenticated and ownership-checked before `accept()`
- [x] Local full-stack run command — `python run.py` (prints bootstrap credentials once on first run); tests via `pytest tests/ -v`

## Plan Map (how the skeleton was built)

| Plan | Wave | Slice |
|---|---|---|
| 01-01 | 1 | `shared/auth.py`, `User`/`Session` tables, `get_current_user`/`get_current_user_ws`, login/logout/me routes, CORS allowlist, `login.html`, `credentials: 'include'` + 401 redirect |
| 01-02 | 2 | Nullable `Chat.user_id`/`Settings.user_id`, idempotent additive migration, bootstrap admin, `user_id` backfill, `run.py` credential banner |
| 01-03 | 3 | `Depends(get_current_user)` + `user_id` filtering on every REST route, owner-scoped global settings, IDOR 404s, full test-suite repair |
| 01-04 | 4 | Pre-accept session + chat-ownership gate on `WS /ws/chat/{chat_id}` |
| 01-05 | 5 | `POST /api/v1/auth/users` and the "Add user" modal (AUTH-02) |

## Out of Scope (Deferred to Later Slices)

Explicit, so later phases do not re-litigate Phase 1's minimalism:

- Password reset, email verification, "forgot password" — no mail infrastructure exists and none is wanted
- OAuth / external identity providers, and any roles/permissions model beyond authenticated-or-not (REQUIREMENTS.md Out of Scope)
- CSRF tokens beyond `SameSite=Lax` (T-01-08, accepted)
- `Secure` cookies / HTTPS / exposure beyond localhost (T-01-07, accepted)
- DB-level `NOT NULL` on `chat.user_id` / `settings.user_id` via SQLite's 12-step table rebuild (T-01-15, accepted; enforced at the application layer instead)
- Re-validating the session per WebSocket message (T-01-28, rejected by D-10)
- Background/scheduled expired-session cleanup — no new process is permitted; cleanup stays in-request if it is ever needed
- A "manage users" list view, user deletion, username change, or an admin panel — Phase 1 ships only the two forms (login, add user)
- Multi-tenant isolation beyond `user_id` (orgs/teams), and cross-user memory sharing

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of this skeleton without renegotiating the decisions above. Every new table created from Phase 2 onward carries a `user_id` FK from birth (no backfill needed again), and every new route is registered with `Depends(get_current_user)` plus an owner filter from its first line.

- **Phase 2 (Day 11) — Memory:** three inspectable memory layers in dedicated `user_id`-scoped tables, written only via explicit LLM tool calls
- **Phase 3 (Day 12) — Personalization:** a per-user profile injected into every request, editable in the UI
- **Phase 4 (Day 13) — Task State Machine:** multiple LLM-created tasks per chat, each with an explicit lifecycle
- **Phase 5 (Day 14) — Invariants:** global (shared) and per-chat (`user_id`-scoped) ground rules injected into context and checked against agent behavior
- **Phase 6 (Day 15) — Controlled Transitions:** illegal task-state transitions hard-rejected with an explanation; pause/resume verified
