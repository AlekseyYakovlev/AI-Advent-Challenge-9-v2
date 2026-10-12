# Phase 21: Project docs in RAG (Day 31) - Research

**Researched:** 2026-10-13
**Domain:** Extending the shipped v3.0 knowledge base (FastAPI + SQLModel/SQLite + FAISS + vanilla JS): folder upload, relative-path sources, incremental add, replace-on-same-source, limits, README
**Confidence:** HIGH for the code map and design (read from source, key mechanics executed in this session); MEDIUM for browser-behaviour claims (MDN only partially confirms)

## Summary

The phase is an extension of existing code, not new technology. No new packages are needed. The work splits into five areas: (1) write `README.md`; (2) change the upload path so a source name is a sanitised relative path (`docs/API_SPEC.md`) instead of a basename, and move document identity from content hash to source name; (3) add a new route plus background job that adds files to a READY base without changing its status (FAISS index patched, not rebuilt); (4) raise `MAX_FILES` and expose all limits to the page from a single source (`agent/kb_limits.py`); (5) frontend: second input with `webkitdirectory`, an accumulating pending list, an add-files modal and card states.

The two decisions that matter most: **(a) `KbDocument` has a table-level UNIQUE (`kb_id`, `sha256`)**, which makes "same content under two source names" and "same base name in different folders with the same text" fail with `IntegrityError`; `create_all` will not alter an existing table, so existing DBs need a SQLite table rebuild (verified below, it must run on an AUTOCOMMIT connection with `foreign_keys=OFF`, otherwise `DROP TABLE kbdocument` cascades and wipes `kbchunk`). **(b) The existing failure path `_fail()` deletes every chunk of the base**; an addition must never reuse it (D-09). The addition needs its own commit/rollback path that leaves status READY, old chunks and the old index file untouched.

Starlette 1.6.0 preserves `/` in multipart filenames (verified), so the browser can send the relative path as the multipart filename via `formData.append('files', file, sourceName)`. `webkitRelativePath` is NOT sent automatically; without the third argument the server only ever sees the basename.

**Primary recommendation:** Keep `KbDocument.filename` as the source name (relative path), key identity on (`kb_id`, `filename`), keep `sha256` only for "unchanged" detection, implement addition as a separate job (`run_add_job`) that embeds first, then in one short DB transaction inserts new chunks BEFORE deleting replaced ones, patches a fresh copy of the FAISS index in memory, writes it atomically and swaps `kb_index_cache`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**README**
- **D-01:** `README.md` is written in English, like the documents in `docs/`.
- **D-02:** It covers: what the app is, the directory structure (`agent/`, `ui/`, `shared/`, `scripts/`, `tests/`, `docs/`), install and run, the main `.env` settings, and links to the four documents in `docs/`. It describes the code as built; every claim is checked against the source.

**Folder selection**
- **D-03:** Choosing a folder takes every supported file (`.pdf`, `.txt`, `.md`) from every nested directory. No directories are skipped by name: hidden and service directories (`.git`, `.planning`, `node_modules`, `__pycache__`) are NOT filtered out. The user chose this explicitly over a skip list.
- **D-04:** Files with unsupported extensions are skipped, and the form shows how many were taken and how many were skipped before the upload starts (KBUP-01).
- **D-05:** The source name of a file chosen through a folder is its relative path including the chosen folder's own name, with forward slashes (choosing `docs` gives `docs/API_SPEC.md`). A file chosen singly keeps its plain file name.

**Pending list**
- **D-06:** Selections accumulate: each new choice of files or of a folder is appended to one pending list instead of replacing it. Every entry is shown with its source name and size and can be removed before the upload starts (KBUP-02).
- **D-07:** Choosing something whose source name is already in the pending list does not add a second entry.

**Addition to an existing base**
- **D-08:** A ready knowledge base stays ready and searchable while files are being added; earlier fragments keep answering. The base's card shows the progress of the addition. The base is not moved back to the "indexing" status that blocks search.
- **D-09:** If an addition fails, the base stays exactly as it was before the addition, and the card shows the error message. No partial fragments from the failed addition remain searchable.
- **D-10:** Added files are indexed with the base's own strategy, chunk size, overlap and embedding model; the user is not asked for these again (KBUP-05).
- **D-11:** Addition is offered only for a base in the ready status.

**Repeated files**
- **D-12:** A file whose source name already exists in the base and whose content changed replaces that file's fragments, without a confirmation prompt (KBUP-06).
- **D-13:** A file whose source name already exists and whose content is unchanged is skipped.
- **D-14:** After an addition the user sees a summary: added N, replaced M, unchanged K.
- **D-15:** A file that is in the base but absent from the new selection is left untouched; an addition never deletes documents.

**Limits**
- **D-16:** The file-count limit of one upload is raised from 10 to a named constant in `agent/kb_limits.py` and is shown in the form; the per-file and total-size limits stay. Exceeding a limit rejects the whole upload with a clear message and stores nothing (KBUP-04).

### Claude's Discretion
- The exact new value of the file-count limit. Because D-03 takes everything from a folder, it must be large enough for a documentation tree (a value around 200 is the starting suggestion) while the 100 MB total cap keeps the request bounded.
- How the same content under two different source names is stored. Today `KbDocument` has a unique constraint on (`kb_id`, `sha256`); KBUP-03 requires two files with the same base name in different folders to be separate sources, so identity must move to the source name.
- The layout of the pending list, the "add folder" control and the "add files" control on a base's card, within the existing Tailwind look.
- How the index is updated on addition and replacement (rebuild or incremental), as long as D-08 and D-09 hold.
- Wording of the Russian UI texts and of the summary.

### Deferred Ideas (OUT OF SCOPE)
- Code and schema files (`.py`, `.json`, `.yaml`, `.sql`) in a knowledge base - KBUP-07.
- Removing a single file from a base - KBUP-08.
- A skip list for service directories - rejected for now by the user (D-03).
- Automatic re-indexing of project documents at start-up.
- Out of scope for the phase: `/help` and "project documentation base" mark (Phase 23), MCP servers (Phase 22).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DOC-01 | `README.md` at repo root: purpose, directory structure, install/run, main `.env` settings, links to `docs/` | "README facts to verify" section; `.env.example`, `shared/config.py`, `run.py`, `requirements.txt` are the sources; no README exists today |
| KBUP-01 | Folder choice, recursive, unsupported skipped, skipped count shown before upload | `<input webkitdirectory>` + client-side extension filter (UI-SPEC section 1, 2); `webkitRelativePath` |
| KBUP-02 | Accumulating pending list, per-entry removal | Frontend state pattern (`state.kbSelectedFiles` generalised to entries `{source, file}`) |
| KBUP-03 | Relative path as source name in file list, search, answer sources; same base name in different folders stay separate | Multipart filename = relative path (3rd arg of `FormData.append`); server sanitiser; identity moves from sha to filename; DB migration |
| KBUP-04 | Higher named file-count limit shown in form; limit/size violation rejected, nothing stored | `MAX_FILES` in `agent/kb_limits.py`; new `GET /api/v1/kb/limits`; message templates built from constants; existing validate-before-store pattern |
| KBUP-05 | Add to ready base, only added files indexed, base settings reused, old fragments searchable throughout | New route + `run_add_job`; FAISS `remove_ids`/`add_with_ids` on a copy; status stays READY, `phase` marks "adding" |
| KBUP-06 | Same source name replaces fragments, no duplicates | Classification added/replaced/unchanged by (filename, sha256); insert-new-then-delete-old |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- No Docker/Redis/RabbitMQ/Celery; no `multiprocessing`/`os.fork`; the app stays Python; IPC is REST + WebSocket only.
- Frontend: vanilla JS + CDN only, no bundler/npm. Never insert HTML without `DOMPurify.sanitize()`; this phase uses `textContent` only (UI-SPEC).
- Type hints everywhere; `async`/`await` for all I/O; `structlog` via `get_logger(__name__)`, never `print()`; log `str`-free keys such as `kb_id=`, `error=type(exc).__name__`; never log secrets or file contents.
- `datetime.now(timezone.utc)`, never `utcnow()`. `.is_(None)` not `== None`. Always `await session.commit()` after writes and `await session.rollback()` in exception handlers. No bare `except:`.
- SQLModel FK cascade via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`, never `Field(ondelete=...)`.
- All data scoped by `user_id` (KB already owner-scoped; new route must use `_get_owned_kb`, answer 404 for foreign ids).
- Tests: `pytest` + `pytest-asyncio` (auto), `respx` for HTTP; separate test DB. `docs/TESTING_GUIDE.md` lists required scenarios - update it when adding tests.
- Browser/UAT checks run on an isolated copy at ports 18000/18001 (memory feedback); never kill the user's 8000/8001 app. Planning docs in English; UI strings in Russian.
- No linter/formatter configured. Commit messages: no `Co-Authored-By: Claude` line (user's global instruction overrides the harness attribution reminder).
- GSD workflow: edits go through a GSD command.
- No project skills found in `.claude/skills` or `.agents/skills`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Folder/file picking, pending list, skipped count, client-side limit hints | Browser / Client | - | Only the browser sees `webkitRelativePath`; UI-SPEC makes counts client-side |
| Source-name normalisation and sanitisation (authoritative) | API / Backend | Browser (display only) | Client values are untrusted; server must re-validate every name |
| Limit enforcement (count, per-file, total) | API / Backend | Browser (early feedback) | Server is the single source of truth (`agent/kb_limits.py`); UI must read values from it |
| Classification added/replaced/unchanged | API / Backend | - | Needs DB rows (existing filename + sha) |
| Chunk/embed/index patching | API / Backend (background task) | - | Existing `kb_indexer` pipeline; CPU parts via `asyncio.to_thread` |
| Document/chunk identity and replace | Database / Storage | API | (`kb_id`, `filename`) identity; `KbChunk.id` doubles as FAISS id |
| Progress and summary delivery | API (`/ws/events`) | Browser | Existing `kb_progress` frame; summary is transient |
| README.md | Repository (static file) | - | Not served by the app; must be accurate to code |

## Standard Stack

No new libraries. Everything needed is already in `requirements.txt` and installed.

### Core (already present, versions verified with `pip list` in this session)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.141.1 | Routes, multipart `UploadFile` | Existing |
| Starlette | 1.6.0 | Multipart parser (`max_files`/`max_fields` default 1000) | Existing; preserves `/` in filenames (verified) |
| python-multipart | 0.0.32 | Form parsing | Existing |
| SQLModel / SQLAlchemy | 0.0.42 / 2.0.52 | ORM, `create_all` | Existing |
| faiss-cpu | 1.15.1 | `IndexIDMap2(IndexFlatIP)`; `remove_ids` + `add_with_ids` verified | Existing |
| numpy | 2.5.3 | Vector arrays | Existing |
| pytest / pytest-asyncio / respx | 9.1.1 / 1.4.0 / 0.23.1 | Tests | Existing |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Patch FAISS index (remove + add) | Rebuild the whole index from all chunks | Rebuild needs every vector; vectors are NOT stored in SQLite, only in the index, so a rebuild would have to `reconstruct` every vector or re-embed everything (violates "only added files are indexed"). Patching is both cheaper and simpler. |
| Table rebuild migration | Keep the old unique constraint and store a per-source fingerprint in `sha256` | Zero migration, but overloads the column meaning and hides intent. Acceptable fallback if the rebuild is judged too risky (see Open Question 1). |

**Installation:** none.

**Version verification:** `pip list` run in this session (above). `Python 3.13.15`. SQLite library 3.44.3 (CLI); the app uses Python's bundled sqlite3.

## Package Legitimacy Audit

No external packages are installed by this phase. slopcheck not run (nothing to check).

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Existing Implementation Map

All claims below are [VERIFIED: codebase read this session].

### Models (`shared/models.py`)
- `KnowledgeBase` (line 669): `status` (`KbStatus`: QUEUED/INDEXING/READY/FAILED), `error` (nullable Text), `strategy`, `chunk_size`, `chunk_overlap`, `embedding_model`, `dim`, `query_prefix`, `doc_prefix`, `file_count`, `chunk_count`, `done_chunks`, `total_chunks`, `phase` (str|None), `created_at`, `updated_at`.
- `KbDocument` (line 697): `__table_args__ = (UniqueConstraint("kb_id", "sha256", name="uq_kbdocument_kb_sha256"),)`; columns `id, kb_id (FK CASCADE), filename, sha256(64), size_bytes, page_count, stored_name`.
- `KbChunk` (line 715): `id` (also the FAISS vector id), `kb_id`, `document_id` (FK CASCADE to `kbdocument`), `chunk_index`, `chunk_id` (`"{doc.id}-{i}"`), `text`, `section`, `source` (= `doc.filename`), `title` (= `Path(filename).stem`), `page_start`, `char_start`, `char_end`.
- Neither table uses `AUTOINCREMENT` (SQLAlchemy default for an Integer PK on SQLite), so deleting the highest rowid lets the next insert reuse it - see Pitfall 4.

### Limits (`agent/kb_limits.py`)
`MAX_FILE_BYTES = 50 MiB`, `MAX_FILES = 10`, `MAX_TOTAL_BYTES = 100 MiB`, `MAX_REQUEST_BYTES = total + 1 MiB`, `ALLOWED_EXTENSIONS = {.pdf,.txt,.md}`, plus chunking/embedding/search constants. Imported by name into `agent/kb_api.py`, and tests `monkeypatch.setattr(kb_api, "MAX_FILE_BYTES", ...)` / `"MAX_TOTAL_BYTES"` - keep the module-level imported names.

### Upload route (`agent/kb_api.py`)
- `create_kb` (line 272): `POST /api/v1/kb`, dependencies `require_allowed_origin`, `_require_content_length_within_cap` (411 if no Content-Length, 413 over `MAX_REQUEST_BYTES`). Order: `_validate_settings` -> `_clean_filenames` -> insert `KnowledgeBase(QUEUED)` + `flush` -> `_store_all` -> add `KbDocument` rows -> `commit` -> `kb_indexer.spawn_index_job` -> `hub.publish(kb_progress_frame)`; `UploadRejectedError` -> rollback + `remove_kb_dir` + 422; `BaseException` -> `asyncio.shield(rollback)` + remove dir.
- `_clean_filenames` (line 153): rejects empty list (422 `MSG_FILES_REQUIRED`), `len > MAX_FILES` (422 `MSG_TOO_MANY_FILES`, text hard-codes "10"), reduces each name to `Path(name.replace("\\","/")).name`, validates the extension.
- `_store_upload`/`_store_all` (lines 174-220): streams to `uploads/{index:02d}.part`, enforces `MAX_FILE_BYTES`/`MAX_TOTAL_BYTES`/non-empty while streaming, renames to `{index:02d}_{sha[:12]}{suffix}` (stored name is server-generated, never user-derived), and rejects a repeated **sha** with `MSG_DUPLICATE` ("Файл {name} уже добавлен.").
- Messages hard-code "10 файлов", "50 МБ", "100 МБ" (lines 59-62). Router order: `GET /embedding-models` is declared before `GET /{kb_id}`; any new static GET route (`/limits`) must also be declared before `/{kb_id}` or it will be matched as a kb id and answer 422.
- No route lists a base's documents. "The file list" of success criterion 3 is therefore the pending list in the form (UI-SPEC section 3) plus test-search/answer sources; no new document-listing endpoint is required (flagged as Open Question 3).

### Indexer (`agent/kb_indexer.py`)
- `spawn_index_job` (75): `asyncio.create_task(run_index_job)`, stored in `agent.state.kb_jobs[kb_id]` (one task per KB), removed by a done-callback. `delete_kb` -> `cleanup_kb_caches` cancels this task.
- `run_index_job` (224): global single-slot `asyncio.Semaphore(1)` -> set INDEXING -> `ensure_embedding_model` -> `_parse_documents` (selects ALL `KbDocument` of the KB) -> `_insert_chunks` (chunks committed BEFORE embedding so each has an id) -> `_embed_all` (batches of 32, throttled progress via `_update_kb`) -> `_build_and_write_index` -> READY. Failures -> `_fail`.
- `_fail` (277): **deletes all `KbChunk` of the KB**, sets FAILED, removes the index file. Right for initial indexing, destructive for an addition.
- `_chunk_document(kb, path, filename)` (108) and `load_document(path, filename)` (`agent/kb_loaders.py:232`) already work with a relative-path `filename` (they only use `Path(filename).suffix/stem`; error texts include the name). Reusable unchanged.
- `recover_orphaned_kb_jobs` (330): only handles QUEUED/INDEXING; it would leave a READY base with a stale `phase` after a crash mid-addition.
- `_update_kb(kb_id, publish=..., **values)` (87) opens its own session, commits, and publishes `kb_progress_frame` - reusable for addition progress.
- `embed_passages(model_id, texts)` applies the model's doc prefix itself (`agent/embeddings.py:207`).

### Storage / search
- `shared/kb_storage.py`: `uploads_dir(user_id, kb_id)`, `index_path`, `build_index` (IDMap2 + IndexFlatIP, L2-normalised), `write_index_bytes` (tmp + `os.replace`, bytes via Python I/O for non-ASCII Windows paths), `read_index_bytes`.
- `agent/kb_search.py`: `search_kb_vectors` requires `status == READY`; `load_index_cached` caches in `kb_index_cache[kb.id]` and raises `KbIndexCorruptError` when `index.ntotal != kb.chunk_count` or `index.d != kb.dim`; hits whose chunk row is missing are silently skipped (`row is None: continue`, line 97). Result `source` = `KbChunk.source` verbatim.
- `shared/database.py`: `ensure_kb_chunk_fts` creates FTS5 `kb_chunk_fts` with AFTER INSERT and AFTER DELETE triggers on `kbchunk` (no UPDATE trigger) - inserting/deleting chunks keeps lexical search in sync automatically; never UPDATE chunk text in place.
- Source flows unchanged to: `agent/rag_fts.py:58`, `agent/rag_pipeline.py:334`, `agent/rag.py:195,247` (whitespace-normalised), `agent/rag_cite.py:130`, `GET /api/v1/kb/{kb_id}/chunks/{chunk_id}?file=` (`agent/rag_api.py:231`, compares `chunk.source != file`, query `max_length=500`).

### Events
`kb_progress_frame(kb)` = `{"type":"kb_progress","kb": KbOut}`; `KbOut` fields: id, name, status, error, strategy, chunk_size, chunk_overlap, embedding_model, dim, file_count, chunk_count, done, total, phase, created_at.

### Frontend (`ui/static/app.js`, `index.html`)
- `index.html:543-548`: `<input type="file" id="kb-files" multiple accept=".pdf,.txt,.md">` and `<ul id="kb-file-list">` inside `#kb-create-modal`.
- `app.js`: state at 4034-4040 (`state.kbSelectedFiles`, `kbModalOpener`, ...); `ruPlural` (4044); `upsertKb` (4062) replaces the whole kb object on each frame; `kbStatusChip` (4085); `kbProgressBar` (4108); `buildKbCard` (4152; actions row 4184-4194); `closeKbModal` (4227); `formatKbFileSize` (4237); `renderKbFileList` (4242; shows `file.name`); `openKbCreateModal` (4311; resets list); `submitKbCreate` (4347; `formData.append('files', file)` - basename only, posts directly to `${AGENT_BASE}/api/v1/kb`); `buildKbResultCard` (4415; `item.source` shown, no `title`); `bindKbUi` (5177; `#kb-files` change handler dedupes by name+size). `kb_progress` handler at 3614 calls `upsertKb` then `renderKbPanel`.
- Source display for answers uses `src.file`/`quote.file` with `truncate` + `title` (5017-5018, 5095-5096) - already path-friendly; test-search result head (4420) has no `truncate`/`title` yet (UI-SPEC section 6 wants it).

### Existing tests touching this area
`tests/test_kb_api.py` (create/validate; `too-many` uses 11 files, `duplicate` uses same-content files, `test_path_traversal_filename_is_sanitised` expects `evil.txt`), `tests/test_kb_models.py::test_kb_document_unique_sha` (asserts `IntegrityError`), `tests/test_kb_indexer.py`, `tests/test_kb_storage.py`, `tests/test_kb_lifecycle.py` (orphan recovery, shutdown), `tests/test_kb_events.py`, `tests/test_kb_search.py`, `tests/test_kb_scoping.py`, `tests/kb_helpers.py` (`seed_kb`, `install_fake_embedder`, `wait_until`), `tests/test_static_js_syntax.py`, `tests/test_*_ui.py` (source-guard style), `scripts/e2e_kb_playwright.py` (line 259 `set_input_files("#kb-files", ...)`, lines 465-469 assert the duplicate-by-content behaviour that is being removed), `scripts/rag_eval.py:582` (creates `KbDocument` with sha256 directly - still valid).

## Architecture Patterns

### System Architecture Diagram

```
Browser (Tailwind/vanilla JS, :8000 page)                       Agent (:8001)
------------------------------------------                       ---------------------------------------------
[Выбрать файлы] input#kb-files ──┐
[Выбрать папку] input#kb-folder  ├─> normalise: source = webkitRelativePath || name
 (webkitdirectory)               │    filter ext (.pdf/.txt/.md) ; count taken/skipped
                                 └─> pending list (dedupe by source, remove, limits from GET /kb/limits)
                                          │ submit
                                          ▼ multipart: files[i] with filename = source (3rd arg of FormData.append)
 POST /api/v1/kb            (create)  ─┐
 POST /api/v1/kb/{id}/files (add)     ├─> origin check ─> Content-Length cap ─> owner check(404) ─> [add: status==READY & not adding, else 409]
                                       └─> _clean_source_names (normalise, sanitise, ext, dup-by-name, count<=MAX_FILES)
                                           └─> stream each file to staging (server-generated names) with size caps; sha256
                                               any violation -> 422, delete staged files, nothing stored
                       create ───────────> KnowledgeBase(QUEUED)+KbDocument rows ─> spawn_index_job ─> (existing pipeline) ─> READY
                       add ──> classify vs existing KbDocument(kb_id, filename): added | replaced (sha differs) | unchanged (sha equal, staged file deleted)
                               claim: UPDATE knowledgebase SET phase='adding' WHERE id=? AND status='ready' AND phase IS NULL (rowcount==1 else 409)
                               └─> 202 KbOut ─> spawn_add_job(kb_id,user_id,plan)  [kb_jobs[kb_id]; global indexing semaphore]
                                     ensure_embedding_model ─> parse+chunk staged files (to_thread) ─> embed batches (progress frames, status stays READY)
                                     ─> ONE short DB tx: insert new KbDocument+KbChunk (ids > old) ; delete replaced docs/chunks ; kb.file_count/chunk_count
                                     ─> copy of current index (read_index_bytes) : remove_ids(old) + add_with_ids(new, normalised)
                                     ─> commit ; os.replace(index) ; kb_index_cache swap ; delete replaced stored files
                                     ─> frame {kb:..., add_summary:{added,replaced,unchanged}}
                                     failure/cancel: rollback, delete staged files, phase=None, error=msg, READY and old index untouched
Search (/kb/{id}/search, RAG) keeps working on the old cached index throughout (status READY)
```

### Recommended changes by file
```
shared/models.py            KbDocument: drop uq (kb_id, sha256); add Index/UniqueConstraint on (kb_id, filename) per decision; docstring "filename = source name (relative path)"
shared/database.py          migrate_kbdocument_drop_sha_unique(): table rebuild on AUTOCOMMIT conn (see Code Examples); called first in init_db
agent/kb_limits.py          MAX_FILES = 200 (named, single source); MAX_SOURCE_NAME_CHARS (<=300, below rag_api's 500 query cap); ADD_PHASE constant optional
agent/kb_api.py             _clean_source_names (replaces _clean_filenames); remove sha-dup rule; build messages from constants; GET /limits (before /{kb_id}); POST /{kb_id}/files
agent/kb_indexer.py         run_add_job + _fail_add (non-destructive); recover_orphaned_kb_jobs resets stale READY phase and cleans staging
agent/kb_schemas.py         kb_progress_frame(kb, add_summary=None) optional transient key; no new DB columns required
shared/kb_storage.py        staging dir helper (uploads/_staging/<uuid>), remove helper; index patch helper (copy, remove_ids, add_with_ids)
ui/static/index.html        folder input, pending header/list/report ids, #kb-add-modal (distinct ids)
ui/static/app.js            pending-list module (shared by create + add modals via a small factory), card states, limits loader
tests/ docs/ scripts/       new tests; update the tests/docs/e2e listed above; README.md at repo root
```

### Pattern 1: Source name = sanitised relative path (server-authoritative)
**What:** One function turns the client-supplied multipart filename into the stored source name.
**Rules (prescriptive):**
1. `unicodedata.normalize("NFC", name)`; replace `\` with `/`.
2. Split on `/`; drop empty and `.` segments and drop `..` segments (existing test expects `"../../evil.txt"` -> `evil.txt`, so drop rather than reject); strip a leading drive prefix such as `C:`; strip control characters `\x00-\x1f`.
3. Join with `/`; last segment must have an allowed extension (case-insensitive); empty result -> 422 `MSG_BAD_EXTENSION` with "без имени".
4. Cap length at `MAX_SOURCE_NAME_CHARS` (<= 300) -> 422 with a clear message; `rag_api.get_chunk_snippet` limits `file` to 500 chars.
5. Compare source names case-sensitively and exactly (decision to record); detect duplicates inside one request by source name -> `MSG_DUPLICATE` (replaces sha-based duplicate).
**Why:** Stored files never take user-derived paths (`stored_name` is server-generated), so traversal is not a filesystem risk here; the risks are identity confusion (NFC/NFD, `a/../b`), prompt/UI injection through names, and overlong names. Keep UI rendering via `textContent`.

### Pattern 2: Addition as a separate non-destructive job
**What:** Never reuse `run_index_job`/`_fail`. New `run_add_job(kb_id, user_id, plan)`.
**Steps:** acquire the shared indexing semaphore (serialises LM Studio use) -> `ensure_embedding_model(kb.embedding_model)` -> chunk staged files in `asyncio.to_thread` with `_chunk_document` (base strategy, size, overlap) -> embed all new chunk texts in batches (reuse `EMBED_BATCH_SIZE`, throttled `_update_kb(phase="adding", done_chunks=..., total_chunks=...)`; embed BEFORE touching the DB so the write transaction is short) -> verify `len(vector) == kb.dim` -> single DB transaction (new documents/chunks first, then delete replaced documents/chunks, update `file_count`, `chunk_count`, clear `phase`/`error`) -> patched index -> commit -> `os.replace` -> cache swap -> publish.
**Failure/cancel:** rollback; delete staged files; set `phase=None`, `error=<message>` on a READY base; never touch chunks or the index file; publish frame. `CancelledError` (base deleted or shutdown) must clean staging and re-raise without resurrecting the base.
**Progress/"adding" signal:** reuse `phase` (e.g. `"adding"`) with `status` staying `ready`; the UI shows "addition running" when `status === 'ready' && phase`. This answers the UI-SPEC open question on page-load state without a schema change (`KbOut` already carries `phase`, `done`, `total`).
**Error persistence:** reuse `KnowledgeBase.error` on a READY base (currently only meaningful for FAILED; `kb_search` ignores it). Clear it at the start of the next addition. UI shows the red line when `status==='ready' && error`.
**Summary:** transient only. UI-SPEC says it clears on reload, so publish it once inside the final frame (extra key `add_summary` or a sibling `kb_add_done` frame) and keep it in a front-end map keyed by kb id. Because `upsertKb` replaces the whole kb object on every frame, do NOT store the summary on the kb object.

### Pattern 3: Atomic admission of an addition (no race)
Between validation and `spawn`, the route awaits file streaming, so two concurrent requests can both pass an in-memory check. Claim the base atomically with a conditional UPDATE (`status = READY AND phase IS NULL` -> set `phase='adding'`, check `rowcount == 1`, else 409 "Идёт добавление файлов" / "База ещё не готова"). Do the claim after the files are staged and classified, release it (phase=None) on any rejection path. This also gives D-11 and the page-load state for free.

### Anti-Patterns to Avoid
- **Reusing `_parse_documents`** for additions: it selects every document of the KB and would re-chunk and duplicate the whole base.
- **Inserting `KbDocument` rows at upload time for an addition**: a failed addition would then leave rows (D-09). Keep the plan in memory (job arguments) and insert rows in the final transaction.
- **Basename reduction** (`Path(name).name`) anywhere on the new path - this is exactly what breaks KBUP-03.
- **Mutating the cached index object** in place: build the new index from a fresh `read_index_bytes` copy so concurrent searches keep a consistent old index.
- **Hard-coding limit numbers** in front-end copy or Python messages (UI-SPEC forbids drift).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Folder enumeration | JS recursive `FileSystemDirectoryEntry` walker | `<input type="file" webkitdirectory>` + `file.webkitRelativePath` | One call returns all nested files with relative paths; no drag-drop API needed |
| Incremental vector index | Rebuild / re-embed whole base | `IndexIDMap2.remove_ids(np.int64[])` + `add_with_ids` | Verified in faiss-cpu 1.15.1 in this session: removes ids, `reconstruct` of survivors still works |
| Multipart parsing | Custom parser | Starlette `UploadFile` (already used) | Preserves slashes in filenames (verified) |
| Atomic file swap | Manual temp handling | `write_index_bytes` (tmp + `os.replace`) | Already handles non-ASCII Windows paths |
| Progress transport | New channel | `hub.publish(user_id, kb_progress_frame(kb))` + `_update_kb(publish=...)` throttling | Existing `/ws/events` |
| Plurals | New helper | `ruPlural` | Exists |
| Text-to-HTML | innerHTML | `mcpEl(...)` + `textContent` | Existing helper, no DOMPurify needed |

**Key insight:** every piece except the DB identity change and the non-destructive add job already exists; the risk is in reusing destructive paths (`_fail`, `_parse_documents`, sha uniqueness) by accident.

## Runtime State Inventory

This is not a rename phase, but the identity change touches stored state, so the five categories are answered explicitly.

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | Existing `kbdocument` rows (basename `filename`, possibly duplicate filenames per KB because the old UI de-duplicated by name+size and the server by sha) and `kbchunk.source` = basename; saved chat messages carry `rag_sources` with chunk ids `"{doc_id}-{i}"` | Schema migration (drop sha unique). Do NOT rewrite legacy sources; old bases keep basenames. Choose non-unique-vs-unique (Open Question 1) knowing legacy duplicate filenames may exist |
| Live service config | None - verified: no external service stores KB names/paths (LM Studio only holds models) | None |
| OS-registered state | None - verified: no scheduled task or service references KB paths | None |
| Secrets/env vars | None new. `KB_STORAGE_DIR` already optional and unchanged | None |
| Build artifacts | `app_kb/<user>/<kb>/uploads/` files named `NN_<sha12>.ext` and `index.faiss` for existing bases; stored names from old uploads remain valid | None for existing; new stored names must not collide with `NN_<sha12>` (use uuid-based names) |

## Common Pitfalls

### Pitfall 1: Browser sends only the basename
**What goes wrong:** `formData.append('files', file)` sends `file.name`; `webkitRelativePath` is never serialised, so `docs/API_SPEC.md` arrives as `API_SPEC.md` and two same-named files collide.
**How to avoid:** `formData.append('files', file, sourceName)` where `sourceName` is the pending-entry source. Server tests must post `files=[("files", ("docs/a/README.md", ...))]`.
**Warning signs:** sources in test search show no folder.
[VERIFIED: Starlette preserves `docs/sub/API_SPEC.md`, `..\..\e.txt` and Cyrillic path segments in `UploadFile.filename`, scratch test this session; third-argument filename is standard `FormData.append(name, blob, filename)` - [ASSUMED] from web-platform knowledge, MDN page for `FormData.append` not fetched]

### Pitfall 2: SQLite table rebuild cascades into `kbchunk`
**What goes wrong:** `DROP TABLE kbdocument` with `PRAGMA foreign_keys=ON` performs an implicit `DELETE FROM`, which fires `ON DELETE CASCADE` on `kbchunk`. The app's connect hook sets `foreign_keys=ON` on every connection, and `PRAGMA foreign_keys` is a no-op inside a transaction (`engine.begin()` in `init_db`).
**How to avoid:** run the rebuild on a dedicated connection `engine.execution_options(isolation_level="AUTOCOMMIT").connect()`, `PRAGMA foreign_keys=OFF` first, rebuild, `PRAGMA foreign_keys=ON`, then `PRAGMA foreign_key_check` (empty). Make it idempotent (inspect `sqlite_master` for `uq_kbdocument_kb_sha256` / `sqlite_autoindex_kbdocument_1` first). Must run BEFORE `create_all` in `init_db` and be skipped when the table does not exist.
**Warning signs:** `kbchunk` count drops to 0 after startup.
[VERIFIED: executed on a scratch DB this session - `fk pragma 0`, `fk check []`, `chunks after 1`]

### Pitfall 3: `create_all` does not alter existing tables
Changing `__table_args__` only affects fresh DBs (tests). Existing `app.db` keeps `uq_kbdocument_kb_sha256` until migrated, and the first add of identical content under another name would raise `IntegrityError` there while tests (fresh DB) pass. Add a migration test that builds the OLD schema, inserts documents and chunks, runs `init_db`, and asserts chunks survive and identical-sha rows are allowed.

### Pitfall 4: Rowid reuse corrupts FAISS ids and old citations
`KbChunk.id` is the FAISS id and `KbDocument.id` forms `chunk_id`. Without AUTOINCREMENT, deleting the highest rowid and then inserting reuses it. If old chunks are deleted first, new chunks can receive ids still present in the old (cached) index, so a concurrent search could pair an old vector with new text; old chat citations (`"{doc_id}-{i}"`) could resolve to different content.
**How to avoid:** in the same transaction INSERT new documents/chunks first, then DELETE replaced ones, so new ids are strictly greater than every existing id at insert time. Never UPDATE a document row in place for a replacement.

### Pitfall 5: `_fail` / `recover_orphaned_kb_jobs` semantics
Using `_fail` for a failed addition deletes the whole base's chunks and index (violates D-09). Conversely `recover_orphaned_kb_jobs` ignores a READY base left with `phase='adding'` after a crash: the card would show a permanent "adding" state and the add button stays disabled. Extend recovery: for READY bases with non-null `phase`, set `phase=None`, optionally `error=MSG_INTERRUPTED_RESTART`-style message for adds, and remove `uploads/_staging`.

### Pitfall 6: Stored-name collisions across requests
Today `_store_upload` writes `{index:02d}.part` and `{index:02d}_{sha12}.ext` into one `uploads/` dir; a second request into the same base reuses `00.part`/`00_…` names (two concurrent adds would clobber each other's `.part`; identical content under two sources in different requests would share one stored file, and deleting one would break the other). Use a per-request staging subdirectory plus `uuid4().hex` stored names; after commit, move/keep the files in `uploads/`, delete the superseded stored file of a replaced document only after commit succeeds.

### Pitfall 7: Index/DB consistency window
`load_index_cached` raises `KbIndexCorruptError` when `index.ntotal != kb.chunk_count`. Do the DB commit (counts) and `os.replace` back-to-back and swap `kb_index_cache[kb_id]` to the new in-memory index right after; do not pop the cache first (a cold reload between commit and replace would read the old file and report corruption). A crash between commit and replace leaves a corrupt base - a millisecond window accepted and documented; optional hardening: startup check for READY bases (`ntotal != chunk_count`) that marks them for the user.

### Pitfall 8: Starlette multipart limits and Content-Length
Starlette defaults: `max_files=1000`, `max_fields=1000`, `max_part_size=1MB` for non-file parts. 200 files is fine; a folder of >1000 files produces a framework-level 400 before app validation. The client should block over-limit submits (UI-SPEC) so this is not normally reached. `_require_content_length_within_cap` requires `Content-Length`; `fetch` with `FormData` sets it. Reuse the same dependency for the add route.

### Pitfall 9: SQLite single writer
Keep write transactions short: embed first (no DB held), then one commit. `_update_kb` progress updates open separate short sessions; WAL + `busy_timeout=5000` is already configured. Do not hold a session open across `await embed_passages`. Use `retry_on_locked_db` only if a test shows lock errors (the existing indexer does not).

### Pitfall 10: Unchanged-detection and legacy rows
Compare `sha256` of the staged file with the existing `KbDocument.sha256` for the same `filename`. Legacy documents have basename filenames, so `docs/API_SPEC.md` will not match legacy `API_SPEC.md` - it is simply "added" (acceptable, documented). Line-ending differences (Windows CRLF vs LF) change the hash: a re-add of an unchanged file from a different checkout counts as "replaced" (harmless). Hash raw bytes, not normalised text.

### Pitfall 11: Folder with many files and D-03
D-03 means choosing the project root can pull `.planning/**` (hundreds of `.md`). That legitimately exceeds the limit and is rejected client-side first; the message must say how many were selected vs allowed (UI-SPEC copy). Counting happens after the extension filter.

### Pitfall 12: Drift between create and add paths
The create modal and the add modal need their own pending state and ids (UI-SPEC). Implement one pending-list controller (factory taking element ids) instead of copy-pasting; keep `tests/test_static_js_syntax.py` green (template literals/regex scanning is homemade - avoid exotic syntax in new code).

## Code Examples

### Send the source name as the multipart filename
```javascript
// Source: web platform FormData.append(name, blob, filename); verified server side in this session
pending.forEach((entry) => formData.append('files', entry.file, entry.source));
// entry.source = file.webkitRelativePath || file.name  (normalise '\\' -> '/')
```
[CITED: developer.mozilla.org/en-US/docs/Web/API/File/webkitRelativePath - "path of the file relative to the ancestor directory the user selected"; that the path starts with the chosen folder's name and uses `/` is MDN-silent, treated as [ASSUMED] from Chromium behaviour and covered by UI-SPEC rule 2; confirm in the Playwright E2E]

### Folder filtering and counts (client)
```javascript
function takeFolderFiles(fileList) {
    const all = Array.from(fileList);
    const kept = all.filter((f) => KB_EXT_RE.test(f.name));   // /\.(pdf|txt|md)$/i
    return { kept, skipped: all.length - kept.length, folder: (all[0]?.webkitRelativePath || '').split('/')[0] };
}
```

### SQLite rebuild migration (idempotent; verified shape)
```python
# Source: executed on a scratch DB this session (SQLite 12-step table rebuild)
async def migrate_kbdocument_drop_sha_unique() -> None:
    """Rebuild kbdocument without UNIQUE(kb_id, sha256) (idempotent)."""
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        row = (await conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='kbdocument'")).first()
        if row is None or "uq_kbdocument_kb_sha256" not in (row[0] or ""):
            return
        await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            # CREATE TABLE kbdocument_new (...same columns, FK kb_id CASCADE...) ; INSERT ... SELECT ;
            # DROP TABLE kbdocument ; ALTER TABLE kbdocument_new RENAME TO kbdocument ; recreate indexes
            ...
        finally:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        # PRAGMA foreign_key_check must return no rows
```
Recreate `ix_kbdocument_kb_id` (the model declares `index=True` on `kb_id`) and the new (`kb_id`, `filename`) index. Wrap in try/except that logs `kbdocument_migration_failed` and re-raises (app must not start on a half-migrated table).

### FAISS patch on a fresh copy
```python
# Source: verified in faiss-cpu 1.15.1 this session
def patch_index_bytes(path, remove_ids, new_vectors, new_ids):
    index = read_index_bytes(path)                      # fresh copy, not the cached object
    if remove_ids:
        index.remove_ids(np.asarray(remove_ids, dtype="int64"))
    if len(new_ids):
        matrix = np.ascontiguousarray(new_vectors, dtype="float32")
        faiss.normalize_L2(matrix)
        index.add_with_ids(matrix, np.asarray(new_ids, dtype="int64"))
    return index                                        # caller: write_index_bytes(path, index); kb_index_cache[kb_id] = index
```

### Atomic claim of the base
```python
# Source: SQLite single-writer semantics; pattern for this phase
result = await session.exec(
    update(KnowledgeBase)
    .where(KnowledgeBase.id == kb_id, KnowledgeBase.status == KbStatus.READY, KnowledgeBase.phase.is_(None))
    .values(phase=PHASE_ADDING, error=None, done_chunks=0, total_chunks=0)
)
await session.commit()
if result.rowcount != 1:
    raise HTTPException(409, detail=MSG_ADD_BUSY)
```

### Limits endpoint (declare before `/{kb_id}`)
```python
@router.get("/limits")
async def limits_route(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES, "extensions": sorted(ALLOWED_EXTENSIONS)}
```

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| `<input webkitdirectory>` as non-standard | Listed as "Baseline 2025 (Newly available)" on MDN | Safe to rely on in current Chromium/Firefox/Safari for this local tool |
| Identity by content hash | Identity by source name, hash only for change detection | Enables same content under different paths and replace-on-update |

**Deprecated/outdated:** none relevant.

## README facts to verify (DOC-01)

No `README.md` exists (`git ls-files` shows only the four `docs/*.md`). Sources of truth to read while writing, per D-02 ("every claim is checked against the source"): `run.py` (start-up, banner, orphan cleanup, ports), `requirements.txt`, `.env.example` + `shared/config.py` (settings: `UI_PORT`, `AGENT_PORT`, `BIND_HOST`, `DB_PATH`, `DEEPSEEK_API_KEY`, `LM_STUDIO_BASE_URL`, `LLM_TIMEOUT`, limits `RATE_LIMIT_MESSAGES_PER_MINUTE`/`LOGIN_MAX_FAILED_ATTEMPTS`/`LOGIN_LOCKOUT_SECONDS`/`MAX_CONCURRENT_GENERATIONS`, MCP settings, `KB_STORAGE_DIR`), top-level directory listing (`agent/`, `ui/`, `shared/`, `scripts/`, `tests/`, `docs/`, plus `run.py`, `pytest.ini`, `requirements.txt`; note `app.db`, `app_kb/`, `logs/`, `eval_out/` are runtime artefacts), the first-run bootstrap admin behaviour (`shared/database.py::ensure_bootstrap_admin`), and the four docs (`API_SPEC.md`, `ARCHITECTURE.md`, `TESTING_GUIDE.md`, `USER_GUIDE.md`). The README is also a retrieval target for Phase 23, so use plain headings and keep paragraphs self-contained (the structural chunker splits Markdown at headings). Add a short "Knowledge base" pointer (folder upload, add files) once the feature exists; update `docs/USER_GUIDE.md:146` ("до 10 файлов", "Одинаковые файлы добавить нельзя"), `docs/API_SPEC.md` (create caps text at 451-455, new routes, `KbOut` phase semantics, event summary) and `docs/ARCHITECTURE.md:449` ("unique on (kb_id, sha256)").

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `FormData.append(name, file, filename)` makes the browser send `filename` verbatim (slashes preserved, `"`/CR/LF percent-escaped) in the multipart header | Pitfall 1 | If a browser strips path characters, relative paths are lost; mitigated by the Playwright E2E asserting `docs/...` sources |
| A2 | Chromium's `webkitRelativePath` begins with the chosen folder name and uses `/` | Code Examples, D-05 | Source names would lack the folder prefix; UI-SPEC already normalises `\` and E2E will verify |
| A3 | Playwright `set_input_files` accepts a directory path for an `<input webkitdirectory>` | Validation / E2E | E2E for folder choice would need a file-list workaround (set the files with their relative layout); does not affect product code |
| A4 | Legacy bases may contain duplicate `filename` values per KB (old UI de-duplicated by name+size) | Runtime State Inventory | If none exist a UNIQUE (`kb_id`,`filename`) migration is safe; if some exist the migration must not add that unique index |
| A5 | A value of about 200 for `MAX_FILES` suffices for `docs/` + README and a modest tree | Limits | Too low -> users must split uploads; too high -> long single request (still bounded by 100 MB) |

## Open Questions (RESOLVED)

All five questions were closed during planning; each carries the decision the plans took. Assumptions A1-A3 (browser behaviour) are not open questions: they are verified in a real Chromium by `scripts/e2e_kb_folder_playwright.py` (Plan 21-07: its check A3 covers assumptions A2 and A3, its check A6 covers assumption A1), and the plan summary must record each as confirmed or not confirmed. A4 is made harmless by the plain index of question 1; A5 is question 5.

1. **Uniqueness of (`kb_id`, `filename`) in the DB**
   - Known: the sha unique must go; identity is by source name; legacy DBs may hold duplicate filenames (A4).
   - Unclear: enforce in the schema or in code.
   - Recommendation: rebuild `kbdocument` without any unique constraint and add a plain index on (`kb_id`, `filename`); enforce uniqueness in code (the claim + serialized job make that safe). Choosing a UNIQUE constraint requires a de-dup step in the migration. The planner should lock this in the first plan.
   - RESOLVED: uniqueness of (`kb_id`, `filename`) is enforced in code only. Plan 21-01 rebuilds `kbdocument` without any unique constraint (`migrate_kbdocument_drop_sha_unique`) and adds the plain, non-unique composite index `ix_kbdocument_kb_filename`; no de-dup step is needed, so legacy bases with duplicate file names (A4) migrate unchanged. The create route rejects a repeated source name inside one request (21-01) and the add route classifies by source name under an exclusive claim (21-04).
2. **Whether to expose "addition running" with a new column vs `phase`**
   - Recommendation: reuse `phase="adding"` + `status="ready"` (no schema change). Document it in `docs/API_SPEC.md` (`KbOut.phase`).
   - RESOLVED: no new column. A running addition is `status == "ready"` with `phase == "adding"` (`agent.kb_limits.PHASE_ADDING`, Plan 21-01). The add route sets it with a conditional UPDATE as the claim (21-04), the job clears it on success and on every failure path (21-03), the card reads it as `kb.status === 'ready' && kb.phase` (21-06), and `docs/API_SPEC.md` documents it under `KbOut` (21-07).
3. **Document listing endpoint**
   - No route returns a base's files; success criterion 3 says "sources in the file list". Interpreted as the pending list plus search/answer sources. If the user expects a per-base file list on the card, that is extra scope (a `GET /api/v1/kb/{id}/documents`) - confirm during planning; not required by DOC-01/KBUP-01..06 text.
   - RESOLVED: no per-base list - the pending list plus test search and answer sources satisfy criterion 3. No `GET /api/v1/kb/{id}/documents` route and no document list on the card are planned. "The knowledge base file list" of KBUP-03 / roadmap criterion 3 is read as the pending list of the create form and of the add modal (21-05, 21-06), where every entry shows its relative-path source name; the stored names are proven by the `kbdocument.filename` check B2 of the browser script (21-07). This is an interpretation, not a user decision: it is stated in the objective and output of Plan 21-05 and must be repeated in the summaries of 21-05 and 21-07 so the user can object. A per-base document list would be new scope for a later phase.
4. **Does the global indexing semaphore also serialise additions?**
   - Recommendation: yes (LM Studio loads one embedding model at a time); an addition queues behind a running index job; card shows "Добавление: 0 из 0" until it starts.
   - RESOLVED: yes. `run_add_job` runs inside the same single-slot `_get_semaphore()` as index jobs (Plan 21-03), so an addition queues behind a running index job or another addition; the base is already `ready` + `phase == "adding"` while it waits. An addition where every file is unchanged does not take the semaphore because it embeds nothing.
5. **Where the real value of `MAX_FILES` is decided** - Claude's discretion; propose 200 (A5).
   - RESOLVED: `MAX_FILES = 200` in `agent/kb_limits.py` (Plan 21-01), the single source of the number: the server enforces it, `GET /api/v1/kb/limits` serves it to the form (21-05 has no fallback number), and `tests/test_docs_kb_upload.py` ties the number written in the documents to the constant (21-07). The per-file (50 MB) and total (100 MB) caps are unchanged.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Everything | yes | 3.13.15 | - |
| faiss-cpu / numpy / FastAPI / Starlette / SQLModel | Implementation | yes | see table | - |
| pytest + pytest-asyncio + respx | Tests | yes | 9.1.1 / 1.4.0 / 0.23.1 | - |
| LM Studio on :1234 with an embedding model | Real indexing/E2E only | not probed (tests use `install_fake_embedder`) | - | Fake embedder for unit tests; E2E script needs it running |
| Playwright (Python) | `scripts/e2e_kb_playwright.py` style UAT | not probed this session | - | Source-guard tests for JS; manual UAT |
| sqlite3 | Migration | yes | 3.44.3 (CLI) | - |

**Missing dependencies with no fallback:** none for unit-level work.

## Validation Architecture

Skipped: `workflow.nyquist_validation` is `false` in `.planning/config.json`. Test guidance for the planner (not a formal section):
- Quick run: `pytest tests/test_kb_api.py tests/test_kb_indexer.py tests/test_kb_models.py tests/test_kb_lifecycle.py -x`; full: `pytest tests/ -v`.
- New tests: relative-path sources through the create route; same base name in two folders (and same content under two names) both stored; sanitiser table (`..`, backslashes, drive letters, NFC/NFD, control chars, overlong, no extension); add route (404 foreign, 409 not-ready/busy, 422 limits leave nothing, unchanged skipped, replaced removes old chunks and FTS rows, index `ntotal == chunk_count`, search finds both old and new fragments, failure leaves old chunks/index/READY and sets `error`, cancel/delete mid-add, orphan recovery of a READY base with `phase`), limits endpoint, migration test on the OLD schema (chunks preserved), UI source-guard test (ids, `webkitdirectory`, `append('files', ..., source)`), `tests/test_static_js_syntax.py`.
- Update: `test_kb_api.py` (`too-many` -> `MAX_FILES + 1`, `duplicate` -> same source name, message text), `test_kb_models.py::test_kb_document_unique_sha` (invert), `scripts/e2e_kb_playwright.py` (duplicate step at 465-469), `docs/TESTING_GUIDE.md`.

## Security Domain

`security_enforcement` is not disabled in config; included.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | yes (existing) | Session cookie via `get_current_user` on new routes |
| V3 Session Management | yes (existing) | Unchanged; HTTP-only cookie |
| V4 Access Control | yes | `_get_owned_kb` -> 404 for foreign ids on the add route; all new data scoped by `user_id` |
| V5 Input Validation | yes | Server-side source-name sanitiser, extension allow-list, count/size caps, name length cap; UI uses `textContent` |
| V6 Cryptography | no | sha256 only for change detection (not security) |
| V12 Files and Resources | yes | Server-generated stored names, per-request staging, size limits enforced while streaming, validate-before-store, cleanup on every failure path |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal via multipart filename (`../..`, `C:\`, absolute) | Tampering | Never use client names for paths (already true); sanitiser drops `..`/drive/empty segments; test with hostile names |
| Resource exhaustion (many files, huge files) | DoS | `MAX_FILES`, `MAX_FILE_BYTES`, `MAX_TOTAL_BYTES`, Content-Length cap, Starlette 1000-file cap; one addition at a time per base |
| Cross-site upload (CSRF) | Spoofing | `require_allowed_origin` dependency on create and add routes |
| Cross-user access to a base | Information disclosure | Owner check, 404 not 403 |
| Stored XSS through source names in cards/answers | Tampering | `textContent` only; never `innerHTML` for source names; `title` attributes set via property |
| Prompt injection through file names | Tampering | `agent/rag.py` already whitespace-normalises `source`; sanitiser strips control characters |
| Partial/corrupt state after a failed or cancelled add | Tampering/DoS | Non-destructive failure path, atomic file replace, staging cleanup, orphan recovery |

## Sources

### Primary (HIGH confidence)
- Repository source read in this session: `agent/kb_api.py`, `agent/kb_indexer.py`, `agent/kb_limits.py`, `agent/kb_search.py`, `agent/kb_loaders.py`, `agent/kb_schemas.py`, `agent/state.py`, `agent/rag_api.py`, `shared/kb_storage.py`, `shared/models.py`, `shared/database.py`, `ui/static/app.js` (KB section), `ui/static/index.html`, `tests/kb_helpers.py`, `tests/test_kb_api.py`, `tests/test_kb_models.py`, `tests/conftest.py`, `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `.planning/phases/21-*/21-CONTEXT.md`, `21-UI-SPEC.md`, `.planning/REQUIREMENTS.md`, `.planning/config.json`
- Executed in this session: Starlette multipart filename preservation test; SQLite table-rebuild test on AUTOCOMMIT with `foreign_keys=OFF`; FAISS `remove_ids`/`add_with_ids`/`reconstruct` test; `pip list`.

### Secondary (MEDIUM confidence)
- developer.mozilla.org/en-US/docs/Web/API/File/webkitRelativePath - definition of `webkitRelativePath`; "Baseline 2025 (Newly available)" status. Folder-name prefix and separator not stated on the page.

### Tertiary (LOW confidence)
- Playwright directory upload support for `webkitdirectory` (A3) - the docs page fetched was truncated before `set_input_files`; not confirmed.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new packages; versions read from the environment.
- Architecture: HIGH - derived from reading the pipeline and prototyping the risky mechanics (multipart, migration, FAISS patch).
- Pitfalls: HIGH for DB/index/concurrency (from code and SQLite behaviour demonstrated), MEDIUM for browser behaviour.

**Research date:** 2026-10-13
**Valid until:** 2026-11-12 (stable local codebase; re-check only if `agent/kb_*` or `shared/models.py` change before planning)
