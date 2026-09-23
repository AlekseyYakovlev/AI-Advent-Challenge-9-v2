# Phase 2: Memory (Day 11) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-20
**Phase:** 02-memory-day-11
**Areas discussed:** Memory tool-call surface, Long-term memory scope, Working memory shape, Memory inspection UI

---

## Memory tool-call surface

| Option | Description | Selected |
|--------|-------------|----------|
| One generic `save_memory` tool | `save_memory(layer, key, content)` — single tool, dispatcher branches on `layer` | |
| Two distinct tools | `save_working_memory(key, content)` / `save_long_term_memory(key, content)` — tool name signals intent | ✓ |
| You decide | Claude picks based on tool-calling reliability across backends | |

**User's choice:** "Решай сам" (you decide) — Claude selected **two distinct tools**.
**Notes:** User first restated the three memory layers (short-term/working/long-term) to confirm shared understanding before delegating the tool-naming choice. Rationale for two tools: named tools signal intent more reliably than a `layer` argument, which matters given the unresolved LM Studio tool-calling reliability question (STATE.md).

---

## Long-term memory scope

| Option | Description | Selected |
|--------|-------------|----------|
| User-level (across chats) | Table keyed by `user_id` only — true cross-chat memory | ✓ |
| Chat-level (`user_id` + `chat_id`) | Each entry tied to the chat it was created in | |
| You decide | Claude picks based on Phase 3 profile needs and MEM-04 | |

**User's choice:** User-level (across chats).
**Notes:** User explicitly reasoned that Phase 3's user profile is built on top of long-term memory, and a profile isn't chat-specific — so long-term memory shouldn't be chat-scoped either. This means the per-chat inspection UI will show a user-level (not chat-filtered) view of long-term memory — captured as an explicit reconciliation note in CONTEXT.md.

---

## Working memory shape

| Option | Description | Selected |
|--------|-------------|----------|
| Key-value (overwritable) | `(chat_id, key, value, updated_at)` — mirrors existing `Settings.chat_id` pattern | ✓ (Claude's choice) |
| Append-only log | `(chat_id, content, created_at)` — every call adds an entry, nothing overwritten | |
| You decide | Claude picks based on what Phase 4's Task State Machine will expect | ✓ |

**User's choice:** "Решай сам" (you decide) — Claude selected **key-value scratchpad**.
**Notes:** Key-value chosen over append-log because it mirrors an existing codebase pattern (`Settings.chat_id`) and avoids duplicating the message tree, which an append-only log risks doing (blurring the short-term/working-memory boundary).

---

## Memory inspection UI

| Option | Description | Selected |
|--------|-------------|----------|
| Sidebar panel (tab) | New tab next to chat settings, always reachable, shows all 3 layers at once | ✓ |
| Modal dialog | "Memory" button in chat header opens an overlay | |
| You decide | Claude picks based on what fits existing `app.js`/`index.html` patterns | |

**User's choice:** Sidebar panel (tab).
**Notes:** No further elaboration requested — user moved directly to finalize after this answer.

---

## Claude's Discretion

- Whether working/long-term memory is automatically read-injected into the system prompt each turn (recommended, but left to research/planning) vs. display-only this phase.
- Exact table field set beyond the decided key-value/user-scoped shapes.
- Tool-calling reliability across DeepSeek vs. the configured local LM Studio model — carried forward from STATE.md's existing open question, not re-litigated in this discussion; flagged for the Phase 2 researcher to verify empirically.
- Exact markup/styling of the sidebar Memory tab.
- Memory tool-call surface naming (two distinct tools) and working memory shape (key-value) — both explicitly delegated by the user ("решай сам").

## Deferred Ideas

None — discussion stayed within phase scope.
