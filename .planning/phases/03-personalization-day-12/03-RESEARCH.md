# Phase 3: Personalization (Day 12) - Research

**Researched:** 2026-09-20
**Domain:** In-repo feature addition to an existing FastAPI + SQLModel app (new table, CRUD module, REST endpoints, system-prompt injection, vanilla-JS sidebar panel). No new external dependencies.
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Profile storage model**
- **D-01:** A new, dedicated `Profile` SQLModel table — not an overlay on `LongTermMemory`. Columns: `user_id` (FK to `user.id`, `ondelete="CASCADE"`, unique — one profile row per user), `style`, `format`, `constraints`, `updated_at`. User explicitly chose this over reserved-key rows in `LongTermMemory`, to keep "settings the user directly edits" distinct from "freeform facts the LLM chose to remember" — the two are conceptually different even though both are long-term, user-scoped state.
- Profile is purely user-scoped (no `chat_id`), matching `LongTermMemory`'s existing user-level scoping (D-02 from Phase 2) and PERS-01's "each user has a profile."

**Edit path**
- **D-02:** Profile is edited **UI-only**, via a direct REST endpoint (e.g. `PUT /api/v1/profile`) — no LLM tool call, no `agent/tools.py` registration. User explicitly chose this over adding an LLM-writable `update_profile` tool. Rationale: PERS-03 requires a UI edit surface regardless; Phase 2's "explicit tool call only" philosophy (MEM-03) was scoped to *memory* writes specifically, and profile is closer to `Settings` (user-controlled configuration) than to memory (LLM-decided facts). This mirrors the existing `update_settings` REST pattern, not the tool-call dispatcher.
- The tool-call dispatcher (`agent/tools.py`) itself needs **no changes** in this phase — consistent with `STATE.md`'s cross-phase decision that it's "built once in Phase 2, reused unchanged by Phases 3-5" (it's simply not used for this particular write path).

**Field shape**
- **D-03:** `style`, `format`, and `constraints` are each freeform text (e.g. a textarea per field) — no enums/dropdowns. User explicitly chose this over constrained-choice fields, for simplicity: freeform text injects into the system prompt verbatim with no mapping/validation layer, and matches the freeform-value precedent already established by `LongTermMemory`/`WorkingMemory`.

**Profile UI**
- **D-04:** The profile editor lives in a **new sidebar tab**, next to the existing Memory tab (same always-reachable placement as `02-CONTEXT.md`'s D-04 memory panel) — not a modal. User explicitly chose this over a modal dialog for consistency with the Memory tab's placement.
  - **Research finding (see Summary/Anti-Patterns below): the actual Phase 2 precedent is a static stacked panel, not a tab-switcher widget — no tab UI exists anywhere in this codebase. Recommend implementing D-04 as a second stacked panel, not a new tab component, unless the user explicitly wants true tab-switching behavior introduced.**

### Claude's Discretion
- Exact injection point/wording in `agent/context_engine.py::build_system_prompt()` — where in the assembled prompt the profile section goes relative to the summary/working-memory/long-term-memory sections, and how it's worded (e.g. "User's stated preferences: style=..., format=..., constraints=..."). Must be present on every request per PERS-02, not conditional.
- Whether an unset field (empty string) is omitted from the injected prompt entirely or included as empty — recommend omitting empty fields to avoid injecting noise like "format: " into the prompt.
- Exact markup/styling of the new sidebar Profile tab and its edit form — match existing Tailwind patterns already used for the Memory tab in `index.html`/`app.js`.
- How PERS-04 ("responses observably differ across profiles") gets demonstrated/verified — that's a verification-step concern, not an implementation decision.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope. (LLM-writable profile via tool call was considered as an option for the "edit path" decision and explicitly not chosen — see D-02 — not a scope-creep deferral.)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PERS-01 | Each user has a profile with preferences (style, format, constraints) | New `Profile` SQLModel table (D-01), lazy get-or-create pattern mirroring `_ensure_global_settings` — see Architecture Patterns #1, Code Examples |
| PERS-02 | The user's profile is attached to every request (injected into context/system prompt) | Injection added inside `build_system_prompt()`, the single shared call site for both the WS chat turn and `compute_chat_stats` via `build_llm_context()` — structurally guaranteed on every request, not per-caller — see Architecture Patterns #3 |
| PERS-03 | UI lets the user view/edit their profile and preferences | New `#profile-panel` sidebar block + `GET`/`PUT /api/v1/profile`, mirroring the existing Memory panel and Settings modal JS patterns — see Recommended Project Structure, Pattern 2 |
| PERS-04 | Responses observably differ across different profiles/preferences | Directive-style injected wording ("always follow these") recommended to maximize observable effect; verification is a manual UAT comparison, not an automated diff — see Don't Hand-Roll, Open Questions #1 |
</phase_requirements>

## Summary

This phase adds a small, well-precedented vertical slice: one new SQLModel table (`Profile`), one new CRUD module (`agent/profile.py`), two new REST endpoints (`GET`/`PUT /api/v1/profile`), one new injection block in `agent/context_engine.py::build_system_prompt()`, and one new always-visible sidebar panel in the vanilla-JS frontend. Every piece has a near-identical precedent already in the codebase from Phase 1 (Auth/Settings) and Phase 2 (Memory), so the implementation risk is low — the main job is faithfully copying established patterns, not inventing new ones.

The one meaningful finding that changes the plan's shape: **CONTEXT.md's D-04 describes the target as a "sidebar tab," but the actual Phase 2 precedent (`#memory-panel`) is not a tab-switcher — it's a permanently-visible stacked panel in the sidebar.** There is no tab UI component anywhere in the codebase. The plan should build a second stacked panel (`#profile-panel`) below/beside `#memory-panel`, not a tab-switching widget, to stay consistent with the only existing precedent and avoid introducing a new UI pattern un-requested by the user.

All other verified facts: `SQLModel.metadata.create_all()` auto-creates new tables with no migration function needed (this only applies to brand-new tables — column additions on *existing* tables need an explicit idempotent `ALTER TABLE` migration, which does not apply here); `build_llm_context()` is the single call site feeding every WS chat turn and the stats endpoint, so injecting profile there satisfies PERS-02's "every request, not just some" unconditionally; and the existing `update_settings` REST handler is a byte-for-byte structural precedent for the new profile PUT endpoint (current_user-scoped, no chat_id ambiguity, commit/rollback try-except).

**Primary recommendation:** Copy `agent/memory.py` for CRUD shape and `agent/main.py::update_settings` for the endpoint shape; copy `#memory-panel` markup/JS for the UI shape; inject profile into `build_system_prompt()` directly after the base `system_prompt` line (before facts/summary/memory) so it reads as a strong, stable directive rather than being buried after other context.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Profile storage (style/format/constraints per user) | API / Backend (`shared/models.py` + SQLite) | — | New `Profile` table, user-scoped, FK-cascade on `user.id` — same tier as `Settings`/`LongTermMemory` |
| Profile CRUD (get/create/update) | API / Backend (`agent/profile.py`) | — | Mirrors `agent/memory.py`; no business logic belongs in the route handler itself |
| Profile REST endpoints | API / Backend (`agent/main.py`) | — | `GET`/`PUT /api/v1/profile`, session-cookie-scoped via `get_current_user`, mirrors `update_settings` |
| Profile injection into LLM context | API / Backend (`agent/context_engine.py::build_system_prompt`) | — | Same tier/function as existing facts/summary/memory injection; must run on every `build_llm_context` call |
| Profile view/edit UI | Browser / Client (`ui/static/index.html`, `ui/static/app.js`) | — | Vanilla JS, no bundler; mirrors the Memory panel's load-on-select + form pattern |
| Auth/session scoping | API / Backend (`agent/dependencies.py::get_current_user`) | — | Already built in Phase 1 (Auth); reused unchanged — no new auth logic in this phase |

## Standard Stack

No new external packages are required by this phase. It is entirely additive within the existing stack (FastAPI, SQLModel, SQLAlchemy 2.x, aiosqlite, structlog, vanilla JS + Tailwind/CDN). No `pip install` step is needed.

### Core (existing, reused unchanged)
| Library | Version (from requirements.txt) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SQLModel | 0.0.22+ | New `Profile` table definition | Already the ORM for every other table in this codebase |
| FastAPI | 0.115.0+ | `GET`/`PUT /api/v1/profile` endpoints | Already the REST framework; `Depends(get_current_user)` pattern is established |
| structlog | 24.4.0+ | `agent/profile.py` write logging | Project-wide logging convention |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| New dedicated `Profile` table (D-01, locked) | Reserved-key rows in `LongTermMemory` | User explicitly rejected this in CONTEXT.md — conflates LLM-chosen facts with user-edited settings. Not re-litigated. |
| UI-only REST edit path (D-02, locked) | LLM-writable `update_profile` tool via `agent/tools.py` | User explicitly rejected this — profile is closer to `Settings` (user-controlled config) than to memory (LLM-decided facts). Not re-litigated. |

**Installation:** None required — no new packages.

## Package Legitimacy Audit

Not applicable — this phase installs no external packages. `requirements.txt` is unchanged. Skip the slopcheck/registry-verification protocol; nothing to audit.

## Architecture Patterns

### System Architecture Diagram

```
Browser (ui/static/index.html + app.js)
  │
  │  GET  /api/v1/profile   (on chat select / page load, mirrors loadChatMemory)
  │  PUT  /api/v1/profile   (on profile form submit, mirrors saveSettings)
  ▼
Agent FastAPI (agent/main.py)
  │  Depends(get_current_user)  →  session-cookie → User row (Phase 1, unchanged)
  │  Depends(get_session)       →  AsyncSession
  ▼
agent/profile.py  (new CRUD module, mirrors agent/memory.py)
  │  get_or_create_profile(session, user_id) → Profile row (lazy-create, mirrors _ensure_global_settings)
  │  update_profile(session, user_id, style, format, constraints) → Profile row
  ▼
shared/models.py::Profile  (new SQLModel table, user_id FK UNIQUE + CASCADE)
  │
  ▼
SQLite (app.db) — profile table, one row per user

──────────────── separately, on every chat turn ────────────────

agent/ws.py::_handle_chat_message()
  ▼
agent/context_engine.py::build_llm_context(session, chat_id, model)
  ▼
agent/context_engine.py::build_system_prompt(session, chat_id)
  │  parts = [system_prompt]
  │  parts.append(profile injection)   ← NEW: agent/profile.py::get_or_create_profile(session, chat.user_id)
  │  parts.append(facts)               (existing)
  │  parts.append(summary)             (existing)
  │  parts.append(working memory)      (existing)
  │  parts.append(long-term memory)    (existing)
  ▼
LLM call (DeepSeek / LM Studio) — every request, unconditionally (PERS-02)
```

### Recommended Project Structure
```
agent/
├── profile.py            # NEW — get_or_create_profile(), update_profile() (mirrors memory.py)
├── main.py                # + GET/PUT /api/v1/profile endpoints
├── context_engine.py       # + profile injection block in build_system_prompt()
├── schemas.py              # + ProfileResponse, ProfileUpdate
shared/
├── models.py               # + Profile SQLModel table
ui/static/
├── index.html               # + #profile-panel sidebar block (below #memory-panel)
├── app.js                   # + loadProfile(), renderProfilePanel(), saveProfile()
tests/
├── test_profile.py          # NEW — CRUD-level tests (mirrors test_memory.py)
├── test_profile_api.py      # NEW — REST endpoint tests (mirrors test_memory_api.py)
├── test_context_engine_profile.py  # NEW — injection tests (mirrors test_context_engine_memory.py)
```

### Pattern 1: Lazy get-or-create row, one per user
**What:** A user has no `Profile` row until first touched; `GET`/injection both call a `get_or_create_profile()` helper that creates an empty-field row on first access, exactly like `agent/main.py::_ensure_global_settings()` does for `Settings`.
**When to use:** Any singleton-per-user/per-chat row where "doesn't exist yet" should behave like "exists with defaults," not 404.
**Example:**
```python
# Source: agent/main.py:54-69 (existing precedent, _ensure_global_settings)
async def get_or_create_profile(session: AsyncSession, user_id: int) -> Profile:
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
    return row
```

### Pattern 2: Owner-scoped REST endpoint, no client-supplied user_id
**What:** `PUT /api/v1/profile` never accepts `user_id` in the request body — it always writes to `current_user.id`, exactly like `update_settings` never lets the caller target another chat's settings without an ownership check.
**When to use:** Any per-user mutable resource reachable only via session auth.
**Example:**
```python
# Source: agent/main.py:461-489 (existing precedent, update_settings)
@app.put("/api/v1/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    row = await profile.get_or_create_profile(session, current_user.id)
    updates = body.model_dump(exclude_unset=True)
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
    return _profile_to_response(row)
```

### Pattern 3: Unconditional injection at a single call site
**What:** `build_system_prompt()` is the one function that assembles every outbound system prompt, and it is called exclusively from `build_llm_context()`, which is itself the only entry point for both the WS chat turn and `compute_chat_stats`. Adding the profile block here — not in `agent/ws.py` or anywhere per-request — is what makes PERS-02 ("every request, not just some") structurally guaranteed rather than something that has to be remembered at each call site.
**When to use:** Any "must apply to every request" requirement — put it in the shared assembly function, never duplicate it at each caller.
**Example:**
```python
# Source: agent/context_engine.py:61-88 (existing function to extend)
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
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
    # ... summary_text, working memory, long_term memory unchanged below
```
Note: this moves the `chat = await session.get(Chat, chat_id)` lookup earlier than its current position (currently it's fetched again lower down for long-term memory) — reuse the single `chat` lookup for both the new profile block and the existing long-term-memory block rather than querying twice.

### Anti-Patterns to Avoid
- **Building a tab-switcher UI component:** CONTEXT.md's wording ("sidebar tab") does not match the actual codebase precedent (`#memory-panel` is a static, always-visible stacked panel, not a tab). Do not introduce a new tab-switching JS pattern for this one feature — add a second stacked panel instead, consistent with the only real precedent.
- **Injecting empty profile fields as noise:** `"style: , format: , constraints: "` for an unfilled profile pollutes every prompt. Per CONTEXT.md's discretion note, omit unset fields entirely (see Pattern 3, `_format_profile` returns `""` when all fields are blank, and the caller skips appending).
- **Re-fetching `chat` twice in `build_system_prompt`:** the existing code already fetches `chat` once for long-term memory; don't add a second `session.get(Chat, chat_id)` call for the profile block — hoist the existing lookup earlier and reuse it.
- **Writing profile via the tool-call dispatcher:** D-02 explicitly locks this to REST-only. Do not add a `save_profile`/`update_profile` entry to `agent/tools.py` or `TOOL_REGISTRY` — the dispatcher needs zero changes this phase (confirmed also by STATE.md's cross-phase decision).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Detecting "responses observably differ across profiles" (PERS-04) | A custom automated diffing/similarity-scoring harness | A manual UAT scenario (`/bm:verify-work`): send the same prompt under two different saved profiles, eyeball that style/format/constraints visibly changed the reply | LLM output is non-deterministic; a hard automated equality/diff check is unreliable and over-engineered for a coursework MVP. `nyquist_validation` is disabled in this project's config anyway. |
| Per-user singleton row lifecycle | A custom "ensure exists" decorator or migration script | The existing `get_or_create` pattern already used by `_ensure_global_settings()` | One more copy of an established 15-line pattern, not a new abstraction |
| New DB table creation | A manual `ALTER TABLE`/migration script | `SQLModel.metadata.create_all()` (already runs on every `init_db()` call) | Brand-new tables are picked up automatically as long as the model class is imported somewhere before `init_db()` runs — no migration needed, unlike column additions to *existing* tables (which this project handles separately via `migrate_add_context_length`/`migrate_add_user_id_columns`) |

**Key insight:** This phase's entire job is "do it exactly like Settings/Memory did it." There is no genuinely novel technical problem here — the risk is in accidentally deviating from an established pattern (e.g., building a tab widget that doesn't exist, or wiring the tool-call dispatcher when D-02 forbids it), not in library selection.

## Common Pitfalls

### Pitfall 1: Table not auto-created because the model class was never imported
**What goes wrong:** `SQLModel.metadata.create_all()` only creates tables for model classes that have been imported into the running process at least once. If `agent/profile.py` is written but never imported anywhere before `init_db()` runs, the `profile` table silently never gets created and every read/write throws `OperationalError: no such table: profile`.
**Why it happens:** `shared/database.py` explicitly imports `Chat, Message, Session, Settings, TokenUsage, User` but *not* `WorkingMemory`/`LongTermMemory`/`Profile` — those get registered transitively because `agent/main.py` imports `from agent import memory`, and `agent/memory.py` imports the models. The same must hold for `Profile`.
**How to avoid:** Ensure `agent/main.py` imports `from agent import profile` (or imports `Profile` from `shared.models` directly) unconditionally at module load time — the same way it already does for `memory`.
**Warning signs:** Tests fail with `sqlite3.OperationalError: no such table: profile` even though the model class compiles fine.

### Pitfall 2: Profile injected only into new chats, not existing ones
**What goes wrong:** If the injection is added by branching on `chat.user_id is not None` in the same block as long-term memory (both currently gated the same way), that's fine — but if someone instead makes profile injection conditional on the chat having *messages* already, or scopes it to `chat_id` instead of `chat.user_id`, profile could silently vanish for chats created before this phase, or for the WS stats-preview path.
**Why it happens:** Copy-paste of the long-term-memory block's conditional without noticing it's chat-derived, not directly `user_id`-derived.
**How to avoid:** Use the exact same `chat.user_id` derivation already proven correct for long-term memory (D-02's precedent); don't gate on message count, strategy, or any other proxy.
**Warning signs:** Profile visibly affects responses in a fresh chat but not in an older one, or vice versa.

### Pitfall 3: Frontend renders profile fields with `innerHTML` instead of `textContent`/`.value`
**What goes wrong:** XSS via freeform profile text (e.g., a `style` field containing `<img onerror=...>`) if it's ever injected into the DOM with `innerHTML`.
**Why it happens:** Easy to copy the wrong helper — `renderMemoryEntries()` already does this correctly with `.textContent`, but a new dev might reach for `innerHTML` out of habit when building richer profile UI.
**How to avoid:** Mirror `renderMemoryEntries()`'s use of `.textContent` for display; use `.value` (not `.innerHTML`) to populate/read the profile edit form's `<textarea>` elements, exactly like `openSettingsModal()`/`saveSettings()` do for `settings-system-prompt`.
**Warning signs:** Any `innerHTML =` assignment touching profile data in `app.js`.

### Pitfall 4: Field max-length unspecified, unbounded strings bloat every prompt
**What goes wrong:** Since profile is injected into *every* request (PERS-02), unbounded `style`/`format`/`constraints` text directly inflates the context window and token cost of every single turn, unlike memory entries which are scoped/summarized.
**Why it happens:** CONTEXT.md's D-03 specifies freeform text but doesn't specify a max length.
**How to avoid:** Cap each field at a conservative length via Pydantic `Field(max_length=...)`, e.g. 2000 characters (~500 tokens) per field — small enough to not dominate the context budget, large enough for a few sentences of real preference. **[ASSUMED — this exact number is not specified anywhere in CONTEXT.md/REQUIREMENTS.md; confirm with user or pick a value and document the rationale in the plan.]**
**Warning signs:** `context_length` usage percent climbing noticeably after adding a profile, `ContextOverflowError` triggered on `no_compression` strategy by profile text alone.

## Code Examples

### Profile SQLModel table (FK-cascade convention)
```python
# Source: shared/models.py:165-186 (LongTermMemory, adapted per CONTEXT.md D-01)
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
Note: `format` is a Python builtin name but not a reserved word — safe as a field name (SQLModel/Pydantic have no conflict here; `Settings` doesn't use it, but it's not shadowed by anything in this codebase).

### Profile injection formatting helper
```python
# New helper in agent/context_engine.py, following the existing inline-formatting style
def _format_profile(row: "Profile") -> str:
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

## State of the Art

Not applicable in the "library evolved" sense — this is a closed, hand-built stack with no external library version drift relevant to this phase. The one relevant "old vs new" axis is internal to the project itself:

| Old Approach (Phase 2, memory) | New Approach (this phase, profile) | When Changed | Impact |
|--------------|------------------|--------------|--------|
| LLM decides what to save, via tool call (`save_long_term_memory`) | User decides what to save, via direct REST edit | This phase (D-02) | No `agent/tools.py` changes; profile writes are synchronous REST, not turn-embedded tool calls |
| Key-value freeform rows (`LongTermMemory.key`/`value`) | Fixed three-column shape (`style`/`format`/`constraints`) | This phase (D-01) | Simpler UI (three labeled textareas instead of an arbitrary key list), no key-collision handling needed |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Per-field max length of 2000 characters is a reasonable cap for `style`/`format`/`constraints` | Common Pitfalls #4, Code Examples | If too small, legitimate user preferences get truncated/rejected; if too large, context budget bloats on every request. Low risk either way — easy to adjust the `Field(max_length=...)` value later since it's a single constant, not a migration-sensitive constraint. |
| A2 | Profile injection should be placed directly after the base `system_prompt`, before facts/summary/memory | Architecture Patterns, Pattern 3 | This is explicitly "Claude's Discretion" per CONTEXT.md — if the planner/user prefers profile placed after memory instead (e.g., to prioritize recent facts over static preferences), this ordering needs to change. Low risk: a one-line reordering of `parts.append(...)` calls. |
| A3 | "Sidebar tab" in CONTEXT.md D-04 should be implemented as a second stacked panel (matching the actual Memory-panel precedent), not a true tab-switcher widget | Summary, Anti-Patterns | If the user actually wants a real tab-switching UI (mutually exclusive panels, not both visible at once), this reading is wrong and adds unwanted scope (a new tab-switcher component). Medium risk — worth flagging explicitly to the planner/user since it changes UI surface area. |

## Open Questions

1. **Exact wording/format of the injected profile block**
   - What we know: CONTEXT.md leaves this to Claude's discretion; must be present on every request (PERS-02).
   - What's unclear: Whether the LLM should be told to treat these as hard constraints ("always follow") vs. soft preferences ("prefer, when reasonable").
   - Recommendation: Use directive language ("always follow these") since PERS-04 requires *observable* differences — a softer phrasing risks the LLM treating it as optional and producing no visible difference, failing the phase's success criteria.

2. **Whether `PUT /api/v1/profile` should be full-replace or partial-update semantics**
   - What we know: `update_settings` uses `model_dump(exclude_unset=True)` (partial update — only supplied fields change).
   - What's unclear: CONTEXT.md doesn't specify; a simple three-field form more naturally submits all three fields every time (full replace), unlike `Settings`' many optional fields.
   - Recommendation: Either works structurally (see Pattern 2's example, which uses `exclude_unset=True` for consistency with `update_settings`); if the UI form always sends all three fields on submit, the distinction is moot in practice. Flag to planner as a minor implementation choice, not a blocking decision.

3. **Does `format` as a Pydantic/SQLModel field name cause any subtle issue?**
   - What we know: Not a reserved word in Python, Pydantic, or SQLAlchemy; no existing field named `format` elsewhere in this codebase to conflict with.
   - What's unclear: Nothing significant — this is LOW risk, included for completeness since it's a common shadowing gotcha (`str.format` method) that's easy to sanity-check but unlikely to cause a real bug (attribute access `row.format` never collides with the builtin).
   - Recommendation: No action needed; proceed as designed.

## Sources

### Primary (HIGH confidence — direct codebase inspection)
- `shared/models.py` — `Settings`, `WorkingMemory`, `LongTermMemory`, `Session` table definitions (FK-cascade convention, unique-constraint patterns)
- `agent/memory.py` — CRUD module structure to mirror for `agent/profile.py`
- `agent/main.py` — `_ensure_global_settings`, `_resolve_settings`, `update_settings`, `get_chat_memory` (all direct precedents)
- `agent/context_engine.py::build_system_prompt` — exact current injection assembly logic and call graph (`build_llm_context` → WS + stats)
- `agent/tools.py`, `agent/schemas.py` — confirms tool-call dispatcher shape (unchanged this phase) and existing Pydantic schema conventions
- `agent/dependencies.py` — `get_current_user` session-cookie resolution (reused unchanged)
- `shared/database.py` — `init_db()`/`SQLModel.metadata.create_all()` auto-table-creation behavior; migration functions only exist for column additions to pre-existing tables
- `ui/static/index.html`, `ui/static/app.js` — actual Memory-panel markup and JS (confirmed: static stacked panel, not a tab widget)
- `tests/conftest.py`, `tests/test_memory_api.py` — test fixture and assertion patterns to mirror
- `.planning/phases/03-personalization-day-12/03-CONTEXT.md` — locked decisions D-01 through D-04, discretion areas, canonical refs
- `.planning/REQUIREMENTS.md` — PERS-01 through PERS-04 acceptance criteria
- `.planning/STATE.md` — cross-phase decision that `agent/tools.py` is unchanged in Phases 3-5
- `.planning/codebase/CONVENTIONS.md` — naming/logging/error-handling conventions
- `CLAUDE.md` (project root) — hard constraints, FK-cascade convention, vanilla-JS-only frontend rule

### Secondary / Tertiary
None used — this phase's entire research surface is internal to the existing codebase; no external library research, WebSearch, or Context7 lookups were needed since no new dependencies or unfamiliar APIs are involved.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies, every piece has a working precedent already running in this codebase
- Architecture: HIGH — injection call graph traced directly through source; table-creation mechanism verified by reading `shared/database.py`
- Pitfalls: HIGH — each pitfall is either a direct extrapolation from a real existing pattern (e.g., `_ensure_global_settings`) or a concrete gotcha already solved elsewhere in the codebase (e.g., `.textContent` vs `innerHTML` in Memory panel)

**Research date:** 2026-09-20
**Valid until:** No expiry driver — this research is tied to the current state of this specific codebase, not to external library versions. Re-research only if the codebase's Settings/Memory patterns change materially before this phase is planned/executed.
