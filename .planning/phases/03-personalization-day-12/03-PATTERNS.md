# Phase 3: Personalization (Day 12) - Pattern Map

**Mapped:** 2026-09-20
**Files analyzed:** 10 (2 modified core backend files + 1 new backend module + 1 schemas addition + 1 modified injection function + 2 modified frontend files + 3 new test files)
**Analogs found:** 10 / 10

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `shared/models.py` (+`Profile` table) | model | CRUD | `LongTermMemory` class, `shared/models.py:165-186` | exact |
| `agent/profile.py` (new) | service | CRUD | `agent/memory.py` (whole file) | exact |
| `agent/schemas.py` (+`ProfileUpdate`/`ProfileResponse`) | model (DTO) | request-response | `SettingsUpdate`/`SettingsResponse`, `agent/schemas.py:62-95` | exact |
| `agent/main.py` (+`GET`/`PUT /api/v1/profile`) | controller/route | CRUD | `update_settings`/`get_settings`/`_ensure_global_settings`, `agent/main.py:54-69,450-489` | exact |
| `agent/context_engine.py::build_system_prompt` (modified) | service | transform | same function, existing memory-injection blocks, `agent/context_engine.py:61-88` | exact (in-place extension) |
| `ui/static/index.html` (+`#profile-panel`) | component | request-response | `#memory-panel` block, `ui/static/index.html:48-70`; textarea markup from Settings modal, `ui/static/index.html:200-209` | exact |
| `ui/static/app.js` (+`loadProfile`/`renderProfilePanel`/`saveProfile`) | component | CRUD | `loadChatMemory`/`renderMemoryPanel`/`renderMemoryEntries` (`app.js:251-304`) + `openSettingsModal`/`saveSettings` (`app.js:773-853`) | exact |
| `tests/test_profile.py` (new) | test | CRUD | `tests/test_memory.py:1-50` | exact |
| `tests/test_profile_api.py` (new) | test | request-response | `tests/test_memory_api.py` (endpoint-level tests, same shape as `test_memory.py` but via `authenticated_client`) | exact |
| `tests/test_context_engine_profile.py` (new) | test | transform | `tests/test_context_engine.py` (existing injection-block tests for facts/summary/memory) | role-match |

## Pattern Assignments

### `shared/models.py` (model, CRUD)

**Analog:** `LongTermMemory` (`shared/models.py:165-186`)

**FK-cascade + unique-per-user pattern** (lines 165-186):
```python
class LongTermMemory(SQLModel, table=True):
    """User-scoped, cross-chat memory for profile/decisions/knowledge (D-02)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_long_term_memory_user_key"),)
```

**Adapt for `Profile`:** swap the `key`/`value`/`UniqueConstraint("user_id", "key")` shape for three fixed fields (`style`, `format`, `constraints`), and make `user_id` itself `unique=True` (one row per user, not one row per key) instead of a composite constraint — mirrors D-01's exact target shape already drafted in `03-RESEARCH.md`'s Code Examples section:
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
    format: str = Field(default="", max_length=2000)
    constraints: str = Field(default="", max_length=2000)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
```
**Import convention:** `Column`, `ForeignKey`, `Integer` already imported at the top of `shared/models.py:7` — no new imports needed for the table itself.

---

### `agent/profile.py` (service, CRUD) — new file

**Analog:** `agent/memory.py` (full file, `agent/memory.py:1-136`)

**Imports pattern** (lines 1-12):
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
Adapt: `from shared.models import Profile`, module docstring `"""Thin CRUD layer owning reads and writes to the profile table."""`.

**Lazy get-or-create pattern** — mirrors `_ensure_global_settings` (`agent/main.py:54-69`), not `save_long_term_memory`'s overwrite-existing-row shape, since Profile is a true one-row-per-user singleton (no key/value list):
```python
# Source: agent/main.py:54-69 (_ensure_global_settings, the closest lazy-singleton precedent)
async def _ensure_global_settings(session: AsyncSession, user_id: int | None) -> Settings:
    """Create default global settings row for the given owner when missing."""
    conditions = [Settings.chat_id.is_(None)]
    if user_id is None:
        conditions.append(Settings.user_id.is_(None))
    else:
        conditions.append(Settings.user_id == user_id)
    result = await session.exec(select(Settings).where(*conditions))
    row = result.first()
    if row is not None:
        return row
    row = Settings(chat_id=None, user_id=user_id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
```
Adapt into `agent/profile.py::get_or_create_profile(session, user_id) -> Profile`:
```python
async def get_or_create_profile(session: AsyncSession, user_id: int) -> Profile:
    """Return the caller's profile row, creating an empty-field row on first access."""
    result = await session.exec(select(Profile).where(Profile.user_id == user_id))
    row = result.first()
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

**Update pattern** — mirrors `save_long_term_memory`'s commit/rollback try-except and `logger.info` on write (`agent/memory.py:85-135`), but simpler since it always operates on the single fetched-or-created row rather than searching by key:
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

**Error handling pattern:** every write wrapped in `try: await session.commit(); await session.refresh(row) except Exception: await session.rollback(); raise` — identical to both `save_working_memory` and `save_long_term_memory`.

---

### `agent/schemas.py` (+`ProfileUpdate`/`ProfileResponse`)

**Analog:** `SettingsUpdate`/`SettingsResponse` (`agent/schemas.py:62-95`)

**Constant + partial-update schema pattern**:
```python
# Source: agent/schemas.py:62-81 (SettingsUpdate)
class SettingsUpdate(BaseModel):
    """Partial settings update for global or per-chat scope."""

    chat_id: Optional[int] = None
    system_prompt: Optional[str] = Field(
        default=None,
        max_length=SYSTEM_PROMPT_MAX_LENGTH,
    )
    ...
```
Adapt — add a `PROFILE_FIELD_MAX_LENGTH = 2000` constant near the other `*_MAX_LENGTH` constants (line 11-20), then:
```python
class ProfileUpdate(BaseModel):
    """Partial profile update (style/format/constraints), always scoped to current_user."""

    style: Optional[str] = Field(default=None, max_length=PROFILE_FIELD_MAX_LENGTH)
    format: Optional[str] = Field(default=None, max_length=PROFILE_FIELD_MAX_LENGTH)
    constraints: Optional[str] = Field(default=None, max_length=PROFILE_FIELD_MAX_LENGTH)


class ProfileResponse(BaseModel):
    """Serialized profile returned to the client."""

    id: int
    style: str = Field(default="", max_length=PROFILE_FIELD_MAX_LENGTH)
    format: str = Field(default="", max_length=PROFILE_FIELD_MAX_LENGTH)
    constraints: str = Field(default="", max_length=PROFILE_FIELD_MAX_LENGTH)
    updated_at: datetime
```
Note: no `chat_id` field (unlike `SettingsUpdate`) — Profile has no per-chat scope (D-01), it is always resolved from `current_user.id` server-side (see Pattern 2 below), so the body never carries an identity field at all.

---

### `agent/main.py` (+`GET`/`PUT /api/v1/profile`)

**Analog:** `get_settings`/`update_settings`/`_ensure_global_settings` (`agent/main.py:54-69, 450-489`)

**Imports to add** (extend existing import blocks, `agent/main.py:15-47`):
```python
from agent import memory          # existing — add "profile" alongside it:
from agent import memory, profile
```
and extend the `agent.schemas` import block (`agent/main.py:16`) with `ProfileResponse, ProfileUpdate`, and the `shared.models` import (`agent/main.py:47`) with `Profile` if a response-builder needs the type (not required for endpoint bodies since `profile.py` returns the SQLModel row directly).

**GET endpoint pattern** (mirrors `get_settings`, `agent/main.py:450-458`):
```python
@app.get("/api/v1/settings", response_model=SettingsResponse)
async def get_settings(
    chat_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SettingsResponse:
    """Return effective settings with global fallback, scoped to the caller."""
    row = await _resolve_settings(session, chat_id, current_user.id)
    return _settings_to_response(row)
```
Adapt (no `chat_id` param — profile is never per-chat):
```python
@app.get("/api/v1/profile", response_model=ProfileResponse)
async def get_profile(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Return the caller's profile, lazily creating an empty row on first access."""
    row = await profile.get_or_create_profile(session, current_user.id)
    return _profile_to_response(row)
```

**PUT endpoint pattern — owner-scoped, no client-supplied user_id** (mirrors `update_settings`, `agent/main.py:461-489`):
```python
# Source: agent/main.py:461-489 (update_settings)
@app.put("/api/v1/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SettingsResponse:
    """Create or update global or per-chat settings owned by the caller."""
    if body.chat_id is None:
        row = await _ensure_global_settings(session, current_user.id)
    else:
        ...
    updates = body.model_dump(exclude_unset=True, exclude={"chat_id"})
    for field, value in updates.items():
        setattr(row, field, value)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    return _settings_to_response(row)
```
Adapt — this is simpler than `update_settings` because there's no chat_id branch at all, and the field-setting loop can be delegated straight to `profile.update_profile()` instead of inlined `setattr`:
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

**Response-builder helper pattern** — mirrors `_settings_to_response` (find and follow its exact shape near the other `_*_to_response` helpers in `agent/main.py`):
```python
def _profile_to_response(row: Profile) -> ProfileResponse:
    return ProfileResponse(
        id=row.id,
        style=row.style,
        format=row.format,
        constraints=row.constraints,
        updated_at=row.updated_at,
    )
```

**Error handling pattern:** identical commit/rollback try-except used throughout `agent/main.py` — always `await session.rollback()` on exception, then `raise` (never swallow).

**Pitfall — table registration (from RESEARCH.md Pitfall 1):** `shared/database.py` does not import `WorkingMemory`/`LongTermMemory`/`Profile` directly; those get registered transitively via `agent/main.py`'s `from agent import memory` import. Adding `from agent import profile` to `agent/main.py`'s import block is what makes `SQLModel.metadata.create_all()` pick up the new `profile` table — do not skip this import even if it looks unused at first glance (it is used, via `profile.get_or_create_profile`/`profile.update_profile`).

---

### `agent/context_engine.py::build_system_prompt` (modified in place)

**Analog:** the function's own existing working-memory/long-term-memory injection blocks (`agent/context_engine.py:61-88`)

**Current structure to extend**:
```python
# Source: agent/context_engine.py:61-88
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    """Build the system prompt with optional facts and memory layers."""
    settings_row = await get_effective_settings(session, chat_id)
    parts = [settings_row.system_prompt or "You are a helpful assistant."]
    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")

    working = await memory.list_working_memory(session, chat_id)
    if working:
        parts.append(
            "Working memory (this chat's current task data): "
            + json.dumps({row.key: row.value for row in working}),
        )

    chat = await session.get(Chat, chat_id)
    if chat is not None and chat.user_id is not None:
        long_term = await memory.list_long_term_memory(session, chat.user_id)
        if long_term:
            parts.append(
                "Long-term memory (persists across all your chats): "
                + json.dumps({row.key: row.value for row in long_term}),
            )

    return "\n\n".join(parts)
```
**Adapt:** hoist the `chat = await session.get(Chat, chat_id)` lookup to right after `parts = [...]` (do not duplicate the query), insert the profile block directly after the base system prompt (per CONTEXT.md's discretion + RESEARCH.md's recommendation to make it a strong early directive), and reuse `chat` for both the new profile block and the existing long-term-memory block:
```python
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    """Build the system prompt with optional facts, profile, and memory layers."""
    settings_row = await get_effective_settings(session, chat_id)
    parts = [settings_row.system_prompt or "You are a helpful assistant."]

    chat = await session.get(Chat, chat_id)
    if chat is not None and chat.user_id is not None:
        profile_row = await profile.get_or_create_profile(session, chat.user_id)
        profile_text = _format_profile(profile_row)
        if profile_text:
            parts.append(profile_text)

    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")

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
**New import needed:** `from agent import profile` at the top of `agent/context_engine.py` alongside the existing `from agent import memory` (`agent/context_engine.py:11`).

**Formatting helper** (new private function, mirrors the inline-formatting style already used for facts/working/long-term blocks — no separate module, just a module-level `_`-prefixed helper next to `_parse_facts_json`):
```python
def _format_profile(row: Profile) -> str:
    """Render non-empty profile fields as a system-prompt directive; empty profile -> ''."""
    fields = []
    if row.style.strip():
        fields.append(f"style={row.style.strip()!r}")
    if row.format.strip():
        fields.append(f"format={row.format.strip()!r}")
    if row.constraints.strip():
        fields.append(f"constraints={row.constraints.strip()!r}")
    if not fields:
        return ""
    return "User's stated preferences (always follow these): " + ", ".join(fields)
```
**Import addition:** `Profile` must be added to the `from shared.models import Chat, ContextStrategy, Message, Settings` line (`agent/context_engine.py:14`) for the type hint.

---

### `ui/static/index.html` (+`#profile-panel`)

**Analog:** `#memory-panel` block (`ui/static/index.html:48-70`) for the stacked-panel container; Settings modal's textarea (`ui/static/index.html:200-209`) for the field markup.

**Panel container pattern** (lines 48-49, container classes to reuse verbatim per UI-SPEC's "Documented Exception" note):
```html
<div id="memory-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-72">
    <h3 class="text-slate-300 font-semibold mb-2">Память</h3>
    ...
</div>
```
Adapt — per UI-SPEC, heading weight is `font-medium` (500) not `font-semibold` (600), placed as a second stacked `<div>` below `#memory-panel` and above `#agent-status` (`ui/static/index.html:70-71` insertion point):
```html
<div id="profile-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400">
    <h3 class="text-slate-300 font-medium mb-2">Профиль</h3>
    <div class="mb-2">
        <label for="profile-style" class="block text-sm text-slate-400 mb-1">Стиль общения</label>
        <textarea id="profile-style" rows="2" placeholder="Например: коротко и по делу, без длинных вступлений"
            class="w-full resize-none rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"></textarea>
    </div>
    <div class="mb-2">
        <label for="profile-format" class="block text-sm text-slate-400 mb-1">Формат ответа</label>
        <textarea id="profile-format" rows="2" placeholder="Например: списком, с примерами кода"
            class="w-full resize-none rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"></textarea>
    </div>
    <div class="mb-2">
        <label for="profile-constraints" class="block text-sm text-slate-400 mb-1">Ограничения</label>
        <textarea id="profile-constraints" rows="2" placeholder="Например: не используй жаргон, всегда указывай источники"
            class="w-full resize-none rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"></textarea>
    </div>
    <button id="btn-save-profile" type="button"
        class="w-full rounded-lg bg-indigo-600 hover:bg-indigo-500 px-4 py-2 text-sm font-medium transition">
        Сохранить профиль
    </button>
</div>
```
**Textarea field classes copied verbatim** from Settings' `settings-system-prompt` (`ui/static/index.html:207-208`):
```html
<textarea id="settings-system-prompt" rows="3"
    class="w-full resize-none rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm"></textarea>
```
(profile textareas add `focus:outline-none focus:ring-2 focus:ring-indigo-500` per UI-SPEC's Component Notes — this is additive, not a deviation, since it's already Tailwind's standard focus-ring utility used elsewhere in the app for inputs.)

---

### `ui/static/app.js` (+`loadProfile`/`renderProfilePanel`/`saveProfile`)

**Analog:** `loadChatMemory`/`renderMemoryPanel`/`renderMemoryEntries` (`app.js:251-304`) for load/render shape; `openSettingsModal`/`saveSettings` (`app.js:773-853`) for the edit-form value get/set and PUT-submit shape.

**State field to add** (mirrors `lastMemory`, `app.js:28`):
```javascript
const state = {
    ...
    lastMemory: null,
    lastProfile: null,   // NEW
};
```

**Load pattern** (mirrors `loadChatMemory`, `app.js:251-259` — but profile is user-scoped, not chat-scoped, so it's called once from `init()`, not per chat-select):
```javascript
// Source: app.js:251-259
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
Adapt:
```javascript
async function loadProfile() {
    try {
        const data = await apiFetch('/api/v1/profile');
        state.lastProfile = data;
        renderProfilePanel();
    } catch (err) {
        console.error('Failed to load profile:', err);
    }
}

function renderProfilePanel() {
    const data = state.lastProfile;
    if (!data) return;
    $('profile-style').value = data.style;
    $('profile-format').value = data.format;
    $('profile-constraints').value = data.constraints;
}
```
**XSS-safety note (RESEARCH.md Pitfall 3):** use `.value` on the `<textarea>` elements, never `.innerHTML` — matches `renderMemoryEntries()`'s `.textContent` discipline (`app.js:274-282`) and `openSettingsModal()`'s `.value` usage (`app.js:793`).

**Save pattern** (mirrors `saveSettings`, `app.js:830-853`):
```javascript
// Source: app.js:830-847
async function saveSettings(event) {
    event.preventDefault();
    const perChat = $('settings-per-chat').checked;
    const body = {
        strategy: $('settings-strategy').value,
        ...
        system_prompt: $('settings-system-prompt').value,
    };
    ...
    await apiFetch('/api/v1/settings', { method: 'PUT', body: JSON.stringify(body) });
    showToast('Настройки сохранены', 'success');
    closeSettingsModal();
    ...
}
```
Adapt (no modal to close; per UI-SPEC's error-state copy, wrap in try/catch since this isn't inside a `<form>` submit handler with implicit validation):
```javascript
async function saveProfile() {
    const body = {
        style: $('profile-style').value,
        format: $('profile-format').value,
        constraints: $('profile-constraints').value,
    };
    try {
        const data = await apiFetch('/api/v1/profile', { method: 'PUT', body: JSON.stringify(body) });
        state.lastProfile = data;
        showToast('Профиль сохранён', 'success');
    } catch (err) {
        showToast('Не удалось сохранить профиль. Проверьте соединение и попробуйте снова.', 'error');
    }
}
```
**Wire into `init()`** (mirrors `await loadModels()` / `await loadChats()` calls in `app.js:942-957`): add `await loadProfile();` once, alongside the other one-time startup loads — not inside `selectChat()`, since profile is user-scoped not chat-scoped (per UI-SPEC's "Load-on-select" note).

**Button binding** — find `bindEvents()` (referenced at `app.js:943`) and add:
```javascript
$('btn-save-profile').addEventListener('click', saveProfile);
```

---

### `tests/test_profile.py` (new)

**Analog:** `tests/test_memory.py:1-50`

**Fixture + CRUD test shape**:
```python
# Source: tests/test_memory.py:1-37
"""Tests for agent/memory.py CRUD semantics: overwrite, scoping, and cascade."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import memory
from shared.database import async_session_factory
from shared.models import Chat, LongTermMemory, WorkingMemory


async def _create_chat(user_id: int, title: str = "Memory chat") -> int:
    async with async_session_factory() as session:
        chat = Chat(title=title, user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


@pytest.mark.asyncio
async def test_save_working_memory_overwrites_same_key(
    authenticated_client: AsyncClient,
) -> None:
    """Saving the same key twice updates in place; total row count stays 1."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_id, "task", "first value")
        await memory.save_working_memory(session, user_id, chat_id, "task", "second value")

    async with async_session_factory() as session:
        result = await session.exec(select(WorkingMemory).where(WorkingMemory.chat_id == chat_id))
        rows = result.all()
        assert len(rows) == 1
        assert rows[0].value == "second value"
```
Adapt: use `agent.profile.get_or_create_profile`/`update_profile`, `authenticated_client.seeded_user_id`, and assert (a) lazy-create produces an empty-field row, (b) repeated `update_profile` calls update the same row (row count stays 1 per user), (c) cascade-delete on `User` removes the `Profile` row (mirrors `test_cascade_delete.py`'s pattern, referenced in CLAUDE.md).

### `tests/test_profile_api.py` (new)

**Analog:** `tests/test_memory_api.py` (endpoint-level, `authenticated_client` + `apiFetch`-equivalent `httpx.AsyncClient` calls to `GET`/`PUT` routes) — same fixture usage as `test_profile.py` but asserting HTTP status codes/response bodies for `GET /api/v1/profile` and `PUT /api/v1/profile`, mirroring however `test_memory_api.py` asserts `GET /api/v1/chats/{chat_id}/memory`.

### `tests/test_context_engine_profile.py` (new)

**Analog:** existing `tests/test_context_engine.py` injection-block tests (assert `build_system_prompt()`'s returned string contains/excludes expected substrings for facts/summary/memory) — new tests assert: (a) empty profile produces no `"User's stated preferences"` substring, (b) a saved profile's `style`/`format`/`constraints` values appear in the returned prompt, (c) profile appears for a chat's very first message (per RESEARCH.md Pitfall 2 — not conditional on message count or strategy).

---

## Shared Patterns

### Commit/Rollback try-except
**Source:** `agent/memory.py:48-52` (and every write in `agent/main.py`)
**Apply to:** `agent/profile.py::get_or_create_profile`, `update_profile`; `agent/main.py::update_profile` endpoint (delegates to the CRUD function, so inherits this automatically)
```python
session.add(row)
try:
    await session.commit()
    await session.refresh(row)
except Exception:
    await session.rollback()
    raise
```

### Owner-scoped resource resolution (no client-supplied identity)
**Source:** `agent/main.py::update_settings` (`agent/main.py:461-489`), `agent/main.py::_ensure_global_settings`
**Apply to:** both new profile REST endpoints — always resolve via `current_user.id` from `Depends(get_current_user)`, never accept a `user_id` field in `ProfileUpdate`'s request body.

### structlog logging on write
**Source:** `agent/memory.py:54`, `:81`, `:107`, `:134` — `logger.info("<entity>_saved", <scope_id>=..., key=...)`
**Apply to:** `agent/profile.py` — `logger.info("profile_created", user_id=user_id)` on lazy-create, `logger.info("profile_updated", user_id=user_id)` on update. `logger = get_logger(__name__)` declared once at module top per project convention.

### Table auto-registration via transitive import
**Source:** `agent/main.py:35` (`from agent import memory`), confirmed by `shared/database.py:18`'s comment that `Chat, Message, Session, Settings, TokenUsage, User` are the only models imported directly there
**Apply to:** `agent/main.py` must add `from agent import profile` (or otherwise import `Profile`) at module load time, or `SQLModel.metadata.create_all()` silently never creates the `profile` table (RESEARCH.md Pitfall 1).

### Frontend: `.value`/`.textContent`, never `.innerHTML`, for user-supplied text
**Source:** `app.js::renderMemoryEntries` (`.textContent`, lines 274-282), `app.js::openSettingsModal` (`.value`, line 793)
**Apply to:** `renderProfilePanel()`'s textarea population, `saveProfile()`'s value reads.

### Frontend: `apiFetch()` wrapper for all REST calls
**Source:** `app.js:48-65`
**Apply to:** `loadProfile()`, `saveProfile()` — both must go through `apiFetch()`, never a raw `fetch()`, to inherit the 401-redirect and error-toast-friendly `Error` throw.

## No Analog Found

None — every file in this phase has a direct, structurally identical precedent already in the codebase (Settings for the REST/lazy-create shape, LongTermMemory/WorkingMemory for the SQLModel FK-cascade + CRUD-module shape, Memory panel for the sidebar-UI shape). This is expected: RESEARCH.md's own conclusion is that "this phase's entire job is 'do it exactly like Settings/Memory did it.'"

## Conventions

**Scope note:** this repository is Python-dominant (FastAPI/SQLModel backend); the deterministic convention-derivation tool used here (`bin/gsd-tools.cjs verify conventions --derive`) only scans JS/TS-style source, so it picked up exclusively `ui/static/app.js` (49 identifiers) — no Python files were in scope for this tool's axes. Python-side conventions are separately documented, and already followed by every analog above, in `CLAUDE.md` §Code conventions and §Conventions (e.g. `snake_case` functions/modules, `PascalCase` classes, `_`-prefixed private helpers, `UPPER_CASE` constants).

Ran: `node bin/gsd-tools.cjs verify conventions --derive` (repo-wide) and `--scope ui/static` (same result — confirms `ui/static/app.js` is the only file contributing data):

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| file-name casing | — | — | — | insufficient-data (only 1 JS file in scope, `app.js`) |
| identifier casing | camel | 100% | 0 | named contract |
| export style | — | — | — | insufficient-data (no `export`/`module.exports` statements found — this app has no JS modules, everything is one global `<script>`) |
| import style | — | — | — | insufficient-data (same reason — no ES module imports in the vanilla-JS frontend) |

**Contested hotspots (author's choice):** none detected in this phase's scope — `identifier-casing` is a clean 100%-camelCase contract for `ui/static/app.js`, matching every existing example extracted above (`loadChatMemory`, `renderMemoryPanel`, `apiFetch`, etc.); the new `loadProfile`/`renderProfilePanel`/`saveProfile` functions should follow the same camelCase convention with no deviation. (This project's actual "intentional dual-convention" precedent — the CJS `bin/lib/**` vs. ESM `sdk/src/**` split — belongs to the `gsd-plugin` tool that generated this document, not to this Python/vanilla-JS codebase; it is not relevant here and no analogous split exists in this repo to preserve.)

## Metadata

**Analog search scope:** `shared/models.py`, `agent/memory.py`, `agent/main.py`, `agent/context_engine.py`, `agent/schemas.py`, `ui/static/index.html`, `ui/static/app.js`, `tests/test_memory.py`, `tests/test_memory_api.py`, `shared/database.py`
**Files scanned:** 10 read directly (full or targeted ranges) + 3 test files located via Glob
**Pattern extraction date:** 2026-09-20
