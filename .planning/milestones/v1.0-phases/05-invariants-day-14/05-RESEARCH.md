# Phase 5: Invariants (Day 14) - Research

**Researched:** 2026-09-20
**Domain:** Prompt-injection-based rule enforcement + self-critique LLM round-trip, layered on an existing FastAPI/SQLModel/vanilla-JS two-process chat app
**Confidence:** HIGH (architecture/patterns — direct codebase precedent for every seam) / MEDIUM (exact self-critique prompt/response-parsing shape — no external library involved, but no in-repo precedent for a *second* LLM-as-judge call, only the single-shot `_extract_facts` pattern to extrapolate from)

## Summary

Phase 5 adds no new external dependencies and touches no new architectural layer — it is pure extension of patterns already shipped in Phases 2-4. The data model precedent is `WorkingMemory` (chat-scoped table) vs. `LongTermMemory` (user-scoped table): two structurally distinct tables for two structurally distinct scopes, not one table with a nullable discriminator column. That precedent directly resolves the CONTEXT.md-flagged schema question for `Invariant`: use two tables, `GlobalInvariant` (no `user_id`, no `chat_id` — matches D-02's "not user_id-scoped, genuinely shared" requirement at the schema level, not just the query level) and `ChatInvariant` (user_id + chat_id, both FK-cascade, plus a nullable `overrides_id` FK to `GlobalInvariant.id` for D-05).

The conflict-check mechanism (D-07/D-08) has a direct precedent in `agent/context_engine.py::_extract_facts()` — a non-streaming `llm_client.complete_chat()` call at `temperature=0.0` whose response is parsed as loosely-validated JSON with a safe fallback (`_parse_facts_json`). This is the only existing "LLM produces structured data, backend parses leniently" pattern in the codebase, and this project has **no JSON-mode/structured-output parameter anywhere** in `llm_client.py` — DeepSeek/LM Studio compatibility for a strict `response_format` is unverified, so the safe path is prompt-instructed JSON + lenient parsing, exactly like `_extract_facts`.

The justify/retract round-trip (D-09) is mechanically identical to the existing post-tool-result follow-up call in `agent/ws.py::_handle_chat_message` (lines 283-309): a third `stream_chat()` call whose tokens get appended to `assistant_text` and streamed as `token` events. The one real design decision left open — and flagged as an open question below — is whether the justify/retract text *replaces/extends the persisted assistant message* or is captured as a *separate justification note* attached to the `InvariantConflict` record (the UI-SPEC's banner copy — "«{invariant.title}» — {LLM's justification/retraction text}" — reads as the latter).

**Primary recommendation:** Two new tables (`GlobalInvariant`, `ChatInvariant`) plus one append-style `InvariantConflict` log table; a `complete_chat()`-based self-critique call inserted after the existing tool-result follow-up (or after the initial response if no tools were called) and before `_persist_assistant_message`; a `stream_chat()`-based justify/retract call whose output is captured into a **separate variable** so it can both be appended to `assistant_text` for persistence AND written verbatim into `InvariantConflict.note` for the banner — resolving the ambiguity without contradicting either D-09 or D-12/UI-SPEC.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Global invariant CRUD | API / Backend (`agent/main.py` REST) | Browser (Invariants tab forms) | UI-only write path (D-03); backend owns validation/persistence, browser owns the form |
| Per-chat invariant CRUD + override link | API / Backend | Browser | Same as above; override dropdown is populated client-side from a GET the backend serves |
| Invariant injection into system prompt | API / Backend (`agent/context_engine.py::build_system_prompt`) | — | Prompt assembly is entirely server-side; browser never sees the raw injected text |
| Self-critique / conflict detection | API / Backend (`agent/ws.py`, new `agent/invariants.py`) | — | LLM call orchestration must run inside the existing per-chat lock; no client involvement |
| Justify/retract round-trip | API / Backend | Browser (renders the streamed tokens + banner) | Backend drives the extra LLM call; browser only renders what it's told |
| Conflict persistence (`InvariantConflict`) | Database / Storage | API / Backend | New SQLite table, written by the WS handler inside the existing per-chat-locked session |
| Conflict surfacing (inline banner + tab badge) | Browser / Client | API / Backend (serves the conflict log endpoint) | Pure rendering; data already resolved server-side |
| Fold/unfold UI state (D-11) | Browser / Client | — | Client-only DOM/CSS toggle, no persistence required per CONTEXT.md discretion note |

## User Constraints (from CONTEXT.md)

<user_constraints>

### Locked Decisions

- **D-01:** An invariant has two fields — title + rule text — not a single freeform blob.
- **D-02:** Global invariants live in a table that is **NOT `user_id`-scoped** — a genuinely shared, app-wide set. Any logged-in user can create/edit/delete global invariants via an unscoped UI-only REST endpoint.
- **D-03:** Per-chat invariants are added **UI-only via REST**, not an LLM tool call. `agent/tools.py`'s dispatcher is not touched by this phase.
- **D-04:** Full CRUD (add/edit/delete) on invariants, both global and per-chat — not append-only.
- **D-05:** Per-chat invariants **can override** a global invariant (not merely add to it). The override relationship is an explicit link set at creation time: a nullable `overrides_id` FK on the per-chat invariant, chosen from a dropdown of existing global invariants — not automatic topic/keyword matching.
- **D-06:** When an override exists, the system prompt injects **both rules, explicitly labeled** — `[GLOBAL] <rule text> (overridden for this chat — see below)` followed by `[CHAT] <rule text> (overrides the above)`.
- **D-07:** The conflict check is a **second LLM call** — a self-critique pass, not a programmatic keyword/pattern scan. It is fed the chat's active invariants (already resolved per D-05/D-06) plus the assistant's completed turn, and asked whether anything conflicts.
- **D-08:** The self-critique call inspects the **full response: prose + tool calls** (not tool-call-args-only).
- **D-09:** On a flagged conflict, the turn does **one more LLM round-trip before the `done` frame** — re-prompting with the specific conflict and asking the LLM to justify or retract, with that reply becoming the final assistant output. Mechanically a third potential `stream_chat` call following the same shape as the existing post-tool-result follow-up call (`agent/ws.py` lines 283-309).
- **D-10:** A new sidebar "Invariants" tab, listing global invariants (add/edit/delete) and the current chat's per-chat invariants (add/edit/delete + overrides dropdown).
- **D-11:** All sidebar tabs except "Chats" become minified (header-only) by default, each with a fold/unfold toggle button in its header. Retrofits Memory, Profile, and Tasks tabs too.
- **D-12:** Detected conflicts surface **inline in the chat stream** (banner right after the flagged response) **and** as a **badge/count on the Invariants tab header**. Both, not either/or.
- **D-13:** Conflict records are **persisted** in a new `InvariantConflict`-style table (chat_id, message_id, invariant_id, justification/note text, created_at) — not transient WS-only events.

### Claude's Discretion

- Exact wording/format of the injected invariants section in `build_system_prompt()` beyond D-06's override-labeling requirement — follow the existing pattern from profile/memory/task injection.
- Exact schema for `Invariant`/`InvariantConflict` tables beyond what D-01 through D-13 require (e.g., whether `title` has a max length matching `Profile`'s 2000-char fields).
- Exact self-critique prompt wording for D-07/D-08/D-09 — the specific instructions given to the LLM for the critique pass and the justify/retract re-prompt.
- Exact Tailwind markup/styling of the new Invariants tab and the fold/unfold mechanism (D-11) — match existing patterns, but note this is genuinely new shared UI (no fold/unfold precedent exists yet in this codebase).
- Whether the fold/unfold state (D-11) persists across reloads (e.g. localStorage) or resets each session — not specified by the user, low-stakes UI polish.

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope. (D-11's tab-minification affects existing Memory/Profile/Tasks tab markup, but it's an in-scope UI decision for this phase, not a deferred idea.)

### Additional constraint carried from phase description

Explicitly out of this phase's scope: hard enforcement/rejection of illegal task transitions (TRANS-01/02/03) is Phase 6, not this phase — invariant conflicts here are flagged/justified, not hard-blocked.

</user_constraints>

## Phase Requirements

<phase_requirements>

| ID | Description | Research Support |
|----|-------------|------------------|
| INV-01 | Global invariants (architecture/stack/business rules for the whole app) are defined and stored separately from the dialog | `GlobalInvariant` table (new, not `user_id`-scoped per D-02); CRUD REST endpoints mirroring `agent/profile.py` + `PUT /api/v1/profile` pattern |
| INV-02 | Per-chat invariants can be added by the user, layered on top of global invariants | `ChatInvariant` table (chat_id + user_id FK-cascade) with nullable `overrides_id` FK to `GlobalInvariant`; CRUD REST endpoints under `/api/v1/chats/{chat_id}/invariants` |
| INV-03 | Invariants are injected into the agent's reasoning context (prompt-injection) on every relevant request | New `parts.append(...)` block in `agent/context_engine.py::build_system_prompt()`, following the existing profile/facts/working-memory/long-term-memory/open-tasks assembly order; D-06 override-labeling format |
| INV-04 | A dedicated check inspects the agent's response/tool calls for conflicts with active invariants and prompts the LLM to justify or retract the conflicting step | New self-critique `complete_chat()` call (pattern: `agent/context_engine.py::_extract_facts`) + conditional justify/retract `stream_chat()` call (pattern: `agent/ws.py` lines 283-309), sequenced in `_handle_chat_message` after the tool round-trip and before `_persist_assistant_message` |
| INV-05 | UI surfaces active invariants and any detected conflicts | New Invariants sidebar tab (D-10, UI-SPEC.md); inline conflict banner (D-12) + tab badge, both sourced from the same `InvariantConflict` record via a new `GET /api/v1/chats/{chat_id}/invariant-conflicts` endpoint |

</phase_requirements>

## Standard Stack

### Core

No new libraries are required. This phase is implemented entirely with the stack already installed and verified in `requirements.txt`:

| Library | Version (installed) | Purpose | Why Standard (for this phase) |
|---------|---------|---------|--------------|
| SQLModel | >=0.0.22 [VERIFIED: requirements.txt] | New `GlobalInvariant`, `ChatInvariant`, `InvariantConflict` tables | Already the project's only ORM; FK-cascade convention (`sa_column=Column(ForeignKey(...))`) is load-bearing per `CLAUDE.md` |
| FastAPI | >=0.115.0 [VERIFIED: requirements.txt] | New REST CRUD endpoints for invariants + conflict log | Existing app framework, `Depends(get_current_user)` pattern reused verbatim |
| httpx / respx | >=0.27.0 / >=0.21.0 [VERIFIED: requirements.txt] | Self-critique (`complete_chat`) and justify/retract (`stream_chat`) LLM calls; test mocking | `llm_client.py` already wraps both call shapes; no new client code needed |
| structlog | >=24.4.0 [VERIFIED: requirements.txt] | Logging new `invariant_*` and `conflict_*` events | Existing convention, `logger = get_logger(__name__)` |

### Supporting

Not applicable — no supporting libraries needed beyond the core stack above.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Second LLM call for conflict detection (D-07, locked) | Programmatic keyword/regex scan of tool-call args | Rejected by user explicitly — a keyword scan cannot catch prose-only conflicts (D-08's stated rationale) and can't reason about intent |
| Two separate tables (`GlobalInvariant`/`ChatInvariant`) | Single `Invariant` table with `is_global: bool` + nullable `chat_id`/`user_id` | Single-table is viable and slightly reduces query joins for the override dropdown (self-referential FK), but breaks the schema-level guarantee D-02 asks for ("NOT user_id-scoped" as a table-level fact, not a runtime-null fact) and diverges from the `WorkingMemory`/`LongTermMemory` precedent already established in this codebase for the same scope split |
| Prompt-instructed JSON + lenient parsing for self-critique | OpenAI-style `response_format: {"type": "json_object"}` | Not used anywhere in `llm_client.py` today; DeepSeek supports it, LM Studio/local-model support is unverified in this repo (STATE.md already flags LM Studio tool-calling reliability as an open question) — safer to reuse the proven `_extract_facts` lenient-parse pattern than introduce an unverified strict mode |

**Installation:** None — no new packages.

**Version verification:** All libraries above are already pinned/installed; verified by reading `requirements.txt` directly (not `npm view`/`pip index` — this is an existing Python project, not a fresh install). No registry lookup was needed or performed.

## Package Legitimacy Audit

Not applicable — this phase introduces zero new external packages. The Package Legitimacy Gate protocol is skipped per its own scope ("whenever this phase installs external packages"); none are installed here.

## Architecture Patterns

### System Architecture Diagram

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Browser (ui/static/app.js + index.html)                              │
│                                                                        │
│  Invariants tab (D-10)        Chat message stream                    │
│  ┌──────────────────┐         ┌────────────────────────────────┐    │
│  │ Global list       │         │ ...assistant bubble...          │    │
│  │ (CRUD forms)       │◄────┐  │ ⚠️ conflict banner (D-12) ───┐  │    │
│  │ Per-chat list      │     │  └───────────────────────────────┘  │    │
│  │ (CRUD + overrides  │     │                     ▲                │    │
│  │  dropdown)          │     │                     │ WS: token*,   │    │
│  │ Conflict badge ─────┼─────┘                     │ invariant_    │    │
│  └──────────────────┘                              │ conflict, done│    │
└──────────────────────────┬──────────────────────────┴────────────────┘
                            │ REST (CRUD)                    │ WebSocket
                            ▼                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Agent process (agent/main.py, agent/ws.py, agent/invariants.py)      │
│                                                                        │
│  REST: GET/POST/PUT/DELETE                    WS: _handle_chat_message│
│    /api/v1/invariants (global)                 ┌───────────────────┐ │
│    /api/v1/chats/{id}/invariants (per-chat)     │ 1. persist user   │ │
│    /api/v1/chats/{id}/invariant-conflicts (log) │ 2. build_llm_     │ │
│                                                  │    context()      │ │
│  agent/invariants.py:                           │    → build_system_│ │
│    resolve_active_invariants(chat_id)  ─────────┼──► prompt() injects│ │
│      (applies D-05/D-06 override labeling)      │    invariants     │ │
│                                                  │ 3. stream_chat()  │ │
│                                                  │    (+ tool round- │ │
│                                                  │    trip if any)   │ │
│                                                  │ 4. self-critique  │ │
│                                                  │    complete_chat()│ │
│                                                  │ 5. if conflict:   │ │
│                                                  │    justify/retract│ │
│                                                  │    stream_chat()  │ │
│                                                  │ 6. persist message│ │
│                                                  │ 7. persist        │ │
│                                                  │    InvariantConf- │ │
│                                                  │    lict row       │ │
│                                                  │ 8. send done      │ │
│                                                  └───────────────────┘ │
└──────────────────────────┬─────────────────────────────────────────┘
                            │ SQLModel / aiosqlite
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│ SQLite (shared/models.py)                                            │
│  GlobalInvariant  ChatInvariant (→overrides_id)  InvariantConflict   │
└─────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
agent/
├── invariants.py       # NEW: CRUD + resolve_active_invariants() + conflict persistence, mirrors profile.py/memory.py
├── context_engine.py    # MODIFIED: build_system_prompt() gains an invariants block
├── ws.py                 # MODIFIED: _handle_chat_message gains self-critique + justify/retract steps
├── main.py               # MODIFIED: new REST endpoints for invariant CRUD + conflict log
shared/
└── models.py             # MODIFIED: new GlobalInvariant, ChatInvariant, InvariantConflict tables
ui/static/
├── index.html            # MODIFIED: new Invariants panel markup; fold/unfold headers on 4 panels
└── app.js                # MODIFIED: renderInvariantsPanel, foldable helper, conflict banner rendering
tests/
├── test_invariants.py            # NEW: CRUD unit tests (mirrors test_profile.py)
├── test_invariants_api.py        # NEW: REST endpoint tests (mirrors test_profile_api.py)
├── test_invariants_ws.py         # NEW: end-to-end self-critique/justify-retract WS flow (mirrors test_memory_ws.py/test_task_ws.py)
└── test_cascade_delete.py        # MODIFIED: add ChatInvariant + InvariantConflict cascade assertions
```

### Pattern 1: Two-table global/per-chat scope split

**What:** Separate SQLModel tables for app-wide vs. chat-scoped data, rather than one table with a scope discriminator column.
**When to use:** Any time a capability has a genuinely different ownership/FK shape at two scopes (already used for `WorkingMemory` vs `LongTermMemory`).
**Example:**
```python
# Source: shared/models.py:138-186 (existing precedent, WorkingMemory vs LongTermMemory)
class WorkingMemory(SQLModel, table=True):
    chat_id: int = Field(sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False))
    # ... chat-scoped

class LongTermMemory(SQLModel, table=True):
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    # ... user-scoped, no chat_id at all
```
Apply the same split for invariants:
```python
class GlobalInvariant(SQLModel, table=True):
    """App-wide invariant, shared across all users (D-02 — deliberately NOT user_id-scoped)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)
    rule_text: str = Field(max_length=2000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatInvariant(SQLModel, table=True):
    """Per-chat invariant that layers on top of (or overrides) a global invariant (D-05)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    chat_id: int = Field(sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False))
    title: str = Field(max_length=200)
    rule_text: str = Field(max_length=2000)
    overrides_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("globalinvariant.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InvariantConflict(SQLModel, table=True):
    """Persisted record of a detected invariant conflict (D-13)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False))
    message_id: int = Field(sa_column=Column(Integer, ForeignKey("message.id", ondelete="CASCADE"), nullable=False))
    invariant_scope: str = Field(max_length=10)  # "global" | "chat" -- which table invariant_id points into
    invariant_id: int  # not FK-constrained (would require a polymorphic FK); see Pitfall below
    invariant_title: str = Field(max_length=200)  # snapshot at flag time -- survives later edit/delete of the invariant
    note: str = Field(default="", max_length=2000)  # LLM's justification/retraction text (D-13)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

**Note on `SQLModel` table name resolution:** SQLModel lowercases the class name for the table name by default (`Task` → `task`, `TaskTransition` → `tasktransition`), so `GlobalInvariant` → `globalinvariant` — verify the exact FK target string matches whatever the actual class produces (check via `GlobalInvariant.__tablename__` or a quick `sqlite3 test_app.db ".tables"` after `init_db()`), don't hardcode blind.

### Pattern 2: Non-streaming LLM call for structured judgment, lenient parsing

**What:** Use `llm_client.complete_chat()` (not `stream_chat()`) for a call whose only output is a small structured verdict, and parse the JSON leniently with a safe empty/negative fallback.
**When to use:** Any "LLM as judge/classifier" call where partial/malformed output must not crash the turn.
**Example:**
```python
# Source: agent/context_engine.py:438-476 (existing precedent, _extract_facts)
async def _run_self_critique(
    llm_messages: list[dict[str, Any]],
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    active_invariants: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    """Ask the LLM whether its own completed turn conflicts with an active invariant."""
    critique_prompt = _build_critique_prompt(active_invariants, assistant_text, tool_calls)
    try:
        raw = await llm_client.complete_chat(
            messages=[{"role": "user", "content": critique_prompt}],
            model=model,
            temperature=0.0,
            max_tokens=512,
        )
        return _parse_critique_json(raw)  # same lenient-parse-with-fallback shape as _parse_facts_json
    except Exception as exc:
        logger.warning("invariant_critique_failed", error=str(exc))
        return {"conflict": False}  # fail OPEN: a broken critique call must never block the turn
```
**Design note — fail-open is deliberate:** if the critique call itself errors (timeout, malformed LLM output, connection drop), the turn must still complete and send `done` — INV-04 adds a soft check, not a hard gate (that's explicitly Phase 6's job per the phase boundary). Do not let critique-call failures raise/propagate and abort the WS turn.

### Pattern 3: Third `stream_chat()` round-trip, mirroring the existing tool-result follow-up

**What:** A conditional extra LLM call appended to the same turn, using the identical error-handling shape as the existing post-tool-result follow-up.
**Example:**
```python
# Source: agent/ws.py:283-309 (existing precedent — the post-tool-result follow-up call this phase must mirror)
try:
    async for token in llm_client.stream_chat(
        llm_messages, payload.model, temperature, max_tokens,
    ):
        assistant_text += token
        await websocket.send_json({"type": "token", "content": token})
except Exception as exc:
    logger.error("llm_stream_failed", chat_id=chat_id, error=str(exc))
    await websocket.send_json({"type": "error", "detail": f"LLM error: {str(exc)}", "code": "LLM_ERROR"})
    await session.delete(user_msg)
    await session.commit()
    return
```
For the justify/retract call (D-09), reuse this shape but capture the streamed text into its own variable (`justification_text`) rather than only appending in place, so it can be both persisted as part of the message AND written to `InvariantConflict.note`:
```python
if critique["conflict"]:
    llm_messages.append({
        "role": "user",
        "content": _build_justify_retract_prompt(critique, active_invariants),
    })
    justification_text = ""
    async for token in llm_client.stream_chat(llm_messages, payload.model, temperature, max_tokens):
        justification_text += token
        assistant_text += token  # keep it part of the persisted message too
        await websocket.send_json({"type": "token", "content": token})
    # justification_text is what goes into InvariantConflict.note and the banner
```

### Anti-Patterns to Avoid

- **Blocking the turn on a critique-call failure:** INV-04 is a soft flag-and-prompt mechanism this phase, not a hard gate (TRANS-01/02/03 hard enforcement is explicitly Phase 6). A `try/except` around the critique call must fail open (assume no conflict), never abort the WS turn or delete the user message the way `ContextOverflowError`/`LLM_ERROR` handling does for the primary response.
- **Adding a `user_id` filter to `GlobalInvariant` queries:** D-02 is explicit that global invariants are NOT scoped — copying the `_get_chat_or_404`/ownership-check pattern from per-chat endpoints onto the global CRUD endpoints would silently violate the locked decision.
- **Single self-referential FK across scopes:** don't try to make `overrides_id` a self-referential FK within one shared `Invariant` table unless you actually build the single-table variant — with the two-table design, `overrides_id` on `ChatInvariant` points into `GlobalInvariant`, a cross-table FK, not self-referential.
- **Re-registering invariant tools on the LLM dispatcher:** D-03 is explicit — `agent/tools.py` is not touched. Do not add `save_invariant`/`create_invariant` to `TOOL_REGISTRY`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| JSON parsing from LLM free text | A custom regex/bracket-matching JSON extractor | The existing `_parse_facts_json`-style `json.loads` + `try/except json.JSONDecodeError` + safe-fallback pattern | Already proven in this codebase against the same two LLM backends (DeepSeek, LM Studio); a new bespoke parser adds a second, divergent failure mode for the same underlying problem |
| Cross-user "who can edit this global rule" logic | A bespoke owner/admin check for global invariants | No check at all beyond `Depends(get_current_user)` (any logged-in user may CRUD) | D-02 + `CLAUDE.md`'s "every account has equal admin capability" — inventing a check here would be adding unrequested scope, not simplifying |
| Markdown/XSS-safe rendering of the conflict banner's LLM-generated text | A new sanitizer or raw `innerHTML` | The existing `renderMarkdown()` + `DOMPurify.sanitize()` pipeline already used for chat bubbles | The banner shows LLM-generated justification text — LLM output is exactly the class of content DOMPurify already guards against for this app; reusing avoids a second unsanitized `innerHTML` surface |

**Key insight:** Every mechanism this phase needs — structured LLM output, an extra streaming round-trip, scoped-vs-unscoped tables, sanitized rendering of untrusted text — already has one working, tested implementation somewhere in Phases 1-4. The research finding is "mirror it," not "invent it."

## Common Pitfalls

### Pitfall 1: Persisting `InvariantConflict` before `assistant_msg.id` exists
**What goes wrong:** `InvariantConflict.message_id` is a non-nullable FK to `Message.id`. If the conflict record is built during/after the self-critique call but before `_persist_assistant_message()` runs, `assistant_msg.id` doesn't exist yet.
**Why it happens:** The self-critique and justify/retract calls naturally happen *before* persistence in the current code shape (they need to finalize `assistant_text` first), but the conflict log needs the persisted message's id.
**How to avoid:** Sequence: (1) initial response + tool round-trip, (2) self-critique, (3) justify/retract if flagged, (4) `_persist_assistant_message()` with the now-final `assistant_text`, (5) *then* build and commit the `InvariantConflict` row using `assistant_msg.id`, (6) `done` frame.
**Warning signs:** `IntegrityError` on `InvariantConflict` insert, or a workaround that stores `message_id=None`/`0` (defeats the FK's purpose).

### Pitfall 2: Mixing SSE-shaped and plain-JSON-shaped respx mocks in one test
**What goes wrong:** Existing WS tests (`test_memory_ws.py`, `test_task_ws.py`) queue a list of `httpx.Response` objects built by `_tool_calls_response`/`_plain_content_response`, both of which build **SSE bodies** (`data: ...\n` lines) because they mock `stream_chat()` calls. The new self-critique call uses `complete_chat()`, which expects a **plain JSON body** (`response.json()`, no SSE framing) — reusing the SSE helpers for it will crash the mock or silently return unparseable content.
**Why it happens:** Both `stream_chat()` and `complete_chat()` POST to the identical URL (`{base_url}/v1/chat/completions`), so `respx.post(url).mock(side_effect=_queue_responses([...]))` can't distinguish them by route — only by response shape, which the test author must get right per queue position.
**How to avoid:** Add a small `_plain_json_response(content: str) -> httpx.Response` test helper that returns `httpx.Response(200, json={"choices": [{"message": {"content": content}}]})` (matching what `complete_chat()` actually parses: `body["choices"][0]["message"]["content"]`), and insert it at the correct position in the mocked call sequence (after the tool-result follow-up, before any justify/retract `stream_chat`).
**Warning signs:** `KeyError: 'delta'` or `json.JSONDecodeError` inside `complete_chat()` during a test that exercises the self-critique path.

### Pitfall 3: Forgetting the self-critique call needs the *fully resolved* invariant set, including override labeling
**What goes wrong:** If the critique prompt is built from raw `GlobalInvariant`/`ChatInvariant` rows without applying D-05/D-06's override resolution, the LLM may flag a conflict against a global rule that this chat has explicitly overridden — a false positive that directly contradicts the user's own configuration.
**Why it happens:** It's tempting to fetch both tables independently for the critique prompt rather than reusing the exact same `resolve_active_invariants()` output that `build_system_prompt()` already injected into `llm_messages`.
**How to avoid:** Build a single `agent/invariants.py::resolve_active_invariants(session, chat_id)` function used by *both* `build_system_prompt()` (INV-03) and the self-critique prompt builder (INV-04) — one source of truth for "what's currently active and how is it labeled," not two independent queries that can drift.
**Warning signs:** A conflict flagged against an invariant that the per-chat override list should have suppressed; test coverage should include an explicit "override present → no false-positive conflict against the overridden global rule" case.

### Pitfall 4: `SQLModel` table name mismatch for the `overrides_id` FK string
**What goes wrong:** SQLModel's default table naming is the lowercased class name (`Task` → `"task"`, not `"tasks"`). A hardcoded `ForeignKey("global_invariant.id", ...)` (with an underscore) against a `GlobalInvariant` class will fail at `init_db()`/migration time with a foreign key resolution error, because the actual table name is `"globalinvariant"` (no underscore) unless `__tablename__` is explicitly set.
**Why it happens:** Easy to assume snake_case table names by analogy with `task_transition`... but that table is `TaskTransition` → `"tasktransition"` too (verify — no explicit `__tablename__` override exists anywhere in `shared/models.py` today).
**How to avoid:** Either set `__tablename__` explicitly on `GlobalInvariant`/`ChatInvariant`/`InvariantConflict` for readability, or grep the actual resolved name from a freshly-`init_db()`'d test SQLite file before writing the FK string.
**Warning signs:** `sqlite3.OperationalError: unknown table` or FK constraint creation failure during `init_db()` in tests.

## Code Examples

### Injecting invariants into `build_system_prompt()` (INV-03, D-06)

```python
# Source: pattern follows agent/context_engine.py:61-107 (existing profile/facts/memory/tasks assembly)
async def build_system_prompt(session: AsyncSession, chat_id: int) -> str:
    ...
    active = await invariants.resolve_active_invariants(session, chat_id)
    if active:
        lines = []
        for item in active:
            if item["overridden_by"] is not None:
                lines.append(f'[GLOBAL] {item["rule_text"]} (overridden for this chat — see below)')
                lines.append(f'[CHAT] {item["overridden_by"]["rule_text"]} (overrides the above)')
            elif item["scope"] == "chat":
                lines.append(f'[CHAT] {item["rule_text"]}')
            else:
                lines.append(f'[GLOBAL] {item["rule_text"]}')
        parts.append("Active invariants (always follow these; flag if you cannot):\n" + "\n".join(lines))
    ...
```

### REST endpoint shape for global invariant CRUD (D-02, D-04)

```python
# Source: pattern follows agent/main.py:622-641 (PUT /api/v1/profile precedent — no ownership check)
@app.get("/api/v1/invariants", response_model=list[GlobalInvariantResponse])
async def list_global_invariants(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),  # auth required, but NOT ownership-filtered (D-02)
) -> list[GlobalInvariantResponse]:
    rows = await invariants.list_global(session)
    return [_global_invariant_to_response(r) for r in rows]

@app.post("/api/v1/invariants", response_model=GlobalInvariantResponse, status_code=status.HTTP_201_CREATED)
async def create_global_invariant(
    body: GlobalInvariantCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GlobalInvariantResponse:
    row = await invariants.create_global(session, body.title, body.rule_text)
    return _global_invariant_to_response(row)
# PUT/DELETE /api/v1/invariants/{id} follow the same no-ownership-check shape
```

### REST endpoint shape for per-chat invariant CRUD (D-05)

```python
# Source: pattern follows agent/main.py:496-508 (GET /api/v1/chats/{chat_id}/tasks — chat-owned resource list)
@app.post(
    "/api/v1/chats/{chat_id}/invariants",
    response_model=ChatInvariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_invariant(
    chat_id: int,
    body: ChatInvariantCreate,  # {title, rule_text, overrides_id: int | None}
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatInvariantResponse:
    await _get_chat_or_404(session, chat_id, current_user.id)  # ownership DOES apply here
    row = await invariants.create_chat_invariant(
        session, current_user.id, chat_id, body.title, body.rule_text, body.overrides_id,
    )
    return _chat_invariant_to_response(row)
```

### Fold/unfold helper, built once, applied to all four panels (D-11)

```javascript
// Source: new pattern, no prior precedent in ui/static/app.js -- matches the
// existing "text-slate-400 hover:text-white" icon-button treatment (UI-SPEC.md)
function setupFoldablePanels() {
    document.querySelectorAll('[data-fold-toggle]').forEach((btn) => {
        const targetId = btn.dataset.foldToggle;
        const body = document.getElementById(targetId);
        if (!body) return;
        btn.addEventListener('click', () => {
            const collapsed = body.classList.toggle('hidden');
            btn.textContent = collapsed ? '▸' : '▾';
        });
    });
}
// Called once at init, alongside the other DOMContentLoaded setup calls.
// Each panel header markup gets:
//   <button data-fold-toggle="memory-panel-body" class="text-slate-400 hover:text-white">▸</button>
// and the existing panel content is wrapped in a <div id="memory-panel-body" class="hidden">...</div>
```

## State of the Art

| Old Approach (Phases 2-4) | Current Approach (Phase 5) | When Changed | Impact |
|--------------|------------------|---------------|--------|
| WS turn does at most 2 `stream_chat()` calls (initial + one post-tool-result follow-up) | WS turn can do up to 4 LLM calls: initial `stream_chat`, optional tool-result follow-up `stream_chat`, always-on self-critique `complete_chat`, optional justify/retract `stream_chat` | This phase | Materially increases per-turn latency and token cost; every chat turn now pays for at least one extra non-streaming LLM call even when no conflict is found — worth flagging to the user/planner as an explicit tradeoff, not hidden in the implementation |
| Sidebar panels (Memory/Profile/Tasks) always rendered expanded | All panels except Chats default to collapsed, with an explicit fold/unfold toggle | This phase (D-11) | Existing Memory/Profile/Tasks markup in `index.html` must be restructured (wrapped body divs) even though no new *feature* requirement drove it — flagged explicitly in CONTEXT.md so planning doesn't scope this down |

**Deprecated/outdated:** None — this phase doesn't remove or replace any existing Phase 1-4 mechanism; it is additive.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | DeepSeek/LM Studio will reliably return prompt-instructed JSON for the self-critique call without a `response_format` parameter, at an acceptable failure rate for lenient parsing to handle | Standard Stack / Alternatives Considered, Pattern 2 | If the local LM Studio model frequently returns unparseable critique output, INV-04's conflict detection will silently under-fire (fail-open swallows the failure) — this compounds the existing open question in `STATE.md` about LM Studio tool-calling reliability; recommend smoke-testing the critique prompt against the actual configured local model before committing to a graded demo, same caveat STATE.md already raised for tool calls |
| A2 | Justify/retract text should be captured as a separate variable and written to `InvariantConflict.note`, distinct from (though also appended to) the persisted `assistant_text` | Pattern 3, Summary | D-09's literal wording ("that reply becoming the final assistant output") could instead mean the justify/retract reply *replaces* the original assistant_text entirely (discarding the flagged content). If the planner/user intends full replacement rather than append, the WS flow and the `Message` row's final content will differ from what's implemented — flag for discuss/plan confirmation, not a silent research decision |
| A3 | `InvariantConflict.invariant_id` should be a plain (non-FK-constrained) integer plus an `invariant_scope` discriminator and a denormalized `invariant_title` snapshot, rather than a strict FK into either table | Pattern 1 code example | SQLite doesn't support a single column with a conditional FK into two different tables; the alternative (a strict FK to `ChatInvariant` only, forcing global-invariant conflicts to be represented differently, e.g. a synthetic per-chat shadow row) is more complex and wasn't chosen — but this is a real design fork the planner should confirm rather than treat as settled |

## Open Questions

1. **Does the justify/retract reply replace or append to the originally streamed assistant response?**
   - What we know: D-09 says the round-trip's reply becomes "the final assistant output"; the existing tool-result follow-up call in `ws.py` *appends* to `assistant_text` (streamed as more `token` events into the same bubble). D-12's UI-SPEC banner copy implies the justification text is *also* shown separately, as its own labeled content ("«{invariant.title}» — {LLM's justification/retraction text}").
   - What's unclear: whether the persisted `Message.content` should be `original_text + justification_text` (matching the existing append-only streaming UX) or `justification_text` alone (a true replacement/retraction), and whether these should ever differ per-conflict-type (justify vs. retract might reasonably behave differently — a retraction arguably *should* replace the flagged content, a justification arguably should append/clarify it).
   - Recommendation: Treat as append (Assumption A2) for planning purposes — it's the lower-risk, code-shape-consistent default — but flag explicitly for `/bm:discuss-phase`-style confirmation or an explicit planner decision before implementation, since it directly affects what gets permanently written to the message tree.

2. **Should the self-critique call also run when the assistant made zero tool calls (prose-only turns)?**
   - What we know: D-08 explicitly requires prose to be in scope ("full response: prose + tool calls"), and INV-04's literal requirement text says "response/tool calls" (either).
   - What's unclear: whether running the critique call on *every* turn (including trivial ones like "hello") is intended, or whether there's an implicit expectation of some lightweight pre-filter (e.g., skip critique if there are zero active invariants for the chat — this is a cheap, safe optimization, not a scope reduction).
   - Recommendation: Always run the critique call when `active_invariants` is non-empty, regardless of tool-call presence, per D-08's literal wording; skip entirely (no LLM call at all) when the chat has zero active invariants — this is a pure performance optimization that changes no observable behavior and should not need separate confirmation.

3. **Does the Invariants tab conflict badge count *unresolved* conflicts only, or all conflicts ever logged?**
   - What we know: D-13 says conflicts are persisted (not transient), backing "the tab's conflict log and badge count." UI-SPEC.md's copy shows a bare numeric count with no "resolved" state modeled anywhere in the schema recommendation above.
   - What's unclear: whether `InvariantConflict` needs a `resolved`/`acknowledged` boolean (so old conflicts stop inflating the badge forever) or whether the badge is simply "count of all conflicts ever recorded for this chat" (monotonically non-decreasing, which may be the intended "audit trail" semantic given D-13's framing as a persisted log rather than a live-alert queue).
   - Recommendation: Default to "count of all conflicts for this chat" (no resolved/acknowledged state) since nothing in CONTEXT.md's decisions mentions dismissal/acknowledgment as a capability, and TRANS-01/02/03-style resolution workflows are explicitly Phase 6 territory. If the planner wants a dismiss/acknowledge affordance, that's new scope beyond D-01 through D-13 and should be confirmed, not assumed.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| SQLite (via aiosqlite) | New `GlobalInvariant`/`ChatInvariant`/`InvariantConflict` tables | ✓ | bundled with Python | — |
| DeepSeek API or LM Studio (local) | Self-critique (`complete_chat`) + justify/retract (`stream_chat`) calls | Depends on runtime `.env`/local setup, same as Phases 2-4 | — | Same fallback already in place project-wide: DeepSeek cloud if LM Studio unreachable; `httpx.ConnectError` surfaces as "LM Studio is not running" per existing convention in `llm_client.py` |

No new external service dependency is introduced — this phase reuses the exact same two LLM backends already required and verified (or flagged as unverified — see A1 above) by Phases 2-4.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No (unchanged) | Existing HTTP-only session cookie auth from Phase 1, reused via `Depends(get_current_user)` |
| V3 Session Management | No (unchanged) | Same as above |
| V4 Access Control | Yes — but with a documented, intentional exception | Per-chat invariant endpoints MUST use `_get_chat_or_404(session, chat_id, current_user.id)` (IDOR-safe, matches existing task/memory endpoints). Global invariant endpoints deliberately have NO ownership check (D-02 — "every account has equal admin capability" is the project's stated access-control model, not a gap) |
| V5 Input Validation | Yes | `title`/`rule_text` length caps via `Field(max_length=...)` on both new tables, mirroring `Profile`'s 2000-char fields and `Task`'s 200/5000/2000-char fields; Pydantic request schemas (`GlobalInvariantCreate`, `ChatInvariantCreate`) enforce the same caps at the API boundary before they ever reach SQLModel |
| V6 Cryptography | No | Not applicable — no secrets/tokens introduced |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Stored XSS via invariant title/rule_text rendered in the sidebar | Tampering / Elevation of Privilege (script execution in another session) | Use `textContent` (never `innerHTML`) for invariant title/rule text in `renderInvariantsPanel`, exactly like `renderMemoryEntries`/`renderTaskPanel` already do for user-controlled strings |
| Stored/reflected XSS via LLM-generated justification text in the conflict banner | Tampering | If the banner renders through `renderMarkdown()` (for consistency with chat bubbles), it MUST pass through `DOMPurify.sanitize()` first, exactly like every other assistant-content render path in this app — LLM output is untrusted input from this app's threat-model perspective, same as user input |
| IDOR on per-chat invariant CRUD (editing/deleting another user's chat's invariants by guessing IDs) | Tampering / Information Disclosure | `_get_chat_or_404` ownership check before any per-chat invariant mutation, returning 404 (not 403) to avoid an existence oracle — identical to the existing `_get_task_or_404` pattern |
| Over-broad global invariant tampering (any user can delete/rewrite app-wide rules) | Tampering (by design) | Accepted risk, explicitly scoped by D-02/PROJECT.md as "every account has equal admin capability" for a single-deployment, handful-of-graders app — not a defect to fix, but should be documented in the plan so it isn't rediscovered as a "bug" during code review |

## Sources

### Primary (HIGH confidence)

- `C:\Projects\AiAdventAgentV2\CLAUDE.md` — project hard constraints, conventions, architecture (read in full per mandatory instructions)
- `C:\Projects\AiAdventAgentV2\shared\models.py` — existing SQLModel schema conventions (WorkingMemory/LongTermMemory scope-split precedent, FK-cascade patterns)
- `C:\Projects\AiAdventAgentV2\agent\context_engine.py` — `build_system_prompt()` injection point; `_extract_facts`/`_parse_facts_json` lenient-JSON-parse precedent
- `C:\Projects\AiAdventAgentV2\agent\ws.py` — `_handle_chat_message` full turn flow, existing tool-result follow-up call shape (lines 283-309)
- `C:\Projects\AiAdventAgentV2\agent\llm_client.py` — `complete_chat()` vs `stream_chat()` exact signatures/parsing, confirms no `response_format` parameter exists
- `C:\Projects\AiAdventAgentV2\agent\profile.py`, `agent\main.py` (profile/task endpoints) — UI-only CRUD REST precedent (D-03)
- `C:\Projects\AiAdventAgentV2\agent\tools.py` — confirms current `TOOL_REGISTRY` contents, verifying D-03 ("not touched by this phase") is accurate against the live codebase
- `C:\Projects\AiAdventAgentV2\ui\static\app.js`, `ui\static\index.html` — existing panel rendering (`renderMemoryPanel`, `renderTaskPanel`, `renderProfilePanel`) and sidebar markup, for the D-10/D-11 UI implementation pattern
- `C:\Projects\AiAdventAgentV2\tests\test_memory_ws.py`, `tests\test_task_ws.py`, `tests\test_cascade_delete.py`, `tests\test_settings_fallback.py`, `tests\conftest.py` — test structural precedent (respx SSE-mock helpers, cascade-delete assertions, global/per-chat fallback test shape, auth fixtures)
- `.planning/phases/05-invariants-day-14/05-CONTEXT.md`, `05-UI-SPEC.md` — locked decisions and UI contract
- `.planning/REQUIREMENTS.md`, `.planning/STATE.md` — requirement text (INV-01..05) and cross-phase decisions/open-question tracking

### Secondary (MEDIUM confidence)

None — no WebSearch or external documentation was needed for this phase; every technical seam is internal to this codebase.

### Tertiary (LOW confidence)

None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; every recommendation cites an existing, already-verified `requirements.txt` entry
- Architecture: HIGH — every seam (data model split, injection point, round-trip shape, REST/CRUD pattern, UI panel pattern) has a direct, current in-repo precedent read during this research session
- Pitfalls: MEDIUM-HIGH — Pitfalls 1, 2, 4 are derived from direct code/test inspection (HIGH); Pitfall 3 and the self-critique reliability caveat (Assumption A1) extrapolate from a single-call precedent (`_extract_facts`) to a not-yet-built second-call pattern, so flagged MEDIUM

**Research date:** 2026-09-20
**Valid until:** 30 days (stable, self-contained codebase; no fast-moving external dependency involved) — but Assumption A1 (LLM JSON reliability) should be spot-checked against whatever LLM backend is actually configured before the graded demo, independent of this expiry window
