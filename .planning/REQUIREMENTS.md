# Requirements: AiAdventAgentV2 — Week 5: RAG

**Defined:** 2026-10-03
**Core Value:** The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where.
**Research:** `.planning/research/SUMMARY.md` (stack, architecture, pitfalls; conflicts resolved there)

## v3.0 Requirements

### Modals (carried over from v2.0, Day 21)

- [ ] **MODAL-01**: Every modal in the app closes only via its "×" button; a click on the backdrop (outside the dialog) and the Escape key no longer close it

### Long-term memory editing (carried over from v2.0, Day 21)

- [x] **MEMUI-01**: Each entry of the sidebar long-term memory list (`#memory-long-term`) has "Редактировать" and "Удалить" buttons; the working-memory list stays read-only
- [x] **MEMUI-02**: "Редактировать" opens an inline form in place of the entry (key input + value textarea prefilled with the full stored value, not the 160-character preview; "Сохранить" / "Отмена"); no modal is added; an unsaved draft survives panel re-renders; saving updates the entry, refreshes the panel and shows a toast; keys and values are rendered as plain text only
- [x] **MEMUI-03**: `PUT /api/v1/memory/long-term/{entry_id}` updates `key` and/or `value` of the caller's own entry with server-side validation (key stripped and non-blank, value not whitespace-only, at most 200 / 50 000 characters, at least one field); `updated_at` is refreshed and `created_at` kept; a foreign or unknown id returns 404; the route requires the session cookie (401), an allowed Origin (403) and a JSON content type (415)
- [x] **MEMUI-04**: Renaming a key onto another existing key of the same user returns 409 with a Russian message and changes nothing (unique `(user_id, key)`)
- [x] **MEMUI-05**: `DELETE /api/v1/memory/long-term/{entry_id}` removes the caller's own entry (204; foreign or unknown id -> 404; session cookie and allowed Origin required); the UI asks for confirmation with `confirm()` before the request and refreshes the list
- [x] **MEMUI-06**: An edit or delete takes effect on the next turn without any cache invalidation (the next `build_system_prompt` and the headless scheduler prompt reflect the new content); pytest covers CRUD, scoping, validation, conflict and the prompt effect; docs are in sync; the full suite passes

### Knowledge base indexing (Day 21)

- [ ] **KB-01**: User sees a collapsible "База знаний" block in the left sidebar with a "Добавить" button and the list of their knowledge bases (name, status, file/chunk counts), each with a "Удалить" button; knowledge bases are scoped by `user_id`
- [ ] **KB-02**: "Добавить" opens a modal with: KB name (string); files area showing one or more selected files with a "Выбрать файлы" button; chunking strategy switch "по фиксированному размеру" / "по структуре (заголовки/разделы/файлы)"; "Размер чанка" and "Перекрытие" number fields shown only for the fixed-size strategy; embedding model dropdown built like the main model picker with default `giga-embeddings-instruct-480m-0826`; "Индексировать" button
- [ ] **KB-03**: User can upload PDF, TXT and MD files; PDF text is extracted with PyMuPDF and cleaned (repeated headers/footers and page numbers stripped, hyphenation and whitespace normalized); a file without a text layer (scan) fails with a readable message
- [ ] **KB-04**: Fixed-size chunking splits text into chunks of the given size (characters) with the given overlap; the server validates size and overlap (`0 <= overlap < size`) and rejects invalid values with a Russian message
- [ ] **KB-05**: Structural chunking splits by document structure — files, headings/sections and, for legal texts, "Глава" / "Статья N" boundaries — and sub-splits sections that exceed the embedding model's input limit
- [ ] **KB-06**: Every chunk is embedded through the selected LM Studio embedding model (`/v1/embeddings`, batched, explicit model load when LM Studio does not auto-load it, per-model query/document prefixes); embedding models typed `llm` by LM Studio (e.g. giga-embeddings) remain selectable
- [ ] **KB-07**: The index is persisted as one FAISS index per knowledge base (cosine via normalized inner product) plus SQLite rows for the KB, its documents and chunks; each chunk carries metadata `source`, `title`/file, `section`, `chunk_id` (plus page/char range); the KB row records embedding model and vector dimension
- [ ] **KB-08**: Indexing runs as a background job without blocking the Agent (health checks keep passing); the UI shows live status and progress (queued / indexing x of y / ready / failed with a readable error) via `/ws/events` with REST fallback; a job interrupted by an Agent restart is marked failed
- [ ] **KB-09**: Deleting a knowledge base removes its SQLite rows (cascade), its on-disk index and uploaded files, and in-memory caches; another user's KB is never visible or accessible (404)
- [ ] **KB-10**: User can run a test search against a KB ("тест поиска" field in the KB UI, backed by `POST /api/v1/kb/{id}/search`) and see the top chunks with scores and metadata
- [ ] **KB-11**: Both PDFs from `C:\Projects\RAG` (ФЗ-196, КоАП РФ) index successfully with each chunking strategy; pytest covers chunking, persistence, scoping and delete with mocked embeddings and isolated index directories

### First RAG query (Day 22)

- [x] **RAG-01**: User can attach one knowledge base to a chat and switch the chat between "без RAG" and "с RAG"; the current mode and KB are visibly indicated in the chat
- [x] **RAG-02**: With RAG on, each question is embedded with the KB's own model, the top-K relevant chunks are retrieved and merged with the question into the LLM request; the stored user message stays the raw question and retrieved text is never persisted into the message tree
- [x] **RAG-03**: The RAG context block has a token budget that coexists with the existing context-compression strategies (it never triggers deletion of the user message)
- [x] **RAG-04**: If retrieval fails (embedding model unavailable, KB deleted), the turn still answers without RAG and shows a visible warning
- [x] **RAG-05**: The sources used for an answer (file, section, chunk_id, score) are stored on the assistant message and shown under it
- [x] **RAG-06**: A frozen set of 10 control questions on the KB exists before any comparison run, each with the expected answer content and expected sources (including out-of-corpus questions)
- [x] **RAG-07**: `scripts/rag_eval.py` runs the control set across modes and embedding models and produces the tables (retrieval hit@k, answers, sources) used by the Day 22-25 reports
- [x] **RAG-08**: `Day22_report.md` lists the 10 questions with expectation and expected sources, and compares answers without RAG vs with RAG, plus an A/B of nomic vs bge-m3 embeddings on retrieval hit@k (giga is not available as an embedder — see Phase 13 spike / D-15)

### Reranking and filtering (Day 23)

- [x] **RANK-01**: Retrieval is two-stage: a wider candidate top-K, then filtering/reranking down to a final top-K; both K values and the similarity cut-off threshold are configurable per chat
- [x] **RANK-02**: A lexical heuristic reranker (word and article-number overlap, e.g. "ст. 12.9") fused with the cosine score can be enabled
- [x] **RANK-03**: An LLM reranker can be enabled: one batched prompt scores the top candidates
- [x] **RANK-04**: Hybrid retrieval can be enabled: SQLite FTS5 full-text search fused with vector search via reciprocal rank fusion
- [x] **RANK-05**: Query rewrite can be toggled: one low-temperature LLM call turns the question into a standalone search query, falling back to the original on bad output
- [x] **RANK-06**: A collapsible "Детали поиска" block under each RAG answer shows the (rewritten) query, candidates before filtering with scores, what was cut and why, and the final chunks
- [x] **RANK-07**: The cut-off threshold is calibrated on the control set (score distributions of answerable vs out-of-corpus questions), per embedding model
- [x] **RANK-08**: `Day23_report.md` compares quality without filter/rewrite vs with filter, with each reranker and with rewrite, on the control set
- [x] **RANK-09**: An optional LLM-judge script (DeepSeek) scores answers as an extra column in the reports; the manual verdict stays primary

### Citations and anti-hallucination (Day 24)

- [x] **CITE-01**: Every RAG answer contains the answer text, a list of sources (source + section / chunk_id) and quotes — fragments of the retrieved chunks
- [x] **CITE-02**: Each quote is verified server-side as a (normalized) substring of the cited chunk and marked verified / unverified; invalid source references are rejected; sources are rendered from chunk metadata, not trusted from model text
- [x] **CITE-03**: If the best relevance after filtering is below the threshold, the assistant answers "не знаю" and asks a clarifying question — enforced in code, not only by prompt
- [x] **CITE-04**: A check on the 10 control questions records per answer: sources present, quotes present, answer meaning matches quotes, and correct "не знаю" on out-of-corpus questions (in a Day 24 report section)

### Mini-chat with RAG and task memory (Day 25)

- [x] **RCHAT-01**: The existing chat works as the RAG mini-chat: dialog history is kept, retrieval runs on every new question, and every answer shows its sources
- [ ] **RCHAT-02**: Task memory per chat records the dialog goal, what the user has already clarified, and fixed constraints/terms; it is updated after each turn deterministically and shown in the UI
- [ ] **RCHAT-03**: Task memory and recent history feed both the system prompt and the retrieval query rewrite, so follow-up questions retrieve correctly
- [x] **RCHAT-04**: Two scripted long scenarios of 10-15 messages run end to end; the assistant keeps the goal and gives answers with sources on every turn
- [x] **RCHAT-05**: `Day25_report.md` contains both scenario transcripts with per-turn checks (goal kept, sources present, task memory contents)

## Future Requirements

- Multi-KB search in one chat (needs score normalization across models)
- Re-index / incremental add of files to an existing KB (delete and re-add for now)
- Per-message RAG override
- Cross-encoder reranker (would pull torch)
- OCR for scanned PDFs, DOCX/HTML ingestion

## Out of Scope

| Feature | Reason |
|---------|--------|
| External vector DB (Chroma, Qdrant, pgvector) | Assignment names FAISS + SQLite; local-first constraint |
| LangChain / LlamaIndex | Hand-written pipeline is small and keeps the app's patterns |
| torch / sentence-transformers | Heavy dependency; LM Studio serves embeddings; no `/v1/rerank` in LM Studio |
| Separate mini-chat UI | The existing chat already has history, memory and tasks |
| Shared knowledge bases across users | Data scope is per `user_id` |
| RAGAS / DeepEval integration | Course scope; eval script + manual rubric + optional LLM-judge suffice |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| MODAL-01 | Phase 10 | Pending |
| MEMUI-01 | Phase 11 | Complete |
| MEMUI-02 | Phase 11 | Complete |
| MEMUI-03 | Phase 11 | Complete |
| MEMUI-04 | Phase 11 | Complete |
| MEMUI-05 | Phase 11 | Complete |
| MEMUI-06 | Phase 11 | Complete |
| KB-01 | Phase 13 | Pending |
| KB-02 | Phase 13 | Pending |
| KB-03 | Phase 13 | Pending |
| KB-04 | Phase 13 | Pending |
| KB-05 | Phase 13 | Pending |
| KB-06 | Phase 13 | Pending |
| KB-07 | Phase 13 | Pending |
| KB-08 | Phase 13 | Pending |
| KB-09 | Phase 13 | Pending |
| KB-10 | Phase 13 | Pending |
| KB-11 | Phase 13 | Pending |
| RAG-01 | Phase 14 | Complete |
| RAG-02 | Phase 14 | Complete |
| RAG-03 | Phase 14 | Complete |
| RAG-04 | Phase 14 | Complete |
| RAG-05 | Phase 14 | Complete |
| RAG-06 | Phase 14 | Complete |
| RAG-07 | Phase 14 | Complete |
| RAG-08 | Phase 14 | Complete |
| RANK-01 | Phase 15 | Complete |
| RANK-02 | Phase 15 | Complete |
| RANK-03 | Phase 15 | Complete |
| RANK-04 | Phase 15 | Complete |
| RANK-05 | Phase 15 | Complete |
| RANK-06 | Phase 15 | Complete |
| RANK-07 | Phase 15 | Complete |
| RANK-08 | Phase 15 | Complete |
| RANK-09 | Phase 15 | Complete |
| CITE-01 | Phase 16 | Complete |
| CITE-02 | Phase 16 | Complete |
| CITE-03 | Phase 16 | Complete |
| CITE-04 | Phase 16 | Complete |
| RCHAT-01 | Phase 17 | Complete |
| RCHAT-02 | Phase 17 | Pending |
| RCHAT-03 | Phase 17 | Pending |
| RCHAT-04 | Phase 17 | Complete |
| RCHAT-05 | Phase 17 | Complete |

**Coverage:**
- v3.0 requirements: 44 total
- Mapped to phases: 44
- Unmapped: 0

---
*Requirements defined: 2026-10-03*
*Last updated: 2026-10-03 after roadmap creation (traceability filled)*
