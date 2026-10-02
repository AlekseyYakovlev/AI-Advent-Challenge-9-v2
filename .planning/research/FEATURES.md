# Feature Landscape: RAG Knowledge Base (Milestone v3.0, Week 5, Days 21-25)

**Domain:** Local RAG over user-uploaded documents inside an existing chat agent (course project, each day graded by demo)
**Researched:** 2026-10-03
**Confidence:** MEDIUM. This rests on well-established RAG practice (retrieve, rerank or filter, cite, abstain, condense follow-up queries, RAGAS-style evaluation). I did not run Context7 or web verification in this pass, so treat library-specific claims as unverified. The app-specific dependency claims come from PROJECT.md and CLAUDE.md and are HIGH confidence.

## How good implementations work (short model)

Pipeline: **ingest** (load, split, embed, store) -> **retrieve** (embed query, top-K by cosine) -> **post-filter** (threshold, optional rerank, dedupe) -> **generate** (a prompt with numbered context blocks plus rules) -> **cite** (the model references block ids; the server validates and renders them) -> **abstain** (if nothing passes the threshold, skip generation or force "не знаю").

Expected behaviours per area:

| Area | Expected behaviour in good implementations |
|------|--------------------------------------------|
| KB management | Create, list, delete KBs. Indexing runs as a background job with visible status (queued, indexing N/M chunks, ready, failed + readable error). Per-file errors do not kill the whole job. Deleting a KB removes its vectors and rows. Changing the embedding model or chunking means a re-index (create a new KB or rebuild), never a silent mix of vectors from different models. |
| KB selection per chat | The chat holds a set of attached KBs (a multi-select or checkboxes). Retrieval searches only those. If no KB is attached, the chat is plain. This is the standard in NotebookLM, Open WebUI, and AnythingLLM workspaces. |
| RAG toggle | Exposed per chat (a persistent setting next to the strategy and model controls). A per-message override is a nice extra but not required. The Day 22 comparison needs a cheap way to run the same question in both modes, so a per-chat toggle plus a "regenerate in the other mode" action covers it. |
| Sources display | Each answer shows a collapsible "Источники" block. Each item has a number `[1]`, the source file, the section, the chunk_id, a similarity score, and an expandable quote or chunk text. Inline `[n]` markers in the answer text map to these items. |
| Quotes | Verbatim fragments copied from the retrieved chunk. Good systems verify programmatically that each quote is a substring of the cited chunk, after whitespace and case normalisation. Fabricated quotes get dropped or flagged. |
| "Не знаю" | Gated first by retrieval, not by the model's goodwill. If the best score is below the threshold (or zero chunks survive the filter), skip generation or inject a strict instruction. The answer is "не знаю" plus a clarifying question, and optionally the nearest topics found. A prompt rule backs this up as a second layer. |
| Query rewrite | Before retrieval, an LLM call condenses the last N turns plus the new question into one standalone query (this is the classic "condense question" step). It also resolves pronouns such as "а за повторное?". Optionally it expands synonyms or legal terms. Show the rewritten query in the debug view. |
| Evaluation | A small hand-written golden set (question, expected answer gist, expected sources). Retrieval is scored by hit@k (is an expected source or chunk in the top-K). Answers are scored by a manual rubric, optionally assisted by an LLM judge. Faithfulness means each claim is supported by the retrieved context. |
| Dialog task memory | Per turn, keep a small structured state: goal, clarified facts, fixed constraints and terms. Inject it into the prompt and into the rewrite step. Update it with an LLM extraction call or tool calls. |

## Table Stakes

Missing any of these means the day's acceptance criteria fail or the demo is hollow.

### Day 21: Indexing and KB management

| Feature | Why Expected | Complexity | Dependencies on existing app / notes |
|---------|--------------|------------|--------------------------------------|
| KB entity (name, user_id, embedding model, chunking params, status) | The modal collects exactly these fields. Storing the params and model per KB prevents mixed-model indexes. | Low | New SQLModel tables scoped by `user_id` (hard constraint). Follow the `Settings`/memory table conventions and FK `ondelete=CASCADE` via `sa_column`. |
| Document + chunk tables with metadata (source file, title, section, chunk_id, page, char range, text) | Day 21 requires metadata per chunk. Needed later for citations. | Low-Med | SQLite is the source of truth for text and metadata. FAISS holds only vectors, mapped by integer row id. |
| PDF loading and text extraction (pypdf or pymupdf; Cyrillic text-layer PDFs) | The test corpus is two Russian legal PDFs. | Med | Check that the PDFs have a text layer. Scanned PDFs mean OCR, which is out of scope. Large PDFs (КоАП is about 1000+ pages) mean indexing takes minutes. |
| Fixed-size chunking with size and overlap | Required UI controls. | Low | Chunk by characters or tokens. `tiktoken` is already a dependency, so token-based sizing is cheap. |
| Structural chunking (headings, sections, files) | Required UI switch. For laws, split by "Статья N" / "Глава N" / "Раздел" regex headings. This is the better strategy on this corpus. | Med | Regex heading detection for the legal corpus, with a fallback to one chunk per file or paragraph when no headings are found. Put an oversize-section guard in (sub-split sections over the limit). Section title goes into the metadata. |
| Embeddings via LM Studio `/v1/embeddings`, batched, default `giga-embeddings-instruct-480m-0826` | Required. | Med | Reuses the `LMStudioClient` / `model_switch_lock` pattern. Embedding models must be loaded in LM Studio, and `ConnectError` surfaces as "LM Studio is not running". Batch the requests (for example 16-64 chunks). Check the model's max input length. |
| Embedding model dropdown in the modal, same as the main screen | Required. | Low-Med | Reuse the provider-grouped model picker, filtered to embedding models. The LM Studio `/api/v0/models` response has a `type` field (llm / vlm / embeddings), which should allow filtering. This is unverified. |
| FAISS index per KB, persisted to disk, with L2-normalised vectors and inner product (cosine) | Required: "index saved (FAISS + SQLite)". | Med | `faiss-cpu` is a new dependency, so pin it in requirements.txt and check it installs on the target Windows Python. Store files under a data dir (for example `data/kb/{kb_id}.faiss`). Vector dim comes from the first embedding response. |
| Sidebar "База знаний" collapsible block, "Добавить" button, list with delete | Required. | Med | Vanilla JS, following the pattern of the existing "Расписание" sidebar panel. |
| "Добавить" modal (name, multi-file picker, chunking switch, conditional size/overlap fields, model dropdown, "Индексировать") | Required. | Med | Multipart upload to the Agent, so `python-multipart` is needed, which is a dependency check. Modals follow the carried-over Phase 10 rule: close only via ×. |
| Indexing as a background job with status and progress (queued, indexing x/y, ready, failed) | Indexing a 1000-page PDF blocks for minutes. A request that hangs with no feedback looks broken. | Med | `asyncio.create_task` in the Agent process (no Celery). Progress is a row field polled via REST, or pushed through the existing `/ws/events` channel used by the scheduler panel. Prefer the existing `/ws/events` push plus polling as a fallback. |
| Error handling (unsupported file, empty extraction, embedding model not loaded, LM Studio down), with a readable message in the KB row | Failure modes are guaranteed in a demo. | Low-Med | Store `status=failed` and `error` on the KB. Allow a per-file status. Don't crash the Agent (same rule as MCP-04). |
| Delete KB (rows, FAISS file, in-memory cache) | Required. | Low | Mirror `cleanup_chat_caches`: drop the loaded FAISS index from the in-process cache. Cover it with a cascade test. |
| Indexing stats (files, chunks, dim, model) shown on the KB list item | Cheap proof that indexing worked. Good for the demo video. | Low | |

### Day 22: First RAG query and report

| Feature | Why Expected | Complexity | Dependencies / notes |
|---------|--------------|------------|----------------------|
| Retrieval function: embed query with the KB's own model, top-K cosine search, hydrate chunks from SQLite | Core of the day. | Med | Must use the same embedding model as the KB. If that model is not loaded, return a readable error. |
| Context builder: numbered chunk blocks plus the question, injected into the LLM request | Core of the day. | Med | Plug into `agent/context_engine.py` / the WS flow. It must not be persisted into the message tree as the user message (store the original question; keep retrieved chunks as message metadata or a side table). Count the extra tokens against `context_length` and the existing 75% compression trigger. |
| Attach KBs to a chat (multi-select) | Chat must pick which KB(s) to use. | Low-Med | New `chat_kb` link table, or fields in per-chat settings. Recommend a link table. Settings has the global vs per-chat fallback pattern, which may be reusable for a default RAG on/off. |
| RAG on/off toggle per chat | "Agent with two modes." | Low | A chat-level setting. Show a clear UI indicator (a badge on the answer showing RAG vs no RAG). |
| Same-question comparison workflow (ask both modes, side by side or in sequence) | The report needs with/without comparisons. | Low-Med | Minimum: toggle plus re-ask. Better: a script that runs the 10 questions in both modes and dumps the outputs to markdown. |
| 10 control questions with expectation and expected sources | Required deliverable. | Low (writing) | Store as a JSON or YAML fixture (for example `eval/questions.json`) so Days 23 and 24 reuse the same set. Include 2-3 questions that are not in the corpus (for the "не знаю" check on Day 24) and 1-2 that need the right article number. |
| `Day22_report.md` | Required deliverable. | Low | Table of question, expectation, no-RAG answer, RAG answer, sources hit, verdict, plus a conclusion. |

### Day 23: Filtering, reranking, query rewrite

| Feature | Why Expected | Complexity | Dependencies / notes |
|---------|--------------|------------|----------------------|
| Retrieve a wide top-K (for example 20), then a similarity threshold cut-off, then final top-N (for example 5) | Required. The classic two-stage shape. | Low | Config: `top_k_before`, `threshold`, `top_k_after`. Store in the Settings model (global with per-chat override via the existing fallback) or in per-KB/per-chat RAG config. Expose in the UI as number inputs. |
| A cut-off by similarity score; calibrate on the corpus | Required. Raw cosine ranges differ per embedding model (for example 0.2-0.5 are typical for relevant matches with some models), so a default of 0.75 is wrong in general. | Low-Med | Calibrate by looking at the score distributions of the 10 questions (in-corpus vs out-of-corpus). Document the chosen value in the report. |
| Query rewrite step (LLM call that makes a retrieval-friendly or standalone query) | Required by the day. | Med | One extra non-streaming LLM call, reusing `llm_client`. The call is skippable (toggle) so the comparison "without vs with" works. Use a low temperature. Log the original and rewritten query. |
| Mode comparison: baseline vs filter vs filter + rewrite | Required by the report. | Low-Med | Same eval fixture and runner as Day 22, with a config switch. Metrics: hit@k plus manual answer quality. |
| `Day23_report.md` | Required deliverable. | Low | |
| Visible debug info: retrieved candidates with scores, which were cut, the rewritten query | Needed to prove the filter works. | Low-Med | A collapsible "Детали поиска" block under the answer, or a log. |

### Day 24: Citations and "не знаю"

| Feature | Why Expected | Complexity | Dependencies / notes |
|---------|--------------|------------|----------------------|
| Structured answer: answer text plus sources (source, section, chunk_id) plus quotes | Required. | Med | Two workable patterns. (a) **Prompt-based with inline markers**: ask the model to cite `[1]`, `[2]` and to output a JSON block or a fixed "Источники:" section. (b) **Server-built sources**: the server knows what was retrieved and renders the sources panel itself, while the model supplies only the quotes. Recommend a hybrid: the model writes the answer with `[n]` markers plus quotes keyed by `[n]` (a JSON footer); the server validates the markers and quotes against the retrieved chunks and renders the UI. Local 9B models are unreliable at strict JSON, so parse defensively and fall back to server-built sources (source plus chunk_id from the top chunks). |
| Programmatic quote verification (substring match, whitespace-normalised) | "Quotes present and meaning matches" is the check. A cheap server-side check catches fabricated quotes. | Low-Med | Mark each quote verified or not. Unverified quotes are dropped or shown with a warning. |
| "Не знаю" gate: below-threshold relevance means "не знаю" plus a clarification question | Required rule. | Low-Med | Retrieval-level gate: no chunk at or above the threshold means skip generation (or send a restrictive prompt) and return a templated "не знаю" with a clarification question. A prompt-level rule is a second layer. Reuses the Day 23 threshold. Keep the gate deterministic so it is testable. |
| Sources rendered in the chat UI (collapsible block, file, section, chunk_id, quote) | Required. | Med | Vanilla JS, DOMPurify on any rendered text. Persist as message metadata so reloading a chat still shows sources. Needs a column on `Message` or a side table `message_sources`. A message-tree-compatible design: the sources belong to the assistant message id, so branches and regeneration stay correct. |
| Check on 10 questions (sources present, quotes present, meaning matches) | Required. | Low-Med | Reuse the fixture. Add a results table with columns: sources present Y/N, quotes present Y/N, quote supports answer Y/N, expected source hit Y/N. Include out-of-corpus questions that must yield "не знаю". |
| Report or section in a doc (Day 24 has no named report, so add `Day24_report.md` or a results section) | Evidence for the grader. | Low | |

### Day 25: Mini-chat with RAG and task memory

| Feature | Why Expected | Complexity | Dependencies / notes |
|---------|--------------|------------|----------------------|
| Existing chat doubles as the mini-chat: history kept, retrieval on every turn, sources always shown | The app already is a chat with history. Do NOT build a second mini-chat. | Low | RAG on per chat means retrieval on every user turn. |
| Query rewrite uses history (condense follow-ups) | "а какой штраф за это?" retrieves nothing without it. This is the main failure mode in multi-turn RAG. | Med | Feed the last 2-4 turns plus the task state into the rewrite call. Already built on Day 23, so Day 25 adds the history input. |
| Task state for the dialog (goal, clarified facts, fixed constraints and terms) | Required. | Med | See the reuse analysis below. |
| Task state injected into the prompt every turn, and updated after each turn | Required for "doesn't lose the goal". | Med | Use the existing memory-injection path. Update through LLM tool calls (the existing pattern) or a small post-turn extraction call. |
| UI to view the task state | Inspectability is the project's core value. | Low-Med | Reuse the existing working-memory / task panel. |
| Two long scenarios (10-15 messages each), scripted and replayable | Required verification. | Med | A scenario file (list of user messages) plus a runner that records answers, sources, and the task state after each turn. Include goal drift traps (a topic switch and a return) and a term the user fixes early ("под штрафом имею в виду административный"). |
| Scenario result log or report | Evidence for the grader. | Low | `Day25_report.md`: per-turn sources present, goal kept, constraints respected. |

## Reuse of the existing working memory and task FSM for RAG dialogs

| Need | Reuse | Recommendation |
|------|-------|----------------|
| Dialog goal | Task record with state (`planning -> execution -> validation -> done`) | Treat "the user's research goal in this RAG dialog" as the active task's title/description. When the LLM already auto-creates tasks via tool call, a RAG dialog gets a task naturally. Do not add RAG-specific FSM states. The FSM is for task lifecycle, not for a dialog. |
| Clarified facts | Working memory rows tied to the `task_id` | Store as working-memory entries with a category (`clarified`). |
| Fixed constraints and terms | Per-chat invariants (INV-02) or working memory with category `constraint`/`term` | Invariants already inject into every request and have conflict checks. Use working memory for facts and terms, and per-chat invariants for hard constraints ("отвечай только по КоАП"). |
| Injection every turn | Existing memory/invariant injection into context | Add a compact "RAG dialog state" block (goal, facts, constraints, terms) that the rewrite step also consumes. |
| Write path | LLM tool calls (MEM-03) | Keep the explicit tool-call approach for consistency. Add a fallback: a deterministic post-turn extraction call if the local model forgets to call the tool (the ledger above notes qwen3.5-9b is weak at tool use). |

Net: Day 25 is mostly wiring (rewrite input, injection, a display panel) and an evaluation scenario runner, not new storage. The risk is the weaker local LLM not calling the memory tools reliably.

## Differentiators

Not required, but they raise quality or the demo value. Pick one or two at most.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Hybrid retrieval (BM25 plus vectors) via SQLite FTS5, merged with reciprocal rank fusion | Legal texts have exact terms and article numbers ("ст. 12.9") that embeddings handle poorly. This is probably the largest real quality gain on this corpus. | Med | FTS5 is built into SQLite, so no new dependency. It also gives a baseline to compare against. Best candidate differentiator. |
| Heuristic reranker (keyword overlap, article-number match boost) | Cheap second stage that satisfies "reranker or heuristic" on Day 23. | Low | A good fit, since Day 23 explicitly allows a heuristic. |
| LLM-as-reranker (score each candidate 0-10 with the local LLM) | Real reranker behaviour without a new model. | Med | N extra LLM calls, which is slow on a local 9B. Limit it to top-10 and batch the scores in one prompt. |
| Cross-encoder reranker model (bge-reranker via LM Studio or sentence-transformers) | Highest-quality reranking. | High | Needs a model plus runtime. LM Studio support for rerank endpoints is uncertain (LOW confidence). Sentence-transformers pulls in torch, which is heavy. Avoid. |
| LLM-judge for the evaluation reports (faithfulness and relevance, 1-5 or yes/no), shown next to the manual verdict | Faster than eyeballing, and an interesting column in the report. | Med | Use DeepSeek as the judge, not the local model. Always add a manual verdict column, because judges are noisy. Keep it a script, not a UI feature. |
| Per-message RAG override (a small toggle by the input box) | Convenient for live A/B in the demo. | Low-Med | Only if the per-chat toggle feels clumsy. |
| Eval runner script (`scripts/rag_eval.py`) that runs the question fixture in all modes and writes the markdown report tables | Makes the Day 22/23/24 reports reproducible and cheap to regenerate. | Med | Strongly recommended, since three days need the same loop. This is closer to table stakes for efficiency than a true differentiator. |
| Retrieval debug panel (scores, cut candidates, rewritten query) | Great for the demo and for tuning. | Low-Med | Overlaps with the Day 23 table-stakes item. Make it an expandable block. |
| Click-through from a source to the full chunk with neighbouring chunks | Helps verify citations. | Low-Med | |
| Clarifying-question suggestions in "не знаю" (nearest topics found) | Better UX than a bare refusal. | Low | Cheap when the retrieval results exist (show the top section titles under the threshold). |
| Re-index action for a KB (same files, new params) | Natural lifecycle, since params are fixed per KB. | Med | Needs the original files kept on disk. If files are stored, re-index is easy. Otherwise "delete and add again" is acceptable. |
| Duplicate-chunk / neighbour merge in the final context | Cleaner context. | Low-Med | |
| Streaming sources event (sources sent over WS before the tokens) | Snappy UX. | Low-Med | One extra WS message type, such as `rag_sources`, ahead of the `done` message. |

## Anti-Features (over-engineering for a course)

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|--------------------|
| External vector DB (Chroma, Qdrant, Milvus, pgvector), or any service | Violates local-first and "no extra services". The week plan names FAISS plus SQLite. | `faiss-cpu` plus SQLite metadata. |
| LangChain / LlamaIndex as a framework | Heavy dependency footprint, hides the pipeline the course wants to see, and conflicts with the existing hand-written client style. | Hand-write loader, chunker, retriever (about 300 lines total). |
| Celery / Redis / job queue for indexing | Hard-constraint violation. | `asyncio.create_task` plus a status field. |
| OCR for scanned PDFs, DOCX/HTML/web crawling, image/table understanding | Scope blow-up. The test corpus is text PDFs. | Support PDF, TXT, MD only. Fail with a clear message for empty extraction. |
| Incremental or delta re-indexing, file watching, document versioning | Not needed. KB params are fixed per KB. | Delete and add again (or a simple full re-index). |
| A KB shared across users, ACLs on KBs | Out of scope (per-user by design). | Scope everything by `user_id`. |
| A second, separate mini-chat UI or CLI for Day 25 | The existing chat already has history and RAG on per chat. The task offers "CLI/web" as an option. | Use the existing chat. |
| New FSM states for RAG dialogs | The FSM governs task lifecycle. | Reuse tasks plus working memory. |
| Fine-tuning embeddings, custom embedding training, multi-vector or ColBERT, HyDE plus multi-query fan-out plus agentic retrieval loops | Disproportionate complexity. | Single-query rewrite. |
| Cross-encoder reranker requiring torch | Large install on Windows, slow, fragile. | Threshold plus a heuristic or LLM-based rerank. |
| Full RAGAS / TruLens / DeepEval integration | Heavy, expects OpenAI-style judges, and pulls many dependencies. | A 10-row table with a manual rubric, plus an optional small LLM-judge script. |
| Streaming-token citation parsing (parsing `[n]` live in the token stream) | Fragile. | Render sources after `done`, or send a sources event before the tokens. |
| Auto-chunk-size tuning, adaptive chunking via LLM | Over-engineering. | Offer two strategies, as the UI specifies. Compare them once in a report. |
| Using the LLM as the sole guard for "не знаю" | Local models ignore refusals. | A deterministic retrieval-score gate first, with a prompt rule as the backup. |
| Storing retrieved context as persisted user-message text | Pollutes the message tree, the compression strategies, and token stats, and breaks regenerate/branch. | Keep the original question as the message content, and the sources and chunks as metadata. Inject chunks only into the outbound request. |

## Evaluation approach (recommended)

- **Golden set**: 10 questions in one fixture file, reused on Days 22, 23, and 24. Each has `expected_answer_gist`, `expected_sources` (file plus article/section), and a `type` flag (factual, article-number lookup, multi-chunk, out-of-corpus). Include at least 2 out-of-corpus questions (the "не знаю" test). Suggested split: 6 answerable direct, 2 needing synthesis across chunks, 2 unanswerable.
- **Retrieval metric**: hit@k (is any expected chunk or section in the top-K), computed automatically by matching expected source/section strings against retrieved metadata. Optionally add MRR. With 10 questions this is a sanity check, not a statistic. Say so in the report.
- **Answer metric**: a manual rubric with 3 levels (correct / partially correct / wrong or hallucinated), plus "sources cited correctly Y/N" and "quote supports answer Y/N". This is what graders expect and is enough. An optional LLM-judge column for faithfulness is a differentiator. Do not replace the manual verdict.
- **Comparison matrix** per report: no-RAG vs RAG (Day 22), RAG vs filtered vs filtered+rewrite (Day 23), citation checks (Day 24).
- **Abstention metrics (Day 24)**: for unanswerable questions, "не знаю" rate (should be 100%), and for answerable ones, false-refusal rate (should be near 0). The threshold is a trade-off between these two, and the report should show that.

## Feature Dependencies

```
KB tables + PDF loader + chunkers + embedder + FAISS store   (Day 21 core)
  -> Background job + status + error handling  -> KB sidebar/modal UI
  -> Retrieval function (Day 22)
       -> Chat<->KB attachment + RAG toggle + context builder
       -> Eval fixture (10 questions) + eval runner (reused Days 22/23/24)
       -> Threshold + top-K before/after + query rewrite (Day 23)
            -> "Не знаю" gate (Day 24, reuses the threshold)
            -> Citations: message_sources metadata + sources UI + quote verification (Day 24)
                 -> History-aware rewrite + task-state injection (Day 25)
                      -> Long scenarios + report (Day 25)
Existing: user_id auth, LM Studio client, model picker, Settings fallback,
          working memory + task FSM + invariants, /ws/events, sidebar panels
```

Critical ordering notes:
- The message-to-sources metadata design must be decided in Day 22 or Day 21, even though it is used on Day 24, because Day 22 already has to show what was retrieved. Retrofitting storage later forces a migration. (The project uses ad-hoc migrations in `shared/database.py`.)
- The eval fixture and runner should be created on Day 22 and reused after.
- The threshold in Day 23 is a prerequisite for the Day 24 "не знаю".

## MVP Recommendation

Prioritise (in order):
1. Day 21 core pipeline plus the sidebar and modal, with background indexing, status, and errors. Structural chunking tuned for "Статья N".
2. Day 22 retrieval, chat-level KB attachment, per-chat RAG toggle, the eval fixture and runner, and the report.
3. Day 23 threshold and top-K before/after, query rewrite, and the heuristic rerank (or just the threshold plus a heuristic). Report.
4. Day 24 citations with hybrid model-plus-server validation, quote verification, the deterministic "не знаю" gate, and the sources UI persisted on the message.
5. Day 25 history-aware rewrite, task-state injection through existing working memory, scripted long scenarios, and the report.

One differentiator worth the cost: **FTS5 hybrid retrieval with RRF** (legal article numbers), or only the cheaper article-number boost heuristic if time is short.

Defer: cross-encoder reranker, LLM-judge UI, per-message override, re-index action (use delete and re-add), source click-through with neighbouring chunks.

Also carried over: Phase 10 (modals close only via ×) applies directly to the new KB modal. Phase 11 (edit and delete long-term memory in UI) is independent of RAG, so keep it as a separate phase to avoid blocking the RAG days.

## Complexity and risk summary

| Item | Complexity | Main risk |
|------|------------|-----------|
| PDF extraction of Russian legal PDFs | Med | Layout noise (headers, footers, page numbers) breaks chunk and heading detection. Strip repeated headers. |
| Indexing time for КоАП | Med | Minutes of embedding on a local 480M model. Needs a progress display, batching, and cancellation or at least a non-blocking job. |
| Embedding model availability in LM Studio | Med | The model must be loaded, and the `model_switch_lock` interacts with a loaded chat LLM (memory). Surface errors clearly. |
| Threshold calibration | Low-Med | Scores differ per model, and a wrong default makes everything "не знаю" or nothing. Calibrate with the fixture. |
| Citation format from a small local LLM | Med | Unreliable JSON. Use server-side validation and fallback sources. |
| Task-state tool calls from a local LLM | Med | The known weakness of qwen3.5-9b with tools. Add a deterministic fallback extraction. |
| Context size growth | Low-Med | Retrieved chunks eat the window and can trigger the 75% compression. Cap the chunk count and size. |

## Sources

- PROJECT.md and CLAUDE.md in this repo (HIGH for app constraints and existing capabilities).
- Established RAG practice: condense-question query rewriting, two-stage retrieve-then-rerank, retrieval-gated abstention, hit@k/MRR and faithfulness-style evaluation (MEDIUM, general domain knowledge, not re-verified this session).
- Unverified items to check in phase research: LM Studio `/api/v0/models` `type` field for embedding models; `faiss-cpu` wheel availability for the target Python on Windows; whether LM Studio exposes any rerank endpoint; the `/v1/embeddings` input-length limit of `giga-embeddings-instruct-480m-0826` (and whether it expects an instruction prefix for queries, as "instruct" embedding models often do).
