# Phase 11: Edit and delete long-term memory entries via UI (Day 21) - Pattern Map

**Mapped:** 2026-10-02
**Files analyzed:** 11 (4 backend, 1 frontend, 3 test files, 4 docs)
**Analogs found:** 11 / 11 (docs use existing sections as format analogs)

Locate JS edit points by function name, not line number (Phases 9 and 10 shift `ui/static/app.js`).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `agent/memory.py` (+get/update/delete, `MemoryKeyConflictError`) | service (CRUD layer) | CRUD | `agent/memory.py::save_long_term_memory` + `agent/mcp_config.py::delete_server` | exact |
| `agent/schemas.py` (+`LongTermMemoryUpdate`) | model (request schema) | request-response | `McpServerUpdate` + `_validate_mcp_text` | exact |
| `agent/main.py` (PUT/DELETE `/api/v1/memory/long-term/{entry_id}`) | controller (route) | request-response | `update_mcp_server` / `delete_mcp_server` + `_get_mcp_server_or_404` | exact |
| `ui/static/app.js` (`renderMemoryEntries` editable, edit state, save/delete) | component | request-response + DOM render | invariants card rendering (`Изменить`/`Удалить`), `deleteGlobalInvariant`, `saveGlobalInvariant` | exact |
| `tests/test_memory_api.py` (extend) | test | request-response | same file + `tests/test_mcp_origin.py` | exact |
| `tests/test_memory.py` (extend) | test | CRUD | same file | exact |
| `tests/test_context_engine_memory.py` (extend) | test | transform | same file `test_long_term_memory_row_appears_in_prompt` | exact |
| `docs/API_SPEC.md`, `TESTING_GUIDE.md`, `USER_GUIDE.md`, `ARCHITECTURE.md` | docs | n/a | existing sections in each file (copy heading style) | role-match |

## Pattern Assignments

### `agent/memory.py` (service, CRUD)

**Analog:** `agent/memory.py` itself (lines 1-12 imports/logger, 85-135 `save_long_term_memory`) and `agent/mcp_config.py::delete_server` (172-182).

**Imports already present** (lines 3-12): `datetime, timezone`, `IntegrityError`, `select`, `AsyncSession`, `get_logger`, `LongTermMemory`. No new imports required.

**Query style to copy** (lines 92-95):
```python
result = await session.exec(
    select(LongTermMemory).where(LongTermMemory.user_id == user_id, LongTermMemory.key == key),
)
row = result.first()
```
`get_long_term_memory` must filter by BOTH `id` and `user_id` in the SQL (404 and IDOR rule).

**Commit/rollback/refresh/log convention** (lines 100-108):
```python
session.add(row)
try:
    await session.commit()
except Exception:
    await session.rollback()
    raise
await session.refresh(row)
logger.info("long_term_memory_saved", user_id=user_id, key=key)
```
For update: also `except IntegrityError` before the generic branch (rollback, then `raise MemoryKeyConflictError(key) from None`), as in lines 114-115. Log `user_id`/`entry_id` only, never `value`.

**Delete pattern** (`agent/mcp_config.py` 172-182):
```python
try:
    await session.delete(row)
    await session.commit()
except Exception:
    await session.rollback()
    raise
logger.info("mcp_server_deleted", user_id=user_id, server_id=server_id)
```
`delete_long_term_memory(session, user_id, entry_id) -> bool` (False when not found). Do NOT reuse `save_long_term_memory` for edits (upsert by key would duplicate on rename). The RESEARCH sketch (lines 114-144) is consistent with this file.

---

### `agent/schemas.py` (model, request-response)

**Analog:** `McpServerUpdate` (lines ~245-272) and `_validate_mcp_text` (183-190).

**Imports present** (line 8): `BaseModel, Field, field_validator, model_validator`; `Optional` from typing (line 6). Constants `MEMORY_KEY_MAX_LENGTH = 200`, `MEMORY_VALUE_MAX_LENGTH = 50_000` at lines 21-22.

**Strip/blank validator pattern:**
```python
def _validate_mcp_text(value: str | None) -> str | None:
    """Strip a name/command and reject it when nothing is left."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped
```
```python
class McpServerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=MCP_NAME_MAX_LENGTH)
    @field_validator("name", "command")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        """Strip and reject blank name/command when supplied."""
        return _validate_mcp_text(value)
```
Apply: strip + non-blank for `key`; for `value` only reject whitespace-only (do NOT strip internal whitespace/newlines). Add `@model_validator(mode="after")` requiring at least one field (style at `ScheduleTaskArgs._check_schedule_fields`, ~line 526: `def _check_...(self) -> "Cls": ... raise ValueError(...) ... return self`). Place near `MemoryEntryResponse` (line ~340). Single-line docstrings required.

---

### `agent/main.py` (controller, request-response)

**Analog:** `update_mcp_server` / `delete_mcp_server` (~1065-1119) and `_get_mcp_server_or_404` (182-195). Insert next to `get_chat_memory` (line 652).

**Imports:** `require_json_content_type` already imported (line 18; `require_allowed_origin` is imported the same way from `agent.dependencies`); `memory` module already imported (line 56). Add `LongTermMemoryUpdate` to the `agent.schemas` import block.

**PUT decorator + signature:**
```python
@app.put(
    "/api/v1/mcp/servers/{server_id}",
    response_model=McpServerResponse,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def update_mcp_server(
    server_id: int,
    body: McpServerUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
```

**DELETE decorator:**
```python
@app.delete(
    "/api/v1/mcp/servers/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_mcp_server(...) -> None:
```

**404 pattern** (never 403, log warning on denial):
```python
logger.warning("mcp_server_access_denied", user_id=user_id, server_id=server_id)
raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server {server_id} not found")
```

**Response construction** (get_chat_memory, 668): `MemoryEntryResponse(id=row.id, key=row.key, value=row.value, updated_at=row.updated_at)`.

409 mapping: `except memory.MemoryKeyConflictError: raise HTTPException(status.HTTP_409_CONFLICT, detail="Запись с таким ключом уже существует") from None`. The endpoint skeleton in RESEARCH lines 245-275 is correct as written.

---

### `ui/static/app.js` (component, DOM render + request-response)

**Analog 1 - `renderMemoryEntries(container, entries)`** (to modify; shared by working and long-term lists, called from `renderMemoryPanel` which passes `data.working` and `data.long_term`). Current body: `container.replaceChildren()`, empty-state `'—'` div, per-entry `div.rounded-lg.bg-slate-800.px-2.py-1` with `keyEl` (`text-slate-300 font-semibold`) and `valueEl` (`text-slate-400 truncate`, 160-char truncation, `title = entry.value`). All via `textContent` (no innerHTML). Add third param `{ editable } = {}`; in `renderMemoryPanel` pass `{ editable: true }` only for `longTermEl`.

**Analog 2 - action buttons** (invariants card, ~706-728):
```javascript
const actionsEl = document.createElement('div');
actionsEl.className = 'flex items-center gap-2 mt-1';
const editBtn = document.createElement('button');
editBtn.type = 'button';
editBtn.className = 'text-slate-400 hover:text-white';
editBtn.textContent = 'Изменить';          // use 'Редактировать'
editBtn.addEventListener('click', () => { state.editingGlobalInvariantId = item.id; ... });
actionsEl.appendChild(editBtn);
const deleteBtn = document.createElement('button');
deleteBtn.type = 'button';
deleteBtn.className = 'text-red-400 hover:text-red-300';
deleteBtn.textContent = 'Удалить';
deleteBtn.addEventListener('click', () => {
    deleteGlobalInvariant(item.id).catch((err) => showToast(err.message, 'error'));
});
```

**Analog 3 - delete flow** (`deleteGlobalInvariant`, ~855-864):
```javascript
async function deleteGlobalInvariant(invariantId) {
    if (!confirm('Удалить этот инвариант? Это действие нельзя отменить.')) return;
    try {
        await apiFetch(`/api/v1/invariants/${invariantId}`, { method: 'DELETE' });
        showToast('Инвариант удалён', 'success');
        await loadInvariants();
    } catch (err) {
        showToast('Не удалось удалить инвариант. ...', 'error');
    }
}
```
Apply with `loadChatMemory(state.currentChatId)` (guard for null) as the refresh; show `err.message` for server detail.

**Analog 4 - save flow** (`saveGlobalInvariant`, ~866-892): trim, empty check with toast `'Заполните ключ и значение'`, `apiFetch(url, { method: 'PUT', body: JSON.stringify(body) })`, success toast, reload panel, catch -> `showToast(..., 'error')`. Use `err.message` for 409/422 detail and keep the form open on error. Disable Save while in flight.

**Refresh hook** (`loadChatMemory`, 287-295): `state.lastMemory = data; renderMemoryPanel();` -- it re-runs after each WS `done` and chat switch and `renderMemoryEntries` calls `container.replaceChildren()`, so keep draft in `state.editingMemory = { id, key, value }` (updated on `input`) and render the edit form from it when `entry.id` matches; drop it if the entry vanished. Seed textarea from full `entry.value` (not the truncated string). Use `.value`/`textContent` only; no `innerHTML`. No modal, no keydown/Escape handler (Phase 10 policy; `tests/test_modal_close_policy.py`).

---

### `tests/test_memory_api.py` (test, request-response)

**Analog:** same file. Header/imports (lines 1-8):
```python
import pytest
from httpx import AsyncClient
from agent import memory
from shared.database import async_session_factory
```
Seeding pattern (lines 62-63):
```python
async with async_session_factory() as session:
    await memory.save_long_term_memory(session, user_id, "profile_name", "Alex")
```
Fixtures: `client` (unauthenticated, 401 test at lines 10-14), `authenticated_client` (`.seeded_user_id`), `second_authenticated_client` (cross-user 404 pattern, lines 72-88: assert `status_code == 404` and secret not in `resp.text`). Style: `@pytest.mark.asyncio`, one-line docstrings, `-> None`. For Origin/content-type tests copy the approach from `tests/test_mcp_origin.py` (`FOREIGN_ORIGIN = "http://evil.example"`, `headers={"Origin": ...}`, `CORS_ORIGINS` from `agent.state`) if desired. Ids for PUT/DELETE come from `GET /api/v1/chats/{id}/memory` `long_term[0]["id"]`.

### `tests/test_memory.py` (test, CRUD)
**Analog:** same file (`test_long_term_memory_isolated_per_user` line 78 for scoping). Add helper-level tests: update keeps `created_at`, bumps `updated_at`; wrong user returns None / False; `MemoryKeyConflictError` on duplicate rename; renaming to own key OK.

### `tests/test_context_engine_memory.py` (test, transform)
**Analog:** `test_long_term_memory_row_appears_in_prompt` (lines 56-72):
```python
user_id = authenticated_client.seeded_user_id
chat_id = await _create_chat(user_id, "Long term chat")
async with async_session_factory() as session:
    await memory.save_long_term_memory(session, user_id, "user_name", "Alex")
    prompt = await build_system_prompt(session, chat_id)
    assert "Long-term memory (persists across all your chats)" in prompt
```
Extend: after `update_long_term_memory`/`delete_long_term_memory`, prompt has new value and not the old; after delete the label is absent. `_create_chat` helper is at lines 14-24.

### Docs (API_SPEC, TESTING_GUIDE, USER_GUIDE, ARCHITECTURE)
Copy heading/table style of neighbouring sections in each file; content list is in RESEARCH "Docs to sync". Phase 10 also edits TESTING_GUIDE and USER_GUIDE; run Phase 11 after Phase 10 to avoid merge noise.

## Shared Patterns

### User-scoped 404 (no existence leak)
**Source:** `agent/main.py::_get_mcp_server_or_404` (182-195). **Apply to:** PUT and DELETE endpoints; filter by `user_id` in SQL, same 404 for missing and foreign.

### CSRF hardening on cookie-auth mutations
**Source:** `agent/main.py` MCP routes. **Apply to:** PUT (origin + JSON content-type) and DELETE (origin).

### Commit / rollback / logging
**Source:** `agent/memory.py` 100-108. **Apply to:** all new memory helpers; `logger = get_logger(__name__)`, snake_case event keys with `user_id=`/`entry_id=`; never log `value`.

### Frontend toast + confirm + refresh
**Source:** `deleteGlobalInvariant` / `saveGlobalInvariant`. **Apply to:** memory save/delete in `app.js`.

## Conventions

Derivation via `gsd-tools verify conventions` was not run; the observations below come from CLAUDE.md and the files read, so treat the Share/Entropy columns as qualitative.

| Axis | Dominant | Share | Entropy | Status |
|---|---|---|---|---|
| File-name casing | `snake_case.py` (Python), `test_<module>.py` | high (documented) | low | named contract |
| Identifier casing | `snake_case` functions/vars, `PascalCase` classes, `UPPER_CASE` constants (JS: `camelCase` functions) | high | low | named contract |
| Export style | Python: direct module imports, no barrels; JS: global functions in one file | high | low | named contract |
| Import style | stdlib -> third-party -> local, `from agent.x import y` absolute from root | high | low | named contract |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` CJS `require`/`module.exports` vs `sdk/src/**` ESM `import`/`export`) is the prototype intentional split; each half is internally consistent per directory, contested only repo-wide. It does not apply to this phase's Python/vanilla-JS files; match each directory's local style.

## No Analog Found

None. The only novel element is the inline edit form with draft state held in `state.editingMemory`; no existing inline-edit-in-list exists (invariants fill an external form), so use RESEARCH Pattern 2 plus the button/class conventions above.

## Metadata

**Analog search scope:** `agent/main.py`, `agent/memory.py`, `agent/mcp_config.py`, `agent/schemas.py`, `ui/static/app.js`, `tests/test_memory*.py`, `tests/test_context_engine_memory.py`, `tests/test_mcp_origin.py`
**Pattern extraction date:** 2026-10-02
