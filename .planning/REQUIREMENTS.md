# Requirements: AiAdventAgentV2 — Week 3: Agent Memory & Task State

**Defined:** 2026-09-19
**Core Value:** The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where.

## v1 Requirements

### Auth

- [x] **AUTH-01**: User can log in with username/password (simple auth, no external identity provider)
- [x] **AUTH-02**: Every user has the same "admin" role and can create additional user accounts
- [x] **AUTH-03**: Session is maintained via an HTTP-only session cookie, valid for both REST and WebSocket
- [x] **AUTH-04**: All existing chats/settings/memory become scoped to the owning user (`user_id`)

### Memory

- [ ] **MEM-01**: Agent has 3 explicitly separated memory layers — short-term (current dialog), working (current task data), long-term (profile, decisions, knowledge)
- [ ] **MEM-02**: Long-term and working memory are stored in dedicated SQLite tables, not folded into the message tree
- [ ] **MEM-03**: The LLM explicitly chooses what to save and to which layer, via tool calls (not implicit/automatic classification)
- [ ] **MEM-04**: It's possible to inspect what data landed in each memory layer for a given chat
- [ ] **MEM-05**: UI shows the current contents of each memory layer

### Personalization

- [x] **PERS-01**: Each user has a profile with preferences (style, format, constraints)
- [x] **PERS-02**: The user's profile is attached to every request (injected into context/system prompt)
- [x] **PERS-03**: UI lets the user view/edit their profile and preferences
- [x] **PERS-04**: Responses observably differ across different profiles/preferences

### Task State

- [ ] **TASK-01**: Task state is modeled as a finite state machine: `planning → execution → validation → done`
- [ ] **TASK-02**: Tasks are granular within a chat (a chat can contain multiple tasks), not one task per chat
- [ ] **TASK-03**: The LLM can autonomously create a new task when it recognizes a new unit of work (tool call), with the structure left open for future delegation to subagents
- [ ] **TASK-04**: A task can be paused at any state and resumed later without re-explaining context
- [ ] **TASK-05**: UI shows the current task, its state, and history of task state changes

### Invariants

- [ ] **INV-01**: Global invariants (architecture/stack/business rules for the whole app) are defined and stored separately from the dialog
- [ ] **INV-02**: Per-chat invariants can be added by the user, layered on top of global invariants
- [ ] **INV-03**: Invariants are injected into the agent's reasoning context (prompt-injection) on every relevant request
- [ ] **INV-04**: A dedicated check inspects the agent's response/tool calls for conflicts with active invariants and prompts the LLM to justify or retract the conflicting step
- [ ] **INV-05**: UI surfaces active invariants and any detected conflicts

### Controlled Transitions

- [ ] **TRANS-01**: Task states have an explicit set of allowed transitions; illegal transitions (e.g. execution before an approved plan, done before validation) are rejected
- [ ] **TRANS-02**: Attempting an illegal transition produces a clear, explainable rejection rather than silently succeeding or crashing
- [ ] **TRANS-03**: Task execution correctly resumes from a paused state without violating the transition graph

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap — research (FEATURES.md) confirms these are legitimate differentiators, not table stakes, for this milestone's scope.

### Memory

- **MEM-06**: Automatic memory summarization/consolidation when a tier grows large
- **MEM-07**: Memory decay / relevance scoring (forget low-value entries over time)
- **MEM-08**: Vector/semantic search over long-term memory

### Personalization

- **PERS-05**: Cross-session profile learning (LLM infers preferences from behavior, not just explicit save)

### Task State

- **TASK-06**: Full subagent dispatch / actual delegated execution of created tasks
- **TASK-07**: Interactive checkpoint editing during pause (human edits state before resume)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| OAuth / external identity providers | Simple username/password is sufficient for a handful of course graders and peers; no need for third-party auth infra |
| Fine-grained roles/permissions | Every user is "admin"; no need for a permissions model beyond authenticated vs not |
| Real subagent execution of created tasks | Day 13 only lays the groundwork (task records the LLM can create); wiring real subagent dispatch is future work |
| Multi-tenant data isolation beyond user_id scoping (orgs/teams) | Single flat user table is sufficient for course scope |
| Long-term memory sharing across users | Per-user by design; only global project invariants are shared |
| General-purpose workflow engine / external state-machine library | Massive overkill for 4-5 states; violates hard constraint of no extra infra/message broker |
| Memory/task UI as a separate SPA or with a JS framework/bundler | Violates hard constraint: vanilla JS + CDN libraries only, no bundler, no npm packages |

## Traceability

Which phases cover which requirements. Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| AUTH-01 | Phase 1 | Complete |
| AUTH-02 | Phase 1 | Complete |
| AUTH-03 | Phase 1 | Complete |
| AUTH-04 | Phase 1 | Complete |
| MEM-01 | Phase 2 | Pending |
| MEM-02 | Phase 2 | Pending |
| MEM-03 | Phase 2 | Pending |
| MEM-04 | Phase 2 | Pending |
| MEM-05 | Phase 2 | Pending |
| PERS-01 | Phase 3 | Complete |
| PERS-02 | Phase 3 | Complete |
| PERS-03 | Phase 3 | Complete |
| PERS-04 | Phase 3 | Complete |
| TASK-01 | Phase 4 | Pending |
| TASK-02 | Phase 4 | Pending |
| TASK-03 | Phase 4 | Pending |
| TASK-04 | Phase 4 | Pending |
| TASK-05 | Phase 4 | Pending |
| INV-01 | Phase 5 | Pending |
| INV-02 | Phase 5 | Pending |
| INV-03 | Phase 5 | Pending |
| INV-04 | Phase 5 | Pending |
| INV-05 | Phase 5 | Pending |
| TRANS-01 | Phase 6 | Pending |
| TRANS-02 | Phase 6 | Pending |
| TRANS-03 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 26 total
- Mapped to phases: 26 (all v1 requirements)
- Unmapped: 0

---
*Requirements defined: 2026-09-19*
*Last updated: 2026-09-19 after initial definition*
