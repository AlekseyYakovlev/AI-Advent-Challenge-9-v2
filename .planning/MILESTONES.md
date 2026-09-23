# Milestones

## v1.0 Week 3: Agent Memory & Task State (Shipped: 2026-09-23)

**Phases completed:** 6 phases, 25 plans, 37 tasks

**Key accomplishments:**

- Dedicated WorkingMemory/LongTermMemory SQLite tables with upsert-on-key CRUD, an ownership-checked `GET /api/v1/chats/{chat_id}/memory` endpoint, and an always-visible sidebar panel rendering all three memory layers via `textContent`.
- A general Pydantic-schema-derived tool registry with a strictly-sequential dispatcher (`agent/tools.py`), plus `stream_chat(..., tools=[...])` SSE tool_calls delta accumulation in `agent/llm_client.py` — the only path a memory write can reach the database through.
- Every chat turn now offers both memory tools to the LLM, executes any returned tool calls synchronously inside the existing per-chat lock and session, feeds the results back for a final reply, reports the writes on the `done` frame, and injects the resulting memory read-only into the system prompt on every subsequent turn.
- Verified end-to-end against a real tool-capable local model (qwen/qwen3.5-9b) that memory writes only ever happen via explicit, dispatched tool calls into the correct layer, and that both layers are correctly scoped (per-chat vs. per-user) and visible in the inspection panel.
- User-scoped `Profile` table with owner-only `GET`/`PUT /api/v1/profile` and unconditional injection of non-empty style/format/constraints into every assembled system prompt.
- Permanently-visible sidebar "Профиль" panel with three freeform textareas (style/format/constraints) wired to `GET`/`PUT /api/v1/profile`, using the same stacked-panel shape as the existing Memory panel.
- Structural strategy/turn-count guards for PERS-02 plus a live A/B transcript proving PERS-04 against a real LM Studio model
- GlobalInvariant SQLModel table (no ownership columns) with full CRUD REST routes and a new collapsible Инварианты sidebar tab, plus a shared fold/unfold retrofit applied to all four sidebar panels
- ChatInvariant table with a structural overrides_id FK into GlobalInvariant, a single resolve_active_invariants() resolver consumed by build_system_prompt, and a sidebar overrides dropdown that lets a chat visibly override a global rule
- A second, non-streaming LLM call judges every turn's full response (prose + tool calls) against the chat's resolved active invariants; a flagged conflict triggers a justify/retract round-trip, and the result is persisted as an InvariantConflict row surfaced both as an inline amber chat banner and an amber badge on the Инварианты tab
- Manual pause/resume/cancel now return HTTP 409 with an explained `detail` on illegal attempts, and the Tasks tab renders every refused attempt inline, red and struck through, right alongside accepted transitions.
- End-to-end acceptance evidence for the transition-graph enforcement built in Plans 01-03, gathered via full regression + a real-app.db migration check + direct REST/WS calls against the live app, with one demo deviation traced to LLM tool-selection ambiguity rather than a code defect.

---
Known deferred items at close: 1 (see STATE.md Deferred Items)
