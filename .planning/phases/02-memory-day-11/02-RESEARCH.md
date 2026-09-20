# Phase 2: Memory (Day 11) - Research

**Researched:** 2026-09-20
**Domain:** Explicit, tool-call-gated agent memory (working + long-term) added to an existing FastAPI/SQLModel/SQLite two-process chat app; OpenAI-compatible function-calling extension to a hand-rolled SSE streaming client
**Confidence:** HIGH (tool-calling wire format — empirically verified live against the actual configured LM Studio instance this session, not just docs; SQLModel schema/patterns — HIGH, direct extension of proven in-codebase conventions; DeepSeek-specific streaming+tools nuance — MEDIUM, docs-verified only, could not hit a real DeepSeek key this session)

## Summary

This phase adds two new SQLite tables (`WorkingMemory`, `LongTermMemory`), a new tool-call dispatcher module (`agent/tools.py`), and a sidebar Memory tab, on top of an already-completed Phase 1 (Auth) that supplies `User`, `Session`, and `user_id`-scoped `Chat`/`Settings`. The single hardest technical question this phase depends on — **does OpenAI-compatible tool/function calling actually work end-to-end (including streaming) against both LLM backends this app supports** — is no longer an open question. This session ran live requests against the developer's actual running LM Studio instance (`http://localhost:1234`, confirmed reachable) with a model that declares `"capabilities": ["tool_use"]` (`qwen/qwen3.5-9b`), covering: non-streaming tool_calls, streaming SSE tool_call deltas, a full tool-result round-trip (`role: "tool"` message back to the model), and a two-distinct-tool discrimination test mirroring D-01's exact `save_working_memory`/`save_long_term_memory` split. All four succeeded on the first attempt, with well-formed JSON arguments and correct tool selection every time. DeepSeek's official docs confirm an identical OpenAI-compatible request/response shape (this session could not hit DeepSeek live — the `.env` still holds the literal placeholder value from `.env.example`, not a real key — see Environment Availability).

A second, unplanned but directly relevant finding: `agent/llm_client.py::LMStudioClient` currently calls `POST /api/v0/models/load` and `/api/v0/models/unload`, but the actual installed LM Studio version rejects both v0 endpoints outright (`"Unexpected endpoint or method"`) — the real, working endpoints are `POST /api/v1/models/load` (body: `{"model": "..."}`) and `POST /api/v1/models/unload` (body: `{"instance_id": "..."}`, not `"model"`). This is a pre-existing bug unrelated to MEM-01..05, but it directly blocks the "load a tool-capable model via the app's own UI" path that a live demo would use, so it's flagged as a pitfall the planner should decide whether to fix in-phase or file separately.

**Primary recommendation:** Extend `agent/llm_client.py::stream_chat()` to accept a `tools: list[dict]` parameter and accumulate `tool_calls` deltas by `index` across SSE chunks (verified shape below); build `agent/tools.py` as a synchronous, sequential dispatcher invoked from inside `agent/ws.py::_handle_chat_message`'s existing per-chat lock (never `asyncio.gather`, never a background task); add `WorkingMemory`/`LongTermMemory` SQLModel tables following the exact `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` pattern already used for `Chat.user_id`; default the graded demo to DeepSeek per STATE.md's stated fallback policy, but do not block the plan on it — LM Studio's tool-calling was empirically excellent in this session's tests.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Tool schema definition (save_working_memory, save_long_term_memory) | API/Backend | — | Schemas are passed to the LLM API as part of the outbound request; pure backend concern, no client involvement |
| Tool-call dispatch (parse `tool_calls`, route to handler, execute sequentially) | API/Backend | — | `agent/tools.py`, invoked inside `agent/ws.py`'s existing per-chat-locked flow; must never live in the browser or a background task |
| Working/long-term memory persistence | Database/Storage | API/Backend | New SQLite tables via SQLModel; write path is exclusively the tool dispatcher (never `context_engine.py`, which only reads) |
| Memory read-injection into system prompt | API/Backend | — | Extends `context_engine.py::build_system_prompt()`, the existing single context-assembly chokepoint — do not fork a second injection path |
| Memory inspection UI (sidebar tab) | Browser/Client | API/Backend (new GET endpoints) | Pure read/render in vanilla JS against new REST endpoints; no business logic in the browser |
| LLM backend selection (DeepSeek vs LM Studio) | API/Backend | — | Existing `payload.model` field already selects the backend implicitly via `llm_client`'s single base_url + API key pairing (see Pitfall: llm_client backend routing, below) — a real architectural gap this phase's tool-calling work will expose |

## User Constraints (from CONTEXT.md)

<user_constraints>

### Locked Decisions

- **D-01:** Two distinct, separately named tools — `save_working_memory(key, content)` and `save_long_term_memory(key, content)` — not one generic `save_memory(layer, ...)` tool. Chosen because the tool *name* signals intent to the LLM more reliably than trusting a correctly-set `layer` argument every call, especially given LM Studio tool-calling reliability was an open question. (This session's empirical test directly validates this rationale — see Code Examples.)
- Short-term memory needs no tool call — it is the existing message tree / current context window, unchanged by this phase.
- **D-02:** Long-term memory is scoped at the **user level** (keyed by `user_id` only, no `chat_id`) — a true cross-chat memory table, not tied to the chat it was written from. Chosen because Phase 3's user profile is built on top of long-term memory, and a profile is not chat-specific.
- **Reconciliation with MEM-04:** since long-term memory is user-scoped, the per-chat inspection panel shows *all* of the user's long-term memory entries (not filtered to the current chat), alongside this chat's working memory and short-term dialog. This is intentional, not a gap — the UI must not be built as if long-term memory were chat-filtered.
- **D-03:** Working memory is a key-value scratchpad table: `(user_id, chat_id, key, value, updated_at)`. `save_working_memory(key, content)` overwrites the row for that key. Chosen over an append-only log because it mirrors the existing `Settings.chat_id`-scoped pattern, and an append-only log risks duplicating the message tree.
- **D-04:** The memory panel lives in the **sidebar as a tab/panel**, next to existing chat settings — always reachable without an extra click, not a modal. Shows all three layers (short-term, working, long-term) for the active chat in one place (long-term shows the full user-level set per D-02's reconciliation).

### Claude's Discretion

- Whether working/long-term memory is automatically injected (read-only) into the system prompt each turn for continuity, vs. purely display-only in the UI this phase. MEM-03 only restricts *writes* to explicit tool calls — reads are not gated. **Recommendation (this research): read-injection.** It mirrors the existing `Settings.facts_json`/`summary_text` injection pattern already in `context_engine.py::build_system_prompt()`, gives the memory tool calls an observable effect on agent behavior (not just a UI artifact), and is a small, well-understood extension of an existing function.
- Exact table field set beyond what D-02/D-03 require (e.g., whether to add a `category` column to long-term memory). **Recommendation: skip `category` for MVP.** Nothing in MEM-01..05 requires categorization, and PROJECT.md's own prior research (`ARCHITECTURE.md`) suggested `category="profile"` for Phase 3's reuse — but that's Phase 3's decision to make when it lands, not something to guess at now. Adding an unused nullable column speculatively violates this project's "no build tooling/dependencies we don't need yet" convention. If Phase 3 needs it, add it then with a trivial `ALTER TABLE` (same idiom as `migrate_add_context_length`).
- Tool-calling reliability across DeepSeek vs. the configured local LM Studio model — **resolved empirically this session, see Summary and Code Examples below.** LM Studio tool-calling was reliable and well-formed across 4 distinct live tests (non-streaming, streaming, round-trip, multi-tool discrimination) with `qwen/qwen3.5-9b`. Per STATE.md's stated policy ("default to DeepSeek if unreliable"), this is now moot for that specific model — but the planner should still implement the DeepSeek-as-safe-default framing in the demo runbook, since `DEEPSEEK_API_KEY` in `.env` is currently a placeholder (see Environment Availability) and other locally-loadable models (e.g. `qwen/qwen2.5-coder-14b-instruct`) do **not** declare `tool_use` capability in LM Studio's own model metadata — reliability is genuinely model-specific, not backend-specific.
- Exact markup/styling of the sidebar Memory tab — match existing Tailwind patterns in `index.html`/`app.js` (see Code Examples for the concrete pattern to copy).

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MEM-01 | Agent has 3 explicitly separated memory layers — short-term (dialog), working (task data), long-term (profile/decisions/knowledge) | Short-term = existing `Message` tree (no change). Working/long-term = two new dedicated SQLModel tables (see Standard Stack, Architecture Patterns). Confirmed empirically that the LLM correctly discriminates which tool to call for which layer (Code Examples, "Multi-tool discrimination test"). |
| MEM-02 | Long-term and working memory stored in dedicated SQLite tables, not folded into the message tree | Two dedicated tables (`WorkingMemory`, `LongTermMemory`), FK'd per D-02/D-03, no relation to `Message`. See Standard Stack for exact schema. |
| MEM-03 | LLM explicitly chooses what to save and to which layer, via tool calls (not implicit/automatic classification) | Verified live: `tools=[...]` param + `tool_calls` in response is the only trigger path. Anti-pattern section documents why `extract_and_update_facts`'s debounced-implicit shape must NOT be reused. Dispatcher design (Architecture Patterns) makes writes reachable only from parsed `tool_calls`, never from prose. |
| MEM-04 | Possible to inspect what data landed in each memory layer for a given chat | New `GET /api/v1/chats/{chat_id}/memory` endpoint returns short-term (existing tree, already available via `/tree`), working (filtered by `chat_id`), and long-term (all rows for `user_id`, per D-02 reconciliation — explicitly not chat-filtered). |
| MEM-05 | UI shows the current contents of each memory layer | Sidebar Memory tab (D-04), pure GET + render, no new WS message type strictly required for MVP (poll on tab-open + refresh after `done` WS message, mirroring the existing stats-panel refresh pattern). |

</phase_requirements>

## Project Constraints (from CLAUDE.md)

These are binding, not suggestions — the plan-checker will verify compliance:

- Type hints everywhere; `async`/`await` for all I/O. No bare `except:`.
- `structlog` (`get_logger(__name__)`), never `print()`.
- `datetime.now(timezone.utc)`, never `datetime.utcnow()`.
- Import order: stdlib → third-party → local.
- SQLModel FK cascade: `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` — **never** `Field(ondelete=...)` (silently ignored).
- `.is_(None)` instead of `== None` in SQLAlchemy `where()`.
- Always `await session.commit()` after writes, `await session.rollback()` in exception handlers.
- No Docker/npm/Node/Redis/RabbitMQ/Celery/`multiprocessing`/`os.fork`.
- Frontend: vanilla JS + CDN libraries only (Tailwind, Marked.js, DOMPurify) — no bundler, no npm packages.
- All new data scoped by `user_id` (per Week-3-specific addendum in CLAUDE.md: "All new data (memory, tasks, invariants, profiles) is scoped by `user_id`").
- Memory writes must be synchronous, per-chat-locked (reuse `agent/state.py::chat_locks`), never fire-and-forget/debounced (explicit Week-3 addendum, matches STATE.md).
- Multiple tool calls in one LLM turn execute strictly sequentially, never `asyncio.gather`d (explicit Week-3 addendum).
- Branch: `Day11`, pushed and merged to `main`, never deleted.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|---------------|
| SQLModel / SQLAlchemy 2.x | already pinned (`sqlmodel>=0.0.22`) | `WorkingMemory`/`LongTermMemory` table definitions | Already the project's only ORM; no new dependency |
| Pydantic 2.x `model_json_schema()` | already pinned (`pydantic>=2.9.0`) | Generate OpenAI-style `tools=[...]` JSON schemas from typed request models | Already a FastAPI/SQLModel dependency; avoids hand-writing JSON schema by hand and keeps tool-arg validation consistent with the rest of the app's Pydantic-first request handling |
| httpx (existing) | already pinned (`httpx>=0.27.0`) | No change to the HTTP client itself — `agent/llm_client.py::stream_chat()` gets a new `tools` kwarg on the same existing POST | Confirmed via `npm`-equivalent check for Python: `pip index versions httpx` not needed, this is a pinned existing dependency, no version bump required |

**No new pip packages are required for this phase.** [VERIFIED: direct empirical test against the project's actual running LM Studio instance] Tool-calling is an additive JSON field (`tools`) on the exact same `/v1/chat/completions` endpoint `agent/llm_client.py` already calls — confirmed live, not just via docs.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| None new | — | — | This phase deliberately adds zero new pip dependencies — matches CLAUDE.md's "no build tooling beyond what's already configured" convention and the prior milestone research's explicit rejection of `langchain`/`instructor`/`pydantic-ai` for tool orchestration (would fight the existing hand-written SSE loop) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled `tools=[...]` + manual delta accumulation in `llm_client.py` | `langchain`/`instructor`/`pydantic-ai` | Rejected — these wrap the LLM call itself with their own streaming abstraction, which would fight (not extend) the existing hand-written SSE loop; large dependency surface for an additive JSON field change |
| Two dedicated tables (`WorkingMemory`, `LongTermMemory`) | One polymorphic `Memory` table with a `layer` enum column | Rejected — MEM-02 explicitly requires dedicated tables; a single discriminated table recreates the "everything in one undifferentiated store" anti-pattern the whole milestone exists to avoid |

**Installation:**
```bash
# No new packages — this phase uses only already-pinned dependencies.
```

**Version verification:** [VERIFIED: `pip show` against the project's actual installed environment]

| Package | Installed Version |
|---------|--------------------|
| sqlmodel | 0.0.42 |
| pydantic | 2.13.5 |
| httpx | 0.28.1 |

All exceed the `requirements.txt` floors already pinned (`sqlmodel>=0.0.22`, `pydantic>=2.9.0`, `httpx>=0.27.0`) — no `requirements.txt` change needed for this phase.

## Package Legitimacy Audit

**Not applicable — this phase installs zero new external packages.** slopcheck/registry verification is skipped per the protocol's scope (only required "whenever this phase installs external packages"). If the planner later decides a `category` column or similar needs a new dependency, re-run this gate at that time.

## Architecture Patterns

### System Architecture Diagram

```
Browser (vanilla JS, ui/static/app.js)
    |
    | WS send {content, model}  (existing, unchanged)
    v
agent/ws.py :: ws_chat()  -- session + origin check (existing, unchanged)
    |
    | per-chat lock (agent/state.py::chat_locks) -- EXISTING, reused not duplicated
    v
agent/ws.py :: _handle_chat_message()
    |
    |-- _persist_user_message()                      (existing)
    |
    |-- context_engine.py :: build_llm_context()      (EXTENDED)
    |     |-- existing: compression strategy over Message tree
    |     |-- NEW: build_system_prompt() also injects working+long-term memory (read-only)
    |     `-- NEW: attaches tools=[...] schema list (from agent/tools.py registry)
    |
    |-- llm_client.py :: stream_chat(..., tools=[...])   (EXTENDED)
    |     |-- tokens stream to WS as today (unchanged wire contract for plain content)
    |     `-- NEW: accumulates tool_calls deltas by index across SSE chunks
    |
    |-- IF tool_calls present:
    |     agent/tools.py :: dispatch_tool_calls()        (NEW)
    |       for each tool_call, STRICTLY SEQUENTIAL, in returned order:
    |         route by tool name -> agent/memory.py :: save_working_memory() / save_long_term_memory()
    |         each write commits synchronously, inside the SAME per-chat lock + DB session
    |       -> feed {role: "tool", tool_call_id, content} back to the SAME session
    |       -> one more stream_chat() call for the final assistant text
    |
    |-- _persist_assistant_message()                  (existing)
    |
    `-- WS `done` message -- EXTENDED with a lightweight memory_writes: [...] summary
                              (id/key/layer only -- full content is read via REST, not pushed)

New REST (pure GET, read path, mirrors GET /api/v1/chats/{id}/stats):
    GET /api/v1/chats/{chat_id}/memory
        -> short_term:   existing tree walk (or a pointer to /tree, reused)
        -> working:      WorkingMemory rows WHERE chat_id = :chat_id AND user_id = caller
        -> long_term:    LongTermMemory rows WHERE user_id = caller  (NOT chat-filtered, per D-02)

SQLite (app.db)
    Existing: Chat, Message, Settings, TokenUsage, User, Session
    NEW: WorkingMemory (user_id, chat_id, key, value, updated_at)
    NEW: LongTermMemory (user_id, key, value, created_at, updated_at)
```

A reader can trace the primary use case end to end: a chat message enters at the WS, `build_llm_context` decides what memory to inject as read-only context, `stream_chat` may surface `tool_calls`, `agent/tools.py` executes them sequentially and durably before the turn is considered done, and the sidebar Memory tab reads the resulting rows back out through a dedicated GET endpoint — completely decoupled from the write path, satisfying MEM-04's inspectability requirement structurally, not just by convention.

### Recommended Project Structure

```
agent/
├── tools.py            # NEW: tool schema registry (Pydantic model_json_schema()) +
│                        #      dispatch_tool_calls() -- sequential executor, reused unchanged
│                        #      by Phases 3-5 per STATE.md's locked decision
├── memory.py            # NEW: WorkingMemory / LongTermMemory CRUD (read for injection,
│                         #      write only reachable from tools.py's dispatcher)
├── llm_client.py          # EXTENDED: stream_chat() gains `tools` param; SSE parser
│                          #           accumulates tool_calls deltas by index
├── context_engine.py       # EXTENDED: build_system_prompt() injects memory read-only;
│                           #           build_llm_context() attaches tool schemas
├── ws.py                    # EXTENDED: _handle_chat_message() gains a tool-call round-trip
│                            #           step, still inside the existing per-chat lock
├── schemas.py                 # EXTENDED: Pydantic request/response models for the new
│                              #           tool argument schemas and the memory REST endpoint
└── main.py                     # EXTENDED: GET /api/v1/chats/{chat_id}/memory route

shared/
└── models.py            # EXTENDED: WorkingMemory, LongTermMemory SQLModel tables

ui/static/
├── index.html           # EXTENDED: sidebar Memory tab/panel markup (D-04)
└── app.js               # EXTENDED: fetch + render for the memory panel; optional new
                          #           'tool_call' WS case in handleWsMessage()'s switch
```

### Structure Rationale

- `agent/tools.py` is the one genuinely new *kind* of module this phase introduces — every other file listed is an extension of an already-established module, matching the existing `*_engine.py`/`*_client.py` naming convention.
- `agent/memory.py` is a thin CRUD layer, deliberately kept separate from `agent/tools.py` so the "read for context, write via tool call" boundary (Anti-Pattern below) stays structurally visible in the module graph, not just documented in a comment.
- No new top-level package — everything stays inside `agent/`/`shared/`/`ui/`, consistent with this project's existing structure.

### Concrete SQLModel Schema (WorkingMemory / LongTermMemory)

Following the exact `Settings`/`Chat` FK-cascade convention already in `shared/models.py`:

```python
# shared/models.py -- additions

class WorkingMemory(SQLModel, table=True):
    """Key-value scratchpad for one chat's current task data (D-03). Overwritten on save."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    chat_id: int = Field(
        sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("chat_id", "key", name="uq_working_memory_chat_key"),)


class LongTermMemory(SQLModel, table=True):
    """Durable, cross-chat fact/decision/knowledge scoped to the user only (D-02)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_long_term_memory_user_key"),)
```

Notes:
- `UniqueConstraint("chat_id", "key")` / `("user_id", "key")` directly implements D-01/D-03's "overwrite the row for that key" semantics as a DB-level guarantee (upsert-on-save), not just an application convention — `agent/memory.py`'s save functions should use a `SELECT ... WHERE chat_id=... AND key=...` then update-or-insert, or an SQLite `ON CONFLICT` upsert.
- `Column(Integer, ForeignKey(...))` matches `Chat.user_id`'s exact existing pattern — do not use `Field(foreign_key=...)` (SQLModel's shorthand), since this codebase's convention (and CLAUDE.md's explicit rule) requires the `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` form for cascade behavior to actually take effect.
- No migration function is strictly required for these two *new* tables — `shared/database.py::init_db()` calls `SQLModel.metadata.create_all`, which creates any table that doesn't exist yet (confirmed by reading `shared/database.py`); the existing `migrate_*` functions in that file are only needed for *altering* already-existing tables (e.g., adding a column to `settings`), which does not apply here.

### Pattern 1: Tool-call dispatch as a synchronous, sequential extension of the existing per-chat-locked flow

**What:** `agent/tools.py::dispatch_tool_calls()` takes the list of `tool_calls` parsed from one LLM response and executes them one at a time, in the order the model returned them, inside the same `async with chat_locks[chat_id]` block and the same DB `session` that `_handle_chat_message` already holds for the triggering message. Each tool's result (including any generated ID) is available to the next tool call in the same turn.

**When to use:** Every tool-call round-trip in this app, for MEM-03 and every later phase that adds tools (Personalization, Tasks, Invariants) to the same dispatcher.

**Why this shape, not a background task:** `agent/context_engine.py::extract_and_update_facts()` is the existing precedent for "fire an LLM-driven side effect from `ws.py`" — but it is debounced and runs *outside* the per-chat lock, which is exactly the pattern documented (in this repo's own prior research, `PITFALLS.md` Pitfall 7) as lossy under rapid messages. Memory tool-call writes must not repeat that mistake.

**Example (empirically verified request/response shape, see Code Examples for live-tested payloads):**
```python
# agent/tools.py
from collections.abc import Callable, Awaitable
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

ToolHandler = Callable[[AsyncSession, int, int, dict[str, Any]], Awaitable[dict[str, Any]]]

TOOL_REGISTRY: dict[str, ToolHandler] = {}


def register_tool(name: str) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator to register a tool handler by its OpenAI-schema function name."""
    def _wrap(fn: ToolHandler) -> ToolHandler:
        TOOL_REGISTRY[name] = fn
        return fn
    return _wrap


async def dispatch_tool_calls(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Execute tool calls strictly sequentially; never asyncio.gather this."""
    results: list[dict[str, Any]] = []
    for call in tool_calls:  # sequential, NOT gathered -- STATE.md hard constraint
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        handler = TOOL_REGISTRY.get(name)
        if handler is None:
            results.append({"tool_call_id": call["id"], "error": f"unknown tool {name}"})
            continue
        result = await handler(session, user_id, chat_id, args)
        results.append({"tool_call_id": call["id"], "content": json.dumps(result)})
    return results
```

### Pattern 2: SSE tool_calls delta accumulation in `stream_chat`

**What:** [VERIFIED: live SSE capture against the project's actual LM Studio instance, see Code Examples] In streaming mode, a `tool_calls` delta arrives as one or more chunks per tool call, each keyed by `delta.tool_calls[].index`. The first chunk for a given index carries `id`, `type: "function"`, and `function.name`; the `function.arguments` string starts empty and is appended to (potentially across many chunks for long arguments) in subsequent chunks that repeat the same `index` but omit `id`/`name`. The terminal chunk for the tool-call turn has an empty `delta` and `finish_reason: "tool_calls"` (contrast with plain content turns, which end with `finish_reason: "stop"`).

**When to use:** Any time `agent/llm_client.py::stream_chat()` is called with a non-empty `tools` list.

**Example:**
```python
# agent/llm_client.py -- extend _parse_sse_stream's sibling to also yield tool-call chunks
async def _parse_sse_stream_with_tools(
    self,
    response: httpx.Response,
) -> AsyncGenerator[dict[str, Any], None]:
    """Parse SSE, accumulating tool_calls deltas by index; yields content and final tool_calls."""
    tool_calls_acc: dict[int, dict[str, Any]] = {}
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        choice = chunk.get("choices", [{}])[0]
        delta = choice.get("delta", {})
        if content := delta.get("content"):
            yield {"type": "content", "content": content}
        for tc_delta in delta.get("tool_calls", []) or []:
            idx = tc_delta["index"]
            acc = tool_calls_acc.setdefault(
                idx, {"id": None, "type": "function", "function": {"name": None, "arguments": ""}},
            )
            if tc_delta.get("id"):
                acc["id"] = tc_delta["id"]
            if fn := tc_delta.get("function"):
                if fn.get("name"):
                    acc["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    acc["function"]["arguments"] += fn["arguments"]
        if choice.get("finish_reason") == "tool_calls":
            yield {"type": "tool_calls", "tool_calls": list(tool_calls_acc.values())}
```

### Pattern 3: Read-only memory injection, extending `build_system_prompt`, never a second context-assembly path

**What:** `context_engine.py::build_system_prompt()` already assembles `system_prompt + facts_json + summary_text` into one string. Extend it to also append working memory (filtered by `chat_id`) and long-term memory (filtered by `user_id`, per D-02) as two more labeled blocks, following the exact `Known facts: {json.dumps(...)}` idiom already there.

**When to use:** Every turn, for every chat — this is the recommended resolution of the "Claude's Discretion" injection question above.

**Example:**
```python
# agent/context_engine.py -- extend build_system_prompt (existing function, do not fork)
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    settings_row = await get_effective_settings(session, chat_id)
    parts = [settings_row.system_prompt or "You are a helpful assistant."]
    facts = _parse_facts_json(settings_row.facts_json)
    if facts:
        parts.append(f"Known facts: {json.dumps(facts)}")
    if settings_row.summary_text.strip():
        parts.append(f"Conversation summary: {settings_row.summary_text.strip()}")

    chat = await session.get(Chat, chat_id)
    working = await memory.list_working_memory(session, chat_id)
    if working:
        parts.append(
            "Working memory (this chat's current task data): "
            + json.dumps({row.key: row.value for row in working}),
        )
    long_term = await memory.list_long_term_memory(session, chat.user_id)
    if long_term:
        parts.append(
            "Long-term memory (persists across all your chats): "
            + json.dumps({row.key: row.value for row in long_term}),
        )
    return "\n\n".join(parts)
```

### Anti-Patterns to Avoid

- **Reusing `extract_and_update_facts`'s debounced-async shape for memory writes:** this is the single most tempting shortcut in this phase and is explicitly disqualifying per MEM-03 and CLAUDE.md's Week-3 addendum ("never fire-and-forget/debounced"). Keep `extract_and_update_facts` exactly as-is for `Settings.facts_json` (unrelated, pre-existing feature); do not extend that pattern to the new tables.
- **`asyncio.gather`-ing tool call execution:** explicitly forbidden by STATE.md and CLAUDE.md. A later call in the same turn (not present in this MVP's two tools, but true in principle and for future phases reusing this dispatcher) may depend on an earlier call's result.
- **A second, parallel context-assembly path in `ws.py` that bypasses `build_llm_context`/`build_system_prompt`:** would duplicate token accounting and silently break the existing 75%-of-context_length overflow trigger, which does not yet know about memory-injection token cost. Extend the existing functions in place; extend `compute_chat_stats` to count the injected memory blocks' tokens too.
- **Treating assistant prose ("I've saved that...") as evidence a write happened:** the only source of truth for "was this saved" is the presence of a `tool_calls` entry with a matching name in that turn's raw response — verified empirically in this session that the model can and does say confirmatory prose *only after* a real tool result comes back (see the round-trip test in Code Examples), so trusting the *tool call*, not the text, is both correct and how the model already behaves when the dispatcher is wired correctly.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Generating JSON Schema for tool arguments | Hand-written JSON schema dicts per tool | Pydantic `BaseModel.model_json_schema()` | Already a project dependency; keeps tool-arg validation consistent with how every other request body in this app is validated; avoids schema/model drift |
| Accumulating streamed tool-call argument fragments | A bespoke string-concat-by-hand ad hoc in `ws.py` | A dedicated accumulator dict keyed by `delta.tool_calls[].index` inside `llm_client.py` (Pattern 2) | This is the one genuinely fiddly bit of the OpenAI streaming spec — get it wrong and multi-chunk arguments silently truncate or interleave across concurrent tool calls in the same turn |
| Global-vs-per-chat/user memory fallback logic | A bespoke condition scattered across `main.py`/`ws.py`/`memory.py` | One `agent/memory.py` module exposing `list_working_memory(session, chat_id)` / `list_long_term_memory(session, user_id)` / `save_*` functions, called from everywhere else | This codebase's own documented tech debt (`Settings` fallback duplicated in 3 call sites) is the exact mistake to avoid repeating for memory |

**Key insight:** Everything genuinely new in this phase (the tool-call wire format) is a well-specified, already-proven-live protocol extension, not a novel design problem — the risk in this phase is entirely architectural discipline (don't let writes leak outside the dispatcher, don't let the injection path fork), not unsolved technology.

## Common Pitfalls

### Pitfall 1: "Explicit" memory silently degrades into the existing implicit `extract_and_update_facts` pattern
**What goes wrong:** The codebase already has a working LLM-driven "extract facts as JSON" pipeline that runs automatically after every user message. It is tempting to relabel or extend it as "memory" since it already exists and superficially looks similar.
**Why it happens:** Path of least resistance — the implicit pattern is already built and "works."
**How to avoid:** Build the two memory tools as a genuinely separate code path reachable only from parsed `tool_calls`; log every write with its triggering `tool_call.id` (from the LLM response) so any write with no corresponding tool-call id is provably a bug.
**Warning signs:** A memory write happens on every message regardless of whether the model emitted a `tool_calls` entry that turn.

### Pitfall 2: The model narrates a save without emitting the tool call ("hallucinated compliance")
**What goes wrong:** [CITED: `.planning/research/PITFALLS.md` Pitfall 6, corroborated by this session's live tests] Smaller/local models can say "Got it, saved!" without a matching `tool_calls` entry.
**Why it happens:** Function-calling reliability is model-dependent.
**How to avoid:** Treat the tool-call event (not the text) as the only source of truth for "was this saved" — this session's empirical round-trip test (Code Examples) confirms the model *does* wait for and reference the actual tool result before confirming in prose when the dispatcher is wired correctly (round-trip test: model said "I have successfully saved..." only in the turn *after* receiving a `role: "tool"` result, not before).
**Warning signs:** UI shows a chat message claiming a save, but the memory panel doesn't show a corresponding row.

### Pitfall 3: Tool-call writes racing the existing debounced-background-task pattern
**What goes wrong:** [CITED: `.planning/research/PITFALLS.md` Pitfall 7] If memory writes are fired as a background task (to avoid blocking token streaming, mirroring `extract_and_update_facts`), a write can commit after a later message's lock has already released, causing last-write-wins data loss.
**How to avoid:** Execute tool calls synchronously, inside the same per-chat lock and DB session as the triggering message (Pattern 1).
**Warning signs:** Two rapid messages in the same chat, each with a memory-tool-call — one save silently missing.

### Pitfall 4: Multiple tool calls in one turn executed out of order or concurrently
**What goes wrong:** [CITED: `.planning/research/PITFALLS.md` Pitfall 8, empirically confirmed this session — the model *does* return multiple `tool_calls` in one array for a single turn, see the multi-tool discrimination test in Code Examples] `asyncio.gather`-ing execution, or executing out of the returned order, risks a later call referencing an ID a not-yet-committed earlier call would have generated.
**How to avoid:** Sequential `for` loop (Pattern 1), never `asyncio.gather`.
**Warning signs:** A turn with `save_long_term_memory` + `save_working_memory` in one response; if either write is missing or out of order after execution, this pitfall has occurred.

### Pitfall 5: `LMStudioClient` model load/unload endpoints are broken against the actual installed LM Studio version
**What goes wrong:** [VERIFIED: live curl against the project's actual running LM Studio instance, this session] `agent/llm_client.py::LMStudioClient._load_model_locked()`/`_unload_model_http()` call `POST /api/v0/models/load` and `POST /api/v0/models/unload`. The real, currently-installed LM Studio instance returns `{"error":"Unexpected endpoint or method. (POST /api/v0/models/load)"}` for both — v0 never had (or has since dropped) a model-load/unload REST endpoint. The correct, working endpoints on this LM Studio version are `POST /api/v1/models/load` (body: `{"model": "<id>"}`, confirmed working, returns `{"type","instance_id","load_time_seconds","status":"loaded"}`) and `POST /api/v1/models/unload` (body: `{"instance_id": "<id>"}` — note the field name is `instance_id`, not `model`).
**Why it happens:** The app's existing tests (`tests/test_lm_studio_client.py`) mock the v0 endpoints with `respx` and have never been run against a real LM Studio instance, so this drift went undetected.
**How to avoid:** This is a pre-existing bug, not new to this phase's scope — but it directly blocks "load a tool-capable model from the app's own UI" for a live demo of MEM-03. **Decide explicitly during planning** whether to fix `LMStudioClient` in this phase (small, isolated change — swap the two URLs and the unload body's field name) or document it as a known limitation and require manually loading the model in the LM Studio desktop app before a demo. Given this phase's demo depends on a loaded tool-capable model, fixing it is low-risk and directly de-risks the phase's own acceptance criteria.
**Detection:** `curl -X POST http://localhost:1234/api/v0/models/load -d '{"model":"..."}'` against a real LM Studio instance returns the "Unexpected endpoint" error shown above; `tests/test_lm_studio_client.py` passes anyway because it only tests against `respx` mocks, never the real service.

### Pitfall 6: Not every locally loadable model declares (or has) tool-use capability
**What goes wrong:** [VERIFIED: live query against `GET /api/v0/models` on this LM Studio instance] Of the 7 LLM/VLM models currently downloaded on this machine, only `qwen/qwen3.5-9b`, `qwen_qwen3.5-9b`, and `prism-ml/bonsai-27b` report `"capabilities": ["tool_use"]` in LM Studio's own metadata. Notably, `qwen/qwen2.5-coder-14b-instruct` — a strong general-purpose candidate one might reach for by default — does **not** list `tool_use`, despite Qwen2.5 models generally supporting function calling upstream (LM Studio's capability flag reflects its own template/detection heuristic, not a hard technical limit).
**Why it happens:** LM Studio's tool-use capability flag is based on whether the model's chat template declares tool-call formatting; not every GGUF quant/config combination is tagged correctly, and the flag can be a conservative under-count.
**How to avoid:** When configuring the demo environment, explicitly select and load a model LM Studio itself reports as `tool_use`-capable (verified via `GET /api/v0/models`), rather than assuming any loaded chat model will emit correct `tool_calls`. `qwen/qwen3.5-9b` is confirmed working end-to-end in this session.
**Detection:** `curl http://localhost:1234/api/v0/models` and check each candidate model's `capabilities` array before committing to it for the demo.

### Pitfall 7: `agent/llm_client.py`'s single global `llm_client` instance conflates the two backends' base URL and API key
**What goes wrong:** [VERIFIED: direct code read] `llm_client = LLMClient(base_url=settings.LM_STUDIO_BASE_URL, api_key=settings.DEEPSEEK_API_KEY)` — a single module-level client is constructed with LM Studio's base URL and DeepSeek's API key baked in together. `payload.model` (from the WS message) selects *which model name* is requested, but the HTTP target (`base_url`) never changes — so a request for a DeepSeek model name (e.g. `deepseek-chat`) would still be sent to `http://localhost:1234/v1/chat/completions` unless something else in the (unread, out-of-phase-scope) model-selection UI logic swaps clients based on the selected model. This is pre-existing behavior, not introduced by this phase, but it directly affects the "default to DeepSeek if LM Studio is unreliable" fallback this phase's CONTEXT.md calls for — that fallback is not just a model-name change, it may require actually pointing requests at DeepSeek's real base URL (`https://api.deepseek.com`).
**Why it happens:** Likely works today because the app has so far only been demoed against one backend at a time, with `LM_STUDIO_BASE_URL` and `DEEPSEEK_API_KEY` in `.env` interpreted together as "whichever is configured."
**How to avoid:** Before relying on "switch to DeepSeek if LM Studio is unreliable" as a fallback plan, confirm (during planning, not this research pass, since it's a pre-existing cross-cutting concern) how `ui/static/app.js`'s model selector actually routes requests to the correct backend base URL — this research could not find that logic in the files read this session and flags it as an **Open Question** below rather than asserting a fix.
**Detection:** Select a DeepSeek model name in the UI while LM Studio is running locally and confirm (via `ui/supervisor.py`/`agent` logs) which base URL the outbound request actually hits.

## Code Examples

All examples below are **live-verified** against the project's actual running LM Studio instance (`http://localhost:1234`, model `qwen/qwen3.5-9b`, which LM Studio's own metadata reports as `tool_use`-capable) during this research session — not merely copied from documentation.

### Non-streaming tool call (basic single-tool test)

Request:
```json
{
  "model": "qwen/qwen3.5-9b",
  "messages": [{"role": "user", "content": "Remember that my favorite color is blue. Use the save_long_term_memory tool to store this."}],
  "temperature": 0,
  "stream": false,
  "tools": [{
    "type": "function",
    "function": {
      "name": "save_long_term_memory",
      "description": "Save a durable fact about the user, scoped to the user across all chats.",
      "parameters": {
        "type": "object",
        "properties": {"key": {"type": "string"}, "content": {"type": "string"}},
        "required": ["key", "content"]
      }
    }
  }]
}
```

Actual response (abbreviated):
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "",
      "reasoning_content": "...",
      "tool_calls": [{
        "type": "function",
        "id": "u61otXRDdSH0du1aIk5YB7aHtlj1VPrb",
        "function": {"name": "save_long_term_memory", "arguments": "{\"key\":\"favorite_color\",\"content\":\"blue\"}"}
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

Notable: the model emits a `reasoning_content` field (Qwen3-style thinking) alongside `content`/`tool_calls` — this app's `_parse_sse_stream` currently only reads `delta.get("content")` and will correctly ignore `reasoning_content` without change, but be aware it exists in both streaming and non-streaming responses from this model family, in case a future phase wants to surface reasoning separately.

### Streaming tool call — SSE delta shape

```
data: {"choices":[{"delta":{"role":"assistant","reasoning_content":"The"},"finish_reason":null}]}
... (many reasoning_content deltas) ...
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"rH3YTa6yXyq1CjS1cCrvwo0YwfhsVXQr","type":"function","function":{"name":"save_long_term_memory","arguments":""}}]},"finish_reason":null}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function","function":{"arguments":"{\"key\":\"favorite_color\",\"content\":\"Blue\"}"}}]},"finish_reason":null}]}
data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]
```

Confirms Pattern 2's accumulate-by-index design: the id/name arrive in the first tool_calls chunk, arguments arrive in a subsequent chunk (potentially fragmented further for longer payloads), and the terminal chunk carries `finish_reason: "tool_calls"` with an empty delta.

### Full round-trip (tool result fed back, final assistant reply)

Request messages array (third call, after the tool executed):
```json
[
  {"role": "user", "content": "Remember that my favorite color is blue. Use the save_long_term_memory tool to store this."},
  {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "save_long_term_memory", "arguments": "{\"key\":\"favorite_color\",\"content\":\"blue\"}"}}]},
  {"role": "tool", "tool_call_id": "call_1", "content": "{\"status\":\"saved\"}"}
]
```

Response:
```json
{"message": {"role": "assistant", "content": "I have successfully saved your favorite color, blue, to my long-term memory.", "tool_calls": []}, "finish_reason": "stop"}
```

Confirms: `finish_reason` flips to `"stop"` once the model has a tool result and produces normal prose — this is the exact shape `agent/ws.py::_handle_chat_message` needs to drive a second `stream_chat()` call after dispatching tools, before persisting the final assistant message.

### Multi-tool discrimination test (directly validates D-01's rationale)

Prompt: "Two things: 1) Permanently remember my name is Alex (long-term fact about me). 2) For just this chat session, note that the current task step is drafting the intro paragraph (temporary scratchpad, not permanent)." — both `save_long_term_memory` and `save_working_memory` offered as tools.

Result: model correctly emitted **two** `tool_calls` in one response, in the correct order and with the correct tool for each:
```json
"tool_calls": [
  {"function": {"name": "save_long_term_memory", "arguments": "{\"key\":\"user_name\",\"content\":\"Alex\"}"}},
  {"function": {"name": "save_working_memory", "arguments": "{\"key\":\"current_task_step\",\"content\":\"drafting the intro paragraph\"}"}}
]
```
This is the empirical basis for treating LM Studio + `qwen/qwen3.5-9b` as reliable for MEM-03/D-01 in this session's testing, and for Pitfall 4's multi-tool-calls-in-one-turn handling being a real (not theoretical) case to design for.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|-------------------|---------------|--------|
| LM Studio REST model management via `/api/v0/models/load`/`unload` (what `agent/llm_client.py` currently implements) | `/api/v1/models/load`/`unload` (v1 REST API) | [CITED: WebSearch result, LM Studio docs — "the native v1 REST API ... has been officially released ... v0 REST API has since been deprecated"] Confirmed live against this project's installed LM Studio version, which already rejects v0 | Any phase touching `LMStudioClient` should migrate to v1; Pitfall 5 above |

**Deprecated/outdated:** LM Studio's `/api/v0/*` model-management endpoints are deprecated in the version currently installed on this machine — do not add any *new* code against `/api/v0/models/*`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|-----------------|
| A1 | DeepSeek's streaming (`stream: true`) tool-calls delta shape is identical to the OpenAI/LM Studio shape verified live this session | Architecture Patterns / Pattern 2, State of the Art | DeepSeek's official docs confirm the non-streaming `tools`/`tool_calls` shape and the `role: "tool"` round-trip explicitly, but do not explicitly document the *streaming* delta-accumulation shape in the fetched page; this app already streams plain content from DeepSeek successfully today (same SSE mechanism), so the risk is that tool_calls-specific streaming deltas differ in some minor way (e.g., different or missing `index` field) — moderate risk, easy to catch in a 5-minute manual test once a real `DEEPSEEK_API_KEY` is configured |
| A2 | No `category` column is needed on `LongTermMemory` for this phase (deferred to Phase 3 if needed) | User Constraints / Claude's Discretion | Low — adding a nullable column later via `ALTER TABLE` (same idiom as `migrate_add_context_length`) is a trivial, additive migration; only risk is minor rework of Phase 3's injection code if Phase 3 assumed the column already existed |
| A3 | Read-injection of working/long-term memory into the system prompt (vs. display-only) is the right call for this phase | User Constraints / Claude's Discretion, Architecture Patterns / Pattern 3 | Low-medium — if wrong, easy to revert (remove the two injection blocks from `build_system_prompt`); the two-tool discrimination test's own prompt shows the model reasoning correctly without needing memory injected back yet, so injection is additive value, not a correctness dependency for MEM-03 itself |

## Open Questions (RESOLVED)

> Both questions below were settled during phase planning. Each carries an inline **RESOLVED** note naming the plan that settled it; the question text is kept verbatim as the record of what was unknown at research time.

1. **How does `ui/static/app.js`'s model selector route a chosen DeepSeek model name to DeepSeek's actual base URL, given `llm_client` is a single module-level instance constructed with LM Studio's base URL?**
   - What we know: `agent/llm_client.py` instantiates one global `llm_client = LLMClient(base_url=settings.LM_STUDIO_BASE_URL, api_key=settings.DEEPSEEK_API_KEY)`. `payload.model` only changes the `"model"` field in the JSON body, not the HTTP target.
   - What's unclear: Whether there's a second `LLMClient` instance somewhere for DeepSeek that this research pass didn't locate (only `ws.py`, `main.py`, `context_engine.py`, `llm_client.py` were read in full), or whether backend-switching is in fact not yet implemented and both "backends" currently only work if you point `LM_STUDIO_BASE_URL` at whichever service you want.
   - Recommendation: The planner should grep the full `agent/` package for any second `LLMClient(...)` construction or a `DEEPSEEK_BASE_URL`-style config key before writing tasks that assume "switch to DeepSeek" is a one-line model-name change. This directly affects how confidently the demo can rely on the "default to DeepSeek if unreliable" fallback CONTEXT.md calls for.
   - **RESOLVED (Plan 02-05):** Backend switching is in fact not implemented — `agent/` contains exactly one `LLMClient(...)` construction, built with `LM_STUDIO_BASE_URL`, so a chosen model name only changes the `"model"` body field and never the HTTP target. Plan 02-05 Task 2 verifies this with `grep -rn "LLMClient(" agent/` (expect exactly one match) and records the DeepSeek-routing limitation in `02-05-SUMMARY.md` as a measured fact. The MEM-03 demo therefore runs on LM Studio only; wiring a real DeepSeek base URL is explicitly out of scope for Phase 2.

2. **Should `agent/llm_client.py::LMStudioClient`'s v0-vs-v1 endpoint bug (Pitfall 5) be fixed inside this phase, or tracked separately?**
   - What we know: It's real, verified, and currently blocks the app's own "Load Model" UI button from working against the installed LM Studio version.
   - What's unclear: Whether fixing it is in MEM-01..05's scope or belongs to a maintenance/bugfix task outside Week 3's phase structure.
   - Recommendation: Given the phase's own acceptance criteria implicitly require a loaded, tool-capable local model for a demo, recommend fixing it as a small, isolated task within this phase (2-line change: URL path + body field name) rather than deferring — but flag this explicitly for user/planner sign-off since it's outside the literal MEM-01..05 text.
   - **RESOLVED (Plan 02-02):** Fixed in-phase, as recommended. Plan 02-02 is a dedicated Wave 1 plan that migrates `_load_model_locked`/`_unload_model_http` to `POST /api/v1/models/load` and `POST /api/v1/models/unload`, captures the returned `instance_id` at load and sends it at unload, and repoints the `respx` mocks in `tests/test_lm_studio_client.py` and `tests/test_model_switch_lock.py` to the v1 contract with a negative assertion that v0 is no longer called. Scoped under MEM-03 because the acceptance demo requires a loaded, `tool_use`-capable local model.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| LM Studio (local server) | MEM-03 empirical verification, local-model demo path | Yes — confirmed reachable at `http://localhost:1234` this session | REST v1 confirmed (v0 deprecated/removed) | — |
| Tool-use-capable local model | MEM-03 demo on the LM Studio backend | Yes — `qwen/qwen3.5-9b` (and `qwen_qwen3.5-9b`, `prism-ml/bonsai-27b`) confirmed via `GET /api/v0/models` `capabilities: ["tool_use"]` | qwen3.5, Q5_K_M/Q6_K quant | `qwen/qwen2.5-coder-14b-instruct` is present but not flagged tool_use-capable — do not default to it for the memory demo |
| DeepSeek API key | Fallback backend per CONTEXT.md ("default to DeepSeek if unreliable") | **No** — `.env`'s `DEEPSEEK_API_KEY` still holds the literal placeholder value from `.env.example` (`your_deepseek_api_key_here`); a live request this session returned `"Authentication Fails, Your api key: ****here is invalid"` | — | LM Studio's live-verified reliability (this session) makes this fallback currently unexercised, but the planner/user should provision a real key before the graded demo if DeepSeek-as-fallback is to be a genuine, tested option rather than an assumed one |
| Agent process (port 8001) | General dev/test loop | Yes — `GET /health` returned `{"status":"ok"}` this session | — | — |

**Missing dependencies with no fallback:**
- A real `DEEPSEEK_API_KEY` — needed only if the team wants to *actually exercise* the DeepSeek fallback path before the graded demo, rather than relying solely on this session's LM Studio empirical results plus DeepSeek's official docs.

**Missing dependencies with fallback:**
- None blocking — LM Studio's local tool-calling is confirmed reliable in this session's tests, so the phase is not blocked on DeepSeek access to proceed.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|----------------|---------|--------------------|
| V2 Authentication | No (new to this phase) | Already handled by Phase 1 — `get_current_user`/`get_current_user_ws`, reused unchanged |
| V4 Access Control | Yes | Every new memory query/write MUST filter by the authenticated caller's `user_id` (via `Chat.user_id` for working memory's `chat_id` ownership check, and directly via `LongTermMemory.user_id`) — reuse the existing `_get_chat_or_404`/`_user_owns_chat` ownership-check pattern, never trust a client-supplied `chat_id`/`user_id` alone |
| V5 Input Validation | Yes | Tool-call `arguments` JSON is LLM-generated, not user-typed, but still untrusted input from the model's perspective — validate `key`/`content` against Pydantic models (length limits, following the existing `CONTENT_MAX_LENGTH`/`SYSTEM_PROMPT_MAX_LENGTH`-style constants in `agent/schemas.py`) before persisting, and reject malformed `tool_calls.function.arguments` JSON with a clear dispatcher-level error rather than letting a `json.loads` exception propagate uncaught |
| V6 Cryptography | No | No new secrets/crypto surface introduced by this phase |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|------------------------|
| Cross-user memory read/write via a spoofed `chat_id` in the new `GET /api/v1/chats/{chat_id}/memory` endpoint or a tool-call's `chat_id` context | Tampering / Information Disclosure | Resolve `chat_id` -> `user_id` ownership via the existing `_get_chat_or_404`-style check before returning/writing any memory row; never accept a bare `user_id` from the client for `LongTermMemory` — always derive it from the authenticated session |
| Unbounded `LongTermMemory`/`WorkingMemory` growth inflating every future system prompt (token-budget starvation, eventually a self-inflicted `ContextOverflowError`) | Denial of Service (self-inflicted) | [CITED: `.planning/research/PITFALLS.md` Moderate Pitfall 4] Not a hard requirement for MVP per CONTEXT.md, but recommend at minimum logging/monitoring row counts; a hard cap is reasonable scope for this phase given it's cheap and directly protects `no_compression` users from hitting overflow sooner because of injected memory |
| Memory-poisoning: content that arrives via a future untrusted-input tool (not in this phase's scope) gets treated as a legitimate long-term fact and re-injected forever | Tampering | [CITED: `.planning/research/PITFALLS.md` Moderate Pitfall 5] Out of this phase's literal scope (chat-only, no external content ingestion yet) — but design the memory schema now so a future ingestion tool doesn't get an implicit free pass to call `save_long_term_memory` from within tool-result content itself; note this for Phase 3+ awareness, no code change required this phase |

## Sources

### Primary (HIGH confidence)
- Live empirical tests against the project's actual running LM Studio instance (`http://localhost:1234`), this session: `GET /v1/models`, `GET /api/v0/models`, `POST /v1/chat/completions` (non-streaming + streaming, single-tool, multi-tool, and full tool-result round-trip), `POST /api/v0/models/load` (confirmed broken), `POST /api/v1/models/load`/`unload` (confirmed working)
- Direct codebase inspection: `agent/llm_client.py`, `agent/ws.py`, `agent/context_engine.py`, `agent/main.py`, `agent/dependencies.py`, `agent/state.py`, `agent/schemas.py`, `shared/models.py`, `shared/auth.py`, `shared/database.py`, `shared/config.py`, `ui/static/index.html`, `ui/static/app.js`, `tests/conftest.py`, `tests/test_lm_studio_client.py`, `.env`
- [LM Studio REST API v0/v1 endpoints — search result summary](https://lmstudio.ai/docs/developer/rest/endpoints) — confirms v0 deprecation in favor of v1

### Secondary (MEDIUM confidence)
- [DeepSeek API — Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls) — WebFetch summary: confirms `tools` request shape, `tool_calls` response shape, and the `role: "tool"` round-trip pattern; does not explicitly document streaming-mode tool_calls deltas
- `.planning/research/ARCHITECTURE.md`, `.planning/research/STACK.md`, `.planning/research/PITFALLS.md`, `.planning/research/SUMMARY.md` — prior milestone-level research (2026-09-19), cited throughout above; this phase's research treats their tool-calling claims as now upgraded from MEDIUM to HIGH confidence per this session's live verification, and their SQLModel/architecture recommendations as still current

### Tertiary (LOW confidence)
- None used without cross-verification in this pass.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; existing pinned versions confirmed via `pip show`
- Tool-calling wire format (both non-streaming and streaming): HIGH — empirically verified live against the actual configured LM Studio instance and model this session, cross-checked against DeepSeek's official docs for the non-streaming/round-trip shape
- Architecture (dispatcher placement, SQLModel schema, injection point): HIGH — direct extension of proven in-codebase conventions (`Settings` fallback pattern, `sa_column=Column(ForeignKey(...))`, existing per-chat lock)
- DeepSeek-specific streaming nuance: MEDIUM — docs-verified only; `.env`'s DeepSeek key is a placeholder, so this could not be tested live this session (see Environment Availability, Assumptions Log A1)
- Pitfalls: HIGH for the two directly-verified findings (LM Studio v0/v1 endpoint break, tool_use capability flags); MEDIUM-HIGH for the memory-write-discipline pitfalls (grounded in this codebase's own already-documented tech debt via prior research)

**Research date:** 2026-09-20
**Valid until:** 2026-10-04 (14 days) — shorter than the default 30-day window because part of this research (live LM Studio behavior, installed model list) is specific to the developer's current local environment and could change if models are added/removed or LM Studio is updated

