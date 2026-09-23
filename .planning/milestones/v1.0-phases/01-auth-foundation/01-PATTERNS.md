# Phase 1: Auth Foundation - Pattern Map

**Mapped:** 2026-09-19
**Files analyzed:** 16
**Analogs found:** 13 / 16 (3 marked No Analog — new crypto/dependency/schema concepts with no prior precedent in this codebase; RESEARCH.md Code Examples used instead)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `shared/models.py` (modify: + `User`, `Session`) | model | CRUD | `shared/models.py` (self — `Chat`/`Settings` FK convention) | exact |
| `shared/database.py` (modify: + migrations, bootstrap) | migration/service | batch | `shared/database.py` (self — `migrate_add_context_length`) | exact |
| `shared/auth.py` (NEW) | utility | transform | none in-repo; use RESEARCH.md Pattern 1 verbatim | no analog |
| `agent/dependencies.py` (NEW) | middleware | request-response | `agent/main.py` (`Depends(get_session)` usage + `_get_chat_or_404` guard-clause style) | role-match |
| `agent/schemas.py` (modify: + `LoginRequest`, `UserResponse`, `CreateUserRequest`) | model (schema) | transform | `agent/schemas.py` (self — existing `BaseModel`/`Field(max_length=...)` conventions) | exact |
| `agent/main.py` (modify: + auth routes, `Depends()` on existing routes, CORS tightening) | controller | request-response | `agent/main.py` (self — `create_chat`/`delete_chat`/`update_settings` commit/rollback pattern) | exact |
| `agent/ws.py` (modify: + cookie check before `accept()`) | middleware / controller | event-driven (WS) | `agent/ws.py` (self — `_validate_origin()` pre-accept gate) | exact |
| `agent/state.py` (reference only, `CORS_ORIGINS`) | config | — | `agent/state.py` (self) | exact |
| `run.py` (modify: + bootstrap-admin invocation/print) | config / entrypoint | batch | `run.py` (self — `cleanup_port()` + startup `print()` banner) | exact |
| `requirements.txt` (modify: + `pwdlib[argon2]`) | config | — | `requirements.txt` (self — existing pinned-version block style) | exact |
| `ui/static/login.html` (NEW) | component | request-response | `ui/static/index.html` (`#settings-modal` card markup, form input styling) | role-match |
| `ui/static/app.js` (modify: `credentials:'include'`, 401 redirect, add-user modal, logout) | component / hook | request-response | `ui/static/app.js` (self — `apiFetch()`, `showToast()`, settings-modal open/close handlers) | exact |
| `ui/static/index.html` (modify: + logout button, "Users"/add-user trigger) | component | request-response | `ui/static/index.html` (self — `#btn-settings` header button, `#agent-status` sidebar footer, `#settings-modal`) | exact |
| `tests/conftest.py` (modify: + `authenticated_client` fixture) | test | request-response | `tests/conftest.py` (self — `client` fixture) | exact |
| `tests/test_auth.py` (NEW) | test | request-response | `tests/test_settings_fallback.py` (REST assertion style) | role-match |
| `tests/test_ws_auth.py` (NEW, optional) | test | event-driven (WS) | `tests/test_ws_origin_validation.py` (`TestClient.websocket_connect` pre-accept rejection pattern) | role-match |

## Pattern Assignments

### `shared/models.py` — add `User`, `Session` tables (model, CRUD)

**Analog:** `shared/models.py` (self), lines 65-77 (`Settings` table FK convention)

**FK-cascade convention to copy exactly** (lines 68-77):
```python
class Settings(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            unique=True,
            nullable=True,
        ),
    )
```
Apply the same `sa_column=Column(Integer, ForeignKey(..., ondelete="CASCADE"), nullable=...)` shape for `Session.user_id -> user.id` (CASCADE — deleting a user should delete their sessions) and for the new nullable `Chat.user_id` / `Settings.user_id` columns discussed below. **Never** use `Field(ondelete=...)` — CLAUDE.md and this file both confirm it is silently ignored by SQLModel.

Timestamp convention to copy (lines 33-35, `Chat.created_at`):
```python
created_at: datetime = Field(
    default_factory=lambda: datetime.now(timezone.utc),
)
```
Use this exact `default_factory` for `User.created_at`, `Session.created_at`, and `Session.expires_at` (with `+ timedelta(days=30)` computed at insert time in the login endpoint, not as a field default).

Suggested new tables (field set is Claude's Discretion per CONTEXT.md, but shape must match the file's existing style):
```python
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_token: str = Field(unique=True, index=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
```
`Chat.user_id` / `Settings.user_id` should be added as **nullable** `Optional[int]` FK fields (never `nullable=False` — see the migration pitfall in `shared/database.py` below); application code enforces non-null on all new writes.

---

### `shared/database.py` — add `migrate_add_user_id_columns()`, `ensure_bootstrap_admin()`/`backfill_user_id()` (migration, batch)

**Analog:** `shared/database.py` (self), lines 73-100 (`migrate_add_context_length` + `init_db` sequencing)

**Idempotent additive-migration pattern to copy exactly** (lines 73-92):
```python
async def migrate_add_context_length(conn: Any) -> None:
    """Add context_length column to settings when missing (idempotent)."""
    table_check = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='settings'",
        ),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(settings)"))
    columns = [row[1] for row in result.fetchall()]
    if "context_length" not in columns:
        logger.info("migrating_settings_add_context_length")
        await conn.execute(
            text(
                "ALTER TABLE settings ADD COLUMN context_length INTEGER DEFAULT 4096",
            ),
        )
```
Mirror this shape exactly for `migrate_add_user_id_columns(conn)` — table-exists check, `PRAGMA table_info`, add-if-missing — but the new column **must** be added nullable (`ALTER TABLE chat ADD COLUMN user_id INTEGER REFERENCES user(id)`, no `NOT NULL`), per RESEARCH.md Pitfall 3 (SQLite forbids `NOT NULL` on a new FK-referencing column added via `ALTER TABLE ADD COLUMN`).

**`init_db()` sequencing to extend** (lines 95-100):
```python
async def init_db() -> None:
    """Create all database tables if they do not exist."""
    async with engine.begin() as conn:
        await migrate_add_context_length(conn)
        await conn.run_sync(SQLModel.metadata.create_all)
        await _migrate_legacy_strategies(conn)
```
Add `migrate_add_user_id_columns(conn)` before `create_all`, and a backfill step (`UPDATE chat SET user_id = :admin_id WHERE user_id IS NULL`) after `create_all` (needs the bootstrap admin's id to exist first — sequence: migrate columns → create_all → ensure bootstrap admin exists → backfill nulls). Note per RESEARCH.md Finding #1: the bootstrap-admin **creation + credential print** itself must be *invoked* from `run.py`, not from `agent/main.py`'s `lifespan()` — but the underlying `ensure_bootstrap_admin()` function should still live here in `shared/database.py` (importable by both processes), following the `_ensure_global_settings()` "create if missing" pattern from `agent/main.py` lines 37-49 as its structural analog (get-or-create, `session.add` + `commit` + `refresh`).

**Logging convention** (`logger.info("migrating_settings_add_context_length")`, `key=value` structlog pairs) applies to all new migration log lines, e.g. `logger.info("migrating_add_user_id", table=table)`.

---

### `shared/auth.py` (NEW) — password hashing + session token generation (utility, transform)

**No in-repo analog** — this is the first cryptographic-utility module in the codebase. Use RESEARCH.md's Pattern 1 code example directly (it is already verified against the official `pwdlib` docs):
```python
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
Apply this codebase's conventions on top: module docstring (single line), full type hints on all params/returns, `logger = get_logger(__name__)` if any logging is added (avoid logging the password/token values themselves — see CLAUDE.md Logging: never log secrets). Define `SESSION_COOKIE_NAME = "session_id"` and `SESSION_TTL_DAYS = 30` here as the single source of truth (RESEARCH.md Open Question #2) — import from both `agent/dependencies.py` and `agent/main.py`'s login endpoint to avoid a naming mismatch.

---

### `agent/dependencies.py` (NEW) — `get_current_user()` (REST), `get_current_user_ws()` (WS) (middleware, request-response)

**Analog:** `agent/main.py`, lines 192-200 (`Depends(get_session)` usage) + lines 52-63 (`_get_chat_or_404` guard-clause/`HTTPException` style)

**`Depends()` injection pattern to copy** (lines 192-200):
```python
@app.get("/api/v1/chats", response_model=list[ChatResponse])
async def list_chats(
    session: AsyncSession = Depends(get_session),
) -> list[ChatResponse]:
```

**Guard-clause + `HTTPException` pattern to copy** (lines 52-63, `_get_chat_or_404`):
```python
async def _get_chat_or_404(
    session: AsyncSession,
    chat_id: int,
) -> Chat:
    """Load a chat by id or raise HTTP 404."""
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat {chat_id} not found",
        )
    return chat
```
Use the same shape (return-early guard clause, explicit `status_code=status.HTTP_401_UNAUTHORIZED`, `detail="..."`) for `get_current_user()`. RESEARCH.md's Pattern 2 code example already combines this with the `Cookie(default=None, alias=SESSION_COOKIE_NAME)` dependency and the sliding-expiry update (D-02) — use it as-is, importing `SESSION_COOKIE_NAME`/`SESSION_TTL_DAYS` from `shared/auth.py` per the note above. Follow this codebase's commit/rollback convention (`await session.commit()` after the `expires_at` update; catch and `await session.rollback()` on exception, matching `agent/main.py::update_settings` lines 319-324).

For `get_current_user_ws()` (used by `agent/ws.py`, not a FastAPI `Depends()` since it runs pre-`accept()`): same lookup logic, but returns `User | None` instead of raising, so the caller can `websocket.close(code=1008, ...)` — mirror `agent/ws.py::_validate_origin()`'s `bool`-return, no-raise style (see below).

---

### `agent/schemas.py` — add `LoginRequest`, `UserResponse`, `CreateUserRequest` (model/schema, transform)

**Analog:** `agent/schemas.py` (self), lines 25-28 (`ChatCreate`) and lines 111-115 (`MessagePayload`)

**Pattern to copy** (constants block, lines 11-16, and a request schema, lines 111-115):
```python
TITLE_MAX_LENGTH = 200
...

class MessagePayload(BaseModel):
    """WebSocket inbound chat message."""

    content: str = Field(min_length=1, max_length=CONTENT_MAX_LENGTH)
    model: str = Field(min_length=1, max_length=200)
```
Add `USERNAME_MAX_LENGTH`/`PASSWORD_MAX_LENGTH`-style constants at the top alongside the existing ones, then:
```python
class LoginRequest(BaseModel):
    """Request body for username/password login."""

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class CreateUserRequest(BaseModel):
    """Request body for creating an additional user account (D-06)."""

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserResponse(BaseModel):
    """Serialized user (never includes password_hash)."""

    id: int
    username: str = Field(max_length=USERNAME_MAX_LENGTH)
```
Note D-12 (no password policy beyond non-empty) — `min_length=1` is the only password constraint; do not add a regex/complexity validator.

---

### `agent/main.py` — auth routes + `Depends()` on existing routes + CORS tightening (controller, request-response)

**Analog:** `agent/main.py` (self), lines 264-286 (`branch_chat` mutation) and lines 298-325 (`update_settings` commit/rollback + get-or-create)

**Commit/rollback + get-or-create pattern to copy** (lines 298-325, `update_settings`):
```python
@app.put("/api/v1/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    ...
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    return _settings_to_response(row)
```
Use this exact `try: commit + refresh / except: rollback + raise` shape for the login endpoint's `Session` row insert (RESEARCH.md's login-endpoint Code Example already follows it) and for `POST /api/v1/auth/users`.

**Route registration style to copy** (lines 203-217, `create_chat`):
```python
@app.post(
    "/api/v1/chats",
    response_model=ChatResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat(
    body: ChatCreate,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
```
New routes to add, following this shape: `POST /api/v1/auth/login` (no `Depends(get_current_user)` — this is the one open endpoint), `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `POST /api/v1/auth/users`.

**Adding auth to existing routes:** every existing `Depends(get_session)` parameter list gains a sibling `current_user: User = Depends(get_current_user)` (from the new `agent/dependencies.py`), e.g.:
```python
async def list_chats(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ChatResponse]:
    result = await session.exec(
        select(Chat).where(Chat.user_id == current_user.id).order_by(Chat.created_at.desc()),
    )
```
`_get_chat_or_404` (lines 52-63) should also gain a `current_user_id: int` parameter and check `chat.user_id != current_user_id` → 404 (not 403, per RESEARCH.md's IDOR mitigation table — "never confirm another user's chat IDs exist").

**CORS tightening** (lines 162-168, currently):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
Replace `allow_origins=["*"]` with `allow_origins=CORS_ORIGINS` imported from `agent/state.py` (already defines `["http://localhost:8000", "http://127.0.0.1:8000"]`) — per RESEARCH.md Pitfall 2, this is required once cookies carry real auth (Starlette reflects `Origin` verbatim when `allow_credentials=True` + wildcard).

---

### `agent/ws.py` — session-cookie check before `accept()` (middleware/controller, event-driven WS)

**Analog:** `agent/ws.py` (self), lines 40-68 (`_validate_origin`) and lines 259-266 (`ws_chat`'s pre-accept gate sequencing)

**Pre-accept gate pattern to copy exactly** (lines 259-266):
```python
async def ws_chat(websocket: WebSocket, chat_id: int) -> None:
    """Handle streaming chat over WebSocket with security guards."""
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()
```
Insert the new session-cookie + chat-ownership check between the origin check and `accept()`, following RESEARCH.md's Pattern 3 exactly:
```python
session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
user = await get_current_user_ws(session_id)
if user is None:
    await websocket.close(code=1008, reason="Unauthorized")
    return

if not await _user_owns_chat(user.id, chat_id):
    await websocket.close(code=1008, reason="Unauthorized")
    return

await websocket.accept()
```
`_validate_origin`'s no-raise/`bool`-return + `logger.info("ws_origin_check", ...)`/`logger.warning("ws_origin_rejected", ...)` structlog style (lines 40-68) is the template for the new check's logging (e.g. `logger.warning("ws_auth_rejected", chat_id=chat_id, reason=...)`). Per D-10, this validation happens **once** here — do not add any re-check inside the `while True` message loop (lines 268-298).

---

### `run.py` — bootstrap-admin invocation + credential print (entrypoint, batch)

**Analog:** `run.py` (self), lines 36-57 (`main()`'s existing `print()` banner + `cleanup_port` sequencing)

**Sanctioned `print()` banner pattern to copy exactly** (lines 43-45):
```python
print(f"Starting UI on http://127.0.0.1:{settings.UI_PORT}")
print(f"Agent will be auto-started on port {settings.AGENT_PORT}")
print("Press Ctrl+C to stop.")
```
Insert `asyncio.run(bootstrap_admin_if_needed())` immediately after the two `cleanup_port()` calls (line 39) and before the existing startup prints, per RESEARCH.md's `run.py` Code Example and Pitfall 4 (the Agent subprocess's stdout is redirected to `logs/agent.log` by `ui/supervisor.py::_launch_agent()` — this print must happen in `run.py`, never in `agent/main.py`'s lifespan). Match the existing banner's plain multi-line `print()` style (no f-string interpolation tricks beyond what's already used) and the `"=" * 60` separator convention shown in RESEARCH.md's example for visual distinction from the routine startup lines.

---

### `requirements.txt` — add `pwdlib[argon2]`

**Analog:** `requirements.txt` (self), lines 7-11 (`DATABASE & ORM` section header/comment-block style)
```
# ==============================================================================
# DATABASE & ORM
# ==============================================================================
sqlmodel>=0.0.22          # Включает в себя SQLAlchemy 2.x и Pydantic v2
aiosqlite>=0.20.0         # Асинхронный драйвер для SQLite (критически важен)
```
Add a new `# AUTHENTICATION` section header (matching the existing `# ===...===` fencing and Russian inline comment style) with `pwdlib[argon2]>=0.3.1`.

---

### `ui/static/login.html` (NEW) — standalone login page (component, request-response)

**Analog:** `ui/static/index.html`, lines 118-189 (`#settings-modal` card markup) and lines 104-111 (form input/button classes)

**Card + form styling to copy** (lines 118-124, 172-186):
```html
<div id="settings-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60">
    <div class="bg-slate-900 border border-slate-700 rounded-xl shadow-xl w-full max-w-lg mx-4">
        <div class="flex items-center justify-between px-5 py-4 border-b border-slate-700">
            <h2 class="text-lg font-semibold">Настройки</h2>
            <button id="btn-close-settings" class="text-slate-400 hover:text-white text-xl leading-none">&times;</button>
        </div>
        <form id="settings-form" class="p-5 space-y-4">
```
Per `01-UI-SPEC.md`'s Component Notes: `login.html` reuses this exact card treatment but as a full standalone page (`bg-slate-950` body, centered `max-w-sm` card instead of a modal overlay), inputs styled like `#settings-system-prompt` (line 174: `class="w-full resize-none rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm"`), submit button styled like the existing primary CTA (line 42-44, `#btn-new-chat`: `bg-indigo-600 hover:bg-indigo-500 px-4 py-2 text-sm font-medium`). Session-expired banner uses the `warning` toast palette (`bg-yellow-900/90 border-yellow-700`, from `app.js` line 39) per UI-SPEC.

---

### `ui/static/app.js` — `credentials:'include'`, 401 redirect, add-user modal, logout (component/hook, request-response)

**Analog:** `ui/static/app.js` (self), lines 47-59 (`apiFetch`) and lines 32-45 (`showToast`)

**`apiFetch` to modify** (lines 47-59):
```javascript
async function apiFetch(path, options = {}) {
    const resp = await fetch(`${AGENT_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body.detail || resp.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (resp.status === 204) return null;
    return resp.json();
}
```
Add `credentials: 'include'` to the `fetch()` call (RESEARCH.md Pitfall 1 — cross-origin `:8000` → `:8001` cookies are dropped otherwise), and add a `resp.status === 401` branch before the generic `!resp.ok` handling that redirects to `login.html?expired=1` (D-09) instead of throwing — e.g. `if (resp.status === 401) { window.location.href = '/static/login.html?expired=1'; return; }`.

**`showToast` reused as-is** for the session-expired banner on `login.html` (`type: 'warning'` → `bg-yellow-900/90 border-yellow-700`, line 39) and for login/add-user inline errors (`text-red-400`, per UI-SPEC — note UI-SPEC specifies inline error text under the form, not a toast, for login/add-user validation failures specifically; reserve `showToast` for the session-expired case and other app-wide notices).

**WebSocket construction** (line 392) needs no `credentials` change — cookies are sent automatically on the WS handshake; only `app.js`'s `fetch()`-based `apiFetch()` needs the opt-in.

---

### `ui/static/index.html` — logout button, add-user modal trigger (component, request-response)

**Analog:** `ui/static/index.html` (self), lines 63-67 (`#btn-settings`) and lines 48-50 (`#agent-status` sidebar footer)

**Secondary-button pattern to copy** (lines 63-67):
```html
<button id="btn-settings"
    class="rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 px-3 py-1.5 text-sm transition"
    title="Настройки">
    ⚙️
</button>
```
Per UI-SPEC's Component Notes: the new **logout control** goes near `#agent-status` (lines 48-50) using this same secondary-button treatment but with `py-2` (8px, on-grid) instead of the legacy `py-1.5` (6px) — this is an intentional, UI-SPEC-approved 2px deviation, do not "fix" it to match `#btn-settings` exactly. The **add-user modal** reuses `#settings-modal`'s full DOM/class pattern verbatim (lines 118-189), including the header title + `×` close button row (line 120-123) — add `aria-label="Закрыть"` to the close button per UI-SPEC (mandatory on the new instance). Trigger button placement: a small "Users" control near `#btn-settings` in the header (UI-SPEC recommendation, not mandatory).

---

### `tests/conftest.py` — `authenticated_client` fixture (test, request-response)

**Analog:** `tests/conftest.py` (self), lines 29-34 (`client` fixture)

**Fixture pattern to copy exactly** (lines 29-34):
```python
@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP client wired to the Agent FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```
Per RESEARCH.md Pitfall 5, add a new `authenticated_client` fixture (do not silently mutate the plain `client` fixture, since some phase tests may still want an unauthenticated client to assert 401s) that: seeds a `User` row via `shared/auth.py::hash_password()`, inserts a valid `Session` row directly via `async_session_factory` (mirroring `test_cascade_delete.py` lines 32-36's direct-session-insert style: `async with async_session_factory() as session: session.add(...); await session.commit()`), then sets the cookie on the `AsyncClient` (`ac.cookies.set(SESSION_COOKIE_NAME, token)`) before yielding. Existing test files that call REST endpoints directly (`test_settings_fallback.py`, `test_cascade_delete.py`, `test_stats.py`, `test_strategies.py`) will need their `client: AsyncClient` parameter swapped to `authenticated_client: AsyncClient` once `Depends(get_current_user)` is wired in — flag this as an in-phase follow-up task, not deferred.

---

### `tests/test_auth.py` (NEW) — login/logout/session tests (test, request-response)

**Analog:** `tests/test_settings_fallback.py` (assertion style, `@pytest.mark.asyncio` + `client: AsyncClient` signature)

**Test shape to copy** (lines 7-28):
```python
@pytest.mark.asyncio
async def test_get_settings_falls_back_to_global(client: AsyncClient) -> None:
    """Per-chat GET should inherit global settings when no override exists."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Fallback chat"})
    assert chat_resp.status_code == 201
    chat_id = chat_resp.json()["id"]
    ...
    resp = await client.get(f"/api/v1/settings?chat_id={chat_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["temperature"] == 0.5
```
Mirror this `arrange → act → assert status_code → assert json fields` shape for: successful login sets cookie + 200, wrong password → 401 generic message (per UI-SPEC copywriting contract — exact string `"Неверное имя пользователя или пароль"`), logout clears cookie + subsequent request 401, duplicate-username add-user → 409/400 with `"Пользователь с таким именем уже существует"`, 30-day sliding expiry extends `expires_at` on each authenticated request (D-02).

---

### `tests/test_ws_auth.py` (NEW, optional) — WS auth-at-accept tests (test, event-driven WS)

**Analog:** `tests/test_ws_origin_validation.py` (full file — `TestClient.websocket_connect` + `pytest.raises` for rejected connections)

**Rejection-assertion pattern to copy exactly** (lines 77-87):
```python
def test_ws_rejects_invalid_origin() -> None:
    """WebSocket from unknown origin should be rejected with code 1008."""
    with TestClient(app) as client:
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": "http://evil.com"},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008
```
Use this exact shape (sync `TestClient`, `pytest.raises(Exception)`, assert `.code == 1008`) for: WS connect with no cookie → 1008, WS connect with an expired/invalid session token → 1008, WS connect to a chat owned by a different user → 1008 (AUTH-04). For the "should succeed" case, mirror `test_ws_accepts_localhost_8000`'s `headers={"Origin": ...}` pattern but add a `Cookie` header carrying a pre-seeded valid session token (seed via the same direct-session-insert approach as `authenticated_client`).

---

## Shared Patterns

### Commit/rollback on every write
**Source:** `agent/main.py::update_settings`, lines 319-324
```python
try:
    await session.commit()
    await session.refresh(row)
except Exception:
    await session.rollback()
    raise
```
**Apply to:** the login endpoint's `Session` row insert, `POST /api/v1/auth/users`, `ensure_bootstrap_admin()`, `backfill_user_id()`.

### FK cascade via `sa_column`, never `Field(ondelete=...)`
**Source:** `shared/models.py`, lines 68-77 (`Settings.chat_id`)
```python
chat_id: Optional[int] = Field(
    default=None,
    sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), unique=True, nullable=True),
)
```
**Apply to:** `Session.user_id` (CASCADE), `Chat.user_id`/`Settings.user_id` (nullable, no cascade-delete-of-chats-on-user-delete needed unless specified — confirm with planner; RESEARCH.md doesn't mandate this).

### Idempotent additive migration, table-exists + `PRAGMA table_info` guard
**Source:** `shared/database.py::migrate_add_context_length`, lines 73-92
**Apply to:** `migrate_add_user_id_columns()`.

### Pre-accept WebSocket security gate, no-raise/`bool`-return style
**Source:** `agent/ws.py::_validate_origin`, lines 40-68
**Apply to:** the new session-cookie + chat-ownership check in `ws_chat`, and `get_current_user_ws()` in `agent/dependencies.py`.

### 404-not-403 on ownership mismatch (IDOR mitigation)
**Source:** `agent/main.py::_get_chat_or_404`, lines 52-63 (existing pattern already returns 404 for any missing chat; extend the same helper to also 404 on `chat.user_id != current_user.id` rather than introducing a new 403 code path)
**Apply to:** every chat-scoped REST/WS handler.

### `structlog` key=value logging, `snake_case_action` message keys
**Source:** `agent/ws.py::_validate_origin`, lines 43, 46, 50, 54, 64, 67 (`logger.info("ws_origin_check", origin=...)`, `logger.warning("ws_origin_rejected", origin=...)`)
**Apply to:** all new auth logging (`login_success`/`login_failed`, `session_expired`, `ws_auth_rejected`, `bootstrap_admin_created`) — never log password/token values (CLAUDE.md Logging).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `shared/auth.py` | utility | transform | First cryptographic-utility module in the codebase — no hashing/token-generation precedent exists; use RESEARCH.md Pattern 1 (verified against official `pwdlib` docs) directly. |
| `agent/dependencies.py` | middleware | request-response | First `Depends()`-based auth dependency — no prior auth middleware exists; synthesized from `agent/main.py`'s existing `Depends(get_session)` + `_get_chat_or_404` guard-clause conventions plus RESEARCH.md Pattern 2. |
| `agent/schemas.py` new classes (`LoginRequest`, `CreateUserRequest`, `UserResponse`) | model (schema) | transform | No prior credential-carrying schema exists; structurally identical to existing `ChatCreate`/`MessagePayload` (same `BaseModel` + `Field(max_length=...)` idiom) — treated as "role-match" via the file's own existing classes rather than a true gap, listed here only because the *specific* username/password shape has no precedent. |

## Conventions

Derived via the shared `gsd-tools.cjs verify conventions --derive` module (repo-wide scope; `--scope agent` returned `no-readable-files` because the tool's JS/TS-oriented analyzers only find identifiers in `.js`/`.ts` sources, none of which live under `agent/`):

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| file-name casing | — | 0% (n=1) | — | insufficient-data |
| identifier casing | camelCase | 100% (n=42) | 0 | named contract |
| export style | — | 0% (n=0) | — | insufficient-data |
| import style | — | 0% (n=0) | — | insufficient-data |

**Caveat:** this repo is Python-first (`shared/`, `agent/`, `ui/main.py`, `run.py`, `tests/`) with a single vanilla-JS frontend file (`ui/static/app.js`). The derivation tool's axes are JS/TS-shaped, so only `app.js` fed the `identifier-casing` result (100% camelCase, matching what was directly observed in `apiFetch`/`showToast`/`renderMarkdown` above) — the other three axes are `insufficient-data` because there is no second JS/TS file and no ESM/CJS import or export statement in scope to sample. For the Python side (all backend files in this phase), follow CLAUDE.md's explicit, already-locked convention table directly rather than the tool's axes: modules/functions `lowercase_with_underscores`, classes `PascalCase`, constants `UPPER_CASE`, private helpers/module vars prefixed `_`, `from x import (a, b)` grouped imports, stdlib → third-party → local ordering — these are treated as a **named contract** (share effectively 100% across every file read in this mapping pass: `shared/models.py`, `shared/database.py`, `agent/main.py`, `agent/ws.py`, `agent/schemas.py`, `agent/state.py`, `run.py`, `tests/conftest.py`), not a contested axis.

**Contested hotspots (author's choice):** none observed within this phase's file set — all Python files sampled follow one consistent style. (For context, the shared tooling's own canonical example of an intentional-contested split is the gsd-plugin's own CJS↔SDK dual resolver — `bin/lib/**` is CJS `module.exports`/`require`, `sdk/src/**` is ESM `export`/`import` — each half internally consistent per-directory, contested only when compared repo-wide. This project has no equivalent split: the two-process Python split (`agent/` vs `ui/`) shares one style via `shared/`, and the sole JS file, `app.js`, has no sibling to contest against. If a future phase adds a second JS/TS file, re-run the derivation to check whether `login.html`'s inline script — if any — introduces a second variant.)

## Metadata

**Analog search scope:** `shared/`, `agent/`, `ui/static/`, `run.py`, `requirements.txt`, `tests/` (repo root)
**Files scanned:** `shared/models.py`, `shared/database.py`, `shared/config.py`, `agent/main.py`, `agent/ws.py`, `agent/schemas.py`, `agent/state.py`, `ui/main.py`, `ui/static/app.js`, `ui/static/index.html`, `run.py`, `requirements.txt`, `tests/conftest.py`, `tests/test_settings_fallback.py`, `tests/test_cascade_delete.py`, `tests/test_ws_origin_validation.py`
**Pattern extraction date:** 2026-09-19
