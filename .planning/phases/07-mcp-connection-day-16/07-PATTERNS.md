# Phase 7: MCP Connection (Day 16) - Pattern Map

**Mapped:** 2026-09-23
**Files analyzed:** 12 (new) + 4 (modified)
**Analogs found:** 15 / 16

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|-----------------|---------------|
| `shared/models.py` (add `McpServerConfig`) | model | CRUD | `shared/models.py::Profile` (user-scoped) + `ChatInvariant` (multi-row-per-user) | exact |
| `agent/mcp_config.py` (new) | service (CRUD layer) | CRUD | `agent/invariants.py` (list/create/update/delete for a table) | exact |
| `agent/mcp_client.py` (new) | service (external-process client + registry) | event-driven / request-response | `agent/llm_client.py::LMStudioClient` (locked external client, status enum, error classification) + `agent/state.py` (in-memory registry) | role-match (composite) |
| `agent/schemas.py` (add Mcp* models) | schema | request-response | `agent/schemas.py::GlobalInvariantCreate/Update/Response`, `ChatInvariant*` | exact |
| `agent/main.py` (add `/api/v1/mcp/servers...` routes) | controller (REST endpoints) | CRUD + request-response | `agent/main.py` chat-invariant endpoints (~586-666) | exact |
| `agent/main.py` (`lifespan`) | controller (lifecycle hook) | event-driven | `agent/main.py::lifespan` (~318) | exact |
| `agent/dependencies.py` | middleware (auth) | request-response | `agent/dependencies.py::get_current_user` | exact (reuse as-is, no new file) |
| `shared/config.py` (add `MCP_CONNECT_TIMEOUT`) | config | — | `shared/config.py::Settings` (existing fields) | exact |
| `requirements.txt` (add `mcp==1.30.0`) | config | — | existing pinned entries (e.g. `pwdlib[argon2]>=0.3.1`) | exact |
| `scripts/mcp_list_tools.py` (new dir+file) | utility (CLI script) | request-response | `run.py` (print()-exception CLI banner) + `migrate_strategies.py` (`asyncio.run()` one-shot script importing app modules directly) | role-match |
| `ui/static/index.html` (`#settings-modal` MCP section) | component (markup) | — | `#invariants-panel` / `#profile-panel` (collapsible sections inside sidebar/modal, `data-fold-toggle`) | exact |
| `ui/static/app.js` (MCP state, render, CRUD calls, connect/disconnect) | component (frontend controller) | CRUD + request-response | `app.js` invariants block (`renderInvariantsPanel`, `saveGlobalInvariant`, `deleteGlobalInvariant`, `loadInvariants`) + `app.js` task block (status badges, action buttons) | exact |
| `tests/test_mcp_config_api.py` (new) | test | CRUD | `tests/test_profile_api.py` / `tests/test_invariants_api.py` | exact |
| `tests/test_mcp_client.py` (new) | test | event-driven | `tests/test_lm_studio_client.py` (respx-style external-client mocking; here: real/fixture stdio subprocess instead of HTTP) | role-match |
| `tests/fixtures/mcp_stdio_server.py` (new) | test fixture | event-driven | none (no existing stdio-server test fixture) | none |
| `tests/conftest.py` (extend if needed) | test fixture | — | `tests/conftest.py` (`authenticated_client`, `clean_test_db`) | exact |

## Pattern Assignments

### `shared/models.py` (model, CRUD) — add `McpServerConfig`

**Analog:** `shared/models.py::Profile` (user-scoped FK pattern) combined with `ChatInvariant` (multiple rows per owner, `created_at`/`updated_at`)

**FK + field pattern** (lines 188-206, `Profile`):
```python
class Profile(SQLModel, table=True):
    """User-scoped style/format/constraint preferences, injected into every request (D-01)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
    )
    style: str = Field(default="", max_length=2000)
    ...
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
```
Note: `McpServerConfig` needs **multiple rows per user** (D-02), so drop `unique=True` on the FK — follow `ChatInvariant`'s non-unique `user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))` (lines 226-231) instead. `args`/`env` are JSON-list/JSON-object stored as `str` fields (mirrors `Settings.facts_json: str = Field(default="{}")`, line 99) — serialize/deserialize with `json.dumps`/`json.loads` in `agent/mcp_config.py`, not at the model layer (no custom SQLAlchemy JSON type used elsewhere in this codebase).

---

### `agent/mcp_config.py` (service/CRUD, CRUD) — new file

**Analog:** `agent/invariants.py` (global-invariant list/get/create/update/delete block, lines 19-85)

**Imports pattern** (lines 1-14):
```python
"""Thin CRUD layer owning all reads and writes to the invariant tables."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import ChatInvariant, GlobalInvariant, InvariantConflict

logger = get_logger(__name__)
```

**List/get/create pattern, user_id-scoped variant** — use `list_chat_invariants`'s `chat_id`-filtered shape but filter by `user_id` instead (matches `memory.py::list_long_term_memory`, lines 23-28):
```python
async def list_long_term_memory(session: AsyncSession, user_id: int) -> list[LongTermMemory]:
    """Return the user's full long-term memory, ordered by key (cross-chat, D-02)."""
    result = await session.exec(
        select(LongTermMemory).where(LongTermMemory.user_id == user_id).order_by(LongTermMemory.key),
    )
    return list(result.all())
```

**Update pattern with partial-field application + commit/rollback** (`agent/invariants.py::update_global`, lines 50-70):
```python
async def update_global(
    session: AsyncSession,
    invariant_id: int,
    **updates: str,
) -> GlobalInvariant | None:
    """Update the supplied fields on a global invariant, or return None if missing."""
    row = await get_global(session, invariant_id)
    if row is None:
        return None
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_updated", scope="global", invariant_id=invariant_id)
    return row
```

**Delete pattern returning bool** (`agent/invariants.py::delete_global`, lines 73-85):
```python
async def delete_global(session: AsyncSession, invariant_id: int) -> bool:
    """Delete a global invariant, returning False if it does not exist."""
    row = await get_global(session, invariant_id)
    if row is None:
        return False
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("invariant_deleted", scope="global", invariant_id=invariant_id)
    return True
```
**Applied to `mcp_config.py`:** `create_server`, `update_server` (must also call `agent.mcp_client`'s disconnect-on-edit per D-08 — the REST endpoint, not this module, should orchestrate that per separation of concerns already used by `main.py::delete_chat` calling `cleanup_chat_caches` after the DB commit), and `delete_server` (same — REST endpoint disconnects first, then calls this module's `delete_server`). Env values (D-12: "editing a server leaves existing env values untouched unless the user retypes them") requires the update function to merge the incoming env dict over the stored one at the dict level, not simply replace it — no existing analog for this merge; document as new logic in the plan.

---

### `agent/mcp_client.py` (service, event-driven + request-response) — new file

**Analog:** `agent/llm_client.py::LMStudioClient` (locked external-resource client with status enum, error classification, lines 175-334) + `agent/state.py` (in-memory dict + cleanup helper, whole file)

**Class shape / lock pattern** (`llm_client.py`, lines 175-184):
```python
class LMStudioClient:
    """LM Studio control and OpenAI-compatible model listing."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.LM_STUDIO_BASE_URL).rstrip("/")
        self._openai_base = f"{self._base_url}/v1"
        self._control_base = self._base_url
        self._model_switch_lock = asyncio.Lock()
        self._current_loaded_model: str | None = None
        self._instance_ids: dict[str, str] = {}
```
Apply the same `asyncio.Lock()`-per-resource idea to a **per-`(user_id, server_id)`** lock (Claude's Discretion item on serializing concurrent Connect) — either a `dict[tuple[int, int], asyncio.Lock]` module-level dict (mirrors `agent/state.py::chat_locks: dict[int, asyncio.Lock]`, line 13) or one lock per `SessionHandle`.

**Error classification / status-enum-return pattern (never raises to the caller, returns a typed result)** (`llm_client.py::_load_model_locked`, lines 240-266):
```python
try:
    async with httpx.AsyncClient(timeout=LOAD_TIMEOUT) as client:
        response = await client.post(url, json=body)
        response.raise_for_status()
        response_body = response.json()
except httpx.TimeoutException:
    logger.error("lm_studio_load_timeout", model_id=model_id)
    await self._emergency_unload(model_id)
    return ModelLoadResult(
        status=ModelLoadStatus.ERROR,
        message=f"Model load timed out for {model_id}",
        model_id=model_id,
    )
except httpx.ConnectError:
    logger.warning("lm_studio_unreachable")
    return ModelLoadResult(
        status=ModelLoadStatus.UNREACHABLE,
        message="LM Studio is not running",
        model_id=model_id,
    )
except (httpx.HTTPError, json.JSONDecodeError) as exc:
    logger.error("lm_studio_load_error", model_id=model_id, error=str(exc))
    return ModelLoadResult(
        status=ModelLoadStatus.ERROR,
        message=str(exc),
        model_id=model_id,
    )
```
**Applied to `mcp_client.py`:** mirror this shape exactly for the connect sequence, but use RESEARCH.md's already-verified `classify_mcp_error()` (catches `TimeoutError`, `FileNotFoundError`/`OSError`, `(ExceptionGroup, BaseExceptionGroup)`, fallback `Exception`) instead of the `httpx.*` exception types — return a typed `McpConnectResult(status=..., code=..., message=..., detail=..., stderr_tail=...)` schema (add to `agent/schemas.py` alongside `ModelLoadResult`) rather than raising `HTTPException` directly from this module — matches this codebase's existing "service layer returns typed result, endpoint layer raises HTTP" split seen in `llm_client.py` (`main.py`'s `/lm-studio/load-model` endpoint wraps `ModelLoadResult` into an HTTP response; do the same for `McpConnectResult`).

**In-memory registry + cleanup pattern** (`agent/state.py`, full file):
```python
"""In-memory process state shared by REST and WebSocket handlers."""

import asyncio
from typing import Any

active_streams: dict[int, Any] = {}
ws_rate_limiter: dict[int, list[float]] = {}
chat_locks: dict[int, asyncio.Lock] = {}


def cleanup_chat_caches(chat_id: int) -> None:
    """Remove in-memory state keyed by the deleted chat."""
    active_streams.pop(chat_id, None)
    ws_rate_limiter.pop(chat_id, None)
    chat_locks.pop(chat_id, None)
```
**Applied to `mcp_client.py`:** keep the live-session registry (`dict[tuple[int, int], SessionHandle]`) and its owner-`asyncio.Task`s either in this same module (co-located with the connect logic, since RESEARCH.md's owner-task pattern requires the registry write and the task creation to happen together) or import `agent/state.py`-style bare dicts if the planner prefers strict separation — either is consistent with the existing single-process-dict convention. Provide a `cleanup_server(user_id, server_id)` function shaped exactly like `cleanup_chat_caches` (signals `close_requested`, awaits `closed`, pops both the task dict and the registry dict) for reuse across Disconnect / delete / edit / disable / lifespan-shutdown per D-06.

**Owner-task pattern (from RESEARCH.md, hands-on verified — copy near-verbatim):**
```python
class SessionHandle:
    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.ready = asyncio.Event()
        self.close_requested = asyncio.Event()
        self.closed = asyncio.Event()
        self.error: Exception | None = None

async def owner_task(handle: SessionHandle, params: StdioServerParameters, errfile) -> None:
    try:
        async with stdio_client(params, errlog=errfile) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                handle.session = session
                handle.ready.set()
                await handle.close_requested.wait()   # parked until Disconnect
    except Exception as exc:
        handle.error = exc
        handle.ready.set()
    finally:
        handle.closed.set()
```

---

### `agent/schemas.py` (schema, request-response) — add Mcp* models

**Analog:** `agent/schemas.py::GlobalInvariantCreate/Update/Response` + `ChatInvariant*` (lines 319-378)

```python
class GlobalInvariantCreate(BaseModel):
    """Request body for creating a global invariant (D-01, D-02)."""

    title: str = Field(min_length=1, max_length=INVARIANT_TITLE_MAX_LENGTH)
    rule_text: str = Field(min_length=1, max_length=INVARIANT_RULE_MAX_LENGTH)


class GlobalInvariantUpdate(BaseModel):
    """Partial update for a global invariant (D-04, full CRUD)."""

    title: Optional[str] = Field(
        default=None, min_length=1, max_length=INVARIANT_TITLE_MAX_LENGTH,
    )
    rule_text: Optional[str] = Field(
        default=None, min_length=1, max_length=INVARIANT_RULE_MAX_LENGTH,
    )


class GlobalInvariantResponse(BaseModel):
    """Serialized global invariant returned to the client."""

    id: int
    title: str = Field(max_length=INVARIANT_TITLE_MAX_LENGTH)
    rule_text: str = Field(max_length=INVARIANT_RULE_MAX_LENGTH)
    created_at: datetime
    updated_at: datetime
```
**Applied to `agent/schemas.py`:** `McpServerCreate`/`McpServerUpdate` (name/command/args: list[str]/env: dict[str,str]/cwd/enabled, all `Optional` on Update for partial-PUT via `model_dump(exclude_unset=True)`), `McpServerResponse` (id, name, command, args, env — **masked**, cwd, enabled, status, error_code, error_message, stderr_tail, server_info, tools — status/serverInfo/tools fields populated only when connected, matching how `ModelLoadResult` composes status+message+model_id). Also add `ModelLoadResult`-style `McpConnectResult` for the service-layer return type, and `McpToolResponse` (name, description, input_schema: dict) mirroring `mcp.types.Tool`'s shape confirmed in RESEARCH.md.

---

### `agent/main.py` (controller, CRUD + request-response) — add `/api/v1/mcp/servers...` routes

**Analog:** chat-invariant CRUD block (lines 586-666) for list/create/update/delete, plus the `_get_chat_invariant_or_404` ownership-check helper (lines 128-143)

**404-not-403 ownership-check helper pattern** (lines 128-143):
```python
async def _get_chat_invariant_or_404(
    session: AsyncSession,
    chat_id: int,
    invariant_id: int,
) -> ChatInvariant:
    """Load a per-chat invariant belonging to chat_id or raise HTTP 404 (never 403)."""
    invariant = await invariants.get_chat_invariant(session, invariant_id)
    if invariant is None or invariant.chat_id != chat_id:
        logger.warning(
            "invariant_access_denied", chat_id=chat_id, invariant_id=invariant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invariant {invariant_id} not found",
        )
    return invariant
```
**Applied to `mcp` endpoints:** write `_get_mcp_server_or_404(session, user_id, server_id)` checking `row.user_id == current_user.id`, same 404-never-403 shape.

**List/Create/Update/Delete endpoint pattern** (lines 586-666, adapted to `user_id` scope like `/api/v1/profile`, lines 811-830, for the "no `chat_id` in path" shape):
```python
@app.get(
    "/api/v1/chats/{chat_id}/invariants",
    response_model=list[ChatInvariantResponse],
)
async def get_chat_invariants(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ChatInvariantResponse]:
    """Return this chat's per-chat invariants (ownership-checked, D-05)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    rows = await invariants.list_chat_invariants(session, chat_id)
    return [await _chat_invariant_to_response(session, row) for row in rows]


@app.post(
    "/api/v1/chats/{chat_id}/invariants",
    response_model=ChatInvariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_invariant_endpoint(...):
    ...


@app.delete(
    "/api/v1/chats/{chat_id}/invariants/{invariant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_chat_invariant_endpoint(
    chat_id: int,
    invariant_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a per-chat invariant (ownership-checked, D-05)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    await _get_chat_invariant_or_404(session, chat_id, invariant_id)
    await invariants.delete_chat_invariant(session, invariant_id)
```
**Applied:** `GET/POST /api/v1/mcp/servers`, `PUT/DELETE /api/v1/mcp/servers/{id}` scoped by `current_user.id` directly (no chat in path — mirrors `/api/v1/profile`'s single-`Depends(get_current_user)` shape, not the chat-scoped double-check). `PUT` (edit) and `DELETE` must call `mcp_client.cleanup_server(user_id, server_id)` **before** doing the DB update/delete when the server is currently connected (D-08) — same ordering as `main.py::delete_chat` calling `cleanup_chat_caches(chat_id)` **after** `session.commit()` (lines 510-516): follow that same "commit DB first, then clean in-memory state" order unless D-08's "auto-disconnect on edit" semantics require disconnecting first (disconnect is idempotent and side-effect-free on the DB, so either order is safe — disconnect first is simplest to reason about).

**Connect/Disconnect/Status endpoints** (no direct analog — closest precedent is the LM Studio load/unload endpoints, which the researcher's Architectural Responsibility Map explicitly maps to `POST /api/v1/lm-studio/load-model` / `unload-model/{model_id}`). Look at `main.py`'s LM Studio load-model endpoint (search `lm-studio/load-model` in `main.py`) for the request/response shape: request body → `LMStudioClient.load_model(...)` → typed `ModelLoadResult` → HTTP response, no manual exception-to-HTTPException mapping needed because the service layer never raises. Mirror this for `POST /api/v1/mcp/servers/{id}/connect` → `mcp_client.connect_server(...)` → `McpConnectResult` → response (map `status=ERROR` results to a 200 response body carrying the error code/message per D-17, not to an HTTP error status, since the UI needs to render the row inline — confirm this choice against D-17's "Tests assert on the code" language, which implies the code lives in the JSON body, not just the HTTP status).

**Lifespan cleanup hook** (lines 318-326):
```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize the database on startup and dispose the engine on shutdown."""
    logger.info("agent_starting")
    await init_db()
    yield
    logger.info("agent_shutting_down")
    await engine.dispose()
```
**Applied:** insert an `await mcp_client.cleanup_all_sessions()` call between `logger.info("agent_shutting_down")` and `await engine.dispose()`, iterating the registry and awaiting every handle's `closed` event (per D-06 and D-19 — must never leave orphaned `filesystem.exe` processes).

---

### `scripts/mcp_list_tools.py` (utility/CLI, request-response) — new file, new directory

**Analog:** `run.py` (print()-based CLI banner, the CLAUDE.md-documented `print()` exception) + `migrate_strategies.py` (one-shot `asyncio.run()` script importing app modules directly, no HTTP)

**Script shape** (`migrate_strategies.py`, full file):
```python
"""Migrate old strategy values to new enum."""
import asyncio

from sqlalchemy import text

from shared.database import async_session_factory
from shared.models import ContextStrategy


async def migrate() -> None:
    """Migrate 'branching' strategy to 'sliding' for all existing settings."""
    ...
    print(f"Migrated {pending} settings records from 'branching' to 'sliding'")


if __name__ == "__main__":
    asyncio.run(migrate())
```
**Applied to `scripts/mcp_list_tools.py`:** `import sys; import asyncio; from agent.mcp_client import connect_once_and_list` (D-20's required single-implementation reuse — this script must call the *same* function the REST `/connect` path calls, not a separate copy), parse `sys.argv[1:]` as `<command> [args...]` manually (no `argparse` subcommands needed — just `command = sys.argv[1]`, `args = sys.argv[2:]`), print serverInfo header + each tool (mirrors `run.py`'s bootstrap-credentials `print()` block, lines 46-53, for the "banner"-style formatting), and on failure print the error code + message + stderr tail to `sys.stderr` and `sys.exit(1)` (per D-20). `print()` here is the CLAUDE.md-documented exception (same as `run.py`) — do not use `structlog`/`logger` for this script's user-facing output, though `agent.mcp_client` itself (the imported logic) still must use `structlog` internally.

---

### `ui/static/index.html` (component/markup) — add MCP section inside `#settings-modal`

**Analog:** `#invariants-panel` (collapsible section with header count badge + fold-toggle + inline add-form, lines 115-154) and `#profile-panel` (simpler collapsible form, lines 77-104)

**Collapsible section header pattern** (lines 105-114, `#task-panel`):
```html
<div id="task-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-72">
    <h3 class="text-slate-300 font-semibold mb-2 flex items-center justify-between">
        <span>Задачи <span id="task-count" class="text-white font-semibold">0</span></span>
        <button type="button" data-fold-toggle="task-panel-body" aria-expanded="false"
            title="Свернуть/развернуть" class="text-slate-400 hover:text-white">▸</button>
    </h3>
    <div id="task-panel-body" class="hidden">
        <div id="task-list" class="space-y-2"></div>
    </div>
</div>
```
**Applied:** per UI-SPEC's Component Inventory, the MCP section goes **inside `#settings-modal`'s `<form id="settings-form">`** (D-01), not the sidebar — but reuse the exact `data-fold-toggle` / `▸`/`▾` / `aria-expanded` markup shape for the per-server "Инструменты (n)" collapsible and each per-tool collapsible row (D-03/D-04). Text inputs/textareas reuse the exact class string from `#settings-system-prompt`/`#invariant-global-rule`: `class="w-full rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"` (input) / add `resize-none` for textareas (args/env fields).

---

### `ui/static/app.js` (component/frontend controller, CRUD + request-response) — add MCP state/render/CRUD functions

**Analog:** the global-invariant block (`renderInvariantsPanel`, `saveGlobalInvariant`, `deleteGlobalInvariant`, lines 663-885) for CRUD-list-with-edit-in-place, plus `renderTaskPanel`/`pauseTask`/`cancelTask` (lines 391-521) for status-badge + per-row-action-button rendering, plus `setupFoldablePanels` (lines 557-568) for the fold-toggle wiring.

**List-render-with-actions pattern** (lines 685-725, `renderInvariantsPanel`'s per-item card):
```javascript
items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'rounded-lg bg-slate-800 px-2 py-1';

    const titleEl = document.createElement('div');
    titleEl.className = 'text-sm font-semibold text-slate-300';
    titleEl.textContent = item.title;
    card.appendChild(titleEl);
    ...
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'text-slate-400 hover:text-white';
    editBtn.textContent = 'Изменить';
    editBtn.addEventListener('click', () => { ... });
    actionsEl.appendChild(editBtn);

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'text-red-400 hover:text-red-300';
    deleteBtn.textContent = 'Удалить';
    deleteBtn.addEventListener('click', () => {
        deleteGlobalInvariant(item.id).catch((err) => showToast(err.message, 'error'));
    });
    actionsEl.appendChild(deleteBtn);

    card.appendChild(actionsEl);
    listEl.appendChild(card);
});
```

**Status-badge pattern** (lines 47-53, `TASK_STATE_BADGE_CLASSES`, applied at line 426):
```javascript
const TASK_STATE_BADGE_CLASSES = {
    planning: 'text-slate-400',
    execution: 'text-sky-400',
    validation: 'text-amber-400',
    done: 'text-emerald-400',
    cancelled: 'text-red-400',
};
...
stateChip.className = `rounded px-1 ${TASK_STATE_BADGE_CLASSES[task.state]}`;
stateChip.textContent = TASK_STATE_LABELS[task.state];
```
**Applied:** define `MCP_STATUS_BADGE_CLASSES` per UI-SPEC's table (`не подключён`→`text-slate-400`, `подключение…`→`text-sky-400`, `подключено`→`text-emerald-400`, `ошибка`→`text-red-400`) — identical shape.

**Confirm-then-DELETE pattern** (lines 848-857, `deleteGlobalInvariant`):
```javascript
async function deleteGlobalInvariant(invariantId) {
    if (!confirm('Удалить этот инвариант? Это действие нельзя отменить.')) return;
    try {
        await apiFetch(`/api/v1/invariants/${invariantId}`, { method: 'DELETE' });
        showToast('Инвариант удалён', 'success');
        await loadInvariants();
    } catch (err) {
        showToast('Не удалось удалить инвариант. Проверьте соединение и попробуйте снова.', 'error');
    }
}
```
**Applied:** `deleteMcpServer(serverId, name, connected)` — branch the `confirm()` copy per UI-SPEC (plain vs "will be disconnected first" wording), same try/catch/showToast/reload shape.

**Fold-toggle wiring** (lines 557-568, generic — no changes needed, new elements just need `data-fold-toggle` attributes and this function is called once at startup and picks up all matching elements):
```javascript
function setupFoldablePanels() {
    document.querySelectorAll('[data-fold-toggle]').forEach((btn) => {
        const targetId = btn.dataset.foldToggle;
        const body = document.getElementById(targetId);
        if (!body) return;
        btn.addEventListener('click', () => {
            const collapsed = body.classList.toggle('hidden');
            btn.textContent = collapsed ? '▸' : '▾';
            btn.setAttribute('aria-expanded', String(!collapsed));
        });
    });
}
```
Caveat: this queries `[data-fold-toggle]` once at bind time — if the MCP tool rows are rendered **dynamically** after `bindEvents()`/`setupFoldablePanels()` already ran (they are, since tools only appear after a successful Connect), the per-tool fold-toggle buttons need either (a) event delegation on a parent container instead of this per-button-listener approach, or (b) calling a per-row toggle-wiring step inside the render function itself. Flag this for the planner — reusing `setupFoldablePanels()` verbatim will not wire up dynamically-inserted tool rows.

**DOMPurify sanitize-then-insert pattern (for the raw-JSON toggle and any server-provided string going into innerHTML)** (line 174):
```javascript
content.innerHTML = isUser ? DOMPurify.sanitize(msg.content) : renderMarkdown(msg.content);
```
**Applied:** for the `inputSchema` JSON `<pre>` block and the stderr-tail `<pre>` block, build the pretty-printed string with `JSON.stringify(schema, null, 2)` / the raw stderr text, then `pre.innerHTML = DOMPurify.sanitize(escapedText)` — or simpler and equally compliant with UI-SPEC's "text-inserted... never raw HTML": use `pre.textContent = text` directly (textContent needs no sanitization since it never parses HTML) for the JSON/stderr blocks specifically, reserving `DOMPurify.sanitize()` + `innerHTML` for any field that legitimately needs markup. Tool descriptions/param descriptions (D-03) are plain user-facing text — prefer `textContent` (matches `renderInvariantsPanel`'s `ruleEl.textContent = item.rule_text`, line 696) over `innerHTML`+DOMPurify unless the UI-SPEC explicitly calls for HTML rendering (it does not — UI-SPEC just requires sanitization as a safety net, `textContent` satisfies that trivially and is the dominant existing pattern for list-row content).

**`apiFetch` wrapper (reuse verbatim, no changes)** (lines 72-89):
```javascript
async function apiFetch(path, options = {}) {
    const resp = await fetch(`${AGENT_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        credentials: 'include',
        ...options,
    });
    if (resp.status === 401) {
        window.location.href = '/static/login.html?expired=1';
        return;
    }
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body.detail || resp.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (resp.status === 204) return null;
    return resp.json();
}
```
All new MCP fetch calls (`loadMcpServers`, `saveMcpServer`, `deleteMcpServer`, `connectMcpServer`, `disconnectMcpServer`) go through this unchanged.

---

### `tests/test_mcp_config_api.py` (test, CRUD) — new file

**Analog:** `tests/test_profile_api.py` (auth-required 401 check, round-trip PUT/GET, partial-update check, oversized-field 422 check)

```python
@pytest.mark.asyncio
async def test_get_profile_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/profile")
    assert resp.status_code == 401
```
Also reference `tests/test_invariants_api.py` / the chat-invariant test file for list/create/update/delete-with-404 coverage and `second_authenticated_client` (conftest.py, lines 126-153) for cross-user IDOR tests — every McpServerConfig endpoint must be checked against `second_authenticated_client` per the CLAUDE.md `user_id`-scoping requirement (mirrors the pattern implied by `_get_chat_invariant_or_404`'s "never 403" 404 design).

### `tests/test_mcp_client.py` (test, event-driven) — new file

**Analog:** `tests/test_lm_studio_client.py` (fixture-per-test client, success/timeout/error-path coverage — structurally the closest existing test even though the transport differs: HTTP+respx here vs. stdio+real-or-fixture-subprocess for MCP)

```python
@pytest.fixture
def lm_client() -> LMStudioClient:
    """Fresh LM Studio client for each test."""
    return LMStudioClient(base_url=BASE_URL)


@respx.mock
@pytest.mark.asyncio
async def test_load_model_success(lm_client: LMStudioClient) -> None:
    """Successful load returns LOADED status."""
    ...
    result = await lm_client.load_model("test-model", gpu_offload=0, context_length=4096)
    assert result.status == ModelLoadStatus.LOADED
```
**Applied:** `respx` cannot mock a stdio subprocess — instead spawn `tests/fixtures/mcp_stdio_server.py` (a tiny `mcp`-SDK-based Python stdio server, portable/Windows-CI-safe) as the success-path and most failure-path target; use `if shutil.which(...)`/an explicit path-exists skip guard to additionally test against the real `filesystem.exe` when present (planner's call per RESEARCH.md's Recommended Project Structure). Assert on the returned `McpConnectResult.status`/`.code` the same way `test_load_model_success` asserts on `result.status == ModelLoadStatus.LOADED` — never assert on raw exception types leaking out of `mcp_client.connect_server`.

---

## Shared Patterns

### Authentication
**Source:** `agent/dependencies.py::get_current_user` (lines 22-49)
**Apply to:** Every new `/api/v1/mcp/servers...` REST endpoint — `current_user: User = Depends(get_current_user)`, exactly as every other authenticated endpoint in `agent/main.py` does. No new auth code needed.

### Ownership check (404-not-403)
**Source:** `agent/main.py::_get_chat_invariant_or_404` (lines 128-143)
**Apply to:** A new `_get_mcp_server_or_404(session, user_id, server_id)` helper — load by id, compare `row.user_id != current_user.id`, raise 404 (never 403) on mismatch, matching this codebase's established anti-enumeration convention.

### Commit/rollback try/except
**Source:** every write function in `agent/invariants.py`, `agent/profile.py`, `agent/tasks.py` (e.g. `agent/invariants.py::create_global`, lines 32-47)
**Apply to:** every write in `agent/mcp_config.py` (`create_server`, `update_server`, `delete_server`):
```python
session.add(row)
try:
    await session.commit()
    await session.refresh(row)
except Exception:
    await session.rollback()
    raise
```

### In-memory per-process registry + cleanup-on-delete
**Source:** `agent/state.py` (whole file) + `agent/main.py::delete_chat` calling `cleanup_chat_caches(chat_id)` (lines 503-516)
**Apply to:** `agent/mcp_client.py`'s session registry, cleaned up from the Disconnect endpoint, the server-delete endpoint, the server-update endpoint (D-08 auto-disconnect), the disable-toggle path (D-13), and `agent/main.py::lifespan`'s shutdown branch (D-06).

### Typed-result-not-raised-exception service boundary
**Source:** `agent/llm_client.py::LMStudioClient.load_model`/`unload_model` returning `ModelLoadResult` (never raising `httpx` exceptions to the caller)
**Apply to:** `agent/mcp_client.py::connect_server`/`disconnect_server`/`get_status` all return typed `McpConnectResult`/`McpServerResponse` objects; the REST endpoint layer in `agent/main.py` inspects `.status`/`.code` to decide the HTTP response shape, exactly like the existing `/api/v1/lm-studio/load-model` endpoint does with `ModelLoadResult`.

### Frontend: fetch wrapper, toast, fold-toggle, confirm-then-delete
**Source:** `ui/static/app.js::apiFetch` (lines 72-89), `showToast` (lines 57-70), `setupFoldablePanels` (lines 557-568), `deleteGlobalInvariant` (lines 848-857)
**Apply to:** every new MCP frontend function — no new low-level frontend infrastructure needed, only new render/CRUD functions built on top of these existing primitives (see per-file pattern assignment above for the dynamic-fold-toggle caveat).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `tests/fixtures/mcp_stdio_server.py` | test fixture | event-driven | No existing stdio-server (or any subprocess-server) test fixture in this codebase — RESEARCH.md's Code Examples section (owner-task pattern, `mcp` SDK server-side API) is the primary reference for this file instead of a codebase analog. |
| Env-value "leave untouched unless retyped" merge logic (D-12) in `agent/mcp_config.py::update_server`) | logic within a CRUD file | CRUD | No existing partial-dict-merge-preserving-unset-keys pattern in this codebase — every existing `update_*` function does whole-field replacement (`setattr(row, field, value)`), never a merge-inside-a-JSON-blob. Plan this as new logic, not a copy. |

**Caveat on D-06's "user deletion (cascade)" cleanup requirement:** this codebase currently has **no `DELETE /api/v1/users/{id}` endpoint** (`grep` for `delete_user`/`users/{` in `agent/main.py` returns nothing) — only a `User`/`Session` FK cascade at the DB level exists. Since the MCP session registry is in-memory (not DB-backed), a future user-delete endpoint would need to call `mcp_client`'s per-user cleanup explicitly (the DB cascade alone cannot reach in-memory state) — flag this as a forward-looking note for the planner rather than a file to modify in this phase, since no such endpoint exists yet to hook into.

## Conventions

Convention derivation via the shared `gsd-tools.cjs verify conventions --derive` module was **skipped** — no `gsd-plugin` installation (`$CLAUDE_PLUGIN_ROOT` unset, no `~/.claude/plugins/cache/gsd-plugin/bm/*/` directory found on this machine) was available to run it. The table below is manually derived from the files read during this pattern-mapping pass (agent/main.py, agent/schemas.py, agent/invariants.py, agent/profile.py, agent/memory.py, agent/llm_client.py, agent/dependencies.py, agent/state.py, shared/models.py, shared/config.py, ui/static/app.js, ui/static/index.html) — consistent with CLAUDE.md's already-documented conventions section, not a substitute for the tool's entropy/share numbers.

| Axis | Dominant | Share (observed) | Status |
|------|----------|-------------------|--------|
| File-name casing (Python) | `lowercase_with_underscores.py` | 12/12 files read | named contract |
| Identifier casing (Python) | `snake_case` functions/vars, `PascalCase` classes | uniform across every file read | named contract |
| Export style (Python) | plain module-level `def`/`class`, no `__all__`, direct `from module import name` | uniform across every file read | named contract |
| Import style (Python) | stdlib → third-party → local, one block each, no aliasing except `Column`/`SAEnum` disambiguation in `shared/models.py` | uniform across every file read | named contract |
| Frontend identifier casing (JS) | `camelCase` functions/vars, `SCREAMING_SNAKE_CASE` module-level constant maps (`TASK_STATE_BADGE_CLASSES`) | uniform in `app.js` | named contract |
| Frontend DOM-id casing (HTML/JS) | `kebab-case` (`#settings-modal`, `#invariant-global-list`) | uniform in `index.html`/`app.js` | named contract |

**Contested hotspots (author's choice):** none identified within the files read for this phase — every axis above showed a single dominant style at effectively 100% share, consistent with CLAUDE.md's explicit, already-documented convention list (this project has no linter/formatter, so consistency here is a matter of manual discipline, not tooling, but the discipline is evidently well-maintained). This phase's new files (`agent/mcp_config.py`, `agent/mcp_client.py`, `scripts/mcp_list_tools.py`, `tests/fixtures/mcp_stdio_server.py`) should simply continue the dominant style — there is no repo-wide contested split analogous to a CJS/ESM dual-resolver in this codebase to reconcile against.

## Metadata

**Analog search scope:** `agent/` (all modules), `shared/models.py`, `shared/config.py`, `ui/static/index.html`, `ui/static/app.js`, `tests/` (conftest.py + 3 representative test files), `run.py`, `migrate_strategies.py`, `requirements.txt`
**Files scanned:** ~18 (full or targeted reads)
**Pattern extraction date:** 2026-09-23
