# Stack Research

**Domain:** Multi-user auth + tool-call-driven agent memory + task state machine, added to an existing FastAPI/SQLModel/SQLite two-process app
**Researched:** 2026-09-19
**Confidence:** HIGH (auth/hashing — verified via PyPI + OWASP), MEDIUM (memory storage pattern — verified via SQLModel docs + DeepSeek/LM Studio tool-calling docs), HIGH (state machine choice — verified via python-statemachine docs, decision driven by existing codebase convention)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| `pwdlib[argon2]` | 0.3.1 (PyPI, Aug 2026) | Password hashing for Auth-01/Auth-02 | Passlib (the historically "default" choice, including in FastAPI's own tutorials) is unmaintained — no active releases, and it breaks under newer `bcrypt` (4.x) releases because it probes a removed `__about__.__version__` attribute. `pwdlib` is the modern, actively maintained successor built by the FastAPI-Users author specifically to replace passlib, with a near-identical `CryptContext`-style API (`PasswordHash.recommended()` / custom hasher list) but no legacy baggage. |
| `argon2-cffi` | 25.1.0 (transitive via `pwdlib[argon2]`) | Argon2id hashing backend | OWASP's primary recommendation in the Password Storage Cheat Sheet (2026 revision) for new applications: memory-hard, resists GPU/ASIC cracking far better than bcrypt. Use OWASP's minimum profile — `m=19456 KiB (19 MiB), t=2, p=1` — via `Argon2Hasher(memory_cost=19456, time_cost=2, parallelism=1)`; `pwdlib`'s `recommended()` preset already tracks OWASP guidance so the explicit params are only needed if you want to pin them. |
| Stdlib `secrets` + `hashlib` | Python stdlib | Session token generation and storage | `secrets.token_urlsafe(32)` for the cookie value, `hashlib.sha256(token).hexdigest()` for the value stored in the DB (never store the raw token — treat it like a password). Zero new dependencies; the existing code style (`shared/database.py`, `agent/state.py`) already favors stdlib over frameworks wherever stdlib suffices. |
| Server-side `Session` table (new SQLModel model) | — | Backing store for Auth-03 | Do **not** use Starlette's built-in `SessionMiddleware` (see "What NOT to Use"). Instead add a `Session` SQLModel table (`token_hash`, `user_id` FK, `created_at`, `expires_at`, `last_seen_at`) in `shared/models.py`, following the exact same pattern as `Chat`/`Message`/`Settings` — async SQLAlchemy queries, cascade delete on user removal. A dependency (`get_current_user`) looks up the hashed cookie value on every request; missing/expired → 401. This single pattern serves **both** REST (FastAPI `Depends`) and WebSocket (manual cookie read in `agent/ws.py::ws_chat`, since WS handshakes don't run through normal `Depends` the same way) — satisfying Auth-03's "valid for both REST and WebSocket" requirement without inventing a second auth mechanism. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| SQLModel `Column(JSON)` (already a dependency — SQLModel 0.0.22+/SQLAlchemy 2.x) | existing | Structured payload storage for MEM-01–MEM-04 memory layers | Add two new tables, not one polymorphic "memory" table: `WorkingMemoryItem` (scoped to `chat_id` + optional `task_id`, cleared/superseded as a task completes) and `LongTermMemoryItem` (scoped to `user_id`, persists across chats). Each row: typed columns for what you need to query/index (`key: str`, `category: str`, `chat_id`/`user_id`, `created_at`, `source="tool_call"`), plus one `value: dict = Field(sa_column=Column(JSON))` for the actual tool-call payload. This mirrors the existing `Settings.facts_json` pattern already in the codebase (`agent/context_engine.py`) — don't invent a new persistence style. |
| Pydantic 2.9+ `model_json_schema()` (already a dependency) | existing | Generating OpenAI-style tool/function schemas for MEM-03, TASK-03 | Define one Pydantic model per tool (e.g. `SaveLongTermMemory(key: str, category: str, value: dict)`, `CreateTask(title: str, description: str)`), then derive the JSON schema FastAPI already validates with, via `.model_json_schema()`, and hand it to the `tools` array in the chat-completion request body in `agent/llm_client.py`. Both backends confirm OpenAI-compatible tool calling: DeepSeek's `deepseek-chat` (V3) fully supports the `tools`/`tool_calls` format, and LM Studio (since 0.3.6) exposes the same OpenAI-compatible Tool Use API for any loaded model that supports it. No new HTTP/tool-calling library needed — this is an additive JSON payload on the existing SSE streaming call. |
| — | — | — | **SSE + tool-calls gotcha to design for up front:** in streaming mode, `tool_calls` arrive as fragmented deltas (the `arguments` string is chunked across SSE events, keyed by `index`) rather than one complete JSON blob — `agent/llm_client.py::stream_chat` will need to accumulate tool-call argument fragments by index before parsing, the same way it already accumulates `assistant_text`. This is a `llm_client.py` implementation detail, not a new dependency, but it's easy to miss and worth flagging for phase planning. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| None new | — | No linter/formatter/type-checker is configured in this repo today (confirmed in `.planning/codebase/STACK.md`) — this research does not introduce one; stay consistent with the existing "no build tooling" convention unless the user asks for it separately. |

## Installation

```bash
# Auth / password hashing
pip install "pwdlib[argon2]==0.3.1"

# No new packages needed for:
# - session tokens (stdlib secrets/hashlib)
# - memory storage (SQLModel/SQLAlchemy JSON columns — already a dependency)
# - tool-call schemas (Pydantic model_json_schema — already a dependency)
# - task state machine (hand-rolled — see below; zero new dependency)
```

Pin `pwdlib[argon2]==0.3.1` in `requirements.txt` alongside the existing pinned versions (project convention per `.planning/codebase/STACK.md`: "uses requirements.txt with pinned versions").

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| `pwdlib[argon2]` | `passlib[bcrypt,argon2]` | Never for new code — unmaintained, actively broken by `bcrypt>=4.1` (passlib probes a `__about__.__version__` attribute bcrypt removed), and known to stop working under Python 3.13+. Only relevant if migrating an *existing* passlib-hashed user table (`pwdlib` doesn't read passlib's hash format out of the box). |
| `pwdlib[argon2]` | Raw `argon2-cffi` (no wrapper) | Fine if you don't want any abstraction layer at all — `pwdlib` is a thin wrapper (hasher selection, upgrade-on-verify), not a heavy framework, so this is a "which thin layer" choice, not "wrapper vs no wrapper." Recommended `pwdlib` mainly for the built-in multi-hasher fallback (useful if you ever add bcrypt as a legacy-verify path). |
| Server-side `Session` table + opaque cookie | Starlette `SessionMiddleware` (`itsdangerous`-signed, client-side cookie) | Only for read-mostly, low-sensitivity session data where you're fine with *no server-side revocation* (a stolen/leaked signed cookie stays valid until it expires — you cannot force-invalidate a single session). Given Auth-02 (any user can create new accounts) and Auth-04 (all data scoped to `user_id`), you want the ability to invalidate a session (e.g. account changes) — that requires server-side state, which `SessionMiddleware` doesn't provide. |
| Hand-rolled `Enum` + transition-table dict for the task FSM | `python-statemachine` (3.2.1, pure-Python, zero hard dependencies) | Reach for `python-statemachine` if the state graph grows past the current 4 linear states + pause/resume (e.g. once TASK-03's "future delegation to subagents" actually lands and states become hierarchical/parallel). It has real advantages worth knowing about: transitions are validated for structural consistency at class-definition time (typos in the transition table become import-time errors, not runtime bugs), it raises a distinct `TransitionNotAllowed` exception (maps directly to TRANS-02's "clear, explainable rejection"), and it has first-class **history pseudo-states** — built specifically for "paused, then resumed into whatever state it was in before" (TASK-04), which is exactly the resume-without-re-explaining-context requirement. It's not overkill in dependency-weight terms (pure Python, no transitive deps) — the reason to *not* default to it now is scope, not weight: 4 states is small enough that a `dict[TaskState, set[TaskState]]` plus one `TaskStateTransition` log table is fully transparent, easy to unit test exhaustively (all 4×4=16 pairs), and matches this codebase's existing convention of doing things directly rather than through a library (deterministic slicing in `context_engine.py`, hand-written tree walks instead of an ORM graph library, etc.). If Day 13+ scope discussion reveals the pause/resume history requirement is non-trivial (e.g. resuming needs to restore more than "last state," like partial validation results), revisit — `python-statemachine`'s history states would remove real hand-rolled complexity at that point. |
| Hand-rolled FSM | `transitions` (pytransitions) | Not recommended over `python-statemachine` if you do pick a library — `transitions` does not validate transition-table consistency at definition time (typos fail silently or at run time instead), which is a worse fit for TRANS-01/TRANS-02's "explicit, enforced, explainable" requirement. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|--------------|
| `fastapi-users` | Full-featured multi-backend auth framework (OAuth, JWT, email verification, RBAC, routers) — all of which is out of scope per `PROJECT.md` ("OAuth / external identity providers" and "fine-grained roles" are explicitly Out of Scope; every user is "admin"). Pulling it in for a single-role, cookie-only app means fighting its opinionated router/dependency structure to get the one narrow thing you need. | Hand-rolled `Session` SQLModel table + a `get_current_user` FastAPI dependency, ~80-120 lines total — matches the app's existing "small, explicit, stdlib-first" style. |
| `passlib` (any hasher) | Unmaintained; breaks under `bcrypt>=4.1`; will not survive a future Python 3.13+ upgrade without patching. Do not add it even though FastAPI's own tutorial still references it — that tutorial is known-stale on this point. | `pwdlib[argon2]` |
| Starlette `SessionMiddleware` | Client-side signed cookie — all session data lives in the cookie itself (size-limited, cannot be revoked server-side, and mixes "session" concerns with the app's actual per-user data model). Also encourages putting `user_id` *and* arbitrary session data straight in a cookie value, which is a bad habit once profiles/memory get more complex. | Opaque random token cookie + server-side `Session` table (see Core Technologies) |
| JWT in a cookie or `localStorage` | `PROJECT.md` explicitly constrains this to "HTTP-only session cookie (not JWT/localStorage)." JWTs stored client-side can't be revoked before expiry and add unneeded complexity (claims, signing keys, expiry skew) for a single-deployment, no-external-consumer app. | Opaque session token, as above |
| `langchain` / `instructor` / `pydantic-ai` for tool-call orchestration | These wrap the LLM call itself (their own streaming/session abstractions), which would fight the existing hand-written SSE streaming in `agent/llm_client.py::stream_chat` rather than extend it — you'd end up maintaining two competing streaming code paths. Also large dependency surfaces for what's a straightforward "add a `tools` array + parse `tool_calls` deltas" change. | Pydantic `model_json_schema()` to build tool defs, plain `tools=[...]` in the existing httpx request payload, manual delta accumulation in `stream_chat` (same pattern already used for `assistant_text`) |
| A single polymorphic `Memory` table with a `layer` enum column covering all three tiers | MEM-02 requires long-term and working memory to be in **dedicated** tables (not folded together), and short-term is explicitly *the message tree itself* (no new storage). A single `Memory` table with a `layer` discriminator re-creates the "everything in one undifferentiated store" anti-pattern the milestone is designed to avoid (see `PROJECT.md`'s Core Value). | Two dedicated tables: `WorkingMemoryItem`, `LongTermMemoryItem` |

## Stack Patterns by Variant

**If the pause/resume requirement (TASK-04) turns out to need more than "resume to last known state":**
- Switch the task FSM from hand-rolled to `python-statemachine` 3.2.1
- Because its history pseudo-states solve "resume into whatever state/sub-state a task was in" natively, instead of you hand-rolling a `paused_from_state` column and re-deriving allowed-next-transitions manually

**If the app is ever deployed behind HTTPS (not `localhost` dev):**
- Set the `Session` cookie's `secure=True` (only send over TLS) and keep `samesite="lax"`
- Because in local dev over plain `http://localhost`, `secure=True` would silently prevent the cookie from ever being set — gate this off a `COOKIE_SECURE` setting in `shared/config.py` (mirrors how `DEEPSEEK_API_KEY`/`LM_STUDIO_BASE_URL` are already environment-driven), defaulting to `False` for local dev

**If both UI (port 8000) and Agent (port 8001) need the same session cookie (they do, per Auth-03):**
- Set the cookie without a `Domain` attribute (host-only, defaults to `localhost`) and without a `Path` restriction narrower than `/`
- Because cookie scoping in browsers is host+path based, not port based — a cookie set for `localhost` is sent to both `localhost:8000` and `localhost:8001` automatically, so the existing two-port split needs no special cross-port cookie relay logic

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| `pwdlib[argon2]==0.3.1` | Python 3.9+ (matches existing FastAPI/SQLModel floor) | `argon2-cffi` 25.1.0 (pulled in transitively) dropped Python 3.7 support; confirm the project's actual Python floor in `run.py`/CI before pinning — `.planning/codebase/STACK.md` lists "Python 3.8+" as the documented floor, which would conflict with `argon2-cffi` 25.1.0's floor. If the project truly still supports 3.8, pin `argon2-cffi<25` explicitly; otherwise bump the documented floor to 3.9+ as part of this milestone. |
| `python-statemachine==3.2.1` (if adopted later) | Python 3.9–3.14, zero hard runtime dependencies | Safe to add at any point without a dependency-resolution fight — it doesn't pull in anything that could collide with FastAPI/SQLModel/Pydantic/SQLAlchemy pins. |
| SQLModel JSON columns | SQLite (existing engine) | SQLite's `JSON` column type is stored as `TEXT` under the hood with SQLite's built-in JSON1 functions (`json_extract`, etc.) available for future querying — no extension/pragma needed, works today with the existing `aiosqlite` driver and `PRAGMA` set already in `shared/database.py`. |
| `pwdlib` password hashes | Existing `Settings`/`Chat`/`Message` tables | No interaction — password hashes live only in the new `User` table; no schema coupling to existing models beyond adding `user_id` FKs per Auth-04. |

## Sources

- [pwdlib PyPI](https://pypi.org/project/pwdlib) — version 0.3.1, Aug 2026 release, argon2/bcrypt extras — HIGH confidence
- [pwdlib guide (frankie567.github.io)](https://frankie567.github.io/pwdlib/guide/) — API shape (`PasswordHash.recommended()`, multi-hasher construction) — HIGH confidence
- [Introducing pwdlib — François Voron](https://www.fvoron.com/blog/introducing-pwdlib-a-modern-password-hash-helper-for-python/) — rationale for replacing passlib — MEDIUM confidence (author's own blog, cross-checked against PyPI discussion)
- [passlib seems not maintained anymore — fastapi/fastapi Discussion #11773](https://github.com/fastapi/fastapi/discussions/11773) — confirms passlib staleness and FastAPI docs known-stale reference — MEDIUM confidence
- [argon2-cffi PyPI](https://pypi.org/project/argon2-cffi/) — version 25.1.0, Python 3.13/3.14 support, dropped 3.7 — HIGH confidence
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) — Argon2id `m=19456,t=2,p=1` minimum profile — HIGH confidence (authoritative source)
- [itsdangerous PyPI](https://pypi.org/project/itsdangerous/) — version 2.2.0, Apr 2024 (used by Starlette `SessionMiddleware`, confirmed NOT recommended here) — HIGH confidence
- [python-statemachine PyPI](https://pypi.org/project/python-statemachine/) — version 3.2.1, Aug 2026 — HIGH confidence
- [python-statemachine async support docs](https://python-statemachine.readthedocs.io/en/latest/async.html) — async engine auto-selection, relevant if FSM transitions are awaited from `agent/ws.py` — MEDIUM confidence
- [python-statemachine — Coming from pytransitions](https://python-statemachine.readthedocs.io/en/v3.0.0/how-to/coming_from_transitions.html) — structural validation vs `transitions` library — MEDIUM confidence
- [DeepSeek API — Function Calling guide](https://api-docs.deepseek.com/guides/function_calling) — confirms `tools`/`tool_calls` OpenAI-compatible support on `deepseek-chat` (V3) — HIGH confidence (official docs)
- [LM Studio — Tool Use docs](https://lmstudio.ai/docs/developer/openai-compat/tools) — confirms OpenAI-compatible Tool Use API since LM Studio 0.3.6 — HIGH confidence (official docs)
- [SQLModel JSON Fields discussion #1925](https://github.com/fastapi/sqlmodel/discussions/1925) — `Field(sa_column=Column(JSON))` pattern — MEDIUM confidence (community-verified pattern, consistent with existing `Settings.facts_json` in this codebase)
- `.planning/codebase/STACK.md`, `.planning/codebase/ARCHITECTURE.md` — existing app conventions (FastAPI/SQLModel/SQLAlchemy 2.x async, structlog, pydantic-settings, `Settings.facts_json` JSON pattern, no build tooling) — HIGH confidence (primary source, already-verified project state)

---
*Stack research for: multi-user auth + agent memory + task state machine, added to AiAdventAgentV2*
*Researched: 2026-09-19*
