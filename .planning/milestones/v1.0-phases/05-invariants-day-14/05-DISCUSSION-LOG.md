# Phase 5: Invariants (Day 14) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-20
**Phase:** 05-invariants-day-14
**Areas discussed:** Invariant shape & creation path, Global/per-chat precedence, Conflict-check mechanism (INV-04), UI surface for invariants & conflicts

---

## Invariant shape & creation path

| Option | Description | Selected |
|--------|-------------|----------|
| Freeform text entry | Single text field per invariant, like LongTermMemory's key-value | |
| Title + rule text | Two fields — short title + rule body, richer display | ✓ (Claude's discretion — recommended) |
| You decide | Claude picks during planning | ✓ (user chose this) |

**User's choice:** You decide → Claude's discretion applied, recommending title + rule text.

| Option | Description | Selected |
|--------|-------------|----------|
| Any user, via UI (admin-equivalent) | Unscoped REST endpoint, matches "every account = admin" | ✓ |
| LLM tool call (any user's chat) | save_global_invariant-style tool | |
| You decide | Claude picks during planning | |

**User's choice:** Any user, via UI (admin-equivalent).

| Option | Description | Selected |
|--------|-------------|----------|
| UI-only REST (like Profile) | Sidebar form, no LLM tool call | ✓ |
| LLM tool call (like create_task) | add_chat_invariant tool | |
| Both | UI form + LLM tool | |

**User's choice:** UI-only REST (like Profile).

| Option | Description | Selected |
|--------|-------------|----------|
| Add + delete only (no edit) | Simplest, no edit form | |
| Full CRUD (add/edit/delete) | Edit in place, more UI work | ✓ |
| You decide | Claude picks during planning | |

**User's choice:** Full CRUD (add/edit/delete).

**Notes:** None beyond the selections above.

---

## Global/per-chat precedence

| Option | Description | Selected |
|--------|-------------|----------|
| Global always wins | Per-chat can only add, never override | |
| Per-chat can override global | Same-topic per-chat rule supersedes global | ✓ |
| You decide | Claude picks during planning | |

**User's choice:** Per-chat can override global.

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit link on creation | Nullable overrides_id FK, chosen from dropdown | ✓ |
| No matching — both injected, LLM resolves | No structural link, LLM reconciles at inference time | |
| You decide | Claude picks during planning | |

**User's choice:** Explicit link on creation. **Notes:** User explicitly rejected relying on the LLM to resolve freeform-text conflicts, since invariants aren't keyed fields.

| Option | Description | Selected |
|--------|-------------|----------|
| Show both, mark override explicitly | [GLOBAL] ... (overridden), [CHAT] ... (overrides) | ✓ |
| Suppress the overridden global rule | Only inject the per-chat rule | |
| You decide | Claude picks during planning | |

**User's choice:** Show both, mark override explicitly.

---

## Conflict-check mechanism (INV-04)

| Option | Description | Selected |
|--------|-------------|----------|
| Second LLM self-critique call | Extra LLM call reviewing the turn against invariants | ✓ |
| Programmatic keyword/pattern scan | Deterministic text-overlap check | |
| You decide | Claude picks during planning | |

**User's choice:** Second LLM self-critique call.

| Option | Description | Selected |
|--------|-------------|----------|
| Full response: prose + tool calls | Feed the critique call everything the agent did/said | ✓ (Claude's discretion — recommended) |
| Tool-call args only | Narrower, cheaper | |
| You decide | Claude picks during planning | ✓ (user chose this) |

**User's choice:** You decide → Claude's discretion applied, recommending full response scope (matches INV-04's literal wording).

| Option | Description | Selected |
|--------|-------------|----------|
| One more round-trip before done | Re-prompt LLM to justify/retract before sending done frame | ✓ (Claude's discretion — recommended) |
| Flag alongside the original response | Original response unedited, separate WS event carries the critique | |
| You decide | Claude picks during planning | ✓ (user chose this) |

**User's choice:** You decide → Claude's discretion applied, recommending the round-trip-before-done flow (INV-04 requires the LLM to actually act, not just be flagged).

---

## UI surface for invariants & conflicts

| Option | Description | Selected |
|--------|-------------|----------|
| New sidebar tab | Matches Memory/Profile/Tasks precedent | ✓ |
| Folded into existing settings panel | Less UI surface, breaks tab-per-concern pattern | |
| You decide | Claude picks during planning | |

**User's choice:** New sidebar tab. **Notes:** User added unprompted scope: all sidebar tabs except "Chats" should default to minified/header-only, with a fold/unfold toggle button in each header — retrofitting the existing Memory, Profile, and Tasks tabs, not just the new Invariants tab.

| Option | Description | Selected |
|--------|-------------|----------|
| Inline in chat + badge on tab | Visible callout in chat stream + tab header badge/count | ✓ |
| Invariants tab only | Conflicts only listed in the sidebar tab's log | |
| You decide | Claude picks during planning | |

**User's choice:** Inline in chat + badge on tab.

| Option | Description | Selected |
|--------|-------------|----------|
| Persisted (new table) | New InvariantConflict table, survives reloads | ✓ |
| Transient (WS event only) | No DB storage, lost on refresh | |
| You decide | Claude picks during planning | |

**User's choice:** Persisted (new table).

---

## Claude's Discretion

- Invariant shape: title + rule text (recommended default, user delegated).
- Self-critique check scope: full response (prose + tool calls), not tool-call-args-only (recommended default, user delegated).
- Justify/retract flow: one more LLM round-trip before the `done` frame (recommended default, user delegated).
- Exact injected system-prompt wording, table schema details beyond what decisions require, self-critique/justify-retract prompt wording, Tailwind markup for the new tab and fold/unfold mechanism, and whether fold/unfold state persists across reloads.

## Deferred Ideas

None — discussion stayed within phase scope. The tab-minification/fold-unfold decision touches existing Memory/Profile/Tasks tab markup from prior phases, but was captured as an explicit in-scope UI decision for this phase (D-11 in CONTEXT.md), not deferred to a future phase.
