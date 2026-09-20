# Phase 2: Memory (Day 11) - Context

**Gathered:** 2026-09-20
**Status:** Ready for planning

<domain>
## Phase Boundary

The agent maintains three explicitly separated, inspectable memory layers, populated only through deliberate LLM tool calls — never implicit/automatic classification. Short-term memory is the existing message-tree/current dialog (no new storage). Working memory (current task data) and long-term memory (profile/decisions/knowledge) are new, dedicated SQLite tables, independent of the message tree. A UI panel lets the user inspect exactly what landed in each layer for a given chat. Personalization (profiles), task lifecycle, and invariants are separate later phases — this phase only builds the memory substrate and the tool-call dispatcher they will all reuse.

</domain>

<decisions>
## Implementation Decisions

### Memory tool-call surface
- **D-01:** Two distinct, separately named tools — `save_working_memory(key, content)` and `save_long_term_memory(key, content)` — not one generic `save_memory(layer, ...)` tool. User explicitly delegated this choice ("решай сам"); two named tools were chosen because the tool name itself signals intent to the LLM, which is more reliable than trusting a correctly-set `layer` argument every call — especially relevant given the open question (STATE.md) about tool-calling reliability on the configured local LM Studio model.
- Short-term memory needs no tool call — it is the existing message tree / current context window, unchanged by this phase.

### Long-term memory scope
- **D-02:** Long-term memory is scoped at the **user level** (keyed by `user_id` only, no `chat_id`) — a true cross-chat memory table, not tied to the chat it was written from. User explicitly chose this over chat-scoped, specifically because Phase 3's user profile is built on top of long-term memory, and a profile is not chat-specific by definition.
- **Reconciliation with MEM-04** ("inspect memory for a given chat"): since long-term memory is user-scoped, the per-chat inspection panel shows *all* of the user's long-term memory entries (not filtered to the current chat), alongside this chat's working memory and short-term dialog. This is intentional, not a gap — flag it explicitly to the researcher/planner so the UI doesn't get built as if long-term memory were chat-filtered.

### Working memory shape
- **D-03:** Working memory is a key-value scratchpad table: `(user_id, chat_id, key, value, updated_at)`. `save_working_memory(key, content)` overwrites the row for that key. User delegated this choice ("решай сам"); key-value was chosen over an append-only log because it mirrors the existing `Settings.chat_id`-scoped pattern already used in this codebase, and an append-only log risks duplicating the message tree (blurring the working/short-term boundary) rather than acting as a distinct "current task data" scratchpad.

### Memory inspection UI
- **D-04:** The memory panel lives in the **sidebar as a tab/panel**, next to existing chat settings — always reachable without an extra click, not a modal. It shows all three layers (short-term, working, long-term) for the active chat in one place (per the D-02 reconciliation above, long-term shows the full user-level set).

### Claude's Discretion
- Whether working/long-term memory is automatically injected (read-only) into the system prompt each turn for continuity, vs. purely display-only in the UI this phase. MEM-03 only restricts *writes* to explicit tool calls — reads are not gated. Recommend read-injection (mirrors the existing `Settings`/facts-injection pattern) so the memory tool calls have an observable effect on agent behavior, not just a UI artifact — but this is an implementation call for research/planning, not decided here.
- Exact table field set beyond what D-02/D-03 require (e.g., whether to add a `category` column to long-term memory).
- Tool-calling reliability across DeepSeek vs. the configured local LM Studio model is an **open question already flagged in STATE.md** ("verify before committing a graded demo to it; default to DeepSeek if unreliable") — not re-litigated here; the Phase 2 researcher must verify this empirically before planning commits to a specific dispatch mechanism.
- Exact markup/styling of the sidebar Memory tab — match existing Tailwind patterns in `index.html`/`app.js`.

</decisions>

<specifics>
## Specific Ideas

- No specific product references beyond the decisions above — user delegated most implementation-shape choices ("решай сам") after confirming the three-layer split and the long-term/user-level scoping.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Memory requirements & constraints
- `.planning/PROJECT.md` §Requirements (MEM-01..05), §Constraints (data scoped by `user_id`), §Key Decisions ("Memory layers as new SQLite tables", "LLM chooses what/when to save via tool calls")
- `.planning/REQUIREMENTS.md` §Memory — MEM-01 through MEM-05 acceptance criteria
- `.planning/ROADMAP.md` §Phase 2: Memory (Day 11) — goal, branch (`Day11`), depends-on (Phase 1/Auth), success criteria
- `.planning/STATE.md` §Accumulated Context — locked cross-phase decisions: tool-call dispatcher (`agent/tools.py`) built once in Phase 2 and reused unchanged by Phases 3-5; memory writes must be synchronous, per-chat-locked, tool-call-only (never fire-and-forget/debounced like `extract_and_update_facts`); multiple tool calls in one LLM turn execute strictly sequentially, never `asyncio.gather`d
- `.planning/STATE.md` §Open Questions — Phase 2's flagged LM Studio tool-calling reliability question (unresolved, for researcher to verify)
- `CLAUDE.md` §Hard constraints — data scoping by `user_id`, no Docker/multiprocessing, vanilla-JS-only frontend

### Existing architecture (no memory/tool-calling exists today)
- `.planning/codebase/ARCHITECTURE.md` — documents the existing `extract_and_update_facts` debounced-fact pattern (the anti-pattern this phase must NOT repeat), the per-chat lock pattern (`agent/state.py::chat_locks`), and the Settings global/per-chat NULL-fallback pattern (analogous to the working-memory chat scratchpad shape)
- `.planning/codebase/CONVENTIONS.md` — SQLModel FK-cascade convention, structlog logging pattern, naming conventions new memory tables/tools must follow

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shared/models.py` SQLModel pattern (`sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`) — new `WorkingMemory`/`LongTermMemory` tables must follow this exact FK-cascade convention, never `Field(ondelete=...)`.
- `agent/state.py::chat_locks` — the per-chat `asyncio.Lock` pattern that memory writes must go through (per STATE.md: writes are synchronous and per-chat-locked).
- `agent/context_engine.py::get_effective_settings()` NULL-fallback query pattern — conceptually similar to how working memory is scoped/queried per chat.

### Established Patterns
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers.
- `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses.

### Anti-Pattern to Avoid
- `agent/context_engine.py::extract_and_update_facts` — debounced, implicit LLM-driven fact extraction. This phase's memory writes must be the opposite: synchronous, explicit, tool-call-triggered only (MEM-03).

### Integration Points
- `shared/models.py` — new `WorkingMemory` and `LongTermMemory` tables.
- New `agent/tools.py` — the tool-call dispatcher (registry + execution), built here and reused unchanged by Phases 3-5.
- `agent/ws.py::_handle_chat_message` — wire tool-call handling into the existing per-chat-locked message flow; multiple tool calls in one turn execute sequentially.
- `ui/static/app.js` / `index.html` — new sidebar Memory tab/panel.

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-memory-day-11*
*Context gathered: 2026-09-20*
