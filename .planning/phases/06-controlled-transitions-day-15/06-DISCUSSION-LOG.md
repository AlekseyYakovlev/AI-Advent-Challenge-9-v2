# Phase 6: Controlled Transitions (Day 15) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-21
**Phase:** 06-controlled-transitions-day-15
**Areas discussed:** Transition graph shape, Rejection channel, Phase 5 conflict-check wiring, Rejected-attempt logging

---

## Transition graph shape

### Should the graph allow backward/rework moves, or only forward progression?

| Option | Description | Selected |
|--------|-------------|----------|
| Forward-only | planning→execution→validation→done, one direction only | |
| Allow one step back | validation→execution and execution→planning rework loops | ✓ (modified) |
| You decide | Claude picks during planning | |

**User's choice:** Allow one step back, with an explicit correction: `done` and `cancelled` are final — no stepping back from them.
**Notes:** Folded into D-01/D-02.

### Should pause/resume be subject to the transition graph's legality checks?

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, guard both | Reject resume on non-paused task, reject pause/resume on terminal tasks | ✓ |
| Leave unguarded | Keep today's unconditional flag-flip behavior | |
| You decide | Claude picks during planning | |

**User's choice:** Yes, guard both.

### Should cancel be blocked once a task is already terminal (done or cancelled)?

| Option | Description | Selected |
|--------|-------------|----------|
| Block on terminal states | cancel_task rejects if state is already done/cancelled | ✓ |
| Leave unguarded | Keep today's no-check behavior | |
| You decide | Claude picks during planning | |

**User's choice:** Block on terminal states.

### Can a transition skip intermediate states?

| Option | Description | Selected |
|--------|-------------|----------|
| No skipping | Each edge is exactly one step, forward or backward | ✓ |
| Allow skipping | Any forward state reachable from any earlier state | |
| You decide | Claude picks during planning | |

**User's choice:** No skipping.

---

## Rejection channel

### Should illegal-transition rejections follow the existing tool-content-only pattern, or be promoted to guarantee a WS-level signal?

| Option | Description | Selected |
|--------|-------------|----------|
| Promote to WS TOOL_ERROR | Mark dispatcher result ok=False, fire the existing WS error frame unconditionally | ✓ |
| Follow existing TaskNotFoundError pattern | Keep it in tool-result content only, rely on LLM prose | |
| Both | Fire WS frame AND pass detail to the LLM | |

**User's choice:** Promote to WS TOOL_ERROR.

### Should this WS-level promotion also apply to pause_task/resume_task rejections, or only transition_task?

| Option | Description | Selected |
|--------|-------------|----------|
| All three task tools | transition_task, pause_task, resume_task all fire the WS frame | ✓ |
| transition_task only | Only state-transition rejections get the WS-level guarantee | |

**User's choice:** All three task tools.

### Should the existing TaskNotFoundError case be upgraded to the same WS TOOL_ERROR treatment, or left as-is?

| Option | Description | Selected |
|--------|-------------|----------|
| Upgrade it too | Harmonize all task-tool domain errors through one ok=False path | ✓ |
| Leave TaskNotFoundError as-is | Only new errors get the WS-level guarantee | |

**User's choice:** Upgrade it too.

---

## Phase 5 conflict-check wiring

### What should ROADMAP's "conflict checks from Phase 5 get wired into transition attempts" concretely mean?

| Option | Description | Selected |
|--------|-------------|----------|
| Invariants can reject transitions too | agent/invariants.py checks run before applying a transition, can block it | |
| Reuse the self-critique pattern only | No new invariant-vs-transition check; illegal transitions get a justify/retract-style re-prompt | ✓ |
| No new integration | ROADMAP line is just context, not a requirement | |

**User's choice:** Reuse the self-critique pattern only.

### Should the rejection ride the existing tool-result follow-up call, or need a distinct justify/retract-style prompt?

| Option | Description | Selected |
|--------|-------------|----------|
| Distinct justify/retract prompt | Build a dedicated prompt mirroring build_justify_retract_prompt's shape | ✓ |
| Ordinary tool-result follow-up | No new prompt construction, rely on the LLM's normal next turn | |

**User's choice:** Distinct justify/retract prompt.

---

## Rejected-attempt logging

### Should rejected attempts be persisted, or purely in-flight?

| Option | Description | Selected |
|--------|-------------|----------|
| Persist rejected attempts | Audit trail in the task's history, mirrors D-13's InvariantConflict precedent | ✓ |
| No persistence | Purely a WS-response concern, nothing in the DB | |

**User's choice:** Persist rejected attempts.

### Extend TaskTransition or a separate table?

| Option | Description | Selected |
|--------|-------------|----------|
| Extend TaskTransition | Add rejected/rejection_reason columns, one chronological table | ✓ |
| New TaskTransitionAttempt table | Separate table for attempts vs. real transitions | |

**User's choice:** Extend TaskTransition.

### Render rejected attempts inline or separately in the Tasks tab?

| Option | Description | Selected |
|--------|-------------|----------|
| Inline, visually distinct | Same chronological list, styled differently (e.g. red/strikethrough) | ✓ |
| Separate section | Two distinct lists/panels | |

**User's choice:** Inline, visually distinct.

---

## Claude's Discretion

- Exact wording of the D-08 justify/retract-style prompt for illegal transitions.
- Exact mechanics of how D-08's re-prompt interacts with the existing unconditional post-tool-dispatch follow-up call in `agent/ws.py:284-310`.
- Whether pause_task/resume_task's WS promotion (D-06) reuses the exact `IllegalTransitionError` type or gets its own error type per tool.
- Exact max-length/nullability for `TaskTransition.rejection_reason` (D-09).
- Whether REST-triggered rejections (cancel_task, etc.) surface as `HTTPException` 4xx per the existing REST convention rather than the WS TOOL_ERROR frame.

## Deferred Ideas

None — discussion stayed within phase scope.
