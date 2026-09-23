# Phase 2: Memory (Day 11) - Pattern Map

**Mapped:** 2026-09-20
**Files analyzed:** 11 (2 new modules, 7 extended modules/files, 2 new test files recommended)
**Analogs found:** 11 / 11

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|---------------|
| `shared/models.py` (add `WorkingMemory`, `LongTermMemory`) | model | CRUD | `shared/models.py::Settings`/`Chat` (same file, same FK-cascade idiom) | exact |
| `agent/tools.py` (new) | service (dispatcher) | event-driven | `agent/context_engine.py::extract_and_update_facts`/`_extract_facts` (anti-pattern to invert) + `agent/ws.py::_handle_chat_message` (the sequential/locked shape to match) | role-match (inverted anti-pattern) |
| `agent/memory.py` (new) | service (CRUD) | CRUD | `agent/context_engine.py::get_effective_settings`/`_facts_target_row` (NULL-fallback + get-or-create idiom) | role-match |
| `agent/llm_client.py::stream_chat`/`_parse_sse_stream` (extend) | service (streaming client) | streaming | same file, same functions (in-place extension) | exact |
| `agent/context_engine.py::build_system_prompt`/`build_llm_context` (extend) | service (context assembly) | transform | same file, same functions (in-place extension) | exact |
| `agent/ws.py::_handle_chat_message` (extend) | controller (WS handler) | event-driven / streaming | same file, same function (in-place extension) | exact |
| `agent/schemas.py` (add tool-arg + memory response models) | model (Pydantic schema) | request-response | `agent/schemas.py::SettingsUpdate`/`SettingsResponse`/`MessagePayload` | exact |
| `agent/main.py` (add `GET /api/v1/chats/{chat_id}/memory`) | route/controller | request-response | `agent/main.py::get_chat_stats` (GET, `_get_chat_or_404` + read-only service call) | exact |
| `ui/static/index.html` (sidebar Memory tab/panel) | component (markup) | request-response | `ui/static/index.html` `#stats-panel` (always-visible, non-modal panel) | role-match |
| `ui/static/app.js` (memory panel fetch/render + optional `tool_call` WS case) | component (fetch+render) | request-response | `app.js::loadChatStats`/`updateStats`/`renderStatsPanel` + `handleWsMessage` switch | exact |
| `tests/test_memory.py` (new) | test | CRUD | `tests/test_scoping.py` (per-user isolation, IDOR 404s) + `tests/test_cascade_delete.py` (FK cascade) | role-match |
| `tests/test_tools.py` (new) | test | event-driven | `tests/test_context_engine.py` (module-level state fixtures, direct-function tests with `async_session_factory`) | role-match |

## Pattern Assignments

### `shared/models.py` — `WorkingMemory`, `LongTermMemory` (model, CRUD)

**Analog:** `shared/models.py::Chat`, `Settings` (lines 20-108, same file)

**FK-cascade pattern to copy exactly** (from `Chat.user_id`, lines 36-43):
```python
user_id: Optional[int] = Field(
    default=None,
    sa_column=Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
    ),
)
```
Never `Field(foreign_key=...)` — CLAUDE.md and RESEARCH.md both call this out as silently ignored for cascade behavior.

**Concrete target schema** (already fully specified in RESEARCH.md's Architecture Patterns section, empirically consistent with the codebase's own idiom — copy verbatim, both tables use `UniqueConstraint` to implement the "overwrite on save" semantics from D-01/D-03 as a DB-level guarantee):
```python
class WorkingMemory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    chat_id: int = Field(
        sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("chat_id", "key", name="uq_working_memory_chat_key"),)


class LongTermMemory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_long_term_memory_user_key"),)
```

**Import needed:** add `UniqueConstraint` to the existing `from sqlalchemy import Column, Enum as SAEnum, ForeignKey, Integer` line (line 7) — do not add a second sqlalchemy import block.

**No migration function needed:** `shared/database.py::init_db()` (lines 177-184) calls `SQLModel.metadata.create_all` unconditionally, which creates any missing table — the `migrate_*` functions in that file are only for altering *existing* tables (see `migrate_add_context_length`, lines 75-94, and `migrate_add_user_id_columns`, lines 97-118, as the analog for *if* a later column addition is ever needed).

---

### `agent/memory.py` (new — service/CRUD)

**Analog:** `agent/context_engine.py::get_effective_settings` (lines 36-57) for the NULL-fallback/get-or-create idiom, and `_facts_target_row` (lines 84-101) for the "find existing row or create" idiom.

**Get-or-create / upsert pattern to copy** (adapt `_facts_target_row`'s shape for `key`-scoped upsert instead of `chat_id`-scoped singleton):
```python
async def _facts_target_row(
    session: AsyncSession,
    chat_id: int,
    settings_row: Settings | None,
) -> Settings:
    """Get or create settings row for facts storage."""
    if settings_row is not None and settings_row.chat_id == chat_id:
        return settings_row
    result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
    row = result.first()
    if row is not None:
        return row
    chat = await session.get(Chat, chat_id)
    new_settings = Settings(chat_id=chat_id, user_id=chat.user_id if chat is not None else None)
    session.add(new_settings)
    await session.commit()
    await session.refresh(new_settings)
    return new_settings
```
For `save_working_memory`/`save_long_term_memory`, mirror this shape: `SELECT ... WHERE chat_id=... AND key=...` (or `user_id=...AND key=...`), then either update `.value`/`.updated_at` on the existing row or `session.add(NewRow(...))`, followed by `await session.commit()` in a try/except that calls `await session.rollback()` on failure (see Shared Patterns below). The `UniqueConstraint` on the table means an `IntegrityError` on the insert path is possible under a race — catch `sqlalchemy.exc.IntegrityError` specifically (see `agent/main.py` import at line 11) and retry as an update, rather than letting the write raise into the dispatcher uncaught.

**Read functions** (`list_working_memory(session, chat_id)`, `list_long_term_memory(session, user_id)`) should mirror `get_effective_settings`'s simple `select(...).where(...)` + `result.all()` shape (`agent/context_engine.py` line 38 pattern, using `.exec()` not raw `.execute()`).

**Naming/module-boundary constraint** (RESEARCH.md, Structure Rationale): `agent/memory.py` must expose only read (`list_*`) and write (`save_*`) functions — no HTTP/WS-specific logic — matching the "thin CRUD layer" role of `agent/context_engine.py`'s settings-access functions, kept separate from the dispatcher exactly as `context_engine.py`'s settings-read functions are separate from `agent/ws.py`'s orchestration.

---

### `agent/tools.py` (new — dispatcher, event-driven)

**Analog (shape to copy):** `agent/ws.py::_handle_chat_message`'s per-chat-locked, single-DB-session, sequential structure (lines 140-250).
**Anti-pattern to explicitly invert:** `agent/context_engine.py::extract_and_update_facts`/`_run_debounced_facts`/`_extract_facts` (lines 372-439) — debounced (`asyncio.create_task` + `asyncio.sleep`), fire-and-forget, outside any lock. Memory tool-call writes must be the structural opposite: synchronous, inside the caller's existing lock/session, never `asyncio.create_task`d.

**Registry + sequential-dispatch pattern** (already concretely specified in RESEARCH.md Pattern 1 — copy verbatim, only the `import json` needs to move to the top per stdlib-first import order convention):
```python
TOOL_REGISTRY: dict[str, ToolHandler] = {}


def register_tool(name: str) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator to register a tool handler by its OpenAI-schema function name."""
    def _wrap(fn: ToolHandler) -> ToolHandler:
        TOOL_REGISTRY[name] = fn
        return fn
    return _wrap


async def dispatch_tool_calls(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Execute tool calls strictly sequentially; never asyncio.gather this."""
    results: list[dict[str, Any]] = []
    for call in tool_calls:  # sequential, NOT gathered -- STATE.md hard constraint
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        handler = TOOL_REGISTRY.get(name)
        if handler is None:
            results.append({"tool_call_id": call["id"], "error": f"unknown tool {name}"})
            continue
        result = await handler(session, user_id, chat_id, args)
        results.append({"tool_call_id": call["id"], "content": json.dumps(result)})
    return results
```

**Error handling to add (not shown in RESEARCH.md's sketch, but required by CLAUDE.md's "no bare except" + V5 input-validation guidance):** wrap `json.loads(call["function"]["arguments"])` in `try/except json.JSONDecodeError` and append a `{"tool_call_id": ..., "error": "malformed arguments"}` result instead of letting the dispatcher raise — mirrors `agent/ws.py`'s existing `try/except ValidationError` pattern around `MessagePayload.model_validate(raw)` (lines 310-319).

**Locking constraint (critical, from STATE.md/CONTEXT.md):** `dispatch_tool_calls` must be called from *inside* `agent/ws.py::_handle_chat_message`'s existing `async with chat_locks[chat_id]:` block (line 148) and passed the *same* `session` already open there — never open a second session or a second lock acquisition.

**Tool schema generation:** use Pydantic `BaseModel.model_json_schema()` per RESEARCH.md's "Don't Hand-Roll" table — define one Pydantic request model per tool (e.g. `SaveWorkingMemoryArgs(BaseModel)` with `key: str`, `content: str`, following the `SettingsUpdate`/`MessagePayload` field-length-limit idiom in `agent/schemas.py`, e.g. `Field(max_length=200)` for `key` and a bounded `content`/`value` length) and derive the `tools=[...]` list from it, rather than hand-writing JSON schema dicts.

---

### `agent/llm_client.py::stream_chat` (extend — streaming)

**Analog:** same file, same function + `_parse_sse_stream` (lines 65-124).

**Current signature to extend:**
```python
async def stream_chat(
    self,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    context_length: int | None = None,
) -> AsyncGenerator[str, None]:
```
Add a `tools: list[dict[str, Any]] | None = None` parameter; only add `"tools": tools` to `payload` (line 78-84) when non-empty, so the wire contract for callers that pass no tools (e.g. `_extract_facts`'s `complete_chat` call) is unchanged.

**SSE delta-accumulation pattern to add** (RESEARCH.md Pattern 2, empirically verified against the live LM Studio instance — copy the accumulate-by-`index` logic, keep `_parse_sse_stream`'s existing `for line in response.aiter_lines()` / `data: ` prefix / `[DONE]` sentinel handling exactly as-is at lines 114-120):
```python
tool_calls_acc: dict[int, dict[str, Any]] = {}
...
for tc_delta in delta.get("tool_calls", []) or []:
    idx = tc_delta["index"]
    acc = tool_calls_acc.setdefault(
        idx, {"id": None, "type": "function", "function": {"name": None, "arguments": ""}},
    )
    if tc_delta.get("id"):
        acc["id"] = tc_delta["id"]
    if fn := tc_delta.get("function"):
        if fn.get("name"):
            acc["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            acc["function"]["arguments"] += fn["arguments"]
if choice.get("finish_reason") == "tool_calls":
    yield {"type": "tool_calls", "tool_calls": list(tool_calls_acc.values())}
```
**Design decision the planner must make explicit:** the existing `stream_chat` yields plain `str` tokens; adding tool-call yields changes its return type to a discriminated dict shape (`{"type": "content"|"tool_calls", ...}`), which is a breaking signature change for every existing caller (`agent/ws.py::_handle_chat_message` line 202, `tests/test_context_engine.py`, any respx-mocked test). RESEARCH.md's Pattern 2 example implies a *new* method (`_parse_sse_stream_with_tools`) — recommend keeping `stream_chat`'s plain-string contract for callers with no `tools`, and only switching to the dict-yield contract when `tools` is non-empty, so `_extract_facts`/other no-tools callers need zero changes.

**Error handling:** keep the existing `except httpx.TimeoutException` (lines 105-107) and `asyncio.CancelledError` (lines 102-104) wrapping unchanged — tool-call accumulation happens entirely inside `_parse_sse_stream`, which is already inside that try block.

---

### `agent/context_engine.py::build_system_prompt` (extend — transform/read-injection)

**Analog:** same file, same function (lines 60-70), extending the existing `Known facts: {json.dumps(facts)}` / `Conversation summary: ...` idiom (lines 66-69).

**Pattern to copy (already fully specified in RESEARCH.md Pattern 3 — matches this function's existing style exactly, do not fork a second context-assembly path):**
```python
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    settings_row = await get_effective_settings(session, chat_id)
    parts = [settings_row.system_prompt or "You are a helpful assistant."]
    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")

    chat = await session.get(Chat, chat_id)
    working = await memory.list_working_memory(session, chat_id)
    if working:
        parts.append(
            "Working memory (this chat's current task data): "
            + json.dumps({row.key: row.value for row in working}),
        )
    long_term = await memory.list_long_term_memory(session, chat.user_id)
    if long_term:
        parts.append(
            "Long-term memory (persists across all your chats): "
            + json.dumps({row.key: row.value for row in long_term}),
        )
    return "\n\n".join(parts)
```
**Import to add:** `from agent import memory` (local import, following the existing `from agent.llm_client import llm_client` top-of-file style, line 10).

**`build_llm_context` (lines 290-303) also needs to attach `tools=[...]`** — read `TOOL_REGISTRY`-derived schemas from `agent/tools.py` and add a `tools` key to the dict `build_llm_context` returns (or have `agent/ws.py` fetch the schema list from `agent/tools.py` directly and pass it into `stream_chat` — planner's call, but keep the schema *source* only in `agent/tools.py`, never duplicated).

---

### `agent/ws.py::_handle_chat_message` (extend — controller, event-driven/streaming)

**Analog:** same file, same function (lines 140-250) — the per-chat lock (`async with chat_locks[chat_id]:`, line 148) and single `async with async_session_factory() as session:` (line 149) block that every new step must stay inside.

**Existing streaming loop to extend** (lines 201-227):
```python
try:
    async for token in llm_client.stream_chat(
        llm_messages,
        payload.model,
        temperature,
        max_tokens,
    ):
        assistant_text += token
        await websocket.send_json(
            {"type": "token", "content": token},
        )
except Exception as exc:
    logger.error("llm_stream_failed", chat_id=chat_id, error=str(exc))
    await websocket.send_json({"type": "error", "detail": f"LLM error: {str(exc)}", "code": "LLM_ERROR"})
    await session.delete(user_msg)
    await session.commit()
    return
finally:
    active_streams.pop(chat_id, None)
```
**New step to insert between the stream loop and `_persist_assistant_message`:** if the stream yields a `tool_calls` event, call `agent.tools.dispatch_tool_calls(session, chat.user_id, chat_id, tool_calls)` (sequential, inside the same lock/session per Pattern 1), append `{"role": "tool", "tool_call_id": ..., "content": ...}` messages to `llm_messages`, and issue one more `stream_chat()` call for the final assistant text — mirroring the empirically-verified round-trip shape in RESEARCH.md's Code Examples (`finish_reason` flips `"tool_calls"` → `"stop"`).

**`done` message extension** (lines 244-250): add a `memory_writes: [...]` summary (id/key/layer only, per RESEARCH.md's architecture diagram) alongside the existing `stats` field — follow the exact same `await websocket.send_json({"type": "done", ...})` shape, just with one more key.

**Do not touch:** `extract_and_update_facts(session, chat_id, payload.content, payload.model)` (line 237) — RESEARCH.md's Anti-Pattern section is explicit that this pre-existing debounced-facts feature is unrelated and must be left exactly as-is, not merged with the new memory-write path.

---

### `agent/schemas.py` (extend — Pydantic request/response models)

**Analog:** `SettingsUpdate`/`SettingsResponse` (lines 60-93) for field-length-limited response/request models, `MessagePayload` (lines 113-117) for a minimal typed inbound payload.

**Pattern to copy** (module-level `*_MAX_LENGTH` constants, lines 11-18, then `Field(max_length=...)` on every `str` field):
```python
class SaveWorkingMemoryArgs(BaseModel):
    """Tool-call arguments for save_working_memory."""
    key: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50_000)


class MemoryEntryResponse(BaseModel):
    """Serialized memory row for the inspection panel."""
    id: int
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    updated_at: datetime


class ChatMemoryResponse(BaseModel):
    """GET /api/v1/chats/{chat_id}/memory response."""
    working: list[MemoryEntryResponse]
    long_term: list[MemoryEntryResponse]
```
Add new `WORKING_MEMORY_KEY_MAX_LENGTH`/`WORKING_MEMORY_VALUE_MAX_LENGTH`-style constants next to the existing ones (lines 11-18) rather than hardcoding `200`/`50_000` inline in multiple places (matches this file's existing convention).

---

### `agent/main.py` — `GET /api/v1/chats/{chat_id}/memory` (route/controller, request-response)

**Analog:** `get_chat_stats` (lines 384-393) — same GET-only, ownership-checked, read-through-service shape.

**Pattern to copy exactly:**
```python
@app.get("/api/v1/chats/{chat_id}/stats")
async def get_chat_stats(
    chat_id: int,
    model: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get real-time statistics for a chat."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    return await compute_chat_stats(session, chat_id, model=model)
```
New endpoint should be:
```python
@app.get("/api/v1/chats/{chat_id}/memory", response_model=ChatMemoryResponse)
async def get_chat_memory(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatMemoryResponse:
    """Return this chat's working memory and the caller's full long-term memory (D-02)."""
    chat = await _get_chat_or_404(session, chat_id, current_user.id)
    working = await memory.list_working_memory(session, chat_id)
    long_term = await memory.list_long_term_memory(session, chat.user_id)
    return ChatMemoryResponse(
        working=[...],
        long_term=[...],
    )
```
**Ownership-check reuse (V4 Access Control, RESEARCH.md Security Domain):** always call `_get_chat_or_404(session, chat_id, current_user.id)` (lines 69-82) before any memory read/write that takes a client-supplied `chat_id` — never resolve `user_id` from anywhere but `current_user.id`/`chat.user_id`, matching the "never 403, always 404" IDOR-safe convention already established for `/tree`, `/stats`, `/branch`, `DELETE`.

**Import additions needed at top of `agent/main.py`:** `from agent import memory` and add `ChatMemoryResponse`, `MemoryEntryResponse` to the existing `from agent.schemas import (...)` block (lines 16-29).

---

### `ui/static/index.html` — sidebar Memory tab/panel (component, request-response)

**Analog:** `#stats-panel` (lines 80-106) — the one existing *always-visible, non-modal* panel in this file; D-04 explicitly rules out the `#settings-modal` (lines 127-199) shape (`hidden fixed inset-0 ... bg-black/60` overlay) since the Memory panel must be "always reachable without an extra click."

**Sidebar structure to extend** (the `<aside>` block, lines 40-56) — currently `+ Новый чат` button, `#chat-list`, and `#agent-status` footer. Add a Memory tab/section inside this `<aside>`, styled with the same Tailwind utility classes already used there (`border-slate-800`, `bg-slate-900`, `text-slate-400`, `rounded-lg`):
```html
<div id="memory-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-64">
    <h3 class="text-slate-300 font-semibold mb-2">Память</h3>
    <div id="memory-working" class="space-y-1 mb-3"></div>
    <div id="memory-long-term" class="space-y-1"></div>
</div>
```
Follow `#stats-panel`'s label/value pairing idiom (lines 83-86: `<div class="text-slate-500">Label</div><div class="text-white font-semibold">value</div>`) for each memory row.

---

### `ui/static/app.js` — memory panel fetch/render (component, request-response)

**Analog:** `loadChatStats`/`updateStats`/`renderStatsPanel` (lines 214-250) for the GET-and-render pattern; `handleWsMessage`'s `switch` (lines 526-563) for the optional `tool_call`/`memory_writes` WS case; `apiFetch` (lines 47-64) for the fetch wrapper.

**Fetch pattern to copy** (mirrors `loadChatStats`, lines 230-246):
```javascript
async function loadChatMemory(chatId) {
    try {
        const memory = await apiFetch(`/api/v1/chats/${chatId}/memory`);
        renderMemoryPanel(memory);
    } catch (err) {
        console.error('Failed to load memory:', err);
    }
}
```
**Render pattern** should follow `renderStatsPanel`'s direct-DOM-manipulation style (lines 170-212) — build rows via `document.createElement` + `textContent` (never `innerHTML` with unsanitized content) for `key`/`value`, matching `renderMessages`'s `DOMPurify.sanitize(msg.content)` discipline (line 149) since memory `value` strings originate from LLM tool-call arguments (untrusted per RESEARCH.md's V5 Input Validation note) — always sanitize/escape before inserting into the DOM, never trust that "it's from the model" makes it safe HTML.

**Refresh trigger:** call `loadChatMemory(chatId)` from `handleWsMessage`'s existing `case 'done':` branch (line 531-538), alongside the existing `if (data.stats) updateStats(data.stats);` and `loadChatTree(state.currentChatId)` calls — this is the "poll on tab-open + refresh after `done`" strategy RESEARCH.md recommends for MEM-05, requiring no new WS message type.

---

## Shared Patterns

### Async DB write + rollback discipline
**Source:** `agent/dependencies.py::get_current_user` (lines 40-44), `agent/main.py::delete_chat` (lines 360-365), `agent/context_engine.py::_extract_facts` (lines 420-425)
**Apply to:** every write in `agent/memory.py` (`save_working_memory`, `save_long_term_memory`) and `agent/tools.py`'s dispatcher.
```python
session.add(row)
try:
    await session.commit()
except Exception:
    await session.rollback()
    raise
```

### NULL/ownership-scoped query idiom (`.is_(None)`, never `== None`)
**Source:** `agent/context_engine.py::get_effective_settings` (lines 45-49), `agent/main.py::_ensure_global_settings` (lines 53-57)
**Apply to:** any `agent/memory.py` query that needs an optional/global scope (not directly needed for D-02/D-03's always-scoped tables, but the `.is_(None)` convention must still be used if any nullable filter is ever added, e.g. a future `category IS NULL` filter).

### Ownership check before any chat-scoped read/write (IDOR-safe 404, never 403)
**Source:** `agent/main.py::_get_chat_or_404` (lines 69-82)
```python
async def _get_chat_or_404(session: AsyncSession, chat_id: int, user_id: int) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chat {chat_id} not found")
    return chat
```
**Apply to:** `GET /api/v1/chats/{chat_id}/memory` (new), and any tool handler in `agent/tools.py` that receives a `chat_id` (dispatcher already has `chat_id` from the trusted WS session context, not from LLM-generated arguments — never accept a client/LLM-supplied `chat_id`/`user_id` inside tool `arguments`, always use the dispatcher's own parameters, per RESEARCH.md's Security Domain).

### Per-chat lock reuse (never a second lock, never `asyncio.gather`)
**Source:** `agent/state.py::chat_locks` + `agent/ws.py::_handle_chat_message` (lines 146-148)
```python
if chat_id not in chat_locks:
    chat_locks[chat_id] = asyncio.Lock()
async with chat_locks[chat_id]:
    async with async_session_factory() as session:
        ...
```
**Apply to:** `agent/tools.py::dispatch_tool_calls` must be invoked from inside this exact block, never independently — this is the STATE.md/CLAUDE.md hard constraint that most directly risks violation if the dispatcher is built as a standalone module without checking its caller's context.

### structlog logging idiom
**Source:** used throughout `agent/context_engine.py`, `agent/ws.py`, `agent/main.py`
```python
logger = get_logger(__name__)
...
logger.info("strategy_selected", chat_id=chat_id, strategy=effective.strategy)
```
**Apply to:** `agent/tools.py` (log every dispatched tool call with its `tool_call_id`, per RESEARCH.md's Pitfall 1 mitigation — "log every write with its triggering tool_call.id so any write with no corresponding tool-call id is provably a bug") and `agent/memory.py`.

### WS error/frame shape
**Source:** `agent/ws.py::_handle_chat_message` error branches (lines 176-192, 212-227)
```python
await websocket.send_json({"type": "error", "detail": str(exc), "code": "SOME_CODE"})
```
**Apply to:** any new error surfaced from tool dispatch mid-turn (e.g. malformed tool arguments) — reuse `{"type": "error", ...}`, do not invent a second error envelope shape.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `agent/llm_client.py::LMStudioClient` v0→v1 endpoint fix (Pitfall 5, `POST /api/v0/models/load|unload` → `/api/v1/...`) | service (HTTP client) | request-response | Not a memory-layer file at all — pre-existing bug flagged by RESEARCH.md as an **open planning decision** (fix in-phase vs. track separately), not a pattern-mapping concern. No analog needed; it's a 2-line URL/field-name fix in the same functions (`_load_model_locked`/`_unload_model_http`, lines 168-260) if the planner chooses to include it. |
| Tool-call round-trip's second `stream_chat()` invocation shape | service (streaming) | streaming | No existing codebase precedent for a *second* LLM call within one WS turn — RESEARCH.md's Code Examples (live-verified request/response JSON) is the only available reference; planner should treat that as the source of truth rather than an in-repo analog. |

## Conventions

Ran the shared deterministic convention-deriver (`gsd-tools.cjs verify conventions --derive`) both repo-wide and scoped to `agent/`.

`--scope agent` returned `{"skipped": true, "reason": "no-readable-files"}` — convention derivation skipped for that scope (the deriver's file-name/identifier/export/import heuristics target JS/TS-style source; this codebase's `agent/` directory is 100% Python, so it found zero files matching its own readable-file filter). The repo-wide run (no `--scope`) surfaced only 1 candidate file for `file-name-casing` and 0 for `export-style`/`import-style` (both `insufficient-data`) with `identifier-casing` reporting `camel` at 46/46 — that result reflects the plugin's own JS/TS tooling files somewhere in the environment, not this project's Python source, and should not be read as a claim about this codebase's naming style.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| file-name casing | n/a | n/a | n/a | insufficient-data (scope has no JS/TS-recognized files) |
| identifier casing | n/a | n/a | n/a | insufficient-data (not meaningful for this Python codebase) |
| export style | n/a | n/a | n/a | insufficient-data |
| import style | n/a | n/a | n/a | insufficient-data |

For this codebase's *actual* conventions, use CLAUDE.md's own documented naming/style rules (already authoritative and directly Python-specific — snake_case modules/functions, `PascalCase` classes/enums, `UPPER_CASE` constants, `_`-prefixed private helpers) rather than the deriver's output, which is not applicable here.

**Contested hotspots (author's choice):** Not applicable to this codebase's own source, but noted per protocol: the deriver's prototype intentional-contested-split case is the gsd-plugin's own **CJS<->SDK dual resolver** (`bin/lib/**` is CJS `module.exports`/`require`; `sdk/src/**` is ESM `export`/`import`) — each half is internally consistent per-directory, contested only repo-wide within the *plugin's* codebase, not this project's. Reviewers/planners working inside the plugin's own source should match the directory's local style; this has no bearing on `AiAdventAgentV2`'s all-Python `agent`/`shared`/`ui` tree.

## Metadata

**Analog search scope:** `shared/models.py`, `shared/database.py`, `agent/context_engine.py`, `agent/state.py`, `agent/ws.py`, `agent/llm_client.py`, `agent/schemas.py`, `agent/main.py`, `agent/dependencies.py`, `ui/static/index.html`, `ui/static/app.js`, `tests/test_scoping.py`, `tests/test_cascade_delete.py`, `tests/test_context_engine.py`
**Files scanned:** 14
**Pattern extraction date:** 2026-09-20
