# Phase 3: Personalization (Day 12) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-20
**Phase:** 03-personalization-day-12
**Areas discussed:** Storage model, Edit path, Field shape, UI placement

---

## Storage model

| Option | Description | Selected |
|--------|-------------|----------|
| New dedicated Profile table | New `Profile` SQLModel table (user_id, style, format, constraints, updated_at) with explicit columns | ✓ |
| Reuse LongTermMemory with reserved keys | Store profile fields as ordinary LongTermMemory rows under reserved keys like `profile:style` | |

**User's choice:** New dedicated Profile table (the recommended option).
**Notes:** None provided beyond selecting the recommendation.

---

## Edit path

| Option | Description | Selected |
|--------|-------------|----------|
| UI-only, direct REST edit | User edits profile fields in a form; PUT /api/v1/profile writes directly (no tool call) | ✓ |
| UI edit + LLM tool call | Adds a new `update_profile` tool the LLM can call mid-conversation, in addition to the UI form | |

**User's choice:** UI-only, direct REST edit (the recommended option).
**Notes:** None provided beyond selecting the recommendation.

---

## Field shape

| Option | Description | Selected |
|--------|-------------|----------|
| Freeform text per field | style/format/constraints are each a free-text textarea | ✓ |
| Constrained choices | style/format are dropdowns/selects with a fixed set of options, constraints stays free text | |

**User's choice:** Freeform text per field (the recommended option).
**Notes:** None provided beyond selecting the recommendation.

---

## UI placement

| Option | Description | Selected |
|--------|-------------|----------|
| New sidebar tab, next to Memory | Mirrors the existing Memory panel pattern — a 'Profile' tab always reachable in the sidebar | ✓ |
| Modal dialog | A 'Profile' button opens an edit modal, similar to the existing 'Add user' modal | |

**User's choice:** New sidebar tab, next to Memory (the recommended option).
**Notes:** None provided beyond selecting the recommendation.

---

## Claude's Discretion

- Exact injection point/wording in `build_system_prompt()` relative to summary/working-memory/long-term-memory sections
- Whether empty profile fields are omitted from the injected prompt (recommended: omit)
- Exact markup/styling of the new sidebar Profile tab and edit form
- How PERS-04's "observable difference across profiles" is demonstrated/verified

## Deferred Ideas

None — discussion stayed within phase scope. LLM-writable profile via tool call was considered as an "Edit path" alternative and explicitly not chosen, not deferred as out-of-scope.
