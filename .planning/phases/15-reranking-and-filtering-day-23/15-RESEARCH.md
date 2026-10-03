# Phase 15: Reranking and filtering (Day 23) - Research

**Researched:** 2026-10-03
**Domain:** two-stage RAG retrieval (threshold, lexical/LLM rerank, FTS5 hybrid, query rewrite) on top of the Phase 13/14 FAISS + SQLite stack
**Confidence:** HIGH for codebase integration and FTS5/FAISS mechanics (probed locally); MEDIUM for calibration numbers (derived from Day 22 raw outputs, not from the Day 23 calibration set); LOW-MEDIUM for 9B rewrite/rerank behaviour (not measured yet)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** The header keeps the Phase 14 controls (RAG toggle, KB select, K). A small "Поиск ⚙" button next to K opens a compact popover with: "Кандидатов" (candidate-K), "Порог" (threshold) and four independent switches — "Лексич. реранк", "LLM-реранк", "Гибрид (FTS5)", "Переписывание запроса". The button is shown only when RAG is on. This replaces the "inline next to K" placement that 14 D-02 anticipated.
- **D-02:** The four stages are independent boolean flags on the per-chat config (ROADMAP success criterion 2), not a single mode ladder and not a `none|lexical|llm` radio as the research sketch suggested.
- **D-03:** Defaults for a chat with RAG on: candidate-K 20, final K 5, threshold = the calibrated value of the KB's embedding model, all four optional stages off. So the default pipeline is "threshold only" with no extra LLM calls.
- **D-04:** "Детали поиска" is a collapsed `<details>` block in the same area as "Источники" (14 D-05). Layout: header lines (original query, rewritten query if any, active stages, threshold used, total latency), then one table of all candidates with columns: rank before → rank after, cosine, and lexical / FTS / LLM scores where the stage ran, plus a status chip per row.
- **D-05:** Status values per candidate: «в ответе», «ниже порога», «вне top-K», «не вошло в бюджет» (dropped by the 30% RAG budget, 14 D-12), and «прошло по FTS» for keyword hits exempt from the threshold (D-08). When rewrite is on, each row also shows which query found it (original / rewritten / both).
- **D-06:** The full trace is stored on the assistant message: queries, a snapshot of the config used, and every candidate's ids, scores and status — metadata only, no chunk text (same rule as 14 D-06). It goes into `Message.rag_sources` as the next version of the Phase 14 JSON and into `done.rag`, so old answers keep their details after reload and answers made with different settings can be compared in history. No candidate cap.
- **D-07:** The cut-off always applies to the raw cosine score, before any reranking. Rerankers only reorder the chunks that survived. Fused, RRF and LLM scores are never compared with the threshold. This is the same score Phase 16 will reuse for "не знаю".
- **D-08:** With hybrid on, a chunk found by FTS5 passes the cut even if its cosine is below the threshold. Its cosine is still computed and shown, and the row is marked «прошло по FTS». Without this exemption hybrid could not help on article-number questions.
- **D-09:** If the threshold cuts every candidate, the turn still goes to the LLM, with no fragment block and a short instruction that nothing relevant was found in the knowledge base. Under the answer a grey line states it with numbers, e.g. «Фрагменты не прошли порог (лучший 0.48 < 0.62)», and "Детали поиска" shows all candidates with their status. The result is stored as verdict `below_threshold` so Phase 16 only swaps the behavior. No templated reply and no LLM skip in this phase.
- **D-10:** Calibrated thresholds are code constants per embedding model (same style as the per-model prefixes in 13 D-09); unknown models default to 0 (no cut). `ChatRagConfig.threshold` is nullable: `NULL` means "use the calibrated value of the KB's model" and the popover shows it as «Порог 0.62 (калибр.)»; a number is a user override. Switching the KB therefore switches the default threshold automatically. No threshold column on `KnowledgeBase`.
- **D-11:** Query rewrite and LLM rerank use the chat's own provider/model, non-streaming, temperature 0. No separate helper-model setting in the UI.
- **D-12:** With rewrite on, both the original question and the rewritten query are embedded and searched, and the candidate lists are merged (best cosine per chunk). A drifting rewrite can add candidates but cannot lose what the original found. If the rewrite output is bad (empty, too long, multi-line, chatty), only the original is used (RANK-05 fallback). Rewrite is single-turn in this phase; history input is Phase 17.
- **D-13:** Stage order when several are on: (rewrite) → vector candidates for each query → (hybrid: FTS5 + RRF) → cosine threshold with the D-08 exemption → lexical fusion reorders all survivors → LLM rerank scores the top 10 in one batched prompt → final top-K → budget (14 D-12). LLM rerank alone also scores the top 10 by the current order. Both rerankers may be on at once (chain), they are not mutually exclusive.
- **D-14:** If an optional stage fails mid-turn (LLM rerank unparsable or timed out, bad rewrite output, FTS5 query error), the stage is skipped and the turn continues with the previous order / the original query. The skip and its reason are shown in "Детали поиска". No yellow warning and no toast. The eval script counts skips per stage.
- **D-15:** The threshold is calibrated on a separate calibration set, not on the 10 control questions: about 20 questions drafted by Claude (roughly 12 answerable, 8 out-of-corpus), approved by the user and frozen as a fixture before the first calibration run (same checkpoint pattern as 14 D-13). The 10 control questions are used only for scoring. The report shows both score distributions per embedder and how the chosen threshold behaves on the control set.
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

### Deferred Ideas (OUT OF SCOPE)
- Code-enforced "не знаю" / templated reply when nothing passes the threshold — Phase 16 (CITE-03). D-09 stores the `below_threshold` verdict it will use.
- History-aware query rewrite (last turns + task memory) — Phase 17 (RCHAT-03).
- Separate helper model for rewrite/rerank — not in this phase.
- Relative cut-off (`top1 − delta`) in addition to the absolute threshold — considered and not chosen.
- Per-KB editable threshold column — considered and not chosen.
- Cross-encoder reranker — already in Future Requirements.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RANK-01 | Two-stage retrieval, candidate-K / final-K / threshold per chat | New `ChatRagConfig` columns + partial-PUT semantics (Pitfall 1); `agent/rag.py` pipeline; `search_kb` over-fetch (Architecture) |
| RANK-02 | Lexical heuristic reranker (words + article number) fused with cosine | "Lexical reranker" pattern: prefix-5 stems, `\d+(\.\d+)+` article regex, min-max fusion; pure Python, `to_thread` |
| RANK-03 | LLM reranker, one batched prompt | "LLM reranker" pattern: top 10, 0-10 scores, tolerant line parser, `reasoning_effort: none` reuse of `titles.py` retry shape |
| RANK-04 | Hybrid FTS5 + RRF | FTS5 verified in the project's SQLite 3.50.4; trigger-synced standalone FTS table; RRF k=60; FTS-only cosine via `index.reconstruct` |
| RANK-05 | Query rewrite with fallback | Rewrite validator (rules listed), reasoning disabled, both-query merge, cosine(q, q') drift signal |
| RANK-06 | "Детали поиска" | Trace object stored in `rag_sources` v2 and `done.rag` (shape proposed below); UI-SPEC already fixes column set |
| RANK-07 | Threshold calibrated per embedding model | Calibration subcommand on a frozen calibration set; preliminary numbers from Day 22 raw outputs |
| RANK-08 | `Day23_report.md` ablation | `ablate` subcommand reusing the shared pipeline; 7-run matrix on the winning embedder (bge-m3 per Day 22 raw data) |
| RANK-09 | Optional LLM-judge (DeepSeek) | Separate `scripts/rag_judge.py`, `LLMClient(DEEPSEEK_BASE_URL, key)`, key-presence preflight (exit 2) |
</phase_requirements>

## Summary

Phase 14 is only partly executed in the repo (branch `Day21` checkout, plans 14-01..14-05 have SUMMARYs; 14-06 UI, 14-07 eval/`Day22_report.md`, 14-08 docs/E2E do not). The backend this phase extends exists and is small: `agent/rag.py` (`retrieve`, `build_rag_block`, versioned payload, `PAYLOAD_VERSION = 1`), `agent/rag_turn.py::prepare_rag_turn` (called from `agent/ws.py` before the stream; payload goes to `Message.rag_sources` and `done.rag`), `agent/rag_api.py` (GET/PUT `/api/v1/chats/{id}/rag`), `agent/kb_search.py::search_kb`, and `scripts/rag_eval.py` + `tests/fixtures/rag/control_set.json` (frozen, 10 questions). `ui/static/app.js` contains no RAG code yet, so Phase 15 UI work depends on Phase 14 plan 06 landing first (the `#rag-k-wrap` anchor and the "Источники" block do not exist). The planner must sequence Phase 14 plans 06-08 before Phase 15 UI work, or state this dependency explicitly.

No new Python packages are needed. FTS5 works in the Python/SQLite build in use (SQLite 3.50.4, `unicode61` tokenizer case-folds Cyrillic, prefix queries and triggers work, FK cascades fire the FTS delete trigger). FAISS `IndexIDMap2` supports `reconstruct(id)`, which gives the cosine of an FTS-only hit without re-embedding. The lexical reranker is hand-rolled pure Python (no `rank_bm25`, no stemmer package).

The two research-flag topics have concrete, data-backed answers. Calibration: the Day 22 raw outputs already show bge-m3 separates cleanly (out-of-corpus top-1 0.517-0.540 vs gold-chunk scores 0.634-0.758), while nomic does not separate at all (out-of-corpus top-1 0.756 and 0.772 sit inside the answerable range 0.757-0.839). Rewrite drift: detect with a deterministic validator plus the cosine between the original and rewritten query embeddings (free, because both are embedded anyway under D-12), and rely on the D-12 merge so a bad rewrite can only add candidates, which the threshold then gates.

**Primary recommendation:** Build one shared async pipeline `run_retrieval_pipeline(config, ...) -> (chunks, trace)` in `agent/rag.py` (or a new `agent/rag_rank.py`) used by `prepare_rag_turn` and by `scripts/rag_eval.py` (calibrate / ablate), with trigger-synced FTS5 table, nullable-threshold config, and a v2 trace payload; calibrate on the frozen calibration set before writing the constants.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Candidate fetch, threshold, fusion, RRF | API / Backend (`agent/rag*.py`) | Database (FTS5, FAISS cache) | Needs index, DB and embedder; one code path for chat and eval |
| FTS5 index maintenance | Database / Storage (triggers in `init_db`) | API (backfill) | Triggers cover every KbChunk delete path (3 call sites + FK cascade) |
| Rewrite / LLM rerank calls | API / Backend | LLM provider | Chat's own provider via `resolve_client`; must finish before stream starts |
| Per-chat config + calibrated default | API (`rag_api.py`) | Browser (popover) | Server resolves NULL threshold; browser only displays |
| Trace storage and transport | API (`Message.rag_sources`, `done.rag`) | Browser (render) | Metadata only; same pipe as Phase 14 |
| "Детали поиска" rendering | Browser (vanilla JS, `textContent`) | — | UI-SPEC fixed; no new CDN libs |
| Calibration, ablation, judge | Scripts (`scripts/`) | Fixtures in `tests/fixtures/rag/` | Offline, scratch DB, never `app.db` |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| sqlite3 FTS5 (via aiosqlite 0.22.1 / SQLAlchemy 2.0.52) | SQLite 3.50.4 | Hybrid keyword retrieval, bm25 | Already in the stack; FTS5 availability verified locally [VERIFIED: local probe, 2026-10-03] |
| faiss-cpu | 1.15.1 (pinned) | Vector candidates; `reconstruct` for FTS-only cosine | Already pinned [VERIFIED: requirements.txt, local probe of IndexIDMap2.reconstruct] |
| numpy | >=2.0 (2.5.3 installed) | Dot products for FTS-only cosines | Already present |
| httpx via `agent.llm_client.LLMClient` | existing | Rewrite / rerank / judge calls (`complete_chat_detailed`) | Existing non-streaming path with `extra_body` [VERIFIED: agent/llm_client.py] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| stdlib `re`, `math`, `statistics` | — | Tokenizing, lexical score, percentiles in calibration | Always; no new deps |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled lexical scorer | `rank_bm25` | Extra dependency for ~20 lines; FTS5 already provides bm25 for the hybrid stage. Not recommended |
| Russian stemmer package (snowball/pymorphy) | prefix-5 truncation | Package adds weight and legitimacy-check burden; truncation is adequate for statute vocabulary. Not recommended |
| Cross-encoder reranker | — | Violates hard constraints (torch/model runtime); explicitly Future Requirements |

**Installation:** none. `requirements.txt` is unchanged.

**Version verification:** no new packages; existing pins read from `requirements.txt`; `faiss 1.15.1`, `numpy 2.5.3`, `aiosqlite 0.22.1`, `SQLAlchemy 2.0.52`, SQLite 3.50.4 confirmed via local `pip list` / `sqlite3.sqlite_version`.

## Package Legitimacy Audit

No external packages are installed in this phase. slopcheck not run (nothing to check).

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
WS user msg
   |
   v
prepare_rag_turn (rag_turn.py)  -- loads ChatRagConfig, KB; resolves threshold (NULL -> CALIBRATED[model])
   |
   v
run_retrieval_pipeline(session, kb, question, cfg, llm=(client, model))     [agent/rag.py]
   |
   |-- (rewrite ON) -- LLM call (non-stream, reasoning off, 64 tok) --> validate
   |        ok -> queries=[orig, rewritten]      bad -> skip stage, queries=[orig]   (D-14)
   |
   |-- for each query: embed_query -> FAISS search top candidate_k  (to_thread)
   |        merge by chunk id, keep best cosine + which query found it (D-12)
   |
   |-- (hybrid ON) -- FTS5 MATCH (kb_id filter, bm25) top candidate_k
   |        FTS-only hits: cosine = dot(query_vec, index.reconstruct(id))
   |        order = RRF(vector rank, fts rank)    error -> skip stage
   |
   |-- THRESHOLD on raw cosine (D-07) ; FTS hit exempt only if it also passes the keyword gate (D-08)
   |        survivors | cut ("ниже порога")
   |        survivors == [] -> verdict below_threshold (D-09)
   |
   |-- (lexical ON) -- lex score per survivor, fuse with cosine, reorder
   |-- (llm ON)     -- top 10 in ONE prompt -> parse scores -> reorder; failure -> keep order
   |
   |-- final top-K ; rest "вне top-K"
   v
build_rag_block(final, budget) (existing, 30% budget) ; overflow -> "не вошло в бюджет"
   |
   v
trace (queries, cfg snapshot, candidates[], stages[], skips[], verdict, latency_ms)
   |        -> payload v2 -> Message.rag_sources  +  done.rag  -> UI "Детали поиска"
   v
LLM answer stream (unchanged)
```

### Recommended Project Structure
```
agent/
├── rag.py            # existing; add CALIBRATED_THRESHOLDS, pipeline entry, payload v2
├── rag_rank.py       # NEW (optional split): lexical scoring, RRF, rewrite validator, rerank parser (pure functions, unit-testable)
├── rag_fts.py        # NEW (optional): FTS query builder + search + backfill helpers
├── kb_search.py      # extend: expose normalized query vector; over-fetch; cosine_for_ids()
├── rag_turn.py       # call pipeline, build v2 payload
├── rag_api.py        # new config fields, partial PUT, calibrated threshold in response
shared/
├── models.py         # ChatRagConfig: candidate_k, threshold, 4 flags
├── database.py       # migrate_add_chatragconfig_rank_columns + FTS5 DDL/triggers/backfill
scripts/
├── rag_eval.py       # add `calibrate` and `ablate` subcommands (default out eval_out/day23)
├── rag_judge.py      # NEW: DeepSeek judge column
tests/fixtures/rag/
├── calibration_set.json   # NEW frozen fixture (~20 questions)
```

### Pattern 1: Pipeline returns chunks AND a trace
**What:** One function returns the final chunk list plus a trace dict that already contains every candidate with all scores/status; `prepare_rag_turn` and the eval script both consume it. Phase 14 already follows "shared by the chat turn and the eval script" (`agent/rag.py` docstring).
**When to use:** always; prevents the chat path and the ablation diverging.

Proposed trace / payload v2 (names are Claude's discretion; keep `v`, `mode`, `kb_id`, `kb_name`, `top_k`, `sources`, `dropped`, `context_tokens`, `warning` unchanged so v1 consumers keep working):
```json
{
  "v": 2, "mode": "rag", "kb_id": 2, "kb_name": "...", "top_k": 5,
  "sources": [ ...v1 shape... ], "dropped": 0, "context_tokens": 812, "warning": null,
  "verdict": "ok",
  "search": {
    "query": "…", "rewritten": "…|null",
    "config": {"candidate_k": 20, "top_k": 5, "threshold": 0.59, "threshold_source": "calibrated|user",
               "lexical": false, "llm": false, "hybrid": false, "rewrite": false},
    "stages": ["threshold"], "skipped": [{"stage": "llm", "reason": "bad_output"}],
    "latency_ms": 340, "best_cosine": 0.71,
    "candidates": [
      {"chunk_id": "12-3", "file": "…", "section": "…", "rank_before": 3, "rank_after": 1,
       "cos": 0.7132, "lex": 0.9, "fts_rank": 2, "llm": 8.0, "found_by": "both",
       "status": "in_answer|below_threshold|outside_top_k|over_budget|fts_exempt"}
    ]
  }
}
```
`status` stored as stable English codes; the UI maps them to the Russian chips. `fts_exempt` rows that also made the final list are shown as in answer plus the FTS chip per UI-SPEC (planner: decide display; UI-SPEC lists `≈ прошло по FTS` as its own status, so store `fts_exempt` only while it applies and keep a boolean `fts_exempt` plus the final status separately to avoid losing "в ответе").

### Pattern 2: Threshold resolution
`CALIBRATED_THRESHOLDS: dict[str, float]` keyed by lower-cased substring of `kb.embedding_model` (e.g. `"bge-m3"`, `"nomic"`), looked up like `agent/embeddings.py::prefixes_for`. Function `calibrated_threshold(model_id) -> float` returns 0.0 for unknown. Effective threshold = `config.threshold if not None else calibrated_threshold(kb.embedding_model)`. The GET response must return both `threshold` (nullable user override) and `calibrated_threshold` (the KB model's constant) so the popover can render «0.59 (калибр.)» without hardcoding.

### Pattern 3: Lexical reranker (RANK-02), pure Python
- Tokenize `re.findall(r"\w+", text.lower())`; drop tokens shorter than 3 chars and a small Russian stoplist (за, на, что, как, при, или, для, это, его, они, который…); stem = first 5 chars [ASSUMED: adequate for Russian legal text; tune on calibration set].
- `token_overlap = |query_stems ∩ chunk_stems| / |query_stems|` over `section + text`.
- Article numbers: query regex `\d+(?:\.\d+)+` (also "ст. 26" plain integers following `ст`/`статья`). `article_match = 1.0` if the number equals the deepest `Статья N` in `section`/`title` (reuse `parse_article` logic from `scripts/rag_eval.py`, moved/duplicated into `agent/`), `0.5` if it only appears in text, else 0. `lex = min(1.0, 0.6*token_overlap + 0.6*article_match)` [ASSUMED weights].
- Fusion: `fused = 0.6*minmax(cos over survivors) + 0.4*lex` [ASSUMED]; min-max is needed because cosines are compressed (bge survivors span ~0.6-0.76). Stable sort, ties keep prior order. CPU work in `asyncio.to_thread` (CONTEXT pattern).

### Pattern 4: FTS5 hybrid (RANK-04)
DDL (executed in `init_db` after `create_all`, via `conn.exec_driver_sql`, all `IF NOT EXISTS`):
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunk_fts USING fts5(
  text, section, kb_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS kbchunk_fts_ai AFTER INSERT ON kbchunk BEGIN
  INSERT INTO kb_chunk_fts(rowid, text, section, kb_id) VALUES (new.id, new.text, new.section, new.kb_id); END;
CREATE TRIGGER IF NOT EXISTS kbchunk_fts_ad AFTER DELETE ON kbchunk BEGIN
  DELETE FROM kb_chunk_fts WHERE rowid = old.id; END;
-- backfill, run only when counts differ:
INSERT INTO kb_chunk_fts(rowid, text, section, kb_id)
  SELECT id, text, section, kb_id FROM kbchunk WHERE id NOT IN (SELECT rowid FROM kb_chunk_fts);
```
Verified locally: Cyrillic is case-folded, `"превышен"*` prefix works, `"12.9"` as a quoted phrase matches, an insert trigger syncs, and `DELETE FROM kb` cascading with `PRAGMA foreign_keys=ON` fires the delete trigger (FTS count went to 0) [VERIFIED: local probe]. Triggers are preferred over explicit deletes in `delete_kb`, `_fail` and `recover_orphaned_kb_jobs` (three separate `delete(KbChunk)` sites in `agent/kb_indexer.py`) and over the FK cascade, which never calls Python.
Query building: never pass the raw question to MATCH (`12.9` unquoted is a syntax error [VERIFIED]). Build from tokens: `"stem"*` for words, `"12.9"` for article numbers, joined with ` OR `; run with `WHERE kb_chunk_fts MATCH ? AND kb_id = ? ORDER BY bm25(kb_chunk_fts) LIMIT ?`. Wrap in try/except `OperationalError` -> skip stage with reason `fts_error` (D-14).
Fusion: `rrf = 1/(60 + rank_vec) + 1/(60 + rank_fts)` (k=60 is the standard RRF constant from the original paper) [ASSUMED from training; widely used].
FTS-only cosine: `vec = index.reconstruct(chunk_id)`; cosine = `dot(query_vec_normalized, vec)` (index stores normalized vectors; reconstruct returned unit-norm vectors in the probe) [VERIFIED: local probe on `eval_out/day22/.../2/index.faiss`]. Do this per query vector (original and rewritten) and take the max.

### Pattern 5: Rewrite validator (RANK-05) - research flag 2
Call: system prompt per Pitfall 18 wording ("Верни только переписанный поисковый запрос, одну строку, без пояснений. Сохрани числа, названия законов и номера статей."), temperature 0, `max_tokens` ~64-96, `extra_body={"reasoning_effort": "none"}` with the same 400/422 retry-without-field shape as `agent/titles.py::_complete_title` (qwen3.5-9b is a reasoning model; `THINK_RE` stripping exists in `scripts/rag_eval.py`, `titles.py` handles unclosed think tags). Wrap the user question in tags and tell the model it is data, as `titles.py` does (prompt injection via the question).
Reject (skip stage, reason `bad_output`) when ANY holds after cleaning (strip `<think>…</think>`, strip surrounding quotes/«», strip a leading label like «Запрос:»/«Переписанный запрос:»):
1. empty, or finish_reason `length` with empty content;
2. contains a newline after trim (multi-line / list);
3. longer than max(120 chars, 3x original) or more than ~25 words (UI-SPEC reason «пустой или слишком длинный результат»);
4. chatty markers at start: «конечно», «вот », «переписанн», «запрос:», «ответ», «я не», «как ии», ends with «?» when the original has none, or contains markdown/code fences;
5. any digit-bearing token of the original (e.g. `40`, `60`, `12.9`) is missing from the rewrite (numbers must be preserved);
6. no content-stem overlap with the original (prefix-5 stems, overlap 0) = it answered/changed topic;
7. identical to the original (not an error: treat as "no change", do not run a second search).
Drift signal for the report: cosine(embed(original), embed(rewrite)) recorded in the trace/eval raw output (both vectors exist under D-12). Record it, do not gate on it until the calibration run shows a safe floor [ASSUMED: floor to be determined empirically; for bge-m3 related paraphrases typically sit well above the 0.52-0.54 out-of-corpus level seen in Day 22].
Safety net: D-12 merge means a bad rewrite cannot remove original candidates. The residual risk is a drifted query pushing off-topic chunks above the threshold and into top-K; measure it in the report as "rewrite helped / hurt / neutral" per question (Pitfall 18).

### Pattern 6: LLM reranker (RANK-03)
One prompt, top 10 survivors (after fusion order), each as `[i] <section> — <first ~600 chars>`; instruct "оцени релевантность каждого фрагмента вопросу от 0 до 10, ответ строго по одной строке `i: оценка`". Parse with `re.findall(r"^\s*\[?(\d+)\]?\s*[:=\-]\s*(\d+(?:[.,]\d+)?)", ..., re.M)`; accept only if every index 1..N is present with 0 <= score <= 10, else skip (reason `bad_output`); `asyncio.TimeoutError` -> skip (reason `timeout`). New config `RAG_LLM_STAGE_TIMEOUT` (suggest 45 s; `settings.LLM_TIMEOUT` is 60). Sort by (score desc, prior order asc). Chunks 11+ keep their order after the scored 10. Reasoning disabled the same way as rewrite. Known limitation to state in the report: a 9B local model is a weak and position-biased listwise judge on Russian legal text (Pitfall 17) - report honestly if it hurts.
Optionally ask for LM Studio structured output (`response_format` JSON schema) - [ASSUMED: supported by LM Studio's OpenAI-compatible endpoint; not verified this session]; the line parser must remain the primary path because DeepSeek and other providers differ.

### Pattern 7: Calibration (RANK-07) - research flag 1
`scripts/rag_eval.py calibrate --kb LABEL=ID ... --fixture tests/fixtures/rag/calibration_set.json` (loader already supports `status: frozen` gating via `load_fixture`). For each question and KB: `search_kb(top_k=20)`; record
- answerable: top-1 score and the score of the best chunk matching `expected_sources` (`chunk_hits` already exists), plus ranks;
- out-of-corpus: top-1 score.
Output `eval_out/day23/calibration.json` + a markdown table per embedder (sorted scores, min/median/max per class, per-chunk percentile table). Rule for the constant: if `max(OOC top-1) < min(answerable gold score)`: `T = round(midpoint, 2)`; else choose the T maximizing Youden's J on (answerable gold score vs OOC top-1) and flag the embedder `not separable` in the report. Compute everything in a pure function (`choose_threshold(gold_scores, ooc_top1) -> (T, stats)`) with unit tests; the script prints the dict literal to paste into `CALIBRATED_THRESHOLDS`.
Preliminary evidence from `eval_out/day22/raw/rag_*_Q*.json` (control set top-5, NOT the calibration set; use only to sanity-check, never to set the constant - D-15):

| Embedder | Answerable gold/top-1 range | Out-of-corpus top-1 | Reading |
|----------|-----------------------------|---------------------|---------|
| bge-m3 | gold chunk 0.634-0.758 (all rank 1) | Q09 0.540, Q10 0.517 | separable; gap about 0.54-0.63, midpoint about 0.59 |
| nomic | top-1 0.757-0.839 (gold only in 3/8) | Q09 0.756, Q10 0.772 | not separable: OOC top-1 inside the answerable band; any cut that drops OOC drops answerable chunks |

Also visible: bge non-gold chunks of answerable questions score 0.59-0.65, so a ~0.59 cut mostly removes out-of-corpus noise, not weak neighbours; nomic hit@5 was 2/6 on direct questions vs bge 6/6, so by Day 22 retrieval data bge-m3 is the likely D-16 winner. `Day22_report.md` does not exist yet (14-07 pending), so the planner must confirm the winner from it before the ablation (checkpoint). For nomic, decide the constant by the rule above and state `not separable`; an open question below asks whether to force 0.0 instead.

### Anti-Patterns to Avoid
- **Copying a tutorial threshold (0.75):** bge-m3 here lives at 0.5-0.76; 0.75 would cut nearly everything (Pitfall 16).
- **Thresholding fused/RRF/LLM scores:** forbidden by D-07; RRF scores are rank-based and meaningless as similarity.
- **Passing raw question text to FTS5 MATCH:** syntax errors on `12.9`, `-`, quotes; also injection of operators.
- **Re-embedding for FTS-only cosine:** use `reconstruct`.
- **Blocking the event loop with FAISS/lexical scoring:** `asyncio.to_thread` as `search_kb` already does.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Keyword retrieval + ranking | Python inverted index / TF-IDF | SQLite FTS5 `bm25()` | Already available, tokenizer handles Cyrillic case |
| FTS sync on KB delete/index failure | Explicit `DELETE` in three call sites | AFTER INSERT/DELETE triggers | Covers `delete_kb`, `_fail`, `recover_orphaned_kb_jobs` and FK cascade |
| FTS-only cosine | Second embedding call | `faiss index.reconstruct(id)` + dot | Exact, no network |
| Non-streaming LLM call + reasoning disable | New HTTP code | `LLMClient.complete_chat_detailed(extra_body=...)` + the `titles.py` retry shape | Handles providers that reject `reasoning_effort` |
| Hit scoring / article parse in eval | New metric code | `chunk_hits`, `hit_at_k`, `first_hit_rank`, `score_question` in `scripts/rag_eval.py` | Already tested in `tests/test_rag_eval.py` |
| Fixture freeze gate | New gate | `load_fixture(require_frozen=True)` | Existing checkpoint pattern |

**Key insight:** every piece except the lexical scorer, validators and trace shaping already exists as a tested helper; the work is wiring plus honest measurement.

## Runtime State Inventory

Not a rename/migration phase, but there is persistent state to extend:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Existing `kbchunk` rows (eval DB has 2424 chunks per KB; app.db KBs unknown) have no FTS rows; existing `chatragconfig` rows lack the new columns | Idempotent `ALTER TABLE chatragconfig ADD COLUMN` x6 (`candidate_k INTEGER DEFAULT 20`, `threshold REAL NULL`, four `BOOLEAN DEFAULT 0`); FTS backfill statement in `init_db` (data migration, not code-only) |
| Live service config | None - verified by reading `agent/` and `shared/` (no external service holds RAG config) | None |
| OS-registered state | None | None |
| Secrets/env vars | `DEEPSEEK_API_KEY` already in `shared/config.py`; `.env` has a key line (value not inspected - may be a placeholder) | D-18 checkpoint; judge preflight exits 2 if empty |
| Build artifacts | `eval_out/day22/eval.db` (+ `-wal/-shm`, `eval_kb/`) is a scratch DB created with Phase 14 code: no FTS table until `init_db()` runs on it | Run `init_db()` (the scripts already do via `build-kbs`; `run` must also call it or the FTS backfill) before hybrid runs; keep `eval_out/` untracked or commit only the report artifacts deliberately |

## Common Pitfalls

### Pitfall 1: PUT replaces the whole config and wipes new fields
**What goes wrong:** `put_rag_config` writes `mode`, `kb_id`, `top_k` from a full body. If new fields get defaults in `RagConfigIn`, every Phase 14 UI save (toggle/KB/K change) resets threshold and the four flags to defaults.
**How to avoid:** give new fields `None` defaults and apply only those in `body.model_fields_set` (Pydantic v2); `threshold: null` explicitly sent must mean "reset to calibrated" (so it must be distinguishable from "not sent" - exactly what `model_fields_set` provides). Validate `candidate_k` 1-50 (UI-SPEC max 50), `threshold` 0-1; server clamps `candidate_k >= top_k`. Add a test: PUT with only the old fields keeps the new ones.

### Pitfall 2: FTS exemption defeats the threshold
**What goes wrong:** D-08 exempts FTS hits. An OR query over common words matches most chunks, so almost every candidate "passes by FTS" and out-of-corpus questions (Q09 "транспортный налог… автомобиль…", Q10 "ОСАГО… тариф") get fragments back, turning `below_threshold` into dead code.
**How to avoid:** build the FTS query from stopword-filtered stems + article numbers; take only the top ~10 bm25 hits; grant the exemption only when the hit ALSO passes a keyword gate (article-number match, or lexical token-overlap >= 0.5). This preserves D-08's purpose (article-number questions) while keeping the OOC path honest. Flag as an interpretation of D-08 for user confirmation (Open Question 2). Verify on the calibration set that OOC questions yield zero exempt hits.

### Pitfall 3: Rowid reuse leaves stale FTS rows
**What goes wrong:** `kbchunk.id` is a plain INTEGER PRIMARY KEY (no AUTOINCREMENT, no stale-id protection); after deleting the highest ids, SQLite may reuse them. A stale FTS row would then point at a different chunk.
**How to avoid:** triggers (above), plus `AND kb_id = ?` on every FTS query, plus an inner join check back to `kbchunk` when loading rows.

### Pitfall 4: Compressed cosines make min-max fusion unstable
**What goes wrong:** with few survivors (1-2) min-max gives 0/1 or divides by zero.
**How to avoid:** if `max-min < 1e-6` use 1.0 for all; fuse only when survivors >= 2.

### Pitfall 5: Reasoning model burns the rewrite/rerank token budget
**What goes wrong:** qwen3.5-9b spends `max_tokens` on thinking, returns empty content (`has_reasoning=True`), rewrite falls back every time, report shows "rewrite did nothing".
**How to avoid:** `reasoning_effort: none` + 400/422 retry (as `titles.py`), strip `<think>` spans, log `finish_reason`/`has_reasoning` in the skip record so the report can distinguish "model refused to be short" from "validator rejected".

### Pitfall 6: Candidate-K over-fetch vs index size and top_k cap
`faiss index.search(k)` with `k > ntotal` returns -1 ids (already filtered in `search_kb`); candidate-K up to 50 is fine. `RagConfigIn.top_k` is capped at 20; keep `candidate_k >= top_k`.

### Pitfall 7: Latency and LM Studio co-loading
Rewrite adds an LLM call before embedding; embedding model and chat model must both be loaded in LM Studio (Pitfalls file notes this as unverified). Time each stage in the trace (`latency_ms` total, optional per stage) and report it; eval timeouts must be generous for the 9B model.

### Pitfall 8: Evaluation theater
Do not tune the threshold or stage weights on the 10 control questions (D-15). Freeze the calibration fixture and record its sha256 in `run_meta.json` like the control set.

## Code Examples

### Partial PUT with nullable threshold (Pydantic v2)
```python
# Source: Pydantic v2 model_fields_set semantics; adapt to agent/rag_api.py
class RagConfigIn(BaseModel):
    mode: Literal["off", "rag"]
    kb_id: int | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=20)
    candidate_k: int | None = Field(default=None, ge=1, le=50)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    lexical: bool | None = None
    llm_rerank: bool | None = None
    hybrid: bool | None = None
    rewrite: bool | None = None

# in handler
fields = body.model_fields_set
if "threshold" in fields:          # explicit null resets to the calibrated default
    row.threshold = body.threshold
for name in ("candidate_k", "lexical", "llm_rerank", "hybrid", "rewrite"):
    if name in fields and getattr(body, name) is not None:
        setattr(row, name, getattr(body, name))
row.candidate_k = max(row.candidate_k, row.top_k)
```

### Idempotent column migration (matches `shared/database.py` style)
```python
async def migrate_add_chatragconfig_rank_columns(conn: Any) -> None:
    """Add Phase 15 columns to chatragconfig when missing (idempotent)."""
    exists = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='chatragconfig'"))
    if exists.fetchone() is None:
        return
    columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info(chatragconfig)"))).fetchall()}
    for name, ddl in (
        ("candidate_k", "INTEGER DEFAULT 20"), ("threshold", "REAL"),
        ("lexical", "BOOLEAN DEFAULT 0"), ("llm_rerank", "BOOLEAN DEFAULT 0"),
        ("hybrid", "BOOLEAN DEFAULT 0"), ("rewrite", "BOOLEAN DEFAULT 0"),
    ):
        if name not in columns:
            await conn.execute(text(f"ALTER TABLE chatragconfig ADD COLUMN {name} {ddl}"))
```
Call it in `init_db` before `create_all`; FTS DDL via `exec_driver_sql` after `create_all`. Register the same migration in `tests/test_database.py` style idempotency test.

### FTS query builder
```python
def build_fts_query(question: str) -> str | None:
    terms: list[str] = [f'"{m}"' for m in re.findall(r"\d+(?:\.\d+)+", question)]
    for tok in re.findall(r"\w+", question.lower()):
        if len(tok) >= 3 and tok not in STOPWORDS and not tok.isdigit():
            terms.append(f'"{tok[:5]}"*')
    return " OR ".join(dict.fromkeys(terms)) or None   # double quotes cannot occur: \w+ only
```

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| Single top-K cosine | Over-fetch, absolute cut on raw cosine, then rerank survivors | Matches CONTEXT D-07/D-13; threshold must be per embedder |
| Cross-encoder rerank | Heuristic + listwise small-LLM rerank under pip-only constraints | Cross-encoder is out of scope (Future Requirements) |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Prefix-5 truncation is adequate Russian stemming for the lexical scorer and FTS prefix terms | Patterns 3, 4 | Lower recall for inflected forms; tune on calibration set |
| A2 | Lexical weights 0.6/0.6 and fusion 0.6/0.4 | Pattern 3 | Reranker hurts or does nothing; report shows it, constants are tunable |
| A3 | RRF k=60 | Pattern 4 | Minor ordering change |
| A4 | Rewrite validator rules (lengths, markers, number preservation) catch 9B drift | Pattern 5 | Some bad rewrites pass; mitigated by D-12 merge + threshold |
| A5 | cos(q, rewrite) floor can be set empirically; no floor proposed now | Pattern 5 | None until measured |
| A6 | LM Studio supports `response_format` JSON schema | Pattern 6 | Only affects an optional path |
| A7 | bge-m3 will be the Day 22 winner (inferred from raw hit@k; `Day22_report.md` not written) | Pattern 7 | Matrix would need to run on nomic |
| A8 | `reasoning_effort: none` works for qwen3.5-9b in LM Studio (works in `titles.py` per existing code; not re-tested here) | Pitfall 5 | Rewrite empties; retry path covers rejection but not silent ignoring |

## Open Questions

1. **Phase 14 UI/report not finished.**
   - Known: 14-06 (UI), 14-07 (Day22 eval + report), 14-08 (E2E/docs) have no SUMMARY; `app.js` has no RAG code.
   - Unclear: whether Phase 15 plans run after those are executed.
   - Recommendation: planner makes Phase 15 UI plans and the ablation depend on 14-06/14-07 explicitly, or flags the sequence to the user.
2. **FTS exemption scope (Pitfall 2).** D-08 says an FTS hit passes the cut; research recommends gating the exemption with a keyword check so out-of-corpus questions are not flooded. Confirm with the user or implement behind a documented constant.
3. **Nomic threshold when not separable.** Day 22 data suggests no threshold separates nomic. Recommendation: still report distributions (RANK-07), store the max-J value, mark `not separable` in the report; alternative is 0.0 (no cut). User choice at the calibration checkpoint.
4. **Judge model name.** `DEEPSEEK_BASE_URL` is `https://api.deepseek.com`; the model id (`deepseek-chat`) should be read from the seeded provider rather than hardcoded [ASSUMED]. `.env` key value is unverified.
5. **Candidate cap in payload.** D-06 says no cap; at candidate-K 50 with two queries and FTS the list is at most ~100 rows (~20 KB). Acceptable; no action.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python / SQLite FTS5 | hybrid | yes | SQLite 3.50.4 | — |
| faiss-cpu, numpy | search, reconstruct | yes | 1.15.1 / 2.5.3 | — |
| LM Studio (chat model + bge-m3 + nomic loaded) | eval runs, rewrite/rerank | not probed (runtime, user machine) | — | tests use respx/fake embedder (`tests/kb_helpers.py`) |
| DeepSeek API key | judge (RANK-09) | `.env` contains a `DEEPSEEK_API_KEY` line, value not checked | — | judge script exits 2; D-18 checkpoint |
| `C:\Projects\RAG\*.pdf` corpus | `build-kbs` | not re-probed; Day 22 scratch KBs already exist in `eval_out/day22/eval.db` | — | reuse existing scratch KBs (ids 1 nomic, 2 bge) |

## Validation Architecture

Skipped: `workflow.nyquist_validation` is `false` in `.planning/config.json`. For the planner's convenience, existing test conventions: `pytest` + `pytest-asyncio` auto mode, `tests/kb_helpers.py` (`install_fake_embedder`, `seed_kb`, `vector_for`), existing files to extend: `tests/test_rag.py`, `test_rag_turn.py`, `test_rag_api.py`, `test_rag_ws.py`, `test_rag_eval.py`, `test_database.py`, `test_kb_lifecycle.py` (FTS cleanup on delete), `test_static_js_syntax.py`. New pure-function tests: lexical scorer, RRF, rewrite validator, rerank parser, `choose_threshold`, FTS query builder. Add the `docs/TESTING_GUIDE.md` scenarios when tests are added (project rule).

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2/V3 Auth/Session | existing | Existing cookie auth; new fields ride the same routes (`get_current_user`, `_get_owned_chat`) |
| V4 Access Control | yes | FTS query must filter `kb_id`, and KB ownership is checked in `_load_kb` (foreign KB = missing); do not add endpoints returning chunk text from the trace |
| V5 Input Validation | yes | Pydantic bounds on candidate_k/threshold; FTS query built from quoted tokens only; rewrite/rerank outputs validated before use |
| V6 Cryptography | no | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| FTS5 operator/syntax injection via question | Tampering | Token-quoting builder, bound parameter, catch `OperationalError` -> skip stage |
| Prompt injection through question or chunk text into rewrite/rerank prompt | Tampering | Delimit as data (`titles.py` tag pattern, `_neutralize` for `===`), parse strictly, never execute output |
| XSS via query/rewrite/section strings in "Детали поиска" | Tampering | `textContent` only, `createElement` table (UI-SPEC) |
| Judge/eval leaking API key | Info disclosure | Never log or write the key; `.env` only |
| Cross-user chunk access via ids in trace | Info disclosure | Trace stores metadata only; snippet route already checks KB ownership |

## Sources

### Primary (HIGH confidence)
- Local codebase read: `agent/rag.py`, `agent/rag_turn.py`, `agent/rag_api.py`, `agent/kb_search.py`, `agent/kb_indexer.py`, `agent/embeddings.py`, `agent/ws.py`, `agent/titles.py`, `agent/llm_client.py`, `shared/models.py`, `shared/database.py`, `shared/config.py`, `scripts/rag_eval.py`, `tests/conftest.py`, `tests/fixtures/rag/control_set.json`
- Local probes (2026-10-03): SQLite 3.50.4 FTS5 with `unicode61` on Cyrillic, prefix queries, quoted phrases, unquoted `12.9` error, trigger sync and FK-cascade delete; faiss `IndexIDMap2.reconstruct` returning unit-norm vectors
- `eval_out/day22/raw/*.json`, `retrieval.md`, `run_meta.json` - per-chunk scores used for preliminary distributions

### Secondary (MEDIUM confidence)
- `.planning/research/PITFALLS.md` (Pitfalls 16-18) and CONTEXT/UI-SPEC for Phase 15

### Tertiary (LOW confidence)
- Training knowledge: RRF k=60, Russian prefix stemming adequacy, LM Studio `response_format` support, 9B rewrite/rerank behaviour (no live measurement this session)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new dependencies; every capability probed locally
- Architecture: HIGH - integration points read directly; payload shape is a proposal
- Calibration numbers: MEDIUM - from Day 22 raw outputs (10 questions, top-5 only), to be superseded by the calibration set
- Rewrite/rerank behaviour: LOW-MEDIUM - validator rules reasoned from Pitfall 18, not yet measured on the 9B model
- Pitfalls: HIGH for 1-4 (code-verified), MEDIUM for 5-7

**Research date:** 2026-10-03
**Valid until:** 2026-11-02 (stable stack; calibration constants only valid until the corpus, chunking or embedder changes)
