# Phase 12: LLM providers section in Settings (Day 21) - Pattern Map

**Mapped:** 2026-10-02
**Files analyzed:** 22 (9 new, 13 modified)
**Analogs found:** 21 / 22

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `shared/models.py` (+`LlmProvider`, `LlmProviderSeed`; `ScheduledTask.provider_id`) | model | CRUD | `McpServerConfig` (shared/models.py:553-576) | exact |
| `shared/database.py` (+`migrate_add_scheduledtask_provider_id`, call in `init_db`) | migration | batch | `migrate_add_message_tool_trace` (database.py:97-112) | exact |
| `shared/config.py` (+`resolve_env_secret`, `LLM_PROVIDER_CHECK_TIMEOUT`) | config/utility | transform | `MCP_CONNECT_TIMEOUT` in config.py (existing setting) | role-match |
| `agent/providers.py` (NEW: CRUD, seeding, resolver, check, cache) | service | CRUD + request-response | `agent/mcp_config.py` (CRUD) + `agent/mcp_client.py` (status/error result) | exact (CRUD) / role-match (check) |
| `agent/schemas.py` (+`LlmProvider*` schemas, `MessagePayload.provider_id`, `ModelLoadRequest.provider_id`) | model (schema) | request-response | `McpServerCreate/Update/Response` (schemas.py:217-287) | exact |
| `agent/main.py` or new `agent/providers_api.py` (router `/api/v1/llm-providers`) | controller | CRUD | MCP endpoints (main.py:182-195, 1001-1120) | exact |
| `agent/main.py` lm-studio endpoints (add `provider_id`) | controller | request-response | itself (main.py:1195-1236) | exact |
| `agent/llm_client.py` (singleton to count-tokens alias; `get_lm_studio_client(base_url)` registry) | service | request-response | itself (llm_client.py:392-395) | exact |
| `agent/ws.py` (`_ToolTurn.client`, resolve before persist, 6 `stream_chat` sites) | controller (WS) | streaming | itself (ws.py:336-350, 670-692) | exact |
| `agent/titles.py` (`provider_id`, own client) | service | event-driven (background) | itself (titles.py:158-181) | exact |
| `agent/headless.py` + `agent/scheduler.py` + `agent/scheduler_ops.py` + `agent/scheduler_schemas.py` + `agent/scheduler_tools.py` + `agent/state.py` | service | batch | `headless.py:163-247` | exact |
| `agent/context_engine.py`, `agent/invariants.py` (client param) | service | request-response | `invariants.py:299`, `context_engine.py:645` | exact |
| `ui/static/index.html` (+`#llm-providers-section`) | component (markup) | CRUD | `#mcp-section` (index.html:317-361) | exact |
| `ui/static/app.js` (providers block; picker refactor) | component | CRUD + request-response | MCP block app.js:1461-1845; picker app.js:1372-1459 | exact |
| `tests/test_llm_providers_api.py` | test | CRUD | `tests/test_mcp_api.py` | exact |
| `tests/test_llm_providers_check.py` | test | request-response | respx tests (`tests/test_lm_studio_client.py`, `test_llm_complete_chat.py`) | role-match |
| `tests/test_llm_providers_routing.py` | test | streaming | `tests/test_mcp_chat_ws.py` | role-match |
| `tests/conftest.py` (clear `MODEL_CACHE`) | test config | batch | `clean_test_db` (conftest.py:24-49) | exact |
| `tests/test_titles.py`, `test_titles_ws.py` (migrate patches) | test | event-driven | themselves | exact |
| `.planning/REQUIREMENTS.md` (PROV-01..07) | doc | n/a | existing requirement rows | n/a |
| `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md` | doc | n/a | existing sections | n/a |
| `09-HUMAN-UAT.md` (999.11 update) | doc | n/a | itself | n/a |
| per-provider env-secret resolver (`dotenv_values`) | utility | transform | none | no analog (use RESEARCH Pattern 2) |

## Pattern Assignments

### `shared/models.py` (model, CRUD) — `LlmProvider`, `LlmProviderSeed`

**Analog:** `McpServerConfig` (shared/models.py:553-576)

**User-scoped FK pattern** (lines 553-576):
```python
class McpServerConfig(SQLModel, table=True):
    """User-scoped MCP stdio server launch config."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    name: str = Field(max_length=100)
    ...
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```
Copy verbatim for `LlmProvider` (fields per RESEARCH "Models + migration": `name, base_url, kind, api_key_env, enabled`). `UniqueConstraint` is imported from `sqlalchemy` (check import block at models.py:7-16; add it if absent). `LlmProviderSeed` uses composite PK with the `sa_column` FK (never `Field(ondelete=...)`).

**`ScheduledTask.provider_id`** (near models.py:457, after `model: str`): plain `Optional[int] = Field(default=None)`, deliberately no FK (see Pitfall 2 in RESEARCH). Contrast with `origin_chat_id` (lines 447-454), which uses `SET NULL`; do NOT copy that.

---

### `shared/database.py` (migration, batch)

**Analog:** `migrate_add_message_tool_trace` (database.py:97-112)
```python
async def migrate_add_message_tool_trace(conn: Any) -> None:
    """Add tool_trace column to message when missing (idempotent)."""
    table_check = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='message'"),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(message)"))
    columns = [row[1] for row in result.fetchall()]
    if "tool_trace" not in columns:
        logger.info("migrating_message_add_tool_trace")
        await conn.execute(text("ALTER TABLE message ADD COLUMN tool_trace TEXT"))
```
Clone for table `scheduledtask`, column `provider_id INTEGER`. Register in `init_db` (database.py:224-232) before `create_all`:
```python
await migrate_add_message_tool_trace(conn)
await migrate_add_scheduledtask_provider_id(conn)   # NEW
await conn.run_sync(SQLModel.metadata.create_all)
```
Metadata import at database.py:18 lists only `Chat, Message, Session, Settings, TokenUsage, User  # noqa: F401`; add `LlmProvider, LlmProviderSeed` so `create_all` registers them.

---

### `agent/providers.py` (service, CRUD) — NEW

**Analog:** `agent/mcp_config.py` (whole file; copy structure).

**Imports + logger** (mcp_config.py:3-12):
```python
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import McpServerConfig

logger = get_logger(__name__)
```

**List / get with ownership (None for foreign)** (lines 66-85):
```python
async def list_servers(session: AsyncSession, user_id: int) -> list[McpServerConfig]:
    result = await session.exec(
        select(McpServerConfig)
        .where(McpServerConfig.user_id == user_id)
        .order_by(McpServerConfig.id),
    )
    return list(result.all())

async def get_server(session, user_id, server_id) -> McpServerConfig | None:
    row = await session.get(McpServerConfig, server_id)
    if row is None or row.user_id != user_id:
        return None
    return row
```

**Create/update/delete commit-rollback** (lines 118-126, 160-169, 176-182):
```python
session.add(row)
try:
    await session.commit()
    await session.refresh(row)
except Exception:
    await session.rollback()
    raise
logger.info("mcp_server_created", user_id=user_id, server_id=row.id, name=row.name)
```
Update sets `row.updated_at = datetime.now(timezone.utc)` before commit and takes optional fields (`None` = unchanged). Log keys: `llm_provider_created/updated/deleted` with `user_id`, `provider_id` only (never key values).

**Seeding race:** catch `sqlalchemy.exc.IntegrityError` on the marker insert, `await session.rollback()`, ignore (RESEARCH Pattern 4).

**Check service / error taxonomy:** analog for classification is `mcp_client.build_error_result` + `McpErrorCode` (schemas.py:137-170; used in main.py:1024-1029) and the `httpx.ConnectError` / `HTTPStatusError` / `TimeoutException` mapping in `headless.py:163-171` (below). Failures are returned as data (check result), never raised, mirroring "connect failures are reported in the body, not as HTTP errors" (main.py:1132).

**Gather-with-no-raise pattern** (main.py:1008-1033): `asyncio.gather(..., return_exceptions=True)`, then per-item handling so one broken item does not fail the list. Use for the concurrent per-provider model fetch in `GET /llm-providers/models`.

**Module state / cache:** no analog for a keyed in-memory cache with test reset beyond `agent/state.py` dicts (`chat_locks`, `title_tasks`) cleared in `conftest.py:40-44`. Follow that: module-level `MODEL_CACHE` dict, cleared in `clean_test_db`.

---

### `agent/schemas.py` (schema, request-response)

**Analog:** `McpServerCreate` / `McpServerUpdate` / `McpServerResponse` (schemas.py:217-287).

Create schema: `Field(min_length=1, max_length=...)` plus `@field_validator(...) @classmethod` strip/validate helpers (lines 227-231). Update schema: all `Optional[...] = Field(default=None, ...)` and the endpoint uses `body.model_dump(exclude_unset=True)` (main.py:1078). Response: never carries secrets; "env values are never exposed, only their names" (line 276), same rule for `api_key_env` (name is returned, resolved value never).

Imports already present (schemas.py:3-11): `re`, `datetime`, `Enum`, `Any, Literal, Optional`, `BaseModel, Field, field_validator, model_validator`. Use `re` for the base-url `^https?://` and env-name regex validators.

**WS payload** (schemas.py:290-294) add one field:
```python
class MessagePayload(BaseModel):
    content: str = Field(min_length=1, max_length=CONTENT_MAX_LENGTH)
    model: str = Field(min_length=1, max_length=200)
    provider_id: int | None = None   # NEW; None = legacy -> seeded LM Studio
```
`ModelLoadRequest` (schemas.py:311-316) gains the same optional `provider_id`.

---

### Provider REST router (controller, CRUD) — `agent/main.py` or `agent/providers_api.py`

**Analog:** MCP endpoints, main.py:182-195 and 1001-1120.

**404-never-403 helper** (main.py:182-195):
```python
async def _get_mcp_server_or_404(session, user_id, server_id) -> McpServerConfig:
    row = await mcp_config.get_server(session, user_id, server_id)
    if row is None:
        logger.warning("mcp_server_access_denied", user_id=user_id, server_id=server_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"MCP server {server_id} not found")
    return row
```

**Route decorators + auth/CSRF dependencies** (main.py:1036-1046):
```python
@app.post(
    "/api/v1/mcp/servers",
    response_model=McpServerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_mcp_server(
    body: McpServerCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
```
PUT: same dependencies; DELETE: `status_code=204`, `dependencies=[Depends(require_allowed_origin)]` (main.py:1106-1110). Action endpoint `POST /{id}/check` mirrors `POST /mcp/servers/{id}/connect` (main.py:1122-1161): `dependencies=[Depends(require_allowed_origin)]`, returns the item with the check result in the body. GET list needs only `get_current_user` (main.py:1001-1005).

Route-order caution: register `GET /api/v1/llm-providers/models` before `/{provider_id}` routes (static path first).

Name conflict: catch `IntegrityError` from `UniqueConstraint` and raise `HTTPException(409)`.

**LM Studio endpoints** (main.py:1195-1236) keep their paths; `ConnectError` handling to preserve (lines 1201-1206):
```python
try:
    return await lm_studio_client.list_models()
except httpx.ConnectError:
    raise HTTPException(status_code=503, detail="LM Studio is not running") from None
except httpx.HTTPError as exc:
    raise HTTPException(status_code=502, detail=str(exc)) from exc
```
`lm_studio_client = LMStudioClient()` at main.py:95 becomes lookup via `get_lm_studio_client(base_url)` (registry keyed by normalized base URL).

---

### `agent/llm_client.py` (service)

**Analog:** itself. Singleton at llm_client.py:392-395:
```python
llm_client = LLMClient(
    base_url=settings.LM_STUDIO_BASE_URL,
    api_key=settings.DEEPSEEK_API_KEY,
)
```
Keep ONLY as a token-counting alias (`llm_client.count_tokens(...)` is used at context_engine.py:46,131-133,163-182 and ws.py:192,217 and ~8 test files). Production `stream_chat` / `complete_chat*` callers move to a per-provider client built as `LLMClient(base_url=row.base_url, api_key=resolve_env_secret(row.api_key_env) or "")`. Drop `DEEPSEEK_API_KEY` from the alias so the key is never sent to LM Studio.

---

### `agent/ws.py` (controller, streaming)

**Analog:** itself.

**`_ToolTurn` dataclass** (ws.py:336-350): add `client: LLMClient` field (before defaulted `allowed_tools`). Headless builds the same dataclass, so both constructors must pass `client`.

**Resolve-before-persist insertion point** (ws.py:675-692):
```python
current_chat_model.set(payload.model)
...
async with async_session_factory() as session:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await websocket.send_json({"type": "error", "detail": f"Chat {chat_id} not found"})
        return
    # NEW: client = await get_client(session, chat.user_id, payload.provider_id)
    #      except ProviderUnavailableError -> send {"type":"error","code":"PROVIDER_UNAVAILABLE",...}; return
    user_msg = await _persist_user_message(session, chat, payload.content)
```
Add `current_chat_provider_id.set(payload.provider_id)` next to line 676 (sibling ContextVar of `current_chat_model` in `agent/state.py:20`).

**`stream_chat` call sites** to change `llm_client.` to `client.` / `turn.client.`: ws.py:315 (`_stream_action_claim_retry`, add `client` param), 392 (`_stream_follow_up`, `turn.client`), 637 (`turn.client`), 755, 775, 904 (local `client`). Error frame shape to reuse (ws.py:683-685): `{"type": "error", "detail": ..., "code": ...}`; existing `LLM_ERROR` handling at ws.py:783-800 deletes the user message — keep it, optionally map 401/403 to a friendlier provider-named message.

---

### `agent/titles.py` (service, event-driven)

**Analog:** itself, titles.py:158-181 (`_complete_title`, two `llm_client.complete_chat_detailed` calls, retry on `TITLE_REJECTED_STATUS_CODES`):
```python
async def _complete_title(messages, model) -> ChatCompletionResult:
    try:
        return await llm_client.complete_chat_detailed(messages=messages, model=model, ...,
            extra_body={"reasoning_effort": TITLE_REASONING_EFFORT})
    except httpx.HTTPStatusError as exc:
        ...
    return await llm_client.complete_chat_detailed(..., extra_body=None)
```
Add a `client` argument threaded from `request_title` / `generate_and_apply_title` / `schedule_title_generation`; the background task opens its own session (`async_session_factory()`) and resolves the client by `(user_id, provider_id)` (`user_id` already a parameter). Provide a patchable seam (e.g. `agent.titles._get_client`) because `tests/test_titles.py` (~15 sites) and `test_titles_ws.py:436` patch `titles.llm_client.complete_chat_detailed`.

---

### `agent/headless.py` (+ scheduler files) (service, batch)

**Analog:** itself.

**Error mapping** (headless.py:163-171) — make provider-aware:
```python
def _map_llm_error(exc: BaseException) -> HeadlessRunError | None:
    if isinstance(exc, httpx.ConnectError):
        return HeadlessRunError("Модель недоступна: LM Studio не запущен")   # use provider name
    if isinstance(exc, httpx.HTTPStatusError):
        return HeadlessRunError(f"Модель недоступна: HTTP {exc.response.status_code}")
    if isinstance(exc, httpx.TimeoutException):
        return HeadlessRunError("Тайм-аут ответа модели")
    return None
```
**Turn construction** (headless.py:222-247): signature `run_headless_turn(session, user_id, prompt, model)` gains `provider_id`; resolve the client first (before `_prepare`) and raise `HeadlessRunError("Провайдер недоступен (удалён или отключён)")` on `ProviderUnavailableError`; pass `client=` into `ws._ToolTurn(...)` (line 235). `payload=SimpleNamespace(model=model)` stays.

Scheduler chain (RESEARCH call-site table): `scheduler.py:341` passes `task.provider_id`; `scheduler_ops.create_scheduled_task`, `scheduler_schemas.py` (`ScheduledTaskOut`, create body) and `scheduler_tools.py:65` (`_schedule_task` reads `current_chat_provider_id`) add the field.

---

### `agent/context_engine.py` / `agent/invariants.py`

**Analog:** `context_engine.py:645` (`raw = await llm_client.complete_chat(...)` in `_extract_facts`) and `invariants.py:299` (`run_self_critique`, fail-open). Add a `client: LLMClient` param (critique, called at ws.py:887) and `user_id` + `provider_id` for the debounced facts task (`_run_debounced_facts` opens its own session; failures already swallowed as `facts_extraction_failed`). Token-counting sites switch to module-level `count_tokens` or keep the alias.

---

### `ui/static/index.html` (markup)

**Analog:** `#mcp-section` (index.html:317-361). Copy structure: section header with accent "+ Добавить провайдера" button (`id="btn-llm-provider-add"`), hidden form `div` (NOT inside `#settings-form`), fields built from this input class string:
```html
<input type="text" id="mcp-name" class="w-full rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500">
```
label class `block text-sm text-slate-400 mb-1`; enabled checkbox row (lines 344-347); error `<p id="..-form-error" class="text-xs text-red-400">`; cancel/save buttons (lines 349-354); empty-state block (356-359); list container `<div id="..-list" class="space-y-2">`. Section wrapper: `class="border-t border-slate-700 p-5 space-y-3"`. Place after line 361. `#model-select` is at index.html:183. Follow `12-UI-SPEC.md` for copy/ids.

---

### `ui/static/app.js` (component, CRUD)

**Analog:** MCP block (app.js:1461-1845).

**Status badge maps + shared helpers** (lines 1463-1493):
```javascript
const MCP_STATUS_BADGE_CLASSES = { not_connected: 'text-slate-400', connecting: 'text-sky-400', connected: 'text-emerald-400', error: 'text-red-400' };
const MCP_NEUTRAL_BTN_CLASSES = 'px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 disabled:opacity-50 disabled:cursor-not-allowed';
state.mcpServers = []; state.mcpEditingId = null; state.mcpConnecting = new Set(); state.mcpSaving = false;
function mcpEl(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; }
```
Reuse `mcpEl` and `MCP_NEUTRAL_BTN_CLASSES`; define `LLM_PROVIDER_STATUS_*` maps and `state.llmProviders/llmProviderEditingId/llmProviderChecking(Set)/llmProviderSaving`. All DOM via `textContent`/`createElement` (provider name, URL, remote error text are untrusted).

**Row render** (renderMcpServerRow 1648-1704): header with truncated name + status span, actions (`Проверить` neutral, `Изменить`, red `Удалить`), disabled while in-flight set contains id. **List render** (1706-1711): toggle `#..-empty`, `textContent = ''`, append rows. **Open/close form** (1713-1732), **save with in-flight guard and inline error** (1751-1804):
```javascript
if (state.mcpSaving) return;
state.mcpSaving = true; saveBtn.disabled = true;
try { ... try { await apiFetch(url, { method, body: JSON.stringify(body) }); } catch (err) { errorEl.textContent = err.message; return; }
      showToast('Сервер сохранён', 'success'); closeMcpForm(); await loadMcpServers();
} finally { state.mcpSaving = false; saveBtn.disabled = false; }
```
After POST/PUT also call `POST /{id}/check`, re-render, then refresh the picker. **Delete with confirm** (1806-1815), **action with in-flight Set + re-render in `finally`** (connectMcpServer 1817-1837, model for the manual "Проверить"). `openSettingsModal` (app.js:2684) already calls `loadMcpServers()`; add `loadLlmProviders()` there.

**Picker refactor (modified, not new)** — current code app.js:1372-1459:
```javascript
state.models = await apiFetch('/api/v1/lm-studio/models');      // loadModels / refreshModelSelector / onModelSelect
...
opt.textContent = `${model.id}${model.loaded ? ' ✓' : ''}`;      // populateModelSelect
const loaded = state.models.find((m) => m.loaded); if (loaded) { select.value = loaded.id; state.selectedModel = loaded.id; }   // BUG: always overrides user's choice
```
Replace with `state.modelGroups` from `GET /api/v1/llm-providers/models`, `<optgroup label=name>` + `<option>` (value `${providerId}::${modelId}`, split on FIRST `::` only), selection priority previous selection > first loaded LM Studio model > first entry; `state.selectedProviderId`; `onModelSelect` keeps the confirm-to-load flow (lines 1431-1458) only for `kind === 'lm_studio'` groups and posts `provider_id` to `/lm-studio/load-model`. Other consumers to update: `populateSchedulerModelSelect` (2250-2268, reads `state.models`), `sendMessage` (~1332, add `provider_id`), scheduler create body (~2327), `model_loaded`/`model_event` handler (~1288), `trySendPending` (needs `selectedProviderId`). Run `tests/test_static_js_syntax.py` after edits.

---

### Tests

**Analog for CRUD/isolation tests:** `tests/test_mcp_api.py` (ownership 404, origin/content-type dependencies; see also `test_mcp_origin.py`). **Analog for routing/WS:** `tests/test_mcp_chat_ws.py` + `login_test_client` (conftest.py:73-85, sync `TestClient`, `client.portal.call(...)`). **Async HTTP:** `authenticated_client` fixture (conftest.py:102+) with `seed_user`. **respx:** existing LM Studio client tests. **Fixture reset:** add `providers.MODEL_CACHE.clear()` next to conftest.py:43-44 (`agent_state.title_tasks.clear()`; `events_hub.clear()`).

Existing WS tests create users via `_create_user` without seeding, so `get_provider_row` must call `ensure_seeded` lazily (RESEARCH Pattern 3), otherwise ~20 tests break.

---

## Shared Patterns

### User-scoped ownership (404 never 403)
**Source:** `agent/mcp_config.py:76-85`, `agent/main.py:182-195`
**Apply to:** providers CRUD, `/check`, LM Studio `provider_id` endpoints, resolver `get_provider`. Foreign id returns `None`, endpoint raises 404 with a `*_access_denied` warning log.

### Session writes
**Source:** `agent/mcp_config.py:118-126`
**Apply to:** all writes in `agent/providers.py`: `session.add` / `try: await session.commit(); await session.refresh(row) except Exception: await session.rollback(); raise`, then `logger.info(...)` (CLAUDE.md rule).

### Mutating-route CSRF dependencies
**Source:** `agent/main.py:1040`
**Apply to:** POST/PUT/DELETE on `/api/v1/llm-providers*` (`require_allowed_origin`; add `require_json_content_type` when a JSON body is read).

### Secrets never leave the Agent
**Source:** `McpServerResponse` docstring (schemas.py:276) and `env_keys=sorted(...)` (main.py:208)
**Apply to:** provider responses (return `api_key_env` NAME only), logs (`provider_id`, `error_code`, status only), exceptions. `follow_redirects=False` on check requests.

### httpx error classification
**Source:** `agent/headless.py:163-171`, `agent/main.py:1201-1211`
**Apply to:** `check_provider`, headless mapping, WS `LLM_ERROR` branch: `ConnectError` (unreachable) / `TimeoutException` (timeout) / `HTTPStatusError` (401/403 bad_key, else http+status).

### Frontend DOM safety
**Source:** `mcpEl` (app.js:1488-1493)
**Apply to:** all provider UI rendering. No `innerHTML` with provider/remote strings (note: current `loadModels` uses `innerHTML` only for static literals).

### Logging
`logger = get_logger(__name__)`, `snake_case_action` keys with `key=value` pairs, no `print`, `datetime.now(timezone.utc)`.

## Conventions

Convention derivation tool unavailable (`gsd-tools.cjs` not resolvable in this environment). The table below is taken from CLAUDE.md and the files read, not computed.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | `snake_case.py` | not computed (CLAUDE.md) | n/a | named contract |
| Identifier casing | `snake_case` funcs/vars, `PascalCase` classes, `UPPER_CASE` constants, `_private` helpers | not computed (CLAUDE.md) | n/a | named contract |
| Export style | plain module-level defs imported by name (`from agent.mcp_config import ...` / `from agent import mcp_config`) | not computed | n/a | named contract |
| Import style | absolute from project root, stdlib / third-party / local order | not computed | n/a | named contract |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` CJS `require`, `sdk/src/**` ESM `import`) is an intentional per-directory split in the GSD tooling and does not appear in this Python repo; match the local style of the directory being edited. In this repo the one real split is Python (`agent/`, `shared/`) vs. vanilla browser JS (`ui/static/app.js`: camelCase, `function` declarations, prefix-namespaced helpers like `mcp*`); follow each side's local style.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| env-secret resolver in `shared/config.py` (`resolve_env_secret`, `is_valid_env_name`) | utility | transform | No code reads arbitrary env-var names; `Settings` has `extra="ignore"` and pydantic-settings does not export `.env` into `os.environ`. Use RESEARCH Pattern 2 (`os.environ` then `dotenv_values(".env")`). |
| `MODEL_CACHE` keyed `(user_id, provider_id)` with check result | state | in-memory cache | Closest is `agent/state.py` dicts (`chat_locks`, `title_tasks`) for placement and test-reset convention only. |
| Tombstone seeding (`LlmProviderSeed`) | model/service | batch | No seeding-with-tombstone exists; closest idempotent seed is the admin/global-settings bootstrap in `shared/database.py` (`_ensure_global_settings`-style) but it does not need to survive deletion. |

## Metadata

**Analog search scope:** `agent/`, `shared/`, `ui/static/`, `tests/conftest.py`, test file names
**Files read:** mcp_config.py, models.py (excerpts), database.py (excerpts), main.py (excerpts), schemas.py (excerpts), llm_client.py (tail), titles.py (excerpt), headless.py (excerpt), ws.py (excerpts), index.html (MCP section), app.js (picker + MCP block), conftest.py
**Pattern extraction date:** 2026-10-02
