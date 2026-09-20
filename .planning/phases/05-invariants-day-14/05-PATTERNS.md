# Phase 5: Invariants (Day 14) - Pattern Map

**Mapped:** 2026-09-20
**Files analyzed:** 7 primary (+ 4 test files noted for completeness)
**Analogs found:** 7 / 7

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `shared/models.py` (add `GlobalInvariant`, `ChatInvariant`, `InvariantConflict`) | model | CRUD | `shared/models.py:138-186` (`WorkingMemory`/`LongTermMemory` scope-split) | exact |
| `agent/invariants.py` (new) | service | CRUD | `agent/profile.py` (single-row CRUD) + `agent/memory.py` (dual-scope CRUD) | exact |
| `agent/context_engine.py::build_system_prompt()` (modify) | transform | request-response (prompt assembly) | same file, lines 61-107 (existing profile/facts/memory/tasks injection blocks) | exact |
| `agent/ws.py::_handle_chat_message` (modify — self-critique + justify/retract) | controller (WS handler) | streaming, event-driven | same file, lines 143-368 (existing tool-result follow-up `stream_chat` round-trip) | exact |
| `agent/context_engine.py` (add self-critique helper, e.g. `_run_self_critique`) | service | request-response (LLM-as-judge) | `agent/context_engine.py:438-476` (`_extract_facts` / `_parse_facts_json`) | exact |
| `agent/main.py` (new REST endpoints: global + per-chat invariant CRUD, conflict log) | controller (route) | CRUD, request-response | `agent/main.py:622-641` (`GET/PUT /api/v1/profile`, unscoped) + `agent/main.py:496-552` (chat-owned task endpoints, `_get_chat_or_404`-guarded) | exact |
| `ui/static/index.html` (new Invariants panel + fold/unfold retrofit on 4 panels) | component (markup) | — | `ui/static/index.html:48-96` (Memory/Profile/Task panel markup) | exact |
| `ui/static/app.js` (new `renderInvariantsPanel`, `setupFoldablePanels`, conflict banner) | component | request-response + event-driven (WS) | `ui/static/app.js:308-322` (`renderMemoryPanel`) + `:364-463` (`renderTaskPanel`, CRUD action buttons) + `:808-849` (`handleWsMessage`) | role-match (fold/unfold has no precedent — new shared UI) |

## Pattern Assignments

### `shared/models.py` (model, CRUD)

**Analog:** `shared/models.py:138-186` (`WorkingMemory` vs `LongTermMemory`)

**Two-table scope-split pattern** (lines 138-185):
```python
class WorkingMemory(SQLModel, table=True):
    """Key-value scratchpad for a chat's current task data (D-03)."""

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
    """User-scoped, cross-chat memory for profile/decisions/knowledge (D-02)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

**Apply to invariants:** `GlobalInvariant` gets NO `user_id`/`chat_id` column at all (D-02 — table-level guarantee, not a runtime-null one). `ChatInvariant` gets `user_id` + `chat_id` (both `ForeignKey(..., ondelete="CASCADE")`) plus a nullable cross-table FK `overrides_id` pointing at `GlobalInvariant.id` — **not self-referential** since these are two distinct tables (per RESEARCH.md Anti-Pattern: "don't try to make `overrides_id` self-referential in one shared table"). `InvariantConflict.chat_id`/`message_id` follow the same `ForeignKey(..., ondelete="CASCADE")` style as `TokenUsage.chat_id` (`shared/models.py:111-125`) and `TaskTransition.task_id` (`shared/models.py:259-269`). Verify SQLModel's resolved table name (`GlobalInvariant` → `globalinvariant`, no underscore, no explicit `__tablename__` used anywhere in this file today) before writing the `ForeignKey("globalinvariant.id", ...)` string — this is Pitfall 4 in RESEARCH.md.

**Field-length convention:** `title`/`rule_text` should mirror `Profile`'s `max_length=2000` fields (`shared/models.py:200-202`) and `Task`'s `title: max_length=200` (`shared/models.py:236`).

---

### `agent/invariants.py` (service, CRUD)

**Analog:** `agent/profile.py` (single-row-per-user CRUD) + `agent/memory.py` (dual-scope CRUD, list/save shape)

**Module docstring + commit/rollback pattern** (`agent/profile.py:1-34`):
```python
"""Thin CRUD layer owning all reads and writes to the profile table."""

from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import Profile

logger = get_logger(__name__)


async def get_or_create_profile(session: AsyncSession, user_id: int) -> Profile:
    row = await get_profile(session, user_id)
    if row is not None:
        return row
    row = Profile(user_id=user_id)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("profile_created", user_id=user_id)
    return row
```

**List-by-scope pattern** (`agent/memory.py:15-28`):
```python
async def list_working_memory(session: AsyncSession, chat_id: int) -> list[WorkingMemory]:
    """Return this chat's working memory rows, ordered by key."""
    result = await session.exec(
        select(WorkingMemory).where(WorkingMemory.chat_id == chat_id).order_by(WorkingMemory.key),
    )
    return list(result.all())


async def list_long_term_memory(session: AsyncSession, user_id: int) -> list[LongTermMemory]:
    """Return the user's full long-term memory, ordered by key (cross-chat, D-02)."""
    result = await session.exec(
        select(LongTermMemory).where(LongTermMemory.user_id == user_id).order_by(LongTermMemory.key),
    )
    return list(result.all())
```

**Apply to invariants:** `list_global(session)` mirrors `list_long_term_memory` but with **no `user_id` filter at all** (D-02). `list_chat_invariants(session, chat_id)` mirrors `list_working_memory`. New functions this module needs beyond the memory/profile precedent: `create_global`, `update_global`, `delete_global`, `create_chat_invariant`, `update_chat_invariant`, `delete_chat_invariant` (all full CRUD per D-04, unlike memory's upsert-only shape), and `resolve_active_invariants(session, chat_id) -> list[dict]` — the single source of truth consumed by **both** `build_system_prompt()` and the self-critique prompt builder (RESEARCH.md Pitfall 3), applying D-05/D-06 override labeling. Every write function must follow the `try: commit/refresh except: rollback; raise` shape shown above — no exceptions.

---

### `agent/context_engine.py::build_system_prompt()` (transform, request-response)

**Analog:** same file, lines 61-107 (existing assembly order: system prompt → profile → facts/summary → working memory → long-term memory → open tasks)

**Injection-block pattern** (lines 81-105):
```python
    working = await memory.list_working_memory(session, chat_id)
    if working:
        parts.append(
            "Working memory (this chat's current task data): "
            + json.dumps({row.key: row.value for row in working}),
        )
    ...
    open_tasks = await tasks.list_open_tasks(session, chat_id)
    if open_tasks:
        lines = []
        for task in open_tasks:
            ...
        parts.append("Open tasks in this chat:\n" + "\n".join(lines))

    return "\n\n".join(parts)
```

**Apply to invariants (per D-06's exact override-labeling format, RESEARCH.md Code Examples):**
```python
    active = await invariants.resolve_active_invariants(session, chat_id)
    if active:
        lines = []
        for item in active:
            if item["overridden_by"] is not None:
                lines.append(f'[GLOBAL] {item["rule_text"]} (overridden for this chat — see below)')
                lines.append(f'[CHAT] {item["overridden_by"]["rule_text"]} (overrides the above)')
            elif item["scope"] == "chat":
                lines.append(f'[CHAT] {item["rule_text"]}')
            else:
                lines.append(f'[GLOBAL] {item["rule_text"]}')
        parts.append("Active invariants (always follow these; flag if you cannot):\n" + "\n".join(lines))
```
Add this block as one more `parts.append(...)` following the existing assembly order (append it after "Open tasks" or wherever the plan decides — the load-bearing pattern is "one more block in the same `parts` list", not a specific position).

---

### `agent/context_engine.py` — self-critique helper (service, request-response / LLM-as-judge)

**Analog:** `agent/context_engine.py:438-476` (`_extract_facts`) + `:124-131` (`_parse_facts_json`)

**Non-streaming `complete_chat()` + lenient JSON parse + fail-open** (lines 438-476):
```python
async def _extract_facts(
    session: AsyncSession,
    chat_id: int,
    user_message: str,
    model: str,
) -> None:
    """Call the LLM to extract facts and merge them into settings."""
    prompt = f"Extract key facts as JSON: {user_message}"
    try:
        raw = await llm_client.complete_chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=512,
        )
        new_facts = _parse_facts_json(raw)
    except Exception as exc:
        logger.warning("facts_extraction_failed", chat_id=chat_id, error=str(exc))
        return
    ...
```

**Lenient parse with safe fallback** (lines 124-131):
```python
def _parse_facts_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object from stored facts text."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        ...
```

**Apply to the self-critique call:** `_run_self_critique(...)` must use `complete_chat()` (not `stream_chat()`), `temperature=0.0`, and **fail open** — `except Exception: return {"conflict": False}` — never let a broken critique call raise/propagate and abort the WS turn (RESEARCH.md explicit Anti-Pattern). Build the critique prompt from `invariants.resolve_active_invariants(...)` output (already override-resolved), not raw table rows.

---

### `agent/ws.py::_handle_chat_message` (controller, streaming / event-driven)

**Analog:** same file, lines 283-309 (existing post-tool-result follow-up `stream_chat()` call)

**Third-round-trip shape to mirror** (lines 283-309):
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
                    logger.error(
                        "llm_stream_failed",
                        chat_id=chat_id,
                        error=str(exc),
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": f"LLM error: {str(exc)}",
                            "code": "LLM_ERROR",
                        },
                    )
                    await session.delete(user_msg)
                    await session.commit()
                    return
```

**Apply to D-09's justify/retract round-trip:** insert after the existing tool-result follow-up (or after the initial response if no tools were called), before `_persist_assistant_message` (line 347). Capture output into its own `justification_text` variable (not just appended in place) so it can be written verbatim to `InvariantConflict.note` as well as appended to `assistant_text`:
```python
                if critique["conflict"]:
                    llm_messages.append({
                        "role": "user",
                        "content": _build_justify_retract_prompt(critique, active_invariants),
                    })
                    justification_text = ""
                    async for token in llm_client.stream_chat(llm_messages, payload.model, temperature, max_tokens):
                        justification_text += token
                        assistant_text += token
                        await websocket.send_json({"type": "token", "content": token})
```

**Sequencing (RESEARCH.md Pitfall 1 — load-bearing ordering):** (1) initial response + tool round-trip (existing), (2) self-critique `complete_chat()` call, (3) justify/retract `stream_chat()` if flagged, (4) `_persist_assistant_message()` with final `assistant_text` (existing line 347), (5) **then** build/commit the `InvariantConflict` row using the now-existing `assistant_msg.id`, (6) `done` frame (existing lines 359-368) — extend the `done` payload (or add a new WS event type) with conflict info for D-12's inline banner.

**Lock context (unchanged):** all of this must run inside the existing `async with chat_locks[chat_id]:` block that already wraps the whole handler (line 151) — no new lock needed.

---

### `agent/main.py` — invariant REST endpoints (controller/route, CRUD + request-response)

**Analog A — unscoped CRUD:** `agent/main.py:622-641` (`GET/PUT /api/v1/profile`, no ownership filter)
```python
@app.get("/api/v1/profile", response_model=ProfileResponse)
async def get_profile(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Return the caller's profile, lazily creating an empty row on first access."""
    row = await profile.get_or_create_profile(session, current_user.id)
    return _profile_to_response(row)


@app.put("/api/v1/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Update the caller's profile fields (UI-only write path, D-02)."""
    updates = body.model_dump(exclude_unset=True)
    row = await profile.update_profile(session, current_user.id, **updates)
    return _profile_to_response(row)
```
**Apply to `GET/POST/PUT/DELETE /api/v1/invariants`:** `current_user: User = Depends(get_current_user)` is required for auth, but there is **deliberately no ownership/ownership-filter check** (D-02 — matches this file's existing "every account has equal admin capability" model). Do NOT copy `_get_chat_or_404`-style filtering onto these routes.

**Analog B — chat-owned, ownership-checked CRUD:** `agent/main.py:86-99` (`_get_chat_or_404`) + `:496-507` (`GET /api/v1/chats/{chat_id}/tasks`)
```python
async def _get_chat_or_404(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> Chat:
    """Load a chat owned by user_id or raise HTTP 404 (never 403, to avoid an IDOR oracle)."""
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chat {chat_id} not found")
    return chat


@app.get("/api/v1/chats/{chat_id}/tasks", response_model=list[TaskResponse])
async def get_chat_tasks(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[TaskResponse]:
    """Return this chat's tasks with embedded, oldest-first transition history."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    rows = await tasks.list_tasks_for_chat(session, chat_id)
    return [_task_to_response(row, await tasks.list_transitions(session, row.id)) for row in rows]
```
**Apply to `GET/POST/PUT/DELETE /api/v1/chats/{chat_id}/invariants` and `GET /api/v1/chats/{chat_id}/invariant-conflicts`:** always call `await _get_chat_or_404(session, chat_id, current_user.id)` first (IDOR-safe, 404-not-403) before any per-chat invariant read/write — this is the one asymmetry vs. the global endpoints above.

**Analog C — delete-with-rollback:** `agent/main.py:427-440` (`delete_chat`)
```python
async def delete_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a chat and clear related in-memory caches."""
    chat = await _get_chat_or_404(session, chat_id, current_user.id)
    try:
        await session.delete(chat)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
```
Apply this exact `try: delete/commit except: rollback; raise` shape to every DELETE invariant endpoint.

---

### `ui/static/index.html` (component/markup) + `ui/static/app.js` (component, request-response + WS)

**Analog — panel markup shape:** `ui/static/index.html:48-96` (Memory/Profile/Task panels — each is a `<div id="X-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 ...">` with an `<h3>` header and a body)
```html
<div id="task-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-72">
    <h3 class="text-slate-300 font-semibold mb-2">Задачи <span id="task-count" class="text-white font-semibold">0</span></h3>
    <div id="task-list" class="space-y-2"></div>
</div>
```
**Apply to the new Invariants panel + D-11 fold/unfold retrofit:** add `<div id="invariants-panel" ...>` following the same header+body shape, with a `data-fold-toggle="invariants-panel-body"` button in the `<h3>` header (for the badge, D-12) and the existing content wrapped in `<div id="invariants-panel-body" class="hidden">...</div>`. Retrofit the same wrapper pattern onto `memory-panel`, `profile-panel`, `task-panel` bodies — this is new shared markup structure applied to all four, not copy-paste of one panel into a new file.

**Analog — list-rendering with actions:** `ui/static/app.js:364-463` (`renderTaskPanel`) — count badge update, `replaceChildren()`, per-item card with action buttons wired via `addEventListener`, empty-state handling:
```javascript
function renderTaskPanel() {
    const tasks = state.lastTasks;
    if (tasks === null) return;
    const countEl = $('task-count');
    const listEl = $('task-list');
    if (countEl) countEl.textContent = String(tasks.length);
    if (!listEl) return;
    listEl.replaceChildren();
    if (!tasks.length) {
        const empty = document.createElement('div');
        empty.className = 'text-slate-600';
        empty.textContent = '—';
        listEl.appendChild(empty);
        return;
    }
    tasks.forEach((task) => {
        const card = document.createElement('div');
        ...
        const cancelBtn = document.createElement('button');
        cancelBtn.addEventListener('click', () => {
            cancelTask(task.id).catch((err) => showToast(err.message, 'error'));
        });
        ...
    });
}
```
**Apply to `renderInvariantsPanel()`:** mirror this shape for both the global list and the per-chat list (with edit/delete buttons per D-04, and an "overrides" `<select>` populated from the global list for D-05). Always use `.textContent` for invariant title/rule text (never `.innerHTML`) — same XSS-safety convention `renderMemoryEntries`/`renderTaskPanel` already follow for user-controlled strings.

**Analog — save/CRUD action + toast pattern:** `ui/static/app.js:515-528` (`saveProfile`) and `:465-494` (`pauseTask`/`resumeTask`/`cancelTask`):
```javascript
async function saveProfile() {
    const body = { style: $('profile-style').value, format: $('profile-format').value, constraints: $('profile-constraints').value };
    try {
        const data = await apiFetch('/api/v1/profile', { method: 'PUT', body: JSON.stringify(body) });
        state.lastProfile = data;
        showToast('Профиль сохранён', 'success');
    } catch (err) {
        showToast('Не удалось сохранить профиль. Проверьте соединение и попробуйте снова.', 'error');
    }
}
```
Apply the same `apiFetch(..., { method, body: JSON.stringify(...) })` + `try/catch` + `showToast` shape to all new invariant CRUD calls.

**Analog — WS event dispatch for the inline conflict banner (D-12):** `ui/static/app.js:808-849` (`handleWsMessage`):
```javascript
function handleWsMessage(data) {
    switch (data.type) {
        case 'token':
            appendTokenToStream(data.content || '');
            break;
        case 'done':
            setStreaming(false);
            removeLoadingBubble();
            ...
            if (data.stats) updateStats(data.stats);
            if (state.currentChatId) {
                loadChatTree(state.currentChatId);
                loadChatMemory(state.currentChatId);
                loadChatTasks(state.currentChatId);
            }
            break;
        case 'error':
            ...
        default:
            break;
    }
}
```
**Apply:** add a `case 'invariant_conflict':` branch (or read conflict info off the existing `done` payload — see `agent/ws.py::_handle_chat_message` pattern assignment above) that renders the inline banner right after the flagged assistant bubble, and triggers a badge-count refresh (e.g. a new `loadChatInvariants(state.currentChatId)` call alongside the existing `loadChatTree`/`loadChatMemory`/`loadChatTasks` calls inside the `'done'` case). Route the banner's LLM-generated justification text through the existing `renderMarkdown()` + `DOMPurify.sanitize()` pipeline (`ui/static/app.js:90` `renderMarkdown`), exactly like `appendTokenToStream` (`:550-560`) does for assistant content — this is untrusted LLM output, same threat class.

**Fold/unfold mechanism (D-11) — no existing precedent, build once:**
```javascript
// New pattern — no prior fold/unfold code exists in this codebase.
function setupFoldablePanels() {
    document.querySelectorAll('[data-fold-toggle]').forEach((btn) => {
        const targetId = btn.dataset.foldToggle;
        const body = document.getElementById(targetId);
        if (!body) return;
        btn.addEventListener('click', () => {
            const collapsed = body.classList.toggle('hidden');
            btn.textContent = collapsed ? '▸' : '▾';
        });
    });
}
// Called once at init (alongside other DOMContentLoaded setup), applied uniformly
// to memory-panel-body, profile-panel-body, task-panel-body, invariants-panel-body.
```

## Shared Patterns

### Commit/rollback on every write
**Source:** `agent/profile.py:27-32`, `agent/memory.py:48-52`, `agent/main.py:434-439`
**Apply to:** `agent/invariants.py` (every create/update/delete), all new REST endpoints in `agent/main.py` that write
```python
try:
    await session.commit()
    await session.refresh(row)
except Exception:
    await session.rollback()
    raise
```

### Ownership check asymmetry (D-02 vs. everything else)
**Source:** `agent/main.py:86-99` (`_get_chat_or_404`)
**Apply to:** `ChatInvariant`/`InvariantConflict` endpoints get `_get_chat_or_404(session, chat_id, current_user.id)` first; `GlobalInvariant` endpoints deliberately get none — do not conflate the two.

### Fail-open on non-critical LLM calls
**Source:** `agent/context_engine.py:438-456` (`_extract_facts`'s `except Exception: logger.warning(...); return`)
**Apply to:** the self-critique `complete_chat()` call in the new critique helper — a broken/unparseable critique response must never abort the WS turn or delete the user message (that behavior is reserved for the primary `stream_chat()` failure path, `agent/ws.py:238-253`/`294-309`).

### `structlog` logging convention
**Source:** every module above (`logger = get_logger(__name__)`, `snake_case_action` message keys with `key=value` pairs, e.g. `profile_created`, `facts_extraction_failed`, `chat_access_denied`)
**Apply to:** new events like `invariant_created`, `invariant_conflict_detected`, `invariant_critique_failed`.

### XSS-safe rendering of user/LLM-controlled text
**Source:** `ui/static/app.js:279-306` (`renderMemoryEntries` — `.textContent`, never `.innerHTML`), `:90` + `:558` (`renderMarkdown` + `appendTokenToStream` piping through `DOMPurify.sanitize()` via `renderMarkdown`)
**Apply to:** invariant title/rule_text in the sidebar (`.textContent`), and the conflict banner's LLM-generated justification/retraction text (`renderMarkdown()` + DOMPurify, same as chat bubbles).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| Fold/unfold toggle mechanism (D-11) in `ui/static/app.js` | component | event-driven (DOM) | RESEARCH.md and CONTEXT.md both explicitly flag this as genuinely new shared UI with no existing precedent in this codebase — build once (`setupFoldablePanels()`), apply to all four panels. The `data-fold-toggle`/hidden-body convention above is a *proposed* pattern (from RESEARCH.md Code Examples), not an extracted one. |

## Metadata

**Analog search scope:** `agent/` (`profile.py`, `memory.py`, `context_engine.py`, `ws.py`, `main.py`, `tasks.py`), `shared/models.py`, `ui/static/` (`index.html`, `app.js`), `tests/` (`test_profile_api.py`, `test_memory_ws.py`, `test_task_ws.py`, `test_task_api.py`) for test-file precedent
**Files scanned:** 11
**Pattern extraction date:** 2026-09-20

### Test-file analogs (for planner's `tests/` file list, per RESEARCH.md's recommended project structure)

| New test file | Analog | Note |
|---|---|---|
| `tests/test_invariants.py` | `tests/test_profile_api.py` (CRUD-only unit shape) is thinner than needed — mirror `agent/memory.py`'s dual-scope CRUD tests if a `tests/test_memory.py`-style unit file exists, otherwise base on `test_profile_api.py`'s auth-fixture pattern (`authenticated_client: AsyncClient` from `tests/conftest.py`) | `client`/`authenticated_client` fixtures from `tests/conftest.py::login_test_client` |
| `tests/test_invariants_api.py` | `tests/test_profile_api.py:1-51` | `resp = await authenticated_client.get(...)`; assert `resp.status_code`; assert response body keys |
| `tests/test_invariants_ws.py` | `tests/test_memory_ws.py:1-69` (respx SSE mock helpers `_plain_content_response`, `_tool_calls_response`, `_queue_responses`, `_send_and_drain`) | **Critical distinction (RESEARCH.md Pitfall 2):** the self-critique call uses `complete_chat()`, which needs a **plain-JSON** mock (`httpx.Response(200, json={"choices": [{"message": {"content": ...}}]})`), not the SSE-body helpers above (`data: ...\n` framing) used for `stream_chat()` calls — add a new `_plain_json_response()` test helper, don't reuse `_plain_content_response` for the critique-call mock position in the queue |
| `tests/test_cascade_delete.py` (modify) | same file, existing cascade assertions for other tables | add `ChatInvariant`/`InvariantConflict` cascade-on-chat-delete assertions following the existing pattern in that file |

## Conventions

Derived via the shared `gsd-tools.cjs verify conventions --derive` module (same one `gsd-code-reviewer` uses).

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| file-name casing | — | — | — | insufficient-data (only 1 file matched the tool's JS/TS-oriented file-naming check) |
| identifier casing | camelCase | 100% | 0 | named contract (`ui/static/app.js`, 59 identifiers sampled) |
| export style | — | — | — | insufficient-data (0 detected — this codebase's vanilla-JS frontend uses no ES module `export`/`import` statements at all, consistent with CLAUDE.md's "no bundler, no npm packages" constraint) |
| import style | — | — | — | insufficient-data (0 detected, same reason) |

**Scope note:** the derivation tool is JS/TS-oriented and returned `skipped: true, reason: "no-readable-files"` when scoped to `agent/` or repo-wide Python code, so it could not derive axes for the Python backend (`shared/models.py`, `agent/invariants.py`, `agent/context_engine.py`, `agent/ws.py`, `agent/main.py`). For those files, follow `CLAUDE.md`'s explicit, human-authored Python conventions instead (already summarized throughout this document): `snake_case` functions/variables, `PascalCase` classes, `UPPER_CASE` constants, type hints everywhere, `structlog` logging, `.is_(None)` in SQLAlchemy `where()`, `sa_column=Column(ForeignKey(..., ondelete=...))` never `Field(ondelete=...)`.

**Contested hotspots (author's choice):** none newly discovered in this phase's scope — `identifier-casing` for the JS frontend is a clean 100%-camelCase named contract (no contested split). For general awareness (not applicable to this phase's file set): this repo's broader tooling ecosystem (the GSD plugin itself, not this project) has a known prototype intentional-contested split between a CJS `bin/lib/**` (`module.exports`/`require`) and an ESM `sdk/src/**` (`export`/`import`) — not relevant here since `AiAdventAgentV2` has no such dual-resolver structure; mentioned only per the standard instruction to note this pattern when present. Match each directory's local style when in doubt (Python in `agent/`/`shared/`, vanilla global-script JS in `ui/static/`).
