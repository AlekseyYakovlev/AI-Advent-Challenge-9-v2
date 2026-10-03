# Project Research Summary

**Project:** AiAdventAgentV2 — milestone v3.0 Week 5: RAG (Days 21-25)
**Domain:** Local-first RAG (FAISS + SQLite, LM Studio embeddings, Russian legal PDFs) added to an existing two-process FastAPI chat app with small local LLMs
**Researched:** 2026-10-03
**Confidence:** MEDIUM-HIGH (stack and integration points verified on this machine or read from code; embedding-model quality and local-LLM compliance are the soft spots)

## Executive Summary

This milestone adds a deterministic RAG pipeline to the existing chat. Ingest: PDF/TXT/MD upload → structure-aware chunking → LM Studio `/v1/embeddings` → one FAISS flat index per knowledge base, with chunk text and metadata in SQLite. Query: pre-retrieval in `agent/ws.py` (not an LLM tool) → threshold / rerank / rewrite → numbered context block → server-rendered sources and verified quotes → code-level "не знаю" gate. Day 25 reuses the existing chat plus working memory as the mini-chat. The corpus is small (~5-10k chunks), so exact search is sub-millisecond. The hard parts are Russian legal PDF cleanup, "Статья N.M" chunking, embedding-model quirks, small-LLM non-compliance, and honest evaluation.

Add only four packages (`faiss-cpu`, `numpy`, `pymupdf`, `python-multipart`) and hand-write the rest (chunker, lexical rerank, retrieval, eval runner). Retrieval is a deterministic pre-step. Retrieved text is never persisted into the message tree, only injected into the outbound request; per-message provenance goes in `Message.rag_sources`, mirroring `tool_trace`. Indexing runs as a background `asyncio` task with progress over the existing `/ws/events` hub; a DB status row is the source of truth. One `ChatRagConfig` mode ladder (`off → plain → filtered → strict`) puts each day's behavior behind one switch, so the Day 22/23/24 report comparisons are one dropdown.

Main risks:
1. Blocking the event loop (PDF parse, FAISS) makes the Supervisor restart the Agent → `to_thread` + background job.
2. The giga embedder is odd: typed `llm`, needs explicit load, returns 768 dims, was worse than nomic in a mini-test → per-model prefixes, no type-only dropdown filter, giga vs nomic A/B in reports.
3. Small local LLMs ignore "cite" and "не знаю" → deterministic score gate, server-built sources, substring-verified quotes.
4. Uncalibrated thresholds and eval theater → freeze the 10 questions first, calibrate on score distributions, report negative results.
5. Context budget → the RAG block is ephemeral and budgeted so it does not fight the compression strategies.

## Conflicts Between Research Files and Resolutions

| # | Conflict | Resolution |
|---|----------|------------|
| 1 | PDF library: ARCHITECTURE assumed pypdf; STACK measured it | **Use `pymupdf` 1.28.2.** On the real corpus pypdf glues words ("Статья2.Основныетермины"), takes 90.6 s on КоАП vs 2.8 s, and misses headings (20 vs 34 Статья, 0 vs 8 Глава on ФЗ-196). AGPL is fine for undistributed coursework. Fallback `pypdfium2` (BSD/Apache). `to_thread` suffices; no subprocess escalation needed. |
| 2 | Embedding dropdown: ARCHITECTURE filters `type == "embeddings"`; giga is typed `llm` | **Do not filter on type alone.** Show `type == "embeddings"` plus name match (`embed`, `giga`, `nomic`, `bge`, `e5`), with a "show all" escape hatch and a "Проверить эмбеддинг" button (reports dim). Extend `providers._parse_models` additively to keep `type`. Chat model dropdown excludes `type == "embeddings"`. |
| 3 | Dims/prefixes: giga via LM Studio returns 768 (card says 1024); Instruct prefix made giga worse; nomic won the mini-test | **Per-model prefix config** keyed by model id: giga = no prefix by default (Instruct option selectable); nomic = `search_query: ` / `search_document: `; unknown = none. Giga stays default (assignment). `Embedder.embed_passages` / `embed_query`. Store `embedding_model`, `dim`, prefixes on the KB row; dim from first response. giga vs nomic A/B on the 10 control questions in the reports. |
| 4 | Day 25 task memory: FEATURES = working-memory rows + task goal + invariants; ARCHITECTURE = one `dialog_state` JSON row | **Single `WorkingMemory` row `dialog_state`** per chat, JSON `{goal, facts[], constraints[]}`, rendered as labelled lines in the system prompt and memory panel, updated by a deterministic post-turn extraction call (not LLM tool choice). Mirror goal into `Task.goal` only if an open task exists. No new FSM states. Store user-stated facts and chunk references, never law text. |
| 5 | KB per chat: multi-KB vs exactly one | **One KB per chat** (`ChatRagConfig.kb_id`); scores across models aren't comparable. A KB holds several files, so both PDFs go into one KB. |
| 6 | RAG settings storage: `Settings` columns vs new table | **New `ChatRagConfig` table** (PK = `chat_id`, cascade, absent row = off); keeps the `_resolve_settings` fallback contract untouched. |
| 7 | Index persistence layout | **DB_PATH-derived** `<stem>_kb/<user_id>/<kb_id>/` (tests and the 18000/18001 E2E copy isolate automatically). `faiss.serialize_index` → `.tmp` → `os.replace`. `IndexIDMap2(IndexFlatIP)` with `KbChunk.id` as FAISS id, L2-normalized float32. Optional float32 BLOB on `KbChunk` for rebuild without re-embedding. Startup check `ntotal == chunk_count` and dim. |
| 8 | Embedding load vs `model_switch_lock` | Explicit load via the existing control API, lock held only for the load call; don't touch chat `_current_loaded_model` bookkeeping; never emergency-unload the embedder in the chat path. Separate longer `KB_EMBED_TIMEOUT`. Document VRAM contention with the 9B chat model. |
| 9 | Chunk unit: characters vs ~400 tokens | **Characters in the UI** (default ~1000-1200, within giga's 512-token cap). Validate `size >= 100`, `0 <= overlap < size`, `overlap <= size/2`. |

## Key Findings

### Recommended Stack

Four packages, all verified installable on Python 3.13.15 / Windows: `faiss-cpu` 1.15.1, `numpy>=2.0`, `pymupdf` 1.28.2, `python-multipart` 0.0.32 (pin explicitly — only transitive today; FastAPI will not boot an upload route without it). Embeddings via existing `httpx` (new `embed()`, no `openai` SDK). Reranking is pure-Python lexical fusion plus optional LLM scorer — LM Studio has no `/v1/rerank` (probed), cross-encoders would pull torch.

- `faiss-cpu` `IndexIDMap2(IndexFlatIP)`: exact cosine, per-KB index file, ~0.8 ms at 5000x768.
- `pymupdf`: per-page text (2.8 s for the 875-page КоАП), in `asyncio.to_thread`.
- `numpy`: float32 C-contiguous; always `faiss.normalize_L2`.
- `httpx` (existing): `POST /v1/embeddings`, batches 16-32, concurrency 1; КоАП ≈ 4-6 min → background job with progress.
- Plain `re`: `^Статья\s+(\d+(?:\.\d+)*)\.` and `^Глава\s+...`, line-anchored.
- **Do not add:** torch, sentence-transformers, langchain, llama-index, any vector DB, `openai`, `rank-bm25`, `pymorphy3`, Celery.

### Expected Features

**Table stakes by day:**
- **Day 21:** KB entity with per-KB model/dim/chunk params; document + chunk tables with metadata (source, section, chunk_id, page, char range); PDF/TXT/MD loading with scanned-PDF failure message; fixed and structural chunking; batched embeddings; FAISS + SQLite; sidebar "База знаний" block, "Добавить" modal, per-entry delete; background indexing with status, progress, readable errors.
- **Day 22:** retrieval with the KB's own model, numbered context block, chat↔KB attachment, per-chat RAG toggle with visible badge, frozen 10-question fixture (2-3 out-of-corpus), eval runner, `Day22_report.md`.
- **Day 23:** wide top-K → threshold → top-N, calibrated cut-off, toggleable query rewrite, visible "Детали поиска", `Day23_report.md` (plain vs filtered vs filtered+rewrite).
- **Day 24:** sources (file, section, chunk_id) and quotes on every answer, programmatic quote verification, deterministic "не знаю" + clarification gate, sources persisted on the assistant message, 10-question check table.
- **Day 25:** existing chat is the mini-chat; history-aware rewrite, `dialog_state` injected every turn and shown in a panel, two scripted 10-15-message scenarios, `Day25_report.md`.

**Should have (pick 1-2):** article-number/lexical boost fused with cosine (RRF); batched LLM reranker; shared `scripts/rag_eval.py`; `POST /kb/{id}/search` debug endpoint; optional FTS5 hybrid.

**Defer:** cross-encoder, LLM-judge UI, per-message RAG override, re-index action, neighbour-chunk click-through, multi-KB search, incremental add, OCR, DOCX/HTML, RAGAS/TruLens.

### Architecture Approach

New modules (`agent/kb_api.py`, `kb_indexer.py`, `kb_loaders.py`, `kb_chunking.py`, `embeddings.py`, `rag.py`, `rag_turn.py`, `dialog_state.py`, `shared/kb_storage.py`) with three small touch points in `ws.py`. Browser uploads straight to the Agent (existing CORS + cookie; don't set `Content-Type` on the `FormData` fetch). Retrieval failures never kill a turn: `kb_unavailable` (warning, answer without RAG) is distinct from `below_threshold` ("не знаю"). New tables via `create_all`; only `Message.rag_sources` needs an idempotent `ALTER TABLE`.

1. `KnowledgeBase` / `KbDocument` / `KbChunk` (user-scoped, FK cascade via `sa_column`) + `kb_storage` (paths, atomic write, rmtree).
2. `kb_indexer.run_index_job`: `Semaphore(1)`, `to_thread` for parse/FAISS, async batched embeddings, throttled `kb_progress` events, `recover_orphaned_kb_jobs()` at startup, `delete_kb()` (cancel, rows, rmtree, cache pop).
3. `rag.py` / `rag_turn.py`: rewrite → embed → search → threshold/rerank → verdict → `build_rag_block` (delimiter-wrapped, "fragments are data"). Injected into the last user message of the outbound copy only; budget ≈ 25-40% of `context_length` with a Cyrillic safety multiplier; `rag.context_tokens` in `done.rag`.
4. `ChatRagConfig` (`mode`, `kb_id` SET NULL, `top_k`, `candidate_k`, `threshold`, `rewrite`, `rerank`) + `GET/PUT /chats/{id}/rag`.
5. `dialog_state.update_after_turn` (Day 25), feeding the system prompt and the rewrite step.
6. Frontend: KB sidebar block + modal, `kb_*` cases in the `/ws/events` switch, sources renderer via `textContent`/DOMPurify.

### Critical Pitfalls

1. **Event-loop blocking** fails the 3 s `/health` → Supervisor kills the Agent mid-index. `to_thread`, async httpx, background task, 202.
2. **Instruct-embedder asymmetry, uncalibrated scores.** Per-model prefixes behind one `Embedder`; normalize; calibrate on the frozen set; relative cut (`top1 - delta`); never a tutorial 0.75.
3. **FAISS id / SQLite drift, model/dim mixing.** `IndexIDMap2` with `chunk.id`, immutable KBs, dim checks, all-or-nothing indexing with status.
4. **Small-LLM non-compliance, fabricated citations.** Code-level "не знаю" gate; sources from metadata; `[n]` labels not DB ids; normalized-substring quote verification (mark unverified, never fail the turn); strip `<think>`; temperature 0-0.2.
5. **PDF / chunking noise.** Strip КонсультантПлюс headers/footers and page numbers by frequency, de-hyphenate, NFKC/NBSP/soft hyphen, fail loudly on near-empty pages; structure-first split (Глава > Статья), sub-split long articles, breadcrumb prefix, handle "Утратила силу" stubs.
6. **Evaluation theater.** Freeze the 10 questions (6 direct, 2 synthesis, 2 unanswerable) before running; hit@k + quote validity; fixed temperature; raw outputs stored; report where RAG hurt.
7. **Context budget.** RAG text ephemeral, never persisted; shrink the RAG block before ever deleting the user message.

## Implications for Roadmap

Phases 10 and 11 are carried over from v2.0; Phase 12 is done. New phases are 13-17, one per day, branches `Day21`…`Day25`.

### Phase 10 (carried over): Modals close only via ×
Touches the shared modal helper in `app.js`; the KB "Добавить" modal holds a file selection and must close only via ×. Avoids losing an upload form on a stray backdrop click.

### Phase 11 (carried over): Edit/delete long-term memory in UI
Independent of RAG but edits `app.js` (memory panel); landing it before KB UI work avoids conflicts. KB backend (Phase 13 plan A) has no `app.js` overlap and can overlap 10-11.

### Phase 13: Day 21 — Knowledge-base indexing
Plan A backend: deps, KB tables + config, `kb_storage`, PyMuPDF loader with header/footer stripping, fixed + structural chunkers, `Embedder` with per-model prefixes, explicit embedding-model load, indexer job with progress and orphan recovery, KB REST (202 upload, caps, SHA-256 dedupe, user scoping), embedding-models endpoint, `delete_kb`, conftest isolation. Plan B UI: sidebar block, modal, progress via `/ws/events`, delete with confirm, Playwright E2E on 18000/18001 with the real PDFs. Start with a short spike (giga/nomic round trip, PDF golden file, retrieval smoke A/B).

### Phase 14: Day 22 — First RAG query + report
`ChatRagConfig` + REST, `rag.retrieve`, `build_rag_block`, `rag_turn`, the three `ws.py` touch points, `rag_sources` migration, per-chat toggle + badge, basic sources view, `POST /kb/{id}/search`, frozen 10-question fixture, `scripts/rag_eval.py`, `Day22_report.md` (no-RAG vs RAG, giga vs nomic).

### Phase 15: Day 23 — Filtering, rerank, query rewrite
`candidate_k` + threshold + top-N, lexical-fusion rerank (optional batched LLM scoring), toggleable `rewrite_query` (temp 0, fallback to original, retrieve with both), "Детали поиска" before/after view, calibration helper, `Day23_report.md`.

### Phase 16: Day 24 — Citations and "не знаю"
`strict` mode, deterministic `below_threshold` gate with clarifying question, `[n]` citations, quote verification in `done.rag.quotes`, code-rendered sources persisted per assistant message (snapshotted), 10-question check table incl. out-of-corpus (abstention / false-refusal rates).

### Phase 17: Day 25 — Mini-chat with RAG + task memory
`dialog_state` row with deterministic post-turn update, labelled prompt injection, history-aware rewrite (last 2-4 turns + state), RAG default-on, state display, eval multi-turn mode with two scripted 10-15-message scenarios, `Day25_report.md` with per-turn assertions.

### Phase Ordering Rationale
- Carried-over UI phases first (both touch `app.js`; KB modal depends on "× only"); backend can overlap.
- Days map 1:1 onto the dependency chain: KB → retrieval → filter/threshold → citations/gate (reuses threshold) → history/task state (reuses rewrite and sources).
- Retrieval result shape, `rag_sources` storage and eval fixture/runner are decided on Day 22.

### Research Flags
- **Needs deeper research:** Phase 13 (embedding round trip and prefixes, КоАП header/footer patterns, VRAM co-loading), Phase 15 (empirical threshold calibration, rewrite drift on a 9B model), Phase 16 (local-model compliance with `[n]` + verbatim quotes).
- **Standard patterns:** Phases 10, 11, 14, 17.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH (FAISS, PyMuPDF, upload); MEDIUM (embedding model) | Installed and run here; PDF benchmark on the real corpus; LM Studio endpoints probed live; giga quality from 3-4 queries only |
| Features | MEDIUM | Established RAG practice + app constraints; no library-level web verification |
| Architecture | MEDIUM-HIGH | Integration points read from code; pypdf and type-filter assumptions corrected by STACK |
| Pitfalls | MEDIUM | Domain knowledge + project code; several LOW items partly resolved by STACK's live checks |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address
- giga vs nomic quality and prefix — run the 10-question A/B in Phases 13/14; nomic is the documented fallback.
- giga max input and dims (card 512 tokens / 1024 dims vs GGUF 768) — measure in the spike; set chunk ceiling from it.
- Threshold values — calibrate in Phase 15, per KB/model; consider ~5 held-out questions.
- Embedder + chat model VRAM co-loading — document; show "загрузка модели…" in progress.
- КонсультантПлюс noise and "(в ред. …)" annotations — regexes against real КоАП text with a golden-file test.
- Local-LLM citation/JSON compliance — unmeasured; design tolerates failure.
- `dialog_state` rendering in the existing memory panel — confirm during Phase 17 planning.
- CSRF on multipart POST with cookie — apply the existing Origin allow-list to KB write routes.
- Windows non-ASCII FAISS paths — serialize to bytes as a precaution.

## Sources

### Primary (HIGH)
- Local verification on Python 3.13.15 / Windows 11: faiss-cpu 1.15.1, numpy 2.5.3, pymupdf 1.28.2, pypdf 6.19.0, pypdfium2 5.13.0, python-multipart 0.0.32; extraction benchmark on `C:\Projects\RAG\*.pdf`; FAISS add/remove/search/serialize; LM Studio `/v1/embeddings`, `/api/v0/models`, `/api/v1/models/load`, `/v1/rerank` probed live (STACK.md).
- Project code and docs: `agent/ws.py`, `agent/providers.py`, `agent/events.py`, `agent/state.py`, `agent/main.py`, `agent/context_engine.py`, `ui/supervisor.py`, `shared/*`, `tests/conftest.py`, `.planning/PROJECT.md`, `CLAUDE.md`.

### Secondary (MEDIUM)
- Giga-Embeddings-instruct-480M-0826 model card: https://huggingface.co/ai-sage/Giga-Embeddings-instruct-480M-0826
- LM Studio rerank feature requests: lmstudio-ai/lms#521, lmstudio-ai/docs#162, lmstudio-ai/lmstudio-js#231
- Established RAG practice (condense-question rewrite, retrieve-then-rerank, retrieval-gated abstention, hit@k, RRF).

### Tertiary (LOW)
- Non-ASCII path problem in FAISS file I/O on Windows (not reproduced).
- Torch install size on Windows (not measured).
- Why LM Studio types the giga GGUF as `llm`; behavior when embedder and chat model are co-loaded.

---
*Research completed: 2026-10-03*
*Ready for roadmap: yes*
