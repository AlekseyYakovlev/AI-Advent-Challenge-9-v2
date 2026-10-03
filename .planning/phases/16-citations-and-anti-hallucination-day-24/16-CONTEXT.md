# Phase 16: Citations and anti-hallucination (Day 24) - Context

**Gathered:** 2026-10-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Every RAG answer carries verifiable evidence: the answer text, the list of sources (file + section / chunk_id, rendered from chunk metadata) and quotes taken from the retrieved chunks. Each quote is checked server-side against the chunk it cites and marked. When no fragment survives the relevance threshold, code (not the prompt) returns "не знаю" plus a clarifying question. `Day24_report.md` records, per control question, sources present, quotes present, meaning matches quotes, and correct "не знаю" on out-of-corpus questions. Requirements: CITE-01..CITE-04. Branch: `Day24`.

Out of this phase: history-aware retrieval and task memory (Phase 17), new retrieval stages or threshold changes (Phase 15 owns them), cross-encoder rerankers (Future Requirements).

Phase 15 is still executing (6 of 13 plans) at the time of this discussion. The decisions below build on its planned shape: `agent/rag_pipeline.py` verdicts and trace, `ChatRagConfig` search flags, the "Поиск ⚙" popover, `rag_sources` payload v2, "Детали поиска", `scripts/rag_judge.py`, `Day23_report.md`.

</domain>

<decisions>
## Implementation Decisions

### Where quotes come from
- **D-01:** Quotes are written by the answering model, inline, in the same streamed call. In strict mode the instruction asks the model to end its answer with a fixed "Цитаты:" section, one line per quote in the form `[N] «…»`, where `[N]` is the fragment label from the injected block (14 D-10). No second LLM call for quotes.
- **D-02:** After the stream ends, the server parses the "Цитаты:" tail, cuts it out of the answer text, and stores the clean answer as `Message.content`. Quotes are rendered only as a structured block built from `done.rag`. While streaming, the raw tail is visible for a moment and is replaced on `done`. No streaming filter is added for it. History replayed to the LLM therefore carries no quote text.
- **D-03:** The prompt asks for 1-3 quotes of at most about 300 characters each (one or two sentences). Longer quotes are still verified and shown truncated with "показать полностью".
- **D-04:** Fallback when the model's quotes fail: if an answer ends with zero verified quotes, code picks the best-overlap sentence from each cited chunk (or from the top chunks when the answer cites nothing) and shows it marked «подобрана автоматически». CITE-01 then holds whatever the model does. Model quotes and auto quotes are stored and counted separately. Auto quotes are not added to `model_idk` or gated answers (D-13, D-15).
- **D-05:** Verification is two-step. Step 1, normalized exact match: casefold, collapse whitespace, unify quote marks and dashes, `ё`→`е`, remove hyphen line-breaks, trim outer punctuation; a quote with "…" inside is split into parts that must all be present in order. Step 2, if step 1 fails: accept when a sliding-window `difflib` similarity against the chunk is ≥ 0.9. This gives three states: «подтверждена» (exact), «почти дословно» (fuzzy), «не подтверждена». The state is stored per quote and the report shows the three counts separately.

### Unverified quotes and bad references
- **D-06:** A quote that fails both checks against its cited chunk is then checked against the other fragments sent in this turn. If it matches one, it is re-attached to that source and noted «источник исправлен». Only if nothing matches is it shown with the red «не подтверждена» chip. Unverified quotes are never dropped silently and never fail the turn.
- **D-07:** A quote line with an out-of-range `[N]` (e.g. `[7]` with 5 fragments) is first tried against all fragments (D-06). If nothing matches, it is rejected: shown as «не подтверждена (нет такого источника)» with no source link. Out-of-range `[N]` markers in the answer text are left as written. The number of invalid references is stored in the payload and shown as a grey note under the answer.
- **D-08:** Quotes get their own block «Цитаты (N)» directly under the answer text, expanded by default. Row layout: status chip · «цитата» · `[N]` file → section. «Источники (N)» and «Детали поиска» stay collapsed below it, so answer, quotes and sources are all visible without a click.
- **D-09:** «Источники» keeps listing every fragment that was sent to the model (14 D-05/D-06). Fragments referenced by a valid `[N]` in the answer text or by a quote get a «цитируется» mark and are listed first. Source rows are always built from chunk metadata, never from model text.

### "Не знаю" gate
- **D-10:** In strict mode, verdict `below_threshold` (no fragment survived, 15 D-09) means the answer LLM is **not called**. Code returns a templated reply: a fixed "Не знаю…" sentence plus a code-built clarifying question. This replaces the `merge_no_fragments_note` / `NO_FRAGMENTS_INSTRUCTION` branch in `agent/rag_turn.py` for strict mode. The reply is persisted as a normal assistant message with its `rag_sources` trace, so "Детали поиска" and the grey below-threshold line still work.
- **D-11:** The clarifying question lists the 2-3 nearest sections found under the threshold, taken from the trace candidates, for example: «Ближайшие темы в базе: КоАП → Статья 12.9; ФЗ-196 → Статья 19. Уточните, к какой из них относится вопрос, или переформулируйте его.» No LLM call is used to write it.
- **D-12:** The gate fires only when zero fragments survive, which is exactly the existing `below_threshold` verdict. A chunk that passed through the FTS5 exemption (15 D-08) is a survivor, so the LLM answers with it. There is no second threshold and no separate "best cosine among survivors" rule.
- **D-13:** When fragments passed but lack the answer, the strict prompt tells the model to reply "Не знаю" plus a clarifying question. Code detects that reply (starts with the marker, no quotes) and stores verdict `model_idk`; it is shown with the same grey "не знаю" styling as the gated reply and gets no auto quotes.
- **D-14:** An answer that has neither a valid `[N]` nor any verified model quote is kept as written and gets an amber line «ответ не подтверждён фрагментами» under it. Answers are never replaced by the template after generation; only the retrieval gate (D-10) is a hard stop.

### Strict mode and the Day 24 check
- **D-15:** Quotes and the gate are controlled by one per-chat switch «Строгий режим (цитаты + «не знаю»)» in the "Поиск ⚙" popover (15 D-01), a new boolean on `ChatRagConfig`, **on by default** for RAG chats. With the switch off the turn behaves exactly as in Phase 15 (soft instruction, LLM still called on `below_threshold`, no quote block), which keeps the Day 22 / Day 23 eval runs reproducible.
- **D-16:** The check lives in a separate `Day24_report.md` at the repo root, next to `Day22_report.md` and `Day23_report.md`, generated by `scripts/rag_eval.py`. One table with the four CITE-04 columns per control question, plus abstention metrics ("не знаю" rate on out-of-corpus questions, false-refusal rate on answerable ones) and counts of model quotes by state vs auto quotes.
- **D-17:** The Day 24 run uses the configuration that won in `Day23_report.md` (embedder + stages), answering with the local `qwen/qwen3.5-9b` at temperature 0, strict mode on. One extra comparison row runs the same configuration with strict mode off, to show the difference on the 2 out-of-corpus questions.
- **D-18:** "Sources present", "quotes present" and "correct не знаю" are computed automatically. "Meaning matches quotes" is a manual verdict (да / частично / нет with a one-line reason) filled by Claude and reviewed by the user at a checkpoint before the report is committed (same as 14 D-17). In addition, `scripts/rag_judge.py` gets a faithfulness rubric and adds an optional DeepSeek judge column; it needs the real `DEEPSEEK_API_KEY` and uses the same pause-for-key checkpoint as 15 D-18. The manual verdict stays primary.

### Claude's Discretion
- Exact strict-prompt wording, the tail marker rules (what counts as the start of "Цитаты:", tolerated variants), and the "Не знаю" marker detection for `model_idk`.
- The fuzzy matcher details beyond the 0.9 bar (window size, how multi-part quotes are scored) and the sentence splitter and overlap score for auto quotes (reuse `agent/rag_rank.py` stems).
- `rag_sources` payload v3 shape and `done.rag` field names for quotes, quote states, invalid-ref count and the new verdicts; the idempotent migration for the new `ChatRagConfig` column.
- How the templated reply is delivered over the WebSocket (single frame vs token frames), wording of the fixed "Не знаю" sentence, and what the clarifying question says when there are no candidates at all.
- How strict mode interacts with tool-call rounds in the chat turn, and with the per-answer mode label (14 D-03).
- How many nearest sections to list when candidates repeat the same section, chip colors and styling, truncation length in the quote block.
- Fixture and raw-output locations for the Day 24 run; the judge rubric text.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` §Citations and anti-hallucination (Day 24) — CITE-01..CITE-04; §Out of Scope (no torch, no separate mini-chat UI)
- `.planning/ROADMAP.md` §Phase 16 — goal, success criteria, research flag (local-model compliance with `[n]` citations and verbatim quotes)

### Research
- `.planning/research/SUMMARY.md` — Phase 16 outline: deterministic gate, `[n]` labels, quote verification in `done.rag`, check table with abstention / false-refusal rates
- `.planning/research/PITFALLS.md` — Pitfall 6 (small local LLMs ignore cite / "не знаю" instructions), Pitfall 7 (hallucinated chunk ids, paraphrased quotes, normalization rules), evaluation theater, XSS in the source panel
- `.planning/research/FEATURES.md` §Day 24 — structured answer patterns, quote verification, gate, abstention metrics; anti-feature: parsing `[n]` live in the token stream

### Prior phases
- `.planning/phases/15-reranking-and-filtering-day-23/15-CONTEXT.md` — D-01 "Поиск ⚙" popover, D-06 trace storage, D-07 raw-cosine threshold, D-08 FTS exemption, D-09 `below_threshold` verdict, D-10 calibrated thresholds, D-18 DeepSeek key checkpoint
- `.planning/phases/15-reranking-and-filtering-day-23/15-RESEARCH.md` and `15-UI-SPEC.md` — payload v2 shape, popover and "Детали поиска" layout
- `.planning/phases/14-first-rag-query-day-22/14-CONTEXT.md` — D-03 per-answer mode label, D-05/D-06 sources block and metadata-only storage, D-08 `done.rag`, D-10 `[N]` injection form, D-11 soft instruction, D-13/D-14/D-17 control set, eval script, manual verdicts
- `.planning/phases/14-first-rag-query-day-22/14-UI-SPEC.md` — source row format
- `.planning/phases/13-knowledge-base-indexing-day-21/13-CONTEXT.md` — D-25 2000-char chunk cap, chunk metadata (source, section, chunk_id)

### Eval inputs
- `tests/fixtures/rag/control_set.json` — the frozen 10 control questions (incl. 2 out-of-corpus)
- `Day22_report.md`, `Day23_report.md` (produced by Phase 15) — the winning configuration for D-17

### Project conventions
- `CLAUDE.md` — hard constraints, code conventions
- `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md` — to be synced after the phase
- `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/rag.py` — `RAG_INSTRUCTION` (today asks only for `[N]`), `NO_FRAGMENTS_INSTRUCTION`, `render_rag_block`, `build_rag_payload` / `serialize_rag_payload` / `parse_rag_payload` with `PAYLOAD_VERSION = 2`, `sources_from_chunks`.
- `agent/rag_turn.py::prepare_rag_turn` — already branches on `VERDICT_BELOW_THRESHOLD`; that branch is where the strict-mode gate goes.
- `agent/rag_pipeline.py` — verdicts (`ok`, `below_threshold`), per-candidate statuses and the trace with every candidate's section and scores, which D-11 uses for "nearest sections".
- `agent/rag_rank.py` — `tokenize`, `stems`, `clean_llm_text`, `_normalise_ws`: building blocks for quote normalization and auto-quote overlap.
- `agent/rag_api.py` — `RagConfigIn` / `RagConfigOut`, `_apply_search_settings`, `get_chunk_snippet` (chunk text by id for quote verification and "показать полностью").
- `ui/static/app.js` — `buildRagMeta`, the «Источники (N)» and «Детали поиска» `<details>` builders, the grey below-threshold line, `renderRagSearchPopover`.
- `scripts/rag_eval.py`, `scripts/rag_judge.py`, `scripts/e2e_rag_playwright.py`.

### Established Patterns
- RAG is fail-soft: a failed check marks, it never breaks the turn (14 D-04/D-07, 15 D-14).
- Retrieved text never enters the message tree; `Message.rag_sources` stores metadata and references. Quote text is model output plus a chunk reference, so the planner must decide what of it is stored (the quote string itself is needed to render history).
- New columns on existing tables need an idempotent `ALTER TABLE`; flags are plain values validated in Pydantic.
- All UI text via `textContent`; vanilla JS, no new CDN libraries.
- CPU-bound work in `asyncio.to_thread`.
- Frozen fixtures and manual verdicts go through a user checkpoint; E2E via Playwright on the isolated copy at ports 18000/18001, never 8000/8001.

### Integration Points
- `agent/ws.py::_handle_chat_message` — post-stream step: parse and strip the "Цитаты:" tail, verify, build the payload, then persist and send `done.rag`; and the short-circuit path that sends the templated reply without calling the LLM.
- `agent/rag_turn.py` / `agent/rag.py` — strict instruction, gate, payload v3.
- `shared/models.py::ChatRagConfig` + `GET/PUT /api/v1/chats/{id}/rag` — the strict-mode flag.
- `ui/static/index.html` / `ui/static/app.js` — the fifth popover switch, the «Цитаты (N)» block, chips, «цитируется» marks, amber and grey lines.
- `scripts/rag_eval.py` — Day 24 mode and `Day24_report.md`; `scripts/rag_judge.py` — faithfulness rubric.

</code_context>

<specifics>
## Specific Ideas

- Chip wording chosen in discussion: «подтверждена», «почти дословно», «не подтверждена», «не подтверждена (нет такого источника)», «подобрана автоматически», «источник исправлен», «цитируется»; amber line «ответ не подтверждён фрагментами».
- Quote line format the model is asked for: `[N] «…»` under a "Цитаты:" heading.
- Clarifying question example: «Ближайшие темы в базе: КоАП → Статья 12.9; ФЗ-196 → Статья 19. Уточните, к какой из них относится вопрос, или переформулируйте его.»
- The strict-mode switch exists so the before/after can be flipped on video, in the same spirit as the Phase 15 toggles.
- Report honesty carries over: show the real compliance of the 9B model (model quotes vs auto quotes, fuzzy vs exact), and false refusals on answerable questions.
- The user chose fuzzy tolerance over pure exact matching, and re-attaching a misattributed quote over a plain red chip. Both add states that the UI and the report must show explicitly rather than fold into "verified".

</specifics>

<deferred>
## Deferred Ideas

- A second LLM call that extracts quotes as JSON, and a retry call when no quote verifies — considered and not chosen (D-01, D-04).
- A streaming filter that hides the "Цитаты:" tail while tokens arrive — considered and not chosen (D-02).
- Replacing an unsupported answer with the templated "не знаю" after generation — considered and not chosen (D-14).
- An LLM-written clarifying question — considered and not chosen (D-11).
- Running the Day 24 check on DeepSeek as the answering model to compare quote compliance with the local model — not in this phase; only the judge column uses DeepSeek.
- Click-through from a source to the full chunk with neighbouring chunks (research differentiator) — not discussed, backlog candidate.

</deferred>

---

*Phase: 16-citations-and-anti-hallucination-day-24*
*Context gathered: 2026-10-03*
