# Phase 19: API keys and HTTP chat (Day 30) - Pattern Map

**Mapped:** 2026-10-12
**Files analyzed:** 17 new/modified code files (+ docs and tests)
**Analogs found:** 17 / 17

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `shared/auth.py` (+`generate_api_key`, `hash_api_key`) | utility | transform | same file `generate_session_token`/`hash_session_token` (25-32) | exact |
| `shared/models.py` (+`ApiKey`) | model | CRUD | `Session` (385-400) | exact |
| `shared/database.py` (import list) | config | batch | existing explicit model import in `init_db` | exact |
| `agent/dependencies.py` (Bearer branch, `get_session_user`) | middleware | request-response | same file `get_current_user` (53-80) | exact |
| `agent/api_keys_api.py` (NEW router) | controller | CRUD | `agent/providers_api.py` | exact |
| `agent/http_chat.py` (NEW router) | controller | request-response (wraps streaming turn) | `agent/headless.py` (RecordingSink) + `agent/ws.py::_handle_chat_message` | role-match |
| `agent/ws.py` (split `_run_chat_turn`, `_discard_user_message`, `cause` on LLM_ERROR) | service | streaming | same file, 798-1179 | exact (refactor) |
| `agent/rag_api.py` (+`apply_chat_kb`) | service | CRUD | `put_rag_config` (149-198) | exact |
| `agent/main.py` (include routers, logout/create_user -> `get_session_user`) | config/controller | request-response | `agent/main.py` 448-453, 531-605 | exact |
| `agent/schemas.py` (`HttpChatRequest`, responses, `ApiKey*`) | model | request-response | `MessagePayload` (359-365), `McpServerUpdate` (247) | exact |
| `ui/static/index.html` (`#api-keys-section`, `#api-key-modal`) | component | request-response | `#llm-providers-section` (387-424), `#add-user-modal` (436-) | exact |
| `ui/static/app.js` (api keys list/create/revoke, modal, `chat_updated`) | component | CRUD + event-driven | `loadLlmProviders`..`deleteLlmProvider` (2482-2631), `handleEventFrame` (3371), `copyTextToClipboard` (2135) | exact |
| `tests/test_api_keys.py`, `tests/test_bearer_auth.py` | test | request-response | `tests/test_auth.py`, `tests/conftest.py` fixtures | role-match |
| `tests/test_http_chat.py` | test | request-response | `tests/test_rag_ws.py` (`_plain_content_response`, respx) | role-match |
| `tests/test_api_keys_ui.py` (static guard) | test | transform | `tests/test_network_ui.py` | exact |
| `docs/API_SPEC.md`, `TESTING_GUIDE.md`, `ARCHITECTURE.md`, `USER_GUIDE.md`, `.planning/REQUIREMENTS.md` (KEY-04 reword) | docs | - | existing sections in same files | exact |

## Pattern Assignments

### `shared/auth.py` (utility, transform)
**Analog:** same file, lines 25-32.
```python
def generate_session_token() -> str:
    """Generate a cryptographically secure opaque session token."""
    return secrets.token_urlsafe(32)

def hash_session_token(token: str) -> str:
    """Hash a session token for at-rest storage (never store the raw token)."""
    return hashlib.sha256(token.encode()).hexdigest()
```
Add `API_KEY_PREFIX = "aia_"`, `generate_api_key()` = `API_KEY_PREFIX + secrets.token_urlsafe(32)`, `hash_api_key()` = same sha256 form. Module has a one-line docstring and type hints everywhere.

### `shared/models.py` `ApiKey` (model, CRUD)
**Analog:** `Session`, lines 385-400.
```python
class Session(SQLModel, table=True):
    """A server-side, revocable login session for a user."""
    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
```
Copy shape: `key_hash` unique+indexed, `user_id` via `sa_column` CASCADE (never `Field(ondelete=)`), plus `name`, `key_prefix`, `last_used_at: Optional[datetime]`. Table created by `create_all`; no migration function needed.

### `agent/dependencies.py` (middleware, request-response)
**Analog:** `get_current_user` lines 53-80 (imports lines 3-16; `_as_aware_utc` at 46). Existing cookie logic stays; prepend a Bearer branch, add `request: Request` param, set `request.state.auth_method`. Add `get_session_user` that wraps `get_current_user` and raises 403 for `api_key`. Reuse the error idiom:
```python
raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
...
row.expires_at = now + timedelta(days=SESSION_TTL_DAYS)
db.add(row)
try:
    await db.commit()
except Exception:
    await db.rollback()
    raise
```
Use the same commit/rollback shape for the throttled `last_used_at` write (only if NULL or older than 60 s). `get_current_user_ws` (83-107) stays untouched = WS cookie-only. Bearer lookup: `select(ApiKey).where(ApiKey.key_hash == hash_api_key(token))`, no caching. Logs: never the key.

### `agent/api_keys_api.py` (controller, CRUD)
**Analog:** `agent/providers_api.py`.

**Imports/router/deps pattern** (lines 1-26):
```python
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from shared.database import get_session
from shared.logger import get_logger
logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/llm-providers", tags=["llm-providers"])
MSG_PROVIDER_NOT_FOUND = "Провайдер не найден"
```
**Owner 404 pattern** (62-68): `_get_or_404` logs `*_access_denied` and raises 404 for missing and foreign alike.
**Mutating route pattern** (102-124): `@router.post("", response_model=..., status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)])` with `session` and `current_user` Depends.
**Delta:** use `Depends(get_session_user)` (cookie-only) instead of `get_current_user` on all three routes; DELETE returns 204 (see `logout` in `agent/main.py:531` for `status_code=204`, `-> None`); cap 20 -> 409 with Russian `detail`; 422 via an `_unprocessable`-style helper (providers_api.py:71). Wrap writes in try/except with `await session.rollback()`.

### `agent/http_chat.py` (controller, request-response over streaming turn)
**Analog 1:** `agent/headless.py` `RecordingSink` (67-76):
```python
class RecordingSink:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
    async def send_json(self, frame: dict[str, Any]) -> None:
        if frame.get("type") != "token":
            self.frames.append(frame)
```
**Analog 2:** `agent/ws.py::_handle_chat_message` (798-808). Lock idiom to be extracted:
```python
current_chat_model.set(payload.model)
current_chat_provider_id.set(payload.provider_id)
if chat_id not in chat_locks:
    chat_locks[chat_id] = asyncio.Lock()
async with chat_locks[chat_id]:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
```
Refactor: `_handle_chat_message` = lock wrapper only; body (from `async with async_session_factory()` on) -> `_run_chat_turn(websocket, chat_id, payload)`, ContextVar `.set` calls move inside it. HTTP route: owner check (use `_get_owned_chat` idiom from `rag_api.py:67-76` -> 404), `lock = chat_locks.setdefault(...)`, `if lock.locked(): 409 CHAT_BUSY`, `await lock.acquire()` immediately (no await between), `asyncio.create_task(...)` kept in a module-level set with done-callback discard, `await asyncio.shield(task)`, release lock in task `finally`.

**Frames to map** (ws.py): `done` frame at 1169-1179 (`message_id, stats, memory_writes, task_writes, invariant_conflict, rag`); errors at 826 (`PROVIDER_UNAVAILABLE`), 855-862 (`CONTEXT_OVERFLOW` + `suggested_strategy`), 956-962 (`LLM_ERROR`, add `cause`). Use `JSONResponse` for `{detail, code, user_message_kept}` bodies (HTTPException would lose `code`). Success: read assistant `Message` by `done.message_id`; `user_message_id` = its `parent_id`. After success: `hub.publish(user.id, {"type": "chat_updated", "chat_id": chat_id})` (import `from agent.events import hub`; same as `agent/titles.py:239`, which publishes `{"type": "chat_title_updated", "chat_id": ..., "title": ...}`).

**Leaf-restore fix (ws.py lines 749, 863, 963, 1036)** - each site does `await session.delete(user_msg); await session.commit()`. Replace all four with `_discard_user_message(session, chat, user_msg)` which first sets `chat.current_leaf_message_id = user_msg.parent_id`, deletes, commits, rollback on error.

### `agent/rag_api.py` `apply_chat_kb` (service, CRUD)
**Analog:** `put_rag_config` lines 160-182 (validation + upsert + rollback):
```python
if body.kb_id is not None:
    kb = await _get_owned_kb(session, body.kb_id, current_user.id)
    if kb.status != KbStatus.READY:
        raise _unprocessable(MSG_KB_NOT_READY)
else:
    mode = MODE_OFF
try:
    row = await session.get(ChatRagConfig, chat_id)
    if row is None:
        row = ChatRagConfig(chat_id=chat_id)
        session.add(row)
    row.mode = mode
    row.kb_id = body.kb_id
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
except SQLAlchemyError:
    await session.rollback()
    raise
```
Delta: do NOT set `top_k`/search fields (PUT resets `top_k`); only `kb_id` + `mode` (`MODE_RAG` when kb given, else `MODE_OFF`). Import `MODE_RAG`/`MODE_OFF` from `agent.rag`. Omitted-vs-null detection: `"kb_id" in body.model_fields_set` (same idiom as `_apply_search_settings`, line 123).

### `agent/schemas.py` (model)
**Analog:** `MessagePayload` (359-365): `content: str = Field(min_length=1, max_length=CONTENT_MAX_LENGTH)`, `model: str = Field(min_length=1, max_length=200)`, `provider_id: int | None = Field(default=None, ge=1)`. Subclass it as `HttpChatRequest` adding `kb_id: int | None = Field(default=None, ge=1)`. Key models follow `LlmProviderOut` style (`name` strip, 1..100). `McpServerUpdate.enabled: Optional[bool]` (line 255) is the body for the D-08 curl step.

### `agent/main.py`
- Router registration, lines 448-453: `app.include_router(rag_router)` ... add `api_keys_router`, `http_chat_router`.
- `logout` (531-537) and `create_user` (573-582): swap `Depends(get_current_user)` for `Depends(get_session_user)`. Everything else untouched.

### `ui/static/index.html` (component)
**Analog:** `#llm-providers-section` lines 387-424 (header row with h3 + indigo "+ Добавить" button, hidden `*-form` card with label/input/`text-xs text-red-400` error `<p>` and Отменить/Сохранить buttons, `*-empty` block, `*-list` container). Insert `<section id="api-keys-section" class="border-t border-slate-700 p-4 space-y-4">` between line 424 and `#network-section` (426). **Modal analog:** `#add-user-modal` 436-454 - `id="...-modal" class="hidden fixed inset-0 z-50 ..."` and `<button id="btn-close-add-user" aria-label="Закрыть" ...>&times;</button>`. New modal id `api-key-modal` must have `btn-close-api-key` (modal policy test). Follow `19-UI-SPEC.md` for exact strings.

### `ui/static/app.js` (component, CRUD + event-driven)
**Analog:** `loadLlmProviders`/`renderLlmProviderRow`/`renderLlmProviderList`/`saveLlmProvider`/`deleteLlmProvider` (2482-2631).
- Load: `state.x = await apiFetch('/api/v1/...') || []; render...();`
- Rows built only with `mcpEl(tag, className, text)` (2072, textContent - no innerHTML); `mcpEl('button', 'text-xs text-red-400 hover:text-red-300 ...', 'Отозвать')`, `.type = 'button'`, click -> `x().catch((err) => showToast(err.message, 'error'))`.
- Save guard: `if (state.saving) return;` disable button, set label, `finally` restore; error text into `*-form-error` element.
- Revoke: `if (!confirm(message)) return; await apiFetch(url, {method:'DELETE'}); showToast(...,'success'); await load...();`
- Copy: `await copyTextToClipboard(text)` (2135, HTTP-safe fallback).
- Modal close binding must be exactly `$('btn-close-api-key').addEventListener('click', ...)` (compare line 3681 `$('btn-close-add-user').addEventListener('click', closeAddUserModal);`); no backdrop/Escape handling.
- Wire into `openSettingsModal` (3589-3592): add `loadApiKeys().catch((err) => showToast(err.message, 'error'));`.
- curl text uses `AGENT_BASE` (app.js:4: `` `http://${window.location.hostname}:${AGENT_PORT}` ``) and path `POST /api/v1/chats/{id}/messages` (the UI-SPEC `/http-message` is a placeholder).
- Events: `handleEventFrame` (3371-3391) - add before `applySchedulerEvent(frame)`:
```js
if (frame && frame.type === 'chat_updated') { onChatUpdatedEvent(frame); return; }
```
  `onChatUpdatedEvent`: `loadChats()`; if `frame.chat_id === state.currentChatId && !state.isStreaming` clear `childrenByParent`/`activeChildByParent` and call `loadChatTree`, `loadChatStats`, `loadChatMemory`, `loadChatTasks`, `loadChatRag`. Do not call `selectChat`.
- Wrap new code in `// ---- API keys ----` / `// ---- end API keys ----` markers so a static test can scope it (as `// ---- Network ----`).

### Tests
- **Fixtures:** `tests/conftest.py` `authenticated_client` (120-147; cookie client, `ac.seeded_user_id`) and `second_authenticated_client` (151+) for cross-user 404. A cookie-less client: `AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.
- **LLM mock:** `tests/test_rag_ws.py::_plain_content_response` (37-47) builds an SSE `httpx.Response`; use `@respx.mock` with `respx.post(f"{BASE_URL}/v1/chat/completions")`; `side_effect=httpx.ConnectError(...)` for 503.
- **Busy test:** pre-acquire `asyncio.Lock()` and place in `agent.state.chat_locks[chat_id]` (conftest clears between tests).
- **Static UI guard:** copy `tests/test_network_ui.py` structure (`STATIC_DIR`, `_raw_app_js`, `_html`, marker-region extraction, `_function_source`). Also rerun `tests/test_modal_close_policy.py` and `tests/test_static_js_syntax.py`.
- WS leaf-restore regression test required (both transports).

## Shared Patterns

### Owner scoping / 404 not 403
**Source:** `agent/rag_api.py` 67-76 (`_get_owned_chat`), `agent/providers_api.py` 62-68. **Apply to:** key DELETE, HTTP chat, KB selection.
```python
if chat is None or chat.user_id != user_id:
    logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chat {chat_id} not found")
```

### Mutating-route guards
**Source:** `agent/providers_api.py` 106. **Apply to:** every new POST/PUT/DELETE.
`dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)]`

### Commit/rollback
**Source:** `agent/dependencies.py` 70-75 and `rag_api.py` 168-182. `await session.commit()` after writes, `await session.rollback()` in the except, specific exceptions (`SQLAlchemyError`), no bare `except:`.

### Logging
`logger = get_logger(__name__)`; snake_case keys with kv pairs, e.g. `api_key_created key_id=, user_id=`; never log the key, `Authorization`, or the body; log `str(exc)` only.

### Time
`datetime.now(timezone.utc)`; normalise SQLite-naive datetimes with `_as_aware_utc` (dependencies.py:46) before comparing.

### Events
`hub.publish(user_id, frame)` is fire-and-forget (`agent/kb_indexer.py:104`, `agent/titles.py:239`).

### Frontend safety
Build DOM via `mcpEl`/`textContent`; any HTML insertion needs `DOMPurify.sanitize()`; UI strings Russian.

## Conventions

Convention derivation skipped (`gsd-tools.cjs` not resolvable in this environment: the plugin path variable was empty). Conventions below come from CLAUDE.md and the files read; treat them as documented, not measured.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case.py (Python), kebab ids in HTML | not measured | - | named contract (CLAUDE.md) |
| Identifier casing | snake_case functions, PascalCase classes, UPPER_CASE constants, `_private` helpers | not measured | - | named contract (CLAUDE.md) |
| Export style | Python module-level names, explicit `router = APIRouter(...)` per module | not measured | - | named contract |
| Import style | stdlib, third-party, local; absolute from project root (`from agent.x import`) | not measured | - | named contract (CLAUDE.md) |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` CJS vs `sdk/src/**` ESM) is not relevant to this Python repo. Local repo split to respect: backend is typed async Python; frontend `ui/static/app.js` is a single vanilla-JS script with top-level `function` declarations (no modules) - match the local file style.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| non-blocking lock check + shielded task in `agent/http_chat.py` | controller | request-response | No existing "reject if locked" path; every current caller awaits `async with chat_locks[...]` (`agent/main.py` 892-967, `agent/task_memory_api.py` 36-38). Use RESEARCH.md Pattern 3. |
| Bearer parsing in `get_current_user` | middleware | request-response | No existing header-based auth; use RESEARCH.md Pattern 1. |

## Metadata

**Analog search scope:** `agent/`, `shared/`, `ui/static/`, `tests/`
**Files scanned:** about 14 read in full or by range, plus greps across `agent/` and `shared/`
**Pattern extraction date:** 2026-10-12
