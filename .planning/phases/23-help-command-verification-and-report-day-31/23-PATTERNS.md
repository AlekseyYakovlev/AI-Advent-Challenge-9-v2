# Phase 23: /help command, verification and report (Day 31) - Pattern Map

**Mapped:** 2026-10-13
**Files analyzed:** 22 new/modified files
**Analogs found:** 21 / 22 (one partial: the control-set fixture has no JSON-fixture analog beyond the Day 22-25 frozen sets)

All line numbers refer to the live code read on 2026-10-13. Prefer these excerpts over the generic snippets in RESEARCH.md.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `shared/models.py` (add `HelpConfig`) | model | CRUD | `shared/models.py::ChatRagConfig` (745-777) | exact |
| `agent/help_command.py` (new: parse, texts, prepare, finalize) | service | request-response + transform | `agent/rag_turn.py` (`prepare_rag_turn`, `finalize_rag_turn`) | role-match |
| `agent/help_api.py` (new: `GET/PUT /api/v1/help/config`) | route | CRUD | `agent/rag_api.py` (35-204) | exact |
| `agent/main.py` (include router) | config | n/a | `agent/main.py` 473-480 (`include_router`) | exact |
| `agent/ws.py` (early `/help` branch; extracted shared middle) | controller (turn) | streaming + event-driven | `agent/ws.py::_run_chat_turn` (825-1190) and `_complete_gated_turn` (737-822) | exact (same file) |
| `agent/ws.py::ws_chat` (rate slot deferral) | controller | request-response | `agent/ws.py::ws_chat` (1259-1320) | exact |
| `agent/http_chat.py` (skip `apply_chat_kb`, notice mapping, `help` field) | route | request-response | `agent/http_chat.py` (88-208) | exact (same file) |
| `agent/schemas.py` (`HttpChatResponse.help`) | model (DTO) | request-response | `agent/schemas.py::HttpChatResponse` | exact |
| `agent/mcp_tools.py` (`only_server_ids` filter) | service | request-response | `agent/mcp_tools.py::build_mcp_toolset` (225-255) | exact (same function) |
| `ui/static/index.html` (`#help-section`) | component (markup) | CRUD | `ui/static/index.html` `#mcp-section` (342-386) | role-match |
| `ui/static/app.js` (settings section, KB badge, label chip, notice) | component | CRUD + event-driven | `saveChatRag` (5003), `renderRagSearchPopover` (4904), `buildKbCard` (4173), `handleWsMessage` (1750), `renderMessages` (170-225), `buildRagQuotesBlock` (5409) | role-match |
| `tests/test_help_command.py` | test | transform | `tests/test_http_chat_rag.py` fixtures + pure-function style | role-match |
| `tests/test_help_api.py` | test | CRUD | `tests/test_http_chat_rag.py::_save_config` + `authenticated_client` | role-match |
| `tests/test_help_ws.py` | test | streaming | `tests/test_rag_ws.py`, `tests/test_mcp_chat_ws.py` | role-match |
| `tests/test_help_http.py` | test | request-response | `tests/test_http_chat_rag.py` | exact |
| `tests/test_help_ui.py` | test | source-guard | `tests/test_kb_folder_ui.py` | exact |
| `tests/test_docs_help.py` | test | source-guard | `tests/test_docs_day30.py` | exact |
| `tests/fixtures/help/control_set.json` | fixture | batch | Day 22-25 frozen scenario files loaded by `scripts/rag_dialog.py::load_scenarios` | partial |
| `scripts/help_eval.py` | utility (script) | batch + request-response | `scripts/rag_dialog.py` (copy/start/login) + `scripts/rag_judge.py` + `scripts/verify_service.py` | role-match |
| `scripts/e2e_help_playwright.py` | utility (script) | event-driven | `scripts/e2e_mcp_git_playwright.py` + `scripts/e2e_kb_playwright.py` helpers | exact |
| `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md`, `docs/TESTING_GUIDE.md` | docs | n/a | Day 30 sections in the same files (guarded by `tests/test_docs_day30.py`) | exact |
| `Day31_report.md`, `eval_out/day31/` | docs/artifact | batch | `Day30_report.md`, `Day25_report.md`, `eval_out/day30` | exact |

## Pattern Assignments

### `shared/models.py` - `HelpConfig` (model, CRUD)

**Analog:** `shared/models.py::ChatRagConfig` (lines 745-777). `HelpConfig` must keep the attribute names `candidate_k, top_k, threshold, lexical, llm_rerank, hybrid, rewrite` because `config_from_row` (rag_pipeline.py:109-124) reads exactly those attributes and works with any object that has them.

**FK rule (CLAUDE.md: `sa_column`, never `Field(ondelete=...)`)** (lines 756-766):
```python
chat_id: int = Field(
    sa_column=Column(
        Integer, ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True
    )
)
kb_id: int | None = Field(
    default=None,
    sa_column=Column(
        Integer, ForeignKey("knowledgebase.id", ondelete="SET NULL"), nullable=True
    ),
)
```
Copy shape: `user_id` primary key with `ForeignKey("user.id", ondelete="CASCADE")` (same as `ChatTaskMemory.user_id`, lines 788-794), `kb_id` with `SET NULL` so deleting a base clears the mark (`kb_indexer.delete_kb`, line 700, removes the row with a Core DELETE; PRAGMA foreign_keys=ON does the rest).

**Field block to mirror** (lines 767-777): `top_k=5`, `candidate_k=20`, `threshold: float | None`, four bool stage flags, `updated_at` with `datetime.now(timezone.utc)`. Drop `mode`, `strict`, `history_turns`. Add `mcp_server_ids_json: str | None`, `tools_memory`, `tools_tasks`, `tools_scheduler`.

**Table creation:** new tables are created by `SQLModel.metadata.create_all` in `shared/database.py::init_db` (line 439). No ALTER migration needed; just define the class in `shared/models.py` (it must be imported before `create_all`, which it is because `shared.models` is already imported there).

---

### `agent/help_api.py` (route, CRUD)

**Analog:** `agent/rag_api.py`

**Imports pattern** (lines 3-25):
```python
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.kb_api import MSG_KB_NOT_FOUND, _get_owned_kb, _unprocessable
from agent.rag import (DEFAULT_CANDIDATE_K, calibrated_threshold, resolve_threshold)
from shared.database import get_session
from shared.logger import get_logger
from shared.models import Chat, ChatRagConfig, KbChunk, KbStatus, KnowledgeBase, User

logger = get_logger(__name__)
router = APIRouter(tags=["rag"])
```

**Validation model** (lines 35-48): ranges `top_k` 1-20, `candidate_k` 1-50, `threshold` 0.0-1.0, `StrictBool | None` for flags. Reuse these exact bounds for `HelpConfigIn`. Omit `strict`, `history_turns`, `mode`.

**Default-GET branch when no row** (lines 85-96, `_config_out`): returns a fixed default object; for `/help` use the same defaults the turn uses (threshold 0.45 per RESEARCH Pitfall 1) so the UI and the turn agree. Resolve `kb_name`/`kb_status` only for a KB owned by the caller (lines 97-103):
```python
kb: KnowledgeBase | None = None
if row.kb_id is not None:
    kb = await session.get(KnowledgeBase, row.kb_id)
    if kb is not None and kb.user_id != chat.user_id:
        kb = None
model_id = kb.embedding_model if kb is not None else None
effective, source = resolve_threshold(row.threshold, model_id)
```

**Partial-update semantics** (lines 127-140, `_apply_search_settings`): `body.model_fields_set` so an explicit `null` threshold resets to calibrated while an omitted field stays unchanged; `row.candidate_k = max(row.candidate_k or DEFAULT_CANDIDATE_K, row.top_k)`.

**PUT handler with ownership check, commit/rollback** (lines 155-204):
```python
@router.put(
    "/api/v1/chats/{chat_id}/rag",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def put_rag_config(chat_id: int, body: RagConfigIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)) -> RagConfigOut:
    ...
    if body.kb_id is not None:
        kb = await _get_owned_kb(session, body.kb_id, current_user.id)
        if kb.status != KbStatus.READY:
            raise _unprocessable(MSG_KB_NOT_READY)
    try:
        row = await session.get(ChatRagConfig, chat_id)
        if row is None:
            row = ChatRagConfig(chat_id=chat_id)
            session.add(row)
        ...
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
    except SQLAlchemyError:
        await session.rollback()
        raise
    logger.info("rag_config_updated", chat_id=chat_id, ...)
```
Adaptations: key is `current_user.id` (no chat id); also verify every chosen MCP server id belongs to the caller (list via `mcp_config.list_servers(session, user_id)`, as `build_mcp_toolset` does) and answer 404 not 403 for foreign ids. Ready check is `status == KbStatus.READY` only (a base with `phase="adding"` stays READY, which matches CONTEXT discretion).

**Router registration:** `agent/main.py` lines 86-89 import and 473-480 `app.include_router(...)`. Add `from agent.help_api import router as help_router` next to `rag_router` and `app.include_router(help_router)` after `app.include_router(rag_router)`.

---

### `agent/help_command.py` (service, request-response + transform)

**Analog:** `agent/rag_turn.py` (prepare/finalize). The `/help` variant must NOT call `prepare_rag_turn` (it reads `ChatRagConfig` by chat id and may return a gated `reply_text`).

**Imports pattern** (rag_turn.py lines 3-41): stdlib, then `sqlalchemy.exc`, `sqlmodel.ext.asyncio.session`, then `agent.rag` names (`MODE_RAG`, `RagFailure`, `build_rag_block`, `build_rag_payload`, `merge_rag_block`, `rag_budget`, `sources_from_chunks`, `serialize_rag_payload`), `agent.rag_cite.process_answer`, `agent.rag_pipeline` (`VERDICT_BELOW_THRESHOLD`, `config_from_row`, `mark_over_budget`, `run_retrieval_pipeline`), `shared.logger.get_logger`.

**Core retrieval sequence to copy** (rag_turn.py lines 217-284). Differences are marked:
```python
history = await _history_context(session, chat, config, parent_id)   # /help: history=None (D-10)
pipeline_config = config_from_row(config, kb, history)               # /help: pass HelpConfig row
chunks, trace = await run_retrieval_pipeline(session, kb, question, pipeline_config, client, model)
verdict = trace.pop("verdict")
if strict and (verdict == VERDICT_BELOW_THRESHOLD or not chunks):    # /help: DROP this gate (D-01)
    ... reply_text=build_idk_reply(trace)
if verdict == VERDICT_BELOW_THRESHOLD:
    merge_no_fragments_note(llm_messages)                            # /help: own "nothing found; you may use git tools" note
    ...
used = _message_tokens(llm_messages) + extra_tokens
budget = rag_budget(context_length, used, max_tokens)
block, kept, dropped = build_rag_block(chunks, budget, strict=strict)
if chunks and block is None:
    raise RagFailure("context_full", MSG_CONTEXT_FULL)
if block is not None:
    if not merge_rag_block(llm_messages, block):
        raise RagFailure("retrieval_failed", MSG_RETRIEVAL_FAILED)
    context_tokens = count_tokens(block)
mark_over_budget(trace, len(kept))
turn = RagTurn(MODE_RAG, _payload(MODE_RAG, kb_id, kb_name, top_k,
        sources=sources_from_chunks(kept), dropped=dropped, context_tokens=context_tokens,
        verdict=verdict, search=trace, strict=strict), kept_chunks=kept, strict=strict)
```
For `/help` set `strict=True, gated=False` when fragments were found so `finalize` runs `process_answer` and the UI quotes block shows (condition at app.js:5410: `rag.strict === true && rag.gated !== true && verdict !== 'model_idk'`). When no fragments: `strict=False`, `sources=[]`.

**Fail-soft error handling to copy** (rag_turn.py lines 285-315): `except asyncio.CancelledError: raise`; `except RagFailure as exc:` build a payload with `warning={"code": exc.code, "text": exc.text}, verdict=VERDICT_KB_UNAVAILABLE`; `except Exception:` log `rag_turn_failed`, `await session.rollback()` if `SQLAlchemyError`. The `/help` turn continues with tools and no fragments in these cases (RESEARCH Open Question 3).

**Kb ownership at turn time** (rag_turn.py lines 110-120, `_load_kb`): `kb.user_id != chat.user_id` counts as missing; reuse for the marked base.

**Finalize** (rag_turn.py lines 345-376): `finalize_rag_turn` returns early unless `turn.mode == MODE_RAG and turn.strict and reply_text is None and not warning and kept_chunks`; then `process_answer(question, assistant_text, turn.kept_chunks)` and `{**turn.payload, **result.payload_fields(turn.kept_chunks)}`. For `/help` wrap it: if any tool call succeeded and no model quote verified, drop auto quotes and set `answer_supported=None` (RESEARCH Pitfall 2). The payload is rebuilt with `{**payload, ...}`, so an added top-level `"help": {"kb_name": ..., "warnings": [...]}` key survives (also see `RagTurn.with_task_memory`, lines 71-75). Do not bump `PAYLOAD_VERSION` (agent/rag.py:66).

**Texts to keep distinct from strict mode:** `agent/rag.py` lines 58-73 define `BLOCK_OPEN`, `RAG_INSTRUCTION`, `NO_FRAGMENTS_INSTRUCTION`; `STRICT_INSTRUCTION` says "answer ONLY from the fragments" and conflicts with tool use. Write `/help` constants in `help_command.py` in the same module-constant style (`UPPER_CASE` string constants, Russian text).

**Parse function** (RESEARCH Pattern 1): module-level compiled regex `^\s*/help(?:\s+(?P<q>.*))?$` with `re.IGNORECASE | re.DOTALL`; return `None` for non-/help, `""` for usage.

**Messages for the turn (D-10):** build `[system, user]` directly, not via `build_llm_context` (which loads the chat tree and can raise `ContextOverflowError`). Sizing: `rag_budget(effective.context_length, used, max_tokens)` as above. System suffix pattern from ws.py:903-912: clock line `build_clock_line(...)` plus `TOOL_USE_RULE` when tools exist; do NOT append `MULTI_STEP_TOOL_HINT` (RESEARCH Pitfall 3).

---

### `agent/ws.py` - `/help` branch in the turn (controller, streaming)

**Analog:** same file. The `/help` path is a sibling of the ordinary path inside `_run_chat_turn`; reuse the stream + action-claim retry + tool-round middle (lines 938-1074) by extracting it into a helper so both paths share it.

**Entry and resolve-client pattern** (lines 831-852):
```python
current_chat_model.set(payload.model)
current_chat_provider_id.set(payload.provider_id)
async with async_session_factory() as session:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await websocket.send_json({"type": "error", "detail": f"Chat {chat_id} not found"})
        return
    try:
        client, provider_row = await resolve_client(chat.user_id, payload.provider_id)
    except ProviderUnavailableError as exc:
        await websocket.send_json({"type": "error", "code": "PROVIDER_UNAVAILABLE", "detail": exc.message})
        return
    user_msg = await _persist_user_message(session, chat, payload.content)   # line 854
```
Insert the branch BEFORE `_persist_user_message`: `question = parse_help(payload.content)`; if not None, decide usage/no-base notice first (send `{"type": "help_notice", "kind": ..., "text": ..., "stored": False}` and `return`), and persist the user message only on the real-turn path. `_persist_user_message` commits immediately and advances `current_leaf_message_id` (lines 159-178), which is why D-14 requires the notice decision first.

**Tool-set construction pattern** (lines 895-912): `_load_mcp_toolset(session, chat.user_id, chat_id)` wraps `build_mcp_toolset` in try/except and falls back to `McpToolset.empty()` ("An MCP problem never breaks a chat turn", lines 124-130). For `/help`: call `build_mcp_toolset(session, user_id, set(TOOL_REGISTRY), only_server_ids=...)` in the same try/except; `tool_schemas = [built-in schemas filtered by group] + toolset.schemas`. Warnings = chosen server names with no id in `{b.server_id for b in toolset.bindings.values()}`.

**Built-in groups:** `TASK_TOOL_NAMES`, `SCHEDULER_TOOL_NAMES` are at ws.py:72-73; memory group is the two memory tools in `TOOL_REGISTRY`. Restrict with `_ToolTurn(allowed_tools=frozenset(...))` (dataclass at lines 378-400). An empty `frozenset()` blocks all built-ins; `None` means unrestricted.

**Gate-free no-model reply analog:** `_complete_gated_turn` (737-822) shows the done-frame shape and `active_streams[chat_id] = asyncio.current_task()` / `finally: active_streams.pop(chat_id, None)` bookkeeping. `/help` notices differ: nothing is stored, so no `_persist_*`, no `done` frame.

**Persist + done pattern for a real /help turn** (lines 1118-1190): `finalize_rag_turn` via `asyncio.to_thread`, `_persist_assistant_message(..., rag_sources=rag_turn.sources_json)`, `compute_chat_stats`, then
```python
await websocket.send_json({
    "type": "done", "message_id": assistant_msg.id, "stats": stats,
    "memory_writes": memory_writes, "task_writes": task_writes,
    "invariant_conflict": conflict_payload, "rag": rag_turn.done_payload,
})
```
For `/help` skip (default D-07): `invariants.run_self_critique` block (1076-1116), `update_task_memory` (1121-1128), `extract_and_update_facts` (1160-1167), `schedule_title_generation` (1171-1179). Keep `compute_chat_stats`.

**Error-path cleanup pattern to copy:** on LLM stream failure, `await websocket.send_json(_llm_error_frame(exc, provider_row))` then `await _discard_user_message(session, chat_id, user_msg_id, user_parent_id)` then `return` (lines 974-983, 1039-1049). A `/help` real turn that fails must discard its user message exactly this way.

**Rate slot (RESEARCH Pattern 6):** `ws_chat` (lines 1290-1316) calls `limits.check_message_rate(chat_id)` before payload validation and `continue`s with a `RATE_LIMITED` frame on `retry_after`. To keep `/help` notices from consuming a slot, parse/validate the payload first and skip the call for `/help` messages, taking it inside the help path only when a model call follows. Frame to reuse:
```python
{"type": "error", "code": limits.CODE_RATE_LIMITED, "detail": limits.MSG_RATE_LIMITED, "retry_after": retry_after}
```
Decide notices before `limits.generation_gate.slot(...)` (in `run_limited_turn`, lines 1211-1227, or `_handle_chat_message`, lines 1230-1239) so a notice never waits in the generation queue.

---

### `agent/http_chat.py` (route, request-response)

**Analog:** same file.

**Skip `apply_chat_kb` for `/help` (D-16)** (lines 124-131):
```python
if kb_given:
    try:
        async with async_session_factory() as session:
            await apply_chat_kb(session, chat_id, user_id, body.kb_id)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```
Change the guard to `if kb_given and parse_help(body.content) is None:` (do not validate the ignored `kb_id`).

**Notice mapping** goes before the `done is None` branch (lines 141-147):
```python
done = next((f for f in sink.frames if f.get("type") == "done"), None)
if done is None:
    errors = [f for f in sink.frames if f.get("type") == "error" and f.get("code") != "TOOL_ERROR"]
    return _map_error_frame(errors[-1]) if errors else _internal_error()
```
Add a `help_notice` lookup first and return `JSONResponse(status_code=200, content={"chat_id": chat_id, "stored": False, "help": {"kind": ..., "text": ...}})`. A returned `JSONResponse` bypasses `response_model=HttpChatResponse` (already how errors work, line 163).

**Success body** (lines 99-112, `_build_success`): `tool_calls=[f for f in sink.frames if f.get("type") == "tool_call"]`, `rag=done.get("rag")`. Add `help=` only if you put it on the response model; since the `help` sub-object rides inside `rag`, `done.get("rag").get("help")` already reaches the client. If a top-level `help` field is added, mirror it in `HttpChatResponse` in `agent/schemas.py` as `help: dict[str, Any] | None = None` (additive).

**Error mapping:** `_map_error_frame` (62-85) does not map `RATE_LIMITED`; HTTP rate checking is in the route (lines 188-198) and returns `_error_response(429, ..., headers={"Retry-After": str(retry_after)}, retry_after=retry_after)`. If the rate check moves into the turn, add a matching `RATE_LIMITED` case here using this exact shape.

---

### `agent/mcp_tools.py` - `only_server_ids` (service)

**Analog:** `build_mcp_toolset` (lines 225-255), same function. Add `only_server_ids: set[int] | None = None` and filter `rows` once, before both the auto-connect call and the live-tools loop:
```python
rows = await mcp_config.list_servers(session, user_id)
if settings.MCP_AUTO_CONNECT:
    await _auto_connect_missing(user_id, rows)
live: list[tuple[int, str, list[McpToolInfo]]] = []
for row in rows:
    if not row.enabled or row.id is None:
        continue
    tools = get_live_tools(user_id, row.id)
    if tools is None:
        continue
    live.append((row.id, row.name, tools))
toolset = build_toolset_from_servers(user_id, live, reserved)
```
Insert `if only_server_ids is not None: rows = [r for r in rows if r.id in only_server_ids]` right after `list_servers`. Existing callers pass nothing and are unchanged. Tool names follow `mcp__{slug}__{tool}` via `server_slug` (68-71) and `_exposed_name` (74-87), so `local-git` tools appear as `mcp__local-git__git_status`.

---

### `ui/static/index.html` and `ui/static/app.js` (component, CRUD)

**Section markup analog:** `#mcp-section` (index.html 342-386): `<section id="mcp-section" class="border-t border-slate-700 p-5 space-y-3">`, `h3.text-lg.font-semibold`, field style `class="w-full rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"`, checkbox row `<input type="checkbox" id="mcp-enabled" class="rounded" checked><label for="mcp-enabled" class="text-sm">`. Insert `<section id="help-section" class="border-t border-slate-700 p-5 space-y-4">` between line 386 (`</section>` of mcp) and 387 (`#llm-providers-section`) per UI-SPEC. The search fields mirror `#rag-search-popover` (reuse its exact label strings: find the ids `rag-candidate-k`, `rag-threshold`, `rag-threshold-note`, `rag-threshold-reset`, `rag-stage-lexical`, `rag-stage-llm`, `rag-stage-hybrid`, `rag-stage-rewrite` in index.html line ~206).

**Load on modal open:** `openSettingsModal` (app.js 3820-3846) ends with
```js
loadMcpServers().catch((err) => showToast(err.message, 'error'));
loadLlmProviders().catch((err) => showToast(err.message, 'error'));
loadApiKeys();
loadNetworkInfo();
```
Add `loadHelpConfig().catch(...)` here. MCP server list for the checkboxes is already in `state.mcpServers` (loaded by `loadMcpServers`, app.js 2135-2138, `apiFetch('/api/v1/mcp/servers')`).

**Autosave-on-change pattern:** `saveChatRag` (app.js 5003-5030): build body from previous state, `apiFetch(..., {method: 'PUT', body: JSON.stringify(body)})`, on failure restore previous state and `showToast(errorText, 'error')`, then re-render. `saveRagSearchSetting(patch)` (4944-4949) is a thin wrapper that supplies the error text. Copy this shape for `saveHelpSetting(patch)` with the UI-SPEC error copy. Clamp handlers (4968-4981): `candidate-k` clamp to `[top_k, 50]`, change listener calls the save wrapper. Threshold render logic (4913-4927): `typeof cfg.threshold === 'number'` shows the value, else `calibrated_threshold` with note `(калибр.)`, else `(нет калибровки)`.

**KB card badge:** `buildKbCard` (4173-4179):
```js
const head = mcpEl('div', 'flex items-center justify-between gap-2');
const name = mcpEl('span', 'text-sm font-semibold truncate', kb.name);
name.title = kb.name;
head.append(name, kbStatusChip(kb));
```
Insert the badge before `kbStatusChip(kb)` when `kb.id === state.helpConfig?.kb_id`, using `RAG_CHIP_NEUTRAL` (app.js 5347-5348) plus `text-xs shrink-0`. Use `mcpEl(tag, className, text)` (sets `textContent`).

**Answer label + blocks:** `renderMessages` (170-225) composes bubble then, as siblings in `container`, tool cards (201-204), quotes (206-208), sources (209-212), details (213-214). Add the label as the first child of the assistant bubble when `msg.rag_sources && msg.rag_sources.help`; add the warning line under the bubble from `msg.rag_sources.help.warnings`; add the "В документации ничего не найдено." note when `rag.help` and `rag.sources.length === 0`. Reuse `buildRagQuotesBlock` (5409-5428) and `buildRagSourcesBlock` (5430-) unchanged, wrapped with `wrapToolCard(...)`. Sources condition at line 209 is `rag.mode === 'rag' && !rag.warning && sources.length > 0`; the `/help` payload must keep `mode: "rag"`.

**Notice (D-14):** add a `case 'help_notice':` in `handleWsMessage` (1750-1820) next to `case 'error':`. Follow the `error` case cleanup (`setStreaming(false); removeLoadingBubble(); unblockInput();` pattern from `done`, lines 1764-1767) but append a transient element to the message list and DO NOT push into `state.messages` and do not call `showToast`. Build it with `mcpEl`/`textContent` only.

**Frontend guards:** `tests/test_kb_folder_ui.py` imports `STATIC_DIR, _app_js_source, _function_source, _index_html` from `tests/test_limits_ui.py` and `check_js_balance` from `tests/test_static_js_syntax.py`; `_input_tag(html, id)` regex helper asserts ids and attributes. Copy that structure for `tests/test_help_ui.py` (ids present, `textContent`-only helpers, `handleWsMessage` has `help_notice` case, no new `innerHTML`).

---

### `tests/test_help_http.py`, `tests/test_help_ws.py`, `tests/test_help_api.py`, `tests/test_help_command.py` (test)

**Analog:** `tests/test_http_chat_rag.py`

**Imports and constants** (lines 1-36):
```python
import pytest
import respx
from httpx import AsyncClient
from agent import kb_search
from agent.kb_indexer import run_index_job
from shared.database import async_session_factory
from shared.models import ChatRagConfig, KbStatus, LongTermMemory
from tests.kb_helpers import get_kb, install_fake_embedder, seed_kb, vector_for
from tests.test_http_chat import (_api_client, _bearer, _chat, _key, _mock_upstream, _post, _rows)
from tests.test_memory_ws import _plain_content_response, _queue_responses, _tool_calls_response
COMPLETIONS = f"{settings.LM_STUDIO_BASE_URL}/v1/chat/completions"
BLOCK_MARK = "=== Фрагменты из базы знаний"
```

**Autouse fixture silencing side jobs and faking embeddings** (lines 39-49):
```python
@pytest.fixture(autouse=True)
def _rag_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)
    install_fake_embedder(monkeypatch)
    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for("Раздел номер 3 описывает тему")
    monkeypatch.setattr(kb_search, "embed_query", fake_query)
```

**Ready base helper and request-body assertion** (lines 52-67): `seed_kb(...)`, `run_index_job(kb_id, user_id)`, `kb.status == KbStatus.READY`; `_has_block(request_body)` checks the last user message for `BLOCK_MARK`. Use `@respx.mock` + `upstream = _mock_upstream()`; `upstream.requests[0]` exposes the outbound body, so you can assert `tools` content (default = `local-git` only) and that no earlier chat messages were sent (D-10). The MCP stdio fixture is `tests/fixtures/mcp_stdio_server.py` (line 36, `FIXTURE`).

**ChatRagConfig-unchanged assertions** (lines 60-62, 101-102): `async with async_session_factory() as session: return await session.get(ChatRagConfig, chat_id)`; assert `None` stays `None` after `/help`.

**kb_id ignored (D-16):** copy `test_kb_id_selects_kb_and_saves_config` (88-102) as the inverse: post `/help ...` with `kb_id=other_kb`, assert `_config(chat_id)` is None/unchanged.

**Rate-slot test (RESEARCH Pitfall 4):** set `RATE_LIMIT_MESSAGES_PER_MINUTE=1`, send `/help` (usage) twice, then an ordinary message; the ordinary one must pass.

---

### `tests/test_docs_help.py` (test, source guard)

**Analog:** `tests/test_docs_day30.py`

Structure (lines 1-45): module docstring, `ROOT_DIR/DOCS_DIR/TESTS_DIR` via `Path(__file__).resolve().parent.parent`, `_read_doc(name)` helper, a heading-order test (`text.index(heading)` list equals sorted), then `@pytest.mark.parametrize("needle", [...])` tests asserting required strings in `docs/API_SPEC.md`, `ARCHITECTURE.md`, `USER_GUIDE.md`, `TESTING_GUIDE.md`. Needles for Phase 23: `/api/v1/help/config`, the `/help` HTTP notice body (`"stored": false`), the statement that `kb_id` is ignored for `/help`, the Settings section name, `local-git`, `Документация проекта`.

---

### `scripts/help_eval.py` (utility, batch + request-response)

**Analogs:** `scripts/rag_dialog.py` (isolated run), `scripts/rag_judge.py` (judge), `scripts/verify_service.py` (HTTP chat with API key). Import, do not copy.

**Isolated-copy start-up** (rag_dialog.py 361-424): `_copy_database` via read-only sqlite backup, `_set_owner_password`, `prepare_run` composing `kb.prepare_copy(scratch)` (where `kb` is `scripts/e2e_kb_playwright.py`), env with `LM_STUDIO_BASE_URL`, `PYTHONIOENCODING`, `PYTHONPATH`, `start_app` using `asyncio.create_subprocess_exec(sys.executable, str(copy_dir / "run.py"), ...)` then `kb.wait_for_app(client)` and `kb.teardown(proc)`. Safety: `assert_isolated(db_path, ui_port, agent_port)` (rag_dialog.py:185). Origin header for mutating routes: `_json_headers()` returns `{"Origin": kb.UI_URL}` (427-430). Login helper: `login(client, run_env)` (432).

Pitfalls to honour (RESEARCH Pitfall 6): `prepare_copy` ignores `.git`, `docs`, `tests`, `scripts`, `.env` and writes an empty `.env`; so (a) upload `README.md` and `docs/` from the real repo root, (b) point `local-git --root` at the real repo, (c) pass `DEEPSEEK_API_KEY` only through the subprocess env, never to a file, (d) record `git rev-parse HEAD`, branch, `git status --short` in `run_meta.json` (see `write_run_meta`, rag_dialog.py:605, and `_git_commit`, 585).

**Judge reuse** (rag_judge.py): `build_judge_messages(question, expected_answer, answer, category)` (106), `parse_judge_reply` (156), `check_access(base_url, api_key, model)` (385) for the preflight. Use category `out_of_corpus` for the outside-the-project question. Pass `--model deepseek-flash` (default `deepseek-chat` is no longer served).

**Console encoding** (RESEARCH Pitfall 9): `sys.stdout.reconfigure(encoding="utf-8")` and `encoding="utf-8"` on every file write, as existing scripts do. Logging uses the script's `report(message)` helper (rag_dialog.py:46), not `print` in app code.

**Honest recording (D-21):** a run whose provider/key fails is written as `not run`, never as passed; failed questions stay in the report.

---

### `scripts/e2e_help_playwright.py` (utility, event-driven)

**Analog:** `scripts/e2e_mcp_git_playwright.py` (functions listed at lines 59-348): `pick_chat_model`, `blocked_exit`, `preflight`, `make_temp_repo`, `open_settings`, `add_server`, `select_chat_model`, `wait_socket`, `ask(page, question) -> (ok, text, ...)`, `card_names_the_tool`, `scenario`, `drive_browser`, `run_e2e`, `main`. Compose these with `scripts/e2e_kb_playwright.py` helpers (`prepare_copy`, `seed_scratch_users`, `wait_for_app`, `teardown`) and `scripts/e2e_kb_folder_playwright.py` for the folder-upload step. Assertions per RESEARCH Pitfall 7: label and sources survive reload; tool-call cards are asserted only before reload (they are in-memory, `state.toolCallsByMessage`, app.js:1761).

---

### `tests/fixtures/help/control_set.json` (fixture, batch) - partial analog

**Analog:** the frozen scenario files loaded by `scripts/rag_dialog.py::load_scenarios(path, require_frozen=True)` (line 51). Mirror the "frozen before the run" flag and per-item fields (id, category, question, expected_source, expected_tool, key_facts). Ten items per D-17: 5 docs-only, 2 git-only, 2 mixed, 1 `out_of_corpus`.

---

### `Day31_report.md`, `eval_out/day31/`

**Analog:** `Day30_report.md` (structure, honest recording, links to `eval_out/`) and `Day25_report.md` (frozen question set, two runs compared, judge column). Written in Russian (D-22). Per question: answer, sources, tool calls, verdict. State plainly that on the DeepSeek run the judge and the answering model are the same provider (D-19).

## Shared Patterns

### Ownership / auth on REST routes
**Source:** `agent/rag_api.py` lines 73-82 (`_get_owned_chat`) and 12 (`get_current_user, require_allowed_origin, require_json_content_type`)
**Apply to:** `agent/help_api.py`, and every check of `kb_id` / MCP server ids in the PUT body
```python
chat = await session.get(Chat, chat_id)
if chat is None or chat.user_id != user_id:
    logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chat {chat_id} not found")
```
404 never 403 (IDOR oracle). Use `_get_owned_kb` / `_unprocessable` from `agent/kb_api.py` for the base. WebSocket auth is cookie only, API key authenticates REST only (no change needed).

### Commit/rollback on writes
**Source:** `agent/rag_api.py` lines 174-188
**Apply to:** `help_api.py` PUT handler, any other write
```python
try:
    ...
    await session.commit()
    await session.refresh(row)
except SQLAlchemyError:
    await session.rollback()
    raise
```

### Logging
**Source:** `agent/rag_api.py` lines 25, 189-203
**Apply to:** all new Python modules
`logger = get_logger(__name__)`; message key `snake_case_action` plus `key=value` pairs (`logger.info("rag_config_updated", chat_id=chat_id, mode=mode, ...)`). Never log keys, secrets or full tracebacks (log `error=str(exc)` or `type(exc).__name__`). Planned keys: `help_config_updated`, `help_turn_prepared`, `help_notice_sent`.

### MCP failure never breaks a turn
**Source:** `agent/ws.py` lines 124-130
**Apply to:** the `/help` toolset load
```python
try:
    return await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
except Exception as exc:
    logger.warning("mcp_toolset_failed", chat_id=chat_id, error=str(exc))
    return McpToolset.empty()
```

### Fail-soft RAG error handling
**Source:** `agent/rag_turn.py` lines 285-315 (`CancelledError` re-raised, `RagFailure` -> warning payload, other `Exception` -> rollback if `SQLAlchemyError`)
**Apply to:** `help_command.prepare_help_turn`

### User-message cleanup on a failed turn
**Source:** `agent/ws.py` lines 974-983 and 1039-1049 (`_llm_error_frame` + `_discard_user_message`)
**Apply to:** the `/help` real-turn path

### Frontend text safety
**Source:** `mcpEl(tag, className, text)` helper used throughout `ui/static/app.js` (e.g. 4174-4179) sets `textContent`; markdown bodies go through Marked + `DOMPurify.sanitize()`
**Apply to:** all new frontend elements (label, badge, notice, warning, section controls). No new `innerHTML`.

### Docs and source guards
**Source:** `tests/test_docs_day30.py`, `tests/test_kb_folder_ui.py`, `tests/test_static_js_syntax.py::check_js_balance`
**Apply to:** `tests/test_docs_help.py`, `tests/test_help_ui.py`

## Conventions

Derived with `gsd-tools.cjs verify conventions --derive --scope agent` (51 file-name samples, 118 identifier samples; the tool counted 19 "camel" file names which looks like a heuristic artefact for single-word lowercase modules such as `ws.py`, so treat the casing result with caution).

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| file-name casing | snake_case (31 snake / 19 "camel" / 1 other) | 61% | 0.68 | contested hotspot (tool-reported; project docs state snake_case everywhere) |
| identifier casing (classes) | PascalCase | 90% | 0.33 | named contract |
| export style | insufficient data (Python) | n/a | n/a | n/a |
| import style | absolute imports, no wildcard (py-import-relativity: absolute 100%, py-wildcard-import: explicit 100%) | 100% | 0.00 | named contract |

**Contested hotspots (author's choice):** The CJS<->SDK dual resolver (`bin/lib/**` is CJS `module.exports`/`require`; `sdk/src/**` is ESM `export`/`import`) is the prototype intentional-contested split of the GSD tooling; each half is internally consistent per directory and contested only repo-wide. This repo (AiAdventAgentV2) has no such split, but the frontend is single-file vanilla JS (`ui/static/app.js`, global functions, no modules) and the backend is Python; match the local style of the file you edit. Python rules from CLAUDE.md apply: `lowercase_with_underscores.py` modules, `PascalCase` classes, `UPPER_CASE` constants, `_private` helpers, absolute imports (`from agent.rag import ...`), stdlib -> third-party -> local import order, type hints everywhere, `X | None` unions, single-line module docstring, `datetime.now(timezone.utc)`, `structlog` only (no `print()` outside `run.py`; scripts use a `report()` helper).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `/help` slash-command recognition (`parse_help`) | utility | transform | No slash command exists in the app (CONTEXT: "`/help` is the first one"); use the regex in RESEARCH Pattern 1 and unit-test the edge cases (`/helpme`, `/ help`, case, leading space) |
| `help_notice` frame (WS) and its HTTP JSON body | protocol | event-driven | No existing frame type that is "answer without storing"; closest shapes are the `error` frame (ws.py 1295-1302) and `_complete_gated_turn`'s `done` frame, but neither fits; follow RESEARCH "Notice frame" examples |
| Transient client-only notice element in the message list | component | event-driven | Every existing message-list element is rendered from `state` messages; the notice must bypass `state.messages` (UI-SPEC section 4) |

## Metadata

**Analog search scope:** `agent/` (ws, http_chat, rag_api, rag_turn, rag, rag_pipeline, mcp_tools, main), `shared/models.py`, `shared/database.py`, `ui/static/index.html`, `ui/static/app.js`, `scripts/`, `tests/`
**Files scanned:** ~25 files read in whole or in targeted ranges (CONTEXT, RESEARCH, UI-SPEC included)
**Pattern extraction date:** 2026-10-13
