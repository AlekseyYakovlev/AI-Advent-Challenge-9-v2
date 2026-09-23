# Phase 3: Personalization (Day 12) - Context

**Gathered:** 2026-09-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Each user's saved preferences (style, format, constraints) are attached to every request and visibly shape how the agent responds. The user can view and edit their own profile in the UI. Long-term/working memory (Phase 2), task lifecycle (Phase 4), and invariants (Phase 5) are separate — this phase only builds the profile substrate, its injection into the system prompt, and the UI to view/edit it.

</domain>

<decisions>
## Implementation Decisions

### Profile storage model
- **D-01:** A new, dedicated `Profile` SQLModel table — not an overlay on `LongTermMemory`. Columns: `user_id` (FK to `user.id`, `ondelete="CASCADE"`, unique — one profile row per user), `style`, `format`, `constraints`, `updated_at`. User explicitly chose this over reserved-key rows in `LongTermMemory`, to keep "settings the user directly edits" distinct from "freeform facts the LLM chose to remember" — the two are conceptually different even though both are long-term, user-scoped state.
- Profile is purely user-scoped (no `chat_id`), matching `LongTermMemory`'s existing user-level scoping (D-02 from Phase 2) and PERS-01's "each user has a profile."

### Edit path
- **D-02:** Profile is edited **UI-only**, via a direct REST endpoint (e.g. `PUT /api/v1/profile`) — no LLM tool call, no `agent/tools.py` registration. User explicitly chose this over adding an LLM-writable `update_profile` tool. Rationale: PERS-03 requires a UI edit surface regardless; Phase 2's "explicit tool call only" philosophy (MEM-03) was scoped to *memory* writes specifically, and profile is closer to `Settings` (user-controlled configuration) than to memory (LLM-decided facts). This mirrors the existing `update_settings` REST pattern, not the tool-call dispatcher.
- The tool-call dispatcher (`agent/tools.py`) itself needs **no changes** in this phase — consistent with `STATE.md`'s cross-phase decision that it's "built once in Phase 2, reused unchanged by Phases 3-5" (it's simply not used for this particular write path).

### Field shape
- **D-03:** `style`, `format`, and `constraints` are each freeform text (e.g. a textarea per field) — no enums/dropdowns. User explicitly chose this over constrained-choice fields, for simplicity: freeform text injects into the system prompt verbatim with no mapping/validation layer, and matches the freeform-value precedent already established by `LongTermMemory`/`WorkingMemory`.

### Profile UI
- **D-04:** The profile editor lives in a **new sidebar tab**, next to the existing Memory tab (same always-reachable placement as `02-CONTEXT.md`'s D-04 memory panel) — not a modal. User explicitly chose this over a modal dialog for consistency with the Memory tab's placement.

### Claude's Discretion
- Exact injection point/wording in `agent/context_engine.py::build_system_prompt()` — where in the assembled prompt the profile section goes relative to the summary/working-memory/long-term-memory sections, and how it's worded (e.g. "User's stated preferences: style=..., format=..., constraints=..."). Must be present on every request per PERS-02, not conditional.
- Whether an unset field (empty string) is omitted from the injected prompt entirely or included as empty — recommend omitting empty fields to avoid injecting noise like "format: " into the prompt.
- Exact markup/styling of the new sidebar Profile tab and its edit form — match existing Tailwind patterns already used for the Memory tab in `index.html`/`app.js`.
- How PERS-04 ("responses observably differ across profiles") gets demonstrated/verified — that's a verification-step concern, not an implementation decision.

</decisions>

<specifics>
## Specific Ideas

No specific product references beyond the decisions above — user confirmed the recommended option on all four gray areas without additional elaboration.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Personalization requirements & constraints
- `.planning/PROJECT.md` §Requirements (PERS-01..04), §Constraints (`user_id` data scoping, vanilla-JS-only frontend)
- `.planning/REQUIREMENTS.md` §Personalization — PERS-01 through PERS-04 acceptance criteria
- `.planning/ROADMAP.md` §Phase 3: Personalization (Day 12) — goal, branch (`Day12`), depends-on (Phase 2/Memory), success criteria
- `.planning/STATE.md` §Accumulated Context — cross-phase decision that `agent/tools.py`'s dispatcher is built once (Phase 2) and reused unchanged by Phases 3-5; writes must be synchronous and per-chat/per-user consistent (no fire-and-forget)
- `CLAUDE.md` §Hard constraints — `user_id` data scoping, no Docker/multiprocessing, vanilla-JS-only frontend, HTTP-only session cookie auth already in place (Phase 1)

### Prior phase precedent (memory substrate this phase builds alongside)
- `.planning/phases/02-memory-day-11/02-CONTEXT.md` — D-02 (long-term memory is user-scoped, not chat-scoped) and D-04 (sidebar tab placement for the Memory panel) are the direct precedents for this phase's D-01 and D-04
- `.planning/codebase/CONVENTIONS.md` — SQLModel FK-cascade convention, structlog logging pattern, naming conventions the new `Profile` table and its CRUD module must follow
- `.planning/codebase/ARCHITECTURE.md` — documents the existing `Settings` global/per-chat pattern and `build_system_prompt()`'s current assembly logic, both directly relevant to where/how profile injection is added

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shared/models.py` SQLModel FK-cascade pattern (`sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`), as used by `WorkingMemory`/`LongTermMemory` (`shared/models.py:138-186`) — the new `Profile` table must follow this exact convention.
- `agent/memory.py` — CRUD module pattern (list/save functions, commit/rollback try-except, `logger.info` on write) to mirror for a new `agent/profile.py` (get/update profile).
- `agent/context_engine.py::build_system_prompt()` (`agent/context_engine.py:61-88`) — existing injection point; already assembles summary text, working memory, and long-term memory into the system prompt. Profile injection is one more `parts.append(...)` block here.
- `agent/main.py`'s existing `update_settings` REST endpoint — direct precedent for a UI-only, non-tool-call PUT endpoint with commit/rollback handling, for the new `PUT /api/v1/profile`.
- `ui/static/app.js`'s Memory tab rendering (`loadChatMemory`, `renderMemoryPanel`, `renderMemoryEntries` at `ui/static/app.js:251-304`) — direct pattern to mirror for a new Profile tab (load-on-select, render-into-container).

### Established Patterns
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses (not expected to be needed here since profile is always keyed by a non-null `user_id`).
- Type hints everywhere; `structlog` logging (`logger = get_logger(__name__)`) — no `print()`.

### Integration Points
- `shared/models.py` — new `Profile` table (one row per user).
- New `agent/profile.py` — get/update CRUD, mirroring `agent/memory.py`'s structure.
- `agent/main.py` — new `GET /api/v1/profile` and `PUT /api/v1/profile` REST endpoints (session-scoped to the current user, no tool-call involvement).
- `agent/context_engine.py::build_system_prompt()` — add profile injection alongside the existing summary/working-memory/long-term-memory sections.
- `ui/static/index.html` / `ui/static/app.js` — new sidebar "Profile" tab with a view/edit form, next to the existing Memory tab.

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. (LLM-writable profile via tool call was considered as an option for the "edit path" decision and explicitly not chosen — see D-02 — not a scope-creep deferral.)

</deferred>

---

*Phase: 03-personalization-day-12*
*Context gathered: 2026-09-20*
