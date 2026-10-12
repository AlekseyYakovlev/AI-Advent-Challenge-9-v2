# Phase 21: Project docs in RAG (Day 31) - Pattern Map

**Mapped:** 2026-10-13
**Files analyzed:** 17 (new/modified)
**Analogs found:** 15 / 17 (README.md and the `webkitdirectory` input have no in-repo analog)

All excerpts are from the current branch (Day31). Line numbers refer to the files as read in this session.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `README.md` | docs | static | `docs/ARCHITECTURE.md` (tone only), `run.py`, `.env.example`, `shared/config.py` (fact sources) | no analog |
| `agent/kb_limits.py` (modify: `MAX_FILES`, `MAX_SOURCE_NAME_CHARS`, `PHASE_ADDING`) | config | constants | itself | exact |
| `shared/models.py::KbDocument` (modify: drop sha unique, index on kb_id+filename) | model | CRUD | itself (lines 697-712) | exact |
| `shared/database.py` (add `migrate_kbdocument_drop_sha_unique`, call in `init_db`) | migration | batch | `migrate_add_chatragconfig_rank_columns` / `migrate_add_message_rag_sources` (idempotent check pattern) | role-match (table rebuild is new, see Research) |
| `agent/kb_api.py` (modify `_clean_filenames` -> `_clean_source_names`, `_store_all`, messages; add `GET /limits`, `POST /{kb_id}/files`) | controller | request-response + file-I/O | `create_kb` in same file (lines 266-341) | exact |
| `agent/kb_indexer.py` (add `run_add_job`, `spawn_add_job`, `_fail_add`; extend `recover_orphaned_kb_jobs`) | service | batch / event-driven | `run_index_job` + `spawn_index_job` + `_fail` (same file) | exact for structure, inverse for failure semantics |
| `agent/kb_schemas.py` (modify `kb_progress_frame` with optional `add_summary`) | utility | pub-sub | `kb_progress_frame` (not re-read; see Research) | exact |
| `shared/kb_storage.py` (add staging dir helper, `patch_index_bytes`) | utility | file-I/O | `uploads_dir`, `write_index_bytes`, `read_index_bytes`, `remove_index_file` (same file) | exact |
| `ui/static/index.html` (folder input, pending header/report, `#kb-add-modal`) | component | request-response | `#kb-create-modal` block (lines 535-549+) | exact |
| `ui/static/app.js` (pending-list controller, card states, limits loader, add submit) | component | request-response + event-driven | `renderKbFileList`, `submitKbCreate`, `buildKbCard`, `bindKbUi` | exact |
| `tests/test_kb_api.py` (modify + new cases) | test | request-response | itself | exact |
| `tests/test_kb_add.py` (new: add route + job) | test | request-response + background job | `tests/test_kb_indexer.py`, `tests/kb_helpers.py` | role-match |
| `tests/test_kb_models.py` (invert `test_kb_document_unique_sha`) / migration test | test | CRUD | itself | exact |
| `tests/test_kb_lifecycle.py` (orphan recovery of READY base with `phase`) | test | batch | `test_recover_orphaned_kb_jobs` (line 121) | exact |
| `tests/test_kb_folder_ui.py` (new source-guard) | test | transform | `tests/test_limits_ui.py` | exact |
| `scripts/e2e_kb_playwright.py` (modify lines ~259, 465-469; add folder/add steps) | test | e2e | itself | exact |
| `docs/API_SPEC.md`, `docs/ARCHITECTURE.md:449`, `docs/USER_GUIDE.md:146`, `docs/TESTING_GUIDE.md` | docs | static | existing sections of same files | exact |

## Pattern Assignments

### `agent/kb_api.py` - source-name cleaning + limits route + add route (controller, request-response/file-I/O)

**Analog:** `create_kb` and helpers in the same file.

**Imports/constants pattern** (lines 24-37, 55-66): limits are imported BY NAME (tests monkeypatch `kb_api.MAX_FILE_BYTES` / `MAX_TOTAL_BYTES`), messages are module-level Russian constants. Keep this; build messages from the imported constants instead of the hard-coded "10" / "50 МБ":
```python
from agent.kb_limits import (ALLOWED_EXTENSIONS, MAX_FILE_BYTES, MAX_FILES, MAX_REQUEST_BYTES, MAX_TOTAL_BYTES, ...)
MSG_TOO_MANY_FILES = "Можно загрузить не больше 10 файлов."   # line 59: replace with a format template using MAX_FILES
MSG_BAD_EXTENSION = "Файл {name}: поддерживаются только PDF, TXT и MD."
MSG_DUPLICATE = "Файл {name} уже добавлен."
```

**Dependencies/auth pattern** (lines 266-271): copy for the add route.
```python
@router.post(
    "",
    response_model=KbOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_allowed_origin), Depends(_require_content_length_within_cap)],
)
async def create_kb(..., files: list[UploadFile] | None = File(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)) -> KbOut:
```
Add route: `@router.post("/{kb_id}/files", ...)` with same dependencies; first line after `try` is `kb = await _get_owned_kb(session, kb_id, current_user.id)` (lines 120-125, 404 for foreign ids). 409 for not READY / busy, mirroring `search_route` (lines 387-390): `raise HTTPException(status.HTTP_409_CONFLICT, detail=MSG_...)`.

**Validation to replace** (lines 153-166 `_clean_filenames`): currently `Path((upload.filename or "").replace("\\", "/")).name` (basename - the thing that breaks KBUP-03). Keep the signature shape (`list[UploadFile] | None -> list[str]`), the empty/over-count guards via `_unprocessable(...)`, and the extension check; replace the basename step with the Research Pattern 1 sanitiser (NFC, `\`->`/`, drop empty/`.`/`..`/drive segments, strip control chars, length cap, in-request duplicate by source name).

**Static GET route ordering** (lines 223, 252, 344): `GET /embedding-models` and `GET ""` are declared before `GET /{kb_id}`; declare `GET /limits` before line 344 or it is captured as a kb id. Auth only, no origin check (same as `embedding_models_route`, lines 223-231).

**Streaming store pattern** (lines 174-220): `_store_upload` streams to `{index:02d}.part`, enforces `MAX_FILE_BYTES`/`MAX_TOTAL_BYTES`/non-empty, hashes sha256, stored name server-generated. Reuse the loop body but (a) target a per-request staging dir with `uuid4().hex` stored names (Research Pitfall 6), (b) drop the `if sha in seen: raise MSG_DUPLICATE` rule (lines 215-217).

**Error/cleanup pattern** (lines 319-330): copy verbatim for the add route (rollback + remove staging; `BaseException` branch uses `asyncio.shield(session.rollback())`):
```python
    except UploadRejectedError as exc:
        await session.rollback()
        ...
        raise _unprocessable(exc.message) from exc
    except BaseException:
        await asyncio.shield(session.rollback())
        ...
        raise
```
After success: `kb_indexer.spawn_add_job(...)` (analog of line 332) and `hub.publish(user_id, kb_progress_frame(kb))` (line 333); return `kb_out(kb)`. Log with `logger.info("kb_add_accepted", kb_id=..., user_id=..., ...)` mirroring line 334.

---

### `agent/kb_indexer.py` - `run_add_job`, `spawn_add_job`, `_fail_add` (service, batch/event-driven)

**Analog:** `spawn_index_job` (lines 75-84), `run_index_job` (224-274), `_fail` (277-303).

**Spawn pattern** (copy exactly, swap target and name):
```python
def spawn_index_job(kb_id: int, user_id: int) -> None:
    task = asyncio.create_task(run_index_job(kb_id, user_id), name=f"kb-index-{kb_id}")
    kb_jobs[kb_id] = task
    def _forget(done: asyncio.Task[None]) -> None:
        if kb_jobs.get(kb_id) is done:
            kb_jobs.pop(kb_id, None)
    task.add_done_callback(_forget)
```
The add job must also live in `kb_jobs[kb_id]` so `delete_kb` (lines 306-327) and `shutdown_kb_jobs` (369-375) cancel it.

**Job skeleton to copy** (lines 224-246): `async with _get_semaphore():` -> read the kb row with `session.expunge(kb)` -> `_update_kb(...)` progress -> `ensure_embedding_model(kb.embedding_model)` -> `asyncio.to_thread(_chunk_document, kb, path, filename)` (line 108-124, reuse unchanged, it takes the relative-path `filename`) -> `_embed_all` style batching (lines 207-221). For addition `_embed_all` needs vectors keyed by not-yet-existing ids; embed texts FIRST, then insert rows (Research Pattern 2). Keep `status` READY; use `_update_kb(kb_id, phase=PHASE_ADDING, done_chunks=.., total_chunks=..)`.

**Progress helper** (lines 87-105): `_update_kb` opens its own session, commits, `session.refresh`, `hub.publish(kb.user_id, kb_progress_frame(kb))`. Reuse; do not hold a session across `await embed_passages`.

**Chunk insert pattern** (lines 137-170): `KbChunk(... chunk_id=f"{doc.id}-{i}", source=doc.filename, title=Path(doc.filename).stem, ...)` with `CHUNK_INSERT_SLICE` batches and `flush()` to get ids; for the add job do it in ONE transaction: insert new `KbDocument` + `KbChunk`, THEN delete replaced documents/chunks (Pitfall 4, rowid reuse).

**Failure pattern - DO NOT REUSE `_fail`** (lines 277-303 delete all chunks, set FAILED, remove index file). `_fail_add` copies only the session/commit/rollback/publish skeleton:
```python
async with async_session_factory() as session:
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None: return
    try:
        kb.phase = None; kb.error = message; kb.updated_at = datetime.now(timezone.utc)
        session.add(kb); await session.commit()
    except Exception:
        await session.rollback(); raise
    await session.refresh(kb); frame = kb_progress_frame(kb)
hub.publish(user_id, frame)
```
plus staging cleanup; status stays READY, chunks and `index.faiss` untouched.

**Exception mapping** (lines 266-274): copy the ladder `except (KbLoadError, EmbeddingError) as exc: ... exc.message` / `except asyncio.CancelledError: ...; raise` / `except Exception: logger.error("kb_add_failed", kb_id=.., error=type(exc).__name__)`; messages as module constants like `MSG_UNEXPECTED` (line 49).

**Orphan recovery** (lines 330-366): extend with a second `update(KnowledgeBase).where(status == READY, phase.is_not(None)).values(phase=None, error=<msg>)` and remove `uploads/_staging`. Use `.is_not(None)` / `.is_(None)` per CLAUDE.md.

---

### `shared/kb_storage.py` - staging dir + index patch (utility, file-I/O)

**Analog:** same file. Path helpers (lines 29-36) are one-liners over `kb_dir`; add `staging_dir(user_id, kb_id) -> Path` returning `uploads_dir(...) / "_staging"` the same way. Index I/O (lines 50-64): use `read_index_bytes` for a fresh copy, `index.remove_ids(np.asarray(ids, dtype="int64"))`, then normalise like `build_index` (lines 39-47: `np.ascontiguousarray(..., dtype="float32")`, `faiss.normalize_L2`, `add_with_ids`), persist with `write_index_bytes` (atomic `os.replace`). Cleanup helper style: `remove_index_file` (lines 74-77, `unlink(missing_ok=True)`).

---

### `shared/database.py` - `migrate_kbdocument_drop_sha_unique` (migration, batch)

**Analog:** `migrate_add_message_rag_sources` (lines 126-141) for the idempotent skeleton, and `init_db` (lines 371-388) for call order.
```python
async def migrate_add_message_rag_sources(conn: Any) -> None:
    """Add rag_sources column to message when missing (idempotent)."""
    table_check = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='message'"))
    if table_check.fetchone() is None:
        return
    ...
    logger.info("migrating_message_add_rag_sources")
```
Differences (see Research Pitfall 2): this migration cannot run on the `engine.begin()` connection in `init_db` because `PRAGMA foreign_keys=OFF` is a no-op inside a transaction (the connect hook at lines 45-52 sets it ON). Run it BEFORE `async with engine.begin()` on `engine.execution_options(isolation_level="AUTOCOMMIT").connect()`, guard on `"uq_kbdocument_kb_sha256" in sqlite_master.sql`, log `kbdocument_migration_failed` and re-raise. Test on the OLD schema with chunks present.

---

### `shared/models.py::KbDocument` (model, CRUD)

**Analog:** lines 697-712. Remove `__table_args__ = (UniqueConstraint("kb_id", "sha256", ...),)`; keep FK pattern `sa_column=Column(Integer, ForeignKey("knowledgebase.id", ondelete="CASCADE"), nullable=False, index=True)` (CLAUDE.md rule). Add a plain composite `Index("ix_kbdocument_kb_filename", "kb_id", "filename")` in `__table_args__` (non-unique, Research Open Question 1). Update docstring: `filename` = source name (relative path).

---

### `ui/static/app.js` - pending list, add modal, card states (component)

**Analog:** KB section lines 4034-4400, 5177-5200.

**Element helper + textContent only** (no innerHTML anywhere in the section):
```javascript
const li = mcpEl('li', 'flex items-center justify-between gap-2 rounded bg-slate-800 px-2 py-1');
const label = mcpEl('span', 'truncate', `${file.name} · ${formatKbFileSize(file.size)}`);   // 4246-4247
label.title = file.name;
const remove = mcpEl('button', 'text-slate-400 hover:text-white text-base leading-none', '×');
remove.type = 'button';
remove.setAttribute('aria-label', `Убрать файл ${file.name}`);
```
Generalise `renderKbFileList` (4242-4259) to entries `{source, file}`: label uses `entry.source`, `title = entry.source`, size from `entry.file.size`. Share one factory between the create and add modals with separate ids (Research Pitfall 12).

**Change handler with dedupe** (5181-5188): currently dedupes by `name+size`; change to dedupe by `source` (D-07), compute `source = (file.webkitRelativePath || file.name).replace(/\\/g, '/')`, filter extensions with `/\.(pdf|txt|md)$/i` for the folder input, count taken/skipped, and always `e.target.value = ''` afterwards so the same folder can be re-picked.

**Submit pattern** (4347-4400): copy for the add submit; change line 4366 to `entries.forEach((e) => formData.append('files', e.file, e.source));`. Keep: `fetch(\`${AGENT_BASE}/api/v1/kb/${id}/files\`, {method:'POST', body: formData, credentials:'include'})`, the 401 redirect, `typeof detail === 'string'` error display, button disable/restore in `finally`. No manual `Content-Type` header (multipart boundary).

**Card state pattern** (`kbStatusChip` 4085-4106, `kbProgressBar` 4108-4120, `buildKbCard` 4152-4196): add the "adding" state when `kb.status === 'ready' && kb.phase` (chip text with `kb.done`/`kb.total`, reuse `kbProgressBar(kb)`), show the red error line when `kb.status === 'ready' && kb.error` (reuse the `line-clamp-2` + `aria-expanded` button at 4171-4182, which today is gated on `failed`), and add the "Добавить файлы" button to the actions row (4184-4194) following the `searchBtn` enabled/disabled-with-title idiom (enabled only when `ready` and no `phase`, D-11). `upsertKb` (4062-4070) replaces the whole kb object each frame, so keep the add summary in a separate `Map` keyed by kb id, not on the kb object.

**Modal open/close** (`openKbCreateModal` 4311-4324, `closeKbModal` 4227-4235): copy for `#kb-add-modal` (reset state, remember `state.kbModalOpener`, focus, restore focus on close).

**Result card** (4415-4423): `head.appendChild(mcpEl('span', '', item.source || ''))` - add `truncate` + `title = item.source` so long paths stay readable.

**Binding** (`bindKbUi` 5177-5200): add listeners for the new ids next to existing ones.

---

### `ui/static/index.html` (component)

**Analog:** lines 541-549 inside `#kb-create-modal`:
```html
<span class="block text-sm text-slate-400 mb-1">Файлы (PDF, TXT, MD)</span>
<label for="kb-files" class="block cursor-pointer rounded-lg border border-dashed border-slate-700 px-3 py-3 text-center text-sm text-slate-300 hover:border-indigo-500 transition">
    Выбрать файлы
    <input type="file" id="kb-files" multiple accept=".pdf,.txt,.md" class="sr-only">
</label>
<ul id="kb-file-list" class="mt-2 space-y-1 text-xs text-slate-300"></ul>
```
Add a sibling label with `<input type="file" id="kb-folder" webkitdirectory class="sr-only">` (no `accept`; filter in JS). No in-repo analog for `webkitdirectory`; follow UI-SPEC and Research. The add modal repeats the block with distinct ids (`kb-add-files`, `kb-add-folder`, `kb-add-file-list`, ...). Limits text is filled from `GET /api/v1/kb/limits`, never hard-coded.

---

### Tests

**`tests/test_kb_api.py` (modify):** fixtures and helpers to reuse (lines 39-65): `spawned` fixture (monkeypatches `kb_api.kb_indexer.spawn_index_job` - add the same for `spawn_add_job`), `_files(*items)` producing `("files", (name, data, "application/octet-stream"))` (pass `"docs/a/README.md"` as the name for relative-path cases), `_post`, `_assert_nothing_left`. Update `test_path_traversal_filename_is_sanitised` (lines 171-183; `"../../evil.txt"` must still yield `evil.txt`), the `too-many` case (use `MAX_FILES + 1`) and the `duplicate` case (same source name instead of same content).

**`tests/test_kb_add.py` (new):** build on `tests/kb_helpers.py`: `seed_user`, `seed_kb(user_id, files, status=KbStatus.READY)`, `install_fake_embedder(monkeypatch, fail_on_batch=..., block=...)`, `chunk_rows`, `get_kb`, `wait_until`. Note `seed_kb` writes `sha256 = sha256(data + str(index))`, so for unchanged-detection tests compute the expected sha explicitly or seed via the create route. Assert: READY throughout, `ntotal == chunk_count`, failure leaves old chunks/index/READY and sets `error`, replaced removes old chunks.

**`tests/test_kb_lifecycle.py`:** extend near `test_recover_orphaned_kb_jobs` (line 121) with a READY base carrying `phase="adding"`.

**UI guard test:** copy `tests/test_limits_ui.py` helpers (`STATIC_DIR`, `_app_js_source()` stripping `//` lines, `_index_html()`, `_function_source`) and `check_js_balance` from `tests/test_static_js_syntax.py`; assert ids present, `webkitdirectory`, and `append('files', ` with three arguments.

## Shared Patterns

### Owner scoping and 404
**Source:** `agent/kb_api.py` lines 93-125. **Apply to:** `POST /{kb_id}/files`. Missing and foreign ids both give 404 `MSG_KB_NOT_FOUND`, never 403.

### Validate completely before storing; cleanup on every failure
**Source:** `agent/kb_api.py` lines 304-330 plus `remove_kb_dir` (`shared/kb_storage.py` 67-71). **Apply to:** create and add routes. For add, clean only the staging directory, never the base's `uploads/` or `index.faiss`.

### Transactions
**Source:** `agent/kb_indexer.py` lines 97-102 and 295-297.
```python
try:
    await session.commit()
except Exception:
    await session.rollback()
    raise
```
**Apply to:** every write in the add job.

### Logging
**Source:** `agent/kb_api.py` lines 289, 334; `agent/kb_indexer.py` lines 234, 267. `logger = get_logger(__name__)`; `snake_case` event + `kb_id=`, `user_id=`, `error=type(exc).__name__`; never file contents or names with secrets.

### Progress delivery
**Source:** `agent/kb_indexer.py::_update_kb` (87-105) + `agent/kb_schemas.py::kb_progress_frame`. **Apply to:** add job; `hub.publish(user_id, frame)` after commit.

### Frontend safety
**Source:** `mcpEl(tag, cls, text)` + `textContent`/`.title` (app.js 4153-4167). No `innerHTML`; source names are untrusted.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `README.md` | docs | static | No README exists; facts must be verified against `run.py`, `.env.example`, `shared/config.py`, `requirements.txt`, directory listing, and `shared/database.py::ensure_bootstrap_admin`. Use plain headings and self-contained paragraphs (retrieval target in Phase 23). |
| `kbdocument` table rebuild (inside `shared/database.py`) | migration | batch | Existing migrations only `ALTER TABLE ADD COLUMN`; the 12-step rebuild on an AUTOCOMMIT connection with `foreign_keys=OFF` is new (Research Pitfall 2, verified on a scratch DB). |
| `<input webkitdirectory>` handling | component | file-I/O | No folder input exists in the repo; use Research code examples and confirm in Playwright E2E. |

## Conventions

Convention derivation skipped (gsd-tools.cjs could not be located: plugin root unresolved in this shell). Conventions below are taken from CLAUDE.md and observed in the analogs.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | `snake_case` (`kb_api.py`, `test_kb_api.py`); static assets lowercase | not measured | not measured | named contract (per CLAUDE.md) |
| Identifier casing | Python `snake_case` / `PascalCase` classes / `UPPER_CASE` constants; JS `camelCase` | not measured | not measured | named contract (per CLAUDE.md) |
| Export style | Python module-level public names, `_private` helpers; no `__all__` | not measured | not measured | named contract |
| Import style | stdlib -> third-party -> local, direct `from agent.x import y` | not measured | not measured | named contract |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` CJS `module.exports`/`require` vs `sdk/src/**` ESM `export`/`import`) is the prototype intentional-contested split; it does not apply to this Python/vanilla-JS repo. Here, match the local style of the directory being edited (Python in `agent/`, `shared/`, `tests/`; non-module vanilla JS in `ui/static/app.js`).

## Metadata

**Analog search scope:** `agent/kb_*.py`, `shared/{models,database,kb_storage}.py`, `ui/static/{app.js,index.html}`, `tests/{kb_helpers,test_kb_api,test_limits_ui}.py`
**Files read:** 10 (plus CONTEXT.md and RESEARCH.md)
**Pattern extraction date:** 2026-10-13
