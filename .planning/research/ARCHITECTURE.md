# Architecture Research

**Domain:** Retrofitting auth + agent memory/task/invariant layers onto an existing two-process (UI/Agent) FastAPI chat app with a message-tree data model
**Researched:** 2026-09-19
**Confidence:** MEDIUM-HIGH (component boundaries and DB design are HIGH confidence — direct extensions of patterns already proven in this codebase; tool-calling support in DeepSeek/LM Studio is MEDIUM confidence — verified against official docs but not implemented/tested here; FSM/invariant runtime behavior is a design recommendation, not an industry-standardized pattern)

## Standard Architecture

### System Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│  Browser (vanilla JS) — ui/static/app.js                               │
│  Sends `credentials: 'include'` fetch + WS to Agent (port 8001)        │
│  directly (NOT proxied through UI/8000 — confirmed in app.js)          │
└───────────────┬───────────────────────────────┬────────────────────────┘
                │ REST (cookie)                 │ WS (cookie, same-site)
┌───────────────▼───────────────────────────────▼────────────────────────┐
│  Agent Server (FastAPI, port 8001) — agent/                            │
│                                                                          │
│  ┌───────────────┐  public routes: /health, /auth/login, /auth/register│
│  │ agent/auth.py │  session dependency: get_current_user()             │
│  │ (NEW)         │  used as router-level Depends() for REST,           │
│  │               │  manual pre-accept check for WS (ws.py)             │
│  └───────┬───────┘                                                     │
│          │ user_id                                                     │
│  ┌───────▼────────────────────────────────────────────────────────┐   │
│  │  agent/main.py (REST)          agent/ws.py (WebSocket)          │   │
│  │  all routes now require       ws_chat() checks session before   │   │
│  │  Depends(get_current_user)    websocket.accept(), same pattern  │   │
│  │  scope queries by user_id     as existing _validate_origin()    │   │
│  └───────┬─────────────────────────────────┬─────────────────────┘   │
│          │                                  │                          │
│  ┌───────▼────────┐  ┌────────────────┐  ┌─▼──────────────┐          │
│  │ context_engine │  │ agent/tools.py │  │ agent/tasks.py  │          │
│  │ (EXTENDED)     │◄─┤ (NEW)          │─►│ (NEW)           │          │
│  │ injects        │  │ Tool-Call      │  │ FSM transition  │          │
│  │ profile +      │  │ Dispatcher:    │  │ validation +    │          │
│  │ invariants +   │  │ save_memory,   │  │ history writes  │          │
│  │ task scratchpad│  │ create_task,   │  └─────────────────┘          │
│  │ into prompt    │  │ transition_task│  ┌─────────────────┐          │
│  └────────────────┘  │ add_invariant  │─►│agent/invariants │          │
│                       └───────┬────────┘  │ .py (NEW)       │          │
│                               │            │ global+per-chat │          │
│                       ┌───────▼────────┐  │ conflict check  │          │
│                       │ agent/memory.py│  └─────────────────┘          │
│                       │ (NEW)          │                                │
│                       │ working +      │                                │
│                       │ long-term R/W  │                                │
│                       └────────────────┘                                │
└──────────────────────────────┬───────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  SQLite (app.db) — shared/models.py + shared/database.py               │
│  Existing: Chat, Message, Settings, TokenUsage                         │
│  NEW: User, WorkingMemory, LongTermMemory, Task, TaskTransition,       │
│       Invariant, InvariantConflict                                     │
└───────────────────────────────────────────────────────────────────────┘
```

The **UI process (port 8000)** is unaffected by any of this — it stays an unauthenticated static-file server plus the Agent supervisor. All new complexity lives inside the Agent process, which already owns the DB and LLM calls. This matters for the build order: nothing here requires touching `ui/`.

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|-------------------------|
| `agent/auth.py` (NEW) | Password hashing, login/register/logout REST handlers, `get_current_user` dependency (REST) and a WS-equivalent check function | Starlette's built-in `SessionMiddleware` (itsdangerous-signed cookie) + `passlib[bcrypt]` for hashing — no new session store table required |
| `agent/tools.py` (NEW) | OpenAI-compatible tool/function schemas + dispatch loop: parses `tool_calls` from LLM response, invokes the matching handler, feeds tool result back for a second completion | Central chokepoint reused by Memory, Personalization, Task, and Invariant phases — build once in the Memory phase |
| `agent/memory.py` (NEW) | CRUD for `WorkingMemory` and `LongTermMemory`; exposes read (for context injection) and write (for tool-call handlers) functions | Mirrors `context_engine.py`'s query style; profile preferences stored as `LongTermMemory` rows with `category="profile"` |
| `agent/tasks.py` (NEW) | Task CRUD, FSM adjacency graph, `is_valid_transition()`, transition history writes | Pure-function transition table (dict/graph), no new dependency needed |
| `agent/invariants.py` (NEW) | Invariant CRUD (global + per-chat), prompt-injection formatting, post-response conflict check | Conflict check follows the existing debounced-async pattern used by `extract_and_update_facts` |
| `agent/context_engine.py` (EXTENDED) | Existing compression logic, now also assembles profile + invariants + active-task scratchpad into the system prompt and passes `tools=[...]` to the LLM call | Extend `build_llm_context()`; do not fork a second context builder |
| `shared/models.py` (EXTENDED) | New SQLModel tables, all FK'd to `User` (directly or transitively via `Chat`) | Same `sa_column=Column(ForeignKey(..., ondelete=...))` pattern already used for `Message.chat_id` |

## Recommended Project Structure

```
agent/
├── main.py             # EXTENDED: mount auth-protected router, add /auth/* public routes
├── ws.py                # EXTENDED: session check before accept(), tool-call round-trip in stream handling
├── context_engine.py     # EXTENDED: inject profile/invariants/task scratchpad, pass tool schemas
├── llm_client.py         # EXTENDED: accept/return tool_calls in stream_chat()
├── auth.py               # NEW: password hashing, session dependency, login/register handlers
├── tools.py               # NEW: tool schema registry + dispatch loop
├── memory.py               # NEW: WorkingMemory / LongTermMemory CRUD
├── tasks.py                 # NEW: Task FSM, transition validation, history
├── invariants.py              # NEW: Invariant CRUD, injection, conflict check
├── schemas.py                  # EXTENDED: Pydantic models for all new REST payloads
└── state.py                     # EXTENDED: per-user rate limiting if needed (optional)

shared/
├── models.py            # EXTENDED: User, WorkingMemory, LongTermMemory, Task,
│                          #           TaskTransition, Invariant, InvariantConflict
└── database.py            # EXTENDED: new migrate_* functions for each table, called from init_db()
```

### Structure Rationale

- One new module per bounded concern (`auth`, `tools`, `memory`, `tasks`, `invariants`), matching the existing `*_engine.py`/`*_client.py` naming convention and keeping `main.py`/`ws.py` as thin orchestrators rather than growing them indefinitely.
- `agent/tools.py` is the one genuinely new *kind* of component (nothing like it exists today) — everything else extends an established pattern (new SQLModel tables, new CRUD module, new context-injection step).
- No new top-level package: everything stays inside `agent/` and `shared/`, matching `STRUCTURE.md`'s explicit guidance ("Avoid: New packages outside `agent/`, `ui/`, `shared/`, `tests/`").

## Architectural Patterns

### Pattern 1: Session auth via Starlette's built-in `SessionMiddleware`, not a new dependency

**What:** FastAPI is built on Starlette, which ships `starlette.middleware.sessions.SessionMiddleware` — a signed-cookie session (itsdangerous `URLSafeTimedSerializer`), HTTP-only by default, with no server-side session store. It processes both `"http"` and `"websocket"` ASGI scope types, so `websocket.session` is populated the same way `request.session` is.

**When to use:** Exactly this project's stated constraint — "HTTP-only session cookie... works uniformly for REST + WebSocket," no external identity provider, single-deployment local app.

**Trade-offs:**
- Pro: Zero new session-store table, zero new heavy dependency (already ships with Starlette/FastAPI); satisfies REST+WS uniformity for free.
- Pro: Cookie carries only `{"user_id": ...}` (small, no PII) — signed, not encrypted, so don't put secrets in it.
- Con: No server-side revocation list — "logout" just clears the cookie; a stolen signed cookie remains valid until expiry. Acceptable for this project's threat model (local, single-deployment, coursework) but should be an explicit, documented trade-off, not an oversight.
- Con: `SameSite=None` requires `Secure` (HTTPS-only) in modern browsers — this app runs on plain `http://localhost`, so `SameSite=None` won't work. This is *not* actually a problem here: `localhost:8000` and `localhost:8001` are cross-*origin* (different port) but same-*site* (SameSite is defined by registrable domain, not port), so the default `SameSite=Lax` cookie is still sent on the Agent's cross-port fetch/WS calls from the UI-served page. Confirmed consistent with the CORS setup already in place (`allow_credentials=True` + explicit origin allowlist on both `ui/main.py` and `agent/main.py`).

**Example:**
```python
# agent/main.py
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET, same_site="lax")

# agent/auth.py
async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
```

### Pattern 2: Auth check before `accept()` on WebSocket, mirroring the existing origin-validation pattern

**What:** `agent/ws.py::ws_chat` already validates the `Origin` header *before* calling `websocket.accept()`, closing with a policy-violation code on failure. Extend the same pre-accept gate to check the session.

**When to use:** Any WS endpoint in a session-cookie-authenticated app — WS doesn't get FastAPI's REST-style `Depends()` short-circuiting for free in the same way; you must reject before upgrading the connection or the client gets a false "connected" state.

**Trade-offs:** Requires reading `websocket.session` (populated by `SessionMiddleware`) manually in `ws_chat`, consistent with how `_validate_origin()` already reads `websocket.headers` manually. No new pattern introduced — just one more manual pre-accept check next to the existing one.

**Example:**
```python
# agent/ws.py — inside ws_chat(), alongside _validate_origin()
async def ws_chat(websocket: WebSocket, chat_id: int):
    if not _validate_origin(websocket):
        await websocket.close(code=1008)
        return
    user_id = websocket.session.get("user_id")
    if user_id is None:
        await websocket.close(code=1008, reason="unauthenticated")
        return
    await websocket.accept()
    # ... existing flow, now scoped by user_id
```

**Critical gotcha to avoid:** Do NOT auth-gate `GET /health` — `ui/supervisor.py`'s health-poll loop calls it every 3s with no session cookie. Keep `/health`, `/auth/login`, `/auth/register` as explicitly public routes (mount the rest under a router with `dependencies=[Depends(get_current_user)]`).

### Pattern 3: Global-vs-per-scope NULL-FK fallback, reused for Invariants (and extended for Settings)

**What:** The codebase already has this exact pattern for `Settings` (`chat_id IS NULL` = global default, non-null = per-chat override, with `get_effective_settings()` falling back). Reuse it verbatim for `Invariant`: `user_id IS NULL AND chat_id IS NULL` = global project invariant (shared across all users, matches PROJECT.md's "only global project invariants remain shared across users"); `user_id = X AND chat_id = Y` = that user's per-chat invariant layered on top.

**When to use:** Any time a project needs "defaults + per-scope override" without a separate defaults table — this domain has two instances of exactly that shape (Settings, Invariants).

**Trade-offs:** Requires the same discipline documented in this codebase's own pitfall list for Settings — every new query path must remember the fallback (`.is_(None)`, not `== None`), or it will silently return nothing for users who haven't set an override.

**Example:**
```python
# agent/invariants.py — mirrors context_engine.py::get_effective_settings()
async def get_active_invariants(session, user_id: int, chat_id: int) -> list[Invariant]:
    global_stmt = select(Invariant).where(
        Invariant.user_id.is_(None), Invariant.chat_id.is_(None), Invariant.active.is_(True)
    )
    per_chat_stmt = select(Invariant).where(
        Invariant.user_id == user_id, Invariant.chat_id == chat_id, Invariant.active.is_(True)
    )
    global_rows = (await session.exec(global_stmt)).all()
    chat_rows = (await session.exec(per_chat_stmt)).all()
    return [*global_rows, *chat_rows]
```

### Pattern 4: Transition table as a pure function + append-only history table

**What:** Model the Task FSM as a hardcoded adjacency dict validated by a pure function, not a new dependency (e.g. `python-statemachine`/`transitions` library) — the graph here is small (4 states) and doesn't need a general-purpose FSM engine. Every accepted transition writes a row to `TaskTransition` (append-only, never updated/deleted) for TASK-05's history requirement; `Task.state` holds only the current value (denormalized for cheap reads).

**When to use:** Small, fixed state graphs with an audit requirement — the standard pattern (confirmed via general DB-design research) is "whitelist of valid transitions + append-only history table," which is exactly what TRANS-01/02/TASK-05 ask for.

**Trade-offs:** A dedicated FSM library would add declarative guards/hooks, but for 4 states and no plans for a 5th, a dict is simpler, has zero new dependency, and is trivially testable as a pure function (`is_valid_transition("execution", "done") == False`).

**Pause is orthogonal to the FSM, not a 5th state.** TASK-04 requires "paused at any state, resumed without re-explaining context." Model this as `Task.paused_at: datetime | None` rather than adding a `paused` state — pausing doesn't change `Task.state`; resuming just clears `paused_at` and re-hydrates working memory for that task. This avoids doubling the transition graph (`planning↔paused`, `execution↔paused`, etc.) and keeps TRANS-03 ("resumes without violating the transition graph") trivially true — a resume never touches the graph.

```python
# agent/tasks.py
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "planning": {"execution"},
    "execution": {"validation"},
    "validation": {"execution", "done"},  # allow rework loop back to execution
    "done": set(),
}

def is_valid_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())
```

### Pattern 5: Tool-call round-trip as a synchronous extension of the existing streaming loop

**What:** Both LLM backends this project uses expose OpenAI-compatible `tools`/`tool_calls` on `/v1/chat/completions`: DeepSeek's API docs confirm the `tools` parameter is "fully compatible with OpenAI's format," and LM Studio's docs confirm tool use through `/v1/chat/completions` "following OpenAI's function-calling style" (MEDIUM confidence — verified against both vendors' official docs pages, not hands-on tested in this session). This means `agent/llm_client.py::stream_chat()` can be extended to pass a `tools=[...]` list and detect `tool_calls` in the streamed/accumulated response without switching SDKs or backends.

**When to use:** MEM-03/TASK-03 explicitly require the LLM to *choose* what to save / when to create a task via tool calls, not implicit classification — this is the only pattern that satisfies that requirement directly.

**Trade-offs:**
- Pro: No new LLM abstraction — extends `llm_client.py` in place.
- Con: Tool-calling reliability is model-dependent for LM Studio's local backend — not every local GGUF model handles function calling correctly. Flag this explicitly for whoever configures the local model in Week 3: pick a tool-calling-capable model (e.g. a recent Qwen/Llama instruct build with function-calling fine-tuning), don't assume "any model that streams chat" also emits well-formed `tool_calls`.
- Con: A tool-call round-trip (assistant emits `tool_calls` → server executes → server sends `tool` role message back → model continues) adds a second (non-streamed or partially-streamed) LLM round-trip mid-response. This changes the WS token-streaming contract subtly — plan for a `type: "tool_call"` WS message so the frontend can show "saving to memory..." / "creating task..." affordances rather than silently pausing the stream.

## Data Flow

### Request Flow — message with a tool call

```
Browser (cookie attached)
    ↓ WS send {content, model}
ws.py::ws_chat  — session check (Pattern 2) → accept() → per-chat lock (existing)
    ↓
_persist_user_message (existing, now Chat is user-scoped)
    ↓
context_engine.build_llm_context()
    ├─ existing: compression strategy over Message tree
    ├─ NEW: memory.py → inject profile (LongTermMemory, category=profile)
    ├─ NEW: invariants.py → get_active_invariants() → inject global+per-chat text block
    ├─ NEW: tasks.py → if chat has an active (non-done, non-paused) task, inject its
    │        WorkingMemory scratchpad rows as task context
    └─ NEW: tools.py → attach tool schema list (save_memory, create_task,
             transition_task, add_invariant) to the outbound LLM payload
    ↓
llm_client.stream_chat() — tokens stream to WS as today; if tool_calls appear:
    ↓
tools.py dispatcher — for each tool_call: route to memory.py / tasks.py / invariants.py
    ├─ memory.write(user_id, chat_id, task_id?, layer, key, value)
    ├─ tasks.create(user_id, chat_id, title) / tasks.transition(task_id, to_state)
    │    → tasks.is_valid_transition() gate (Pattern 4); reject → WS error, no DB write
    └─ invariants.add(user_id, chat_id, text)  [user-authored, via UI or LLM proposal]
    ↓ tool results fed back to LLM for final completion
_persist_assistant_message (existing)
    ↓
NEW (debounced, async — same shape as existing extract_and_update_facts):
invariants.py::check_conflicts() — inspect assistant text + tool_calls against active
    invariants → on conflict, write InvariantConflict row, prompt LLM to justify/retract
    ↓
WS `done` message — EXTENDED with { memory_writes: [...], task: {...}, conflicts: [...] }
```

### Key Data Flows

1. **Auth resolution happens once per REST request / once per WS connection**, not per message — `get_current_user` resolves `user_id` from the signed cookie; everything downstream (Chat, Task, Memory, Invariant queries) is scoped by that `user_id`, either directly (tables with their own `user_id` FK) or transitively (tables scoped by `chat_id`, where `Chat.user_id` is the authority).
2. **Memory/task/invariant writes are LLM-tool-call-triggered, not automatic** (per MEM-03/TASK-03) — the *only* place these tables get written from chat activity is inside the tool-call dispatcher (`agent/tools.py`), never inside `context_engine.py` (which only reads, for injection) and never as a side effect of plain message persistence. This boundary is important to keep explicit and inspectable, matching the stated Core Value ("explicit, inspectable decisions about what goes where").
3. **Invariant injection is read-only context assembly**; the conflict check is a separate, asynchronous, non-blocking step run *after* the response is persisted — same debounce pattern already used for fact extraction, so it doesn't add latency to the visible token stream.
4. **UI panels (memory/task/invariant inspection — MEM-05/TASK-05/INV-05) are pure GET endpoints** against the new tables, no different in shape from the existing `GET /api/v1/chats/{id}/stats` pattern — poll or refresh-on-`done`-message, consistent with how the stats panel already works.

## Scaling Considerations

This is a local-first, single-deployment, coursework-scale app — scaling to "100k users" is out of scope by the project's own constraints (single SQLite DB, no auth infra beyond username/password, no orgs/teams). The only meaningful "scale" axis here is *per-user data volume over the life of the course*, not concurrent users.

| Scale | Architecture Adjustments |
|-------|---------------------------|
| Single user, few chats (current default) | Everything as designed above; SQLite single-writer + per-chat lock already handles this |
| A handful of course users (peers/graders), each with several chats/tasks | No architecture change needed — `user_id` FKs already isolate data; SQLite WAL mode already used. Watch `LongTermMemory` row growth per user (no cap currently specified — MEM-04/05 need "inspect what's in each layer," which argues for pagination in the inspection endpoint sooner rather than later) |
| Long-running chats with many tasks/invariants | `WorkingMemory` scoped to `task_id` should be cleared/archived on task completion (`state == "done"`) to keep the injected scratchpad small — this is a compression concern analogous to the existing `context_engine.py` strategies, not a new problem |

### Scaling Priorities

1. **First bottleneck:** Context prompt size — profile + global invariants + per-chat invariants + task scratchpad all get injected on *every* request, stacking on top of the existing compression-strategy output. This is a real near-term risk (75%-of-context_length trigger in the existing engine doesn't currently account for these new injected blocks). Recommend token-counting the new injected blocks the same way `context_engine.py` already token-counts messages, and treating them as part of the budget, not additive/free.
2. **Second bottleneck:** SQLite single-writer contention if tool-call writes (memory/task/invariant) start competing with message-tree writes inside the same request — mitigate by keeping tool-call writes inside the *same* per-chat lock already held by `ws.py::_handle_chat_message`, not a separate lock, to avoid deadlock/ordering bugs.

## Anti-Patterns

### Anti-Pattern 1: Auth-gating `/health` or other supervisor-facing endpoints

**What people do:** Apply a blanket `dependencies=[Depends(get_current_user)]` at the `app` level instead of scoping it to a router.

**Why it's wrong:** `ui/supervisor.py`'s health-poll loop has no session cookie and no way to acquire one — it would immediately mark the Agent "unhealthy" and restart-loop it every 3 seconds.

**Do this instead:** Mount two routers — one public (`/health`, `/auth/*`), one authenticated (everything else) — and apply the dependency at the authenticated router only.

### Anti-Pattern 2: Treating auth as a bolt-on before touching `Chat`/`Settings`/`Message`

**What people do:** Add a `User` table and login flow, but leave `Chat.user_id` optional/nullable "for now," planning to backfill later.

**Why it's wrong:** Every subsequent phase (Memory, Personalization, Tasks, Invariants) needs `user_id` scoping from day one (explicitly called out in PROJECT.md's own Key Decisions: "Auth as its own foundation phase... before Day 11... avoids retrofitting"). A nullable `Chat.user_id` means every downstream query needs an extra `IS NOT NULL`/ownership check, and existing chats created pre-auth need an explicit migration decision (assign to a default/first-created user) — do this once, in the Auth phase, not scattered across four later phases.

**Do this instead:** In the Auth phase: add `User`, add `Chat.user_id` (NOT NULL after migration), add `Settings.user_id` (global-fallback logic now needs `chat_id IS NULL AND user_id = X`, i.e. "global per user," not one true global row across all users), migrate existing rows to a bootstrap admin user, and only then start Day 11.

### Anti-Pattern 3: Implicit/automatic memory writes instead of tool-call-gated ones

**What people do:** Have `context_engine.py` (or a background job) automatically decide "this looks like a fact worth remembering" and write to `LongTermMemory` without an explicit LLM tool call — this is exactly the pattern the existing `extract_and_update_facts()` uses for `Settings.facts_json`, and it would be tempting to reuse that shape for the new memory tables.

**Why it's wrong:** MEM-03 explicitly requires the LLM to *choose* what to save and to which layer via tool calls — implicit classification is the anti-goal, not a shortcut. Reusing the fact-extraction shape here would violate the phase's core acceptance criterion.

**Do this instead:** Route all `WorkingMemory`/`LongTermMemory`/`Task`/`Invariant` writes exclusively through the tool-call dispatcher (`agent/tools.py`). The existing `extract_and_update_facts()` pattern remains fine as-is for `Settings.facts_json` (unrelated, pre-existing feature) — just don't extend that pattern to the new tables.

### Anti-Pattern 4: A second, parallel context-assembly path for the new injections

**What people do:** Build profile/invariant/task-scratchpad injection as a separate prepend step in `agent/ws.py`, bypassing `context_engine.py::build_llm_context()`.

**Why it's wrong:** This is precisely the "duplicate `parent_id` logic" anti-pattern already documented in this codebase's own `ARCHITECTURE.md` for tree traversal — a second context-assembly path will drift from the first (e.g. new compression strategies wouldn't account for the injected blocks' token cost, breaking the 75%-of-context_length trigger).

**Do this instead:** Extend `build_llm_context()` itself to append these blocks after existing compression, and extend `compute_chat_stats()` to count their tokens too, so the existing overflow/trigger logic stays correct.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|----------------------|-------|
| DeepSeek API | Existing SSE streaming client, extended with `tools=[...]` param | MEDIUM confidence tool-calling support, per DeepSeek's official docs (api-docs.deepseek.com/guides/tool_calls) |
| LM Studio (local) | Existing OpenAI-compatible `/v1/` client, extended with `tools=[...]` param | MEDIUM confidence — LM Studio's own docs (lmstudio.ai/docs/developer/openai-compat/tools) confirm the API shape; actual tool-call *reliability* depends on which local model is loaded — flag for whoever configures LM Studio in Week 3 |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|----------------|-------|
| `agent/auth.py` ↔ REST routes | `Depends(get_current_user)` at router level | Public router (`/health`, `/auth/*`) stays unguarded |
| `agent/auth.py` ↔ `agent/ws.py` | Manual pre-accept session check, `websocket.session` | Mirrors existing `_validate_origin()` pre-accept pattern |
| `agent/context_engine.py` ↔ `agent/memory.py`/`agent/invariants.py`/`agent/tasks.py` | Direct function calls, read-only (context assembly never writes) | Keeps "read for context, write via tool call" boundary explicit (MEM-04 inspectability) |
| `agent/tools.py` ↔ `agent/memory.py`/`agent/tasks.py`/`agent/invariants.py` | Direct function calls, dispatcher routes by tool name to the owning module's write function | Single chokepoint for all agent-initiated writes to the new tables |
| `agent/ws.py` ↔ `agent/tools.py` | Synchronous, in the same per-chat-locked flow as message persistence | Avoid a second lock — reuse `agent/state.py::chat_locks` |
| New tables ↔ `Chat`/`Message` | FK only, no cross-table joins duplicated — `Task.chat_id`, `WorkingMemory.chat_id` FK to `Chat.id`; nothing new hangs off `Message` directly | Keeps the message tree itself untouched, consistent with "database always keeps full history" principle |

## Suggested Build Order (confirms downstream_consumer's proposed sequence)

1. **Auth (foundation, own branch `Auth`)** — `User` table, `Chat.user_id`/`Settings.user_id` migration + backfill, `SessionMiddleware`, `get_current_user` dependency, WS pre-accept check, login/register REST endpoints, login UI. *Nothing* in phases 2-6 can be scoped correctly without this landing first — confirmed, not just assumed, by tracing every proposed new table back to a `user_id` FK.
2. **Memory (Day 11)** — `WorkingMemory`/`LongTermMemory` tables, `agent/memory.py`, and critically, **`agent/tools.py` (the tool-call dispatcher) gets built here**, since MEM-03 needs it and every later phase (Personalization, Tasks, Invariants) reuses it rather than rebuilding it. Building the dispatcher once, generically, in this phase is the highest-leverage architectural decision in the whole milestone.
3. **Personalization (Day 12)** — `UserProfile` preferences modeled as `LongTermMemory` rows (`category="profile"`), reusing Memory's storage and the Day-11 dispatcher (add a `save_profile` tool or reuse `save_memory` with a reserved category); adds the injection point into `build_llm_context()` and a profile edit UI. Depends on Memory for storage; independent of Tasks/Invariants.
4. **Task State Machine (Day 13)** — `Task`/`TaskTransition` tables, `agent/tasks.py`, `create_task`/`transition_task` tools registered on the Day-11 dispatcher, task panel UI. Depends on Memory (dispatcher) but not on Invariants.
5. **Invariants (Day 14)** — `Invariant`/`InvariantConflict` tables, `agent/invariants.py`, global/per-chat injection (Pattern 3), post-response conflict check. Depends on Memory (dispatcher, for an `add_invariant` tool if the LLM can propose one) and benefits from Tasks existing (a conflict can reference a task's transition), but is not hard-blocked by Tasks.
6. **Controlled Transitions (Day 15)** — Hardens TRANS-01/02/03: the `is_valid_transition()` gate from Day 13 gets its explainable-rejection WS error path, pause/resume correctness (Pattern 4's orthogonal `paused_at` flag) gets tested end-to-end, and invariant conflict checks get wired into transition attempts specifically (not just free-form chat). This is squarely a hardening/integration phase over Tasks (5) + Invariants (4) rather than new components — expect this phase to touch `agent/tasks.py` and `agent/ws.py` more than it adds new files.

**Why this order minimizes rework:** Each phase after Auth adds exactly one new SQLModel table group and reuses (rather than re-implements) the tool-call dispatcher, the context-injection extension point, and the global/per-scope NULL-fallback pattern — all three of which are established once (Auth for the fallback pattern already existing via Settings; Memory for the dispatcher). No phase requires re-opening an already-merged table's FK design, because every table gets its `user_id`/`chat_id` scoping decided against the Auth-phase schema up front.

## Sources

- [Design Patterns for Long-Term Memory in LLM-Powered Architectures — Serokell](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures)
- [Long-Term Memory Architectures for AI Agents — Redis](https://redis.io/blog/long-term-memory-architectures-ai-agents/)
- [5 Architectural Patterns for Persistent Memory and State in AI Agents — MachineLearningMastery](https://machinelearningmastery.com/5-architectural-patterns-for-persistent-memory-and-state-in-ai-agents/)
- [Tool Calls — DeepSeek API Docs](https://api-docs.deepseek.com/guides/tool_calls/)
- [Function Calling — DeepSeek API Docs](https://api-docs.deepseek.com/guides/function_calling)
- [Tool Use — LM Studio Docs](https://lmstudio.ai/docs/developer/openai-compat/tools)
- [How I Solved WebSocket Authentication in FastAPI — DEV Community](https://dev.to/hamurda/how-i-solved-websocket-authentication-in-fastapi-and-why-depends-wasnt-enough-1b68)
- [how to set/get Cookie from WebSocket? — fastapi/fastapi Discussion #10658](https://github.com/fastapi/fastapi/discussions/10658)
- [The Ultimate Multifunctional Database Table Design: Workflow States Pattern — Medium](https://medium.com/@herihermawan/the-ultimate-multifunctional-database-table-design-workflow-states-pattern-156618996549)
- [How to Implement a State Machine in MySQL — OneUptime](https://oneuptime.com/blog/post/2026-03-31-mysql-state-machine/view)
- [What Is LLM Guardrails? — FutureAGI](https://futureagi.com/glossary/llm-guardrails/)
- Direct codebase inspection: `agent/main.py`, `agent/ws.py`, `ui/main.py`, `ui/static/app.js`, `shared/models.py` (existing Settings/Chat FK and fallback patterns), `.planning/codebase/ARCHITECTURE.md`, `.planning/PROJECT.md`

---
*Architecture research for: auth + agent memory/task/invariant layers on an existing two-process FastAPI chat app*
*Researched: 2026-09-19*
