# Phase 5: Invariants (Day 14) - Context

**Gathered:** 2026-09-20
**Status:** Ready for planning

<domain>
## Phase Boundary

The agent's reasoning is grounded in explicit, layered ground rules ("invariants") that the user can see, extend per-chat, and that flag conflicts with actual agent behavior. Global invariants (architecture/stack/business rules) are stored separately from the dialog and shared across all users; per-chat invariants layer on top with an explicit precedence rule. Active invariants are injected into context on every relevant request. A dedicated check inspects the agent's response/tool calls for conflicts and prompts the LLM to justify or retract. UI surfaces active invariants and detected conflicts.

**Explicitly out of this phase's scope (carried forward from `STATE.md`):** hard enforcement/rejection of illegal task transitions (TRANS-01/02/03) is Phase 6, not this phase — invariant conflicts here are flagged/justified, not hard-blocked.

</domain>

<decisions>
## Implementation Decisions

### Invariant data model & shape
- **D-01 (Claude's discretion — recommended default applied):** An invariant has **two fields: title + rule text**, not a single freeform blob. User delegated the shape choice; title+text is recommended because it makes the sidebar list, conflict messages ("violates invariant: X"), and the override dropdown (D-05) all readable/selectable — a plain freeform blob can't be dropdown-selected cleanly.
- **D-02:** Global invariants live in a table that is **NOT `user_id`-scoped** — a genuinely shared, app-wide set, per `PROJECT.md`'s "only global project invariants remain shared across users" (the one deliberate exception to this project's usual per-user data scoping). **Any logged-in user can create/edit/delete global invariants** via an unscoped UI-only REST endpoint (mirrors Profile's `PUT /api/v1/profile` pattern, just without a `user_id` filter) — consistent with "every account has equal admin capability."
- **D-03:** Per-chat invariants are added **UI-only via REST**, not an LLM tool call — mirrors Profile's D-02 precedent (`03-CONTEXT.md`): this is user-controlled configuration, not LLM-decided memory. `agent/tools.py`'s dispatcher is **not touched** by this phase at all (no new tools registered for invariants).
- **D-04:** **Full CRUD** (add/edit/delete) on invariants, both global and per-chat — not append-only. User explicitly chose editability over an add-only/immutable log.

### Global/per-chat precedence
- **D-05:** Per-chat invariants **can override** a global invariant (not merely add to it) — resolves the open question flagged in `STATE.md` ("Global-vs-per-chat invariant precedence direction — must be explicit"). The override relationship is an **explicit link set at creation time**: a nullable `overrides_id` FK on the per-chat invariant, chosen from a dropdown of existing global invariants — not automatic topic/keyword matching. This is why D-01's title field matters: the dropdown needs a short label per global invariant.
- **D-06:** When an override exists, the system prompt injects **both rules, explicitly labeled** — e.g. `[GLOBAL] <rule text> (overridden for this chat — see below)` followed by `[CHAT] <rule text> (overrides the above)`. User explicitly chose full transparency over a shorter, suppressed-global-rule prompt.

### Conflict-check mechanism (INV-04)
- **D-07:** The conflict check is a **second LLM call** — a self-critique pass, not a programmatic keyword/pattern scan. It is fed the chat's active invariants (already resolved per D-05/D-06) plus the assistant's completed turn, and asked whether anything conflicts.
- **D-08 (Claude's discretion — recommended default applied):** The self-critique call inspects the **full response: prose + tool calls** (not tool-call-args-only) — resolves `STATE.md`'s other flagged open question, and matches INV-04's literal wording ("response/tool calls"). User delegated the scope choice; full-response is recommended since prose-only conflicts (e.g. the LLM suggesting a Docker-based approach in text without ever calling a tool) would otherwise be invisible to the check.
- **D-09 (Claude's discretion — recommended default applied):** On a flagged conflict, the turn does **one more LLM round-trip before the `done` frame** — re-prompting with the specific conflict and asking the LLM to justify or retract, with that reply becoming the final assistant output. User delegated this choice; recommended because INV-04 requires the LLM to actually **act** ("justify or retract"), not just be flagged after the fact — a build-time decision, not a passive UI-only annotation. Mechanically this is a third potential `stream_chat` call in `agent/ws.py::_handle_chat_message`, following the same shape as the existing post-tool-result follow-up call (lines 283-309).

### UI surface
- **D-10:** A **new sidebar "Invariants" tab**, listing global invariants (with add/edit/delete controls) and the current chat's per-chat invariants (with add/edit/delete + an "overrides" dropdown, per D-05) — same always-reachable placement precedent as the Memory/Profile/Tasks tabs (`02-CONTEXT.md` D-04, `03-CONTEXT.md` D-04, `04-CONTEXT.md` D-10).
- **D-11 (scope note — explicit user request, extends beyond a brand-new-tab-only change):** **All sidebar tabs except "Chats" become minified (header-only) by default**, each with a **fold/unfold toggle button in its header**. This retrofits the existing Memory, Profile, and Tasks tabs (shipped in Phases 2-4) as well as the new Invariants tab — not scope creep in the "new capability" sense (no new feature requirement is added), but it does mean this phase's UI work touches prior phases' sidebar markup. Flagged explicitly so the planner doesn't scope it down to only the new tab.
- **D-12:** Detected conflicts surface **inline in the chat stream** — a visible flag/callout right after the flagged response — **and** as a **badge/count on the Invariants tab header**. Both, not either/or.
- **D-13:** Conflict records are **persisted** in a new `InvariantConflict`-style table (chat_id, message_id, invariant_id, justification/note text, created_at) — not transient WS-only events. Backs the Invariants tab's conflict log and badge count, and survives page reloads.

### Claude's Discretion
- Exact wording/format of the injected invariants section in `build_system_prompt()` beyond D-06's override-labeling requirement — follow the existing pattern from profile/memory/task injection.
- Exact schema for `Invariant`/`InvariantConflict` tables beyond what D-01 through D-13 require (e.g., whether `title` has a max length matching `Profile`'s 2000-char fields).
- Exact self-critique prompt wording for D-07/D-08/D-09 — the specific instructions given to the LLM for the critique pass and the justify/retract re-prompt.
- Exact Tailwind markup/styling of the new Invariants tab and the fold/unfold mechanism (D-11) — match existing patterns in `index.html`/`app.js`, but note this is genuinely new shared UI (no fold/unfold precedent exists yet in this codebase).
- Whether the fold/unfold state (D-11) persists across reloads (e.g. localStorage) or resets each session — not specified by the user, low-stakes UI polish.

</decisions>

<specifics>
## Specific Ideas

- User specifically wants the Invariants tab (and, retroactively, Memory/Profile/Tasks) to default to **minified/collapsed** with an explicit fold/unfold control in the header — came up unprompted while answering "where does the invariants list live," not something offered as an option. This is a genuine UI density preference, not scope creep (see D-11).
- The override relationship (D-05) should be **explicit and structural** (a dropdown/FK at creation time), not inferred — user rejected the "LLM resolves conflicting freeform text" option specifically because invariants aren't keyed fields.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Invariants requirements & constraints
- `.planning/PROJECT.md` §Requirements (INV-01..05), §Constraints (`user_id` data scoping — note the deliberate exception for global invariants, D-02), §Key Decisions ("Invariants enforced via prompt-injection + explicit response-conflict check")
- `.planning/REQUIREMENTS.md` §Invariants (lines 40-44) — INV-01 through INV-05 acceptance criteria; §Traceability (lines 109-113)
- `.planning/ROADMAP.md` §Phase 5: Invariants (Day 14) — goal, branch (`Day14`), depends-on (Phase 2/Memory), success criteria
- `.planning/STATE.md` §Accumulated Context — cross-phase decisions: `agent/tools.py` dispatcher built once in Phase 2, reused unchanged by Phases 3-5 (this phase deliberately does NOT add new tools to it, per D-03); writes synchronous/per-chat-locked; multiple tool calls in a turn execute strictly sequentially
- `.planning/STATE.md` §Open Questions — both Phase 5-flagged questions ("INV-04 conflict-check scope: tool-args-only vs. also-scanning-prose" and "global-vs-per-chat invariant precedence direction") are **resolved by this discussion** (D-08 and D-05/D-06 respectively) — researcher/planner should treat them as closed, not re-litigate
- `CLAUDE.md` §Hard constraints — `user_id` data scoping (with D-02's documented exception), no Docker/multiprocessing, vanilla-JS-only frontend, HTTP-only session cookie auth (Phase 1)

### Prior phase precedent (patterns this phase must follow)
- `.planning/phases/03-personalization-day-12/03-CONTEXT.md` — D-02 (UI-only REST edit path bypassing the tool-call dispatcher) is the direct precedent for D-03/D-10 here; D-04 (sidebar tab placement) precedent for D-10
- `.planning/phases/02-memory-day-11/02-CONTEXT.md` — D-04 (sidebar tab placement, "always reachable without an extra click")
- `.planning/phases/04-task-state-machine-day-13/04-CONTEXT.md` — D-11 (chronological history list rendering) is a relevant pattern for the conflict log (D-13); D-12 (manual REST control bypassing the tool dispatcher) reinforces the UI-only precedent
- `.planning/codebase/CONVENTIONS.md` — SQLModel FK-cascade convention, structlog logging pattern, naming conventions the new `Invariant`/`InvariantConflict` tables and `agent/invariants.py` must follow
- `.planning/codebase/ARCHITECTURE.md` — documents `build_system_prompt()`'s existing injection assembly and the per-chat lock pattern (`agent/state.py::chat_locks`) the conflict-check round-trip must run inside

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/context_engine.py::build_system_prompt()` (`agent/context_engine.py:61-107`) — direct injection point; invariants become one more `parts.append(...)` block, following the existing profile/facts/working-memory/long-term-memory/open-tasks assembly order.
- `agent/ws.py::_handle_chat_message` (`agent/ws.py:143-368`) — the per-chat-locked WS turn already runs up to 2 `stream_chat` calls (initial response, then a post-tool-result follow-up at lines 283-309 with the same error/rollback handling). D-09's justify/retract round-trip is a third call following that exact shape.
- `shared/models.py`'s `Settings` chat_id-nullable-FK pattern (`shared/models.py:73-108`) — conceptual precedent for per-chat vs. global scoping, but **note the difference**: `Settings`' global row is still per-request-resolvable via NULL `chat_id`, while `Invariant`'s global rows have no `user_id` at all (D-02) — don't copy the NULL-fallback query pattern verbatim; the global/per-chat split here is structurally different (two tables or a scope discriminator, not a single NULL-able FK).
- `agent/profile.py` + `agent/main.py`'s `PUT /api/v1/profile` — direct precedent for the UI-only invariant CRUD REST endpoints (D-03), including commit/rollback handling.
- `ui/static/app.js`'s `renderMemoryPanel`/`renderProfilePanel`/task panel rendering (`ui/static/app.js:251-334` and the Phase 4 task tab) — pattern to mirror for the new Invariants tab; the fold/unfold mechanism (D-11) is **new shared UI** with no existing precedent to copy — it needs to be built once and applied to all four tabs.

### Established Patterns
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses.
- Type hints everywhere; `structlog` logging (`logger = get_logger(__name__)`) — no `print()`.
- Tool-call writes are synchronous, per-chat-locked — **not applicable this phase**, since invariants are UI-only writes (D-03), but the conflict-check LLM call must still run inside the existing `chat_locks[chat_id]` lock in `_handle_chat_message`.

### Integration Points
- `shared/models.py` — new `Invariant` table (id, is_global bool or separate global/chat tables, title, rule_text, chat_id nullable FK `ondelete="CASCADE"` for per-chat rows, `overrides_id` nullable self-referential FK for D-05, created_at, updated_at) and a new `InvariantConflict` table (chat_id, message_id FK, invariant_id FK, note/justification, created_at) for D-13.
- New `agent/invariants.py` — CRUD + active-invariant-resolution logic (list global, list per-chat, resolve overrides per D-05/D-06), mirroring `agent/memory.py`/`agent/profile.py`'s structure.
- `agent/context_engine.py::build_system_prompt()` — add invariants injection, including override labeling (D-06).
- `agent/ws.py::_handle_chat_message` — add the self-critique call (D-07/D-08) after the assistant turn is assembled, and the conditional justify/retract round-trip (D-09) before the `done` frame; persist conflicts (D-13); include conflict info in the `done` payload or a new WS event type for the inline chat flag (D-12).
- `agent/main.py` — new REST endpoints: `GET/POST/PUT/DELETE /api/v1/invariants` (global, unscoped by `user_id` per D-02), `GET/POST/PUT/DELETE /api/v1/chats/{chat_id}/invariants` (per-chat), `GET /api/v1/chats/{chat_id}/invariant-conflicts` (conflict log for the tab).
- `ui/static/index.html` / `ui/static/app.js` — new sidebar "Invariants" tab (list + add/edit/delete forms + overrides dropdown); the fold/unfold retrofit (D-11) applied to Memory, Profile, Tasks, and Invariants tab headers; inline conflict banner in the chat message stream (D-12).

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. (D-11's tab-minification affects existing Memory/Profile/Tasks tab markup, but it's a direct, explicit user request captured as an in-scope UI decision for this phase, not a deferred idea.)

</deferred>

---

*Phase: 05-invariants-day-14*
*Context gathered: 2026-09-20*
