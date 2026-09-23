# Phase 4: Task State Machine (Day 13) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-20
**Phase:** 04-task-state-machine-day-13
**Areas discussed:** Task tool surface & delegation field, Pause/resume model, Multiple tasks & current-task concept, Task UI placement & interaction

---

## Task tool surface & delegation field

### What fields does create_task capture?

| Option | Description | Selected |
|--------|-------------|----------|
| Title + description only | Minimal — mirrors save_working_memory's key/content simplicity | |
| Title + description + goal | Richer initial framing, useful context for validation later | ✓ |
| You decide | Claude picks based on simplicity | |

**User's choice:** Title + description + goal

### How does the LLM transition a task's state?

| Option | Description | Selected |
|--------|-------------|----------|
| Separate transition_task tool | Explicit, auditable, clean history source | ✓ |
| create_task tool doubles as update | Fewer tools, blurs intent | |
| You decide | | |

**User's choice:** Separate transition_task tool

### How should the delegation field (TASK-03) be shaped?

| Option | Description | Selected |
|--------|-------------|----------|
| Nullable delegate_to column, unused | Cheap, self-documenting, no migration needed later | ✓ |
| Generic metadata JSON/text column | More flexible, less self-documenting | |
| You decide | | |

**User's choice:** Nullable delegate_to column, unused

---

## Pause/resume model

### Is 'paused' a state value or an orthogonal flag?

| Option | Description | Selected |
|--------|-------------|----------|
| Orthogonal is_paused flag | Keeps FSM strictly 4 states | ✓ |
| 'paused' as a 5th enum state | Loses which state it was paused from | |
| You decide | | |

**User's choice:** Orthogonal is_paused flag

### How does a task get paused/resumed?

| Option | Description | Selected |
|--------|-------------|----------|
| New pause_task/resume_task tools | LLM-driven, explicit, auditable | (recommended, applied) |
| Via transition_task with a special value | Fewer tools, conflates concepts | |
| You decide | | ✓ |

**User's choice:** You decide — Claude applied the recommended option (new pause_task/resume_task tools).

### What must persist so resume doesn't require re-explaining context (TASK-04)?

| Option | Description | Selected |
|--------|-------------|----------|
| Task fields + existing WorkingMemory | Reuses Phase 2 storage, matches STATE.md's Phase 6 decision | (recommended, applied) |
| Dedicated task-scoped notes field | New storage, independent of WorkingMemory | |
| You decide | | ✓ |

**User's choice:** You decide — Claude applied the recommended option (task fields + existing WorkingMemory).

---

## Multiple tasks & current-task concept

### Is there a single 'active' task per chat, or a flat list?

| Option | Description | Selected |
|--------|-------------|----------|
| Flat list, no single 'active' task | Matches TASK-02's literal "concurrent tasks" requirement | ✓ |
| One 'current'/'focused' task at a time | Simpler mental model, works against concurrency | |
| You decide | | |

**User's choice:** Flat list, no single 'active' task

### Must task tool calls always pass an explicit task_id?

| Option | Description | Selected |
|--------|-------------|----------|
| Always explicit task_id required | Safer, mirrors memory tools' explicit-key pattern | ✓ |
| Optional, defaults to most-recent task | Convenient but risky with concurrent tasks | |
| You decide | | |

**User's choice:** Always explicit task_id required

---

## Task UI placement & interaction

### Where does the task panel live?

| Option | Description | Selected |
|--------|-------------|----------|
| New sidebar tab | Matches Memory/Profile precedent | ✓ |
| Inline in the chat transcript | Breaks from established pattern | |
| You decide | | |

**User's choice:** New sidebar tab

### How is state-change history displayed?

| Option | Description | Selected |
|--------|-------------|----------|
| Chronological list/timeline per task | Mirrors Memory panel's entry-list rendering | ✓ |
| Compact 'last transition only', expandable | Less clutter with many tasks | |
| You decide | | |

**User's choice:** Chronological list/timeline per task

### Can the user manually transition/close a task from the UI?

| Option | Description | Selected |
|--------|-------------|----------|
| Purely LLM-tool-driven, UI read-only | Tight scope, matches TASK-03 framing | |
| UI also allows manual pause/resume/cancel | Adds a second write path (echoes Phase 3's Profile split) | ✓ |
| You decide | | |

**User's choice:** UI also allows manual pause/resume/cancel

**Notes:** Follow-up needed to pin down scope and mechanism.

### Follow-up: manual UI task control — scope and mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Pause/resume only, via plain REST endpoint | Bypasses tool-call dispatcher, mirrors Profile's PUT endpoint | |
| Pause/resume + manual state transitions, via REST | Broader safety valve, more UI surface | |
| You decide | | |

**User's choice:** Free-text — "Pause/resume/cancel" (added cancel, not offered as a listed option). Interpreted as: pause/resume/cancel via REST, bypassing the tool-call dispatcher (mirrors Phase 3's `PUT /api/v1/profile` split); no manual arbitrary state transitions.

### Follow-up: how does 'cancel' fit the 4-state enum?

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel is a 5th state ('cancelled') | Distinct terminal state, clear in history/UI | ✓ |
| Cancel maps to a flag, not a new state | Keeps enum at exactly 4 values | |
| You decide | | |

**User's choice:** Cancel is a 5th state ('cancelled')

---

## Claude's Discretion

- Pause/resume tool mechanism (new `pause_task`/`resume_task` tools) — user said "you decide"; recommended option applied.
- Resume-data mechanism (task fields + existing WorkingMemory) — user said "you decide"; recommended option applied.
- Exact system-prompt wording for task-tool awareness, `delegate_to` column type detail, sidebar tab markup/styling, and whether `transition_task`'s `note` argument is required or optional — none of these were asked; left to planning/implementation per CONTEXT.md's "Claude's Discretion" section.

## Deferred Ideas

None — discussion stayed within phase scope. The "cancel" addition (from the manual UI control follow-up) was folded into this phase's scope (D-07/D-12 in CONTEXT.md) rather than deferred, since it's a natural extension of the pause/resume UI control already being discussed, not a new capability outside TASK-01..05.
