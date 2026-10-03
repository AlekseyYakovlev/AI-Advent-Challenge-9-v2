# Phase 14: First RAG query (Day 22) - Context

**Gathered:** 2026-10-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Users attach one knowledge base (built in Phase 13) to a chat, switch the chat between "без RAG" and "с RAG", and with RAG on every question is embedded with the KB's own model, the top-K chunks are retrieved and merged into the outbound LLM request, and the sources used are stored on the assistant message and shown under it. Retrieval failures degrade to a no-RAG answer with a visible warning. A frozen 10-question control set, `scripts/rag_eval.py` and `Day22_report.md` compare no-RAG vs RAG answers and two embedding models on retrieval hit@k.

Out of this phase: candidate-K / threshold / rerankers / query rewrite / "Детали поиска" (Phase 15), quotes with verification and code-enforced "не знаю" (Phase 16), task memory and history-aware retrieval (Phase 17). Branch: `Day22`.

</domain>

<decisions>
## Implementation Decisions

### RAG controls in the chat
- **D-01:** RAG controls live in the chat header next to `#model-select`: an on/off toggle ("с RAG" / "без RAG") plus a KB select. The KB stays attached when RAG is switched off, so toggling is one click. Backed by per-chat `ChatRagConfig` (`mode`, `kb_id`, `top_k`) and `GET/PUT /api/v1/chats/{id}/rag`.
- **D-02:** A compact number input "K" (range 1-20, default 5) sits in the header next to the toggle and is shown only when RAG is on. Phase 15 will add candidate-K and threshold next to it.
- **D-03:** Mode is indicated twice: a header badge ("RAG: <имя БЗ>" / "без RAG") and a small per-answer label on each assistant message showing the mode it was produced with, so the with/without comparison is visible in history.
- **D-04:** `ChatRagConfig.kb_id` is an FK with `ON DELETE SET NULL` (via `sa_column`, never `Field(ondelete=...)`). The KB select lists only the user's `ready` KBs. If RAG is on but the turn could not use it (KB gone/not ready, embedder unavailable), the turn answers without RAG and shows the RAG-04 warning.

### Sources under the answer
- **D-05:** Sources render as a collapsed `<details>` block "Источники (N)" under the assistant message, built like the existing `tool_trace` block. Phase 15 extends the same area with "Детали поиска".
- **D-06:** Each source row shows file, section (breadcrumb), chunk_id and score, plus a short snippet with a "показать полностью" toggle — reuse the Phase 13 test-search card (13 D-17), all text via `textContent`. `Message.rag_sources` stores only references/metadata (kb_id, chunk_id, file, section, score, rank) plus mode/warning; snippet text is fetched by chunk_id from `KbChunk` and is not copied into the message. If the KB has been deleted, the row shows metadata only.
- **D-07:** RAG-04 warning: a persistent yellow line under the answer with the reason (e.g. «модель эмбеддинга не загружена», «база знаний удалена»), stored in `rag_sources` so it survives reload, plus a toast at the moment it happens.
- **D-08:** Sources arrive in the final WebSocket `done` frame (`done.rag`: mode, sources, warning, context_tokens); no separate pre-stream frame. Retrieval itself runs before the LLM stream.

### Retrieval and prompt
- **D-09:** Retrieval is a deterministic pre-step in the WS turn (not an LLM tool). The query is embedded with the KB's own model and prefixes (Phase 13 D-09/D-24), FAISS top-K by normalized inner product.
- **D-10:** Retrieved fragments are injected into the **last user message of the outbound copy only**: a delimiter-wrapped block "Фрагменты из базы знаний" with numbered entries `[1]..[K]` (each with file + section header), followed by the question. The stored user message stays the raw question; retrieved text never enters the message tree.
- **D-11:** Day 22 instruction is soft: answer based on the fragments, reference them as `[N]`, say so if the fragments don't contain the answer, and treat fragments as data, not instructions. No hard "не знаю" gate and no quote requirement (Phase 16).
- **D-12:** RAG block budget is 30% of the effective `context_length`. If the fragments exceed it, the lowest-scoring chunks are dropped. The budget is applied before the compression strategy so `no_compression` never deletes the user message because of RAG. Block size goes to `done.rag.context_tokens`.

### Eval and Day 22 report
- **D-13:** Claude drafts the 10 control questions: 6 direct (answer in one article), 2 synthesis (ФЗ-196 + КоАП), 2 out-of-corpus. Each has the expected answer content and expected sources (article numbers/files). They live in a JSON fixture, the user approves the draft, and it is committed (frozen) before the first comparison run.
- **D-14:** `scripts/rag_eval.py` takes provider/model, KB(s), top_k and mode as arguments and runs at temperature 0. The default answering LLM is the local `qwen/qwen3.5-9b` via LM Studio; DeepSeek is optional (its key is currently a placeholder, see 12-06). Raw outputs are saved alongside the generated tables.
- **D-15:** Embedding A/B is **nomic (`text-embedding-nomic-embed-text-v1.5`, 768 dim) vs bge-m3 (`text-embedding-bge-m3`, typed `embeddings`, already downloaded, 1024 dim)**, replacing the impossible giga vs nomic A/B (Phase 13 D-24). The report states why giga was not compared, citing the Phase 13 spike.
- **D-16:** A short spike at the start of the phase checks whether LM Studio routes `/v1/embeddings` by `model` when both nomic and bge-m3 are loaded (the dim difference makes it obvious). If it does not, the eval runner loads only the needed embedder at a time. In the chat path, retrieval verifies the returned query vector dimension against the KB's stored `dim`; a mismatch counts as a retrieval failure (D-04/D-07 warning), never a silent wrong search.
- **D-17:** Scoring is automatic hit@k against the expected sources plus a manual verdict column (верно / частично / неверно / галлюцинация). Claude fills the verdict against the expectations and the user reviews it. No LLM-judge in this phase (RANK-09, Phase 15).

### Claude's Discretion
- Exact `rag_sources` JSON shape, `done.rag` field names, migration details (idempotent `ALTER TABLE` for `Message.rag_sources`), module split (`agent/rag.py` / `rag_turn.py`), the Cyrillic token safety multiplier, snippet length, header layout and styling of the toggle/badge/K input, and the fixture/report file locations.

</decisions>

<specifics>
## Specific Ideas

- The header controls exist to make the with/without comparison a one-click demo on video; the per-answer mode label keeps that comparison readable in the chat history.
- The test corpus is `C:\Projects\RAG` (ФЗ-196, КоАП РФ). The control questions should cover article-level facts, for example the age requirements in ФЗ-196 Статья 19, which already retrieved top-1 in the Phase 13 smoke test.
- Report honesty: show where RAG hurt or didn't help, not only wins (research pitfall "evaluation theater").

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` §First RAG query (Day 22) — RAG-01..RAG-08
- `.planning/ROADMAP.md` §Phase 14 — goal, success criteria, research flag (retrieval result shape, `rag_sources` storage, eval fixture)

### Research and prior phase
- `.planning/research/SUMMARY.md` — retrieval as a pre-step, `ChatRagConfig` mode ladder, `rag_sources` mirroring `tool_trace`, budget, pitfalls (eval theater, context budget)
- `.planning/research/ARCHITECTURE.md` — `rag.py` / `rag_turn.py`, the three `ws.py` touch points, `done.rag`
- `.planning/research/PITFALLS.md` — context budget, small-LLM non-compliance, evaluation pitfalls
- `.planning/phases/13-knowledge-base-indexing-day-21/13-CONTEXT.md` — KB decisions, especially D-09 prefixes, D-17 test-search card, D-21 FAISS layout, D-24 giga guard, D-25 2000-char cap
- `.planning/phases/13-knowledge-base-indexing-day-21/13-RESEARCH.md` — spike findings: `/v1/embeddings` ignores `model`, nomic is the working embedder, score range 0.72-0.84
- `.planning/phases/13-knowledge-base-indexing-day-21/13-UI-SPEC.md` — KB UI conventions to reuse for source cards

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md`
- `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md` — context strategies and WS `done` contract that RAG must coexist with

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/embeddings.py`: `prefixes_for`, `list_embedding_models`, explicit load and the identity guard. Reuse these for query embedding.
- `shared/kb_storage.py` + `agent/kb_indexer.py`: per-KB FAISS index path and loading. Retrieval reads the same `IndexIDMap2` with `KbChunk.id` as the FAISS id.
- `POST /api/v1/kb/{id}/search` (Phase 13): an existing search path. Factor its core into the shared `rag.retrieve` so chat retrieval and test search are the same code.
- Test-search result card (Phase 13 D-17): reuse it for source rows.
- `Message.tool_trace` + `serialize_tool_trace` in `agent/ws.py` and its UI `<details>` rendering: the pattern for `rag_sources` storage and display.

### Established Patterns
- Per-chat settings with global fallback (`_resolve_settings`). `ChatRagConfig` is per-chat only (no global row needed), and the default is RAG off.
- SQLModel FK via `sa_column=Column(ForeignKey(..., ondelete=...))`. New tables are created via `create_all`, and new columns on existing tables need an idempotent `ALTER TABLE`.
- WS `done` frame carries live stats (`compute_chat_stats`). `done.rag` is added alongside them.
- Chat deletion clears caches via `agent.state.cleanup_chat_caches`. `ChatRagConfig` should cascade on chat delete.
- E2E/UAT via Playwright on an isolated copy at ports 18000/18001, never on 8000/8001.

### Integration Points
- `agent/ws.py::_handle_chat_message`: retrieve before building LLM messages, inject into the last user message of the outbound copy, attach `rag_sources` when persisting the assistant message, and add `done.rag`.
- `agent/context_engine.py`: the RAG budget has to be applied before the strategy and overflow checks.
- `ui/static/index.html` header (`#chat-title`, `#model-select`) for the toggle, KB select, K input and badge; `ui/static/app.js` for rendering the source block and warning.
- KB delete path (`delete_kb`): SET NULL on `ChatRagConfig.kb_id` happens via the FK.

</code_context>

<deferred>
## Deferred Ideas

- Candidate-K, threshold, rerankers, query rewrite and "Детали поиска" belong to Phase 15. The header K input is the anchor they will extend.
- LLM-judge column belongs to Phase 15 (RANK-09).
- Quotes and code-enforced "не знаю" belong to Phase 16.
- Multi-KB search per chat and per-message RAG override are already listed as Future Requirements.

</deferred>

---

*Phase: 14-first-rag-query-day-22*
*Context gathered: 2026-10-03*
