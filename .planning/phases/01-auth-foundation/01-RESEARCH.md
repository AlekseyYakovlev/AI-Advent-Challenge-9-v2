# Phase 1: Auth Foundation - Research

**Researched:** 2026-09-19
**Domain:** Cookie-based session authentication for a two-process FastAPI app (SQLite/SQLModel), argon2id password hashing
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Session Mechanism**
- **D-01:** DB-backed `Session` table (new SQLModel table: session_id, user_id, created_at, expires_at) — not Starlette `SessionMiddleware`/signed-cookie. An opaque random token in the HTTP-only cookie maps to a session row.
- **D-02:** 30-day sliding-window expiry — `expires_at` extends on each authenticated request, not a fixed cutoff.
- **D-03:** Multiple concurrent sessions per user are allowed. Logging in on a second device/browser does NOT invalidate the first session.
- **D-04:** Logout deletes the server-side session row AND clears the cookie — real revocation, not just a client-side cookie clear.

**Account Creation & Bootstrap Admin**
- **D-05:** On first startup with zero users, auto-generate a bootstrap admin account (username + random password), create it, and print the credentials once to the console/startup banner (this is the sanctioned `print()` exception already carved out in `CLAUDE.md` for `run.py`'s startup banner).
- **D-06:** No public/unauthenticated signup route. Any already-logged-in user can create additional accounts via an "Add user" action inside the app (all accounts are equal-privilege per AUTH-02).
- **D-07:** Pre-migration data (existing chats, settings, facts) is scoped to the bootstrap admin user created in D-05 — one migration path, no separate "legacy" user concept.

**Login UI Integration**
- **D-08:** Separate `ui/static/login.html` page (NOT a login gate embedded inside `index.html`). User explicitly chose this over the recommended embedded-gate approach — deliberate deviation, keep it.
- **D-09:** On any 401 response mid-session, redirect the browser to `login.html` with an inline "your session expired, please log in again" banner. No proactive client-side expiry tracking/timers — react only when a real request actually fails.
- **D-10:** WebSocket auth is validated once at connection-accept time via the session cookie sent on the WS handshake (same origin as the existing `_validate_origin()` check in `agent/ws.py`) — no per-message re-validation inside the message loop.

**Password Hashing & Policy**
- **D-11:** `argon2id` via `pwdlib` for password hashing. Confirmed safe: actual runtime is Python 3.13 (not the stale 3.8 floor in `STACK.md`), well past `argon2-cffi`'s 3.9+ requirement — no version bump or fallback-to-bcrypt needed.
- **D-12:** No enforced password policy — any non-empty password is accepted. User explicitly chose this over the recommended minimum-length rule — deliberate deviation for this low-stakes, trusted-user coursework context.

### Claude's Discretion
- Exact `Session`/`User` table field set and indexes beyond what D-01/D-07 require.
- Cookie attributes: `HttpOnly=True` always; `Secure` flag omitted (local dev runs over plain http); `SameSite=Lax`.
- Exact markup/styling of `login.html` and the "session expired" banner (match existing Tailwind usage in `index.html`).
- Where the "Add user" UI lives (modal vs. a settings-panel section vs. small dedicated page) — pick whatever best fits `app.js`'s existing panel patterns.
- Expired-session-row cleanup strategy: lazy deletion on next login attempt vs. any other in-request approach. No new background/scheduled process (hard constraint: no extra infra/services).

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AUTH-01 | User can log in with username/password (no external identity provider) | `POST /api/v1/auth/login` pattern (Code Examples), `pwdlib[argon2]` hash/verify (Standard Stack), `login.html` UI-SPEC already approved |
| AUTH-02 | Every user has the same "admin" role and can create additional user accounts | `POST /api/v1/auth/users` pattern requiring only "is logged in" (no role check — flat privilege model), D-06 |
| AUTH-03 | Session maintained via HTTP-only session cookie, valid for both REST and WebSocket | Cookie dependency pattern (REST) + `websocket.cookies` pattern (WS) in Architecture Patterns; cross-origin cookie pitfall documented (Common Pitfalls #1) |
| AUTH-04 | Existing chats/settings/memory scoped to owning user (`user_id`) | SQLite `ALTER TABLE ADD COLUMN` + FK constraint restriction (Common Pitfalls #3), migration sequencing (Architecture Patterns), bootstrap-admin-as-owner backfill (D-07) |
</phase_requirements>

## Summary

This phase adds the first authentication code to a codebase that currently has none. The user's `/bm:discuss-phase` session already locked nearly every architectural decision (DB-backed sessions, argon2id via pwdlib, bootstrap admin, separate login page, WS auth-at-accept) — this research focuses on the *mechanics* of implementing those decisions correctly inside this specific codebase's constraints, not on re-litigating alternatives.

Three findings materially change how the plan should be sequenced, beyond what CONTEXT.md already specifies:

1. **The bootstrap-admin credential print (D-05) cannot live inside `agent/main.py`'s lifespan/`init_db()` alone.** The Agent subprocess's stdout/stderr are redirected to `logs/agent.log` by `ui/supervisor.py::_launch_agent()` (`stdout=log_fd, stderr=log_fd`) — nothing the Agent process prints reaches the user's terminal. `CLAUDE.md`'s sanctioned `print()` exception is scoped specifically to `run.py`'s startup banner. The bootstrap-admin creation + one-time credential print must therefore run from `run.py` itself (before it spawns the UI server, which in turn spawns the Agent), using `shared/database.py`/`shared/auth.py` directly via `asyncio.run(...)`. The Agent's own `init_db()` still runs its normal migrations afterward and is a no-op for bootstrap (the user already exists).

2. **`user_id` cannot be added as a `NOT NULL` foreign-key column via SQLite `ALTER TABLE ADD COLUMN`** on the existing `chat`/`settings` tables while `PRAGMA foreign_keys=ON` is active — SQLite requires new FK-referencing columns added this way to default to `NULL`. The correct, idempotent migration (matching the existing `migrate_add_context_length` pattern) is: add `user_id` as a nullable FK column, backfill every existing row to the bootstrap admin's id (D-07), and enforce non-null at the application layer for all new writes (not at the DB schema level, unless a full 12-step table rebuild is explicitly wanted — out of scope for this MVP walking skeleton).

3. **The frontend's `apiFetch()` helper in `app.js` does not send cookies cross-origin today**, and it needs to. The UI (`:8000`) and Agent (`:8001`) are different origins (same host, different port), so browser `fetch()` omits cookies by default unless `credentials: 'include'` is set — this must be added to every Agent-bound `fetch()` call, or the session cookie set by `/api/v1/auth/login` will never be sent back on subsequent REST calls even though it round-trips fine for WebSocket (browsers attach cookies to WS handshakes automatically, with no `credentials`-style opt-in). The existing `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` in `agent/main.py` already supports this (Starlette reflects the request `Origin` instead of literal `*` when `allow_credentials=True` — verified against the installed `starlette==1.6.0` source), but this wildcard-with-credentials-reflected configuration is now a real credential-theft surface (previously there was no cookie to steal) and should be tightened to an explicit origin allowlist as part of this phase.

**Primary recommendation:** Hand-roll the auth layer exactly as CONTEXT.md specifies (no `fastapi-users`/`authlib`/JWT libraries) using `pwdlib[argon2]` for hashing and `secrets.token_urlsafe()` for session tokens; sequence bootstrap-admin creation in `run.py` (not the Agent lifespan) so its console print is actually visible; treat the `user_id` backfill as an additive nullable-column-plus-backfill migration, never an in-place `NOT NULL` `ALTER TABLE`; and add `credentials: 'include'` to `app.js`'s `apiFetch()` while tightening the Agent's CORS origin allowlist.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Login form render (`login.html`) | Browser / Client | — | Static HTML/JS served verbatim by `ui/main.py`'s `StaticFiles` mount; no server logic in the UI process |
| Credential verification & session issuance | API / Backend | — | `agent/main.py::POST /api/v1/auth/login` — password hash compare + `Session` row insert + `Set-Cookie` happen only in the Agent process, per CONTEXT.md specifics ("auth is entirely an Agent-process concern") |
| REST request authentication | API / Backend | — | `Depends()`-based cookie dependency added to every existing/new Agent route |
| WebSocket authentication | API / Backend | — | `agent/ws.py::ws_chat`, validated once at connection-accept, alongside `_validate_origin()` |
| Add-user account creation | API / Backend | Browser / Client (modal form) | `POST /api/v1/auth/users` does the write; UI is just the form (D-06) |
| `user_id` data scoping | Database / Storage | API / Backend | FK column + `WHERE user_id = :current_user` filters added to every query; DB enforces referential integrity, API enforces the filter |
| Bootstrap admin creation & one-time credential print | API / Backend (logic) | — but must be *invoked* from `run.py` | Logic lives in `shared/database.py`/`shared/auth.py` (importable by both processes); only `run.py`'s stdout is user-visible (see Summary finding #1) |
| Session expiry / sliding-window renewal | API / Backend | Database / Storage | `expires_at` column updated in the same DB write path as the auth dependency |
| Static file serving (`login.html`, `index.html`) | CDN / Static (functionally — `ui/main.py`'s `StaticFiles`) | — | No auth logic belongs here; confirmed by CONTEXT.md specifics |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pwdlib[argon2]` | 0.3.1 (latest on PyPI) [VERIFIED: official docs (frankie567.github.io/pwdlib) + PyPI registry] | Password hashing facade — `PasswordHash.recommended()` or explicit `Argon2Hasher()`, `.hash()`, `.verify()`, `.verify_and_update()` | Locked by D-11. Modern, actively maintained successor to `passlib` (unmaintained), built by the `fastapi-users` author specifically for this use case |
| `argon2-cffi` | 25.1.0 (latest on PyPI) [VERIFIED: official docs (argon2-cffi.readthedocs.io) + PyPI registry] | Underlying argon2id implementation, pulled in automatically as the `pwdlib[argon2]` extra | PHC (Password Hashing Competition) winner; OWASP's current recommended default for new applications; `argon2-cffi-bindings` (native wheel) installed cleanly on this Windows/Python 3.13.15 environment during verification |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `secrets` (stdlib) | builtin, Python 3.13 | Cryptographically secure random session token generation (`secrets.token_urlsafe(32)`) | Every new opaque session token — never `uuid.uuid4()` (documented as not intended for security tokens) or `random`/`os.urandom` directly |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `pwdlib` | `passlib` | `passlib` is effectively unmaintained (no active releases in years); `pwdlib` is its intended modern replacement and is what D-11 already locked in |
| DB-backed `Session` table (D-01) | Starlette `SessionMiddleware` (signed cookie) | Rejected explicitly — no server-side revocation (can't implement D-04's "logout really revokes"), and the whole session payload would live client-side |
| Hand-rolled login/session endpoints | `fastapi-users` library | Rejected — brings JWT/OAuth-flow-oriented abstractions and its own user model conventions that fight the codebase's flat "every user is admin," DB-session-only design; far heavier than the thin slice this walking-skeleton phase needs |
| App-layer `NOT NULL` enforcement on `user_id` | Full 12-step SQLite table rebuild to add a true `NOT NULL` FK column | Table rebuild is the "textbook correct" SQLite migration but is a large increase in blast radius (rewrite `chat`/`settings`/`message` tables) for a phase whose own framing calls for the thinnest correct slice — recommended only if the user later asks for stricter DB-level guarantees |

**Installation:**
```bash
pip install "pwdlib[argon2]"
```
This transitively installs `argon2-cffi` (and its native `argon2-cffi-bindings` wheel). No other new dependency is required — session tokens use the stdlib `secrets` module.

**Version verification:** Confirmed via `pip index versions pwdlib` → `0.3.1` and `pip index versions argon2-cffi` → `25.1.0`, both matching their respective official documentation sites, on 2026-09-19.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|--------------|-----------|-------------|
| `pwdlib` | PyPI | ~3 years (first released 2023, per project changelog) | Not queried numerically; widely referenced across FastAPI ecosystem docs/tutorials | `github.com/frankie567/pwdlib` | `[OK]` | Approved |
| `argon2-cffi` | PyPI | ~11 years (maintained by hynek since ~2015) | Not queried numerically; long-standing dependency of Django's recommended password hasher and many other frameworks | `github.com/hynek/argon2-cffi` | `[OK]` | Approved |

Both packages were installed and ran `slopcheck install pwdlib argon2-cffi` (v0.6.1) against the live PyPI registry during this research session; both returned `[OK]`. Both also have dedicated official documentation sites confirmed via `WebFetch` (not just registry presence), satisfying the stricter `[VERIFIED]` bar (official docs + slopcheck), not merely registry existence.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

No `postinstall`-equivalent script risk applies (Python packages don't run arbitrary `postinstall` scripts the way npm packages can); `argon2-cffi-bindings` compiles/installs a native extension via a prebuilt wheel on this platform (`argon2_cffi_bindings-26.1.0-cp310-abi3-win_amd64.whl`), confirmed during the verification install — no source compilation or network calls observed.

## Architecture Patterns

### System Architecture Diagram — Login & Session Issuance

```
Browser (login.html, served by UI :8000)
   │  POST /api/v1/auth/login  { username, password }
   │  fetch(..., { credentials: 'include' })   ← cross-origin, must opt in
   ▼
Agent process (:8001) — agent/main.py
   │
   ├─ look up User by username
   ├─ pwdlib PasswordHash.verify(password, user.password_hash)
   │     invalid → 401 { detail: "..." }
   ├─ valid → create Session row
   │     session_token = secrets.token_urlsafe(32)
   │     user_id, created_at, expires_at = now + 30d
   │     INSERT + commit
   ▼
Response: Set-Cookie: session_id=<token>; HttpOnly; SameSite=Lax; Path=/
   (Secure omitted — local http dev)
   │
   ▼
Browser stores cookie → redirects to index.html
```

### System Architecture Diagram — Authenticated Request (REST + WS)

```
Browser (index.html / app.js, served by UI :8000)
   │
   ├── REST: fetch(`${AGENT_BASE}/api/v1/chats`, { credentials: 'include' })
   │      cookie attached automatically (same-site, credentials:'include' opts into cross-origin send)
   │      ▼
   │   Agent :8001 — Depends(get_current_user)
   │      cookie missing/invalid/expired → 401
   │      valid → extend Session.expires_at (sliding window, D-02)
   │             → filter query WHERE user_id = current_user.id
   │      ▼
   │   200 { ...scoped data... }  OR  401 → app.js redirects to login.html?expired=1 (D-09)
   │
   └── WebSocket: new WebSocket(`${WS_BASE}/ws/chat/{chat_id}`)
          cookie sent automatically on the handshake (no credentials opt-in needed for WS)
          ▼
       agent/ws.py::ws_chat, BEFORE websocket.accept():
          1. _validate_origin(websocket)          (existing)
          2. look up session cookie → Session row → User   (new, D-10)
          3. verify chat.user_id == current_user.id          (new, AUTH-04)
          invalid at any step → websocket.close(code=1008, ...)  — never accept() first
          valid → accept(), extend Session.expires_at ONCE (not per message)
```

### Recommended Project Structure
```
shared/
├── models.py         # + User, Session tables
├── database.py       # + migrate_add_user_id_columns(), ensure_bootstrap_admin(), backfill_user_id()
└── auth.py           # NEW — hash_password(), verify_password(), generate_session_token()
                       #       (imported by both run.py's bootstrap flow and agent/main.py's login endpoint)

agent/
├── main.py           # + POST /api/v1/auth/login, /logout, GET /auth/me, POST /auth/users
├── dependencies.py    # NEW — get_current_user() (REST Depends), get_current_user_ws() (WS helper)
├── ws.py              # + session-cookie check before websocket.accept()
└── schemas.py         # + LoginRequest, UserResponse, CreateUserRequest

ui/static/
├── login.html          # NEW — per 01-UI-SPEC.md
└── app.js              # + credentials:'include' on apiFetch, 401→redirect, add-user modal, logout button

run.py                  # + asyncio.run(bootstrap_admin_if_needed()) BEFORE uvicorn.run(ui app)
```

### Pattern 1: DB-backed opaque session token
**What:** Cookie holds a high-entropy random string (`secrets.token_urlsafe(32)`), not a signed/encoded payload. The string is looked up directly against a unique-indexed `session_token` column on the `Session` table.
**When to use:** Whenever server-side revocation (D-04) and sliding expiry (D-02) are required — the opaque-token-plus-DB-row pattern is what makes both possible, unlike a self-contained signed cookie (JWT/`itsdangerous`) which cannot be revoked without an additional denylist.
**Example:**
```python
# shared/auth.py — Source: pwdlib official docs (https://frankie567.github.io/pwdlib/guide/), verified 2026-09-19
import secrets
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()  # argon2id by default

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    valid, _ = password_hash.verify_and_update(password, hashed)
    return valid

def generate_session_token() -> str:
    return secrets.token_urlsafe(32)
```

### Pattern 2: FastAPI cookie dependency for REST
**What:** A `Depends()`-injected function that reads the session cookie, resolves it to a `User`, extends `expires_at`, and raises `HTTPException(401)` on failure.
**When to use:** Attached to every REST endpoint under `/api/v1/*` except `POST /api/v1/auth/login`.
**Example:**
```python
# agent/dependencies.py — pattern synthesized from FastAPI Cookie-dependency docs
# and this codebase's existing get_session()/HTTPException conventions
from datetime import datetime, timedelta, timezone
from fastapi import Cookie, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from shared.database import get_session
from shared.models import Session as SessionRow, User

SESSION_COOKIE_NAME = "session_id"
SESSION_TTL_DAYS = 30

async def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
) -> User:
    if session_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    result = await db.exec(select(SessionRow).where(SessionRow.session_token == session_id))
    row = result.first()
    now = datetime.now(timezone.utc)
    if row is None or row.expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    row.expires_at = now + timedelta(days=SESSION_TTL_DAYS)  # D-02 sliding window
    db.add(row)
    await db.commit()
    user = await db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
```

### Pattern 3: WebSocket cookie check before `accept()`
**What:** `websocket.cookies` is populated from the handshake request headers and is readable before `await websocket.accept()` — exactly like `websocket.headers.get("origin")` is already used in `_validate_origin()`.
**When to use:** `agent/ws.py::ws_chat`, immediately after the existing origin check, before `accept()`.
**Example:**
```python
# agent/ws.py — pattern verified against FastAPI/Starlette WebSocket cookie-access behavior
# (websocket.cookies is available pre-accept; multiple community-verified sources, see Sources)
async def ws_chat(websocket: WebSocket, chat_id: int) -> None:
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
    user = await _resolve_ws_user(session_id)  # look up Session -> User, check expiry
    if user is None:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    # AUTH-04: verify chat ownership before accept
    if not await _user_owns_chat(user.id, chat_id):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    ...
```

### Pattern 4: Idempotent additive migration for `user_id` (mirrors existing `migrate_add_context_length`)
**What:** Add `user_id` as a nullable FK column via raw SQL (SQLite forbids `NOT NULL` on a new FK-referencing column added via `ALTER TABLE ADD COLUMN` while `PRAGMA foreign_keys=ON`), then backfill.
**When to use:** `shared/database.py::init_db()`, before `create_all()` runs (so it only fires against pre-existing installations; a brand-new DB gets the full schema — including `user_id` — directly from the `SQLModel` class definitions via `create_all()`).
**Example:**
```python
# shared/database.py — pattern matches the existing migrate_add_context_length() idempotency check
async def migrate_add_user_id_columns(conn: Any) -> None:
    """Add nullable user_id FK column to chat/settings when missing (idempotent)."""
    for table in ("chat", "settings"):
        table_check = await conn.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"),
        )
        if table_check.fetchone() is None:
            continue
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        columns = [row[1] for row in result.fetchall()]
        if "user_id" not in columns:
            logger.info("migrating_add_user_id", table=table)
            # NOTE: must be nullable — SQLite forbids NOT NULL on a column added
            # this way while a REFERENCES clause is present and foreign_keys=ON.
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES user(id)"),
            )
```

### Anti-Patterns to Avoid
- **Printing bootstrap credentials from inside `agent/main.py`'s lifespan:** the Agent subprocess's stdout is redirected to `logs/agent.log` by `ui/supervisor.py` — the user will never see it. Print only from `run.py`.
- **Adding `user_id NOT NULL` directly via `ALTER TABLE ... ADD COLUMN`:** SQLite will refuse this combination while FK enforcement is on and the column has a `REFERENCES` clause; add nullable, backfill, enforce at the app layer.
- **Relying on `SameSite=Lax` alone for cross-origin `fetch()` cookie delivery:** `SameSite` governs whether a cookie set on one site is *eligible* to be sent; it does not bypass the separate `credentials: 'include'` requirement that `fetch()`/XHR impose for cross-origin requests. Both are needed.
- **Re-validating the session on every WebSocket message:** explicitly rejected by D-10 — validate once at `accept()`-time only.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Password hashing | Custom PBKDF2/SHA-256+salt scheme | `pwdlib[argon2]` (`PasswordHash.recommended()`) | Argon2id parameter tuning (memory/time cost), timing-safe comparison, and future algorithm migration (`verify_and_update`) are already solved; hand-rolled hashing is a classic source of real-world breaches |
| Secure random token generation | `uuid.uuid4()` or `random.random()`-based tokens | `secrets.token_urlsafe(32)` | `secrets` is explicitly documented as the CSPRNG-backed module for security tokens; `uuid4`/`random` are not guaranteed unpredictable in the same way |
| Cookie attribute serialization | Manually building `Set-Cookie` header strings | `Response.set_cookie(...)` / `JSONResponse(...).set_cookie(...)` (Starlette/FastAPI) | Correct attribute quoting/escaping and cross-browser edge cases are already handled |

**Key insight:** Everything else in this phase (session table design, cookie dependency wiring, migration sequencing) is intentionally hand-rolled per the locked decisions — the *only* things to reach for a library for are the cryptographic primitives (hashing, random tokens) and the framework's own cookie-response API.

## Common Pitfalls

### Pitfall 1: Cross-origin `fetch()` silently drops the session cookie
**What goes wrong:** Login appears to succeed (cookie is set), but every subsequent `apiFetch()` call in `app.js` gets a 401 as if no cookie exists.
**Why it happens:** `AGENT_BASE` (`http://<host>:8001`) is a different origin than the page served from `:8000`. Browsers omit cookies on cross-origin `fetch()`/XHR requests unless `credentials: 'include'` is explicitly set — `app.js`'s current `apiFetch()` (line 47-59) does not set this.
**How to avoid:** Add `credentials: 'include'` to the `fetch()` call inside `apiFetch()` (and to the raw `fetch()` call used for login, before `apiFetch` even applies).
**Warning signs:** Login POST returns 200 with a `Set-Cookie` header (visible in DevTools Network tab), but the very next `GET /api/v1/chats` returns 401 with no `Cookie` header on the request.

### Pitfall 2: Wildcard CORS + credentials becomes a real credential-theft surface once cookies carry auth
**What goes wrong:** `agent/main.py`'s `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` is currently harmless (nothing sensitive to steal), but Starlette's `CORSMiddleware` — confirmed by reading the installed `starlette==1.6.0` source (`starlette/middleware/cors.py`) — reflects the request's `Origin` header back verbatim instead of literal `*` whenever `allow_credentials=True`. That means *any* site making a credentialed cross-origin request to the Agent gets an origin-matching CORS response, once the response includes a real session cookie.
**Why it happens:** This is Starlette's documented workaround for `allow_origins=["*"]` + `allow_credentials=True` (browsers refuse to honor literal `*` with credentialed requests, so Starlette reflects the caller's `Origin` instead) — it is a widely-flagged footgun across several projects' security advisories.
**How to avoid:** Replace `allow_origins=["*"]` with an explicit allowlist — the codebase already defines exactly this list in `agent/state.py::CORS_ORIGINS` (`http://localhost:8000`, `http://127.0.0.1:8000`) for the WS origin check; reuse it for the REST `CORSMiddleware` too.
**Warning signs:** none until the phase's own security review or a `code-review` pass flags it — this is a "quietly correct-looking config that becomes wrong" pitfall, not a runtime error.

### Pitfall 3: SQLite forbids `NOT NULL` on a new FK column added via `ALTER TABLE ADD COLUMN`
**What goes wrong:** A naive migration (`ALTER TABLE chat ADD COLUMN user_id INTEGER NOT NULL REFERENCES user(id)`) fails outright on any existing `app.db`/`test_app.db` file, because SQLite requires a column added this way, when it carries a `REFERENCES` clause, to default to `NULL`.
**Why it happens:** SQLite's `ALTER TABLE` documentation explicitly restricts `ADD COLUMN` + `NOT NULL` to columns with a non-NULL `DEFAULT`, which is incompatible with a `REFERENCES` clause whose valid value (the bootstrap admin's id) isn't known until the migration runs.
**How to avoid:** Add the column nullable, backfill every existing row to the bootstrap admin's `id` (D-07) in the same migration step, and enforce non-null only at the SQLModel/application layer for all future writes. A true DB-level `NOT NULL` requires SQLite's 12-step table-rebuild procedure (rename → recreate → copy → drop → rename) — explicitly out of scope for this MVP walking-skeleton phase.
**Warning signs:** `sqlite3.OperationalError: Cannot add a NOT NULL column with default value NULL` (or the aiosqlite-wrapped equivalent) on first startup against a pre-existing `app.db`.

### Pitfall 4: Bootstrap-admin credentials print to a log file the user never sees
**What goes wrong:** D-05's "print once to console" requirement silently fails to reach the user if the bootstrap logic is placed inside `agent/main.py`'s `lifespan()`/`init_db()` call path.
**Why it happens:** `ui/supervisor.py::_launch_agent()` spawns the Agent subprocess with `stdout=log_fd, stderr=log_fd` where `log_fd` is a file handle to `logs/agent.log` — the Agent process's stdout is redirected away from the terminal entirely, and `CLAUDE.md`'s sanctioned `print()` exception is scoped specifically to `run.py`.
**How to avoid:** Trigger bootstrap-admin creation (and the one-time print) from `run.py::main()`, via `asyncio.run(...)`, before `uvicorn.run(ui_app, ...)` is called — not from the Agent's lifespan.
**Warning signs:** Bootstrap admin row exists in the DB, login page works with generated credentials found only by manually reading `logs/agent.log`, but nothing appeared in the terminal on first run.

### Pitfall 5: Existing REST tests will break the moment auth is enforced
**What goes wrong:** `tests/conftest.py`'s `client` fixture wires an unauthenticated `AsyncClient` against `agent/main.py::app`. Every existing test file (`test_cascade_delete.py`, `test_settings_fallback.py`, `test_stats.py`, `test_strategies.py`, etc.) calls REST endpoints directly with no login step — once `Depends(get_current_user)` is added to those endpoints, all of them start failing with 401.
**Why it happens:** Auth is being retrofitted onto a codebase whose entire existing test suite predates it.
**How to avoid:** Plan a new `authenticated_client` fixture (or update `client` itself) early in the phase's task sequence — e.g., seed a test user + valid `Session` row and set the cookie on the `AsyncClient`'s default headers/cookie jar — before/alongside adding the `Depends()` calls, so the plan doesn't leave existing tests red until a later cleanup pass.
**Warning signs:** `pytest tests/ -v` goes from green to mass-401-failures immediately after wiring the auth dependency into `agent/main.py`.

## Code Examples

### Login endpoint (REST)
```python
# agent/main.py — pattern follows existing commit/rollback convention (see update_settings)
@app.post("/api/v1/auth/login")
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    result = await session.exec(select(User).where(User.username == body.username))
    user = result.first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    token = generate_session_token()
    session_row = SessionRow(
        session_token=token,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS),
    )
    session.add(session_row)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        # secure=False intentionally omitted (local http dev per Claude's Discretion)
        path="/",
    )
    return UserResponse(id=user.id, username=user.username)
```

### `run.py` bootstrap invocation (must run here, not in the Agent process)
```python
# run.py — Source: reasoned from CLAUDE.md's print()-exception scoping
# and ui/supervisor.py's stdout redirection (see Pitfall 4)
import asyncio
from shared.database import bootstrap_admin_if_needed  # new function

def main() -> None:
    cleanup_port(settings.UI_PORT)
    cleanup_port(settings.AGENT_PORT)

    credentials = asyncio.run(bootstrap_admin_if_needed())
    if credentials is not None:
        username, password = credentials
        print("=" * 60)
        print("Bootstrap admin account created:")
        print(f"  username: {username}")
        print(f"  password: {password}")
        print("Save this now — it will not be shown again.")
        print("=" * 60)

    from ui.main import app
    ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| bcrypt / PBKDF2 as the default recommended password hash | argon2id (Argon2 PHC-winner, memory-hard) | OWASP's Password Storage Cheat Sheet has recommended Argon2id as the first choice for several years now | Already reflected in D-11's locked choice; no action needed beyond following it |
| `passlib` as the go-to Python hashing wrapper | `pwdlib` | `passlib` has had no meaningful releases in years; `pwdlib` (2023+) is its actively maintained, type-hinted, async-friendly-ecosystem successor | Confirms D-11's package choice is current best practice, not just a locked-in preference |

**Deprecated/outdated:** none directly relevant beyond the `passlib`→`pwdlib` shift already noted above.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `pwdlib`'s age is "~3 years" and its exact weekly download count | Package Legitimacy Audit | Low — package identity/existence is independently verified via official docs + slopcheck; only the qualitative age/popularity framing is approximate (no download-count API was queried) |
| A2 | Exact FastAPI/Starlette WebSocket `cookies` pre-`accept()` behavior generalizes correctly to the installed `starlette==1.6.0` | Architecture Patterns (Pattern 3) | Low-Medium — behavior is corroborated by multiple independent sources (official FastAPI docs page + community discussions) but was not directly exercised against this exact installed version in this session; verify with a small manual test during planning/execution if time allows |

**If this table is empty:** N/A — two low-risk items logged above; neither blocks planning.

## Open Questions (RESOLVED)

1. **Should the raw session token be hashed before storage in the `Session` table, or stored as plaintext (as sketched in the Code Examples)?**
   - What we know: D-01 only specifies "an opaque random token in the HTTP-only cookie maps to a session row" — it does not mandate hashing at rest. Storing the raw high-entropy token indexed is a common, acceptable pattern (comparable to many frameworks' default session-store behavior) and is what the Code Examples above show for simplicity.
   - What's unclear: Whether the user wants the extra defense-in-depth of hashing tokens at rest (mirrors password-reset-token best practice) given this is coursework in a trusted, local-only environment.
   - Recommendation: Ship the simpler plaintext-token-in-DB approach for this MVP phase (matches "thinnest possible correct slice" framing); note hashing-at-rest as a fast-follow hardening option, not a blocker.
   - **RESOLVED (during planning, in `01-01-PLAN.md` Task 2 "SESSION-TOKEN-AT-REST DECISION"):** hash at rest. The DB stores only `sha256(token)` in `Session.token_hash`; the raw `secrets.token_urlsafe(32)` value exists solely in the HTTP-only cookie and is never persisted or logged. This overrides the plaintext-token sketch in the Code Examples above. Plain SHA-256 (not argon2) is correct here because the token already carries 256 bits of entropy, so it needs no key-stretching, and lookups stay a single indexed equality query. Tracked as threat T-01-06 in `01-01-PLAN.md`'s STRIDE register.

2. **Exact cookie name and header casing for the WS-side lookup.**
   - What we know: REST and WS must share the same cookie (D-10 says "same origin as `_validate_origin()`"), so whatever name is chosen for `Set-Cookie` in the login endpoint must match what `websocket.cookies.get(...)` looks up.
   - What's unclear: Nothing blocking — this is a naming decision for planning (`session_id` used consistently throughout this research is a reasonable default).
   - Recommendation: Planner should pick one constant (e.g., `SESSION_COOKIE_NAME = "session_id"`) defined once in `shared/auth.py` or `agent/dependencies.py` and imported everywhere it's needed, to avoid a naming mismatch between the login endpoint and the WS/REST auth dependencies.
   - **RESOLVED (during planning, in `01-01-PLAN.md`):** a single shared constant `SESSION_COOKIE_NAME = "session_id"` is defined once in `shared/auth.py` and imported everywhere it is needed — `agent/dependencies.py` (`Cookie(alias=SESSION_COOKIE_NAME)` for both `get_current_user` and `get_current_user_ws`), `agent/main.py` (`response.set_cookie` / `response.delete_cookie`), `ui/static/login.html`, and `agent/ws.py`'s handshake cookie lookup. No literal cookie-name string is duplicated anywhere, so a REST/WS naming mismatch is structurally impossible.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Python | Runtime | ✓ | 3.13.15 | — |
| pip | Package install | ✓ | present (used throughout this session) | — |
| `pwdlib[argon2]` | Password hashing | ✓ (installable, verified this session) | 0.3.1 / argon2-cffi 25.1.0 | — |
| SQLite3 | Database | ✓ (bundled with Python; existing `app.db`/WAL mode already in use) | — | — |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** none — all required tooling is already present or installs cleanly.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|----------------|---------|--------------------|
| V2 Authentication | yes | `pwdlib[argon2]` (argon2id) for password storage; no password policy per D-12 (explicit, documented user deviation — not a research gap) |
| V3 Session Management | yes | DB-backed opaque session token (D-01), `HttpOnly`+`SameSite=Lax` cookie, 30-day sliding expiry (D-02), real server-side revocation on logout (D-04) |
| V4 Access Control | yes (flat model) | Every authenticated user has equal capability (AUTH-02, by design — no RBAC per Out of Scope in REQUIREMENTS.md); access control here means *authentication presence* + *row ownership* (`user_id` scoping), not role checks |
| V5 Input Validation | yes | Existing Pydantic/SQLModel schema validation patterns extend to new `LoginRequest`/`CreateUserRequest` schemas (max-length fields, matching `agent/schemas.py` conventions) |
| V6 Cryptography | yes | Password hashing via `pwdlib`/`argon2-cffi` only — never hand-rolled (see Don't Hand-Roll); session tokens via `secrets.token_urlsafe()` only |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| Session fixation / prediction | Spoofing | High-entropy (`secrets.token_urlsafe(32)` = 256 bits) random token, freshly generated per login — never derived from user input or predictable state |
| Cross-origin credential leakage via permissive CORS + credentials | Information Disclosure / Spoofing | Replace `allow_origins=["*"]` with the explicit allowlist already defined in `agent/state.py::CORS_ORIGINS` (see Pitfall 2) |
| Cross-site request forgery on state-changing endpoints (login, add-user, logout) | Tampering | `SameSite=Lax` cookie (blocks cross-site POST from being credentialed in most browsers) — acceptable given CONTEXT.md's explicit choice of `Lax` over `Strict`/CSRF-token machinery for this low-stakes coursework scope; not upgraded further without user direction |
| IDOR — one user reading/modifying another user's chat via `chat_id` in the URL | Tampering / Information Disclosure | Every REST/WS handler must check `chat.user_id == current_user.id` (extending `_get_chat_or_404`-style helpers), returning 404 (not 403) to avoid confirming another user's chat IDs exist — mirrors this codebase's existing 404-on-missing convention |
| Timing side-channel on username enumeration at login | Information Disclosure | Return the same generic `"Invalid username or password"` message (already specified verbatim in `01-UI-SPEC.md`'s Copywriting Contract) regardless of whether the username or the password was wrong |

## Sources

### Primary (HIGH confidence)
- `starlette/middleware/cors.py` (installed `starlette==1.6.0`, read directly from `C:\Python\Python313\Lib\site-packages\`) — confirmed wildcard-origin + credentials reflection behavior
- [pwdlib official guide](https://frankie567.github.io/pwdlib/guide/) — `PasswordHash.recommended()`, `.hash()`, `.verify()`, `.verify_and_update()` API
- [argon2-cffi official documentation](https://argon2-cffi.readthedocs.io/en/stable/) — confirmed official docs, basic hashing API
- `pip index versions pwdlib` / `pip index versions argon2-cffi` (PyPI registry, run 2026-09-19) — 0.3.1 / 25.1.0
- `slopcheck install pwdlib argon2-cffi` (v0.6.1) — both `[OK]`
- Direct codebase reads: `shared/models.py`, `shared/database.py`, `agent/main.py`, `agent/ws.py`, `agent/state.py`, `agent/schemas.py`, `ui/main.py`, `ui/supervisor.py`, `run.py`, `shared/config.py`, `ui/static/app.js`, `tests/conftest.py`

### Secondary (MEDIUM confidence)
- [pwdlib GitHub repo](https://github.com/frankie567/pwdlib) — general project framing
- WebSearch results cross-referencing FastAPI cookie-dependency and WebSocket-cookie-access patterns (multiple independent sources: FastAPI official docs `fastapi.tiangolo.com/advanced/websockets/`, FastAPI GitHub discussions #10658)
- SQLite `ALTER TABLE`/foreign-key restriction findings, cross-referenced across sqlite.org's own `lang_altertable.html` semantics and multiple independent tutorial/SO-style sources agreeing on the same NOT NULL + REFERENCES restriction

### Tertiary (LOW confidence)
- Qualitative "age"/"popularity" framing for `pwdlib`/`argon2-cffi` in the Package Legitimacy Audit table (no download-count API was queried this session)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — package identities and APIs verified via official docs + registry + slopcheck, matching the already-locked CONTEXT.md decisions
- Architecture: HIGH — session/cookie/migration patterns verified against this codebase's actual installed `starlette` source and actual file contents (not assumed), plus the two non-obvious findings (log-file-redirected stdout, SQLite ALTER TABLE FK restriction) confirmed via direct inspection
- Pitfalls: HIGH — all five pitfalls trace to concrete, inspected artifacts in this repo (supervisor.py's `stdout=log_fd`, app.js's missing `credentials:'include'`, installed starlette's CORS reflection behavior, SQLite's documented ALTER TABLE restriction, conftest.py's unauthenticated client fixture) rather than generic domain knowledge

**Research date:** 2026-09-19
**Valid until:** 30 days (stable domain — FastAPI/SQLModel/pwdlib/argon2-cffi APIs are not fast-moving; codebase-specific findings, e.g. supervisor.py's stdout redirection, only go stale if that file changes)
