# Phase 13: Knowledge base indexing (Day 21) - Pattern Map

**Mapped:** 2026-10-03
**Files analyzed:** 19 new/modified
**Analogs found:** 15 / 19 (4 have no codebase analog: pure chunking, loaders, FAISS storage, embeddings parse; use 13-RESEARCH.md patterns)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/kb_api.py` | route (APIRouter) | request-response + CRUD (multipart 202) | `agent/scheduler_api.py` | exact (role), multipart is new |
| `agent/kb_indexer.py` | service (bg job) | batch + event-driven | `agent/scheduler.py` (`spawn_run`, `execute_run`, `recover_orphaned_runs`) | exact |
| `agent/embeddings.py` | service (HTTP client) | request-response | `agent/llm_client.py::LMStudioClient` | role-match |
| `agent/kb_loaders.py` | utility | file-I/O, transform | none | no analog |
| `agent/kb_chunking.py` | utility (pure fns) | transform | `agent/schedule.py` (pure validation + Russian error class) | partial |
| `shared/kb_storage.py` | utility | file-I/O | none (RESEARCH Pattern 1) | no analog |
| `shared/models.py` (+ `KnowledgeBase`, `KbDocument`, `KbChunk`, `KbStatus`) | model | CRUD | `ScheduledTask` / `TaskRun` in same file (l.410-560) | exact |
| `agent/state.py` (+ `kb_jobs`, cleanup fn) | state | in-memory | `title_tasks` + `cleanup_chat_caches` (l.15-34) | exact |
| `agent/main.py` (router + lifespan recovery) | config/wiring | event-driven | l.390-417 itself | exact |
| `agent/providers.py::_parse_models` (additive `type`) | utility | transform | itself (l.354-364) | exact |
| `agent/events.py` consumers (`kb_progress`, `kb_deleted` frames) | event | pub-sub | `agent/titles.py:239`, `scheduler_schemas.py:165-189` | exact |
| `requirements.txt` | config | - | itself | exact |
| `ui/static/index.html` (sidebar block + 2 modals) | component | request-response | `#scheduler-panel`, `#scheduler-create-modal` | exact |
| `ui/static/app.js` (KB panel, create modal, search modal, WS cases) | component | request-response + pub-sub | scheduler panel/modal code (l.2263, 2520-2600, 2895-2925) | exact |
| `ui/static/app.js::populateModelSelect` (hide embeddings) | component | transform | itself (l.1447-1480) | exact |
| `tests/conftest.py` (KB dir isolation + `kb_jobs.clear()`) | test config | - | itself (l.27-55) | exact |
| `tests/test_kb_api.py`, `test_kb_scoping.py`, `test_kb_delete.py` | test | request-response | `tests/test_scoping.py`, `test_cascade_delete.py` | role-match |
| `tests/test_kb_indexer.py`, `test_kb_events.py` | test | event-driven | `tests/test_scheduler_runner.py`, `test_scheduler_events.py` | role-match |
| `tests/test_kb_chunking.py`, `test_kb_loaders.py` (golden) | test | transform | `tests/test_scheduler_schedule.py` | partial |
| `tests/test_modal_close_policy.py` (extend modal id list) | test | source guard | itself (auto-discovers `-modal` ids; update `test_index_declares_known_modals`) | exact |

## Pattern Assignments

### `agent/kb_api.py` (route, request-response / CRUD)

**Analog:** `agent/scheduler_api.py`

**Imports + router + 404 helper** (lines 3-31):
```python
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from shared.database import get_session
from shared.models import ScheduledTask, User

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])
MSG_TASK_NOT_FOUND = "Задание не найдено"

def _not_found(detail: str) -> HTTPException:
    """Build the 404 used for missing and foreign ids alike (never 403)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

def _validation_error(exc) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.message)   # Russian message shown verbatim
```

**Write route + auth (copy, but DROP `require_json_content_type` on the multipart POST)** (lines 57-67, 110-114):
```python
@router.post(
    "/tasks", response_model=ScheduledTaskOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_task(body: ScheduledTaskCreate, session: AsyncSession = Depends(get_session),
                      current_user: User = Depends(get_current_user)) -> ScheduledTaskOut:
```
KB create: `status_code=202`, `dependencies=[Depends(require_allowed_origin)]` only; params `files: list[UploadFile] = File(...)`, `name: str = Form(...)`, etc. JSON routes (search, embed-check) keep both dependencies. Wrap domain errors: ops raise `KbValidationError`/`KbNotFoundError` -> `_validation_error` / `_not_found` (same split as `_change_state`, lines 94-107). Every query filters `user_id == current_user.id`; foreign -> 404.

**Wiring** (`agent/main.py` l.60-61, 416-417): `from agent.kb_api import router as kb_router` then `app.include_router(kb_router)`.

---

### `agent/kb_indexer.py` (service, batch + event-driven)

**Analog:** `agent/scheduler.py`

**Own-session + strong-ref task + cancel handling** (lines 322-361):
```python
def spawn_run(self, run_id: int) -> None:
    task = asyncio.create_task(self.execute_run(run_id), name=f"scheduler-run-{run_id}")
    self._runs[run_id] = task
    task.add_done_callback(lambda _done: self._forget_run(run_id))

async def execute_run(self, run_id: int) -> None:
    async with async_session_factory() as session:          # job opens its own session
        ...
    try:
        async with self._semaphore:                          # Semaphore outside any deadline
            ...
    except asyncio.CancelledError:
        await self._finish_cancelled(run_id)                 # best-effort record, then re-raise
        raise
    except Exception as exc:
        logger.error("scheduler_run_failed", run_id=run_id, error=type(exc).__name__)
        await self._finish_run(run_id, RunStatus.FAILED, error=msg_unexpected(exc))
```
KB variant: store tasks in `agent.state.kb_jobs: dict[int, asyncio.Task]` (module-level `asyncio.Semaphore(1)`, created lazily to avoid closed-loop issues in tests); cancelled job must NOT write `ready` (D-02); log `str`/`type(exc).__name__` only.

**Orphan recovery** (lines 286-320): bulk `update(...).where(status.in_(...)).values(status=FAILED, error=MSG_INTERRUPTED_RESTART)`, `await session.commit()` in try, `except Exception: await session.rollback(); raise`, `logger.info("scheduler_recovered_orphans", count=recovered)`. Call as `await recover_orphaned_kb_jobs()` in `agent/main.py::lifespan` directly after l.396 `await scheduler.recover_orphaned_runs()`; also rmtree partial index dirs.

**Progress publish** (`scheduler.py` l.234, `titles.py:239`): `hub.publish(user_id, {"type": "kb_progress", "kb_id": ..., "status": ..., "done": ..., "total": ..., "phase": ...})`; build frames via small helper funcs like `scheduler_schemas.py` l.165-189 (`{"type": "task_deleted", "task_id": task_id}` -> `{"type": "kb_deleted", "kb_id": id}`). `hub.publish` is sync, never raises. Throttle ~0.5 s, always emit status transitions. Never publish from `to_thread`.

`delete_kb`: cancel `kb_jobs.pop(id)`, `await` with `suppress(asyncio.CancelledError)`, delete rows, `to_thread(shutil.rmtree)`, then publish `kb_deleted` (same order as `titles` cleanup in `state.cleanup_chat_caches`).

---

### `agent/embeddings.py` (service, request-response)

**Analog:** `agent/llm_client.py::LMStudioClient` (lines 240-337)

**Pattern:** class holding `_base_url`, per-call `async with httpx.AsyncClient(timeout=...) as client`, `response.raise_for_status()`; load is serialized by `_model_switch_lock`; map `httpx.ConnectError` -> "LM Studio не запущен", `httpx.TimeoutException` -> Russian timeout message; log `error=str(exc)`.
```python
url = f"{self._control_base}/api/v1/models/load"
async with httpx.AsyncClient(timeout=LOAD_TIMEOUT) as client:
    response = await client.post(url, json={"model": model_id})
    response.raise_for_status()
```
Important gaps vs analog (D-24):
- `LMStudioClient.list_models()` hits `/v1/models` (no `type`/`state`). The pre-flight guard needs `GET {base}/api/v0/models` (fields `type`, `state`); add a new method on the embedder, do not change `list_models`.
- `LMStudioClient.load_model` unloads `_current_loaded_model` first (l.285-289): do NOT reuse it for the embedder (would evict the chat model and mutate chat bookkeeping). Use a standalone POST to `/api/v1/models/load` under `model_switch_lock` only for that call, no `_current_loaded_model` write.
- Use new `KB_EMBED_TIMEOUT` (add to `shared/config.py::Settings`, env-driven like other timeouts), not `LLM_TIMEOUT`.
- `/v1/embeddings` via `client.post(f"{base}/v1/embeddings", json={"model": id, "input": batch})`; verify `len(data) == len(batch)` and constant dim. PREFIXES dict constants (D-09): nomic `search_query: ` / `search_document: `, others `""`. Guard code: RESEARCH Pattern 2.
- Resolve base URL via `get_lm_studio_client(app_config.LM_STUDIO_BASE_URL)` pattern from `agent/main.py::_lm_studio_for` (l.1196-1213) if provider-aware; legacy default is `settings.LM_STUDIO_BASE_URL`.

---

### `shared/models.py` (+ KB tables)

**Analog:** `ScheduledTask` / `TaskRun` (lines 410-560)

**Status enum + SAEnum column + FK cascade** (l.410-418, 439-446, 475-484):
```python
class ScheduledTaskStatus(str, Enum):
    ACTIVE = "active"
    ...
user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"),
                                      nullable=False, index=True))
status: ScheduledTaskStatus = Field(
    default=ScheduledTaskStatus.ACTIVE,
    sa_column=Column(SAEnum(ScheduledTaskStatus,
        values_callable=lambda enum_cls: [m.value for m in enum_cls]), nullable=False))
created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
__table_args__ = (Index("ix_scheduledtask_status_next", "status", "next_run_at"),)
```
Apply: `KbStatus(str, Enum)` queued/indexing/ready/failed; `KnowledgeBase.user_id` FK user cascade; `KbDocument.kb_id` FK `knowledgebase.id` cascade; `KbChunk.kb_id`/`document_id` FK cascade (table names are lowercased class names: `knowledgebase`, `kbdocument`); `UniqueConstraint("kb_id", "sha256")` (already imported in models.py). New tables come via `create_all` in `init_db` (check `shared/database.py` for table-registration/migration list; RESEARCH says none needed). Never `Field(ondelete=...)`.

---

### `agent/state.py` (+ KB job state)

**Analog:** same file, l.15-34:
```python
title_tasks: dict[int, asyncio.Task[None]] = {}

def cleanup_chat_caches(chat_id: int) -> None:
    title_task = title_tasks.pop(chat_id, None)
    if title_task is not None and not title_task.done():
        title_task.cancel()
```
Add `kb_jobs: dict[int, asyncio.Task[None]] = {}` and a `cleanup_kb_caches(kb_id)` with the identical pop-and-cancel shape. Remember `tests/conftest.py` l.45-49 must `agent_state.kb_jobs.clear()`.

---

### `agent/providers.py::_parse_models` (additive)

**Analog:** itself (l.354-364). Current output `{"id", "loaded"}`. Change additively to keep `type`: `models.append({"id": ..., "loaded": loaded, "type": item.get("type")})` (only meaningful for `lm_studio=True`, otherwise None). Keep existing keys (tests `test_llm_providers_*` assert shape; check them for strict dict equality before editing, Pitfall 9). Note `_fetch_models` calls `LMStudioClient.list_models()` which reads `/v1/models`; verify that endpoint actually returns `type` on this LM Studio build, otherwise `_fetch_models` must read `/api/v0/models` for lm_studio kind.

---

### `ui/static/index.html` (sidebar block, 2 modals)

**Analog:** l.155-166 (panel) and l.437-448 (modal shell).

Sidebar (insert after `#scheduler-panel`, before `#agent-status` l.167):
```html
<div id="scheduler-panel" class="border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-72">
  <h3 class="text-slate-300 font-semibold mb-2 flex items-center justify-between">
    <span>Расписание <span id="scheduler-count" ...>0</span></span>
    <button type="button" data-fold-toggle="scheduler-panel-body" aria-expanded="false" ...>▸</button>
  </h3>
  <div id="scheduler-panel-body" class="hidden">
    <button type="button" id="btn-scheduler-new" class="w-full rounded-lg bg-indigo-600 hover:bg-indigo-500 px-4 py-2 text-sm font-semibold transition">+ Новое задание</button>
    <div id="scheduler-list" class="space-y-2 mt-2"></div>
```
Modal shell (l.437-448): overlay `hidden fixed inset-0 z-50 ... bg-black/60` + `role="dialog" aria-modal="true" aria-labelledby`, card `max-w-lg mx-4 max-h-[90vh] overflow-y-auto`, header with `&times;` button `id="btn-close-..."`, `<form class="p-5 space-y-4">`, inputs `rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500`. IDs: `kb-panel`, `kb-count`, `kb-panel-body`, `kb-list`, `btn-kb-new`, `kb-create-modal` (`max-w-lg`), `kb-search-modal` (`max-w-2xl`), `kb-create-error` (`role="alert"`). Both new modal ids end in `-modal` and are auto-checked by `test_modal_close_policy.py`.

---

### `ui/static/app.js` (KB panel / modals / WS)

**Analog:** scheduler code in the same file.

**Fetch helper caveat** (l.75-90): `apiFetch` forces `Content-Type: application/json`. For the multipart create call use a dedicated `fetch(`${AGENT_BASE}/api/v1/kb`, {method:'POST', body: formData, credentials:'include'})` with NO Content-Type header (browser sets boundary); replicate its 401 redirect and `detail` extraction (l.80-89). All other KB calls use `apiFetch`.

**Load + render** (l.2263-2281): `async function loadKbList(silent)` -> `apiFetch('/api/v1/kb')`, store `state.lastKbs`, render; on error `showToast('Не удалось загрузить ...')` unless silent. Fold toggle hookup at l.3165-3168 (`document.querySelector('[data-fold-toggle="scheduler-panel-body"]')` -> load on expand).

**Modal open/close** (l.2520-2597): `hideSchedulerModal` / `closeSchedulerModal` (restores focus to opener) / `openSchedulerCreateModal` (reset form, clear error, show, focus). Wire only the `×` button; no overlay/Escape listeners (policy test).

**WS dispatch** (l.2895-2925): add to the `switch (frame.type)` in the events handler:
```js
case 'kb_progress': upsertKb(frame); break;
case 'kb_deleted': removeKb(frame.kb_id); break;
```
then re-render without resetting expanded/confirm state (UI-SPEC). Add a REST refetch on WS reconnect (see `connectEventsWs`, l.~2930). Note the handler starts with `if (state.lastSchedulerTasks === null) { loadSchedulerTasks(true); return; }`: add the KB cases BEFORE or outside that early return, otherwise KB frames are dropped.

**Model picker** (l.1447-1480 `populateModelSelect`): filter `group.models.filter(m => m.type !== 'embeddings')`. The embedding dropdown mirrors this builder (optgroup per provider, `✓` for loaded) with name-regex filter `/embed|giga|nomic|bge|e5/i` plus "показать все модели" toggle; default = first model passing the D-24 guard (`type === 'embeddings'` and loaded).

**Rendering safety:** KB names, filenames, chunk text via `textContent` only (CLAUDE.md; contrast `renderMarkdown` at l.104 which uses DOMPurify, not used for KB). Toasts: `showToast(msg, 'success')` ("База знаний удалена").

---

### `tests/*` (KB)

**Analogs:** `tests/conftest.py`, `tests/test_scoping.py`, `tests/test_scheduler_events.py`

Fixtures to reuse (conftest l.108-167): `authenticated_client`, `second_authenticated_client` (IDOR 404 tests, `ac.seeded_user_id`), `seed_user`, `login_test_client` (sync TestClient + `client.portal.call`), WS event tests per `test_scheduler_events.py` (`WS_ORIGIN = "http://localhost:8000"`, `hub.publish` via `client.portal.call`, `EVENTS_PATH = "/ws/events"`).

`clean_test_db` extension (l.27-55): also `agent_state.kb_jobs.clear()`; set/rmtree the KB dir (`<DB_PATH stem>_kb`, i.e. `test_app_kb/`) before and after (`shutil.rmtree(..., ignore_errors=True)`); prefer env override `KB_STORAGE_DIR` pointing at `tmp_path` if added to config. Mock embeddings with `respx` (project standard) on `/v1/embeddings` and `/api/v0/models`. Cascade test as `tests/test_cascade_delete.py`. `/health` responsiveness smoke during a mocked job (Pitfall 5). Golden КоАП excerpt in `tests/fixtures/`; real PDFs `pytest.mark.skipif(not Path(r"C:\Projects\RAG\...").exists())`. `test_modal_close_policy.py::test_index_declares_known_modals` list can include the two new modal ids.

---

## Shared Patterns

### Auth + scoping
**Source:** `agent/dependencies.py` l.19-80; `scheduler_api.py` l.47-54
**Apply to:** every `kb_api.py` route
`current_user: User = Depends(get_current_user)`; write routes add `dependencies=[Depends(require_allowed_origin)]`; every query/`session.get` is followed by an `owner_id == current_user.id` check, foreign -> 404 with Russian detail (never 403). Background jobs never receive the request session; they open `async_session_factory()` (scheduler.py l.336).

### DB write / error handling
**Source:** `scheduler.py` l.267-283 and `dependencies.py` l.71-75
```python
try:
    ...
    await session.commit()
except Exception:
    await session.rollback()
    raise
```
**Apply to:** kb_indexer status updates, delete_kb, create route. Bulk ops via `update(...)` + `.is_(None)` style where needed.

### Logging
`from shared.logger import get_logger` / `logger = get_logger(__name__)`; keys `kb_index_started`, `kb_index_failed`, `kb_deleted` with `kb_id=`, `user_id=`, `error=type(exc).__name__`/`str(exc)`; no file paths/content in logs or `detail`.

### Event publishing
**Source:** `agent/events.py` l.45-60 (`hub.publish(user_id, frame)` sync, drops oldest on full queue).
**Apply to:** kb_indexer, delete_kb.

### Russian user-facing messages
Module-level `MSG_*` constants (e.g. `MSG_TASK_NOT_FOUND`, `MSG_INTERRUPTED_RESTART` in scheduler); put KB caps and messages in one place (D-04), e.g. `agent/kb_chunking.py`/`kb_api.py` constants `MAX_FILE_BYTES = 50 * 1024 * 1024`, `MAX_FILES = 10`, `MAX_TOTAL_BYTES = 100 * 1024 * 1024`, `MAX_EMBED_CHARS = 2000`.

### Blocking work
All PyMuPDF, chunking, numpy/FAISS, `rmtree`, file writes via `await asyncio.to_thread(...)`; httpx stays async (Supervisor kills Agent after missed health checks).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `agent/kb_loaders.py` | utility | file-I/O | No document parsing exists; use RESEARCH "Spike (b)" cleaning algorithm (frequency header/footer filter, line joining, annotation regex, scan heuristic) |
| `agent/kb_chunking.py` | utility | transform | Pure functions; follow `validate_chunk_params`, `chunk_fixed`, structural cascade in RESEARCH Code Examples. Style analog only: `agent/schedule.py` (error class with `.message`, pure validators) |
| `shared/kb_storage.py` | utility | file-I/O | No file storage in codebase; RESEARCH Pattern 1 (FAISS bytes serialize, `.tmp` -> `os.replace`, non-ASCII safe) |
| multipart upload streaming in `kb_api.py` | route | file-I/O | No `UploadFile` use exists; RESEARCH Pitfall 7 (stream with cap, SHA-256 while streaming, sanitized stored names) |

## Conventions

Convention derivation skipped (gsd-tools.cjs not resolvable from this environment: `CLAUDE_PLUGIN_ROOT` unset and plugin cache not found). Values below are taken from CLAUDE.md/CONVENTIONS and the files read, not computed.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case `.py` (JS: single `app.js`) | not computed (~100% in `agent/`, `shared/`) | n/a | named contract (per CLAUDE.md) |
| Identifier casing | snake_case funcs/vars, PascalCase classes, UPPER_CASE consts, `_private` helpers (JS: camelCase) | not computed | n/a | named contract (per CLAUDE.md) |
| Export style | direct module-level defs, imported explicitly by dotted path | not computed | n/a | named contract |
| Import style | absolute from project root (`from agent.x import y`), stdlib -> third-party -> local, no aliases | not computed | n/a | named contract |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` CJS `module.exports`/`require` versus `sdk/src/**` ESM `export`/`import`) is the prototype intentional-contested split: each half is internally consistent per directory and contested only repo-wide, so match the local directory style. It does not apply to this Python/vanilla-JS repo; here the analogous rule is: Python in `agent/`/`shared/` follows type-hinted async style with single-line module docstrings; `ui/static/app.js` is plain global-function vanilla JS (no modules).

## Metadata

**Analog search scope:** `agent/`, `shared/`, `tests/`, `ui/static/index.html`, `ui/static/app.js`
**Files scanned:** ~15 read in part (scheduler_api, scheduler, events, state, dependencies, main lifespan, providers, llm_client, models, conftest, index.html, app.js slices)
**Pattern extraction date:** 2026-10-03
