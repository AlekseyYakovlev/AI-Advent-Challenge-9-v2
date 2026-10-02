# Phase 13: Knowledge base indexing (Day 21) - Context

**Gathered:** 2026-10-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Users build a knowledge base (KB) from PDF/TXT/MD files in the sidebar "База знаний" block: modal with name, files, chunking strategy (fixed / structural), size/overlap, embedding model → "Индексировать". Result is a persisted, searchable FAISS + SQLite index with live indexing progress, a "тест поиска" view, and per-KB delete. Attaching a KB to a chat and answering with retrieved chunks is Phase 14 and out of scope here. Requirements: KB-01..KB-11.

</domain>

<decisions>
## Implementation Decisions

### Failed files & job lifecycle
- **D-01:** Indexing is all-or-nothing. If any file fails (e.g. scan PDF without text layer), the whole KB becomes `failed` with a Russian message naming the file; nothing is half-indexed. No "ready with warnings" state, no per-file status in the UI.
- **D-02:** "Удалить" works in every status, including `queued`/`indexing`: it cancels the background task first, then removes rows, on-disk index dir, uploaded files and in-memory caches.
- **D-03:** No re-index / "Повторить" action. A failed KB is recovered by deleting it and re-creating it through the modal. A job interrupted by an Agent restart is marked `failed` at startup (orphan recovery).

### Upload limits & duplicates
- **D-04:** Server-enforced caps: 50 MB per file, 10 files per KB, 100 MB total per KB. Constants defined in one place. Violations return a Russian error.
- **D-05:** The same file twice in one KB (same SHA-256) is rejected for the whole upload with a Russian message ("файл X уже добавлен") before any parsing/embedding. No silent dedupe.
- **D-06:** Validation errors (bad size/overlap, extension, empty name, caps, duplicates) are shown inline in the modal above the "Индексировать" button, using the server `detail` text. The modal stays open with the file selection intact (Phase 10: modals close only via ×).

### Embedding model picker & defaults
- **D-07:** Embedding dropdown lists models with `type == "embeddings"` OR name containing `embed`/`giga`/`nomic`/`bge`/`e5`, plus a "показать все модели" toggle. Default is `giga-embeddings-instruct-480m-0826`. The chat model picker hides `type == "embeddings"`. Extend `providers._parse_models` additively to keep `type`.
- **D-08:** If the selected model is not loaded, the indexer tries an explicit load through the LM Studio control API first (lock held only for the load call, separate longer embed timeout). If the load still fails, the KB goes to `failed` with a clear Russian message ("Модель X не найдена/не загружается в LM Studio"). No silent fallback to another model; a KB is bound to one model/dim.
- **D-09:** giga query/document prefix defaults to none; nomic uses `search_query: ` / `search_document: `; unknown models use none. Prefixes are per-model code constants, stored on the KB row together with `embedding_model` and `dim` (dim taken from the first response).
- **D-10:** The modal has a small "Проверить эмбеддинг" button that embeds one test string with the selected model and shows the returned dimension (or the error). Small addition beyond the KB-02 field list, backs KB-06.

### Chunking details
- **D-11:** Fixed strategy defaults: size 1000 / overlap 150 characters. Validation: `size >= 100`, `0 <= overlap < size`, `overlap <= size/2`, Russian messages.
- **D-12:** Structural strategy is a cascade: legal markers (`Глава` / `Статья N[.M]`, line-anchored) → Markdown `#` headings (MD files) → blank-line paragraphs packed up to the size limit. A file with no detectable structure becomes one section per file, then sub-split by size.
- **D-13:** Sections that exceed the embedding model's input limit are sub-split at paragraph, then sentence boundaries with small overlap. Every structural chunk's text is prefixed with a breadcrumb (e.g. `КоАП > Глава 5 > Статья 5.1`); `section` is also stored as metadata.
- **D-14:** КонсультантПлюс noise: strip `(в ред. ...)` annotations and repeated headers/footers/page numbers by regex/frequency (golden-file test on real КоАП text). Keep "Утратила силу" stub articles as tiny chunks so article numbers stay findable.

### KB list & test-search UI
- **D-15:** Each sidebar KB row shows: name, status chip (в очереди / индексация x из y / готово / ошибка, error text on hover/expand), "N файлов · M чанков", short embedding model name, and "Удалить" with confirm.
- **D-16:** "Тест поиска" opens in a separate modal (closes only via ×), enabled only for `ready` KBs, backed by `POST /api/v1/kb/{id}/search`.
- **D-17:** Test-search results are the top 5 chunks as cards: score, source, section, chunk_id and a snippet with a "показать полностью" toggle. K is fixed at 5 in the UI (API accepts up to 20). All text is rendered via `textContent` (no raw HTML).

### Spike & acceptance
- **D-18:** A short spike precedes planning and proves: (a) embedding round trip for giga and nomic (explicit load, real dim, max input tokens, prefix effect on 3-4 queries) which sets the chunk ceiling; (b) КоАП golden file (PyMuPDF extraction, header/footer/annotation regexes, Статья/Глава counts); (c) embedder + 9B chat model VRAM co-loading and what the UI shows while a model loads; (d) retrieval smoke A/B giga vs nomic on ФЗ-196 with a few questions.
- **D-19:** KB-11 is proven by Playwright E2E on the isolated copy at ports 18000/18001 (never touching 8000/8001) with the two real PDFs and each chunking strategy, plus pytest for chunking, persistence, scoping and delete with mocked embeddings and isolated index directories.

### Carried from research (locked, not re-discussed)
- **D-20:** PyMuPDF for PDFs (pypdf rejected: glued words, 90 s on КоАП); new deps `faiss-cpu`, `numpy`, `pymupdf`, `python-multipart` (pin explicitly). No torch, langchain, vector DB or `openai` SDK.
- **D-21:** One FAISS `IndexIDMap2(IndexFlatIP)` per KB with L2-normalized float32 vectors and `KbChunk.id` as FAISS id; atomic write (`.tmp` → `os.replace`); storage under `<DB_PATH stem>_kb/<user_id>/<kb_id>/`.
- **D-22:** Indexing is a background asyncio task (`Semaphore(1)`, `to_thread` for parse/FAISS, async batched embeddings 16-32 with concurrency 1), progress via `/ws/events` with REST fallback; DB status row is source of truth; upload returns 202.
- **D-23:** KB write routes use the existing Origin allow-list (multipart POST with cookie); all KB data is scoped by `user_id`, foreign KB returns 404.

### Claude's Discretion
- Exact progress throttling interval, batch size within 16-32, button/chip styling, retry/backoff on embedding HTTP errors, module split within `agent/kb_*.py`, and Windows non-ASCII FAISS path handling (serialize to bytes).

</decisions>

<specifics>
## Specific Ideas

- Real corpus: `C:\Projects\RAG\19951210_20260626_FZ_N_196_FZ.pdf` (ФЗ-196) and the КоАП РФ PDF (875 pages, ~4-6 min to embed).
- Embedding dropdown "built like the main model picker" (KB-02).

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/REQUIREMENTS.md` — KB-01..KB-11 (Knowledge base indexing section)
- `.planning/ROADMAP.md` — Phase 13 goal, success criteria, research flag
- `.planning/research/SUMMARY.md` — resolved conflicts (PDF lib, embedding dropdown, prefixes, persistence layout, VRAM/lock)
- `.planning/research/STACK.md` — measured PDF benchmark, LM Studio embeddings endpoints
- `.planning/research/ARCHITECTURE.md` — planned modules, tables, indexer flow
- `.planning/research/PITFALLS.md` — event-loop blocking, FAISS/SQLite drift, PDF noise

### Project conventions
- `CLAUDE.md` — hard constraints (no Docker/npm, vanilla JS, asyncio only), code conventions, SQLModel FK cascade rule
- `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md` — to be synced after the phase
- `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/STRUCTURE.md`, `.planning/codebase/TESTING.md`

### Prior phase decisions
- `.planning/milestones/v2.0-phases/12-llm-providers-section-in-settings-day-21/12-CONTEXT.md` — provider/model picker patterns
- `.planning/phases/10-modals-close-only-via-x-button-day-21/` — modals close only via ×
- `.planning/milestones/v2.0-phases/08-scheduler-day-18/` — `/ws/events` hub and background job pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/events.py` — `/ws/events` hub for per-user progress events (`kb_progress`)
- `agent/providers.py` / `agent/providers_api.py` — model listing (`_parse_models`) and LM Studio control API for explicit load
- `agent/llm_client.py` — `LMStudioClient`, `model_switch_lock`, httpx patterns
- `agent/state.py` — in-memory dicts and `cleanup_chat_caches` pattern (add KB job/cache cleanup)
- `shared/database.py`, `shared/models.py` — create_all, `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`
- `agent/scheduler.py` — background asyncio job and orphan-recovery precedent
- `ui/static/app.js` — modal helper, model picker, toast, sidebar blocks
- `tests/conftest.py` — isolated DB fixtures to extend with isolated KB dir

### Established Patterns
- User scoping by `user_id` with 404 for foreign rows; Origin allow-list on write routes
- structlog `snake_case_action` logging, `await session.commit()` / rollback
- Frontend: vanilla JS, DOMPurify/`textContent` only

### Integration Points
- New: `agent/kb_api.py`, `kb_indexer.py`, `kb_loaders.py`, `kb_chunking.py`, `embeddings.py`, `shared/kb_storage.py`; tables `KnowledgeBase`, `KbDocument`, `KbChunk`
- `agent/main.py` router registration and startup orphan recovery; `ui/static/app.js` + `index.html` for sidebar block and two modals (create, test search); `/ws/events` switch gets `kb_*` cases

</code_context>

<deferred>
## Deferred Ideas

- Re-index / "Повторить" action for failed KBs — not in this phase (delete and re-create)
- Per-file partial success ("ready with warnings") — rejected for now
- Editable embedding prefix in the modal — rejected (per-model constants)
- Incremental add of files to an existing KB, OCR for scans, DOCX/HTML — out of scope (see REQUIREMENTS Future/Out of Scope)

</deferred>

---

*Phase: 13-knowledge-base-indexing-day-21*
*Context gathered: 2026-10-03*
