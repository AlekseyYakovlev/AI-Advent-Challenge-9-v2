# Phase 8: Scheduler (Day 18) - Pattern Map

**Mapped:** 2026-09-26
**Files analyzed:** 27 (12 new source/test files + 15 modified)
**Analogs found:** 27 / 27 (2 are "partial": `agent/headless.py` and `agent/events.py`, see "No Exact Analog")

All line numbers below were read from the working tree on branch `Day18` (HEAD `597a796`). RESEARCH.md is the design source; this file only maps each new/changed file to the real code it must copy from.

## File Classification

### New source files

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `agent/schedule.py` | utility (pure schedule math + validation) | transform | `agent/tool_guard.py` (`build_clock_line`, pure helpers) + `agent/mcp_config.py` (pure `load_*` helpers) | role-match |
| `agent/scheduler.py` | service (poll loop, claim, recovery, executor) | batch / event-driven (background task) | `agent/mcp_config.py` (DB service shape) + `agent/main.py::lifespan` (lifecycle) + `shared/database.py::retry_on_locked_db` | partial |
| `agent/headless.py` | service (socket-free LLM+tool turn) | streaming (consumes `stream_chat`) | `agent/ws.py::_handle_chat_message` lines 664-873 + `_ToolTurn`/`_run_tool_rounds` | role-match (reuses the same helpers) |
| `agent/events.py` | provider (per-user hub) + WS handler | pub-sub / event-driven | `agent/ws.py::ws_chat` (lines 982-1037) + `active_connections` | role-match (NOT ownerless) |
| `agent/scheduler_api.py` | route (APIRouter, CRUD + lifecycle) | request-response / CRUD | `agent/main.py` MCP routes (lines 988-1170) + `_get_mcp_server_or_404` | exact (role), first router in repo |
| `agent/scheduler_tools.py` | service (3 LLM tools) | request-response | `agent/tools.py` `@register_tool` handlers (lines 261-433) | exact |

### Modified source files

| Modified File | Role | Change | Closest Analog (in-file or sibling) | Match Quality |
|---------------|------|--------|-------------------------------------|---------------|
| `shared/models.py` | model | + `ScheduledTask`, `TaskRun`, enums, partial unique index | `Task` (296-334), `McpServerConfig` (393-416), `WorkingMemory.__table_args__` (163) | exact |
| `shared/config.py` | config | + `SCHEDULER_*` settings | existing `MCP_*` fields (21-24) | exact |
| `shared/database.py` | config/migration | NO CHANGE needed (`create_all` picks up new tables) | `init_db` (224-232) | n/a |
| `agent/schemas.py` | model (Pydantic) | + `ScheduleTaskArgs`, `CancelScheduledTaskArgs`, `ListScheduledTasksArgs`, request/response models | `SaveLongTermMemoryArgs` (370-382), `TransitionTaskArgs` (436-450), `McpServerCreate`/`McpServerResponse` (214-284), `TaskResponse` (482-494) | exact |
| `agent/tools.py` | service | `dispatch_tool_calls(..., allowed_tools=None)` | own `unknown tool` branch (119-131) | exact |
| `agent/ws.py` | controller | `_ToolTurn.allowed_tools`; `_dispatch_round` pass-through; `_collect_memory_writes` exclusion; one-line ContextVar set | own code (332-345, 442-456, 547-553, 664-672) | exact |
| `agent/state.py` | store | + `current_chat_model: ContextVar[str | None]` | own dicts (11-13) | exact |
| `agent/main.py` | controller | `include_router`, `@app.websocket("/ws/events")`, lifespan start/stop, import scheduler_tools | `lifespan` (385-393), `@app.websocket` (1226-1230) | exact |
| `requirements.txt` | config | + `cronsim==2.7` under a new SCHEDULER heading | `mcp==1.30.0` block | exact |
| `ui/static/index.html` | component | + `#scheduler-panel` (after `#invariants-panel`, before `#agent-status`), 2 modals | `#task-panel` (105-114), `#invariants-panel` (115-154), `#add-user-modal` (354-384), MCP form (305-349) | exact |
| `ui/static/app.js` | component | scheduler state, render, actions, `/ws/events` client, modal wiring | `renderTaskPanel` (398-497), MCP block (1461-1845), `connectWs` (1114-1151), `bindEvents`/`init` (1997-2049) | exact |

### Tests and docs

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `tests/test_scheduler_schedule.py` | test (pure unit) | transform | `tests/test_tool_guard.py` | role-match |
| `tests/test_scheduler_service.py` | test | batch / DB | `tests/test_cascade_delete.py` (DB via `async_session_factory`) | role-match |
| `tests/test_scheduler_runner.py` | test | streaming (respx SSE) | `tests/test_memory_ws.py` (helpers 22-68), `tests/test_tool_rounds_ws.py::_stream_queue` (28-36) | exact (helper reuse) |
| `tests/test_scheduler_api.py` | test | request-response | `tests/test_scoping.py` (IDOR tests) | exact |
| `tests/test_scheduler_tools.py` | test | request-response | `tests/test_tools.py`, `tests/test_cascade_delete.py` | role-match |
| `tests/test_scheduler_events.py` | test | pub-sub / WS | `tests/test_ws_auth.py` | exact |
| `tests/test_scheduler_lifespan.py` | test | lifecycle | `tests/test_memory_ws.py` (`with TestClient(app)`) | role-match |
| `tests/conftest.py` (edit) | test config | - | own `clean_test_db` (22-43) | exact |
| `tests/test_scoping.py` (edit) | test | - | own `UNAUTH_ROUTES` (10-20) | exact |
| `tests/test_mcp_tools.py` (edit, line 333) | test | - | `len(build_tool_schemas()) == 6` becomes `== 9` | exact |
| `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md` (edit) | docs | - | existing sections | - |

## Pattern Assignments

### `shared/models.py` (model, CRUD) -- add `ScheduledTask`, `TaskRun`

**Analog:** `Task` (`shared/models.py` 296-334) for enum column + user FK; `McpServerConfig` (393-416) for user-scoped indexed FK; `WorkingMemory` line 163 for `__table_args__`.

**Import line to extend** (line 7 currently):
```python
from sqlalchemy import Column, Enum as SAEnum, ForeignKey, Integer, Text, UniqueConstraint
# add: Index, text   (partial unique index for one running run per task)
```

**User-scoped FK with CASCADE + index** (`McpServerConfig`, 397-404) -- copy for `ScheduledTask.user_id` and `TaskRun.user_id`:
```python
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
```

**Nullable FK with SET NULL** (`Chat.current_leaf_message_id`, 25-32; `Message.parent_id`, 57-64) -- copy for `origin_chat_id` (D-13), pointing at `chat.id` (no `use_alter` needed, `chat` is not a cycle here):
```python
    origin_chat_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
```

**Enum column (decision: use `SAEnum` with `values_callable`, matches repo)** (`Task.state`, 317-326). Stored literal is the lowercase value, so the partial index predicate `status = 'running'` matches:
```python
    state: TaskState = Field(
        default=TaskState.PLANNING,
        sa_column=Column(
            SAEnum(
                TaskState,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
```
Enum class shape to copy (`TaskState`, 286-293): `class X(str, Enum)` with `UPPER_CASE = "lower"` members. New enums: `ScheduleType(once|interval|cron)`, `ScheduledTaskStatus(active|paused|completed|cancelled)`, `RunStatus(running|success|failed|skipped)`, `RunTrigger(schedule|manual)`. Do NOT reuse `TaskState`/`Task` names (the word "task" is taken; D-14 naming rule).

**Timestamps and text columns** (`Task.created_at`, 329-334; `Message.tool_trace`, 68):
```python
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    tool_trace: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
```
Use `sa_column=Column(Text, nullable=True)` for `prompt`, `result_text`, `error`, `tool_trace`.

**`__table_args__`** (`WorkingMemory`, line 163): `__table_args__ = (UniqueConstraint("chat_id", "key", name="uq_working_memory_chat_key"),)`. Extend to a tuple with the partial index (verified in RESEARCH):
```python
    __table_args__ = (
        Index("uq_taskrun_one_running", "scheduled_task_id", unique=True,
              sqlite_where=text("status = 'running'")),
    )
```
Add a composite `Index("ix_scheduledtask_status_next", "status", "next_run_at")` on `ScheduledTask`. Default table names `scheduledtask` / `taskrun` (no clash with `task`/`tasktransition`). `TaskRun.scheduled_task_id` FK is `scheduledtask.id` `ondelete="CASCADE"` (like `TaskTransition.task_id`, 341-347).

**Migration:** none. `init_db` (`shared/database.py` 224-232) runs `SQLModel.metadata.create_all`; `migrate_*` helpers exist only for columns on pre-existing tables.

---

### `shared/config.py` (config)

**Analog:** the existing field block, lines 15-24. Append after `MCP_AUTO_CONNECT`:
```python
    MCP_CONNECT_TIMEOUT: float = 10.0
    MCP_TOOL_CALL_TIMEOUT: float = 30.0
    MCP_TOOL_RESULT_MAX_CHARS: int = 20000
    MCP_AUTO_CONNECT: bool = True
    # new:
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_POLL_INTERVAL: float = 1.0
    SCHEDULER_RUN_TIMEOUT: float = 120.0
    SCHEDULER_MIN_INTERVAL_SECONDS: int = 10
    SCHEDULER_LATE_THRESHOLD_SECONDS: float = 60.0
    SCHEDULER_MAX_CONCURRENT_RUNS: int = 2
    SCHEDULER_MAX_ACTIVE_TASKS_PER_USER: int = 50
```
Accessed everywhere as `from shared.config import settings` (see `agent/mcp_tools.py` line 23). `agent/main.py` imports it as `app_config` (line 66) to avoid clashing with the `Settings` model, so use `app_config.SCHEDULER_ENABLED` there.

---

### `agent/schemas.py` (Pydantic models)

**Analog for tool arg models:** `SaveLongTermMemoryArgs` (370-382) and `TransitionTaskArgs` (436-450). Use `Field(description=...)` because the model reads it; put constants at module top next to `TASK_TITLE_MAX_LENGTH` etc.
```python
class TransitionTaskArgs(BaseModel):
    """Tool-call arguments for transition_task."""

    task_id: int = Field(
        gt=0,
        description="The numeric id of an existing task in this chat",
    )
    new_state: LlmTaskState = Field(
        description="The lifecycle state to move the task into",
    )
    note: str = Field(default="", max_length=TASK_NOTE_MAX_LENGTH, description="...")
```
- `ScheduleTaskArgs`: `schedule_type: Literal["once","interval","cron"]` (`Literal` already imported line 6), `delay_seconds/run_at/interval_seconds/cron` optional, `prompt`, `title`, `max_runs`, plus `@model_validator(mode="after")` enforcing per-type fields (import `model_validator` from pydantic; `field_validator` is already imported at line 8).
- Do NOT give any tool arg an enum containing `"cancelled"` (`tests/test_tasks.py::test_cancel_is_not_an_llm_tool` scans property enums). Use `CancelScheduledTaskArgs(task_id: int = Field(gt=0, ...), user_requested_cancellation: bool)`; `ListScheduledTasksArgs` has no fields (empty model) or a benign optional field.

**Analog for REST request/response models:** `McpServerCreate` (214-240: `Field(min_length, max_length)` + `@field_validator` strip) and `TaskResponse` (482-494: `datetime` fields, nested list). New response models (`TaskOut`, `RunSummary`, `RunDetail`) must emit UTC-aware ISO: see the "Datetime serialization" shared pattern below.

---

### `agent/tools.py` (service) -- `allowed_tools` guard (Pitfall 1)

**Analog:** the in-file unknown-tool branch (lines 119-131). Add a keyword-only `allowed_tools: frozenset[str] | None = None` to the signature (77-84) and extend the condition; MCP bindings stay routed before it (line 98-101), so MCP names are unaffected.
```python
async def dispatch_tool_calls(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    tool_calls: list[dict[str, Any]],
    *,
    mcp_bindings: dict[str, McpToolBinding] | None = None,
    # new: allowed_tools: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
...
        if name not in TOOL_REGISTRY:          # new: `or (allowed_tools is not None and name not in allowed_tools)`
            results.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "ok": False,
                    "content": json.dumps({"error": f"unknown tool {name}"}),
                    "write": None,
                    "arguments": raw_arguments,
                    "mcp": None,
                },
            )
            continue
```
Result-dict shape (keys `tool_call_id, name, ok, content, write, arguments, mcp`) must not change; `serialize_tool_trace` and `_tool_call_frame` depend on it.

---

### `agent/scheduler_tools.py` (service, request-response) -- 3 LLM tools

**Analog:** `agent/tools.py` handlers, e.g. `_transition_task` (323-354) and `_save_long_term_memory` (278-292). Registry decorator and handler signature (25-47) are the contract:
```python
ToolHandler = Callable[[AsyncSession, int, int, dict[str, Any]], Awaitable[dict[str, Any]]]

@register_tool(
    "transition_task",
    TransitionTaskArgs,
    "Move an EXISTING task ... Requires the explicit numeric task_id ... there is no implicit 'current task'.",
)
async def _transition_task(session: AsyncSession, user_id: int, chat_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Transition a task owned by this chat/user; returns an error dict otherwise."""
    try:
        row = await tasks.transition_task(session, user_id, chat_id, args["task_id"], ...)
    except tasks.TaskNotFoundError:
        return {
            "status": "error",
            "code": "not_found",
            "error": f"task {args['task_id']} not found in this chat",
        }
```
Copy: (a) `{"status": "error", "code": ..., "error": ...}` for every failure (dispatcher line 166 turns `status == "error"` into `ok=False`); (b) success dict with `"status"` + `"id"`; (c) "explicit numeric task_id, no implicit current" wording for `cancel_scheduled_task` (D-14); (d) description text is the first cancel-gate layer.
- `origin_chat_id = chat_id` (handler arg). Model comes from the new `current_chat_model` ContextVar (see `agent/state.py`).
- Cancel gate layer 3 reads `Chat.current_leaf_message_id` then `Message.content` (leaf is the just-committed user message, see `_persist_user_message`, ws.py 177-196). Heuristic style for `user_asked_to_cancel(text)`: copy the regex-stem + negation-window approach from `agent/tool_guard.py` lines 91-103 (`_NEGATION_OR_FUTURE_RE`, `_is_negated_or_future`).
- Import `agent/scheduler_tools.py` from `agent/main.py` so `TOOL_REGISTRY` is populated (same reason `agent/tools.py` is imported via `agent/ws.py` line 54).
- Cancel is an owned-row lookup: scope by `user_id`, foreign/missing -> `not_found` (never distinguish).

---

### `agent/ws.py` (controller) -- surgical edits only

1. **`_ToolTurn`** (332-345): add last field with a default so existing constructors keep working:
```python
@dataclass
class _ToolTurn:
    websocket: WebSocket
    session: AsyncSession
    chat: Chat
    chat_id: int
    payload: MessagePayload
    llm_messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    toolset: McpToolset
    temperature: float
    max_tokens: int
    # new: allowed_tools: frozenset[str] | None = None
```
2. **`_dispatch_round`** (442-456): pass `allowed_tools=turn.allowed_tools` into `dispatch_tool_calls(...)`. It already reads only `turn.chat.user_id`, `turn.chat_id`, `turn.session`, `turn.toolset.bindings`.
3. **`_collect_memory_writes`** (547-553): extend exclusion (Pitfall 10):
```python
        if r["ok"] and r["write"] is not None and r["name"] not in TASK_TOOL_NAMES
        # -> not in NON_MEMORY_TOOL_NAMES   where NON_MEMORY_TOOL_NAMES = TASK_TOOL_NAMES + SCHEDULER_TOOL_NAMES
```
   `TASK_TOOL_NAMES` is defined at line 65; keep `_collect_task_writes` (556+) on `TASK_TOOL_NAMES` only.
4. **`_handle_chat_message`** (664-672): after the signature, one line `current_chat_model.set(payload.model)` (imported from `agent.state`, import block lines 27-32). `_run_tool_rounds` is awaited inline in the same task, so handlers see the value.
5. Do NOT rename or move `MAX_TOOL_ROUNDS` (imported from `agent.tool_guard`; tests do `monkeypatch.setattr("agent.ws.MAX_TOOL_ROUNDS", 3)`, `tests/test_tool_rounds_ws.py:124`) and do not touch `active_connections` / `broadcast_model_event` (ownerless, lines 67 and 965-979).

Reused unchanged by `agent/headless.py`: `_ToolTurn` (332), `_ToolRoundsResult` (348-358), `_stream_follow_up_with_empty_retry` (406-422), `_pick_nudge` (480-490), `_stream_nudge` (493-505), `_run_tool_rounds` (508-544), `_validate_origin` (124-152).

---

### `agent/state.py` (store)

**Analog:** the file itself (20 lines). Add a ContextVar next to the dicts (module docstring style: single line; imports stdlib only):
```python
import asyncio
from typing import Any

active_streams: dict[int, Any] = {}
ws_rate_limiter: dict[int, list[float]] = {}
chat_locks: dict[int, asyncio.Lock] = {}
# new: from contextvars import ContextVar
# new: current_chat_model: ContextVar[str | None] = ContextVar("current_chat_model", default=None)
```
Note `cleanup_chat_caches` (16-20) needs no change (jobs survive chat deletion, D-13). The `EventHub` is per-user, so it lives in `agent/events.py`, not here; `tests/conftest.py::clean_test_db` must clear it like it clears these dicts (conftest 36-38).

---

### `agent/headless.py` (service, streaming) -- `RecordingSink` + reused helpers

**Analog:** the chat turn in `agent/ws.py::_handle_chat_message` (664-873) -- copy its ORDER of operations, minus persistence and the WebSocket.

**Toolset build with failure isolation** (`_load_mcp_toolset`, ws.py 115-121) -- copy verbatim, log `error=type(exc).__name__` (CLAUDE.md: no str(exc) of MCP text; RESEARCH security table):
```python
async def _load_mcp_toolset(session: AsyncSession, user_id: int, chat_id: int) -> McpToolset:
    """Build the per-turn MCP toolset; an MCP problem must never break the chat turn."""
    try:
        return await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
    except Exception as exc:
        logger.warning("mcp_toolset_failed", chat_id=chat_id, error=str(exc))
        return McpToolset.empty()
```
Note `reserved=set(TOOL_REGISTRY)` -- pass the FULL registry so MCP names never collide with built-ins, even though only the allowlist is offered.

**System-prompt suffix order** (ws.py 729-738) -- copy exactly; `TOOL_USE_RULE` MUST stay last so `strip_tool_use_rule` (tool_guard.py 51-64) still works after round 1:
```python
suffix = "\n\n" + build_clock_line(datetime.now(timezone.utc).astimezone())
if tool_schemas:
    suffix += "\n\n" + MULTI_STEP_TOOL_HINT + "\n\n" + TOOL_USE_RULE
```
Base prompt: `build_system_prompt` (context_engine.py 233-298) needs a chat, so build a slim one: user's global `Settings.system_prompt or "You are a helpful assistant."` (query shape: `select(Settings).where(Settings.chat_id.is_(None), Settings.user_id == user_id)`, from `get_effective_settings` 218-223; read-only, do NOT create the row) + headless preface + `memory.list_long_term_memory(session, user_id)` rendered as `"Long-term memory (persists across all your chats): " + json.dumps({row.key: row.value ...})` (context_engine.py 260-266).

**Turn construction and driving** (ws.py 828-841, 746-765 first stream, 795-823 nudge, 859-872 fallback) -- feed a duck-typed sink:
```python
turn = _ToolTurn(
    websocket=RecordingSink(), session=session,
    chat=SimpleNamespace(user_id=user_id), chat_id=HEADLESS_CHAT_ID,   # 0
    payload=SimpleNamespace(model=model), llm_messages=messages,
    tool_schemas=schemas, toolset=toolset,
    temperature=temperature, max_tokens=max_tokens,
    allowed_tools=HEADLESS_TOOL_ALLOWLIST,
)
```
`schemas = [s for s in build_tool_schemas() if s["function"]["name"] in HEADLESS_TOOL_ALLOWLIST] + toolset.schemas` (build_tool_schemas: tools.py 59-74). Allowlist is `frozenset({"save_long_term_memory"})` -- an ALLOWlist, so future tools are excluded by default.

**Result shaping:** `serialize_tool_trace(rounds.results)` (context_engine.py 91-107) for `TaskRun.tool_trace`; `build_tool_fallback_summary(results, prompt)` (tool_guard.py 193-211) when the model produced no text; `MAX_TOOL_ROUNDS` enforced inside `_run_tool_rounds` (523).

**Timeout:** `async with asyncio.timeout(settings.SCHEDULER_RUN_TIMEOUT)` (project floor is Python 3.11, requirements.txt line 1; CLAUDE.md mandates `asyncio.wait_for`-style timeouts). Acquire the concurrency semaphore BEFORE the timeout block.

**Error mapping** (Russian, per RESEARCH): `httpx.ConnectError` -> `Модель недоступна: LM Studio не запущен` (same detection idea as `list_lm_studio_models`, main.py 1187-1193, which surfaces ConnectError as "LM Studio is not running"); `httpx.TimeoutException` -> `Тайм-аут ответа модели`; `TimeoutError` -> `Превышено время выполнения (120 с)`.

**Session hygiene:** `await session.commit()` before the first LLM call; own session from `async_session_factory()` (`shared/database.py` 27-31, `expire_on_commit=False`).

Coupling note: `headless.py` deliberately imports underscore names from `agent.ws`; add a module comment and a test that fails if a symbol disappears.

---

### `agent/events.py` (provider, pub-sub) -- `EventHub` + `/ws/events` handler

**Analog:** `agent/ws.py::ws_chat` (982-1037). Copy the pre-accept guard sequence and the receive loop; replace the chat-ownership check and the message handling.

**Pre-accept auth (copy 984-1000):**
```python
    if not _validate_origin(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
    async with async_session_factory() as db:
        user = await get_current_user_ws(session_id, db)
        if user is None:
            logger.warning("ws_auth_rejected", chat_id=chat_id, reason="no_session")
            await websocket.close(code=1008, reason="Unauthorized")
            return
    await websocket.accept()
```
Imports to copy (ws.py 23, 55-56): `from agent.dependencies import get_current_user_ws`, `from shared.auth import SESSION_COOKIE_NAME`, `from shared.database import async_session_factory`; `_validate_origin` from `agent.ws`.

**Receive loop with mandatory timeout (copy 1003-1011):**
```python
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="Idle timeout")
                break
    except WebSocketDisconnect:
        logger.info("ws_disconnected", chat_id=chat_id)
    finally:
        active_connections.discard(websocket)
```
For `/ws/events` use `receive_text()` with a 60 s timeout that `continue`s (client pings ~25 s) rather than closing; `finally` calls `hub.unsubscribe(user.id, queue)` and cancels the pump task.

**Anti-pattern to invert:** `broadcast_model_event` (ws.py 965-979) iterates an ownerless set. The hub must be `dict[int, set[asyncio.Queue]]` keyed by `user_id`; `publish` is sync `put_nowait` with drop-oldest so a dead browser never stalls `execute_run`. One writer task per socket serialises `send_json`.

**Route registration** (`agent/main.py` 1226-1230):
```python
@app.websocket("/ws/chat/{chat_id}")
async def websocket_chat_endpoint(websocket: WebSocket, chat_id: int) -> None:
    """WebSocket chat endpoint."""
    logger.info("ws_route_called", chat_id=chat_id)
    await ws_chat(websocket, chat_id)
```
Add a sibling `@app.websocket("/ws/events")` delegating to `ws_events`.

---

### `agent/scheduler_api.py` (route, CRUD) -- first `APIRouter` in the repo

**Analog:** MCP routes in `agent/main.py` 988-1170 (no router precedent; `main.py` uses `@app.*` only, so this file is the new shape -- keep decorator arguments identical).

**Route decorator with origin + content-type guards (main.py 1023-1033):**
```python
@app.post(
    "/api/v1/mcp/servers",
    response_model=McpServerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_mcp_server(
    body: McpServerCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    """Create an MCP server config for the current user."""
```
Delete/POST-without-body routes use only `dependencies=[Depends(require_allowed_origin)]` (main.py 1093-1097); GET routes use neither. Pause/resume/cancel/run are body-less POSTs, so `require_allowed_origin` only. Imports (main.py 15-19): `from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type`; `from shared.database import get_session`.

**404-not-403 ownership loader (main.py 178-191)** -- copy for `_get_scheduled_task_or_404` and `_get_run_or_404` (run ownership via `TaskRun.user_id`, denormalised):
```python
async def _get_mcp_server_or_404(session, user_id, server_id) -> McpServerConfig:
    """Load an MCP server config owned by user_id or raise HTTP 404 (never 403)."""
    row = await mcp_config.get_server(session, user_id, server_id)
    if row is None:
        logger.warning("mcp_server_access_denied", user_id=user_id, server_id=server_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id} not found",
        )
    return row
```
**Conflict mapping (main.py 777-794, pause):** illegal state -> `HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc`. Use 409 for pause of non-active, run-now while running.

**Validation errors:** raise `HTTPException(status_code=422, ...)` with the Russian `detail` strings from 08-UI-SPEC (main.py 167-175 uses literal `422` with a comment because the Starlette constant was renamed -- copy that).

**Response mapper style:** module-level `_x_to_response(row) -> XResponse` (main.py 194-210 `_mcp_server_to_response`, 312-335 `_task_to_response`). Put the mappers in `scheduler_api.py`.

**Wiring:** `from agent.scheduler_api import router as scheduler_router` and `app.include_router(scheduler_router)` in `main.py` before the `@app.websocket` routes.

**Delete of in-flight run (Pitfall 9):** cancel the run's `asyncio.Task` via the scheduler service (`run_id -> Task` map) and await it before `session.delete(row)`; commit/rollback per the pattern in the Shared Patterns section.

---

### `agent/schedule.py` (utility, transform) -- pure, no DB

**Analog:** pure helper style of `agent/tool_guard.py::build_clock_line` (173-182) and `agent/mcp_config.py::load_args/load_env` (17-36): small typed functions, single-line docstrings, no I/O, no logger needed.
```python
def build_clock_line(now: datetime) -> str:
    """Return the system-message line giving the current local date, time and UTC offset."""
    offset: timedelta = now.utcoffset() or timedelta(0)
```
Copy RESEARCH "Code Examples: Local-time cron -> UTC" (`_to_local_naive`, `_from_local_naive`, `validate_cron`, `next_cron_run`, `next_interval_run`). Rules that matter: enforce exactly 5 fields before `CronSim`; catch `(CronSimError, StopIteration)`; use `datetime.now(timezone.utc)` never `utcnow()`; injectable `tz`/`now` params for tests; `from cronsim import CronSim, CronSimError` (third-party group between stdlib and local imports).

---

### `agent/scheduler.py` (service, background batch)

**Analogs:** lifecycle from `agent/main.py::lifespan` (385-393); DB service shape from `agent/mcp_config.py` (create/update with commit + rollback); per-key async guards from `agent/state.py::chat_locks` idea (but the overlap invariant is the DB partial index, not a lock).

**Transaction pattern to copy for every write** (`mcp_config.create_server`, 118-125; CLAUDE.md rule):
```python
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("mcp_server_created", user_id=user_id, server_id=row.id, name=row.name)
```
**Atomic claim** (RESEARCH Pattern 1): `session.exec(update(ScheduledTask).where(id, status == ACTIVE, next_run_at == old).values(...))`, check `result.rowcount == 1`, `INSERT TaskRun` in the same commit, `except IntegrityError` (already imported in main.py line 11: `from sqlalchemy.exc import IntegrityError`) -> rollback -> retry once as `skipped`. Use `.is_(None)` (never `== None`) for `next_run_at IS NULL` in the finalize rule.

**Lock-retry helper available:** `shared/database.py::retry_on_locked_db` (44-65) retries `OperationalError` "locked" with backoff; wrap the tick's short bookkeeping transactions if you see lock contention with chat writes.

**Loop robustness** (RESEARCH Pattern 2): `except asyncio.CancelledError: raise` before `except Exception` (CLAUDE.md: CancelledError must propagate); keep strong refs to `create_task` results in a set; log keys `scheduler_tick_failed error=type(exc).__name__`, `scheduler_run_started task_id= run_id= user_id=`, etc. (`snake_case_action` + `key=value`; never log prompts/tool args).

**Lifespan wiring** (replace main.py 385-393):
```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize the database on startup and dispose the engine on shutdown."""
    logger.info("agent_starting")
    await init_db()
    # new: await scheduler.recover_orphaned_runs()      (always)
    # new: if app_config.SCHEDULER_ENABLED: await scheduler.start()
    yield
    logger.info("agent_shutting_down")
    # new: await scheduler.stop()                        (BEFORE mcp cleanup and engine.dispose)
    await mcp_client.cleanup_all_sessions()
    await engine.dispose()
```
Gate only `start()` behind `SCHEDULER_ENABLED`; `recover_orphaned_runs()` is cheap and always runs. Rationale (Pitfall 2): the supervisor hard-kills the Agent, so shutdown hooks may never run.

---

### `ui/static/index.html` (component)

**Analog for the panel:** `#task-panel` (105-114) -- copy the container/heading/fold-toggle skeleton exactly; insert new panel between line 154 (`</div>` of `#invariants-panel`) and line 155 (`#agent-status`):
```html
<div id="task-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-72">
    <h3 class="text-slate-300 font-semibold mb-2 flex items-center justify-between">
        <span>Задачи <span id="task-count" class="text-white font-semibold">0</span></span>
        <button type="button" data-fold-toggle="task-panel-body" aria-expanded="false"
            title="Свернуть/развернуть" class="text-slate-400 hover:text-white">▸</button>
    </h3>
    <div id="task-panel-body" class="hidden">
        <div id="task-list" class="space-y-2"></div>
    </div>
</div>
```
New ids per UI-SPEC: `scheduler-panel`, `scheduler-panel-body`, `scheduler-count`, `scheduler-running-badge` (badge span copies `#invariant-conflict-badge` line 117: `class="text-amber-400 font-semibold hidden"`, use `text-sky-400`), `scheduler-list`, full-width `+ Новое задание` button (copy indigo button classes from line 132, but per UI-SPEC use `font-semibold` not `font-medium`).

**Analog for both modals:** `#add-user-modal` (354-384) and `#settings-modal` shell (235-240):
```html
<div id="add-user-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60">
    <div class="bg-slate-900 border border-slate-700 rounded-xl shadow-xl w-full max-w-sm mx-4">
        <div class="flex items-center justify-between px-5 py-4 border-b border-slate-700">
            <h2 class="text-lg font-semibold">Добавить пользователя</h2>
            <button id="btn-close-add-user" aria-label="Закрыть" class="text-slate-400 hover:text-white text-xl leading-none">&times;</button>
        </div>
        <form id="add-user-form" class="p-5 space-y-4">
            <div>
                <label for="add-user-username" class="block text-sm text-slate-400 mb-1">Имя пользователя</label>
                <input type="text" id="add-user-username"
                    class="w-full rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500">
            </div>
            <p id="add-user-error" class="text-xs text-red-400"></p>
            <div class="flex justify-end gap-2 pt-2">
                <button type="button" id="btn-cancel-add-user" class="rounded-lg px-4 py-2 text-sm text-slate-400 hover:text-white transition">Отмена</button>
                <button type="submit" class="rounded-lg bg-indigo-600 hover:bg-indigo-500 px-4 py-2 text-sm font-medium transition">Создать пользователя</button>
            </div>
        </form>
    </div>
</div>
```
Create modal: `max-w-lg`, `#scheduler-create-modal`, error `<p id="scheduler-create-error" class="text-xs text-red-400">` (copies `#mcp-form-error`, line 336). Result modal: `max-w-2xl`, `#scheduler-run-modal`. Add `role="dialog" aria-modal="true" aria-labelledby` (new vs. existing modals; UI-SPEC requires it). The input class string above is the "focus:ring-2" variant; UI-SPEC wants `focus:outline-none focus:ring-2 focus:ring-indigo-500` (as in MCP inputs, lines 314-330). Do not use `py-1.5` (UI-SPEC spacing rule, even though line 146 uses it). No new `<script>` tags (CDN set unchanged).

---

### `ui/static/app.js` (component)

**State:** attach new state the way the MCP block does (1482-1486) rather than editing the big `state` literal (9-39):
```js
state.mcpServers = [];
state.mcpEditingId = null;
state.mcpConnecting = new Set();
state.mcpExpanded = new Set();
```
New: `state.lastSchedulerTasks = null; state.schedulerExpanded = new Set(); state.schedulerRuns = new Map(); state.eventsWs = null; state.eventsReconnectAttempt = 0; state.eventsShouldReconnect = true; state.schedulerPollTimer = null;`

**Label/badge maps** (copy naming from `TASK_STATE_LABELS`/`TASK_STATE_BADGE_CLASSES`, 41-55, and `MCP_STATUS_*`, 1463-1475): `SCHEDULER_JOB_STATUS_LABELS`, `SCHEDULER_RUN_STATUS_LABELS`, `SCHEDULER_*_BADGE_CLASSES` with values from the UI-SPEC color table.

**DOM builder helper:** reuse `mcpEl(tag, className, text)` (1488-1493) -- it sets `textContent`, so titles/prompts/errors are never injected as HTML. Do not create a second helper unless renamed to a shared one.

**Full re-render pattern** (`renderTaskPanel`, 398-497): `state.lastX === null` early return; `listEl.replaceChildren()`; per item build `card` with `'rounded-lg bg-slate-800 px-2 py-1'`; empty state; action buttons wired with `addEventListener('click', () => { action(id).catch((err) => showToast(err.message, 'error')); })`. Button class strings for neutral/accent/destructive are in `renderTaskPanel` 463-464, 473-474, 484-485 and `MCP_NEUTRAL_BTN_CLASSES` (1479); UI-SPEC changes weight to `font-semibold`.

**Expand/collapse that survives re-render:** `bindMcpFold(button, body, key, caret)` (1509-1525) with `state.mcpExpanded` -- copy as `bindSchedulerFold` using `state.schedulerExpanded` (Set of job ids).

**Action functions** (`cancelTask`/`pauseTask`, 499-528): native `confirm()` for destructive actions, `apiFetch(path, { method: 'POST' })`, re-fetch list, `showToast(..., 'success')`, catch -> action-error copy:
```js
async function cancelTask(taskId) {
    if (!confirm('Отменить эту задачу? Это действие нельзя отменить.')) return;
    try {
        await apiFetch(`/api/v1/tasks/${taskId}/cancel`, { method: 'POST' });
        await loadChatTasks(state.currentChatId);
        showToast('Задача отменена', 'success');
    } catch (err) {
        showToast('Не удалось отменить задачу. Проверьте соединение и попробуйте снова.', 'error');
    }
}
```
`apiFetch` (74-91) already sends cookies, redirects on 401, returns `null` on 204, and throws `Error(detail)` -- so server Russian `detail` strings surface via `err.message`. In-flight disable + `finally` re-enable: copy `saveMcpServer` (1751-1804: `state.mcpSaving` flag, `saveBtn.disabled`, `finally`).

**Markdown result:** `renderMarkdown(text)` (98-101, `marked.parse` + `DOMPurify.sanitize`) assigned via `innerHTML` ONLY for the result body; everything else `textContent` (UI-SPEC section 7).

**Timestamps:** `formatTaskTimestamp` (353-361) outputs day/month/year/hour/minute; UI-SPEC wants `dd.mm hh:mm` (no year) and seconds for run rows, so add one shared `formatSchedulerTimestamp(iso, withSeconds)`; requires server ISO with `Z`/offset (Pitfall 6).

**WebSocket client:** copy `connectWs` (1114-1151) and `scheduleReconnect` (1107-1112) for `/ws/events`, with these deliberate differences: no chat id; `onopen` resets `state.eventsReconnectAttempt`, calls `loadSchedulerTasks()`, stops the poll timer; `onclose` uses `STOP_RECONNECT_CODES.has(event.code)` (line 6, includes 1008) to stop and otherwise `scheduleReconnect`-style backoff `Math.min(1000 * 2 ** attempt, MAX_RECONNECT_DELAY)` (line 7); `onerror` must NOT call `showToast` (the chat socket does at 1148-1150; UI-SPEC forbids an offline banner); start a 10 s `setInterval` poll only while disconnected and the panel body is expanded; add a ~25 s client ping. Use `WS_BASE` (line 5).
```js
    ws.onclose = (event) => {
        if (STOP_RECONNECT_CODES.has(event.code)) {
            state.shouldReconnect = false;
            showToast(`Соединение закрыто: ${event.reason || event.code}`, 'error');
            return;
        }
```
(for events: keep the stop branch, drop the toast.)

**Modals:** existing open/close functions (1876-1886) are the pattern (`classList.remove('hidden')`, focus first field). Add backdrop-click handler like line 1997-1999 (`e.target === $('settings-modal')`) and extend the existing Escape handler (2000-2005) to also close the two scheduler modals; store `document.activeElement` to restore focus (new per UI-SPEC).

**Wiring:** button listeners in `bindEvents` next to the MCP ones (2024-2028); `setupFoldablePanels()` (564-575, called at 2029) already handles the new `data-fold-toggle` with no change; in `init()` (2032-2049) add `loadSchedulerTasks()` and `connectEventsWs()` after `await checkAgentHealth()` / `loadInvariants()`. Fetch models for the create form from `state.models` / `state.selectedModel` (already populated by `loadModels`, line 2036).

---

### `agent/main.py` (controller) -- integration edits only

- Imports: add `from agent import scheduler_tools  # noqa: F401` (registers tools) next to line 56 (`from agent import invariants, mcp_client, mcp_config, memory, profile, tasks`), the scheduler service import, `from agent.events import ws_events`, and the router import.
- Lifespan (385-393): see `agent/scheduler.py` section.
- Route registration: `app.include_router(...)` after the `CORSMiddleware` block (398-404); new `@app.websocket("/ws/events")` beside 1226-1230.
- Optional hardening (RESEARCH Pattern 5): in the logout handler (main.py 481-514) where `mcp_client.cleanup_user_sessions` is called, also close that user's event sockets.
- CORS is already `allow_credentials=True` for `CORS_ORIGINS` (398-404); no change.

---

### Tests

**`tests/conftest.py` (edit)** -- `os.environ.setdefault("DB_PATH", "test_app.db")` is at line 13, BEFORE `from agent.main import app` (line 17); add `os.environ.setdefault("SCHEDULER_ENABLED", "false")` on the next line so `with TestClient(app):` (lifespan) never starts a live poll loop (Pitfall 7). Clear the hub in `clean_test_db` next to lines 36-38:
```python
    agent_state.active_streams.clear()
    agent_state.ws_rate_limiter.clear()
    agent_state.chat_locks.clear()
    # new: hub._subs.clear()  (module-level state; stale queues from a closed loop break later tests)
```
**Fixtures to reuse:** `client`, `authenticated_client` (testuser), `second_authenticated_client` (otheruser; both expose `.seeded_user_id`), `seed_user`, `login_test_client(client)` (sync TestClient, returns user_id), `client.portal.call(async_fn, ...)` to run DB code inside the app loop. Fixtures at conftest 46-155.

**`tests/test_scheduler_api.py`** -- analog `tests/test_scoping.py`. IDOR pattern (test_scoping 83-114): create as A, access as B, assert 404 and that A's row survives via `async_session_factory()`:
```python
    resp = await second_authenticated_client.request(method, f"/api/v1/chats/{a_chat_id}{suffix}", json=body)
    assert resp.status_code == 404
    async with async_session_factory() as session:
        chat = await session.get(Chat, a_chat_id)
        assert chat is not None
```
Add new routes to `UNAUTH_ROUTES` (test_scoping.py 10-20, `(method, path, body)` tuples, e.g. `("GET", "/api/v1/scheduler/tasks", None)`), all must 401.

**`tests/test_scheduler_runner.py`** -- analog `tests/test_memory_ws.py`: import (do not copy) `_plain_content_response`, `_tool_calls_response`, `_queue_responses` (lines 27-68) and `tests/test_tool_rounds_ws.py::_stream_queue` (28-36; also answers the non-stream facts call). Decorate with `@respx.mock`, route `respx.post(f"{BASE_URL}/v1/chat/completions")` with `BASE_URL = settings.LM_STUDIO_BASE_URL`. A tool-call turn: first response `_tool_calls_response([("call_1", "save_long_term_memory", json.dumps({"key": "user_name", "content": "Alex"}))])`, second `_plain_content_response("...")`; assert a `LongTermMemory` row for the right `user_id` and that no `Message`/`Chat` rows were created and `chat_locks` is empty. Timeout test: `monkeypatch.setattr(settings, "SCHEDULER_RUN_TIMEOUT", 0.05)` with an async respx side effect that sleeps. Rounds cap: `monkeypatch.setattr("agent.ws.MAX_TOOL_ROUNDS", 3)` (test_tool_rounds_ws.py 124). Hallucinated `create_task` in a headless run must yield `ok=False`, "unknown tool".

**`tests/test_scheduler_events.py`** -- analog `tests/test_ws_auth.py`: `with TestClient(app) as client:` + `login_test_client(client)` (test_ws_auth 54-63); rejection = `client.cookies.clear()` or `client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")` then `pytest.raises(Exception)` with `getattr(exc_info.value, "code", None) == 1008` (66-90); origin header `{"Origin": "http://localhost:8000"}` (`WS_ORIGIN`). Bad-origin analog: `tests/test_ws_origin_validation.py`. Two-user isolation: two sync `TestClient`s or publish via `client.portal.call(hub.publish, user_id, frame)` and assert user B's socket receives nothing.

**`tests/test_scheduler_service.py` / `test_scheduler_tools.py`** -- analog `tests/test_cascade_delete.py`: direct row insert via `async with async_session_factory() as session:` (36-70), delete via REST, then `session.get(Model, id)`. Required checks: deleting a chat keeps the `ScheduledTask` and NULLs `origin_chat_id`; deleting the user cascades jobs and runs. Drive the scheduler by calling `tick(now)` / `execute_run(run_id)` directly with an injected `now`; never `asyncio.sleep`.

**`tests/test_mcp_tools.py` (edit)** line 333: `assert len(build_tool_schemas()) == 6` -> `== 9`. **Check** `tests/test_tasks.py::test_cancel_is_not_an_llm_tool` still passes (no `cancelled` in any tool property enum).

**Docs:** `docs/TESTING_GUIDE.md` requires a new "Scheduler" scenario block (CLAUDE.md: check before adding tests); `docs/API_SPEC.md` gets `/api/v1/scheduler/*` and `WS /ws/events`; `docs/ARCHITECTURE.md` gets the poll-loop description.

---

## Shared Patterns

### Authentication (REST)
**Source:** `agent/dependencies.py` 53-80 (`get_current_user`), 19-43 (`require_allowed_origin`, `require_json_content_type`)
**Apply to:** every `agent/scheduler_api.py` route
```python
current_user: User = Depends(get_current_user)        # 401 if no/expired cookie session
dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)]   # JSON POST/PUT
dependencies=[Depends(require_allowed_origin)]                                       # DELETE / body-less POST
```
Session extends its sliding expiry inside `get_current_user`; nothing else to add.

### Authentication (WebSocket)
**Source:** `agent/ws.py` 984-1000, `agent/dependencies.py` 83-107 (`get_current_user_ws`)
**Apply to:** `agent/events.py::ws_events`. Never raises; returns `None` -> close 1008. Origin check is `_validate_origin(websocket)` (ws.py 124-152).

### Ownership scoping, 404 not 403
**Source:** `agent/main.py` 112-125 (`_get_chat_or_404`), 178-191 (`_get_mcp_server_or_404`), `agent/mcp_config.py` 76-85 (`get_server`)
**Apply to:** all job/run REST routes, all 3 LLM tool handlers, event fan-out. Query by `user_id`, treat foreign == missing, log `*_access_denied` with ids only.
```python
    row = await session.get(McpServerConfig, server_id)
    if row is None or row.user_id != user_id:
        return None
```

### DB write: commit + rollback + log
**Source:** `agent/mcp_config.py` 118-125 (also `agent/main.py::delete_chat`, 603-608)
**Apply to:** every scheduler write. `session.add` -> `try: await session.commit()` -> `except Exception: await session.rollback(); raise` -> `logger.info("snake_case_event", key=value)`. Cascaded deletes go through `await session.delete(row)`, not raw SQL, so FK `ON DELETE CASCADE` fires under `PRAGMA foreign_keys=ON` (`shared/database.py` 34-41).

### Logging
**Source:** `agent/mcp_config.py` 9-12; `agent/ws.py` 57-60
**Apply to:** every new `agent/*.py` module. `from shared.logger import get_logger` then `logger = get_logger(__name__)` right after imports; keys are `snake_case_action` with `key=value` pairs (`task_id=`, `run_id=`, `user_id=`, `error=type(exc).__name__`). Never log prompts, tool args, results, secrets, or tracebacks.

### Datetime serialization (UTC-aware) -- NEW shared helper
**Source:** `agent/dependencies.py` 46-50 already has the idiom:
```python
def _as_aware_utc(value: datetime) -> datetime:
    """Normalize a naive SQLite-returned datetime to UTC-aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
```
**Apply to:** every scheduler response mapper and WS frame (SQLite returns naive; existing responses like `TaskResponse` emit naive strings, which JS `new Date()` reads as local time -- Pitfall 6). Reuse/promote this helper instead of writing another; add a unit test asserting the `+00:00`/`Z` suffix. Comparisons against stored values in the claim (`next_run_at == old`) work with aware or naive values (verified in RESEARCH).

### Error handling
**Source:** `agent/main.py` 1187-1198 (httpx errors), 777-794 (409 mapping); CLAUDE.md
**Apply to:** headless runner + REST. Specific exceptions only (`httpx.ConnectError`, `httpx.TimeoutException`, `httpx.HTTPStatusError`, `TimeoutError`, `asyncio.CancelledError` re-raised); no bare `except:`; loop-level `except Exception` only where the loop must never die, always preceded by `except asyncio.CancelledError: raise`.

### Type and style conventions for new Python
**Source:** CLAUDE.md "Code conventions" + `agent/mcp_config.py`
Single-line module docstring; `str | None` unions and built-in generics; all params/returns typed; import order stdlib -> third-party -> local (`from shared...` last); private helpers `_prefixed`; constants `UPPER_CASE`; `datetime.now(timezone.utc)` only; `Field(...)` FK cascades only via `sa_column=Column(Integer, ForeignKey(..., ondelete=...))`.

---

## No Exact Analog Found

| File / Concern | Role | Data Flow | Reason | Use Instead |
|----------------|------|-----------|--------|-------------|
| Background asyncio poll loop in `agent/scheduler.py` | service | batch | Nothing in `agent/` runs a background task inside the Agent process; the only loop-like code is `ui/supervisor.py::_healthcheck_loop` (UI process, HTTP polling, not DB) | RESEARCH Pattern 2 + `ui/supervisor.py` for the `create_task`/`cancel`/`gather` lifecycle idiom |
| Atomic claim (`UPDATE ... WHERE next_run_at = old`, `rowcount`) | service | CRUD | No optimistic-concurrency writes exist | RESEARCH Pattern 1 (prototype-verified on SQLModel 0.0.42); `select`/`session.exec` usage style from `agent/mcp_config.py` 66-73 |
| Per-user event hub (`dict[user_id, set[Queue]]`) | provider | pub-sub | Only `ws.active_connections` (ownerless, must not be reused) exists | RESEARCH Pattern 5 |
| `APIRouter` | route | request-response | `agent/main.py` registers everything on `app`; no router yet | Same decorator args as main.py MCP routes; FastAPI `APIRouter(prefix="/api/v1/scheduler", tags=[...])` |
| Cron next-occurrence | utility | transform | No date/cron math in repo | `cronsim==2.7` per RESEARCH (5-field enforcement is our code) |
| Partial unique index | model | - | Only `UniqueConstraint` exists (`WorkingMemory`, `LongTermMemory`) | `Index(..., unique=True, sqlite_where=text(...))` (RESEARCH, verified through `create_all`) |
| Frontend modals with focus return / `role="dialog"` | component | - | Existing modals have no aria roles or focus restore | 08-UI-SPEC section 5 "Modal behavior" |

## Conventions

Derivation via `gsd-tools verify conventions --derive` (the buildomator/bm plugin 4.7.3 module; the `gsd-plugin/bm` path in the agent template does not exist here). The tool only parses JS/TS sources: repo-wide it scanned `ui/static/app.js` alone and reported `skipped: no-readable-files` for `--scope agent` and `--scope tests` (Python is not analysed). Python conventions below are therefore taken from CLAUDE.md and confirmed by reading the analogs.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| file-name casing | snake_case (Python: `llm_client.py`, `mcp_config.py`, `test_<module>.py`); single JS file `app.js` | n/a (JS: insufficient-data, total 1) | n/a | named contract (CLAUDE.md) |
| identifier casing | snake_case functions/vars, `UPPER_CASE` constants, `PascalCase` classes (Python); camelCase (JS, derived: 96/96 = 100%) | 100% (JS derived) | 0 | named contract |
| export style | Python: direct `from agent.x import y`, no `__all__`; JS: none (single classic script, globals) | insufficient-data | n/a | named contract (CLAUDE.md "direct imports preferred") |
| import style | Python: stdlib -> third-party -> local, absolute from project root; JS: none | insufficient-data | n/a | named contract (CLAUDE.md) |

**Contested hotspots (author's choice):** The repo-wide CJS<->SDK dual resolver split (`bin/lib/**` CJS `module.exports`/`require` versus `sdk/src/**` ESM `export`/`import`) belongs to the GSD toolchain, not this project; there is no such split in AiAdventAgentV2. The only locally contested item is the file-scoped Python style: `agent/main.py` uses decorator-registered `@app.*` routes while the new `agent/scheduler_api.py` introduces an `APIRouter`; each file stays internally consistent and reviewers should match the directory's local style (inline `@app.*` in `main.py`, `@register_tool` in tool modules, `@router.*` only inside `scheduler_api.py`). Other Python choices, in particular the underscore-prefixed cross-module imports from `agent.ws` in `headless.py`, are deliberate and commented, not deviations to "fix".

## Metadata

**Analog search scope:** `agent/`, `shared/`, `ui/static/`, `tests/`, `requirements.txt`, `docs/` (listing only)
**Files read in full or by targeted range:** CLAUDE.md, 08-CONTEXT/RESEARCH/UI-SPEC, `agent/tools.py`, `agent/dependencies.py`, `agent/state.py`, `agent/mcp_config.py`, `agent/tool_guard.py`, `agent/ws.py` (56-226, 330-570, 660-880, 960-1042), `agent/main.py` (targeted ranges), `agent/schemas.py` (210-515), `agent/context_engine.py` (88-300), `agent/mcp_tools.py` (1-120, `build_mcp_toolset`), `shared/models.py`, `shared/database.py`, `shared/config.py`, `ui/static/index.html` (100-160, 230-390), `ui/static/app.js` (1-110, 350-600, 1100-1210, 1460-1540, 1648-1760, 1800-1900, 1990-2051), `tests/conftest.py`, `tests/test_scoping.py`, `tests/test_ws_auth.py`, `tests/test_memory_ws.py`, `tests/test_cascade_delete.py`, `tests/test_tool_rounds_ws.py` (helper)
**Files scanned:** ~40
**Pattern extraction date:** 2026-09-26
