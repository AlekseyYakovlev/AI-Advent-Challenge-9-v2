# Phase 13: Knowledge base indexing (Day 21) - Research

**Researched:** 2026-10-03
**Domain:** Local RAG ingestion (PyMuPDF + chunking + LM Studio embeddings + FAISS/SQLite) inside an existing two-process FastAPI app
**Confidence:** MEDIUM-HIGH (spike run live on this machine against real PDFs and the running LM Studio; VRAM co-loading with the 9B chat model NOT measured)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Indexing is all-or-nothing. If any file fails (e.g. scan PDF without text layer), the whole KB becomes `failed` with a Russian message naming the file; nothing is half-indexed. No "ready with warnings" state, no per-file status in the UI.
- **D-02:** "Удалить" works in every status, including `queued`/`indexing`: it cancels the background task first, then removes rows, on-disk index dir, uploaded files and in-memory caches.
- **D-03:** No re-index / "Повторить" action. A failed KB is recovered by deleting it and re-creating it through the modal. A job interrupted by an Agent restart is marked `failed` at startup (orphan recovery).
- **D-04:** Server-enforced caps: 50 MB per file, 10 files per KB, 100 MB total per KB. Constants defined in one place. Violations return a Russian error.
- **D-05:** The same file twice in one KB (same SHA-256) is rejected for the whole upload with a Russian message ("файл X уже добавлен") before any parsing/embedding. No silent dedupe.
- **D-06:** Validation errors (bad size/overlap, extension, empty name, caps, duplicates) are shown inline in the modal above the "Индексировать" button, using the server `detail` text. The modal stays open with the file selection intact (Phase 10: modals close only via ×).
- **D-07:** Embedding dropdown lists models with `type == "embeddings"` OR name containing `embed`/`giga`/`nomic`/`bge`/`e5`, plus a "показать все модели" toggle. Default is `giga-embeddings-instruct-480m-0826`. The chat model picker hides `type == "embeddings"`. Extend `providers._parse_models` additively to keep `type`.
- **D-08:** If the selected model is not loaded, the indexer tries an explicit load through the LM Studio control API first (lock held only for the load call, separate longer embed timeout). If the load still fails, the KB goes to `failed` with a clear Russian message ("Модель X не найдена/не загружается в LM Studio"). No silent fallback to another model; a KB is bound to one model/dim.
- **D-09:** giga query/document prefix defaults to none; nomic uses `search_query: ` / `search_document: `; unknown models use none. Prefixes are per-model code constants, stored on the KB row together with `embedding_model` and `dim` (dim taken from the first response).
- **D-10:** The modal has a small "Проверить эмбеддинг" button that embeds one test string with the selected model and shows the returned dimension (or the error). Small addition beyond the KB-02 field list, backs KB-06.
- **D-11:** Fixed strategy defaults: size 1000 / overlap 150 characters. Validation: `size >= 100`, `0 <= overlap < size`, `overlap <= size/2`, Russian messages.
- **D-12:** Structural strategy is a cascade: legal markers (`Глава` / `Статья N[.M]`, line-anchored) → Markdown `#` headings (MD files) → blank-line paragraphs packed up to the size limit. A file with no detectable structure becomes one section per file, then sub-split by size.
- **D-13:** Sections that exceed the embedding model's input limit are sub-split at paragraph, then sentence boundaries with small overlap. Every structural chunk's text is prefixed with a breadcrumb (e.g. `КоАП > Глава 5 > Статья 5.1`); `section` is also stored as metadata.
- **D-14:** КонсультантПлюс noise: strip `(в ред. ...)` annotations and repeated headers/footers/page numbers by regex/frequency (golden-file test on real КоАП text). Keep "Утратила силу" stub articles as tiny chunks so article numbers stay findable.
- **D-15:** Each sidebar KB row shows: name, status chip (в очереди / индексация x из y / готово / ошибка, error text on hover/expand), "N файлов · M чанков", short embedding model name, and "Удалить" with confirm.
- **D-16:** "Тест поиска" opens in a separate modal (closes only via ×), enabled only for `ready` KBs, backed by `POST /api/v1/kb/{id}/search`.
- **D-17:** Test-search results are the top 5 chunks as cards: score, source, section, chunk_id and a snippet with a "показать полностью" toggle. K is fixed at 5 in the UI (API accepts up to 20). All text is rendered via `textContent` (no raw HTML).
- **D-18:** A short spike precedes planning (embedding round trip, КоАП golden file, VRAM co-loading, retrieval A/B). *(Executed in this research, see Spike Results.)*
- **D-19:** KB-11 is proven by Playwright E2E on the isolated copy at ports 18000/18001 (never touching 8000/8001) with the two real PDFs and each chunking strategy, plus pytest for chunking, persistence, scoping and delete with mocked embeddings and isolated index directories.
- **D-20:** PyMuPDF for PDFs (pypdf rejected: glued words, 90 s on КоАП); new deps `faiss-cpu`, `numpy`, `pymupdf`, `python-multipart` (pin explicitly). No torch, langchain, vector DB or `openai` SDK.
- **D-21:** One FAISS `IndexIDMap2(IndexFlatIP)` per KB with L2-normalized float32 vectors and `KbChunk.id` as FAISS id; atomic write (`.tmp` → `os.replace`); storage under `<DB_PATH stem>_kb/<user_id>/<kb_id>/`.
- **D-22:** Indexing is a background asyncio task (`Semaphore(1)`, `to_thread` for parse/FAISS, async batched embeddings 16-32 with concurrency 1), progress via `/ws/events` with REST fallback; DB status row is source of truth; upload returns 202.
- **D-23:** KB write routes use the existing Origin allow-list (multipart POST with cookie); all KB data is scoped by `user_id`, foreign KB returns 404.

### Claude's Discretion
Exact progress throttling interval, batch size within 16-32, button/chip styling, retry/backoff on embedding HTTP errors, module split within `agent/kb_*.py`, and Windows non-ASCII FAISS path handling (serialize to bytes).

### Deferred Ideas (OUT OF SCOPE)
- Re-index / "Повторить" action for failed KBs
- Per-file partial success ("ready with warnings")
- Editable embedding prefix in the modal
- Incremental add of files to an existing KB, OCR for scans, DOCX/HTML
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| KB-01 | Sidebar block, list, delete, user scoping | UI-SPEC sidebar contract; user-scoping pattern (Architecture) |
| KB-02 | Create modal fields, model dropdown | `_parse_models` extension; embedding-model filter; BLOCKER B1 on giga |
| KB-03 | PDF/TXT/MD upload, PyMuPDF cleanup, scan failure | Spike results: footer/header patterns, justified-text line breaks, scan heuristic |
| KB-04 | Fixed chunking + validation | Fixed chunker pattern; size ceiling 2000 chars (silent truncation finding) |
| KB-05 | Structural chunking | Article/Глава regexes incl. `7.1-1` suffix; Раздел; sub-split rules |
| KB-06 | Batched embeddings, explicit load, prefixes | `/v1/embeddings` behaviour: ignores `model`, giga cannot embed (B1), batching timings |
| KB-07 | FAISS + SQLite persistence, metadata | FAISS bytes round trip verified incl. non-ASCII path; table design |
| KB-08 | Background job, live progress, orphan recovery | `recover_orphaned_runs` + `hub.publish` precedents |
| KB-09 | Delete cascade, 404 for foreign | `sa_column` FK rule; `delete_kb` order |
| KB-10 | Test search | `POST /api/v1/kb/{id}/search`; query prefix |
| KB-11 | Both PDFs index with each strategy; pytest | Golden-file counts; conftest isolation |
</phase_requirements>

## Summary

The spike ran live against LM Studio at localhost:1234 and the two real PDFs. It produced one **blocking finding**: LM Studio's `/v1/embeddings` **ignores the `model` field** [VERIFIED: live probe]. A bogus id (`does-not-exist`) and a chat LLM id (`liquid/lfm2-1.2b`) both return vectors, and `giga-embeddings-instruct-480m-0826` and `text-embedding-nomic-embed-text-v1.5` return **bit-identical vectors** when both are loaded. With nomic unloaded and only giga loaded, the endpoint returns `"No models loaded"`. So giga (typed `llm`, arch qwen3) can **never** be served by `/v1/embeddings` on this LM Studio build; the earlier "giga returns 768 dims after explicit load" claim in research/STACK.md was really nomic answering. Consequently the locked default (D-07/D-09 giga) produces silently wrong data (nomic vectors labelled giga) or a hard error. The planner must add a guard and the user must decide on the default (see Open Questions Q1).

Other measured facts: embedding input is silently truncated at roughly 2500 Cyrillic chars (~512 tokens) with no error; per-request latency is ~2.3 s fixed (batch 16/32/64/128 = 2.4/2.5/2.9/3.4 s), so batch size is almost free and КоАП (~4300 chunks) takes ~3.5-6 min; PyMuPDF extracts ФЗ-196 (45 pages) in 0.1 s and КоАП (875 pages, 3.68M chars) in 2.8 s; КоАП is a **Техэксперт** export, not КонсультантПлюс, with a 5-line repeated footer on every page and multi-line `(... См. предыдущую редакцию)` annotations (1809 occurrences) instead of `(в ред. ...)`; justified paragraphs come out with one word per line, so line-joining is mandatory before chunking.

**Primary recommendation:** Build `embeddings.py` with a pre-flight identity guard (requested model must be `state=loaded` AND `type=="embeddings"` per `/api/v0/models`, otherwise `failed` with a Russian message), default the dropdown to the first working embeddings-typed model (nomic) unless the user confirms otherwise, hard-cap chunk/embedding input at 2000 chars, and clean PDFs with a number-normalised line-frequency filter plus multi-line annotation regex.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| File upload, caps, SHA-256 dedupe, validation | API / Backend (Agent) | Browser (pre-checks only) | Server is source of truth (D-04..D-06) |
| PDF/TXT/MD parse and cleaning | API / Backend (`to_thread`) | — | CPU-bound; must not block loop |
| Chunking | API / Backend (`to_thread`) | — | Pure CPU |
| Embedding | API / Backend -> LM Studio (external) | — | httpx async, concurrency 1 |
| Vector index + metadata persistence | Database / Storage (FAISS file + SQLite) | — | SQLite = truth, FAISS derived |
| Job state/progress | API / Backend (DB row) | Browser via `/ws/events` | DB row is truth, WS is a hint |
| KB list, modals, progress rendering | Browser / Client | — | Vanilla JS |
| Auth scoping / Origin check | API / Backend | — | `user_id` filter, 404 on foreign |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| faiss-cpu | 1.15.1 | Per-KB vector index | Required by assignment; wheel cp313 win_amd64; round trip verified [VERIFIED: pip index + local run] |
| pymupdf | 1.28.2 | PDF text extraction | 2.8 s for 875 pages; correct word spacing; locked D-20 [VERIFIED: local run] |
| numpy | 2.5.3 (pin `>=2.0`) | float32 vectors for FAISS | FAISS requirement [VERIFIED: pip index] |
| python-multipart | 0.0.32 | FastAPI `UploadFile`/`Form` | Without it upload routes fail at import; currently only transitive [VERIFIED: pip index + installed] |
| httpx (existing) | existing | `/v1/embeddings` calls | No `openai` SDK (D-20) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pymupdf (AGPL) | pypdfium2 (BSD/Apache) | Fallback only if licence ever matters; coursework is undistributed |

**Installation:**
```bash
pip install faiss-cpu==1.15.1 pymupdf==1.28.2 "numpy>=2.0" python-multipart==0.0.32
```
Add the four lines to `requirements.txt` with a `# RAG (Day 21)` header.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| faiss-cpu | PyPI | years | very high | github.com/facebookresearch/faiss | [OK] | Approved |
| pymupdf | PyPI | years | very high | github.com/pymupdf/PyMuPDF | [OK] | Approved |
| numpy | PyPI | years | very high | github.com/numpy/numpy | [OK] | Approved |
| python-multipart | PyPI | years | very high | github.com/Kludex/python-multipart | [OK] (name pattern note: established package) | Approved |

**Removed [SLOP]:** none. **Flagged [SUS]:** none. All four were named in the user-locked decision D-20; slopcheck run locally returned 4 OK.

## Architecture Patterns

### System Architecture Diagram

```
Browser (modal, multipart POST + cookie)
   |  POST /api/v1/kb  (Origin check, auth, caps, SHA-256 dup check, param validation)
   v
Agent kb_api --202--> KnowledgeBase row (queued) + files saved to <stem>_kb/<uid>/<kb>/uploads
   |  create_task(run_index_job)  [Semaphore(1), strong ref in state dict]
   v
kb_indexer: status=indexing
   |-> preflight: model loaded AND type=="embeddings"? else explicit load (lock only for load) else FAIL
   |-> per file: to_thread(load: pymupdf -> clean -> normalise) --scan?--> FAIL whole KB
   |-> to_thread(chunk: fixed | structural(+breadcrumb, sub-split <=2000 chars))
   |-> insert KbDocument/KbChunk rows (flush, ids)
   |-> embed batches of 32 (httpx, concurrency 1, retry) -> progress frame via hub.publish (throttled) + DB done/total
   |-> to_thread(normalize_L2, IndexIDMap2.add_with_ids, serialize) -> .tmp -> os.replace
   v
status=ready (chunk_count, dim)            on any error/cancel: status=failed + Russian error, rmtree partial index

Browser <- /ws/events kb_progress | REST GET /api/v1/kb (fallback on reconnect)
Test search: POST /api/v1/kb/{id}/search -> embed_query (query prefix) -> to_thread(index.search) -> join KbChunk rows
Startup (lifespan): recover_orphaned_kb_jobs(): queued/indexing -> failed ("прервано перезапуском")
```

### Recommended Project Structure
```
agent/
  kb_api.py        # APIRouter /api/v1/kb (list, create 202, get, delete, search, embedding-models, embed-check)
  kb_indexer.py    # run_index_job, delete_kb, recover_orphaned_kb_jobs, progress publish
  kb_loaders.py    # load_pdf/txt/md -> list[Page/Section text]; clean_pdf_text()
  kb_chunking.py   # chunk_fixed, chunk_structural, validate_chunk_params (pure functions)
  embeddings.py    # Embedder: ensure_model, embed_passages, embed_query, PREFIXES
shared/
  kb_storage.py    # kb_dir(user_id, kb_id), save_upload, atomic_write_index, read_index, rmtree
  models.py        # + KnowledgeBase, KbDocument, KbChunk
tests/fixtures/    # kopap golden excerpt (trimmed text), tiny generated PDFs
```

### Pattern 1: FAISS bytes round trip (Windows non-ASCII safe) [VERIFIED: local run]
```python
import faiss, numpy as np, os
from pathlib import Path

def write_index(path: Path, index: faiss.Index) -> None:
    data = faiss.serialize_index(index).tobytes()      # never faiss.write_index(str(path))
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)

def read_index(path: Path) -> faiss.Index:
    return faiss.deserialize_index(np.frombuffer(path.read_bytes(), dtype="uint8"))

# build: vectors float32 C-contiguous (n, d); faiss.normalize_L2(vectors) in place
index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
index.add_with_ids(vectors, np.asarray(chunk_ids, dtype="int64"))
```
Tested with a Cyrillic directory name; search returns the stored ids.

### Pattern 2: Embeddings client with identity guard
```python
# POST {base}/v1/embeddings {"model": id, "input": [..]} -> data[i].embedding (float list)
# LM Studio IGNORES "model" if any embeddings-typed model is loaded. Guard BEFORE the first batch:
async def ensure_embedding_model(client, model_id) -> None:
    models = await client.list_models()                 # /api/v0/models (has type, state)
    entry = next((m for m in models if m["id"] == model_id), None)
    if entry is None: raise EmbeddingModelError(f"Модель {model_id} не найдена в LM Studio.")
    if entry.get("type") != "embeddings":
        raise EmbeddingModelError(f"Модель {model_id} не поддерживает эмбеддинги в LM Studio (тип {entry.get('type')}).")
    if entry.get("state") != "loaded":
        await client.load_model(model_id)               # lock only for this call
```
Use a separate `KB_EMBED_TIMEOUT` (e.g. 120 s) from `LLM_TIMEOUT` (60 s). Verify the response length equals batch size and dim is constant, otherwise fail.

### Pattern 3: Cancellable background job + orphan recovery
Mirror `scheduler.spawn_run` / `recover_orphaned_runs`: keep strong references in a `kb_jobs: dict[int, asyncio.Task]` in `agent/state.py`; `lifespan` calls `await recover_orphaned_kb_jobs()` next to `scheduler.recover_orphaned_runs()` (UPDATE status in (queued, indexing) -> failed with Russian text). `delete_kb`: cancel task, `await` it with `suppress(CancelledError)`, delete rows, `shutil.rmtree(kb_dir, ignore_errors=False)` in `to_thread`, pop caches. A cancelled job's `finally` must not write `ready`. Per-KB cancel must also release the `Semaphore(1)` (use `async with`).

### Pattern 4: Progress events
`hub.publish(user_id, {"type": "kb_progress", "kb_id":..., "status":..., "done":..., "total":...})`; throttle to at most ~1 frame/0.5 s plus always send status transitions. `/ws/events` client switch (`ui/static/app.js` ~line 2900) gets `kb_progress`/`kb_deleted` cases. Frame must include `user_id` scoping implicitly (hub is per user).

### Anti-Patterns to Avoid
- **Trusting the `model` field** in embedding responses: results silently come from whichever embeddings model is loaded.
- **`faiss.write_index(str(path))`** on non-ASCII Windows paths; use bytes.
- **Sync PDF/FAISS work on the loop**: Supervisor kills the Agent after missed 3 s health checks.
- **Applying `require_json_content_type`** to the multipart route (only `require_allowed_origin`). Browser must not set `Content-Type` on `FormData` fetch.
- **`\s+` in line-anchored regexes** (matches newlines; see Pitfall 3).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PDF text extraction | custom PDF parser | `pymupdf` `page.get_text()` | spaces/Cyrillic handled |
| Vector search | numpy brute force loop | `faiss.IndexIDMap2(IndexFlatIP)` | assigned stack; id mapping |
| Multipart parsing | manual body parsing | FastAPI `UploadFile`/`Form` + python-multipart | caps/spooling built in |
| Event fan-out | new WS endpoint | existing `agent.events.hub` | per-user, bounded queue |
| Orphan recovery | new mechanism | copy `recover_orphaned_runs` pattern | proven |
| Token counting for chunk sizes | tokenizer | character limits (2000 hard cap) | LM Studio truncates by tokens it does not expose |

## Spike Results (D-18)

### (a) Embedding round trip [VERIFIED: live probe, LM Studio at localhost:1234]
- `/api/v0/models`: nomic = `type: embeddings`, loaded, ctx 2048; giga = `type: llm`, arch qwen3, Q8_0, loaded, ctx 8192.
- Both ids return 768-dim vectors; the vectors are **identical** (cos = 1.0, same first components). `does-not-exist` and `liquid/lfm2-1.2b` also succeed with the same vector. After unloading nomic (restored afterwards), giga alone returns `{"error": "No models loaded..."}`. Conclusion: giga cannot be used for embeddings through LM Studio's endpoint; `model` is not a selector.
- Giga model card says 1024 dims / 512 tokens [CITED: huggingface.co/ai-sage/Giga-Embeddings-instruct-480M-0826], unreachable here.
- **Input limit:** cosine of a truncated prefix vs a 12000-char input rises 0.909 (500 chars) -> 0.986 (2000) -> 0.9967 (2400) -> 1.0000 (2600+). Text beyond ~2500 chars of Russian legal text is silently dropped, no error. Set **hard cap 2000 chars per embedded text (breadcrumb included)**; validate fixed-size `size <= 2000` (new Russian message) so the user cannot create silently truncated chunks.
- **Latency:** ~2.3 s per request regardless of size; batch 16 = 2.4 s, 32 = 2.5 s, 64 = 2.9 s, 128 = 3.4 s. Recommended batch **32** (within D-22's 16-32; 64 would cut КоАП to ~3.5 min if the user wants, still discretionary).
- **Prefix effect:** only nomic is real, so a giga prefix A/B is moot. Nomic prefixes `search_query: ` / `search_document: ` are the documented requirement [ASSUMED: nomic-embed-text-v1.5 model card; not re-fetched this session]. Mini smoke on ФЗ-196 (26 articles) with nomic: top-1 for "возраст ... управлению транспортным средством" was Статья 19 (0.817); scores cluster 0.72-0.84, so thresholds belong to Phase 15.

### (b) КоАП golden file [VERIFIED: local run on C:\Projects\RAG]
| Fact | ФЗ-196 | КоАП |
|------|--------|------|
| Pages / chars / extract time | 45 / 125k / 0.1 s | 875 / 3.68M / 2.8 s |
| Source system | КонсультантПлюс (`КонсультантПлюс: примечание.`, 72 `(в ред. ...)`) | **Техэксперт** (not КонсультантПлюс) |
| `^Статья` headings | 34 | 1137 matches, 906 unique numbers |
| `^Глава` | 8 | 33 (matches the code) |
| `^Раздел` | - | 5 |
| "Утратил(а/и) силу" | 5 | 13 (stubs like `(Утратила силу с 12 июля 2021 года - Федеральный закон ... - См. предыдущую` wrapping onto next line) |
| Noise | `(в ред. Федерального закона от #.#.# N #-ФЗ)` top/bottom lines | 5 footer lines on **every** page (count 875 each after digit-normalising): `Кодекс Российской Федерации об административных правонарушениях (с изменениями на ...)` (wraps over 2 lines), `Кодекс РФ от 30.12.2001 N 195-ФЗ`, `Страница N`, `Внимание! Документ с изменениями и дополнениями ... "Оперативная информация"`, `ИС «Техэксперт: 6 поколение» Интранет`; 1809 multi-line annotations `(Часть в редакции, введенной в действие ... - См. предыдущую редакцию)`; `Комментарий к статье N.M.` lines |
| Article length | - | median 1796 chars, max 139,184 (needs sub-split) |
- 231 of the 1137 heading matches are duplicates caused by suffix numbering: `Статья 7.1-1.` matched by `\d+(\.\d+)*\.` as `7`. Use `^Статья[ \t]+(\d+(?:\.\d+)*(?:-\d+)?)\.` and golden-assert the unique count after cleaning (906 +/- a few, final value fixed in the test from the real run).
- Heading lines can wrap (`Статья 5.1. Нарушение ... списком избирателей,` + continuation line), and a page may start with a whitespace-only line. Take the title as the first line only and treat the next lines as body.
- **Justified text:** many paragraphs come out one word per line with trailing spaces (`запросом, \nи \n(или) \n`). Cleaning must strip each line, drop empties, and join consecutive lines into paragraphs (join with a space unless the next line starts a marker: `Статья|Глава|Раздел|Комментарий к статье|\d+\.|\d+\)|(Утратил`). This is what makes `chunk_fixed` produce real 1000-char chunks.
- Header/footer algorithm: for each page take first 3 and last 6 non-empty lines, normalise digits (`\d+` -> `#`), count across pages; drop any normalised line appearing on >= 30% of pages (min 3 pages) from those positions. Doc with < 3 pages: skip frequency filter. Then drop `^Страница \d+$` and bare-number lines.
- Annotation regex (DOTALL over joined text): `\((?:Часть|Пункт|Абзац|Статья|Наименование|В редакции|в ред\.|п\. [\d.]+ введен|абзац введен)[^()]{0,400}?(?:См\. предыдущую редакцию|ред\.|N \d+-ФЗ)\s*\)` plus `КонсультантПлюс: примечание.` blocks. Keep "(Утратила силу ...)" text (D-14). Whether to also drop "Комментарий к статье N.M." lines: yes, drop (noise, no content).
- Per-word dehyphenation is risky (`топливо- и`): only merge `\w-\n` when the next line begins with a lowercase letter and the continuation is >= 3 letters; golden test required. [ASSUMED rule; verify in golden test]
- Scan detection (KB-03): if total extracted chars / pages < 100 or > 50% of pages have < 50 chars, fail with «Не удалось извлечь текст из файла {имя}. Возможно, это скан без текстового слоя.» [ASSUMED thresholds]. Generate the test scan PDF with pymupdf (page with only an image or blank page).

### (c) VRAM co-loading [PARTIAL]
Currently loaded: nomic + giga only, GPU 6355 / 16303 MiB used. A 9B Q4 chat model plus a ~0.3 GB embedder very likely fits in 16 GB [ASSUMED], but the co-load was not exercised (it would evict/reload the user's models). Embedding model load took 3.4 s via `/api/v1/models/load` [VERIFIED]. During a model load the progress should show status text «загрузка модели…» (add an optional `phase` field in the frame, UI shows it in the chip). Do not unload the embedder from the chat path; do not touch chat `_current_loaded_model` bookkeeping.

### (d) Retrieval A/B giga vs nomic [INVALIDATED]
Cannot be done through LM Studio (B1). Phase 14 reports should compare nomic only (or nomic with/without prefix), unless a different embedding model (e.g. bge-m3 GGUF typed `embeddings`) is loaded.

## Common Pitfalls

### Pitfall 1 (B1): Silent wrong embeddings (giga labelled, nomic served)
**What goes wrong:** KB row says giga, vectors are nomic's (or call fails "No models loaded").
**How to avoid:** `ensure_embedding_model` guard (type must be `embeddings`, state loaded). "Проверить эмбеддинг" runs the same guard and shows the Russian error. Never rely on `data[].model`. Store `embedding_model` only after the guard passes. Consider comparing a probe vector only if needed (not required with the guard).
**Warning signs:** `type: llm` in the models list for the selected id.

### Pitfall 2: Silent input truncation (~2500 chars)
**How to avoid:** hard cap 2000 chars including breadcrumb and prefix text; fixed-size `size <= 2000` validation (Russian: «Размер чанка не должен превышать 2000»); sub-split structural sections to <= 2000 minus breadcrumb length.

### Pitfall 3: Regex traps on КоАП
`\s+` after `Статья` spans newlines; `7.1-1` suffix numbering; wrapped heading titles; per-word lines; multi-line annotations. Use `[ \t]+`, the `-\d+` suffix, join lines before running annotation regexes, and keep a trimmed golden excerpt in `tests/fixtures/` (do not commit the 10 MB PDF; E2E uses `C:\Projects\RAG` paths directly and `pytest.mark.skipif` if absent).

### Pitfall 4: FAISS id / SQLite drift
Insert `KbChunk` rows (flush to get ids) before `add_with_ids`; write the index only after all rows exist; mark `ready` in the same final step; at startup (and on search) verify `index.ntotal == chunk_count` and `index.d == dim`, otherwise raise a readable error / mark `failed`. Optional float32 BLOB column is deferred (no re-index action in this phase; skip it).

### Pitfall 5: Event-loop blocking
PyMuPDF parse (0.1-2.8 s), `normalize_L2`, `add_with_ids`, `serialize_index`, `rmtree`, SQLite bulk inserts of ~4k rows must be `to_thread` or batched `executemany` through the async session; httpx stays async. Add a smoke test that `/health` responds during a mocked index job.

### Pitfall 6: Test DB isolation
`DB_PATH` in tests is `test_app.db`, so KB dir is `test_app_kb/` in the cwd. Extend `clean_test_db` to `shutil.rmtree` it before/after and clear `kb_jobs` state; alternatively set an env override `KB_STORAGE_DIR`. For SQLite FK: `KbDocument.kb_id` and `KbChunk.kb_id/document_id` use `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`; deleting rows explicitly in `delete_kb` is still fine, but test cascade as in `test_cascade_delete.py`.

### Pitfall 7: Multipart + Origin + body size
`require_allowed_origin` only (no JSON content-type dependency). Enforce 50 MB/file by streaming `await file.read(chunk)` into a spooled write and aborting at the cap (do not trust `Content-Length`); enforce 10 files, 100 MB total; compute SHA-256 while streaming; reject duplicates before any DB insert. Sanitize filenames (`Path(name).name`, store as `<doc_id>_<safe>.ext` or by hash) to prevent path traversal; validate extension against `.pdf/.txt/.md`; TXT/MD decode with `utf-8-sig`, fallback `cp1251` [ASSUMED], else Russian error.

### Pitfall 8: Cancel/delete races
Delete while indexing: cancel task, await it, then remove rows/dir. The embedding loop must check cancellation between batches (awaits already provide it). The `finally` of the job must open its own session; never reuse the request session.

### Pitfall 9: `_parse_models` regression
Extending dicts with `type` must stay additive (existing tests `test_llm_providers_*` assert shape). The chat picker hides `type == "embeddings"`; giga is typed `llm` so it will still appear in the chat picker (acceptable; flag in UI by name filter is out of scope).

## Code Examples

### Chunk parameter validation (D-11 + 2000 cap)
```python
def validate_chunk_params(size: int, overlap: int) -> str | None:
    if size < 100: return "Размер чанка должен быть не меньше 100"
    if size > MAX_EMBED_CHARS: return f"Размер чанка не должен превышать {MAX_EMBED_CHARS}"
    if overlap < 0 or overlap >= size or overlap > size // 2:
        return "Перекрытие должно быть меньше размера чанка и не больше его половины"
    return None
```

### Fixed chunker (paragraph-aware optional; char windows with char offsets)
```python
def chunk_fixed(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    step = size - overlap
    return [(i, min(i + size, len(text))) for i in range(0, len(text), step) if text[i:i+size].strip()]
```
Store `char_start/char_end` (+ `page` when known) per chunk. Prefer snapping the end to the last whitespace in the window to avoid cutting words (small tweak, tests assert chunk count and overlap).

### Structural cascade
```python
ARTICLE = re.compile(r"^Статья[ \t]+(\d+(?:\.\d+)*(?:-\d+)?)\.[ \t]*(.*)$", re.M)
CHAPTER = re.compile(r"^(Глава|Раздел)[ \t]+([\dIVXLC]+)\.?[ \t]*(.*)$", re.M)
# breadcrumb: f"{doc_title} > {Раздел/Глава line} > Статья {n}. {title}"; section metadata = "Глава 5 > Статья 5.1"
# MD: ^#{1,6}\s+ headings; fallback: blank-line paragraphs packed to size; no structure -> one section per file then size sub-split
```
Sub-split sections longer than `2000 - len(breadcrumb)` at paragraph then sentence (`(?<=[.!?;])\s+`) boundaries with ~100-char overlap; repeat breadcrumb on every piece.

### Orphan recovery
```python
await session.exec(update(KnowledgeBase)
    .where(KnowledgeBase.status.in_([KbStatus.QUEUED, KbStatus.INDEXING]))
    .values(status=KbStatus.FAILED, error=MSG_INTERRUPTED_RESTART))
```
Call from `lifespan` right after `scheduler.recover_orphaned_runs()`; also remove partial index dirs of those KBs.

## Data Model (suggested)

| Table | Key columns |
|-------|-------------|
| `KnowledgeBase` | id, user_id (FK user, cascade), name, status (queued/indexing/ready/failed), error, strategy, chunk_size, chunk_overlap, embedding_model, dim, query_prefix, doc_prefix, file_count, chunk_count, done_chunks, total_chunks, created_at, updated_at |
| `KbDocument` | id, kb_id (FK cascade), filename, sha256, size_bytes, page_count, stored_path |
| `KbChunk` | id (= FAISS id), kb_id (FK cascade), document_id (FK cascade), chunk_index, text, section, source (filename), page_start, char_start, char_end, `chunk_id` string e.g. `{doc_id}-{index}` |
Create via `create_all` (no migration needed for new tables). `file_count`/`chunk_count` can be denormalised on the KB row for the list endpoint (set at ready). Unique index on `(kb_id, sha256)` as second line of defence for D-05.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | - |
| faiss-cpu / pymupdf / numpy | indexing | installed during spike | 1.15.1 / 1.28.2 / 2.5.3 | - |
| python-multipart | upload | yes | 0.0.32 | - |
| LM Studio | embeddings E2E | yes (running) | CLI commit 07b7252 | mock `/v1/embeddings` with respx in pytest |
| Embeddings model (nomic) | embeddings | yes, loaded | v1.5, 768 dims | - |
| Embeddings model (giga) | default per D-07 | **loaded but not usable for embeddings** | typed `llm` | nomic |
| Test PDFs | KB-11 | yes | 2 files in `C:\Projects\RAG` | generated mini PDFs for pytest |
| Playwright | D-19 E2E | not checked | - | per memory feedback, isolated copy on 18000/18001 |

**Missing, blocking:** a working giga embedding path (see B1/Q1).

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2/V3 Auth/Session | yes | existing cookie session, `get_current_user` on every KB route |
| V4 Access Control | yes | every query filters `user_id`; foreign KB -> 404; never 403 |
| V5 Input Validation | yes | Pydantic/Form validation, extension allow-list, caps, filename sanitation |
| V12 Files/Resources | yes | stream with size cap, store under generated names inside KB dir, no path from client |
| V6 Cryptography | minimal | `hashlib.sha256` for dedupe only |

### Known Threat Patterns
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal via filename | Tampering | `Path(name).name`, server-generated stored names, resolve-and-check inside KB dir |
| Zip/PDF bomb or huge upload (DoS) | DoS | 50/10/100 MB caps enforced while streaming; page cap guard optional |
| CSRF on multipart POST | Tampering | `require_allowed_origin` (browsers always send Origin on POST) |
| Cross-user KB access / IDOR | Info disclosure | `user_id` filter + 404 |
| Stored XSS via chunk text/filename | Tampering | `textContent` only in UI (D-17); never `innerHTML` |
| Malicious PDF exploiting parser | Tampering | PyMuPDF in `to_thread`, catch broad parse exceptions into Russian `failed` message (log `str(exc)` only) |
| Leaking absolute paths / secrets in errors | Info disclosure | Russian generic message; no paths in `detail` |

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| pypdf | pymupdf | 30x faster, spacing correct |
| Trust OpenAI-style `model` routing | LM Studio ignores `model` for embeddings | Guard required |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | nomic requires `search_query: ` / `search_document: ` prefixes (model card, not re-fetched) | Spike (a) | Slightly worse retrieval; fixable per-model constant |
| A2 | 9B chat model + embedder co-fit in 16 GB VRAM | Spike (c) | Load eviction; indexing slower; needs doc note |
| A3 | Scan thresholds (<100 chars/page avg, >50% pages <50 chars) | Spike (b) | False positives/negatives on unusual PDFs |
| A4 | TXT/MD fallback encoding cp1251 | Pitfall 7 | Mojibake for rare encodings |
| A5 | Dehyphenation rule (lowercase next line, >=3 letters) | Spike (b) | Minor word glue; golden test catches |
| A6 | Annotation regex scope for Техэксперт/КонсультантПлюс | Spike (b) | Residual noise in chunks; tune with golden test |

## Open Questions

1. **BLOCKER: what to do about giga (D-07/D-09 default)?**
   - Known: LM Studio cannot serve giga on `/v1/embeddings`; with nomic loaded, giga requests return nomic vectors; alone it errors "No models loaded". The assignment named giga as default.
   - Recommendation: keep giga in the list but mark unsupported via the type guard (error text above), make the dropdown default the first model that passes the guard (nomic), keep the D-09 prefix table with giga = none. Needs user confirmation (planner should add a `checkpoint:decision` or encode the guard + fallback default as the documented deviation). Optionally the user can try a different LM Studio build/another embeddings GGUF (e.g. bge-m3), which would then pass the guard automatically.
2. **Size upper bound 2000 chars** is an addition to D-11 (needed by the truncation finding). Recommend accepting it.
3. **Exact КоАП golden counts** (unique articles after cleaning) should be fixed in the golden test from the first implementation run.

## Sources

### Primary (HIGH)
- Live probes on this machine: LM Studio `/v1/embeddings`, `/api/v0/models`, `/api/v1/models/load|unload`; PyMuPDF extraction of both PDFs; FAISS serialize/deserialize round trip; `pip index versions` for four packages; slopcheck 4 OK.
- Project code read: `agent/events.py`, `agent/scheduler.py` (orphan recovery), `agent/main.py` (lifespan, router, Origin dependency), `agent/dependencies.py`, `agent/providers.py::_parse_models`, `agent/llm_client.py` (LMStudioClient load/unload), `tests/conftest.py`.
- `.planning/research/SUMMARY.md`, `STACK.md` (note: its giga-works claim is superseded by B1).

### Secondary (MEDIUM)
- Giga model card (dims/512 tokens): https://huggingface.co/ai-sage/Giga-Embeddings-instruct-480M-0826 (from prior research, not re-fetched)

### Tertiary (LOW)
- nomic prefix requirement (training knowledge, A1).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH (installed and exercised)
- Embedding behaviour: HIGH for the `model`-ignored finding and truncation; MEDIUM for prefix effect
- PDF cleaning: MEDIUM-HIGH (patterns measured; regex needs golden-file tuning)
- VRAM co-load: LOW (not measured)

**Research date:** 2026-10-03
**Valid until:** 2026-11-02 (LM Studio behaviour may change between builds; re-probe if upgraded)
