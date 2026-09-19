# Project Research Summary

**Project:** AiAdventAgentV2 -- Week 3: Agent Memory & Task State
**Domain:** Retrofitting multi-user auth + explicit agent memory (MemGPT-style tiered storage) + a task FSM with invariant enforcement onto an existing two-process FastAPI/SQLModel/SQLite chat app
**Researched:** 2026-09-19
**Confidence:** MEDIUM-HIGH

## Executive Summary

This is a coursework retrofit, not a greenfield build: the app already has a working two-process (UI:8000 / Agent:8001) chat system with a message-tree data model, global/per-chat Settings fallback, and dual LLM backends (DeepSeek cloud + LM Studio local). Week 3 adds four layers on top -- user authentication, three-tier explicit memory (short-term/working/long-term), per-user personalization, and a formal task state machine with invariant enforcement -- all of which the research is unanimous should live entirely inside the existing Agent process, extending established codebase patterns (new SQLModel tables FK'd through `user_id`, the `Settings` global/per-chat NULL-fallback pattern reused for Invariants, `context_engine.py` as the single context-assembly chokepoint) rather than introducing new frameworks or a second architecture.

The recommended approach is deliberately minimal: `pwdlib[argon2]` for password hashing (not the unmaintained `passlib`), a hand-rolled dict-based task transition table (not a workflow-engine library), two dedicated memory tables written exclusively through a new LLM tool-call dispatcher (`agent/tools.py`) built once in the Memory phase and reused by every later phase, and -- critically -- auth must land first as its own foundation phase, since every new table in every subsequent phase needs `user_id` scoping from creation, not retrofitted later. One open question the research surfaces but does not resolve: STACK.md recommends a server-side `Session` SQLModel table for revocable sessions, while ARCHITECTURE.md recommends Starlette's built-in `SessionMiddleware` (signed cookie, no server-side revocation) for zero-dependency simplicity -- this must be explicitly decided during Auth phase planning, not left ambiguous (see Gaps below).

The dominant risk category is not "will this work" but "will the explicit/inspectable guarantee actually hold" -- the codebase already has an implicit, debounced fact-extraction pattern (`extract_and_update_facts`) that is structurally tempting to relabel as the new "explicit" memory feature, and a debounced-background-task precedent that will silently drop concurrent tool-call writes if copied verbatim for memory/task mutations. Both are documented, well-understood pitfalls with concrete prevention strategies (synchronous, per-chat-locked, tool-call-only writes), not open research questions -- the roadmap should treat them as binding architectural constraints from day one rather than something to catch in review.

## Key Findings

### Recommended Stack

The stack additions are deliberately small: one new pinned dependency (`pwdlib[argon2]==0.3.1`, replacing the unmaintained/broken `passlib`), and everything else built on already-present dependencies (stdlib `secrets`/`hashlib` for tokens, SQLModel `Column(JSON)` for memory payloads, Pydantic `model_json_schema()` for tool schemas). No workflow engine, no vector DB, no LLM orchestration framework (LangChain/instructor/pydantic-ai) is warranted at this scope -- all were explicitly evaluated and rejected as overkill relative to the actual requirements.

**Core technologies:**
- `pwdlib[argon2]` 0.3.1 -- password hashing for Auth-01/02 -- actively maintained passlib successor; `argon2-cffi` backend follows OWASP's 2026 minimum profile (`m=19456, t=2, p=1`)
- Session mechanism (server-side `Session` table **or** Starlette `SessionMiddleware`, unresolved -- see Gaps) -- Auth-03's REST+WS session -- must be decided before Auth phase planning begins
- SQLModel `Column(JSON)` on two new dedicated tables (`WorkingMemoryItem`, `LongTermMemoryItem`) -- MEM-01/02 storage -- mirrors the existing `Settings.facts_json` JSON-column convention already in the codebase
- Pydantic `model_json_schema()` + hand-rolled `tools=[...]` payload extension to `agent/llm_client.py` -- MEM-03/TASK-03 tool-call schemas -- both DeepSeek and LM Studio confirm OpenAI-compatible tool-calling support (MEDIUM confidence, docs-verified not hands-on tested)
- Hand-rolled `dict[str, set[str]]` transition table + guard function -- TASK-01/TRANS-01/02 -- 4 states is too small to justify `python-statemachine`; revisit only if pause/resume needs history pseudo-states later

### Expected Features

FEATURES.md maps every Active requirement in PROJECT.md (Auth-01..04, MEM-01..05, PERS-01..04, TASK-01..05, TRANS-01..03) directly onto patterns already proven in production agent-memory systems (Anthropic's memory tool, Letta/MemGPT, LangGraph, ChatGPT Memory) -- there is no feature gap between "what graders will expect" and "what's already scoped in PROJECT.md." The single biggest anti-feature risk is implicit/automatic memory classification, which is both the easiest thing to accidentally build (given the existing `extract_and_update_facts` precedent) and explicitly disqualifying per MEM-03.

**Must have (table stakes):**
- Explicit, tool-call-gated memory writes (never implicit/inferred) -- MEM-03
- Three separately-queryable memory tiers, working/long-term as dedicated tables -- MEM-01/02
- Inspection endpoint + UI panel per memory layer -- MEM-04/05
- Per-user profile injected into every request with observable behavioral difference -- PERS-01..04
- Explicit state enum + transition table + guard function with clear rejection -- TASK-01, TRANS-01/02
- LLM-driven task creation via tool call, with an open/unused delegation field for future subagent work -- TASK-03
- Durable (DB-persisted, not in-process) pause/resume -- TASK-04/TRANS-03
- Task history/transition log visible in UI -- TASK-05

**Should have (competitive, only if time allows):**
- Lightweight memory pruning once a tier is hard to read in the UI (reactive, not preemptive)
- Interactive pause-time editing of task state before resume

**Defer (v2+, explicitly out of scope per PROJECT.md):**
- Automatic memory summarization/decay scoring
- Cross-session implicit profile inference
- Real subagent dispatch executing created tasks (schema hook only, no execution)
- Vector/semantic search over long-term memory
- Cross-user memory sharing, fine-grained RBAC

### Architecture Approach

All new complexity lives inside the Agent process (port 8001) -- the UI process is untouched. The design adds five new modules (`agent/auth.py`, `agent/tools.py`, `agent/memory.py`, `agent/tasks.py`, `agent/invariants.py`), each mapping 1:1 to a bounded concern, with `agent/tools.py` as the single new *kind* of component: a tool-call dispatcher built once during the Memory phase and reused unchanged by Personalization, Tasks, and Invariants. `context_engine.py` is extended (not forked) to be the sole place that assembles profile/invariant/task-scratchpad context and attaches tool schemas to the outbound LLM payload -- this preserves the existing 75%-of-context_length overflow trigger by keeping token accounting centralized.

**Major components:**
1. `agent/auth.py` -- password hashing, login/register/logout, `get_current_user` dependency (REST) + WS pre-accept session check
2. `agent/tools.py` -- OpenAI-compatible tool schema registry + sequential dispatch loop (the reusable chokepoint for all agent-initiated writes)
3. `agent/memory.py` / `agent/tasks.py` / `agent/invariants.py` -- CRUD + domain logic for each new table group, all written to exclusively through the dispatcher, never from `context_engine.py` (read-only) or background tasks
4. `shared/models.py` (extended) -- `User`, `WorkingMemory`, `LongTermMemory`, `Task`, `TaskTransition`, `Invariant`, `InvariantConflict`, all FK'd to `User` directly or transitively via `Chat`

### Critical Pitfalls

1. **WS auth checked inconsistently from REST auth** -- `Depends()` doesn't transparently secure WebSocket routes; must add a manual pre-accept session check in `agent/ws.py::ws_chat`, mirroring the existing `_validate_origin()` pre-accept pattern, or an unauthenticated/cross-user WS connection can stream chat tokens even with REST "protected."
2. **Wildcard CORS (`allow_origins=["*"]`) actively breaks credentialed cross-port cookie auth** -- browsers reject `Access-Control-Allow-Origin: *` combined with `credentials: "include"`; this must become an explicit origin allowlist as a hard requirement of the Auth phase, not a deferred nice-to-have.
3. **"Explicit" memory quietly degrades into the existing implicit `extract_and_update_facts` pattern** -- the codebase already has a debounced, automatic fact-extraction pipeline that is structurally tempting to relabel as MEM-03; build memory as a genuinely separate tool-call-only code path and log every write with its triggering `tool_call.id`.
4. **Tool-call writes racing the existing debounced-background-task pattern** -- `extract_and_update_facts` already runs outside the per-chat lock and is documented as lossy under rapid messages; memory/task tool-call writes must execute synchronously inside the same per-chat lock and DB transaction as the triggering message, never fire-and-forget.
5. **Multiple tool calls in one LLM turn executed out of order or concurrently** -- never `asyncio.gather` tool execution; execute strictly sequentially in the order returned, since a later call (e.g. `transition_task_state`) may depend on an earlier call's generated ID (e.g. `create_task`).
6. **FSM soft-enforcement window (Day13 tasks land before Day15 hard transition checks)** -- illegal transitions will succeed during Day13-14 by course design; store full transition history (`from_state`/`to_state`) from Day13 onward so Day15's hard check has something to validate against retroactively.

## Implications for Roadmap

Based on combined research, the suggested phase structure closely tracks PROJECT.md's own Day11-15 sequencing, with Auth pulled out as an explicit prerequisite phase (already anticipated in PROJECT.md's Key Decisions).

### Phase 1: Auth Foundation
**Rationale:** Every subsequent table (Memory, Task, Invariant) needs `user_id` scoping from creation -- retrofitting it later means re-touching every table's FK design and every query's fallback logic. ARCHITECTURE.md and PITFALLS.md both independently converge on "Auth first, no exceptions."
**Delivers:** `User` table, login/register/logout REST endpoints + login UI, session mechanism (decide `Session` table vs `SessionMiddleware` -- see Gaps), `get_current_user` REST dependency + WS pre-accept check, CORS fixed to explicit origin allowlist, `Chat.user_id`/`Settings.user_id` migration with backfill to a bootstrap admin user, centralized (not triply-duplicated) global/per-chat settings fallback resolver.
**Addresses:** Auth-01..04
**Avoids:** Pitfalls 1-4 (WS auth inconsistency, CORS wildcard breaking credentialed requests, cookie host/port scoping mistakes, settings-fallback duplication leaking data across users)

### Phase 2: Memory (Day 11)
**Rationale:** MEM-03 requires the tool-call dispatcher, and every later phase (Personalization, Tasks, Invariants) reuses it rather than rebuilding it -- building it generically here is the highest-leverage architectural decision in the milestone, per ARCHITECTURE.md's explicit build-order analysis.
**Delivers:** `WorkingMemoryItem`/`LongTermMemoryItem` tables, `agent/memory.py`, `agent/tools.py` (the reusable dispatcher), memory inspection endpoint + UI panel.
**Addresses:** MEM-01..05
**Avoids:** Pitfalls 5-9 (implicit-classification relabeling, hallucinated-compliance/narrated-but-not-saved writes, background-task write races, out-of-order parallel tool calls, orphaned writes on turn rollback/context overflow)

### Phase 3: Personalization (Day 12)
**Rationale:** Depends only on Memory's storage + dispatcher (profile modeled as `LongTermMemory` rows with `category="profile"`), independent of Tasks/Invariants -- can proceed immediately after Phase 2 without waiting on the state machine.
**Delivers:** Profile schema/injection point in `context_engine.py::build_llm_context()`, profile edit UI, verified observable behavioral difference across profiles.
**Addresses:** PERS-01..04
**Uses:** Memory phase's dispatcher and storage pattern; extends `context_engine.py` in place (not a second context-assembly path, per Anti-Pattern 4)

### Phase 4: Task State Machine (Day 13)
**Rationale:** Depends on Memory's dispatcher (for `create_task`/`transition_task` tool registration) but not on Invariants; TRANS-01 cannot precede this phase since the state enum must exist before a transition graph can validate against it.
**Delivers:** `Task`/`TaskTransition` tables, `agent/tasks.py` (dict-based transition table + guard function), task panel UI, `paused_at` field modeled orthogonally to the state enum (not a 5th state).
**Addresses:** TASK-01..05 (soft/prompt-level transition guidance only -- hard enforcement is deferred to Phase 6)
**Avoids:** Pitfall 8 (out-of-order parallel tool calls), Pitfall 10's schema half (store full transition history now even though hard checks land later)

### Phase 5: Invariants (Day 14)
**Rationale:** Depends on Memory's dispatcher (for a potential `add_invariant` tool) and benefits from, but is not hard-blocked by, Tasks existing.
**Delivers:** `Invariant`/`InvariantConflict` tables, `agent/invariants.py` reusing the Settings global/per-chat NULL-fallback pattern verbatim, injection with an explicit precedence rule for global-vs-per-chat conflicts, post-response async conflict check (same debounce shape as existing fact extraction, but read-only/advisory -- never a write path).
**Addresses:** INV-01..05
**Avoids:** Moderate Pitfalls 1-2 (prose-based violations missed by tool-call-only checks; undefined global/per-chat precedence causing non-deterministic behavior) -- both require an explicit, documented scope decision before implementation, not after a demo reveals the gap

### Phase 6: Controlled Transitions (Day 15)
**Rationale:** Hardening/integration phase over Phases 4-5 rather than new components -- the `is_valid_transition()` gate from Phase 4 gets its explainable-rejection WS error path, and invariant conflict checks get wired into transition attempts specifically.
**Delivers:** Hard TRANS-01/02/03 enforcement, tested illegal-transition rejection (retroactively validated against Phase 4's transition history), verified pause/resume correctness end-to-end using working memory (not compressed chat history) as source of truth.
**Addresses:** TRANS-01..03
**Avoids:** Pitfall 10 (soft-enforcement gap reconciliation), Moderate Pitfall 3 (resume must read working memory directly, not rely on whatever the active compression strategy happens to still contain)

### Phase Ordering Rationale

- **Auth is a strict dependency root**, confirmed independently by all three of STACK.md, ARCHITECTURE.md, and PITFALLS.md -- not just a convention choice but a structural requirement, since every new table's FK design depends on `User` existing first.
- **The tool-call dispatcher (`agent/tools.py`) is built once, in Memory, and never rebuilt** -- this is the single highest-leverage sequencing decision; every later phase only adds tool schemas to an existing registry.
- **TRANS-01 cannot precede TASK-01** (need the state enum before a transition graph can reference it), and **TRANS-03 cannot be meaningfully hardened before TASK-04's pause/resume persistence exists** -- this is why Controlled Transitions is sequenced last, as a hardening pass over Tasks rather than a parallel-buildable phase.
- **Personalization and Invariants are each independently orderable relative to Tasks** (both only hard-depend on Memory's dispatcher) -- the Day11-15 sequence in PROJECT.md is a reasonable default, but if schedule pressure hits, Personalization could be reordered before or after Tasks without rework, per ARCHITECTURE.md's dependency analysis.

### Research Flags

Needs research during planning:
- **Phase 1 (Auth):** session-mechanism choice (`Session` table vs `SessionMiddleware`) must be resolved explicitly -- STACK.md and ARCHITECTURE.md disagree; recommend `/bm:plan-phase --research-phase 1` or an explicit ADR-style decision before implementation starts.
- **Phase 2 (Memory):** LM Studio tool-calling reliability is model-dependent and untested in this session (MEDIUM confidence, docs-only verification) -- verify the actual configured local model emits well-formed `tool_calls` before committing a graded demo to it; default to DeepSeek if unreliable.
- **Phase 6 (Controlled Transitions):** scope decision for INV-04 (tool-call-args-only vs. also-scanning-prose conflict detection) needs to be made explicitly and documented, not discovered during implementation.

Phases with standard, well-documented patterns (skip deep research):
- **Phase 3 (Personalization):** directly mirrors the existing `_resolve_settings` injection pattern; low implementation risk.
- **Phase 4 (Task State Machine):** dict-based transition table + guard function is a standard, well-established DB-design pattern (whitelist + append-only history table), confirmed via general FSM/DB-design research, not a novel design.
- **Phase 5 (Invariants):** reuses the Settings global/per-chat NULL-fallback pattern verbatim -- an established in-codebase pattern, not new territory.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH (auth/hashing, OWASP-verified); MEDIUM (memory JSON-column pattern, community-verified); HIGH (state machine choice, driven by existing codebase convention) | pwdlib/argon2-cffi verified via PyPI + OWASP Cheat Sheet; tool-calling support verified via DeepSeek/LM Studio official docs but not hands-on tested |
| Features | MEDIUM-HIGH | Verified against multiple independent production systems (Anthropic memory tool, Letta/MemGPT, LangGraph, ChatGPT Memory); no Context7 library applies directly since this is an architectural pattern question, not an SDK API question |
| Architecture | MEDIUM-HIGH | Component boundaries and DB design are HIGH confidence (direct extensions of patterns already proven in this codebase, verified by direct source inspection); tool-calling support is MEDIUM (docs-verified, not implemented/tested); FSM/invariant runtime behavior is a design recommendation, not an industry-standardized pattern |
| Pitfalls | MEDIUM-HIGH | WS-auth and CORS-credentials pitfalls are HIGH confidence (standards-based: Fetch/CORS spec, RFC 6265); memory-poisoning and parallel-tool-call-race pitfalls are MEDIUM confidence (recent arXiv preprints, community forum reports); several pitfalls are directly grounded in this codebase's own already-documented `CONCERNS.md` issues (settings-fallback duplication, debounced-extraction message loss, CORS wildcard, unbounded `facts_json`) -- HIGH confidence where cross-referenced against existing docs |

**Overall confidence:** MEDIUM-HIGH -- the architectural and pitfall research is unusually strong because it's grounded directly in this specific codebase's existing patterns and documented tech debt (`CONCERNS.md`), not generic best practices. The main uncertainty is forward-looking: actual tool-calling reliability on the configured LM Studio model, and one unresolved session-mechanism disagreement between two research files.

### Gaps to Address

- **Session mechanism conflict (STACK.md vs ARCHITECTURE.md):** STACK.md recommends a server-side `Session` SQLModel table for revocability; ARCHITECTURE.md recommends Starlette's built-in `SessionMiddleware` for zero-new-dependency simplicity (explicitly accepting "no server-side revocation" as a documented trade-off). Resolve explicitly in Phase 1 planning -- both are legitimate, differ on revocability-vs-simplicity, and the roadmap should not proceed with both assumed simultaneously.
- **LM Studio tool-calling reliability on the actual configured model:** unverified in this research session (docs confirm API shape, not empirical reliability). Handle during Phase 2 planning/execution: test the configured local model's tool-call emission before committing a demo to it; fall back to DeepSeek as default if unreliable.
- **Python version floor conflict:** `.planning/codebase/STACK.md` documents "Python 3.8+" but `argon2-cffi` 25.1.0 (pulled transitively via `pwdlib[argon2]`) requires 3.9+. Confirm the project's actual floor in `run.py`/CI during Phase 1; either bump the documented floor or pin `argon2-cffi<25`.
- **INV-04 conflict-check scope (tool-call-args-only vs. prose-scanning too):** not resolved by research -- this is a project-specific scope decision to make explicitly before Phase 5/6 implementation, per Moderate Pitfall 1.
- **Global-vs-per-chat invariant precedence rule:** research confirms this must be explicit (documented pitfall) but does not prescribe which precedence direction is correct for this project -- decide during Phase 5 planning and state it in the injected system prompt itself.

## Sources

### Primary (HIGH confidence)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) -- Argon2id minimum profile
- [pwdlib PyPI](https://pypi.org/project/pwdlib), [argon2-cffi PyPI](https://pypi.org/project/argon2-cffi/) -- versions, Python compatibility
- [DeepSeek API -- Function Calling / Tool Calls guides](https://api-docs.deepseek.com/guides/function_calling) -- official docs
- [LM Studio -- Tool Use docs](https://lmstudio.ai/docs/developer/openai-compat/tools) -- official docs
- [Memory tool - Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool), [claude-cookbooks memory_cookbook.ipynb](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/memory_cookbook.ipynb) -- official reference implementation for explicit tool-call memory
- [Human-in-the-loop - Docs by LangChain](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) -- checkpointer-based pause/resume
- [Memory and new controls for ChatGPT (OpenAI)](https://openai.com/index/memory-and-new-controls-for-chatgpt/) -- personalization/profile pattern
- MDN/Fetch spec behavior for `Access-Control-Allow-Origin: *` with credentials; RFC 6265 cookie scoping -- standards-based
- Direct codebase inspection: `agent/main.py`, `agent/ws.py`, `agent/state.py`, `ui/static/app.js`, `shared/models.py`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md`, `.planning/PROJECT.md`

### Secondary (MEDIUM confidence)
- [Introducing pwdlib -- Francois Voron](https://www.fvoron.com/blog/introducing-pwdlib-a-modern-password-hash-helper-for-python/); [fastapi/fastapi Discussion #11773](https://github.com/fastapi/fastapi/discussions/11773) -- passlib staleness
- [Agent Memory: How to Build Agents That Learn and Remember (Letta)](https://www.letta.com/blog/agent-memory/) -- tiered memory model, vendor blog but consistent with MemGPT paper
- [Design Patterns for Long-Term Memory in LLM-Powered Architectures -- Serokell](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures); [Redis long-term memory architectures](https://redis.io/blog/long-term-memory-architectures-ai-agents/)
- [How I Solved WebSocket Authentication in FastAPI -- DEV Community](https://dev.to/hamurda/how-i-solved-websocket-authentication-in-fastapi-and-why-depends-wasnt-enough-1b68) -- WS auth gap corroboration
- [Race Condition with Parallel Tool Calls (LangChain Forum)](https://forum.langchain.com/t/race-condition-with-parallel-tool-calls-tool-responses-out-of-order/1112); [Parallel Tool Calls in LLM Agents: The Coupling Test](https://tianpan.co/blog/2026/04/10/parallel-tool-calls-hidden-coupling) -- sequential-execution pitfall corroboration

### Tertiary (LOW-MEDIUM confidence)
- [Memory-Induced Tool-Drift in LLM Agents (arXiv 2605.24941)](https://arxiv.org/pdf/2605.24941); [MemoryGraft (arXiv 2512.16962)](https://arxiv.org/pdf/2512.16962); [From Untrusted Input to Trusted Memory (arXiv 2606.04329)](https://arxiv.org/pdf/2606.04329) -- memory-poisoning attack patterns, recent preprints, used to justify design-time provenance considerations only
- [Your Agent Isn't Losing Memory. It's Rotting. (DEV Community)](https://dev.to/danilgaleev/your-agent-isnt-losing-memory-its-rotting-2edd) -- used only to justify deferring summarization/decay as a v2+ concern

---
*Research completed: 2026-09-19*
*Ready for roadmap: yes*
