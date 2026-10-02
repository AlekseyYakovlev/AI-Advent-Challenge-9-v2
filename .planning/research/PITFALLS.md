# Domain Pitfalls

**Domain:** Adding a local RAG knowledge base (FAISS + SQLite, LM Studio embeddings, Russian legal PDFs) to an existing two-process FastAPI chat app with small local LLMs
**Milestone:** v3.0 Week 5: RAG (Days 21-25)
**Researched:** 2026-10-03
**Overall confidence:** MEDIUM. Based on project code and constraints (HIGH for integration points) plus established RAG/FAISS/PDF engineering knowledge (MEDIUM). No live verification was done of giga-embeddings-instruct-480m-0826 prefix conventions or LM Studio embedding-type behavior. Those are flagged LOW and need a 30-minute spike in Day 21.

Phase key: D21 = indexing, D22 = first RAG query, D23 = rerank/threshold/rewrite, D24 = citations + "не знаю", D25 = mini-chat + task memory. "Cross" means it must be designed in D21 even if it bites later.

---

## Critical Pitfalls

Mistakes that cause rewrites or invalidate the reports.

### Pitfall 1: Instruct embedding model used without query/passage asymmetry
**What goes wrong:** Instruct-style embedders (the "instruct" in giga-embeddings-instruct) are trained so that queries carry a task instruction (e.g. `Instruct: <task>\nQuery: <text>`) while passages are embedded raw. Embedding both sides the same way gives mediocre but plausible-looking retrieval, so the bug hides.
**Why it happens:** The LM Studio `/v1/embeddings` call has no query/passage parameter; the prefix must be added by our code. Docs are on the HF model card, not in LM Studio.
**Consequences:** Low recall; scores compressed into a narrow band, which makes the Day 23 threshold impossible to calibrate; reports show RAG "barely helps".
**Prevention:** Put the embedding call behind one `Embedder` class with two methods, `embed_passages(list)` and `embed_query(str)`; the prefix lives only there. Read the model card for the exact instruction format (LOW confidence on the 480m variant; verify). Store `embedding_model`, `prefix_scheme_version` and `dim` in the KB row. Nomic-embed, if used as fallback, needs `search_document: ` / `search_query: ` prefixes, so the abstraction must be per-model config.
**Detection:** The same question phrased as a near-verbatim статья title does not return that статья in top-3; the top-1 vs top-10 score gap is under about 0.05.
**Phase:** D21 (design the interface), D22 (verify with a retrieval smoke test).

### Pitfall 2: Cosine/inner-product confusion (unnormalized vectors in IndexFlatIP)
**What goes wrong:** `IndexFlatIP` computes raw dot product. If vectors are not L2-normalized, "scores" depend on vector length and the threshold is meaningless; if `IndexFlatL2` is used, lower is better and the threshold comparison is inverted.
**Why it happens:** LM Studio may or may not return normalized vectors depending on the model; the code assumes.
**Consequences:** Scores above 1.0 or negative; threshold filters the wrong direction; D23/D24 "не знаю" fires always or never.
**Prevention:** Always `faiss.normalize_L2` on both indexed and query vectors (float32, C-contiguous) and use `IndexFlatIP`. Document the score as cosine in [-1, 1]. Assert in a unit test that `abs(norm - 1) < 1e-3` for stored vectors.
**Detection:** Any score > 1.0001; scores for unrelated query still > 0.8.
**Phase:** D21.

### Pitfall 3: FAISS row index <-> SQLite chunk id drift
**What goes wrong:** FAISS returns positional row numbers (flat index) while metadata is in SQLite with autoincrement ids. After a delete, a re-index, or a crash between "add to FAISS" and "commit to SQLite", position N points to the wrong chunk. Answers cite wrong sources with high confidence.
**Why it happens:** `IndexFlat*` does not support `remove_ids` with stable ids, and `IndexFlat` has no custom ids at all unless wrapped in `IndexIDMap2`.
**Consequences:** Silent wrong citations (worst failure in D24). Per-entry delete in the UI (a stated requirement) corrupts the mapping.
**Prevention:** Wrap with `IndexIDMap2` and use the SQLite `chunk.id` (int64) as the FAISS id. Better and simpler for this scale (a few tens of thousands of chunks): treat the FAISS file as a derived cache. Store embeddings as BLOBs (float32) in SQLite, rebuild the in-memory index per KB from SQLite on load or after any delete; persist the `.faiss` file only as optimization with a stored `chunk_count` + `model` + `dim` check, and rebuild if the check fails. Write order: SQLite is source of truth, commit first, then update the index; startup reconciliation compares counts.
**Detection:** `index.ntotal != SELECT count(*) FROM chunk WHERE kb_id=?`; retrieved chunk text lacks the query's key terms for exact-title queries.
**Phase:** D21 (cross: D24 citations depend on it).

### Pitfall 4: Mixing embedding models/dimensions across KBs (and within one KB)
**What goes wrong:** User switches the default embedding model (or LM Studio JIT loads a different one) and adds a document to an existing KB. Vectors from two spaces coexist; or `faiss.add` raises on dimension mismatch mid-batch leaving a half-indexed document.
**Prevention:** KB row stores `embedding_model` and `dim`; the model is immutable after the first document (UI: "to change model, create a new KB / reindex"). Query always embeds with the KB's model, not the current default. Indexing a document is all-or-nothing: embed everything first, then write SQLite + index in one transaction; status column (`indexing/ready/failed`) shown in the UI.
**Detection:** Dimension error in logs; `ready` documents with 0 chunks.
**Phase:** D21.

### Pitfall 5: Blocking the asyncio event loop (PDF parse, chunking, embeddings, FAISS)
**What goes wrong:** PDF extraction of the КоАП (very large, probably 1000+ pages) is CPU-bound for tens of seconds; sync `requests`/`openai` embedding calls and `faiss.search` run in the Agent's single event loop. WebSocket streaming for every user stalls, the supervisor's `/health` ping (3s interval) times out, and the supervisor **kills and restarts the Agent mid-indexing**.
**Why it happens:** The project has a hard rule against `multiprocessing`/`os.fork`, so people reach for inline calls.
**Consequences:** Indexing "randomly" restarts the app; half-written index; the user sees a hung upload.
**Prevention:** Run parsing/chunking in `asyncio.to_thread` (or `loop.run_in_executor` with a `ThreadPoolExecutor`; PyMuPDF and FAISS release the GIL for most work). Embeddings via the existing async `httpx` client in batches with `await` between batches. Indexing as a background `asyncio.create_task` job with a status row and progress (reuse the Scheduler's pattern: status in SQLite, UI polls or listens on `/ws/events`); the upload endpoint returns 202 immediately. Do not use `subprocess` Python workers other than `asyncio.create_subprocess_exec` (allowed).
**Detection:** `/health` latency spikes during upload; supervisor `agent_restart` log lines; WebSocket tokens freeze.
**Phase:** D21 (cross: D22 `faiss.search` is fast, fine inline; the reranker in D23 may be CPU-bound, also use `to_thread`).

### Pitfall 6: Small local LLMs ignore "cite sources" / "say не знаю" instructions
**What goes wrong:** qwen3.5-9b / gemma-3n answer from parametric memory when retrieval is weak, skip citations, or write plausible article numbers. Prompt-only compliance is maybe 60-80% and degrades with long contexts.
**Prevention:** Do not rely on the model for the "не знаю" decision. D24 gate is **code-level**: if best score < threshold (or zero chunks after filter), skip the LLM and return a templated "не знаю + clarifying question" (optionally one cheap LLM call only to produce the clarification). Sources are **attached by the server from the retrieved chunk metadata** (a deterministic "Источники" block appended to the response), not generated by the model. For quotes, ask the model for structured output (JSON: `answer`, `quotes[{chunk_id, quote}]`) and validate (see Pitfall 7). Keep the prompt short, in Russian, with the instruction both at the start of the system prompt and repeated after the context ("Отвечай только по фрагментам выше"). Lower temperature (0-0.2) for RAG turns. Qwen3 "thinking" output (`<think>`) must be stripped before parsing.
**Detection:** Answers to out-of-corpus control questions (e.g. "рецепт борща") are not "не знаю"; answers contain "ст. 12.9" that is not in the retrieved chunks.
**Phase:** D24 (design the gate in D22 so the retrieval result object carries `score`, `chunk_id`, `source`, `section`).

### Pitfall 7: Hallucinated chunk_ids and fabricated or paraphrased quotes
**What goes wrong:** Model cites `chunk_id 57` that was not in context, or "quotes" a paraphrase. A reviewer clicks the source and the quote is not there.
**Prevention:** Give the model short, per-request labels ([1], [2], ...) rather than DB ids; map back server-side. Post-validate every quote: normalize (casefold, collapse whitespace, unify quotes/dashes, `ё`->`е`, strip hyphen line-breaks) then require `norm(quote) in norm(chunk.text)`; optionally fuzzy fallback (`difflib` ratio >= 0.9) but mark it "approximate". Drop or repair failed quotes (replace with a server-extracted sentence from the best chunk by lexical overlap). Reject citations to labels not in the context. Log validation failure rate; report it in Day 24.
**Detection:** Validation failure counter > 0; citations whose label is outside the 1..K range.
**Phase:** D24 (labels designed in D22 prompt builder).

### Pitfall 8: RAG context blows the window / fights the existing compression strategies
**What goes wrong:** K chunks (statute articles can be 500-1500 tokens each) + history + memory/invariants/task layers exceed `context_length` (small local models often 4-8k). With `no_compression` it deletes the user message and sends `CONTEXT_OVERFLOW` (known behavior in CONCERNS.md). Or retrieved chunks are saved as messages and then re-summarized/sliding-windowed on later turns, compounding.
**Why it happens:** The context engine was built for messages only; token counting uses tiktoken `cl100k_base`, which undercounts Cyrillic for Qwen/Gemma tokenizers (Russian is about 2-3x more tokens per word than English; divergence already noted in CONCERNS.md).
**Prevention:** Treat retrieved context as an **ephemeral, per-turn system-side block**, never persisted as a message, so strategies never compress or re-summarize it. Give it an explicit token budget (for example at most 40% of `context_length`) enforced by dropping lowest-ranked chunks, counted with a safety multiplier (e.g. x1.5 for Russian) or the model's real tokenizer if LM Studio exposes it. Budget order: system+invariants+profile+task memory -> RAG block -> history (strategy applies only to history). Make `compute_chat_stats` include the RAG block so the UI usage % is honest. Decide and document behavior for `no_compression` overflow (prefer: shrink RAG block first, not delete user message). Store per-turn retrieval record (chunk ids + scores) in a side table keyed by the assistant message, so history is reproducible without bloating context.
**Detection:** `CONTEXT_OVERFLOW` right after enabling RAG; LM Studio "context length exceeded"/truncated prompts; the model answers ignoring the top chunk (truncated from the front).
**Phase:** D22 (budget), D25 (history + task memory interplay).

### Pitfall 9: Evaluation theater in the reports (cherry-picking, judging by eye)
**What goes wrong:** Day 22/23/24 reports pick questions after seeing results, judge "looks right" by eye, compare with/without RAG on different phrasing, or change thresholds while looking at the same 10 questions (overfitting) and then claim improvement.
**Prevention:** Freeze the 10-question set **before** running any mode: for each question record the expected answer gist, expected source (закон + статья/часть), and whether it is answerable from the corpus (include 2-3 deliberately out-of-corpus and 2 near-miss questions for D24). Machine-checkable metrics where possible: hit@K of expected статья (substring check on chunk text or metadata), rank of first correct chunk, quote validity rate, "не знаю" precision on out-of-corpus. Run all modes with the same set, same temperature/seed, and store raw outputs (JSON) in the repo; report links to them. For the threshold: report the score distributions (correct-chunk vs wrong-chunk) and pick the cut by that, then note it was tuned on the same 10 questions (say so; add 5 held-out questions if cheap). Report negative results (cases where RAG made it worse). Small LLM sampling noise: run each question 2-3 times or fix temperature 0.
**Detection:** No failures listed in any report; thresholds chosen "by feel".
**Phase:** D22 (set + harness), D23, D24, D25 reuse.

---

## Moderate Pitfalls

### Pitfall 10: PDF extraction artifacts in Russian legal texts
**What goes wrong:**
- Hyphenation at line ends ("правонару-\nшение") splits words, damages embeddings and breaks quote matching.
- Running headers/footers and page numbers ("Страница 123", "КонсультантПлюс", "www.consultant.ru", "Документ предоставлен КонсультантПлюс", date stamps) get injected mid-sentence in chunks and mid-статья.
- Two-column or footnote layouts interleave text; ligatures/odd Unicode (soft hyphen U+00AD, NBSP, `ё` variants) break substring matching.
- Scanned (image-only) PDFs yield empty text with no error.
- Amendment markers "(в ред. Федерального закона от 01.01.2020 N 1-ФЗ)", "(часть первая в ред. ...)", "(п. 3 введен ...)", "Статья 12.9 утратила силу" inflate chunks with noise and may outrank real content.
**Prevention:** Use PyMuPDF (`pymupdf`) for speed on the large КоАП; extract per page with `sort=True`; detect repeating header/footer lines by frequency across pages (lines appearing on > 30% of pages, or in top/bottom 7% of the page bbox) and strip them; regex-remove known КонсультантПлюс strings and standalone page numbers. De-hyphenate only when line ends with a letter + `-` and next line starts lowercase (do not merge "кто-\nлибо" type legit dashes blindly; accept small error). Normalize: NFKC, remove U+00AD, NBSP->space, collapse whitespace, keep paragraph breaks. Keep amendment annotations out of the **embedded** text but keep them in stored text (or strip with regex `\((в ред\.|введен|введена|п\. .* в ред\.)[^)]*\)`, with the nesting risk noted); simplest: strip them from embedding input, retain in the displayed quote source. Fail loudly if extracted chars per page < threshold (scanned PDF -> "OCR not supported" message in the UI). Write a golden-file test: first 3 pages of a fixture PDF -> expected cleaned text.
**Detection:** Chunks containing "Страница", "КонсультантПлюс", dangling "-" at end, text under 50 chars per page; regex count of header strings in the cleaned corpus should be 0.
**Phase:** D21.

### Pitfall 11: Chunking ignores statute structure
**What goes wrong:** Fixed-size chunks cut a статья mid-часть; the "Статья 12.9. Превышение установленной скорости движения" heading ends up in one chunk and the penalty (`часть 2`) in another, so the answer chunk has no article context. Splitting on the regex `Статья \d+` fails for КоАП numbering like `12.9`, `1.3.1`, `14.1.2`, "Статья 28.1.1", and for "Глава 12" / "Раздел II". The "Примечание" and "Примечания" (notes after the статья defining terms) get separated or glued to the next статья. References like "см. статью 12.8" are not headings but match naive regexes (false boundaries mid-paragraph).
**Prevention:** Structure-first, size-second: split by regex anchored to line start `^Статья\s+\d+(\.\d+)*\.` (also `^Глава\s+`, `^Раздел\s+`), keep the heading as `section` metadata (Глава -> Статья -> Часть). Then, if a статья exceeds the max chunk size, split by `^\d+\.` numbered parts (carefully: КоАП numbered parts are "1.", "2." at line start) and only then by sentence with overlap; **prepend the breadcrumb** ("КоАП РФ > Глава 12 > Статья 12.9. Превышение ...") to each chunk's embedding text so every part is retrievable by article name. Merge tiny chunks (e.g. < 200 chars like "Статья N. Утратила силу.") into neighbors or drop them with a flag. Offer both strategies in the UI as the assignment requires (fixed vs structure) and use the same corpus for a comparison in the Day 21/22 notes.
**Detection:** Histogram of chunk sizes (many < 100 chars or > 4000); `section` empty for > 5% of chunks; sample query "штраф за превышение скорости на 20-40 км/ч" returns a chunk without the статья number.
**Phase:** D21.

### Pitfall 12: Chunk-size parameter validation and unit confusion
**What goes wrong:** `overlap >= chunk_size` causes an infinite loop or zero progress in the splitter (`start += size - overlap`). Sizes in characters vs tokens are mixed: UI says "500", embedder's context is in tokens, and Russian text is about 2.5-3.5 chars/token for many tokenizers (so 500 "tokens" is not 500 chars). Negative/zero values, overlap > 50%, huge chunks silently truncated by the embedding model.
**Prevention:** Validate on API and in the modal: `chunk_size >= 100`, `0 <= overlap < chunk_size`, `overlap <= chunk_size/2` (warn), upper bound under embedder context (see Pitfall 14). Choose **characters** as the unit (simple, deterministic, no tokenizer dependency), label it as such, and set the default so worst-case token count fits the embedder (e.g. 1000 chars ~ 350-450 tokens). Splitter must guarantee `step = size - overlap > 0` and terminate; property-test it on random strings (every char covered, no empty chunks, max length respected). Split at word/sentence boundaries, not mid-word.
**Detection:** Indexing job never finishes; chunk count is absurdly high (overlap near size).
**Phase:** D21.

### Pitfall 13: LM Studio embedding model loading, JIT, and "type llm" misclassification
**What goes wrong:** (a) giga-embeddings-instruct-480m is listed as type `llm`, nomic-embed as `embeddings`; code that filters the model list by `type == "embeddings"` hides the default model, or code that lets the user pick any model puts a chat LLM in the embeddings slot (LOW confidence on the exact cause: likely the GGUF/architecture metadata; verify by calling `/v1/embeddings` directly). (b) The existing `model_switch_lock` / `_current_loaded_model` tracks **one** loaded chat model; loading the embedder may unload the chat model or the embedder gets unloaded when the user switches the chat model, producing a slow first request or a 404 "model not loaded". (c) Embedding call with a model that is not loaded relies on JIT loading, which can take tens of seconds and exceed `LLM_TIMEOUT` (60s). (d) VRAM contention: 9B chat model + embedder on a laptop GPU.
**Prevention:** Do not filter by type; use a configured embedding model id (setting, default giga) plus a "Test embedding" button that does a 1-string `/v1/embeddings` call and reports dim. Keep embedding load logic **separate** from the chat-model `_current_loaded_model` bookkeeping (do not route through `model_switch_lock` in a way that makes chat requests wait on indexing; or document that they serialize). Use a longer timeout for the first embedding call (separate from chat timeout), retry once on "model not loaded", and surface `ConnectError` as "LM Studio isn't running" as already done for chat. Keep embedder resident (TTL) and prefer CPU-offload or small `gpu_offload` if VRAM tight. Never emergency-unload the embedder in the chat path.
**Detection:** First upload after switching chat model takes > 60s or 404s; model list lacks the default embedder; chat gets slower after indexing.
**Phase:** D21.

### Pitfall 14: Embedding batch size and input length limits
**What goes wrong:** Sending 2000 chunks in one request or one chunk per request: the first hits LM Studio request-size/timeouts or OOM; the second takes forever (thousands of round trips). Inputs longer than the embedder's max sequence length are silently truncated (the tail of a long статья is not represented), or LM Studio returns an error for the whole batch. Empty strings cause a 400 for the entire batch.
**Prevention:** Batch 16-64 chunks per request (tune in the spike), sequential with progress updates (or at most 2 concurrent). Know the model's max tokens (check the model card; get from LM Studio model info; LOW confidence on the number for giga-480m) and keep chunk size under it with a margin. Filter empty/whitespace chunks. On a batch failure retry with batch size halved, then per-item, and record the failing chunk. Log dim and count mismatch (`len(data) != len(batch)`); sort results by `index` field in the response, do not assume order.
**Detection:** Indexing of КоАП takes hours or fails around the same chunk number; response count != request count.
**Phase:** D21.

### Pitfall 15: FAISS on Windows / Python 3.13 / Cyrillic paths / threads
**What goes wrong:** `faiss-cpu` wheels availability on Python 3.13 Windows may lag (verify before locking the stack; no Conda allowed in this project's pip-only rule). `faiss.write_index`/`read_index` take a C string path and fail or mis-encode non-ASCII paths on Windows (the corpus lives at `C:\Projects\RAG`, and a user home may be `Aleksey`, but KB names may be Cyrillic). Concurrent `add` and `search` from different threads/tasks is not safe; search during a rebuild returns garbage or crashes the Agent. Index file writes interrupted by the supervisor killing the process leave a corrupt file.
**Prevention:** Pin `faiss-cpu` after confirming a cp313 win_amd64 wheel exists (check PyPI; fallback is a pure NumPy cosine search, which at under 50k chunks x 768 dims is milliseconds; consider it the **default** and make FAISS a thin swap-in, which also satisfies the Day 21 "FAISS" requirement if the wheel works). Name index files by ASCII ids (`kb_{id}.faiss`), never by user-provided names; resolve paths with `pathlib` under a configured data dir; for robustness serialize with `faiss.serialize_index` to bytes and write bytes via Python (`Path.write_bytes` to a temp file then `os.replace`), and read via `faiss.deserialize_index(np.frombuffer(...))`. Guard each KB's index with an `asyncio.Lock` (or build a new index object and atomically swap the reference: copy-on-write). Single-process (Agent) only owns the index; the UI process must never open it.
**Detection:** `RuntimeError: could not open ... for reading`; crash during simultaneous upload and chat; zero-byte `.faiss` after a restart.
**Phase:** D21 (stack spike first).

### Pitfall 16: Similarity threshold calibrated on the wrong thing
**What goes wrong:** A threshold like 0.75 copied from tutorials; scores are model-specific (instruct embedders and Russian text produce different distributions; some models cluster all scores 0.6-0.9), so the threshold is either always passed or always failed. Top-K filtering "before/after" in the Day 23 report is meaningless if scores are uncalibrated. Thresholding on a reranker score (different scale) with the embedding threshold.
**Prevention:** Make the threshold a per-KB/per-model setting with a calibration helper (script/endpoint): run the frozen question set (answerable + unanswerable), print the score of the best chunk for each, pick the threshold between the two distributions, store with the model id. Use relative cut-offs as well (drop chunks below `top1 - delta`) to be robust. Different thresholds for retrieval stage (loose) and "не знаю" stage (strict). Log score of every retrieved chunk.
**Detection:** After the filter, always K chunks or always 0; in-corpus questions fall below the threshold.
**Phase:** D23 (calibrate), D24 (strict "не знаю" gate).

### Pitfall 17: Reranker choices that don't fit the constraints
**What goes wrong:** Adding a cross-encoder (sentence-transformers + torch) violates the spirit of the lightweight pip-only stack, pulls a gigabyte of dependencies, and blocks the loop; or using the small chat LLM as a listwise reranker is slow (K LLM calls) and unreliable at 9B for Russian legal text.
**Prevention:** Prefer cheap rerankers: (1) over-fetch (K=20-30), then rerank with a heuristic (BM25-style lexical overlap with article number/keywords via `rank_bm25` or hand-rolled, plus exact "ст. N" boosting) fused with the vector score (RRF); (2) optionally LM Studio reranker if one is loadable. Run CPU-bound rerank in `to_thread`. Always report K before/after and latency.
**Detection:** Rerank adds > 5s per query; install pulls torch.
**Phase:** D23.

### Pitfall 18: Query rewrite drifts the intent
**What goes wrong:** Small LLM rewrite changes meaning ("штраф за непристёгнутый ремень" -> "правила безопасности пассажиров"), drops numbers/статья references, answers the question instead of rewriting, or adds a chatty preamble that is then embedded. In D25 the rewrite also needs history ("а если повторно?") and may inject earlier facts wrongly.
**Prevention:** Strict prompt ("Верни только переписанный поисковый запрос, одну строку, без пояснений. Сохрани числа, названия законов и номера статей."), temperature 0, max tokens ~64, post-process (first line, strip quotes/`<think>`), length sanity check (empty, > 3x original -> fall back to the original), and **retrieve with both** original and rewritten queries then merge (union + max score). Never show the rewrite as the user's question; show it in a debug panel. Report per question whether the rewrite helped, hurt, or was neutral. In D25 use history-aware condensation only on the last 2-4 turns.
**Detection:** Rewrite pass rate lower than baseline on named-article questions; rewrites containing newline or "Конечно".
**Phase:** D23, D25.

### Pitfall 19: Large uploads and request handling
**What goes wrong:** A КоАП PDF (tens of MB) uploaded via `UploadFile` is read entirely into memory (`await file.read()`), the proxy/UI process between browser and Agent (the UI at :8000 and Agent at :8001) has body limits or times out, or the browser posts to the wrong origin and loses the session cookie. Python-multipart temp files accumulate. Filename path traversal (`../../`) and Cyrillic filenames on Windows. Duplicate uploads index the same document twice. Auth: KB must be scoped by `user_id` (project constraint) and the upload endpoint must check the cookie like other routes.
**Prevention:** Stream to a temp file in chunks (`while chunk := await file.read(1 MB)`), enforce a max size (configurable, e.g. 100 MB) and extension/content-type allowlist (pdf, txt, md), store under a server-generated UUID name, keep the original name only as metadata (sanitized), dedupe by SHA-256 per KB, delete the temp file in `finally`. Return 202 + job id. Upload directly to the Agent origin the same way existing REST calls do. Every KB/document/chunk query filters by `user_id` (a test must prove user B cannot see/search user A's KB; search must filter in SQL after retrieval, or use per-KB indexes so cross-user leakage is structurally impossible).
**Detection:** Memory spike of the Agent on upload; 413/timeouts; document duplicates in the sidebar.
**Phase:** D21.

### Pitfall 20: Deleting a KB entry leaves orphans
**What goes wrong:** Per-entry delete removes SQLite rows but not the vectors (or vice versa); cascade clean-up of in-memory caches is forgotten (project convention: `cleanup_chat_caches`, FK cascade via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`, never `Field(ondelete=...)`). Deleting a document while a RAG query is mid-flight returns chunks that no longer exist -> citation to a missing chunk.
**Prevention:** FK cascade document -> chunk; rebuild-or-`remove_ids` (with `IndexIDMap2`) in the same locked section as the SQLite delete; drop in-memory index caches for the KB; tolerate missing chunk on lookup (skip it, log). Test in the style of `test_cascade_delete.py`. If a chat's RAG block references the deleted chunk's retrieval record, keep the stored snippet text so old messages still display their sources (snapshot `source/section/quote` into the retrieval record instead of joining live).
**Detection:** `ntotal` unchanged after delete; sources panel shows blank titles.
**Phase:** D21 (cross: D24/D25 snapshotting).

### Pitfall 21: Long-dialog (D25) retrieval and task-memory interplay
**What goes wrong:** Retrieval on each turn using only the last user message ("а за повторное?") returns irrelevant chunks; old retrieved chunks pile up in history and crowd out new ones; task memory (clarified facts/constraints/goal) is saved by the LLM tool-call mechanism and the small model forgets to call it; memory stores quoted law text as "facts" and drifts from the source; the dialog goal is overwritten by a later sub-question.
**Prevention:** Build the retrieval query from last user message + task-memory goal/terms (+ rewrite); keep RAG blocks ephemeral (Pitfall 8); store in task memory only user-stated facts/constraints and **references** (chunk ids), not law text. Add a deterministic server-side update of "sources used so far" to the task state rather than relying on the LLM to call a tool. Always show sources for each turn (rendered from the retrieval record). For the two scenarios, script them as fixtures (messages list) and run them with the harness so results are repeatable; assert on retrieval records (expected статья appears by turn N).
**Detection:** Turn 6+ answers cite statutes unrelated to the dialog; memory panel fills with article text.
**Phase:** D25.

### Pitfall 22: Interaction with the existing per-user, tool-calling chat pipeline
**What goes wrong:** Putting retrieval inside the LLM tool-calling loop (the model decides whether to search) means small models often skip it. Putting it unconditionally ignores the "RAG on/off" toggle semantic. The toggle stored globally rather than per chat/settings (settings have global vs per-chat fallback; any new setting must preserve fallback and `_resolve_settings`). RAG on/off applied to the wrong WebSocket turn (race with `chat_locks`).
**Prevention:** Retrieval is a **deterministic pre-step** in `_handle_chat_message` (not a tool) when `rag_enabled` for the chat; add `rag_enabled`, `kb_id(s)`, `top_k`, `threshold`, `rewrite` as `Settings` columns with global fallback and a DB migration (existing pattern: `migrate_add_*`), tests like `test_settings_fallback.py`. Emit a WebSocket event (e.g. `rag_sources`) before/with `done` so the UI renders sources independent of model text. Keep the retrieve step inside the per-chat lock, with a timeout (`asyncio.wait_for`) so a hung embedder does not hold the chat; on embedder failure, degrade gracefully ("RAG недоступен, ответ без базы знаний") and say so in the UI rather than silently answering without RAG.
**Detection:** Toggle ON yet identical answers to OFF; sources shown only sometimes.
**Phase:** D22.

---

## Minor Pitfalls

### Pitfall 23: Test isolation (index files, DB, LM Studio)
**What goes wrong:** Tests write `.faiss` files into the real data dir (or the repo), share the dev `app.db`, depend on a live LM Studio, or leave state that breaks the next run. CI-less local runs on Windows keep files locked by open handles.
**Prevention:** Index/data dir comes from settings and tests override it with `tmp_path`; DB uses the test DB from `conftest.py`; embeddings mocked with `respx` returning deterministic fake vectors (hash-based or small hand-made vectors so "relevant" documents are actually nearest); one optional marker-gated integration test for real LM Studio (skipped by default). Chunker, cleaner, quote validator, and threshold logic are pure functions with unit tests (fixture PDF small and committed, **not** the full КоАП). Add `.gitignore` entries for index dir and uploaded files; never commit the corpus.
**Phase:** D21 (set the pattern), all later.

### Pitfall 24: Score and metadata exposure/UI leaks
**What goes wrong:** Source panel inserts chunk text as HTML without `DOMPurify.sanitize()` (PDF text can contain `<`), very long quotes break the layout, modal closing behavior (carried-over Phase 10: modals close only via ×) is not followed for the new "Добавить" modal and upload progress is lost on accidental click-away.
**Prevention:** Render chunk text with `textContent` or DOMPurify; truncate display to about 300 chars with expand; the KB modal closes only via ×, per the carried-over requirement; show indexing progress/status in the sidebar.
**Phase:** D21 / D24.

### Pitfall 25: Logging and secrets
**What goes wrong:** Logging full chunk text or user questions at INFO, API keys; `print()` for indexing progress.
**Prevention:** `structlog` with ids/counts/scores only (`kb_id`, `doc_id`, `chunks`, `elapsed_ms`), no text; follow existing conventions.
**Phase:** all.

### Pitfall 26: Re-index of unchanged files and stale KBs
**What goes wrong:** Re-adding the same PDF after editing chunk params appends duplicates; no way to know which params built a KB, making reports unreproducible.
**Prevention:** Store `chunk_strategy`, `chunk_size`, `overlap`, `embedding_model`, `file_sha256`, `indexed_at` on the document row; "re-index" replaces atomically. Put these into the report headers.
**Phase:** D21.

---

## Phase-Specific Warnings

| Phase | Topic | Likely Pitfall | Mitigation |
|-------|-------|---------------|------------|
| D21 | Stack spike (do first, 1-2 hours) | faiss-cpu wheel on py3.13 Windows; LM Studio `/v1/embeddings` with giga model (type "llm"), dim, max tokens, prefix format | Verify install + one embedding round trip + dim before designing; NumPy fallback ready |
| D21 | PDF extraction | Headers/footers, hyphenation, КонсультантПлюс strings, scans | Pitfall 10; golden-file test; fail on near-empty pages |
| D21 | Chunking | Статья boundaries, `12.9` numbering, notes, tiny/huge chunks, overlap >= size | Pitfalls 11-12; breadcrumb prefix; validation |
| D21 | Embedding pipeline | Query/passage asymmetry, normalization, batch/timeouts, model pinning | Pitfalls 1, 2, 4, 13, 14 |
| D21 | Index persistence | ID drift, delete, atomic write, Cyrillic paths, thread-safety | Pitfalls 3, 15, 20; SQLite as source of truth |
| D21 | Async/upload | Event-loop blocking, supervisor restart, big uploads, user scoping | Pitfalls 5, 19 |
| D22 | RAG pipeline | Context budget, toggle semantics, settings fallback, embedder failure | Pitfalls 8, 22 |
| D22 | Report | Question set/metric design before running | Pitfall 9 (freeze set, include out-of-corpus) |
| D23 | Threshold/rerank | Uncalibrated scores, heavy rerankers | Pitfalls 16, 17 |
| D23 | Query rewrite | Drift, chatty output | Pitfall 18 (merge original + rewrite) |
| D24 | Citations / "не знаю" | LLM non-compliance, hallucinated ids, fake quotes | Pitfalls 6, 7: code-level gate, server-attached sources, substring validation |
| D25 | Long dialog | History-aware retrieval, memory bloat, window overflow | Pitfalls 8, 21 |
| All | Tests | Index files in repo/real data dir, live LM Studio dependency | Pitfall 23 |

## Sources

- Project context: `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md` (context overflow deletion, tiktoken divergence, model_switch_lock, event-loop and supervisor behavior) — HIGH for integration facts.
- Established practice (training knowledge, not re-verified this session) — MEDIUM: FAISS `IndexFlatIP` + `normalize_L2` for cosine, `IndexIDMap2` for custom ids, flat index lacks `remove_ids` stable semantics; instruct-embedder asymmetric prompting (E5/Nomic/GigaEmbeddings model cards); PyMuPDF text extraction/`sort=True`; hybrid lexical + vector fusion (RRF).
- LOW / needs validation in the Day 21 spike: GigaEmbeddings 480m exact instruction format and max sequence length; why LM Studio labels it `llm`; `faiss-cpu` cp313 Windows wheel availability; LM Studio behavior when an embedding model and chat model are co-loaded.
