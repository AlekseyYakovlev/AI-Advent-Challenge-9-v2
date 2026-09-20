# Phase 4: Task State Machine (Day 13) - Pattern Map

**Mapped:** 2026-09-20
**Files analyzed:** 9
**Analogs found:** 9 / 9

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|-----------------|---------------|
| `shared/models.py` (Task, TaskTransition, TaskState) | model | CRUD | `shared/models.py::WorkingMemory`/`LongTermMemory`/`Settings.strategy` | exact |
| `agent/schemas.py` (Create/Transition/Pause/ResumeTaskArgs, TaskResponse, TaskTransitionResponse, LlmTaskState) | model (Pydantic schema) | request-response / CRUD | `agent/schemas.py::SaveWorkingMemoryArgs`/`SaveLongTermMemoryArgs`, `ProfileResponse`/`ChatMemoryResponse` | exact |
| `agent/tasks.py` (NEW) | service | CRUD | `agent/memory.py` (also `agent/profile.py`) | exact |
| `agent/tools.py` (extended: 4 new `@register_tool` entries) | service (tool dispatcher registrations) | event-driven / request-response | `agent/tools.py::_save_working_memory`/`_save_long_term_memory` (lines 158-190) | exact |
| `agent/context_engine.py::build_system_prompt` (extended) | transform | transform (read-injection) | `agent/context_engine.py::build_system_prompt` working/long-term memory blocks (lines 81-94) | exact |
| `agent/main.py` (new `GET /api/v1/chats/{chat_id}/tasks`, `POST /api/v1/tasks/{id}/pause\|resume\|cancel`) | controller / route | request-response, CRUD | `agent/main.py::get_chat_memory` (GET, lines 420-443) + `agent/main.py::update_profile`/`update_settings` (UI-only REST write, lines 482-532) | exact |
| `ui/static/index.html` (`#task-panel`) | component (markup) | request-response (render target) | `#memory-panel`/`#profile-panel` stacked sidebar sections (lines 48-92) | exact |
| `ui/static/app.js` (loadChatTasks/renderTaskPanel + pause/resume/cancel handlers) | component (frontend) | request-response | `loadChatMemory`/`renderMemoryPanel`/`loadProfile`/`renderProfilePanel`/`saveProfile` (lines 252-339), `handleWsMessage`'s `'done'` case (lines 623-633) | exact |
| `tests/test_cascade_delete.py` (extended with Task/TaskTransition case) | test | CRUD | `test_delete_chat_cascades_db_records` (lines 27-63) | exact |

## Pattern Assignments

### `shared/models.py` (model, CRUD)

**Analog:** `shared/models.py::WorkingMemory` (lines 138-163), `LongTermMemory` (lines 165-185), and `Settings.strategy`'s `SAEnum` idiom (lines 90-98)

**Imports pattern** (already present at top of file, lines 1-8):
```python
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel
```

**FK-cascade + enum pattern to copy verbatim** (`Settings.strategy`, lines 90-98):
```python
strategy: ContextStrategy = Field(
    default=ContextStrategy.SLIDING_WINDOW,
    sa_column=Column(
        SAEnum(
            ContextStrategy,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
    ),
)
```
Apply the identical `values_callable` idiom to `Task.state`, `TaskTransition.from_state` (with `nullable=True` added), and `TaskTransition.to_state` — this is Pitfall 3 in RESEARCH.md (SAEnum stores member *names* not *values* unless `values_callable` is passed).

**Dual-FK ownership pattern to copy verbatim** (`WorkingMemory`, lines 138-163):
```python
class WorkingMemory(SQLModel, table=True):
    """Key-value scratchpad for a chat's current task data (D-03)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    chat_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
```
`Task` needs the same dual `user_id`+`chat_id` FK-cascade pair (both `ondelete="CASCADE"`), `title`/`description`/`goal` as plain `str = Field(max_length=...)`, `is_paused: bool = Field(default=False)`, `delegate_to: Optional[str] = Field(default=None, max_length=200)` (D-03, unused this phase), `created_at`/`updated_at` via `default_factory=lambda: datetime.now(timezone.utc)`. `TaskTransition.task_id` needs a single `ForeignKey("task.id", ondelete="CASCADE")` (mirrors `TokenUsage.chat_id`, lines 111-125). **Never** use `Field(foreign_key=...)` — RESEARCH.md Pitfall 5 confirms it silently drops cascade behavior.

**No migration needed:** both tables are picked up automatically by `shared/database.py`'s unconditional `SQLModel.metadata.create_all` (same as `WorkingMemory`/`LongTermMemory` in Phase 2 — no `_migrate_legacy_*` function required unless a later phase renames a `TaskState` value).

---

### `agent/schemas.py` (model/Pydantic schema, request-response + CRUD)

**Analog:** `SaveWorkingMemoryArgs`/`SaveLongTermMemoryArgs` (lines 170-197), `MemoryEntryResponse`/`ChatMemoryResponse` (lines 152-168), `ProfileUpdate`/`ProfileResponse` (lines 200-215)

**Constants pattern** (lines 11-21, extend this block, don't fork a new one):
```python
TITLE_MAX_LENGTH = 200
...
MEMORY_KEY_MAX_LENGTH = 200
MEMORY_VALUE_MAX_LENGTH = 50_000
PROFILE_FIELD_MAX_LENGTH = 2000
```
Add `TASK_TITLE_MAX_LENGTH = 200`, `TASK_DESCRIPTION_MAX_LENGTH = 5_000`, `TASK_GOAL_MAX_LENGTH = 2_000`, `TASK_NOTE_MAX_LENGTH = 2_000` alongside these.

**Tool-args pattern to copy** (`SaveWorkingMemoryArgs`, lines 170-183):
```python
class SaveWorkingMemoryArgs(BaseModel):
    """Tool-call arguments for save_working_memory."""

    key: str = Field(
        min_length=1,
        max_length=MEMORY_KEY_MAX_LENGTH,
        description="Short stable identifier for this scratchpad entry, e.g. current_task_step",
    )
    content: str = Field(
        min_length=1,
        max_length=MEMORY_VALUE_MAX_LENGTH,
        description="The value to store",
    )
```
`CreateTaskArgs` (title/description/goal), `TransitionTaskArgs` (task_id: `Field(gt=0)`, new_state: a narrower `LlmTaskState` enum excluding `"cancelled"` per D-07, note: optional/default `""`), `PauseTaskArgs`/`ResumeTaskArgs` (task_id only) all follow this exact `BaseModel` + `Field(min_length=..., max_length=..., description=...)` shape — **do not** add a `delegate_to` field to `CreateTaskArgs`/`TransitionTaskArgs` (D-03: never LLM-settable this phase).

**Response pattern to copy** (`ChatMemoryResponse`, lines 161-168, and `ProfileResponse`, lines 208-215):
```python
class ChatMemoryResponse(BaseModel):
    """GET /api/v1/chats/{chat_id}/memory response: all three memory layers."""

    chat_id: int
    short_term_message_count: int
    working: list[MemoryEntryResponse]
    long_term: list[MemoryEntryResponse]
```
`TaskResponse` (id/title/description/goal/state/is_paused/delegate_to/created_at/updated_at/`history: list[TaskTransitionResponse]`) and `TaskTransitionResponse` (from_state/to_state/note/created_at) mirror this nested-list-of-sub-response shape exactly — one combined GET response, no N+1 (same rationale as `ChatMemoryResponse` bundling working+long_term in one fetch).

---

### `agent/tasks.py` (NEW — service, CRUD)

**Analog:** `agent/memory.py` (full file, 136 lines) and `agent/profile.py` (full file, 62 lines)

**Module header + imports pattern** (`agent/memory.py`, lines 1-12):
```python
"""Thin CRUD layer owning all reads and writes to the memory tables."""

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import LongTermMemory, WorkingMemory

logger = get_logger(__name__)
```
`agent/tasks.py` follows the identical shape: `from shared.models import Task, TaskState, TaskTransition`.

**Commit/rollback + logger.info pattern to copy verbatim** (`agent/profile.py::update_profile`, lines 37-61):
```python
async def update_profile(
    session: AsyncSession,
    user_id: int,
    style: str | None = None,
    format: str | None = None,
    constraints: str | None = None,
) -> Profile:
    """Update the caller's profile fields (partial), creating the row if missing."""
    row = await get_or_create_profile(session, user_id)
    if style is not None:
        row.style = style
    if format is not None:
        row.format = format
    if constraints is not None:
        row.constraints = constraints
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("profile_updated", user_id=user_id)
    return row
```
This is the exact shape for `transition_task`/`set_paused`/`cancel_task`: load-or-404 the row, mutate fields, bump `updated_at`, `session.add`, try/commit/refresh/except-rollback-raise, `logger.info(...)`.

**Ownership-check pattern (new for this phase, but same idiom as `agent/main.py::_get_chat_or_404`, lines 82-95):**
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat {chat_id} not found",
        )
    return chat
```
`agent/tasks.py`'s `transition_task`/`set_paused`/`resume`/`cancel_task` receive an LLM- or REST-supplied `task_id` and MUST verify `task.chat_id == chat_id and task.user_id == user_id` before writing (RESEARCH.md Pitfall 2 — `task_id` is not in `agent/tools.py::_SCOPE_KEYS`, so the dispatcher does not protect this). Since `agent/tasks.py` is a plain CRUD module (no `HTTPException`/FastAPI import), raise a plain `ValueError`/return an error dict for the tool path, and let `agent/main.py`'s own `_get_task_or_404`-style helper raise the `HTTPException` for the REST path — do not import FastAPI into `agent/tasks.py`.

**Creation-writes-first-transition-row pattern (RESEARCH.md Pattern 4, follow verbatim):**
```python
async def create_task(
    session: AsyncSession, user_id: int, chat_id: int, title: str, description: str, goal: str,
) -> Task:
    task = Task(
        user_id=user_id, chat_id=chat_id, title=title, description=description,
        goal=goal, state=TaskState.PLANNING,
    )
    session.add(task)
    await session.flush()  # get task.id before writing the transition row
    session.add(TaskTransition(task_id=task.id, from_state=None, to_state=TaskState.PLANNING, note=""))
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(task)
    logger.info("task_created", chat_id=chat_id, task_id=task.id)
    return task
```

**List pattern** (`agent/memory.py::list_working_memory`, lines 15-20):
```python
async def list_working_memory(session: AsyncSession, chat_id: int) -> list[WorkingMemory]:
    """Return this chat's working memory rows, ordered by key."""
    result = await session.exec(
        select(WorkingMemory).where(WorkingMemory.chat_id == chat_id).order_by(WorkingMemory.key),
    )
    return list(result.all())
```
`list_tasks_for_chat` (flat, no "current" pointer per D-08) and `list_open_tasks` (excludes `done`/`cancelled`, used by `build_system_prompt`) follow this exact `select(...).where(...).order_by(...)` + `list(result.all())` shape. Use `.is_(None)` if any nullable-column filter is needed (none currently required here, but keep in mind for `from_state`).

---

### `agent/tools.py` (extended — service, event-driven/request-response)

**Analog:** `_save_working_memory`/`_save_long_term_memory` (lines 158-190), registry/dispatcher machinery (lines 1-22, UNCHANGED — do not modify)

**Imports to extend** (lines 10-12):
```python
from agent import memory
from agent.schemas import SaveLongTermMemoryArgs, SaveWorkingMemoryArgs
from shared.logger import get_logger
```
becomes (adding, not replacing):
```python
from agent import memory, tasks
from agent.schemas import (
    CreateTaskArgs,
    PauseTaskArgs,
    ResumeTaskArgs,
    SaveLongTermMemoryArgs,
    SaveWorkingMemoryArgs,
    TransitionTaskArgs,
)
from shared.logger import get_logger
```

**Registration pattern to copy verbatim** (lines 158-172):
```python
@register_tool(
    "save_working_memory",
    SaveWorkingMemoryArgs,
    "Save a value to the temporary scratchpad for the CURRENT chat's task data. "
    "Overwritten per key; does not persist to other chats.",
)
async def _save_working_memory(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Write a working-memory row scoped to the current chat."""
    row = await memory.save_working_memory(session, user_id, chat_id, args["key"], args["content"])
    return {"status": "saved", "layer": "working", "key": row.key, "id": row.id}
```
`create_task`/`transition_task`/`pause_task`/`resume_task` register with the identical `@register_tool(name, ArgsModel, description)` decorator + `async def _handler(session, user_id, chat_id, args) -> dict` signature. **Do not touch** `dispatch_tool_calls`, `register_tool`, or `build_tool_schemas` (lines 25-155) — STATE.md's locked decision and RESEARCH.md both confirm this dispatcher is reused unchanged.

**Critical (RESEARCH.md Pitfall 2):** unlike `_save_working_memory` (which never receives `chat_id`/`user_id` from the LLM — the dispatcher supplies them), `_transition_task`/`_pause_task`/`_resume_task` receive an LLM-supplied `task_id` in `args`. The handler must let `agent/tasks.py`'s function perform the `task.chat_id == chat_id and task.user_id == user_id` check and propagate a tool-level error (e.g. `{"status": "error", "error": "task not found"}`) rather than raising — tool handlers return `dict[str, Any]`, they don't raise HTTP errors.

---

### `agent/context_engine.py::build_system_prompt` (extended — transform)

**Analog:** the existing working/long-term memory injection blocks in the same function (lines 81-94)

**Pattern to copy verbatim (extend in place, do not fork a second function):**
```python
working = await memory.list_working_memory(session, chat_id)
if working:
    parts.append(
        "Working memory (this chat's current task data): "
        + json.dumps({row.key: row.value for row in working}),
    )

if chat is not None and chat.user_id is not None:
    long_term = await memory.list_long_term_memory(session, chat.user_id)
    if long_term:
        parts.append(
            "Long-term memory (persists across all your chats): "
            + json.dumps({row.key: row.value for row in long_term}),
        )

return "\n\n".join(parts)
```
Add, immediately before the final `return "\n\n".join(parts)`:
```python
open_tasks = await tasks.list_open_tasks(session, chat_id)
if open_tasks:
    lines = []
    for t in open_tasks:
        paused_note = " [ON PAUSE]" if t.is_paused else ""
        lines.append(
            f"- #{t.id} \"{t.title}\" (state={t.state.value}{paused_note}): "
            f"goal={t.goal!r}",
        )
    parts.append("Open tasks in this chat:\n" + "\n".join(lines))
```
Add `from agent import tasks` to the existing `from agent import memory, profile` import (line 11). `list_open_tasks` must exclude `done`/`cancelled` but INCLUDE paused tasks (TASK-04's whole point — RESEARCH.md's "Design note for the planner").

---

### `agent/main.py` (extended — controller/route, request-response + CRUD)

**Analog A — read endpoint:** `get_chat_memory` (lines 420-443)
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
    long_term = await memory.list_long_term_memory(session, current_user.id)
    path = await _build_tree_path(session, chat)
    return ChatMemoryResponse(
        chat_id=chat_id,
        short_term_message_count=len(path),
        working=[...],
        long_term=[...],
    )
```
`GET /api/v1/chats/{chat_id}/tasks` follows this exact shape: `_get_chat_or_404`, then `tasks.list_tasks_for_chat(session, chat_id)` with embedded history mapped to `TaskResponse`/`TaskTransitionResponse` lists (oldest-first per D-11).

**Analog B — UI-only REST write path bypassing the dispatcher (D-12):** `update_profile` (lines 523-532)
```python
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
**Deviation required (RESEARCH.md Pattern 2 — the one new architectural decision this phase needs):** unlike `Profile` (user-scoped, correctly lock-free), `Task` is chat-scoped, so the new pause/resume/cancel endpoints must acquire `agent/state.py::chat_locks[task.chat_id]` before writing:
```python
from agent.state import chat_locks

async def _get_task_or_404(session: AsyncSession, task_id: int, user_id: int) -> Task:
    """Load a task owned by user_id or raise HTTP 404 (never 403, IDOR-safe -- same idiom
    as _get_chat_or_404)."""
    task = await session.get(Task, task_id)
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found")
    return task


@app.post("/api/v1/tasks/{task_id}/pause", response_model=TaskResponse)
async def pause_task_endpoint(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Manually pause a task (D-12) -- bypasses agent/tools.py, but NOT the chat's lock."""
    task = await _get_task_or_404(session, task_id, current_user.id)
    if task.chat_id not in chat_locks:
        chat_locks[task.chat_id] = asyncio.Lock()
    async with chat_locks[task.chat_id]:
        row = await tasks.set_paused(session, current_user.id, task.chat_id, task_id, True)
    return _task_to_response(row)
```
`resume`/`cancel` endpoints follow the identical lock-then-write shape (`tasks.set_paused(..., False)` / `tasks.cancel_task(...)`). This mirrors the WS handler's own lock-acquisition idiom in `agent/ws.py` (lines 148-150):
```python
if chat_id not in chat_locks:
    chat_locks[chat_id] = asyncio.Lock()
async with chat_locks[chat_id]:
    async with async_session_factory() as session:
        ...
```

**`_xxx_to_response` mapper pattern to copy** (`_profile_to_response`, lines 140-148):
```python
def _profile_to_response(row: Profile) -> ProfileResponse:
    """Map a Profile ORM row to the API response schema."""
    return ProfileResponse(
        id=row.id,
        style=row.style,
        format=row.format,
        constraints=row.constraints,
        updated_at=row.updated_at,
    )
```
Add `_task_to_response(row: Task, history: list[TaskTransition]) -> TaskResponse` in the same block (near line 148), following the identical field-by-field mapping shape.

---

### `ui/static/index.html` (`#task-panel` — component markup)

**Analog:** `#profile-panel` (lines 71-92), `#memory-panel` (lines 48-70)

**Pattern to copy verbatim (structure, not content):**
```html
<div id="profile-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400">
    <h3 class="text-slate-300 font-medium mb-2">Профиль</h3>
    ...
</div>
```
**CRITICAL (RESEARCH.md Pitfall 6):** `#memory-panel`/`#profile-panel` are NOT a click-to-switch tab bar — they are always-visible, vertically-stacked `<div>`s in the same `<aside>`, separated by `border-t border-slate-800`. Despite D-10's "new sidebar tab" wording, build `#task-panel` as one more stacked `<div class="border-t border-slate-800 p-3 text-xs text-slate-400">` placed after `#profile-panel` (line 92) and before `#agent-status` (line 93) — no tab-switching JS, no `.tab-button`/`aria-selected` semantics anywhere in this codebase to mirror.

**Count-badge heading pattern** (`#memory-panel` heading, lines 49, 53, 59, 66):
```html
<h3 class="text-slate-300 font-semibold mb-2">Память</h3>
...
<span class="text-slate-500">Рабочая (этот чат)</span>
<span id="memory-working-count" class="text-white font-semibold">0</span>
```
Task panel heading: `Задачи <span id="task-count" class="text-white font-semibold">0</span>` (per UI-SPEC Copywriting Contract).

---

### `ui/static/app.js` (component, request-response)

**Analog A — load + render pair:** `loadChatMemory`/`renderMemoryPanel` (lines 252-260, 291-305)
```javascript
async function loadChatMemory(chatId) {
    try {
        const data = await apiFetch(`/api/v1/chats/${chatId}/memory`);
        state.lastMemory = data;
        renderMemoryPanel();
    } catch (err) {
        console.error('Failed to load memory:', err);
    }
}
```
`loadChatTasks(chatId)` / `renderTaskPanel()` follow this exact shape: `apiFetch` the new GET endpoint, stash into `state.lastTasks`, call a render function. Add `lastTasks: null` to the `state` object (line ~28, alongside `lastMemory`/`lastProfile`).

**Analog B — row-rendering pattern:** `renderMemoryEntries` (lines 262-289)
```javascript
function renderMemoryEntries(container, entries) {
    container.replaceChildren();
    if (!entries.length) {
        const empty = document.createElement('div');
        empty.className = 'text-slate-600';
        empty.textContent = '—';
        container.appendChild(empty);
        return;
    }
    entries.forEach((entry) => {
        const row = document.createElement('div');
        row.className = 'rounded-lg bg-slate-800 px-2 py-1';
        ...
        container.appendChild(row);
    });
}
```
Use `container.replaceChildren()` + `createElement`/`textContent` (never `innerHTML` with untrusted data — no DOMPurify needed here since all fields are plain-text, not markdown) for each task card and its history timeline entries. Empty state uses the exact `—` character per UI-SPEC.

**Analog C — button-triggered REST write + toast pattern:** `saveProfile` (lines 326-339)
```javascript
async function saveProfile() {
    const body = { style: ..., format: ..., constraints: ... };
    try {
        const data = await apiFetch('/api/v1/profile', { method: 'PUT', body: JSON.stringify(body) });
        state.lastProfile = data;
        showToast('Профиль сохранён', 'success');
    } catch (err) {
        showToast('Не удалось сохранить профиль. Проверьте соединение и попробуйте снова.', 'error');
    }
}
```
`pauseTask(taskId)`/`resumeTask(taskId)`/`cancelTask(taskId)` follow this exact `apiFetch(..., { method: 'POST' })` + try/`showToast(success)`/catch/`showToast(error)` shape, using the exact copy strings from UI-SPEC's Copywriting Contract table.

**Analog D — destructive-action confirm() pattern:** chat-delete handler (line 971)
```javascript
if (confirm('Удалить этот чат?')) {
    deleteChat(chatId).catch((err) => showToast(err.message, 'error'));
}
```
`cancelTask` must wrap its call in `confirm('Отменить эту задачу? Это действие нельзя отменить.')` before firing — the UI-SPEC explicitly calls out this exact copy and pattern; Pause/Resume fire immediately with no confirm (non-destructive/reversible).

**Analog E — WS `done` handler refresh hook** (lines 623-633):
```javascript
case 'done':
    setStreaming(false);
    removeLoadingBubble();
    unblockInput();
    state.lastFailedMessage = null;
    if (data.stats) updateStats(data.stats);
    if (state.currentChatId) {
        loadChatTree(state.currentChatId);
        loadChatMemory(state.currentChatId);
    }
    break;
```
Add `loadChatTasks(state.currentChatId);` alongside the existing `loadChatMemory(...)` call so a `create_task`/`transition_task`/`pause_task`/`resume_task` tool call made during a turn refreshes the Task panel the same way memory writes already do.

**`bindEvents()` registration pattern** (lines 975-977):
```javascript
$('btn-save-profile').addEventListener('click', () => {
    saveProfile().catch((err) => showToast(err.message, 'error'));
});
```
Per-task pause/resume/cancel buttons are rendered dynamically inside `renderTaskPanel`'s per-task DOM nodes (there is no static `#btn-*` id to bind once at `bindEvents()` time, since tasks are a dynamic list) — attach `addEventListener('click', ...)` directly on each button element as it's created, following the same inline-handler style already used for `chat-list`'s dynamically rendered per-chat buttons (see the `contextmenu` delegated-listener pattern at lines 966-974 as the alternative if event delegation on a static container is preferred instead).

---

### `tests/test_cascade_delete.py` (extended — test, CRUD)

**Analog:** `test_delete_chat_cascades_db_records` (lines 27-63)
```python
@pytest.mark.asyncio
async def test_delete_chat_cascades_db_records(authenticated_client: AsyncClient) -> None:
    """DELETE should CASCADE-remove messages, settings, and token usage."""
    async with async_session_factory() as session:
        chat = Chat(title="Cascade API", user_id=authenticated_client.seeded_user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        message = Message(chat_id=chat.id, role="user", content="Hi")
        ...
        session.add(message)
        ...
        await session.commit()
        chat_id = chat.id
        message_id = message.id
        ...

    resp = await authenticated_client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 204

    async with async_session_factory() as session:
        assert (await session.get(Chat, chat_id)) is None
        assert (await session.get(Message, message_id)) is None
        ...
```
Add a new case (or extend this one) that creates a `Task` + at least 2 `TaskTransition` rows scoped to a chat, deletes the chat via the REST endpoint, and asserts zero surviving `Task`/`TaskTransition` rows — RESEARCH.md Pitfall 5 flags this explicitly as required coverage.

---

## Shared Patterns

### Per-chat lock (concurrency guard)
**Source:** `agent/state.py::chat_locks` (lines 1-20), acquisition idiom in `agent/ws.py` (lines 148-150)
**Apply to:** `agent/main.py`'s new `POST /api/v1/tasks/{id}/pause|resume|cancel` endpoints (NOT `agent/tools.py`'s tool handlers, which already run inside the WS handler's lock)
```python
if chat_id not in chat_locks:
    chat_locks[chat_id] = asyncio.Lock()
async with chat_locks[chat_id]:
    ...
```
This is the one genuinely new architectural wrinkle this phase introduces (Task is chat-scoped, unlike user-scoped `Profile`) — "bypass the tool dispatcher" (D-12) is not license to bypass this lock.

### IDOR-safe ownership check (404, never 403)
**Source:** `agent/main.py::_get_chat_or_404` (lines 82-95)
**Apply to:** `agent/tasks.py`'s task-mutating functions (ownership check on `task.chat_id`/`task.user_id`) and a new `agent/main.py::_get_task_or_404` helper for the REST layer.
```python
if chat is None or chat.user_id != user_id:
    logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chat {chat_id} not found")
```

### Commit/rollback discipline
**Source:** every write function in `agent/memory.py`/`agent/profile.py`
**Apply to:** every write in `agent/tasks.py` and every REST endpoint in `agent/main.py`.
```python
session.add(row)
try:
    await session.commit()
    await session.refresh(row)
except Exception:
    await session.rollback()
    raise
```

### FK-cascade table convention
**Source:** `shared/models.py::WorkingMemory`/`LongTermMemory` (lines 138-185), `Settings.strategy` SAEnum idiom (lines 90-98)
**Apply to:** `Task`/`TaskTransition` table definitions.
```python
sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
```
Never `Field(foreign_key=...)`. Always pair `SAEnum(EnumCls, values_callable=lambda enum_cls: [m.value for m in enum_cls])` with any new enum column.

### Toast + error-copy template
**Source:** `ui/static/app.js::showToast` (lines 34-47), `saveProfile`'s catch block (line 337)
**Apply to:** all four task REST calls in `app.js`.
```javascript
showToast('Не удалось {action}. Проверьте соединение и попробуйте снова.', 'error');
```

## No Analog Found

None — every file this phase touches has a direct, recently-shipped (Phase 2/3) in-codebase analog. No RESEARCH.md-only patterns were needed.

## Conventions

Derivation was run via the shared `gsd-tools.cjs verify conventions --derive` module (repo-wide; a `--scope agent` pass returned `no-readable-files` because the tool's file-name/export/import heuristics target JS/TS-shaped source and this project's new-file surface is almost entirely Python).

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | camel | n/a (total=1) | n/a | insufficient-data |
| Identifier casing | camel | 100% (52/52) | 0 | named contract |
| Export style | — | n/a (total=0) | n/a | insufficient-data |
| Import style | — | n/a (total=0) | n/a | insufficient-data |

**Contested hotspots (author's choice):** The derivation tool's axes above only reflect `ui/static/app.js` (the sole JS-parseable source in this repo) — its 52 camelCase identifiers are a real, uncontested (100% share) local convention for that one file and should be matched exactly for the new `loadChatTasks`/`renderTaskPanel`/`pauseTask`/`resumeTask`/`cancelTask` functions and `state.lastTasks` field. The tool has no Python heuristics, so it cannot score `agent/`, `shared/`, or `tests/` — for those, CLAUDE.md's own documented conventions are the named contract instead (100% consistent across the existing codebase, verified by direct reading in this session): `snake_case` module/function/variable names, `PascalCase` classes, `UPPER_CASE` constants, `_`-prefixed private helpers, stdlib→third-party→local import ordering. This project's repo-wide "contested split" precedent (per the standard playbook note) is the CJS↔SDK dual resolver pattern used in gsd-plugin tooling itself, not anything present in this codebase — not applicable here since this project has no such dual-runtime boundary; the only real per-directory style boundary in *this* repo is the Python-backend/vanilla-JS-frontend split already documented in CLAUDE.md (snake_case Python vs. camelCase JS), and each side is internally 100%-consistent, so new files should match whichever side (Python or JS) they land in.

## Metadata

**Analog search scope:** `agent/`, `shared/models.py`, `ui/static/`, `tests/test_cascade_delete.py` (Phase 2/3-era files identified in CONTEXT.md/RESEARCH.md's "Reusable Assets"/"Integration Points" sections)
**Files scanned:** `agent/tools.py`, `agent/schemas.py`, `agent/memory.py`, `agent/profile.py`, `shared/models.py`, `agent/state.py`, `agent/main.py`, `agent/context_engine.py`, `agent/ws.py`, `ui/static/index.html`, `ui/static/app.js`, `tests/test_cascade_delete.py` (12 files read this session)
**Pattern extraction date:** 2026-09-20
