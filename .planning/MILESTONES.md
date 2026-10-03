# Milestones

## v2.0 Week 4: MCP Integration (Shipped: 2026-10-02)

**Phases completed:** 4 phases (7, 8, 9, 12), 27 plans

**Key accomplishments:**

- Owner-task MCP stdio client (mcp 1.30.0) with in-memory (user_id, server_id) session registry, single handshake timeout, four fixed error codes with Russian messages, stderr tail capture and lazy liveness.
- User-scoped `McpServerConfig` table plus a thin CRUD service with JSON args round-tripping and masked-env merge semantics, proven by 10 tests including cascade delete and engine-restart survival.
- User-scoped MCP server REST API (CRUD, connect, disconnect, status) with 404-only ownership checks, env values never returned, auto-disconnect on edit/disable/delete, lazy liveness on list/status, and shutdown cleanup.
- `python scripts/mcp_list_tools.py <command> [args...]` connects through the shared `connect_once_and_list`, prints serverInfo and all tools with typed/required parameters, and on failure prints code, Russian message, detail and stderr tail to stderr with exit code 1.
- "MCP серверы" section in the Settings modal: server list with status badges, inline add/edit form with masked env, connect/disconnect, serverInfo, collapsible tool list with params and raw inputSchema JSON, and Russian error plus stderr display, all built via textContent.
- Socket-free `run_headless_turn` that drives the existing chat tool loop through a RecordingSink, with a dispatcher allowlist limiting built-in tools to `save_long_term_memory` plus the user's MCP tools.
- Foldable sidebar scheduler panel with create/result modals and a live /ws/events client (REST re-sync, backoff reconnect, single guarded poll timer), plus a Node-free Python JS balance check.
- Scheduler documented in API_SPEC / ARCHITECTURE / TESTING_GUIDE; the Day 18 demo was run by Claude through Playwright (real LM Studio model + real filesystem MCP) with 28/30 first-pass checks green, both remaining FAILs being script artifacts that pass on re-check; one model-behaviour limitation accepted by the user.
- Chats are auto-titled by the chat's own model after the first Q&A turn (non-blocking, sanitized, fallback to the first user message, race-safe conditional UPDATE, live `chat_title_updated` over `/ws/events`) — Phase 9 (Day 21).
- "Провайдеры LLM" Settings section: user-scoped OpenAI-compatible providers with `.env`-named keys, connection check badges, provider-grouped model picker, and every LLM call (chat, self-critique, facts, titles, scheduler) routed by `provider_id` — Phase 12 (Day 21).

**Carried over to v3.0:** Phase 10 (modals close only via ×) and Phase 11 (edit/delete long-term memory in UI) — planned, not executed.

---

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
