# Phase 9: Auto-rename chats with LLM (Day 21) - Pattern Map

**Mapped:** 2026-10-02
**Files analyzed:** 8 new/modified code files (+ docs)
**Analogs found:** 8 / 8

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/titles.py` (NEW) | service | event-driven (fire-and-forget background task) + request-response (LLM) | `agent/context_engine.py` lines 621-688 (facts) + `agent/invariants.py::run_self_critique` | exact (role+flow) |
| `agent/state.py` (MOD: `title_tasks`) | store (in-memory state) | in-memory cache | itself (`chat_locks`, `cleanup_chat_caches`) | exact |
| `agent/ws.py` (MOD: one call near `done`) | controller (WS) | streaming | itself, lines 952-968 (`extract_and_update_facts` call) | exact |
| `ui/static/app.js` (MOD: dispatcher + `applyChatTitleUpdate`) | component | event-driven | `applySchedulerEvent` / `connectEventsWs` (2565-2624), `renderChatList` (1001) | exact |
| `tests/conftest.py` (MOD: clear `title_tasks`) | test config | - | itself, lines 40-43 | exact |
| `tests/test_titles.py` (NEW) | test | transform (pure unit) | `tests/test_invariants_ws.py` helper style (pure parts: none exist; use plain pytest fns) | role-match |
| `tests/test_titles_ws.py` (NEW) | test | request-response/WS + event | `tests/test_invariants_ws.py` (WS + respx) and `tests/test_scheduler_api.py:359-383` (hub frames) | exact |
| `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md` | docs | - | existing `/ws/events` sections | role-match |

## Pattern Assignments

### `agent/titles.py` (service, event-driven background task)

**Analog A:** `agent/context_engine.py` lines 621-688 (debounced background task registered in a module dict, fail-open LLM call, own session via `async_session_factory()`).

**Background task skeleton** (context_engine.py 621-633, 677-688):
```python
async def _run_debounced_facts(chat_id: int, model: str) -> None:
    try:
        await asyncio.sleep(FACTS_DEBOUNCE_SECONDS)
        ...
        async with async_session_factory() as session:
            await _extract_facts(session, chat_id, user_message, model)
    except asyncio.CancelledError:
        return
    finally:
        _debounce_tasks.pop(chat_id, None)

def extract_and_update_facts(session, chat_id, user_message, model) -> None:
    """Schedule debounced facts extraction."""
    ...
    task = asyncio.create_task(_run_debounced_facts(chat_id, model))
    _debounce_tasks[chat_id] = task
```
Titles: no debounce; guard `if existing is not None and not existing.done(): return`; use `title_tasks` from `agent.state` (not a private module dict) so `cleanup_chat_caches` can cancel it.

**Analog B (fail-open LLM call):** `agent/invariants.py` lines 285-308 and `context_engine.py` 644-654:
```python
try:
    raw = await llm_client.complete_chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.0,
        max_tokens=512,
    )
    return parse_critique_json(raw)
except Exception as exc:
    logger.warning("invariant_critique_failed", error=str(exc))
    return {"conflict": False}
```
Titles: same, but `messages=[system, user]`, `max_tokens=TITLE_MAX_TOKENS` (30), wrap in `asyncio.wait_for(..., TITLE_TIMEOUT_SECONDS)`, on exception/None/non-str -> `fallback_title(user_text)`. `complete_chat` signature (llm_client.py 40-63): `(messages, model, temperature=0.0, max_tokens=1024) -> str`, indexes `body["choices"][0]["message"]["content"]` (can raise KeyError/IndexError, content may be None).

**Imports pattern** (mirror scheduler.py 1-22 ordering: stdlib, third-party, local):
```python
import asyncio
import re

from sqlalchemy import update

from agent.llm_client import llm_client
from agent.state import title_tasks
from agent.tool_guard import TOOL_TRACE_HEADER
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import Chat

logger = get_logger(__name__)
```
Do NOT import `agent.ws`, and do NOT import `agent.events` at module level (events.py line 11 imports `agent.ws._validate_origin`; ws.py will import titles -> cycle). Note `agent/scheduler.py` line 11 does import `hub` at top level because scheduler is not imported by ws.py; titles IS (via ws.py), so use a function-local import:
```python
def _publish_title(user_id: int, chat_id: int, title: str) -> None:
    from agent.events import hub  # function-local: events -> ws -> titles cycle
    hub.publish(user_id, {"type": "chat_title_updated", "chat_id": chat_id, "title": title})
```

**Conditional UPDATE (race-safe set-once)** - copy exact call style from `agent/scheduler.py` 267-284 (`session.exec(update(...))`, commit/rollback/raise, `rowcount`):
```python
try:
    result = await session.exec(
        update(ScheduledTask).where(...).values(status=..., updated_at=now)
    )
    await session.commit()
except Exception:
    await session.rollback()
    raise
return result.rowcount == 1
```
Titles version: `update(Chat).where(Chat.id == chat_id, Chat.title == DEFAULT_CHAT_TITLE).values(title=title)`; imports `from sqlalchemy import update`, `session` is `AsyncSession` from `sqlmodel.ext.asyncio.session` (scheduler.py 6-9). `rowcount == 0` -> chat deleted/renamed -> log, no publish. Skip publish when `user_id is None` (legacy chats).

**Error handling:** `except asyncio.CancelledError: return`; `except Exception as exc: logger.warning("chat_title_failed", chat_id=chat_id, error=str(exc))`. Log `snake_case` keys, never user text or full title (CLAUDE.md logging rules).

**Other contents (no codebase analog; follow RESEARCH.md):** `clean_title`, `fallback_title`, `build_title_messages` (tag wrapping + tag-breakout `re.sub`), constants (`DEFAULT_CHAT_TITLE = "New Chat"`, 50 chars, 30 tokens, 20 s). `TOOL_TRACE_HEADER` lives in `agent/tool_guard.py` (imported in ws.py lines 41-54 area).

---

### `agent/state.py` (store, in-memory)

**Analog:** itself, lines 3-25:
```python
import asyncio
...
active_streams: dict[int, Any] = {}
ws_rate_limiter: dict[int, list[float]] = {}
chat_locks: dict[int, asyncio.Lock] = {}

def cleanup_chat_caches(chat_id: int) -> None:
    """Remove in-memory state keyed by the deleted chat."""
    active_streams.pop(chat_id, None)
    ws_rate_limiter.pop(chat_id, None)
    chat_locks.pop(chat_id, None)
```
Add `title_tasks: dict[int, asyncio.Task[None]] = {}` after `chat_locks`, and in cleanup: `task = title_tasks.pop(chat_id, None); if task is not None and not task.done(): task.cancel()`.

---

### `agent/ws.py` (controller, streaming) - single hook

**Analog:** itself, lines 952-968 (the facts call just before `done`):
```python
            extract_and_update_facts(
                session,
                chat_id,
                payload.content,
                payload.model,
            )
            stats = await compute_chat_stats(session, chat_id, payload.model)
            await websocket.send_json(
                {
                    "type": "done",
                    "message_id": assistant_msg.id,
                    ...
                },
            )
```
Insert the `schedule_title_generation(chat_id, chat.user_id, payload.content, assistant_text, payload.model)` call (guarded by `chat.title == DEFAULT_CHAT_TITLE and user_msg.parent_id is None`) right after `extract_and_update_facts(...)` and BEFORE `compute_chat_stats`/`send_json(done)` (RESEARCH Pitfall 5: `create_task` does not run until the next await, and a client disconnect cannot skip scheduling). Pass plain values only, never ORM `chat`/`session`.

**Imports:** ws.py line 15-22 imports from `agent.context_engine` (incl. `extract_and_update_facts`) in a parenthesized block; line 24 `from agent import invariants, tasks`. Add `from agent.titles import DEFAULT_CHAT_TITLE, schedule_title_generation` in alphabetical position among `agent.*` imports (after `agent.text_tool_calls`, before `agent.tool_guard`).

---

### `ui/static/app.js` (component, event-driven)

**Analog:** `applySchedulerEvent` (2565-2595) and `connectEventsWs` (2602-2624). Current `onmessage` (2616-2624):
```javascript
    ws.onmessage = (event) => {
        let frame;
        try {
            frame = JSON.parse(event.data);
        } catch {
            return;
        }
        applySchedulerEvent(frame);
    };
```
Change last line to `handleEventFrame(frame);` and add (before `applySchedulerEvent`, near line 2565) the `handleEventFrame` + `applyChatTitleUpdate` functions from RESEARCH.md "Frontend". Reason: `applySchedulerEvent` early-returns/reloads scheduler tasks for any frame (lines 2567-2570, `default: return`).

**Sidebar rendering** (renderChatList 1001-1015) already safe: `btn.textContent = chat.title;` - reuse by calling `renderChatList()` after mutating `state.chats`. Header: `$('chat-title').textContent = chat?.title || 'Чат';` (line 1060). Use `textContent` only (no innerHTML). Optional: `loadChats()` in events `ws.onopen` (line 2606-2614) for missed frames. `tests/test_static_js_syntax.py` must still pass.

---

### `tests/conftest.py` (test config)

**Analog:** itself, lines 40-43 - add `agent_state.title_tasks.clear()` next to `agent_state.chat_locks.clear()` (stale tasks bound to closed per-test loops).

---

### `tests/test_titles_ws.py` (test, WS + hub)

**Analog:** `tests/test_invariants_ws.py`.

**Imports/constants** (lines 3-18):
```python
import json
import httpx
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from shared.config import settings
...
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"
```
**Helpers to copy** (lines 26-36 `_plain_content_response` SSE body; 59-61 `_plain_json_response`; 74-83 `_send_and_drain`):
```python
def _plain_json_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

def _send_and_drain(ws, content: str) -> list[dict]:
    ws.send_json({"content": content, "model": MODEL})
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done":
            break
    return frames
```
Do NOT use `_queue_responses` (order-based, line 64) for title tests: use a `side_effect` that inspects `json.loads(request.content)["stream"]` (pattern in `tests/test_tool_rounds_ws.py::_stream_queue`) - SSE for stream=True, `_plain_json_response` for stream=False. Mock route: `respx.post(f"{BASE_URL}/v1/chat/completions")` with `@respx.mock` decorator (line 109-113). Create chats with `{"title": "New Chat"}` (existing tests use custom titles so they never trigger). Wait for the result by subscribing to the hub, not sleeping.

**Hub frame assertions** (analog `tests/test_scheduler_api.py` 359-383): `queue = hub.subscribe(user_id)`; owner-only: second user's queue must stay empty; `_drain(queue)` helper exists in that file (copy it). Async DB inspection helpers `_get_message`/`_list_messages` (test_invariants_ws.py 414-427) use `async with async_session_factory() as session`.

### `tests/test_titles.py` (test, pure unit)

No pure-unit analog among the WS tests; plain `def test_...` functions (pytest-asyncio auto mode so async tests need no decorator, as in test_scheduler_api.py). Cover `clean_title`, `fallback_title`, prompt tag-breakout, and the conditional UPDATE using `async_session_factory` (fixtures in conftest `clean_test_db` autouse). Monkeypatch `agent.titles.TITLE_TIMEOUT_SECONDS` for timeout tests.

---

## Shared Patterns

### Fail-open background LLM call
**Source:** `agent/invariants.py` 285-308, `agent/context_engine.py` 636-654
**Apply to:** `agent/titles.py`. `except Exception as exc: logger.warning("<snake_key>", ..., error=str(exc))`; never raise from a background task.

### Commit/rollback on writes
**Source:** `agent/scheduler.py` 267-284 (and CLAUDE.md)
**Apply to:** the conditional UPDATE in `titles.py`.
```python
try:
    ...
    await session.commit()
except Exception:
    await session.rollback()
    raise
```

### Per-chat in-memory state cleanup
**Source:** `agent/state.py` `cleanup_chat_caches` (called by `DELETE /api/v1/chats/{id}`; see `test_cascade_delete.py`)
**Apply to:** `title_tasks`.

### Per-user push
**Source:** `agent/events.py::hub.publish(user_id, frame)` (non-blocking, non-raising); consumed by `app.js::connectEventsWs`
**Apply to:** `titles.py` (function-local import) and app.js dispatcher.

### Logging
`logger = get_logger(__name__)` after imports; snake_case message keys with key=value; no user text, no tracebacks.

## Conventions

Convention derivation skipped (`gsd-tools.cjs` not resolvable in this environment: `CLAUDE_PLUGIN_ROOT` unset and plugin cache path not found). Conventions below are taken from `./CLAUDE.md` and observed in the analogs.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case (`.py`), lowercase | n/a (not derived) | n/a | named contract (CLAUDE.md) |
| Identifier casing | snake_case funcs/vars, PascalCase classes, UPPER_CASE constants, `_private` helpers | n/a | n/a | named contract (CLAUDE.md) |
| Export style | direct module-level definitions; no `__all__`; private helpers `_`-prefixed | n/a | n/a | named contract |
| Import style | absolute from project root (`from agent.x import y`), stdlib -> third-party -> local, parenthesized multi-imports | n/a | n/a | named contract |

**Contested hotspots (author's choice):** CJS<->SDK dual resolver (`bin/lib/**` CJS vs `sdk/src/**` ESM) is a GSD-tooling split not present in this Python repo; for this phase match the local directory style (Python in `agent/`, vanilla ES2015+ non-module JS in `ui/static/app.js` with 4-space indent and single quotes).

## No Analog Found

| File/Part | Role | Data Flow | Reason |
|-----------|------|-----------|--------|
| `clean_title` / `fallback_title` text sanitizers | utility | transform | No existing text-sanitization helper for LLM output (no `<think>` handling anywhere in `agent/`); use RESEARCH.md rules. Closest tangential: `agent/text_tool_calls.py`, `TraceLeakFilter` in `agent/tool_guard.py`. |
| Chat rename path | - | - | No rename endpoint/UI exists (out of scope). |

## Metadata

**Analog search scope:** `agent/`, `ui/static/app.js`, `tests/`
**Files read:** state.py, context_engine.py (605-688), invariants.py (283-327), scheduler.py (1-30, 260-288), ws.py (925-968 + imports), llm_client.py (38-63), app.js (1000-1030, 2560-2634), conftest.py, test_invariants_ws.py, test_scheduler_api.py (350-390)
**Pattern extraction date:** 2026-10-02
