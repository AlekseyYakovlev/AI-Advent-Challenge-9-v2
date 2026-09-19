# Feature Research

**Domain:** LLM agent memory systems, per-user personalization, and task state machines (course project — "AI Advent Challenge" Week 3, Days 11-15)
**Researched:** 2026-09-19
**Confidence:** MEDIUM-HIGH (memory-tool and state-machine patterns verified against multiple independent sources incl. Anthropic's own memory tool docs, Letta/MemGPT, LangGraph; personalization patterns verified against OpenAI's public ChatGPT Memory writeup; no Context7 library applies directly since this is an architectural/pattern question, not a specific SDK API question)

## Feature Landscape

### Table Stakes (Users/Graders Expect These)

These map directly to the Active requirements in PROJECT.md (MEM-01..05, PERS-01..04, TASK-01..05, TRANS-01..03). Every production/research agent-memory or task-orchestration system reviewed (Anthropic's memory tool, Letta/MemGPT, LangGraph, OpenAI Agents SDK, ChatGPT Memory) implements some version of each row below. Skipping any of these makes the "we have layered memory / a real state machine" claim hollow.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Explicit memory-write tool calls (not silent/implicit classification) | Every reference implementation (Anthropic `memory_20250818` tool, Letta/MemGPT `core_memory_append`, `archival_memory_insert`) makes the LLM call a tool to write memory — this is the industry-standard way to make "what got saved and why" legible and demoable. Anthropic's own memory tool is explicitly client-side: Claude requests file/record operations, your app executes and persists them. This directly satisfies MEM-03. | MEDIUM | Define 2-3 tool schemas (e.g. `save_to_working_memory`, `save_to_long_term_memory`) with a `layer`/`category` field and a `reason`/justification string — the justification is what makes the decision inspectable (MEM-04) for near-zero extra cost. |
| Distinct, separately-queryable storage per memory tier | MemGPT/Letta's foundational contribution is precisely this: core memory (in-context), recall memory (session history), archival memory (long-term, queried). Anthropic's memory tool also treats "context window" and "persistent store" as structurally different. This satisfies MEM-01/MEM-02 (dedicated tables, not folded into the message tree). | LOW-MEDIUM | You already have a message tree for short-term/dialog; working memory and long-term memory each need their own SQLModel table scoped by `chat_id` and/or `user_id`. |
| Session-scoped working memory tied to the active task | Distinguishing "current task scratchpad" from "durable profile/knowledge" is the norm — MemGPT calls this a working-set distinction; it maps onto your MEM-01 three-layer split (short-term/working/long-term). Working memory should be cheap to clear when a task completes. | LOW-MEDIUM | Working memory rows should carry the `task_id` they belong to so clearing/archiving a task can cascade cleanly. |
| A way to inspect stored memory (admin/debug view) | Both Letta and ChatGPT Memory expose a management surface ("Manage Memory" in ChatGPT settings; Letta's memory viewer/API). Without this, "explicit and inspectable" (Core Value in PROJECT.md) can't be verified by a grader. Satisfies MEM-04. | LOW | A REST endpoint returning each layer's current rows per chat/user is sufficient; UI panel is MEM-05. |
| UI surface showing live memory contents | ChatGPT ships a dedicated "Manage Memory" UI; this is now a users' baseline expectation for any product claiming to "remember." Satisfies MEM-05. | LOW-MEDIUM | Given the vanilla-JS/no-bundler constraint, a simple panel that polls a REST endpoint (same pattern as the existing context/token stats panel) is idiomatic for this codebase. |
| Per-user preference/profile object with structured fields | ChatGPT Memory, and essentially every personalization system, stores discrete facts/preferences (style, role, constraints) rather than a single blob, so they can be surfaced/edited individually. Satisfies PERS-01. | LOW | A `Settings`-like table scoped to `user_id` (style, verbosity, tone, format, domain constraints) is enough — no need for a general knowledge graph. |
| Profile injected into every request (system prompt or prepended context) | This is the entire premise of "personalization" in every system reviewed — ChatGPT "references that profile silently in every future conversation." Satisfies PERS-02. | LOW | Analogous to how `_resolve_settings` already injects global/per-chat settings — add user profile to the same assembly step in `agent/main.py`/context builder. |
| UI to view/edit profile | ChatGPT's Settings > Personalization > Memory is the reference pattern — users expect direct control over what's inferred/stored about them. Satisfies PERS-03. | LOW | Simple form bound to the profile table; matches existing per-chat Settings UI pattern already in the codebase. |
| Observable behavioral difference across profiles | This is the acceptance test for personalization, not an implementation detail — if two users with different profiles get identical responses, the feature isn't real. Satisfies PERS-04. | LOW (given the above) | Comes largely for free once the profile is actually injected into the system prompt — verify with a manual A/B test per acceptance criteria. |
| Explicit task state enum with defined allowed-transition graph | Every state-machine reference (LangGraph's explicit graph + checkpointer, generic agent-FSM writeups) treats "the transitions are a named/finite set" as the baseline, not "the LLM decides free-form." Satisfies TASK-01, TRANS-01. | LOW-MEDIUM | `planning → execution → validation → done` plus explicit `paused` per TASK-04; encode the adjacency list directly in code as a lookup table (dict of allowed next-states) — no need for a workflow engine. |
| Guard/validation function that rejects illegal transitions with a clear reason | LangGraph's guard/interrupt model and general FSM literature both treat validation as a chokepoint function, not inline conditionals scattered through business logic; "transitions between states can be determined dynamically... or triggered by rule-based logic" is the standard split. Satisfies TRANS-01/TRANS-02. | LOW | A single `validate_transition(current, requested) -> Ok | Rejected(reason)` function called from one place (wherever the LLM's transition tool call lands) keeps this auditable and testable — critical since TRANS-02 requires the rejection to be *explainable*, not just blocked. |
| LLM-driven task creation via tool call, granular within a chat | OpenAI Agents SDK's "agent-as-tool"/handoff pattern and Letta's task/tool-call pattern both treat "the model decides a new unit of work exists" as a tool call, not an automatic heuristic — matches TASK-02/TASK-03 exactly. | MEDIUM | A `create_task(title, description)` tool that inserts a new task row under the current chat; keep the schema open (e.g. an unused `assignee`/`delegate_to` field) since TASK-03 explicitly wants room for future subagent delegation without redesign. |
| Pause/resume that preserves full task state (not just "stop responding") | LangGraph's checkpointer is the industry-standard mechanism: "saves the exact state of the graph... before an interrupt... loads the snapshot and continues as if milliseconds had passed." This is the bar TASK-04/TRANS-03 need to clear — resuming must not silently drop working memory or force the user to re-explain. | MEDIUM | Persist task state as a durable DB row (state, current step notes, associated working-memory rows) rather than in-process/in-memory state, since the two-process model has no shared in-process state and a `paused` task must survive an Agent-process restart. |
| Task history / transition log visible in UI | LangGraph and generic FSM guidance both emphasize "operator-visible history" and "measurable transitions" as what turns a state machine from an implementation detail into a demonstrable, auditable feature — this is TASK-05. | LOW-MEDIUM | An append-only `TaskTransition` table (task_id, from_state, to_state, reason/actor, timestamp) doubles as both the audit log and the UI history feed. |

### Differentiators (Nice-to-Have, Not Required for This Milestone)

These show up in more mature/production systems (Letta, MemGPT, ChatGPT Memory, LangGraph) but are explicitly beyond what a 5-day course phase needs. Consider only if a phase's acceptance criteria are already met with time to spare.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Automatic memory summarization/consolidation when a tier grows large | MemGPT's "recall→archival" flush avoids memory bloat over long sessions; research (RaMem, "memory rot" pieces) confirms unbounded long-term memory degrades retrieval quality over time. | HIGH | You already have a "sticky" context-compression strategy that summarizes at a token threshold — the same pattern *could* extend to long-term memory, but PROJECT.md's CONCERNS.md flags summarization as already stubbed/incomplete elsewhere in the codebase; don't let this milestone inherit that debt. |
| Memory decay / relevance scoring (forget low-value entries over time) | Reduces "context rot" — stale facts competing with current ones — per multiple 2026 papers reviewed (RaMem, "Your Agent Isn't Losing Memory, It's Rotting"). | HIGH | Requires a scoring/eviction policy; explicitly out of scope for a course milestone whose acceptance bar is "3 layers exist and are inspectable," not "memory self-curates." |
| Cross-session profile *learning* (LLM infers preferences from behavior, not just explicit save) | ChatGPT Memory does this ("chat history... insights ChatGPT gathers from past chats") in addition to explicit "remember this." | HIGH | PROJECT.md's PERS-01..04 only requires a profile the user can view/edit and that gets injected — implicit inference adds a whole classification/consent surface (what if the inference is wrong or feels invasive?) with no requirement asking for it. |
| Vector/semantic search over long-term memory or archival tier | Letta's archival memory is vector-indexed for retrieval at scale; several papers (episodic-semantic memory, dual-trace encoding) build on this. | HIGH | For a single-user-turned-small-multi-user course app with modest data volume, a simple `WHERE user_id = ?` SQL query against a long-term-memory table is sufficient — don't add an embeddings pipeline the requirements never asked for. |
| Full subagent dispatch / actual delegated execution of created tasks | OpenAI Agents SDK handoffs and "agent-as-tools" patterns show this is standard in production multi-agent frameworks. | HIGH | PROJECT.md Out of Scope explicitly excludes this for Week 3 — TASK-03 only asks that the task *schema* leave room for it later (e.g. an optional `delegate_to` field), not that delegation actually run. |
| Interactive checkpoint editing during pause (human edits state before resume) | LangGraph explicitly supports "pause, let the human edit the checkpoint, then resume from the new state" — useful for real HITL workflows. | MEDIUM-HIGH | TASK-04 only requires resuming without re-explaining context, not mid-pause state editing by the user; a differentiator if there's spare time in the phase, not a requirement. |
| Invariant-conflict detection that inspects tool calls/responses for rule violations (INV-04) | Not a memory/task-state feature per se, but sits at a similar "explicit guardrail as a checked function, not just a prompt" level of rigor as the transition guard. | MEDIUM-HIGH | Already scoped as its own requirement family (INV-01..05) in PROJECT.md — noted here only because it shares the "validation layer inspects agent output" pattern with TRANS-01/02; likely warrants its own phase-specific research later given it's a genuinely harder, more judgment-based check (an LLM-based conflict evaluator, not a lookup-table guard). |

### Anti-Features (Commonly Tempting, Explicitly Out of Scope)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| Implicit/automatic memory classification (LLM silently decides to remember something without a visible tool call) | Feels "smarter" and more magical, like ChatGPT's background "chat history" insights layer | Directly contradicts MEM-03 ("explicitly choose ... via tool calls, not implicit/automatic classification") and undermines the inspectability requirement (MEM-04) — a save that never shows up as a discrete, attributable action can't be audited or demoed | Keep every write to working/long-term memory behind an explicit, named tool call with a visible justification string |
| Cross-user memory sharing / a shared knowledge base across users | Reduces duplicate work, feels efficient for a "team" of course graders using the same app | PROJECT.md Out of Scope explicitly excludes this ("Long-term memory sharing across users — per-user by design"); it also breaks the personalization premise (profiles are supposed to differ per user) and raises unnecessary privacy/isolation complexity for a single-user-per-account course app | Scope every memory row and profile by `user_id`; only global project invariants (INV-01) remain intentionally shared |
| General-purpose workflow engine / external state-machine library (e.g., wiring in a Temporal-style orchestrator) | LangGraph and similar frameworks make full graph orchestration look like the "proper" way to do task states | Massive overkill for 4-5 states and a handful of transitions; violates the project's hard constraints (no Docker, no extra infra/message broker) and adds a dependency with its own learning curve for a scope this small | A single in-code transition table + one guard function, backed by your existing SQLite tables, delivers the same guarantees (deterministic, auditable, illegal-transition rejection) at a fraction of the complexity |
| Full RBAC / fine-grained permissions on tasks, memory, or invariants | Feels like the "enterprise-grade" thing to add alongside auth | PROJECT.md Out of Scope explicitly excludes this ("every user is admin; no need for a permissions model beyond authenticated vs not") | Simple `user_id` ownership checks are sufficient; skip roles/policies entirely |
| Real subagent execution wired to created tasks | Natural next step once TASK-03 exists ("the LLM can create a task") | PROJECT.md Out of Scope explicitly defers this ("Actual subagent execution of tasks... out of scope for Week 3"); building it now risks scope creep that jeopardizes the 5-day/phase cadence | Leave an open, unused extension point on the task schema (e.g. nullable `delegate_to`/`subagent_type` column) so future work can wire it in without a schema migration |
| Memory/task UI as a separate SPA or with a JS framework/bundler | Richer state management (React/Vue) is the default reach for "live-updating panels" | Violates the hard frontend constraint: vanilla JS + CDN libraries only, no bundler, no npm packages | Reuse the existing pattern already in this codebase (WebSocket `done` message + REST polling driving a plain-JS panel, as done for context/token stats) for the memory and task panels |

## Feature Dependencies

```
Auth (user_id scoping)
    └──requires (prerequisite for all of the below)──> PERS-01 (per-user profile)
    └──requires──────────────────────────────────────> MEM-01/02 (user-scoped memory tables)
    └──requires──────────────────────────────────────> TASK-01/02 (user-scoped tasks)

MEM-01 (3 memory layers defined)
    └──requires──> MEM-02 (dedicated SQLite tables per layer)
                       └──requires──> MEM-03 (LLM tool calls choose layer + write)
                                          └──requires──> MEM-04 (inspect what landed where)
                                                             └──requires──> MEM-05 (UI shows layer contents)

PERS-01 (profile schema exists)
    └──requires──> PERS-02 (profile injected into every request)
                       └──enables───> PERS-04 (observable behavioral difference)
    └──requires──> PERS-03 (UI to view/edit profile)

TASK-01 (state enum + transition graph defined)
    └──requires (prerequisite)──> TRANS-01 (explicit allowed-transition set; the graph must exist before it can be validated against)
                                       └──requires──> TRANS-02 (illegal transition → clear rejection, not silent success/crash)
    └──requires──> TASK-02 (tasks granular within a chat, not 1:1 with chat)
                       └──requires──> TASK-03 (LLM creates new task via tool call)
    └──requires──> TASK-04 (pause/resume without re-explaining context)
                       └──requires──> TRANS-03 (resume must not violate the transition graph — depends on TRANS-01 existing first)
    └──requires──> TASK-05 (UI shows current task, state, and transition history)

MEM-01/02 (working memory layer) ──enhances──> TASK-04 (pause/resume relies on working memory surviving the pause, not living only in-process)

INV-01..03 (global/per-chat invariants + injection) ──parallels (same "guardrail" pattern as)──> TRANS-01/02 (explicit rule set + rejection), but is a separate requirement family — not a hard dependency in either direction

Memory summarization/decay (differentiator) ──conflicts with──> "explicit, inspectable, LLM-chosen" premise of MEM-03/04 if done automatically/implicitly — if ever added, must itself go through an explicit tool call to stay consistent with this milestone's design principle
```

### Dependency Notes

- **Auth is the true root dependency.** Every memory row, profile, and task must carry `user_id` from day one — PROJECT.md already recognizes this by scoping Auth as its own foundation phase/branch before Day 11. Retrofitting `user_id` onto memory/task tables after they exist is far more expensive than building them scoped from the start.
- **TRANS-01 cannot precede TASK-01.** You cannot define "the set of allowed transitions" without first defining the state enum those transitions connect — this is a strict prerequisite, not just a nice ordering. If a milestone phase ever tried to build illegal-transition rejection before the state machine itself, it would have nothing to validate against.
- **TRANS-03 (resume correctness) depends on TRANS-01 (transition graph) already existing**, and on TASK-04 (pause preserves state) being correctly implemented — pause/resume is not really "done" as its own feature until the guard also verifies a resume can't skip states (e.g. resuming a paused `planning` task straight into `done`).
- **MEM-03 (LLM chooses save target via tool call) depends on MEM-01/02 existing** — the tool call needs a destination table/layer to write into; building the tool call before the storage schema would leave it with nowhere durable to persist.
- **PERS-02 (injection into every request) enables PERS-04 (observable difference), not the reverse** — PERS-04 is effectively a test/acceptance criterion for PERS-02, not a separate implementation task; don't schedule it as independent roadmap work.
- **MEM-01 (working memory) and TASK-04 (pause/resume) reinforce each other**: pausing a task and resuming "without re-explaining context" implicitly requires that whatever the task's working memory held survives the pause — if working memory lives only in-process (violating the two-process model's "no shared in-process state" constraint) or is cleared on pause, TASK-04 silently fails even if the state machine itself is correct. Treat working-memory persistence as a shared prerequisite for both MEM-01 and TASK-04, not two independent features.
- **INV-01..05 is a structurally parallel but independent requirement family.** It shares the "guardrail that inspects agent output and can reject/flag" shape with TRANS-01/02, but nothing in TASK/TRANS strictly depends on invariants existing first (or vice versa) — they can be built in either order, though building the transition guard first gives you a smaller, already-tested pattern (lookup-table validation) to generalize when building the harder, judgment-based invariant-conflict check (INV-04, which likely needs its own LLM call, not just a lookup).

## MVP Definition

### Launch With (v1) — required for Week 3 acceptance criteria

- [ ] Auth foundation with `user_id` scoping (prerequisite for everything else)
- [ ] MEM-01/02: three memory tables (short-term = existing message tree; working; long-term), all user/chat-scoped
- [ ] MEM-03: explicit tool-call-driven save (at minimum `save_working_memory` / `save_long_term_memory`)
- [ ] MEM-04/05: inspection endpoint + minimal UI panel per memory layer
- [ ] PERS-01..04: profile schema, request-time injection, edit UI, and a verifiable behavioral difference
- [ ] TASK-01/02: state enum (`planning → execution → validation → done` + `paused`) and per-chat, multi-task granularity
- [ ] TASK-03: LLM-driven task creation via tool call, with an open/unused delegation field
- [ ] TRANS-01/02: explicit transition table + guard function producing clear rejection messages
- [ ] TASK-04/TRANS-03: durable (DB-persisted, not in-process) pause/resume that respects the transition graph
- [ ] TASK-05: UI showing current task, state, and transition history

### Add After Validation (v1.x) — only if a phase finishes early

- [ ] Lightweight memory-entry pruning/archival when a tier grows large — trigger: a memory table actually becomes hard to read in the inspection UI during testing, not preemptively
- [ ] Interactive pause-time editing of task/working-memory state before resume — trigger: user explicitly wants to correct course mid-task, not just resume it

### Future Consideration (v2+) — explicitly deferred, do not build now

- [ ] Automatic/implicit memory summarization or decay scoring — defer until "explicit, LLM-chosen, inspectable" memory is proven and stable; revisit only alongside the existing (already-flagged-as-stubbed) summarization work elsewhere in the codebase
- [ ] Cross-session implicit profile inference (learning preferences from behavior rather than explicit save) — defer indefinitely; conflicts with the explicit-choice design principle unless it too goes through a visible tool call
- [ ] Real subagent dispatch executing created tasks — defer per PROJECT.md Out of Scope; only the schema hook (from TASK-03) should exist now
- [ ] Vector/semantic search over long-term memory — defer until data volume or retrieval-quality problems actually appear; plain SQL lookups are sufficient at this scale

## Feature Prioritization Matrix

| Feature | User/Grader Value | Implementation Cost | Priority |
|---------|--------------------|----------------------|----------|
| Auth + user_id scoping | HIGH (blocks everything else) | MEDIUM | P1 |
| 3-layer memory tables + tool-call writes (MEM-01..03) | HIGH | MEDIUM | P1 |
| Memory inspection + UI (MEM-04/05) | HIGH (demoability) | LOW-MEDIUM | P1 |
| Per-user profile + injection (PERS-01/02) | HIGH | LOW | P1 |
| Profile edit UI + observable diff (PERS-03/04) | HIGH (demoability) | LOW | P1 |
| Task state enum + transition table (TASK-01, TRANS-01) | HIGH | LOW-MEDIUM | P1 |
| Task granularity + LLM-driven creation (TASK-02/03) | HIGH | MEDIUM | P1 |
| Illegal-transition rejection with explanation (TRANS-02) | HIGH (this is the acceptance-tested behavior) | LOW | P1 |
| Durable pause/resume (TASK-04, TRANS-03) | HIGH | MEDIUM | P1 |
| Task history UI (TASK-05) | MEDIUM-HIGH (demoability) | LOW-MEDIUM | P1 |
| Memory summarization/decay | LOW at this scope | HIGH | P3 |
| Cross-session implicit profile learning | LOW at this scope, conflicts with design principle | HIGH | P3 |
| Vector search over archival memory | LOW at this scale | HIGH | P3 |
| Real subagent dispatch | Explicitly out of scope | HIGH | P3 (do not build) |

**Priority key:**
- P1: Must have — directly maps to an Active requirement in PROJECT.md
- P2: Should have, add when possible (none identified beyond the "Add After Validation" list — this milestone's scope is already tightly bounded by explicit requirements)
- P3: Nice to have / explicitly deferred per PROJECT.md Out of Scope or this research's anti-features analysis

## Reference System Comparison

| Feature | Anthropic Memory Tool | Letta / MemGPT | LangGraph | ChatGPT Memory | This Project's Approach |
|---------|------------------------|------------------|-----------|------------------|---------------------------|
| Memory write trigger | Explicit client-side tool call (`memory_20250818`) | Explicit tool calls (`core_memory_append`, `archival_memory_insert`) | N/A (not a memory-specialized framework) | Mostly implicit + some explicit "remember this" | Explicit tool call only (matches Anthropic + Letta, not ChatGPT) — required by MEM-03 |
| Memory tiers | Flat file-based store (app defines structure) | 3 tiers: core (in-context), recall (session), archival (long-term vector) | N/A | 2 tiers: saved memories, chat-history insights | 3 tiers: short-term (message tree, existing), working (per-task), long-term (per-user), matching MEM-01 |
| Storage backend | Developer-owned (your infra) | Its own DB + vector store | Checkpointer (Postgres/SQLite/Mongo) | OpenAI-managed | SQLite via existing SQLModel/`shared/database.py`, per MEM-02 |
| State machine / pause-resume | N/A | N/A | Explicit graph + checkpointer, `interrupt()`/`Command(resume=...)` | N/A | In-code transition table + guard function + DB-persisted task rows (no external orchestrator, per hard constraints) |
| Task delegation | N/A | Agent-defined tool calls | Multi-agent graphs, subgraphs | N/A | Schema hook only (open `delegate_to` field); no execution, per Out of Scope |

## Sources

- [Managing context on the Claude Developer Platform (Anthropic)](https://www.anthropic.com/news/context-management) — HIGH confidence, official source
- [Memory tool - Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) — HIGH confidence, official docs; corroborated by the bundled `claude-api` skill's own memory-tool notes (`memory_20250818` type, client-side/developer-owned storage)
- [claude-cookbooks memory_cookbook.ipynb (anthropics/claude-cookbooks)](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/memory_cookbook.ipynb) — HIGH confidence, official reference implementation
- [Agent Memory: How to Build Agents That Learn and Remember (Letta)](https://www.letta.com/blog/agent-memory/) — MEDIUM-HIGH confidence, vendor blog but consistent with the well-established MemGPT paper's core/recall/archival model
- [Mem0 vs Letta (MemGPT) comparison (Vectorize)](https://vectorize.io/articles/mem0-vs-letta) — MEDIUM confidence, third-party comparison, used to corroborate the tiered-memory description
- [Human-in-the-loop - Docs by LangChain](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) — HIGH confidence, official docs
- [Architecting Human-in-the-Loop Agents: Interrupts, Persistence, and State Management in LangGraph (Medium)](https://medium.com/data-science-collective/architecting-human-in-the-loop-agents-interrupts-persistence-and-state-management-in-langgraph-fa36c9663d6f) — MEDIUM confidence, third-party but consistent with official LangGraph docs on checkpointer-based pause/resume
- [Memory and new controls for ChatGPT (OpenAI)](https://openai.com/index/memory-and-new-controls-for-chatgpt/) — HIGH confidence, official source for the "saved memories vs. chat history" personalization pattern
- [Your AI Agents Need Finite State Machines (FSMs) (DEV Community)](https://dev.to/remojansen/your-ai-agents-need-finite-state-machines-fsms-2i9j) — MEDIUM confidence, community source; used only for the general "explicit FSM with guarded transitions, operator-visible history" framing, which is well-established software-engineering practice independent of this specific article
- [Agent orchestration - OpenAI Agents SDK](https://openai.github.io/openai-agents-python/multi_agent/) and [Handoffs - OpenAI Agents SDK](https://openai.github.io/openai-agents-python/handoffs/) — HIGH confidence, official docs; used for the task-delegation/subagent pattern referenced by TASK-03's "structure left open for future delegation"
- [Your Agent Isn't Losing Memory. It's Rotting. (DEV Community)](https://dev.to/danilgaleev/your-agent-isnt-losing-memory-its-rotting-2edd) and related 2026 arXiv papers surfaced in search (RaMem, dual-trace encoding, episodic-semantic memory) — LOW-MEDIUM confidence (recent, some non-peer-reviewed preprints), used only to justify why summarization/decay is flagged as a differentiator/deferred item, not a table-stakes requirement
- `C:\Projects\AiAdventAgentV2\.planning\PROJECT.md` — primary source of truth for exact requirement IDs (MEM-01..05, PERS-01..04, TASK-01..05, TRANS-01..03) and Out of Scope boundaries

---
*Feature research for: LLM agent memory, personalization, and task state machines*
*Researched: 2026-09-19*
