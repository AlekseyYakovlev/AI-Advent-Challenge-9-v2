# Phase 11: Edit and delete long-term memory entries via UI (Day 21) - Research

**Researched:** 2026-10-02
**Domain:** Brownfield FastAPI REST + vanilla-JS sidebar panel (SQLModel/SQLite, user-scoped CRUD)
**Confidence:** HIGH (codebase is the primary source; no new libraries)

## Summary

Long-term memory is the `LongTermMemory` table (`shared/models.py:175`): `id`, `user_id` (FK CASCADE), `key` (<=200), `value` (<=50 000), `created_at`, `updated_at`, with a unique constraint `(user_id, key)`. All access goes through `agent/memory.py`, which today has only `list_long_term_memory` and `save_long_term_memory` (upsert by key, used by the LLM tool and the headless scheduler). The only REST surface is read-only `GET /api/v1/chats/{chat_id}/memory`. **No PUT/PATCH/DELETE exists for memory entries.** The UI (`renderMemoryEntries` in `ui/static/app.js:297`) renders a read-only row (key + truncated value) inside the collapsed sidebar `#memory-panel`; there are no buttons.

The system prompt is rebuilt from the DB on every turn (`build_system_prompt` -> `memory.list_long_term_memory`, and `headless.py` does the same per run). There is no memory cache in `agent/state.py` and no memory event on `/ws/events`. So an edit or delete takes effect on the next turn with no cache invalidation, no event broadcast, and no stats change (long-term memory is not part of `compute_chat_stats`; it is only part of the system prompt text).

The work is small and self-contained: 3 CRUD helpers in `agent/memory.py`, one Pydantic update schema, two user-scoped endpoints, an inline edit/delete UI in the existing sidebar panel (no modal, so no conflict with Phase 10), tests, and a docs sync (the memory endpoint is currently undocumented in `docs/API_SPEC.md`, and `docs/USER_GUIDE.md` has no Memory section).

**Primary recommendation:** Add `PUT /api/v1/memory/long-term/{entry_id}` and `DELETE /api/v1/memory/long-term/{entry_id}` (user-scoped, foreign id -> 404, key collision -> 409), and render per-entry "Редактировать" / "Удалить" buttons with an inline edit form inside `#memory-long-term`; delete uses native `confirm()` like every other delete in the app.

<user_constraints>
## User Constraints (from CONTEXT.md)

No CONTEXT.md exists for this phase (user chose to plan from research and the roadmap goal only). The only locked text is the ROADMAP goal:

### Locked Decisions
- Goal (verbatim): "the user must be able to edit long-term memory fields through the UI ("Редактировать" and "Удалить" buttons per entry)". Button labels are therefore "Редактировать" and "Удалить".
- Branch `Day21` (shared with Phases 9-12); do not create/rename/switch branches. Depends on Phase 8. UI hint: yes.

### Claude's Discretion
- Everything else (endpoint shape, which fields are editable, inline vs modal, confirmation style). Recommendations below.

### Deferred Ideas (OUT OF SCOPE)
- None recorded. Treat as out of scope: editing *working* memory, creating long-term entries from the UI ("Добавить"), live cross-tab sync via `/ws/events`, bulk delete, undo.
</user_constraints>

<phase_requirements>
## Phase Requirements

ROADMAP says "Requirements: TBD". No `MEMUI-*` IDs exist anywhere in `.planning/` (checked); v1.0 uses `MEM-01..05` (archived in `.planning/milestones/v1.0-REQUIREMENTS.md`), so `MEMUI-` does not collide. Proposed (planner adds them to `.planning/REQUIREMENTS.md` under a new "### Long-term memory editing (Day 21)" heading, same style as TITLE-/SCHED-, and to ROADMAP Phase 11 "Requirements"):

| ID | Description | Research Support |
|----|-------------|------------------|
| MEMUI-01 | Each entry in the sidebar long-term memory list has "Редактировать" and "Удалить" buttons; the working-memory list is unchanged (read-only) | `renderMemoryEntries` is shared by both lists -> needs an `editable` option |
| MEMUI-02 | Editing opens an inline form (key input + value textarea, prefilled with the FULL value, not the 160-char truncation) with "Сохранить" / "Отмена"; saving updates the row, refreshes the panel and shows a toast | Pattern 2; `entry.value` is full in the API response |
| MEMUI-03 | `PUT /api/v1/memory/long-term/{entry_id}` updates `key` and/or `value` of the caller's own entry; server-side validation (strip, non-blank, <=200 / <=50 000, at least one field); `updated_at` is refreshed, `created_at` kept; a foreign or unknown id returns 404 | Pattern 1; `MEMORY_KEY_MAX_LENGTH` / `MEMORY_VALUE_MAX_LENGTH` |
| MEMUI-04 | Renaming a key onto another existing key of the same user returns 409 with a Russian message and changes nothing (unique `(user_id,key)`) | Pitfall 1 |
| MEMUI-05 | `DELETE /api/v1/memory/long-term/{entry_id}` removes the caller's own entry (204; foreign/unknown id -> 404); the UI asks `confirm()` first and refreshes the list | Pattern 3 |
| MEMUI-06 | An edit/delete takes effect on the next turn: the next `build_system_prompt` (and headless run) reflects the new content; pytest covers CRUD, scoping, validation, conflict and prompt effect; docs (API_SPEC, TESTING_GUIDE, USER_GUIDE, ARCHITECTURE) are synced; the full suite passes | Test plan, docs list |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Persist/update/delete a long-term row | Database (SQLite via `agent/memory.py`) | — | All memory reads/writes are owned by the thin CRUD layer |
| Ownership + validation + 404/409 mapping | API / Agent (`agent/main.py`, `agent/schemas.py`) | — | Matches every other user-scoped resource (MCP servers, tasks) |
| Edit/delete buttons, inline form, confirm | Browser (`ui/static/app.js`, `index.html`) | — | Vanilla JS, rendered into `#memory-long-term` |
| Effect on LLM context | Agent (`build_system_prompt`, `headless._build_*`) | — | Reads DB each turn; nothing to invalidate |
| Static file serving | UI process | — | Unchanged |

## Standard Stack

No new packages. Everything uses what is already pinned.

| Library | Purpose | Note |
|---------|---------|------|
| FastAPI / Pydantic v2 | Endpoints + `LongTermMemoryUpdate` schema (`field_validator`, `model_validator`) | Already used in `agent/schemas.py` [VERIFIED: codebase] |
| SQLModel / SQLAlchemy async | `session.get`/`select().where()`, `session.delete`, `IntegrityError` catch | Pattern already in `memory.py`, `main.py::create_user` [VERIFIED: codebase] |
| pytest + pytest-asyncio + httpx `AsyncClient` | Tests with `authenticated_client` / `second_authenticated_client` fixtures | `tests/conftest.py` [VERIFIED: codebase] |
| Vanilla JS (`apiFetch`, `showToast`, `$`) | UI | No bundler, no npm (hard constraint) |

## Package Legitimacy Audit

No external packages are installed in this phase. Not applicable. Nothing tagged `[ASSUMED]` for packages.

## Architecture Patterns

### Data flow

```
Browser sidebar #memory-long-term
  [Редактировать] -> inline form (state.editingMemoryId + draft) -> [Сохранить]
        |  PUT  /api/v1/memory/long-term/{id}  {key?, value?}   (cookie auth, JSON)
        v
  agent/main.py endpoint -> get_current_user -> memory.update_long_term_memory(session, user_id, id, ...)
        |   not found / other user's -> 404 ; duplicate key -> 409 ; blank/too long -> 422
        v
  LongTermMemory row (UPDATE, updated_at=now)  --> 200 MemoryEntryResponse
        |
  UI: toast + loadChatMemory(currentChatId) -> renderMemoryPanel

  [Удалить] -> confirm() -> DELETE /api/v1/memory/long-term/{id} -> 204 -> toast + loadChatMemory

Next chat turn / scheduler run:
  build_system_prompt / headless -> memory.list_long_term_memory(user_id) -> fresh DB rows
  (no cache, no event; LLM tool save_long_term_memory upserts by key as before)
```

### Recommended file changes
```
agent/memory.py      # + get_long_term_memory, update_long_term_memory, delete_long_term_memory
agent/schemas.py     # + LongTermMemoryUpdate
agent/main.py        # + PUT/DELETE /api/v1/memory/long-term/{entry_id}
ui/static/app.js     # renderMemoryEntries(editable), edit state, update/delete fns
ui/static/index.html # (likely no change; only if a container/aria hook is needed)
tests/test_memory_api.py (or new tests/test_memory_edit_api.py), tests/test_memory.py,
tests/test_context_engine_memory.py
docs/API_SPEC.md, docs/TESTING_GUIDE.md, docs/USER_GUIDE.md, docs/ARCHITECTURE.md
.planning/REQUIREMENTS.md, .planning/ROADMAP.md (IDs + plan list)
```

### Pattern 1: User-scoped PUT with 404/409 (recommendation: `/api/v1/memory/long-term/{entry_id}`, NOT chat-scoped)
Long-term memory is cross-chat (D-02), so a chat id in the path would only add an irrelevant ownership check. Scope by `user_id` in the query (the same way `_get_mcp_server_or_404(session, user_id, id)` works). Always filter `LongTermMemory.id == entry_id AND user_id == current_user.id`; return the same 404 for "missing" and "someone else's" (no existence leak). Add `dependencies=[Depends(require_allowed_origin)]` (+ `require_json_content_type` on PUT), as the MCP mutating routes do (`agent/dependencies.py`) — cookie auth makes CSRF hardening worthwhile even though older routes (invariants) omit it.

```python
# agent/memory.py (sketch; follows the file's commit/rollback conventions)
async def get_long_term_memory(session, user_id: int, entry_id: int) -> LongTermMemory | None:
    result = await session.exec(select(LongTermMemory).where(
        LongTermMemory.id == entry_id, LongTermMemory.user_id == user_id))
    return result.first()

class MemoryKeyConflictError(Exception): ...

async def update_long_term_memory(session, user_id, entry_id, key: str | None, value: str | None):
    row = await get_long_term_memory(session, user_id, entry_id)
    if row is None:
        return None
    if key is not None and key != row.key:
        dup = await session.exec(select(LongTermMemory.id).where(
            LongTermMemory.user_id == user_id, LongTermMemory.key == key))
        if dup.first() is not None:
            raise MemoryKeyConflictError(key)
        row.key = key
    if value is not None:
        row.value = value
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:           # race with LLM tool save of the same new key
        await session.rollback()
        raise MemoryKeyConflictError(key) from None
    except Exception:
        await session.rollback(); raise
    await session.refresh(row)
    logger.info("long_term_memory_updated", user_id=user_id, entry_id=entry_id)
    return row
```
Endpoint maps `None` -> `HTTPException(404, "Memory entry not found")`, `MemoryKeyConflictError` -> `409` with Russian detail (e.g. "Запись с таким ключом уже существует"), returns `MemoryEntryResponse(id, key, value, updated_at)` (already defined in `schemas.py:340`). Log `user_id`/`entry_id` only, never key/value contents beyond what existing logs do (existing code logs `key`; keep consistent, never log `value`).

Schema:
```python
class LongTermMemoryUpdate(BaseModel):
    """Partial update of a long-term memory entry."""
    key: Optional[str] = Field(default=None, max_length=MEMORY_KEY_MAX_LENGTH)
    value: Optional[str] = Field(default=None, max_length=MEMORY_VALUE_MAX_LENGTH)
    # validators: strip key; reject blank key; reject blank (whitespace-only) value;
    # model_validator(mode="after"): at least one of key/value must be set.
```
Do not strip internal whitespace/newlines of `value` (multi-line values are legitimate); only reject whitespace-only. Note the LLM tool args (`SaveLongTermMemoryArgs`) do not strip; do not change them (out of scope).

### Pattern 2: Inline edit in the sidebar (recommendation: inline, no modal)
Why inline: D-04 of the Phase 2 context says the memory panel lives in the sidebar "not a modal"; avoids any interaction with Phase 10's x-only modal policy (its guard test `tests/test_modal_close_policy.py` discovers modals from `index.html`; a new modal would have to follow the x-only rule and the `btn-close-<stem>` naming); and matches the existing inline edit pattern for invariants (edit button fills form, "Изменить"/"Удалить" buttons with classes `text-slate-400 hover:text-white` / `text-red-400 hover:text-red-300`, `ui/static/app.js:709-728`).

Implementation points:
- Change `renderMemoryEntries(container, entries)` to `renderMemoryEntries(container, entries, { editable })`; call with `editable: true` only for `data.long_term`. Row actions: `div.flex.items-center.gap-2.mt-1` with buttons `type="button"`, labels exactly "Редактировать" and "Удалить".
- Edit mode: replace the row content with `<input maxlength=200>` (key) and `<textarea rows=4 maxlength=50000>` (value, `textarea.value = entry.value` — the full value), plus "Сохранить" / "Отмена". All text set via `.value`/`.textContent`; **no `innerHTML`** anywhere, so no HTML injection path exists (DOMPurify is only required when inserting HTML; per CLAUDE.md never insert HTML without it — simply avoid HTML).
- Keep edit state in `state` (e.g. `state.editingMemory = { id, key, value }`), updated on `input`. `loadChatMemory` is triggered after every `done` WS frame (`app.js:1261`) and on chat switch (`:1064`), which re-runs `renderMemoryPanel` and rebuilds the container; without draft state a mid-edit re-render would silently discard the user's typing (see Pitfall 2). Render the edit form from `state.editingMemory` when `entry.id` matches. Clear it on save/cancel; if the entry no longer exists after a reload, drop it.
- Save: `apiFetch('/api/v1/memory/long-term/${id}', { method: 'PUT', body: JSON.stringify({ key, value }) })`; client-side trim and non-empty check with a Russian toast (like `saveGlobalInvariant`: "Заполните ключ и значение"); on error show `err.message` (the server 409/422 detail) via `showToast(..., 'error')`; on success `showToast('Запись памяти обновлена', 'success')` then `await loadChatMemory(state.currentChatId)`. Disable the Save button while the request is in flight (double-click guard). If `state.currentChatId` is null the panel is not loaded; entries are only shown with a chat, so handle null defensively.
- Delete: `if (!confirm(`Удалить запись «${entry.key}»? Это действие нельзя отменить.`)) return;` (same native-`confirm` convention as invariants/MCP/scheduler/chat delete), `DELETE`, toast "Запись памяти удалена", reload. Mention in the confirm text or docs that the assistant may save the fact again later (see Pitfall 4).
- The panel body is collapsed by default (`#memory-panel-body.hidden`, toggle `[data-fold-toggle]`); the row height lives inside `#memory-panel` with `max-h-72 overflow-y-auto`, so a 4-row textarea is fine in the ~sidebar width; use `w-full`, `min-w-0`, Tailwind slate inputs consistent with invariant form inputs.

### Pattern 3: Delete returns 204
Mirror `delete_mcp_server` (`status_code=204`, `-> None`). Helper `delete_long_term_memory(session, user_id, entry_id) -> bool` with try/except/rollback; endpoint raises 404 when False. `ondelete`/cascade is not involved (nothing references `LongTermMemory`).

### Anti-Patterns to Avoid
- Chat-scoped path for a user-scoped resource (extra, misleading ownership check).
- Re-using `save_long_term_memory` (upsert by key) for editing: renaming a key would create a second row instead of updating, and the old row would remain.
- Rendering values with `innerHTML` / `marked` (values are LLM-written and may contain markup).
- Truncating the value in the edit textarea (the list shows 160 chars + `…`; the edit form must use the full `entry.value`).
- Adding a modal "just for editing" (conflicts with the Phase 10 policy and Phase 2 D-04).
- Inventing a cache-invalidation or WS event: nothing is cached.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Request validation / blank handling | Manual `if` chains in the endpoint | Pydantic `Field(max_length)` + `field_validator`/`model_validator` (pattern: `_validate_mcp_text`) | Consistent 422s; lengths shared with `MEMORY_*_MAX_LENGTH` |
| Ownership scoping | Per-endpoint ad-hoc checks | One `get_long_term_memory(session, user_id, id)` filtering by both | Single place to get the 404-vs-leak rule right |
| Delete confirmation | Custom confirm modal | Native `confirm()` | Project convention; a modal would hit Phase 10 rules |
| Unique-key conflict | Parse SQLite error text | Pre-check select + catch `IntegrityError` | Existing pattern (`create_user`, `save_*_memory`) |
| Toasts / fetch | New helpers | `showToast`, `apiFetch` | Already handle 401 redirect and error detail |

## Runtime State Inventory

Not a rename/refactor/migration phase. No schema change is required (no new column; `created_at`/`updated_at` already exist). Omitted per template rules; no stored data, config, OS state, secrets or build artifacts are affected.

## Interactions with the rest of the system (verified)

| Area | Finding |
|------|---------|
| `agent/tools.py::_save_long_term_memory` | Upsert by `(user_id,key)`; unchanged. After a user deletes entry X the model can legitimately re-create it; after a user renames X->Y the model saving "X" creates a new row. Acceptable; document it. |
| Prompt injection of memory | `context_engine.build_system_prompt` and `headless.py` read from DB on every turn/run -> next turn sees edits; current in-flight stream already built its prompt (fine). |
| `agent/state.py` | No memory state; `cleanup_chat_caches` irrelevant (long-term is not chat-bound). |
| `/ws/events`, `/ws/chat` | Memory writes appear only as `memory_writes` in the chat `done` frame; no event for REST edits. A second browser tab shows stale memory until its next `done`/chat switch — accept (out of scope). |
| Token stats | `compute_chat_stats` does not include memory text; no change. |
| User deletion | `ondelete=CASCADE` already removes memory with the user. |
| Tool-trace history / old `memory_writes` | Past messages keep their record of the write; not affected. |
| Chat deletion | Does not touch long-term memory (cross-chat); unchanged. |

## Phase 10 / Phase 9 coordination (same branch, same files)

- Phase 10 (`10-01-PLAN.md`) edits `bindEvents()` / `bindSchedulerModals()` in `app.js` (deleting 5 backdrop/Escape blocks) and adds `tests/test_modal_close_policy.py`, plus doc one-liners in `docs/TESTING_GUIDE.md` and `docs/USER_GUIDE.md`. Phase 11 touches `renderMemoryEntries`/`renderMemoryPanel` (`app.js:287-340`) and adds new functions; different hunks, so no logical conflict. Both edit the same two docs files: expect trivial textual merges, or execute Phase 11 after Phase 10 (recommended wave order since all share branch `Day21`). Phase 9 also edits `app.js` (title WS handling).
- Phase 11 must not add a modal or a `document` keydown/Escape handler. If the planner nevertheless prefers a modal, it must close only via its x button (id `btn-close-<stem>`, overlay id `<stem>-modal`) and the guard test would auto-cover it; this is NOT recommended.
- Locate all edit points by function name, not line number (other phases shift lines).

## Common Pitfalls

### Pitfall 1: Key rename collides with the unique constraint
**What goes wrong:** Renaming entry A's key to an existing key B raises `IntegrityError` -> 500.
**Why:** `uq_long_term_memory_user_key (user_id,key)`.
**How to avoid:** pre-check + catch `IntegrityError` -> 409 "Запись с таким ключом уже существует"; keep the form open and show the toast so the user can fix it. Test: two rows, rename one onto the other -> 409, both rows unchanged.
**Warning signs:** 500s from PUT in tests with duplicate keys.

### Pitfall 2: Panel re-render wipes the open edit form
**What goes wrong:** `loadChatMemory` runs after each chat `done`, on chat switch, and after saves; `renderMemoryEntries` does `container.replaceChildren()`, destroying an unsaved draft.
**How to avoid:** hold the draft in `state` and re-render the edit form from it (Pattern 2). Also reset `state.editingMemory = null` on logout/chat switch only if the product wants it (recommended: keep it, since long-term memory is cross-chat).

### Pitfall 3: Cross-user access (IDOR)
Filter by `user_id` in the SQL itself; never `session.get(LongTermMemory, id)` followed by a later check that's easy to forget. Test with `second_authenticated_client`: PUT and DELETE on user A's id return 404 and the row is unchanged; also unauthenticated -> 401.

### Pitfall 4: User deletes a fact, the model re-saves it
Not a bug; `save_long_term_memory` is the model's explicit choice (MEM-03). State in docs/USER_GUIDE. No "forbidden keys" list (out of scope).

### Pitfall 5: Truncated value saved back
If the edit textarea were seeded from the displayed (160-char) string, saving would silently truncate the stored value. Seed from `entry.value`. Add a UI-level note in tests/UAT: edit a >160-char value, save, verify full length preserved.

### Pitfall 6: Unescaped markup / HTML
Values are LLM-generated. Use `textContent`/`.value` only. A UAT value like `<img src=x onerror=alert(1)>` must show as literal text.

### Pitfall 7: Windows/Playwright specifics
Native `confirm()` blocks Playwright unless a `page.on('dialog')` handler accepts/dismisses it; the memory panel body is collapsed by default and must be expanded first (click `[data-fold-toggle="memory-panel-body"]`); long-term entries need to be seeded (via DB/`memory.save_long_term_memory` on the isolated copy or via a chat turn) before the UI check.

## Code Examples

### Endpoint skeleton (follows `update_mcp_server` / `delete_mcp_server`)
```python
@app.put(
    "/api/v1/memory/long-term/{entry_id}",
    response_model=MemoryEntryResponse,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def update_long_term_memory_entry(
    entry_id: int,
    body: LongTermMemoryUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MemoryEntryResponse:
    """Edit the caller's own long-term memory entry (user-scoped; foreign id -> 404)."""
    try:
        row = await memory.update_long_term_memory(
            session, current_user.id, entry_id, key=body.key, value=body.value,
        )
    except memory.MemoryKeyConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Запись с таким ключом уже существует") from None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Memory entry not found")
    return MemoryEntryResponse(id=row.id, key=row.key, value=row.value, updated_at=row.updated_at)


@app.delete(
    "/api/v1/memory/long-term/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_long_term_memory_entry(entry_id: int, session=..., current_user=...) -> None:
    ...
```
Place them next to `get_chat_memory` in `agent/main.py`; add `LongTermMemoryUpdate` to the `agent.schemas` import block. (Check whether `require_allowed_origin`/`require_json_content_type` are already imported in `main.py`; they are used by the MCP routes.)

### UI delete (pattern: `deleteGlobalInvariant`, `app.js:855`)
```javascript
async function deleteLongTermMemory(entry) {
    if (!confirm(`Удалить запись «${entry.key}»? Это действие нельзя отменить.`)) return;
    try {
        await apiFetch(`/api/v1/memory/long-term/${entry.id}`, { method: 'DELETE' });
        showToast('Запись памяти удалена', 'success');
        if (state.currentChatId) await loadChatMemory(state.currentChatId);
    } catch (err) {
        showToast(err.message || 'Не удалось удалить запись памяти.', 'error');
    }
}
```

## State of the Art

Not applicable (internal CRUD). Existing conventions to follow: invariants/MCP/scheduler edit-delete UX; native `confirm()` for destructive actions.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Both `key` and `value` should be editable (not only `value`) | Pattern 1 | If user only wants value edits, drop key from the schema and the 409 path (simpler). Roadmap says "edit long-term memory fields" (plural), so both is the safer reading. |
| A2 | Inline editing in the sidebar is preferred over a modal | Pattern 2 | A modal needs x-only close + guard-test compliance; slightly more work |
| A3 | Native `confirm()` is acceptable for delete confirmation | Pattern 2 | If a custom confirm UI is wanted, a modal would be needed |
| A4 | No live cross-tab sync is needed | Interactions | Stale panel in a second tab until its next refresh |
| A5 | Adding `require_allowed_origin`/`require_json_content_type` on the new routes is desired hardening (older invariant routes omit it) | Pattern 1 | None functionally: browser same-site requests pass; tests without Origin pass |

All other statements are `[VERIFIED: codebase]` (files and line references cited above).

## Open Questions (RESOLVED)

Resolution basis (2026-10-02, plan revision): there is no CONTEXT.md for this phase, so no question was put to the user. Each question below is closed by adopting this research's own recommendation as a planning assumption. These are assumptions adopted from research, not user decisions; each one names the plan that implements it and can be flipped as described in the `<assumptions>` tables of 11-01-PLAN.md and 11-02-PLAN.md.

1. **Should key be editable?** Recommendation: yes (A1); the key is the identifier the LLM uses to overwrite the fact, and users may want to fix a bad key. 409 handles collisions.
   RESOLVED: yes, both `key` and `value` are editable (assumption A1 adopted from this research, not a user decision). Implemented by 11-01-PLAN.md (Task 2: `update_long_term_memory` with the key-collision pre-check and `MemoryKeyConflictError`; Task 3: `LongTermMemoryUpdate` with both fields and the 409 path) and 11-02-PLAN.md (Task 2: key input + value textarea in the inline form); verified in a browser by 11-04-PLAN.md (scenario S4).
2. **Should deletion remove the same fact from LLM re-saving?** No; out of scope (Pitfall 4).
   RESOLVED: no, the LLM re-saving a deleted fact is documented only, not prevented (recommendation of Pitfall 4 adopted as an assumption, not a user decision). 11-01-PLAN.md leaves the `save_long_term_memory` tool and `SaveLongTermMemoryArgs` unchanged and adds no "forbidden keys" list; 11-03-PLAN.md (Task 1) documents the behaviour in `docs/ARCHITECTURE.md` ("Known limits") and `docs/USER_GUIDE.md` ("Memory").
3. **Order of execution vs Phase 10?** Recommend 10 before 11 to avoid merge noise in `app.js`/docs; not a hard dependency.
   RESOLVED: Phase 11 is not hard-dependent on Phase 10 (recommendation adopted as an assumption, not a user decision); no plan of Phase 11 has a `depends_on` or a precondition on Phase 10, and the ROADMAP dependency stays "Phase 8". Implemented by all four plans locating every edit point by function / heading name instead of line number (11-01, 11-02, 11-03, 11-04 context blocks), by 11-02-PLAN.md adding no modal and no key handler (guard test `test_memory_ui_adds_no_modal_or_key_handler`, and Task 2 runs `tests/test_modal_close_policy.py` when it exists), and by 11-03-PLAN.md appending to `docs/TESTING_GUIDE.md` / `docs/USER_GUIDE.md` by heading. Running Phase 10 first remains the recommended order but is the orchestrator's choice.

## Environment Availability

| Dependency | Required By | Available | Fallback |
|------------|------------|-----------|----------|
| Python 3.11+, pytest, pytest-asyncio, httpx, respx | tests | assumed present (existing suite) | — |
| Playwright (browser UAT) | UI verification | per project memory the agent already runs it against an isolated copy | manual checklist |

UAT recipe (from user memory): run an isolated copy of the app on ports 18000/18001 (env `UI_PORT`/`AGENT_PORT`/`DB_PATH` via `shared/config.py`; frontend derives `AGENT_BASE` from `window.location.hostname` + injected agent port; confirm CORS origin list follows the ports before relying on it), never touch the user's 8000/8001 processes (note `run.py` kills processes bound to 8000/8001 at startup, so do not start the copy with default ports). Seed a user + a few long-term rows, open the Memory panel (expand it), exercise edit/save/cancel, 409 on duplicate key, 404 path (via API), delete with accepted and dismissed `confirm()`, XSS literal text, >160-char value roundtrip, and that a new chat still sees the edited memory.

## Validation Architecture

`workflow.nyquist_validation` is `false` in `.planning/config.json` -> the formal Validation Architecture section is skipped. Test plan (runs under existing `pytest tests/ -v`, `asyncio_mode=auto`):

| Req | Test | File |
|-----|------|------|
| MEMUI-03 | PUT updates value/key/updated_at, keeps created_at; response shape; 401 unauthenticated; 422 on blank/too-long/empty body | `tests/test_memory_api.py` (extend) |
| MEMUI-03 | other user's id -> 404 for PUT and DELETE, row unchanged; unknown id -> 404 | same |
| MEMUI-04 | rename onto existing key -> 409, both rows unchanged; renaming to own current key is OK | same |
| MEMUI-05 | DELETE -> 204, subsequent GET `/chats/{id}/memory` no longer lists it; second DELETE -> 404 | same |
| MEMUI-06 | after edit, `build_system_prompt` contains new value and not the old; after delete the "Long-term memory" label disappears when empty | `tests/test_context_engine_memory.py` (extend) |
| MEMUI-03/05 | CRUD helpers: `update_long_term_memory`, `delete_long_term_memory`, scoping | `tests/test_memory.py` (extend) |
| MEMUI-01/02 | JS still parses (`tests/test_static_js_syntax.py` runs automatically); optionally a source-guard asserting `innerHTML` is not used in memory rendering and the button labels exist | new small test or skip |
| UI | Playwright UAT on isolated copy (see above) | manual/agent-run |

Quick run: `pytest tests/test_memory_api.py tests/test_memory.py tests/test_context_engine_memory.py tests/test_static_js_syntax.py -q`. Full: `pytest tests/ -v`. Test fixtures: `authenticated_client` (has `.seeded_user_id`), `second_authenticated_client`; seed rows with `memory.save_long_term_memory(session, user_id, key, value)` through `async_session_factory()`. No Wave 0 gaps (framework and fixtures exist).

Docs to sync (all verified as lacking memory-edit coverage; `docs/API_SPEC.md` does not document even the GET memory endpoint, `docs/USER_GUIDE.md` has no Memory section):
- `docs/API_SPEC.md`: new section "Long-term memory (Day 21)" with GET (existing, shape), PUT, DELETE, status codes 200/204/401/404/409/422.
- `docs/TESTING_GUIDE.md`: add a bullet to "Required Tests" plus a short scenario list for the new tests.
- `docs/USER_GUIDE.md`: short "Memory" section: where the panel is, edit/delete buttons, note that the assistant may save a fact again.
- `docs/ARCHITECTURE.md`: one paragraph: edits go through `agent/memory.py`, read fresh each turn, no cache.
- `.planning/REQUIREMENTS.md`: MEMUI-01..06 and traceability; `.planning/ROADMAP.md`: Requirements + Plans list.

## Security Domain

`security_enforcement` is absent in config -> enabled.

| ASVS Category | Applies | Control |
|---------------|---------|---------|
| V2 Authentication | yes (existing) | `Depends(get_current_user)` session cookie; 401 when missing |
| V3 Session Management | existing | HTTP-only cookie; unchanged |
| V4 Access Control | yes | user_id-filtered SQL; foreign id -> 404 identical to unknown id |
| V5 Input Validation | yes | Pydantic max_length 200/50 000, strip + non-blank, >=1 field; maxlength attrs in UI are only convenience |
| V6 Cryptography | no | — |
| V13/CSRF | yes | `require_allowed_origin` (+ JSON content-type on PUT) like MCP routes |

| Threat | STRIDE | Mitigation |
|--------|--------|------------|
| IDOR on entry id | Information disclosure / Tampering | SQL filter by `user_id`; test with second user |
| Stored XSS via memory key/value (LLM-written) | Tampering | `textContent`/`.value` only, no `innerHTML`; if HTML is ever required, `DOMPurify.sanitize()` |
| Oversized payload | DoS | max_length on schema (50 000 matches the column) |
| Accidental data loss | — | `confirm()` before delete; edit form has Cancel |
| CSRF on cookie-auth mutating route | Tampering | Origin check + JSON content-type |
| Logging secrets | Info disclosure | log ids only, never `value` |

## Sources

### Primary (HIGH confidence, codebase)
- `shared/models.py:148-195` (WorkingMemory/LongTermMemory), `agent/memory.py`, `agent/schemas.py:21-22,340-385`, `agent/main.py:652-675` (GET memory), `:968-998` and `:1065-1120` (update/delete patterns), `agent/dependencies.py`, `agent/tools.py:292-307`, `agent/context_engine.py:233-266`, `agent/headless.py:105`, `agent/state.py`
- `ui/static/app.js:57-91,287-340,700-870,1064,1245-1269`, `ui/static/index.html:48-76`
- `tests/conftest.py`, `tests/test_memory_api.py`, `tests/test_memory.py`, `tests/test_context_engine_memory.py`, `tests/test_static_js_syntax.py`
- `.planning/phases/10-modals-close-only-via-x-button-day-21/10-01-PLAN.md`, `.planning/milestones/v1.0-REQUIREMENTS.md`, `.planning/milestones/v1.0-phases/02-memory-day-11/*CONTEXT.md` (D-02, D-03, D-04), `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, `.planning/config.json`
- `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`

### Secondary / Tertiary
- None used (no web research needed).

## Project Constraints (from CLAUDE.md)

- Vanilla JS + CDN only; no npm/Node/Docker/Redis; no `multiprocessing`/`os.fork`.
- HTTP-only session cookie auth; all data scoped by `user_id`.
- Type hints everywhere; async/await for all I/O; `structlog` via `get_logger(__name__)` (never `print`); `datetime.now(timezone.utc)`.
- SQLModel FK via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` (no schema change here); `.is_(None)` in `where()`.
- `await session.commit()` after writes and `await session.rollback()` in exception handlers; no bare `except:`.
- Never insert HTML without `DOMPurify.sanitize()`; never hardcode secrets.
- `pytest` + `pytest-asyncio` (`asyncio_mode=auto`); tests use a separate DB (`tests/conftest.py`); check `docs/TESTING_GUIDE.md` before adding tests.
- Commits: no `Co-Authored-By` Claude line (user's global and project memory instruction overrides any attribution reminder).
- Do not create/rename/switch branches; Phase 11 stays on `Day21`.
- No project skills found (`.claude/skills`, `.agents/skills` absent).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH (no new dependencies)
- Architecture: HIGH (direct reading of code; patterns exist for each piece)
- Pitfalls: HIGH for code-derived ones; MEDIUM for Playwright specifics (not re-run in this session)

**Research date:** 2026-10-02
**Valid until:** 2026-10-30 (codebase moves with Phases 9, 10, 12 on the same branch; re-check line references)
