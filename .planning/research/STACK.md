# Technology Stack: RAG Knowledge Base (milestone v3.0, Week 5)

**Project:** AiAdventAgentV2
**Researched:** 2026-10-03
**Scope:** only NEW additions for RAG (indexing, retrieval, rerank/filter, upload). The existing stack is unchanged.
**Overall confidence:** HIGH for FAISS, PDF and upload (installed and run on this machine, Python 3.13.15 / Windows 11). MEDIUM for the embedding model. See the warning below.

## Headline findings

1. **Add only 4 packages:** `faiss-cpu`, `numpy`, `pymupdf`, `python-multipart`. Nothing else is needed. Do NOT add torch, sentence-transformers, langchain, llama-index or a vector DB.
2. **Use PyMuPDF for PDFs, not pypdf.** Measured on the real test corpus (below), pypdf drops spaces between words, is about 30x slower on the КоАП, and misses headings.
3. **`giga-embeddings-instruct-480m-0826` works through `/v1/embeddings`, but only after an explicit load.** LM Studio's `type: "llm"` classification breaks JIT auto-load on the embeddings endpoint. The call returns "No models loaded" until the model is loaded via `POST /api/v1/models/load`. The app already has this load path in `LMStudioClient`.
4. **Embedding quality caveat (important for Day 22-24 reports):** in a small test on ФЗ-196, this GGUF performed worse than `text-embedding-nomic-embed-text-v1.5`, and the "Instruct:" prefix made it worse. Details and mitigation are in the Embeddings section.
5. **LM Studio has no `/v1/rerank` endpoint.** Verified locally: it returns "Unexpected endpoint". Rerank must be done with an LLM or a heuristic, not a cross-encoder service.

## Recommended Stack (additions)

### Vector search
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| faiss-cpu | 1.15.1 (`pip download` succeeded: `faiss_cpu-1.15.1-cp313-cp313-win_amd64.whl`, 16 MB) | Vector index | cp313 win_amd64 wheel exists. Verified in this environment: IndexIDMap2, remove_ids, serialize, write_index all work. Required by the course assignment. |
| numpy | >=2.0 (2.5.3 resolved; faiss-cpu pulls it in) | float32 arrays for FAISS | Required by the FAISS Python API. Pin it explicitly as `numpy>=2.0`. |

**Index type: `faiss.IndexIDMap2(faiss.IndexFlatIP(dim))` with L2-normalized vectors (cosine via inner product).**
- Corpus size: the КоАП is about 3.7M chars. At roughly 1000-1500 chars per chunk that is 2.5-4k chunks, plus the ФЗ-196. That is under 10k vectors.
- Exact search over 5000x768 vectors took about 0.8 ms. IVF/HNSW add training or tuning complexity and approximate recall for no gain at this scale. Do not use IVF.
- `IndexIDMap2` (not `IndexIDMap`) supports `remove_ids` and `reconstruct`. Verified: `remove_ids` on 1000 of 5000 vectors returned 1000 removed and `ntotal=4000`. Note that `remove_ids` on a flat index is O(n), which is fine here.
- Use the SQLite `chunk.id` (int64 primary key) as the FAISS id with `add_with_ids`. This gives a clean join from FAISS hits to chunk text and metadata.

**Persistence and KB layout.**
- Recommended: **one FAISS index file per KB** at `data/kb/{kb_id}.faiss`, plus SQLite tables for KBs, documents and chunks.
- Deleting a KB then means deleting one file plus the SQLite rows (cascade). This avoids `remove_ids` entirely and keeps deletion trivially correct.
- Store text and metadata in SQLite, not in FAISS. Store only vectors in FAISS. The FAISS file is a derived artifact and can be rebuilt from SQLite chunk rows if embeddings are also stored (optional `BLOB` column).
- Use `faiss.serialize_index()` / `deserialize_index()` and write the bytes yourself with pathlib. This avoids FAISS C++ file I/O, which is known to mishandle non-ASCII Windows paths (not reproduced here because the user path is ASCII; this is a precaution). `write_index` and `read_index` also worked here.
- FAISS calls are synchronous CPU work. Wrap `index.search` and `add_with_ids` in `asyncio.to_thread`, per the project's all-I/O-async rule. Keep an in-memory `dict[kb_id, Index]` cache in `agent/state.py` and evict it in `cleanup_chat_caches`-style cleanup on KB delete.
- Searching several KBs: loop over the per-KB indexes and merge by score. This is simple at this scale.

### PDF extraction
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pymupdf | 1.28.2 (cp313 wheel, installed OK) | Text extraction per page | Fast, accurate spacing, no external binaries |

**Measured on `C:\Projects\RAG` (this machine):**

| Metric | pypdf 6.19.0 | PyMuPDF 1.28.2 |
|--------|--------------|----------------|
| ФЗ-196 (45 pages), time | 0.8 s | 0.1 s |
| КоАП (875 pages), time | 90.6 s | 2.8 s |
| ФЗ-196 text quality | words glued together: "Статья2.Основныетермины" | "Статья 2. Основные термины" |
| КоАП text quality | newline after almost every token in some pages ("Федеральным\n \nзаконом\n \nот") | clean lines |
| "Статья N." headings detected, ФЗ-196 | 20 | 34 |
| "Глава N." headings detected, ФЗ-196 | 0 | 8 |
| КоАП: статей / глав | 1138 / 32 | 1138 / 32 |

pypdf is therefore unsuitable. It breaks the Статья/Глава regex on ФЗ-196, and it would block the event loop for 90 s on the КоАП.

- **License caveat:** PyMuPDF is AGPL-3.0 (or commercial). This is a local coursework app that is not distributed, so this is acceptable. If this ever matters, the drop-in permissive alternative is **pypdfium2** (BSD/Apache, 5.13.0). It was tested here: 0.1 s on ФЗ-196, correct spacing, but it uses `\r\n` line endings, so normalize them. Do not use pdfplumber (pdfminer-based, slow like pypdf, adds Pillow/cryptography). I did not benchmark it on the КоАП.
- Run extraction in `asyncio.to_thread`. It is CPU-bound, though 3 s for the КоАП is tolerable.
- Add plain `.txt` and `.md` support with no extra libraries (decode as UTF-8, fall back to cp1251).

**Structure detection: plain `re`, no library.**
- Per-line regexes with `re.M`:
  - `^Статья\s+(\d+(?:\.\d+)*)\.\s*(.*)$` (handles "Статья 1.1." and "Статья 12.3").
  - `^Глава\s+([\dIVXLC]+(?:\.\d+)?)\.\s*(.*)$`.
  - Optionally `^Раздел\s+[IVXLC\d]+\.`.
- Section = Глава (parent) > Статья. Store `section` as "Глава 12 > Статья 12.9" in chunk metadata.
- Known cases to handle:
  - The Глава title often sits on the next line.
  - Strip page headers/footers and consultant-style footer lines that repeat per page (the КоАП is a ConsultantPlus-style export). Detect lines repeated on more than N pages and drop them.
  - Do not anchor regexes with `\s*` before "Статья", which would match in-text references like "см. статью 2".
  - The same regexes work after PyMuPDF; the 4 rows above give the evidence.
- Long articles (a КоАП article can exceed 500 tokens) must be sub-split with overlap, keeping the article header on each piece. See the embeddings section on the token limit.

### Embeddings (via the existing httpx + LM Studio setup, no new package)
Do NOT add the `openai` SDK. Extend `agent/llm_client.py` with `async def embed(texts, model) -> np.ndarray` that POSTs `{base}/v1/embeddings` with `{"model": id, "input": [..]}`, mirroring `_post_chat_completion`.

Verified behavior (this machine, LM Studio running):
- Both models return **L2-normalized** vectors (norm about 1.0). Still call `faiss.normalize_L2` defensively, because it is cheap and protects against other models.
- Batching: `input` as a list works. 32 texts of about 300 tokens took 2.5 s. Use batches of 16-32 with a per-request timeout of about 120 s and bounded concurrency of 1 (single local GPU/CPU). The КоАП (about 3k chunks) is about 4-6 minutes. This must be a **background job with progress** (poll or WebSocket), not a blocking request.
- `usage` returns `prompt_tokens: 0`, so use tiktoken for token estimates.
- `giga-embeddings-instruct-480m-0826`: `GET /api/v0/models` says `type: "llm"`, `arch: qwen3`, `quantization: Q8_0`, max context 8192.
  - `POST /v1/embeddings` returned "No models loaded" when the model was not loaded. After `POST /api/v1/models/load {"model": ...}` (3 s) it returned 768-dim vectors.
  - Nomic (`type: "embeddings"`, 768-dim) JIT-loads fine.
  - Integration: before indexing or querying, ensure the embedding model is loaded (call the existing load path under `model_switch_lock`). A load request for an embedding model will evict or compete with the chat model if VRAM is tight. Document this.
  - The embedding dropdown cannot filter on `type == "embeddings"` only, or giga disappears. Show `type == "embeddings"` plus an allowlist or name match for `embed` and `giga`, or let the user pick any LM Studio model.
- **Dimension:** store `dim` and the model id per KB. Reject a query against a KB built with a different embedding model, because mixing models silently produces garbage.

**Discrepancies and quality warning (MEDIUM-LOW confidence, needs validation in the Day 21 phase):**
- The official HF card for Giga-Embeddings-instruct-480M-0826 says: dimension **1024**, mean pooling + L2 norm, max sequence **512 tokens**, queries use `Instruct: {task}\nQuery: {text}`, documents get no prefix (source: https://huggingface.co/ai-sage/Giga-Embeddings-instruct-480M-0826).
- The LM Studio copy (publisher `rad0main`, arch `qwen3`) returns **768** dims. It is a community GGUF conversion and may not be equivalent to the official model.
- Mini test on ФЗ-196 (26 article-level chunks, 3 questions with a known gold article, then 1 without), ranking by cosine:
  - giga **with** the Instruct prefix: gold article not in the top 3 for questions 1 and 2. Article 1 ranked first on every question (a "hub" effect).
  - giga **plain (no prefix)**: gold in top 3 for 2 of 3 (articles 2 and 30), but still wrong for the medical question.
  - nomic with `search_query:` / `search_document:` prefixes: gold in the top 3 for 3 of 3 (the medical-exam question hit article 23 at rank 1).
  - This is only 3-4 queries on truncated text. It is a signal, not a benchmark.
- **Recommendation:**
  - Keep giga as the default, as the assignment demands.
  - Make the query prefix and document prefix **per-model configurable** (a small dict keyed by model id: giga = no prefix by default; nomic = `search_query: ` / `search_document: `; unknown = none).
  - Cap chunk size at about 400 tokens (about 1200-1500 chars of Russian) for giga, because of the 512-token limit on the official model.
  - Plan the Day 22/23 comparison report to include an embedding-model A/B (giga vs nomic) on the 10 control questions. Nomic is a ready fallback that JIT-loads with no extra setup.
- Do not hand-roll a `lms` CLI step. The HTTP load endpoint is already used by the app.

### Reranking / relevance filtering
| Option | Verdict | Cost |
|--------|---------|------|
| Similarity threshold + top-K (before/after) | **Primary, Day 23-24.** Required anyway for the "не знаю" behavior. | 0 deps |
| Lexical rerank (BM25 or term-overlap on query tokens vs chunk, fused with cosine via a weighted sum or RRF) | **Recommended heuristic rerank.** Russian legal text has strong exact-term signals (article numbers, "штраф", "лишение права"). Implement about 30 lines in pure Python: lowercase, `re.findall(r"\w+")`, simple suffix truncation (stem-lite). Do not add `rank-bm25` or `pymorphy3`. | 0 deps |
| LLM-as-reranker (existing `complete_chat_detailed`, ask for a 0-10 relevance score per candidate as JSON, top 8-10 candidates, `temperature=0`) | **Recommended as the "separate model" variant.** Reuses the DeepSeek or LM Studio plumbing. Batch all candidates in a single prompt to limit latency. | 0 deps; one extra LLM call per query |
| Query rewrite (LLM call to expand or rephrase before embedding) | Same mechanism as above: `complete_chat`. | 0 deps |
| Cross-encoder via sentence-transformers (`sentence-transformers` 6.1.0) | **Do NOT add.** It pulls torch (hundreds of MB to GB on Windows), which violates the spirit of the lightweight local-first constraint. I did not install it to measure the exact size. | high |
| LM Studio-hosted reranker (bge-reranker-v2-m3 GGUF) | **Not possible today.** LM Studio has no `/v1/rerank` endpoint; confirmed locally ("Unexpected endpoint or method") and via open feature requests (lmstudio-ai/lms#521, lmstudio-ai/docs#162). LM Studio maps rerank GGUFs to the embeddings interface incorrectly (lmstudio-js#231). | n/a |

Where the Day 23 spec allows "similarity threshold / separate model / heuristic", the plan is: threshold filter (primary) + lexical-fusion rerank (heuristic) + optional LLM scoring (separate model). That covers all three with zero new dependencies.

### Upload
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| python-multipart | 0.0.32 (already installed here as an indirect dependency, probably via `mcp`) | FastAPI `UploadFile` / `Form` multipart parsing | FastAPI refuses to start routes using `Form`/`File` without it. **Pin it explicitly** in `requirements.txt` so it is not a hidden transitive dependency. |

- Endpoint shape: `POST /api/v1/knowledge-bases` as `multipart/form-data` with `files: list[UploadFile] = File(...)` and `Form` fields: `name`, `strategy`, `chunk_size`, `chunk_overlap`, `embedding_model`.
- The КоАП PDF is 10 MB. Starlette spools uploads to temp files, so memory is fine. `await file.read()` then pass bytes to `asyncio.to_thread(extract)`. Alternatively, the user's PDFs live at a local path, so the endpoint can also accept server-side paths. Prefer upload because the UI spec says "file picker".
- Do not accept arbitrary file paths from the client (path traversal). Use the filename only as display metadata and keep the stored name server-generated.
- Return `202` plus a job id for indexing and show progress. A one-shot blocking request of 5 minutes will hit browser and proxy limits.
- Frontend: a plain `<input type="file" multiple accept=".pdf,.txt,.md">` and `FormData` with `fetch`. No library. The WebSocket and cookie auth model is unchanged. Remember that the fetch must not set `Content-Type` manually.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Vector store | faiss-cpu `IndexFlatIP` + `IndexIDMap2` + SQLite | chromadb / lancedb / sqlite-vec | The assignment explicitly says FAISS + SQLite. The others add dependencies or a server. At under 10k vectors, brute-force is the right answer. |
| FAISS index type | Flat | IVF / HNSW | Needs training or tuning, no speedup at this scale, and IVF removal semantics are more awkward. |
| PDF | pymupdf | pypdf | Measured: no spaces, 30x slower, misses headings. |
| PDF | pymupdf | pypdfium2 | Permissive license, equally good in a quick test; pick it only if AGPL is a concern. |
| PDF | pymupdf | pdfplumber | Slow, heavier, no quality gain for plain-text PDFs. |
| Embedding client | httpx (existing) | `openai` SDK | A new dependency for a single POST. |
| Reranker | lexical fusion + LLM scoring | sentence-transformers cross-encoder | Torch weight. |
| Chunking | hand-written (about 60 lines) | langchain text splitters | Heavy dependency tree; the structure-aware splitter needs custom regexes anyway. |
| Tokenizer for chunk size | tiktoken (existing, `cl100k_base`) | model-specific tokenizer | Only an approximation for the Qwen3-based giga tokenizer. Cyrillic tokenizes worse than English in cl100k, so cl100k over-counts. Use a conservative size (about 400 cl100k tokens, or characters) to stay inside 512. |

## Installation

```bash
# requirements.txt additions (new section: RAG, Week 5)
faiss-cpu>=1.13,<2      # 1.15.1 verified on Windows / cp313
numpy>=2.0              # required by faiss; float32 vector arrays
pymupdf>=1.26           # 1.28.2 verified; PDF text extraction (AGPL, local use)
python-multipart>=0.0.20  # 0.0.32 present; required for UploadFile/Form
```

```bash
pip install faiss-cpu numpy pymupdf python-multipart
```

No dev dependencies are needed. For tests, use `respx` (existing) to mock `/v1/embeddings` with deterministic vectors. FAISS and PyMuPDF run for real in tests, and a tiny generated PDF can come from `pymupdf` itself.

## Integration points

- `agent/llm_client.py`: add `embed()` using the same httpx pattern. Provider selection for embeddings is LM Studio only. DeepSeek has no embeddings endpoint, so the embedding dropdown lists only LM Studio models (consistent with the provider-grouped picker).
- `shared/models.py`: new SQLModel tables `KnowledgeBase`, `KbDocument`, `KbChunk` (id, kb_id, document_id, chunk_index, text, section, source, char range, token count). Use `user_id` scoping, and `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` for cascade, per project convention. Index files live outside SQLite, so delete them in the delete handler and clear the in-memory index cache in `agent/state.py`.
- New module (for example `agent/rag/`): `extract.py` (PyMuPDF), `chunking.py` (fixed + structure), `embeddings.py`, `index.py` (FAISS wrapper, `to_thread`), `retrieve.py`, `rerank.py`.
- Windows gotcha for all scratch scripts and logging: set `PYTHONIOENCODING=utf-8` or the console will raise `UnicodeEncodeError` on Cyrillic. Not an issue inside the app, because structlog output is JSON, but keep `ensure_ascii=False` in mind for debug output.
- Use `faiss.normalize_L2` on a C-contiguous `float32` array. Passing a float64 array raises an error.
- Concurrency: guard index write and rebuild per KB with an `asyncio.Lock` (same pattern as `chat_locks`). The embedding load must go through the existing `model_switch_lock`.

## What NOT to add

- torch / sentence-transformers / transformers
- langchain / llama-index / haystack
- chromadb / qdrant / lancedb / sqlite-vec
- `openai` SDK
- `rank-bm25`, `pymorphy3`, `nltk` (use the 30-line heuristic)
- Celery or any queue for indexing; use an `asyncio.create_task` background job with an in-memory progress dict (consistent with the existing in-process state pattern)
- Any npm or bundled frontend library for upload

## Sources

- Local verification (HIGH): `pip download` and install of faiss-cpu 1.15.1 (cp313 win_amd64), numpy 2.5.3, pymupdf 1.28.2, pypdf 6.19.0, pypdfium2 5.13.0, python-multipart 0.0.32 on Python 3.13.15. Extraction benchmark on `C:\Projects\RAG\*.pdf`. FAISS add, remove, search, serialize and write/read tested. LM Studio `/v1/embeddings`, `/api/v0/models`, `/api/v1/models/load` and `/v1/rerank` probed live.
- Giga-Embeddings-instruct-480M-0826 model card (MEDIUM, official but does not describe the LM Studio GGUF): https://huggingface.co/ai-sage/Giga-Embeddings-instruct-480M-0826
- Giga-Embeddings-instruct family and prefix format (MEDIUM): https://huggingface.co/ai-sage/Giga-Embeddings-instruct
- LM Studio rerank feature requests (MEDIUM; consistent with the local probe): https://github.com/lmstudio-ai/lms/issues/521 , https://github.com/lmstudio-ai/docs/issues/162 , https://github.com/lmstudio-ai/lmstudio-js/issues/231
- Not verified by me (LOW): the non-ASCII path problem in FAISS file I/O on Windows. This comes from training knowledge and was not reproduced, so the byte-serialization advice is a precaution. Torch install size on Windows was not measured.
- Not done: Context7 library lookups (the faiss API used here was exercised directly instead). No retrieval benchmark beyond the 4-question mini test, so the embedding model comparison needs a proper 10-question evaluation during the phase.
