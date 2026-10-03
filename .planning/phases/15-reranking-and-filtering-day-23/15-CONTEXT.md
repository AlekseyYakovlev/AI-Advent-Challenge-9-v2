# Phase 15: Reranking and filtering (Day 23) - Context

**Gathered:** 2026-10-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Retrieval for a RAG chat turn becomes two-stage: a wider candidate top-K is fetched, low-scoring chunks are cut by a calibrated cosine threshold, and optional stages (lexical reranker, LLM reranker, hybrid SQLite FTS5 + RRF, query rewrite) can each be enabled independently per chat before the final top-K reaches the LLM. Every step is inspectable in a collapsible "Детали поиска" block under each RAG answer. The threshold is calibrated per embedding model, and `Day23_report.md` compares the configurations on the frozen control set, with an optional DeepSeek LLM-judge column. Requirements: RANK-01..RANK-09. Branch: `Day23`.

Out of this phase: quotes with verification and the code-enforced "не знаю" gate (Phase 16), history-aware rewrite and task memory (Phase 17), cross-encoder rerankers (Future Requirements).

Phase 14 is planned but not executed at the time of this discussion. The decisions below build on its planned shape (`agent/rag.py`, `ChatRagConfig`, `Message.rag_sources`, `done.rag`, `scripts/rag_eval.py`).

</domain>

<decisions>
## Implementation Decisions

### Search controls
- **D-01:** The header keeps the Phase 14 controls (RAG toggle, KB select, K). A small "Поиск ⚙" button next to K opens a compact popover with: "Кандидатов" (candidate-K), "Порог" (threshold) and four independent switches — "Лексич. реранк", "LLM-реранк", "Гибрид (FTS5)", "Переписывание запроса". The button is shown only when RAG is on. This replaces the "inline next to K" placement that 14 D-02 anticipated.
- **D-02:** The four stages are independent boolean flags on the per-chat config (ROADMAP success criterion 2), not a single mode ladder and not a `none|lexical|llm` radio as the research sketch suggested.
- **D-03:** Defaults for a chat with RAG on: candidate-K 20, final K 5, threshold = the calibrated value of the KB's embedding model, all four optional stages off. So the default pipeline is "threshold only" with no extra LLM calls.

### Детали поиска
- **D-04:** "Детали поиска" is a collapsed `<details>` block in the same area as "Источники" (14 D-05). Layout: header lines (original query, rewritten query if any, active stages, threshold used, total latency), then **one table of all candidates** with columns: rank before → rank after, cosine, and lexical / FTS / LLM scores where the stage ran, plus a status chip per row.
- **D-05:** Status values per candidate: «в ответе», «ниже порога», «вне top-K», «не вошло в бюджет» (dropped by the 30% RAG budget, 14 D-12), and «прошло по FTS» for keyword hits exempt from the threshold (D-08). When rewrite is on, each row also shows which query found it (original / rewritten / both).
- **D-06:** The full trace is stored on the assistant message: queries, a snapshot of the config used, and every candidate's ids, scores and status — metadata only, no chunk text (same rule as 14 D-06). It goes into `Message.rag_sources` as the next version of the Phase 14 JSON and into `done.rag`, so old answers keep their details after reload and answers made with different settings can be compared in history. No candidate cap.

### Threshold semantics
- **D-07:** The cut-off always applies to the **raw cosine score, before any reranking**. Rerankers only reorder the chunks that survived. Fused, RRF and LLM scores are never compared with the threshold. This is the same score Phase 16 will reuse for "не знаю".
- **D-08:** With hybrid on, a chunk found by FTS5 passes the cut even if its cosine is below the threshold. Its cosine is still computed and shown, and the row is marked «прошло по FTS». Without this exemption hybrid could not help on article-number questions.
- **D-09:** If the threshold cuts every candidate, the turn still goes to the LLM, with no fragment block and a short instruction that nothing relevant was found in the knowledge base. Under the answer a grey line states it with numbers, e.g. «Фрагменты не прошли порог (лучший 0.48 < 0.62)», and "Детали поиска" shows all candidates with their status. The result is stored as verdict `below_threshold` so Phase 16 only swaps the behavior. No templated reply and no LLM skip in this phase.
- **D-10:** Calibrated thresholds are code constants per embedding model (same style as the per-model prefixes in 13 D-09); unknown models default to 0 (no cut). `ChatRagConfig.threshold` is nullable: `NULL` means "use the calibrated value of the KB's model" and the popover shows it as «Порог 0.62 (калибр.)»; a number is a user override. Switching the KB therefore switches the default threshold automatically. No threshold column on `KnowledgeBase`.

### Rerankers and rewrite
- **D-11:** Query rewrite and LLM rerank use the chat's own provider/model, non-streaming, temperature 0. No separate helper-model setting in the UI.
- **D-12:** With rewrite on, **both** the original question and the rewritten query are embedded and searched, and the candidate lists are merged (best cosine per chunk). A drifting rewrite can add candidates but cannot lose what the original found. If the rewrite output is bad (empty, too long, multi-line, chatty), only the original is used (RANK-05 fallback). Rewrite is single-turn in this phase; history input is Phase 17.
- **D-13:** Stage order when several are on: (rewrite) → vector candidates for each query → (hybrid: FTS5 + RRF) → cosine threshold with the D-08 exemption → lexical fusion reorders all survivors → LLM rerank scores **the top 10** in one batched prompt → final top-K → budget (14 D-12). LLM rerank alone also scores the top 10 by the current order. Both rerankers may be on at once (chain), they are not mutually exclusive.
- **D-14:** If an optional stage fails mid-turn (LLM rerank unparsable or timed out, bad rewrite output, FTS5 query error), the stage is skipped and the turn continues with the previous order / the original query. The skip and its reason are shown in "Детали поиска" (e.g. «LLM-реранк: пропущен (некорректный ответ модели)»). No yellow warning and no toast: the answer is still a RAG answer. The eval script counts skips per stage.

### Calibration and Day 23 report
- **D-15:** The threshold is calibrated on a **separate calibration set**, not on the 10 control questions: about 20 questions drafted by Claude (roughly 12 answerable, 8 out-of-corpus), approved by the user and frozen as a fixture before the first calibration run (same checkpoint pattern as 14 D-13). The 10 control questions are used only for scoring. The report shows both score distributions per embedder and how the chosen threshold behaves on the control set.
- **D-16:** Both nomic and bge-m3 get a calibrated threshold with their distributions in the report (RANK-07). The full comparison matrix runs only on the embedder that won hit@k in `Day22_report.md`.
- **D-17:** `Day23_report.md` is an ablation of 7 runs on the 10 control questions: baseline (Day 22 plain top-5) · +threshold · +threshold+lexical · +threshold+LLM rerank · +threshold+hybrid · +threshold+rewrite · everything on. Per run: hit@k, chunks before/after, manual verdict, latency, skipped stages. All 7 runs get generated answers and verdicts.
- **D-18:** The LLM-judge script (RANK-09) runs on DeepSeek. The user will provide a real `DEEPSEEK_API_KEY` before the eval: the eval plan has a checkpoint that pauses and asks for the key in `.env`, then fills the judge column. The manual verdict stays primary (14 D-17: Claude fills it, the user reviews).

### Claude's Discretion
- Popover styling, ranges and steps for candidate-K and threshold, and a "reset to calibrated" affordance.
- The exact lexical scoring formula and the weight of an exact "ст. N" match; RRF constant; FTS5 tokenizer and query escaping.
- How existing KBs get their FTS5 rows (backfill from `KbChunk` rather than forcing re-indexing) and FTS cleanup on KB delete.
- How the cosine of an FTS-only hit is obtained.
- Rewrite and rerank prompt wording, timeouts, and the exact bad-output rules.
- The `rag_sources` v2 JSON shape and `done.rag` field names, new `ChatRagConfig` columns and their idempotent migration.
- Whether the Phase 13 test-search modal gains any of the new stages (not required).
- Fixture and raw-output file locations, judge rubric and script name.

</decisions>

<specifics>
## Specific Ideas

- The popover mockup the user picked:
  ```
  [с RAG ●] [БЗ: КоАП ▾] K [5] [Поиск ⚙]
                            ┌────────────────────────┐
                            │ Кандидатов   [20]      │
                            │ Порог        [0.62]    │
                            │ ☐ Лексич. реранк        │
                            │ ☐ LLM-реранк           │
                            │ ☐ Гибрид (FTS5)        │
                            │ ☐ Переписывание запроса│
                            └────────────────────────┘
  ```
- The "Детали поиска" mockup the user picked:
  ```
  ▸ Детали поиска
    Запрос:      штраф за превышение на 40
    Переписан:   штраф превышение скорости 40 км/ч ст. 12.9
    Этапы: порог 0.62 · лексич. · 20→5 · 340 мс
    # было→стало  cos   lex   статус
    3→1          0.71  0.90  в ответе
    1→2          0.78  0.40  в ответе
    7→—          0.58  0.10  ниже порога
    9→—          0.64  0.05  вне top-K
  ```
- Each toggle should show its own effect when flipped on video, which is why the default is "threshold only".
- The 0.62 in the mockups is a placeholder; real values come from calibration. The Phase 13 spike saw nomic scores in a narrow 0.72-0.84 band, so calibration must be empirical.
- Report honesty carries over from Phase 14: show where a stage hurt or did nothing, including rewrite drift on the local 9B model.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` §Reranking and filtering (Day 23) — RANK-01..RANK-09; §Future Requirements and §Out of Scope (no cross-encoder, no torch, no RAGAS)
- `.planning/ROADMAP.md` §Phase 15 — goal, success criteria, research flag (empirical threshold calibration, rewrite drift on a 9B local model)

### Research
- `.planning/research/SUMMARY.md` — resolved conflicts and the overall RAG plan
- `.planning/research/ARCHITECTURE.md` — `rag.py` stage-2 `filter_and_rerank()`, `rewrite_query()`, stats shape, verdicts (`ok` / `below_threshold` / `kb_unavailable` / `off`). Note: its single mode ladder and `rerank: none|lexical|llm` are superseded by D-02
- `.planning/research/FEATURES.md` §Day 23 — two-stage shape, FTS5 + RRF, heuristic and LLM rerankers, LLM-judge
- `.planning/research/PITFALLS.md` — Pitfall 16 (threshold calibration), 17 (reranker choices, `to_thread`), 18 (rewrite drift, merge original + rewrite), evaluation theater

### Prior phases
- `.planning/phases/14-first-rag-query-day-22/14-CONTEXT.md` — D-01/D-02 header controls, D-05/D-06 sources block and metadata-only storage, D-08 `done.rag`, D-10 injection form, D-12 budget, D-13/D-14/D-17 fixture, eval script and verdicts
- `.planning/phases/14-first-rag-query-day-22/14-RESEARCH.md` — `ChatRagConfig` shape (mode as plain `str`), versioned `rag_sources` JSON, `RAG_BUDGET_RATIO`
- `.planning/phases/14-first-rag-query-day-22/14-UI-SPEC.md` — `#rag-k-wrap` anchor, source row format
- `.planning/phases/14-first-rag-query-day-22/14-PATTERNS.md` — code analogs for the RAG modules
- `.planning/phases/13-knowledge-base-indexing-day-21/13-CONTEXT.md` — D-09 per-model prefixes, D-21 FAISS layout, D-24 embedding identity guard, D-25 2000-char cap
- `.planning/phases/13-knowledge-base-indexing-day-21/13-RESEARCH.md` — spike: `/v1/embeddings` ignores `model`, nomic score range 0.72-0.84

### Project conventions
- `CLAUDE.md` — hard constraints, code conventions, SQLModel FK rule
- `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md` — to be synced after the phase
- `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/kb_search.py::search_kb` — the current single-stage vector search (FAISS top-K, cosine via normalized IP, `KbChunk` lookup). Candidate fetch builds on it; Phase 14 wraps it in `agent/rag.py` rather than rewriting it.
- `agent/embeddings.py::embed_query` — query embedding with the KB's model and prefix; used once more for the rewritten query (D-12).
- `agent/state.py::kb_index_cache` — cached FAISS index per KB.
- `KbChunk` rows (text, source, section, chunk_id) — the content source for an FTS5 table and for lexical scoring.
- Phase 14 (planned): `agent/rag.py`, `ChatRagConfig`, `Message.rag_sources`, `done.rag`, `scripts/rag_eval.py`, the frozen control-set fixture, the "Источники" `<details>` block.
- `Message.tool_trace` rendering in `ui/static/app.js` — the `<details>` pattern.
- Non-streaming LLM completion path in `agent/llm_client.py` (used by title/facts jobs) — for rewrite and LLM rerank.

### Established Patterns
- New columns on existing tables need an idempotent `ALTER TABLE`; `mode` and similar fields are plain `str` validated in Pydantic.
- CPU-bound work (lexical scoring, FAISS) runs in `asyncio.to_thread`; LLM calls are async with timeouts.
- RAG failures are fail-soft: the turn always answers (14 D-04/D-07). D-14 applies the same idea per stage.
- All UI text via `textContent`; vanilla JS, no new CDN libraries.
- E2E via Playwright on the isolated copy at ports 18000/18001, never 8000/8001.
- Frozen fixtures are drafted by Claude, approved by the user at a checkpoint, then committed before the first run.

### Integration Points
- `agent/rag.py` — stage 2 (threshold, rerank, hybrid, rewrite) and the trace/stats object.
- `GET/PUT /api/v1/chats/{id}/rag` — new fields: candidate_k, threshold (nullable), four flags.
- `agent/ws.py::_handle_chat_message` — rewrite/rerank LLM calls happen before the answer stream; `done.rag` carries the trace.
- KB indexing and delete paths (`agent/kb_indexer.py`, `agent/kb_api.py`) — FTS5 rows created with chunks and removed with the KB.
- `ui/static/index.html` header and `ui/static/app.js` — "Поиск ⚙" popover, "Детали поиска" block, grey below-threshold line.
- `scripts/rag_eval.py` — calibration mode, the 7-run matrix, per-stage skip counts; a new judge script.

</code_context>

<deferred>
## Deferred Ideas

- Code-enforced "не знаю" / templated reply when nothing passes the threshold — Phase 16 (CITE-03). D-09 stores the `below_threshold` verdict it will use.
- History-aware query rewrite (last turns + task memory) — Phase 17 (RCHAT-03).
- Separate helper model for rewrite/rerank (e.g. DeepSeek helpers with a local answer model) — not in this phase.
- Relative cut-off (`top1 − delta`) in addition to the absolute threshold — considered and not chosen; revisit if calibration shows compressed scores.
- Per-KB editable threshold column — considered and not chosen.
- Cross-encoder reranker — already in Future Requirements.

</deferred>

---

*Phase: 15-reranking-and-filtering-day-23*
*Context gathered: 2026-10-03*
