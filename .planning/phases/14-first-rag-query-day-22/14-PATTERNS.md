# Phase 14: First RAG query (Day 22) - Pattern Map

**Mapped:** 2026-10-03
**Files analyzed:** 22 new/modified
**Analogs found:** 21 / 22

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/rag.py` (new) | service | request-response (retrieve + budget + block) | `agent/kb_search.py` + `agent/context_engine.py` (`serialize_tool_trace`, token helpers) | role-match |
| `agent/rag_turn.py` (new, optional glue) | service | request-response (fail-soft pre-step) | `agent/headless.py` (glue that reuses `ws` helpers) / ws.py suffix block | partial |
| `agent/rag_api.py` (new) | route | CRUD + request-response | `agent/kb_api.py` | exact |
| `agent/kb_search.py` (mod: `EmbeddingDimMismatchError`) | service | request-response | itself (`KbIndexCorruptError`) | exact |
| `agent/ws.py` (mod: 3 touch points) | controller (WS) | streaming | itself (`tool_trace` plumbing) | exact |
| `agent/schemas.py` (mod: `MessageResponse.rag_sources`) | model (schema) | CRUD | `MessageResponse` | exact |
| `agent/main.py` (mod: `_message_to_response`, `include_router`) | route | CRUD | `include_router(kb_router)` line 421 | exact |
| `shared/models.py` (mod: `ChatRagConfig`, `Message.rag_sources`) | model | CRUD | `Message.tool_trace` + `WorkingMemory` / `KnowledgeBase` FKs | exact |
| `shared/database.py` (mod: migration) | migration | batch | `migrate_add_message_tool_trace` | exact |
| `shared/config.py` (mod: `RAG_EMBED_TIMEOUT`) | config | - | `KB_EMBED_TIMEOUT` | exact |
| `ui/static/index.html` (mod: header controls) | component | request-response | header block lines 190-199 | exact |
| `ui/static/app.js` (mod: header controls, sources block, warning, mode label) | component | event-driven | `buildToolCallCard`, `buildKbResultCard`, `renderMessages`, `done` case | exact |
| `tests/test_rag.py` (new) | test | unit | `tests/test_kb_search.py` | exact |
| `tests/test_rag_api.py` (new) | test | CRUD | `tests/test_kb_api.py` / `tests/test_cascade_delete.py` | role-match |
| `tests/test_rag_ws.py` (new) | test | streaming | `tests/test_memory_ws.py` | exact |
| `tests/test_rag_static.py` (new) | test | static check | none | no analog |
| `tests/test_rag_fixture.py`, `test_rag_eval.py`, `test_rag_report.py` (new) | test | file check / unit | `tests/test_kb_search.py` (fakes) | partial |
| `tests/fixtures/rag/control_set.json` (new) | fixture | - | `tests/fixtures/` | partial |
| `scripts/rag_eval.py` (new) | utility (CLI) | batch | `scripts/e2e_kb_playwright.py` (bootstrap) + `agent/headless.py` (in-process) | role-match |
| `scripts/e2e_rag_playwright.py` (new) | test (UAT) | request-response | `scripts/e2e_kb_playwright.py` | exact |
| `tests/test_cascade_delete.py`, `tests/test_database.py` (extend) | test | CRUD | themselves | exact |
| `Day22_report.md` (new) | doc | - | none | no analog |

## Pattern Assignments

### `agent/rag.py` (service, request-response)

**Analog:** `agent/kb_search.py` (retrieval core to wrap, not rewrite) and `agent/context_engine.py` (serializer + token helpers).

**Imports / logger / message-constant pattern** (`agent/kb_search.py` lines 1-24):
```python
"""Semantic search over a ready knowledge base using a cached FAISS index."""

import asyncio
from typing import Any

import faiss
import numpy as np
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.embeddings import embed_query
from agent.state import kb_index_cache
from shared.kb_storage import index_path, read_index_bytes
from shared.logger import get_logger
from shared.models import KbChunk, KbStatus, KnowledgeBase

logger = get_logger(__name__)

MSG_NOT_READY = "Поиск доступен после завершения индексации"
MSG_INDEX_CORRUPT = "Индекс базы знаний повреждён. Удалите её и создайте заново."
```

**Domain error class pattern** (`agent/kb_search.py` lines 27-42):
```python
class KbNotReadyError(Exception):
    """Search was requested before the knowledge base finished indexing."""

    def __init__(self) -> None:
        super().__init__(MSG_NOT_READY)
        self.message = MSG_NOT_READY


class KbIndexCorruptError(Exception):
    """The stored index is missing or disagrees with the database."""
    ...
```
Copy this shape for `RagFailure(code, text)` (RESEARCH sketch uses a dataclass Exception; carrying `.message`/`.text` mirrors `EmbeddingError.message` in `agent/embeddings.py:35-40`).

**Core search call to wrap** (`agent/kb_search.py` lines 56-70, result dict shape lines 86-98):
```python
async def search_kb(session, kb, query, top_k) -> list[dict[str, Any]]:
    if kb.status != KbStatus.READY:
        raise KbNotReadyError()
    index = await load_index_cached(kb)
    vector = await embed_query(kb.embedding_model, query)
    array = np.asarray([vector], dtype="float32")
    if array.shape[1] != index.d:
        raise KbIndexCorruptError()      # <- change to EmbeddingDimMismatchError (subclass)
    ...
        {"rank": ..., "score": round(score, 4), "chunk_id": row.chunk_id, "source": row.source,
         "title": row.title, "section": row.section, "page": row.page_start, "text": row.text}
```
`rag.retrieve` = `asyncio.wait_for(search_kb(...), settings.RAG_EMBED_TIMEOUT)` + exception-to-`RagFailure` mapping (RESEARCH "Retrieval wrapper"). The search route in `agent/kb_api.py:366-388` already maps the same exceptions to HTTP; mirror its `except` ordering (subclass first).

**Serializer pattern to mirror for `rag_sources` JSON** (`agent/context_engine.py` lines 92-108): `json.dumps(entries, ensure_ascii=False)`, return `None` when nothing to store. Use one serializer for both `Message.rag_sources` and `done.rag`. Parser tolerance pattern: `_parse_trace_entries` (context_engine.py:111-117) catches `(json.JSONDecodeError, TypeError)` and returns empty; copy for `_message_to_response` corrupt-JSON handling (`rag_sources_parse_failed`).

**Token counting:** `from agent.llm_client import count_tokens`; `context_engine._message_tokens(llm_messages)` for `used_tokens` (RESEARCH Pattern 3). Merge helper (`merge_rag_block`) replaces last user dict with `{**msg, "content": merged, "token_count": count_tokens(merged)}`.

---

### `agent/rag_api.py` (route, CRUD)

**Analog:** `agent/kb_api.py`

**Imports / router / write-route dependencies** (`kb_api.py` lines 1-15, 59-63, 366-372):
```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from shared.database import get_session
from shared.logger import get_logger
from shared.models import KnowledgeBase, User

logger = get_logger(__name__)

@router.post(
    "/{kb_id}/search",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def search_route(
    kb_id: int,
    body: KbSearchRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, list[dict[str, Any]]]:
```
Use the same dependencies on `PUT /api/v1/chats/{id}/rag`; GET routes take no origin/JSON dependency. Router is prefix-free here (routes under both `/api/v1/chats/...` and `/api/v1/kb/...`); register with `app.include_router(rag_router)` right after `app.include_router(kb_router)` (`agent/main.py:421`) and `from agent.rag_api import router as rag_router` next to line 60.

**Ownership check (404 for foreign and missing)** (`kb_api.py` lines 116-121):
```python
async def _get_owned_kb(session: AsyncSession, kb_id: int, user_id: int) -> KnowledgeBase:
    """Return the caller's knowledge base or raise 404."""
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None or kb.user_id != user_id:
        raise _not_found()
    return kb
```
Owned-chat helper must be local to `rag_api.py` (importing from `agent.main` is circular). Copy from `agent/main.py:117-129`:
```python
chat = await session.get(Chat, chat_id)
if chat is None or chat.user_id != user_id:
    logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chat {chat_id} not found")
```
Reuse `MSG_KB_NOT_FOUND = "База знаний не найдена"` (kb_api.py:57-ish) for the snippet route. Validation errors use 422 via `_unprocessable(msg)` (kb_api.py ~line 100). Write path must follow the commit/rollback rule from CLAUDE.md (see `agent/main.py::update_settings`).

---

### `shared/models.py` (model) - `ChatRagConfig`, `Message.rag_sources`

**Analog:** `Message.tool_trace` (line 77) and FK style in `Message`/`WorkingMemory`/`KnowledgeBase`.

**Column to clone** (`shared/models.py:77`):
```python
tool_trace: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
```
-> `rag_sources` identically, directly below it.

**FK cascade/SET NULL via sa_column** (`shared/models.py` lines 60-72 and 159-165):
```python
chat_id: int = Field(
    sa_column=Column(Integer, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False),
)
parent_id: Optional[int] = Field(
    default=None,
    sa_column=Column(Integer, ForeignKey("message.id", ondelete="SET NULL"), nullable=True),
)
```
`ChatRagConfig.chat_id` = CASCADE + `primary_key=True`; `kb_id` = `ForeignKey("knowledgebase.id", ondelete="SET NULL")`, nullable. Table name for `KnowledgeBase` is `knowledgebase`. Class docstring single line; `datetime.now(timezone.utc)` default factory as in `Message.created_at`. `mode` stays a plain `str` column (RESEARCH Pattern 1).

---

### `shared/database.py` (migration)

**Analog:** `migrate_add_message_tool_trace` (lines 107-122) - clone exactly for `rag_sources`:
```python
async def migrate_add_message_tool_trace(conn: Any) -> None:
    """Add tool_trace column to message when missing (idempotent)."""
    table_check = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='message'"),
    )
    if table_check.fetchone() is None:
        return

    result = await conn.execute(text("PRAGMA table_info(message)"))
    columns = [row[1] for row in result.fetchall()]
    if "tool_trace" not in columns:
        logger.info("migrating_message_add_tool_trace")
        await conn.execute(text("ALTER TABLE message ADD COLUMN tool_trace TEXT"))
```
Register in `init_db()` (lines 255-263) after `migrate_add_message_tool_trace(conn)` and before `create_all`. New table `ChatRagConfig` needs no ALTER (created by `create_all`).

---

### `shared/config.py` (config)

**Analog:** existing `KB_EMBED_TIMEOUT` (config.py ~line 50). Add `RAG_EMBED_TIMEOUT: float = 30.0` beside it with the same style.

---

### `agent/ws.py` (WS controller, streaming) - three touch points

**Analog:** the existing `tool_trace` plumbing in the same file.

1. `_persist_assistant_message` (lines 206-229): add keyword-only `rag_sources: str | None = None` after `tool_trace`, pass `rag_sources=rag_sources` into `Message(...)`.
```python
async def _persist_assistant_message(session, chat, parent_id, content, *, tool_trace: str | None = None) -> Message:
    assistant_msg = Message(chat_id=chat.id, parent_id=parent_id, role="assistant", content=content,
                            token_count=count_tokens(content), tool_trace=tool_trace)
```
2. Pre-step location: after the system-suffix block (ws.py ~lines 781-794: `if llm_messages and llm_messages[0]["role"] == "system": ... llm_messages[0] = {...}`) and before `assistant_text = ""` (line ~796). Insert `rag_turn = await prepare_rag_turn(session, chat, payload.content, llm_messages, effective)`. `llm_messages` is built at ~lines 738-766 by `build_llm_context` inside `try/except ContextOverflowError` (that branch deletes `user_msg`; RAG must never reach it). The same `llm_messages` list is reused by tool rounds, action-claim retry (`llm_messages.append(...)`) and invariant retry, so mutation in place survives all.
3. Persist + done: `assistant_msg = await _persist_assistant_message(session, chat, user_msg.id, assistant_text, tool_trace=tool_trace, rag_sources=rag_turn.sources_json)` (line ~970) and extend the done frame (lines ~1020-1029):
```python
await websocket.send_json({
    "type": "done", "message_id": assistant_msg.id, "stats": stats,
    "memory_writes": memory_writes, "task_writes": task_writes,
    "invariant_conflict": conflict_payload,
    # + "rag": rag_turn.done_payload
})
```
**Error pattern to NOT trigger from RAG** (ws.py ~lines 750-765, 856-870): `CONTEXT_OVERFLOW` / `LLM_ERROR` paths do `await session.delete(user_msg); await session.commit(); return`. `prepare_rag_turn` must catch everything (`except Exception`, re-raise `asyncio.CancelledError`) and return a degraded `RagTurn`.
Logging: `logger.info("snake_case_key", chat_id=chat_id, key=value)`; never log chunk/query text.

---

### `agent/schemas.py` + `agent/main.py` (tree exposure)

**Analog:** `MessageResponse` (schemas.py:66-75) and `_message_to_response` (main.py:359-369). Add `rag_sources: Optional[dict[str, Any]] = None` to the schema and parse `message.rag_sources` JSON in the mapper (tolerate corrupt -> None, log `rag_sources_parse_failed`).
```python
return MessageResponse(
    id=message.id, chat_id=message.chat_id, parent_id=message.parent_id, role=message.role,
    content=message.content, token_count=message.token_count, created_at=message.created_at,
)
```
Note `MessageResponse` currently exposes no `tool_trace`; `rag_sources` is the first persisted-extra field in the tree.

---

### `ui/static/index.html` (header controls)

**Analog:** header lines 190-199 (`#chat-title` div, `#model-select`, then buttons). Insert `#rag-badge`, `#rag-toggle`, `#rag-kb-select`, `#rag-k-wrap` between the title div and `#model-select`, copying the select class string:
```html
<select id="model-select"
    class="rounded-lg bg-slate-800 border border-slate-700 px-3 py-1.5 text-sm max-w-xs truncate">
```
Exact layout/copy: `14-UI-SPEC.md`.

---

### `ui/static/app.js` (component, event-driven)

**Analog 1 - collapsed `<details>` card, `textContent` only** (`buildToolCallCard`, lines 942-972):
```javascript
function buildToolCallCard(evt) {
    const card = document.createElement('details');
    card.className = 'tool-call-card max-w-[75%] rounded-lg border border-slate-700 '
        + 'bg-slate-900 text-xs text-slate-300 px-3 py-2';
    const summary = document.createElement('summary');
    summary.className = 'cursor-pointer select-none ...';
    summary.textContent = ...;
    card.appendChild(summary);
    ...
    return card;
}
function wrapToolCard(card) { /* div.flex.justify-start wrapper */ }
```
Build `buildRagSourcesBlock(ragSources)` ("Источники (N)") the same way.

**Analog 2 - source row card** (`buildKbResultCard`, lines 3573-3596) uses helper `mcpEl(tag, className, text)` and `line-clamp-4` + "показать полностью" toggle:
```javascript
const card = mcpEl('div', 'bg-slate-800 border border-slate-700 rounded-lg p-3 space-y-1');
const head = mcpEl('div', 'text-xs flex flex-wrap items-center gap-2 text-slate-400');
head.appendChild(mcpEl('span', '', `#${item.rank}`));
head.appendChild(mcpEl('span', 'text-slate-200', Number(item.score).toFixed(3)));
head.appendChild(mcpEl('span', '', item.source || ''));
if (item.section) head.appendChild(mcpEl('span', 'truncate', item.section));
head.appendChild(mcpEl('span', 'font-mono text-slate-500', String(item.chunk_id)));
const body = mcpEl('p', 'text-sm text-slate-200 line-clamp-4 whitespace-pre-wrap', item.text || '');
const toggle = mcpEl('button', 'text-xs text-indigo-400 hover:text-indigo-300', 'показать полностью');
toggle.addEventListener('click', () => { const expanded = !body.classList.toggle('line-clamp-4'); ... });
```
For sources, snippet `text` is lazily fetched on first `<details>` `toggle` event from `GET /api/v1/kb/{kb_id}/chunks/{chunk_id}?file=...`; metadata-only when 404/KB deleted. Source field is `file` in `rag_sources` (vs `source` in search results) - adapt.

**Analog 3 - render integration** (`renderMessages`, lines 162-208): inside the per-message loop, after `bubble.appendChild(content)`/token info and for `!isUser`, append mode label, warning line and sources block (read `msg.rag_sources` from `state.messages`). Existing precedent for rows attached after a message: `state.lastConflicts.filter(...)` -> `container.appendChild(buildConflictBanner(conflict))`. Note `content.innerHTML` is DOMPurify/markdown-only; all KB/warning text must use `textContent`.

**Analog 4 - done frame and toast** (lines 1250-1274): `case 'done':` already calls `loadChatTree(state.currentChatId)` so persisted `rag_sources` render from the tree; add one-time toast only from `data.rag.warning`:
```javascript
if (data.invariant_conflict) { showToast('⚠️ Обнаружен конфликт с инвариантом', 'warning'); }
// + if (data.rag && data.rag.warning) showToast(data.rag.warning.text, 'warning');
```
`showToast(message, type)` (line 60) supports `'warning'` (yellow). Load config in `selectChat` (line 1047); KB list from `state.lastKbs` / `loadKbList` (lines 3192-3232); hook the existing `kb_deleted`/`kb_progress` frame handling (line ~2888) to refresh the KB select/badge. Toggle and K input PUT immediately and revert + error toast on failure; use `apiFetch(url, {method:'PUT', body: JSON.stringify(...)})` as in `runKbSearch` (lines 3611-3614).

---

### `tests/test_rag.py` (unit) / `tests/test_rag_api.py` / `tests/test_rag_ws.py`

**Analog for retrieval units:** `tests/test_kb_search.py` - helpers from `kb_helpers` and the fake query patch:
```python
from kb_helpers import NOMIC, RU_TEXT, chunk_rows, get_kb, install_fake_embedder, seed_kb, seed_user, vector_for

async def _ready_kb(monkeypatch) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    assert (await get_kb(kb_id)).status.value == "ready"
    return user_id, kb_id

def _patch_query(monkeypatch, text, seen):
    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        seen.append(model_id)
        return vector_for(text)
    monkeypatch.setattr(kb_search, "embed_query", fake_query)
```
For dim mismatch, patch to return a wrong-length vector. Test DB is the separate `test_app.db` (conftest).

**Analog for WS tests:** `tests/test_memory_ws.py` lines 17-80: `BASE_URL = settings.LM_STUDIO_BASE_URL`, `WS_ORIGIN = "http://localhost:8000"`, `_plain_content_response(text)`, `_queue_responses([...])`, `@respx.mock`, and:
```python
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
Capture the outbound `/v1/chat/completions` request body via respx to assert the block is in the last user content and the stored user row equals the raw question.

**Analog for cascade tests:** `tests/test_cascade_delete.py` (uses `authenticated_client`, `async_session_factory`, model imports) - add `ChatRagConfig` CASCADE-on-chat-delete and SET NULL-on-KB-delete cases. Migration idempotency: extend `tests/test_database.py`.

---

### `scripts/rag_eval.py` (CLI, batch)

**Analog:** `scripts/e2e_kb_playwright.py` header (docstring with usage + exit codes, `REPO_ROOT`, `sys.path.insert(0, str(REPO_ROOT))`, `LM_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")`, preflight exit code 2) and `agent/headless.py` for in-process reuse of agent modules. Answer generation: `LLMClient.complete_chat(messages, model, temperature=0.0, max_tokens)` / `complete_chat_detailed(...)` (`agent/llm_client.py:68-93`); retrieval via `agent.rag.retrieve`; prompt via `rag.build_rag_block` + `merge_rag_block` so chat and eval are byte-identical. `print()` is permitted for CLI summary (precedent: e2e scripts).

### `scripts/e2e_rag_playwright.py`
**Analog:** `scripts/e2e_kb_playwright.py` - copy the isolated-copy flow (copy tree to temp dir, ports 18000/18001, scratch DB and KB storage, PASS/FAIL lines, never touch 8000/8001).

---

## Shared Patterns

### Logging
**Source:** `shared/logger.py`; every module `logger = get_logger(__name__)`; keys `snake_case_action` with `key=value`; ids, counts, `error=type(exc).__name__` only (see `kb_index_load_failed` in kb_search.py:46). Apply to `rag.py`, `rag_api.py`, `rag_turn.py`.

### Auth + ownership
**Source:** `agent/kb_api.py::_get_owned_kb` (116-121), `agent/main.py::_get_chat_or_404` (117-129), `Depends(get_current_user)`, write routes also `require_allowed_origin` + `require_json_content_type`. 404 (never 403) for foreign or missing.

### Error mapping (domain exception -> degraded result / HTTP)
**Source:** `agent/kb_api.py::search_route` lines 380-388:
```python
try:
    results = await search_kb(session, kb, query, body.top_k)
except KbNotReadyError as exc:
    raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_NOT_READY) from exc
except KbIndexCorruptError as exc:
    raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_INDEX_CORRUPT) from exc
except EmbeddingError as exc:
    raise _embedding_http_error(exc) from exc
```
`rag.retrieve` applies the same mapping to `RagFailure` codes (kb_not_ready, dim_mismatch [subclass first], index_corrupt, embedder_unavailable incl. `asyncio.TimeoutError`, kb_deleted, retrieval_failed catch-all). Never bare `except:`.

### DB write discipline
**Source:** CLAUDE.md + `agent/main.py::delete_chat`/`update_settings`: `await session.commit()` after writes, `await session.rollback()` in exception handlers, `.is_(None)` in `where()`.

### Frontend safety
**Source:** `buildToolCallCard`/`buildKbResultCard`: `textContent` / `mcpEl(..., text)` only for KB data, filenames, warnings; DOMPurify only for markdown.

### Conventions
Convention derivation skipped (`gsd-tools.cjs` not resolvable from this environment: CLAUDE_PLUGIN_ROOT unset, plugin cache absent). Project conventions per CLAUDE.md: `snake_case` files and functions, `PascalCase` classes, `UPPER_CASE` constants, `_private` helpers, single-line docstrings, `str | None` unions, stdlib -> third-party -> local imports. Contested hotspot note: `bin/lib/**` (CJS) vs `sdk/src/**` (ESM) is not present in this repo; the analogous split here is `agent/**` (absolute `from agent.x import`) vs tests (`from kb_helpers import ...` via tests dir on sys.path) - match the local directory's style.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `tests/test_rag_static.py` | test | static grep of `app.js` | no existing static-analysis test of frontend source; write with plain `pathlib.read_text` + regex |
| `Day22_report.md` | doc | generated report | no precedent for a root report; generate from eval output (RESEARCH open question 2: root + `eval_out/day22/`) |
| `tests/fixtures/rag/control_set.json` | fixture | - | `tests/fixtures/` exists but holds no JSON eval set; shape given in 14-RESEARCH "Fixture shape" |

## Metadata

**Analog search scope:** `agent/`, `shared/`, `ui/static/`, `scripts/`, `tests/`
**Files scanned:** ~15 read (targeted ranges), plus grep across `agent/`, `ui/static/app.js`, `tests/`
**Pattern extraction date:** 2026-10-03
