# Phase 15: Reranking and filtering (Day 23) - Pattern Map

**Mapped:** 2026-10-03
**Files analyzed:** 17 (new/modified)
**Analogs found:** 16 / 17 (at mapping time the UI files had no analog: `ui/static/app.js` had no RAG code until Phase 14 plan 06 landed)

**Update 2026-10-03:** Phase 14 is fully executed. The RAG UI now exists (`#rag-k-wrap` in `ui/static/index.html`; `loadChatRag`, `saveChatRag`, `renderRagControls`, `buildRagMeta`, `buildRagSourcesBlock` in `ui/static/app.js`; `tests/test_rag_static.py`), as do `Day22_report.md`, `tests/test_rag_report.py` and `scripts/e2e_rag_playwright.py`. Where this document says the Phase 14 UI is "not yet executed" or "pending", read the real files as the analog instead.

Note: the repo is Python (no TypeScript). Line numbers refer to the current working tree (branch `Day21`, Phase 14 plans 01-05 executed).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/rag.py` (modify: pipeline entry, `CALIBRATED_THRESHOLDS`, payload v2) | service | request-response / transform | itself (`retrieve`, `build_rag_payload`) + `agent/embeddings.py::prefixes_for` | exact |
| `agent/rag_rank.py` (new: lexical scorer, RRF, rewrite validator, rerank parser, `choose_threshold`) | utility | transform (pure, CPU) | `agent/titles.py::clean_title` (validator/cleaner), `scripts/rag_eval.py::parse_article` | role-match |
| `agent/rag_fts.py` (new: FTS query builder, search, backfill) | service | CRUD (read) | `agent/kb_search.py::search_kb` | role-match |
| `agent/rag_llm.py` or functions in `rag_rank.py` (rewrite + LLM-rerank calls) | service | request-response (non-streaming LLM) | `agent/titles.py::_complete_title` / `request_title` | exact |
| `agent/kb_search.py` (modify: over-fetch, expose query vector, `cosine_for_ids`) | service | request-response | itself | exact |
| `agent/rag_turn.py` (modify: call pipeline, v2 payload, LLM client arg) | service | request-response | itself | exact |
| `agent/ws.py` (modify: pass `client`/model to `prepare_rag_turn`, ~line 796) | controller | streaming | itself | exact |
| `agent/rag_api.py` (modify: new config fields, partial PUT) | route | CRUD | itself | exact |
| `shared/models.py` (modify: `ChatRagConfig` columns) | model | CRUD | `ChatRagConfig` itself | exact |
| `shared/database.py` (modify: column migration + FTS5 DDL/triggers/backfill) | migration | batch | `migrate_add_message_rag_sources` | exact (columns); no analog for FTS DDL |
| `agent/kb_indexer.py` (touch only if backfill hook needed; triggers cover deletes at lines 285, 314, 346) | service | batch | itself | exact |
| `scripts/rag_eval.py` (modify: `calibrate`, `ablate` subcommands) | utility/script | batch | itself (`run_command`, `build_parser`, `load_fixture`) | exact |
| `scripts/rag_judge.py` (new: DeepSeek judge) | utility/script | batch | `scripts/rag_eval.py::run_command` DeepSeek branch | role-match |
| `tests/fixtures/rag/calibration_set.json` (new frozen fixture) | config/fixture | file-I/O | `tests/fixtures/rag/control_set.json` | exact |
| `tests/test_rag.py`, `test_rag_turn.py`, `test_rag_api.py`, `test_rag_eval.py`, `test_database.py`, `test_kb_lifecycle.py`, new `test_rag_rank.py` | test | request-response | `tests/test_rag_turn.py` | exact |
| `ui/static/index.html`, `ui/static/app.js` ("Поиск" popover, "Детали поиска", grey below-threshold line) | component | request-response / event | `Message.tool_trace` `<details>` rendering in `app.js`; Phase 14 plan 06 ("Источники", `#rag-k-wrap`) | partial (RAG UI not present yet) |
| `docs/*` sync | docs | - | - | n/a |

## Pattern Assignments

### `agent/rag.py` pipeline entry, threshold constant, payload v2 (service, request-response)

**Analog:** `agent/rag.py` (existing `retrieve`, `build_rag_payload`) and `agent/embeddings.py::prefixes_for` for the per-model constant.

**Imports pattern** (`agent/rag.py` 3-20): stdlib, then `sqlmodel.ext.asyncio.session.AsyncSession`, then `agent.*`, then `shared.*`; module docstring one line; `logger = get_logger(__name__)`.

**Fail-soft conversion pattern** (`agent/rag.py` 77-105): wrap every failure into `RagFailure(code, text)`; re-raise `asyncio.CancelledError` first; log `rag_retrieve_failed` with `kb_id`, `code`, `error=type(failure).__name__` only.
```python
try:
    results = await asyncio.wait_for(
        search_kb(session, kb, query, top_k),
        timeout=settings.RAG_EMBED_TIMEOUT,
    )
except asyncio.CancelledError:
    raise
except KbNotReadyError as exc:
    code, text, failure = "kb_not_ready", MSG_KB_NOT_READY, exc
...
except Exception as exc:
    code, text, failure = "retrieval_failed", MSG_RETRIEVAL_FAILED, exc
else:
    logger.info("rag_retrieved", kb_id=kb_id, top_k=top_k, results=len(results))
    return results
```
Apply per optional stage (D-14): each stage in its own try/except that records `{"stage": ..., "reason": ...}` into `skipped` and continues, never raising.

**Per-model constant lookup** (`agent/embeddings.py` 27-49), copy for `CALIBRATED_THRESHOLDS` / `calibrated_threshold(model_id)` (unknown model -> 0.0, D-10):
```python
MODEL_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic": ("search_query: ", "search_document: "),
}
def prefixes_for(model_id: str) -> tuple[str, str]:
    lowered = model_id.lower()
    for marker, prefixes in MODEL_PREFIXES.items():
        if marker in lowered:
            return prefixes
    return ("", "")
```

**Versioned payload** (`agent/rag.py` 63, 174-196): keep all v1 keys; bump `PAYLOAD_VERSION = 2`; add `verdict` and `search` keys (research Pattern 1 shape). `parse_rag_payload` (204-212) must keep tolerating v1 rows (old messages).
```python
def build_rag_payload(*, mode, kb_id, kb_name, top_k, sources, dropped, context_tokens, warning):
    return {"v": PAYLOAD_VERSION, "mode": mode, "kb_id": kb_id, "kb_name": kb_name, "top_k": top_k,
            "sources": sources, "dropped": dropped, "context_tokens": context_tokens, "warning": warning}
```
Add optional kwargs `verdict: str = "ok"` and `search: dict | None = None` (default keeps existing callers/tests green).

**Budget stage reuse** (`agent/rag.py` 133-145): `build_rag_block(chunks, budget)` returns `(block, kept, dropped)`; candidates beyond `kept` get status `over_budget`. `sources_from_chunks` (159-171) stays the "в ответе" source list.

---

### `agent/rag_rank.py` (utility, pure transform)

**Analog:** `agent/titles.py` (cleaner/validator functions, compiled module-level regexes, pure and unit-testable) and `scripts/rag_eval.py::parse_article` (lines 60-65) for the article-number logic (`ARTICLE_IN_SECTION_RE` defined near the top of that script; move/duplicate into `agent/`, do not import `scripts/` from `agent/`).

**Cleaner/validator pattern** (`agent/titles.py` 49-53, 120-148): precompiled regexes, `isinstance` guard, strip `<think>` blocks and dangling closing tag, take first non-empty line, strip quotes and label, return `None` when unusable.
```python
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)
_LABEL_RE = re.compile(r"^(?:chat\s+title|title|название|заголовок)\s*:\s*", re.IGNORECASE)
_QUOTE_CHARS = "\"'«»“”„"
...
text = _THINK_BLOCK_RE.sub("", raw)
closings = list(_THINK_CLOSE_RE.finditer(text))
if closings:
    text = text[closings[-1].end():]
```
Rewrite validator (`validate_rewrite(original, raw) -> str | None`) copies this front half, then applies the research Pattern 5 rejection rules (multi-line, length, chatty markers, digit tokens preserved, stem overlap). Reuse by importing `_THINK_BLOCK_RE` style or re-declaring; if importing from `agent.titles` keep it to public helpers (consider a small shared helper rather than importing private names).

**Prompt-injection delimiter pattern** (`agent/titles.py` 32-39, 73-85): system prompt states tag content is data; wrap user text in tags; neutralize wrapper tags with `_neutralize_tags`. For rerank prompt chunk text also pass through `agent.rag._neutralize` (rag.py 116-118).

CPU-bound scoring (lexical fusion) is called through `asyncio.to_thread` from the pipeline (CONTEXT established pattern, same as `index.search` in `kb_search.py` line 79).

---

### LLM-calling stages: rewrite and LLM rerank (service, request-response non-streaming)

**Analog:** `agent/titles.py::_complete_title` + `request_title` (lines 164-215). This is the exact shape: `complete_chat_detailed`, temperature 0, `extra_body={"reasoning_effort": "none"}`, one retry without the field on HTTP 400/422, `asyncio.wait_for` timeout, any failure returns None and logs.

```python
async def _complete_title(client: LLMClient, messages: list[dict[str, str]], model: str) -> ChatCompletionResult:
    try:
        return await client.complete_chat_detailed(
            messages=messages, model=model, temperature=TITLE_TEMPERATURE,
            max_tokens=TITLE_MAX_TOKENS, extra_body={"reasoning_effort": TITLE_REASONING_EFFORT},
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code not in TITLE_REJECTED_STATUS_CODES:
            raise
        logger.info("chat_title_reasoning_control_rejected", model=model, status_code=status_code)
    return await client.complete_chat_detailed(
        messages=messages, model=model, temperature=TITLE_TEMPERATURE,
        max_tokens=TITLE_MAX_TOKENS, extra_body=None,
    )
```
```python
try:
    result = await asyncio.wait_for(_complete_title(client, messages, model), timeout=TITLE_TIMEOUT_SECONDS)
except Exception as exc:
    logger.warning("chat_title_llm_failed", error_type=type(exc).__name__, error=str(exc))
    return None
...
logger.warning("chat_title_llm_unusable", model=model, finish_reason=result.finish_reason,
               content_empty=not (result.content or "").strip(), has_reasoning=result.has_reasoning,
               completion_tokens=result.completion_tokens)
```
Differences for Phase 15: return a stage result with a skip reason (`bad_output` / `timeout` / `http_error`) instead of bare None (D-14); log `finish_reason`/`has_reasoning` (research Pitfall 5); timeout constant `RAG_LLM_STAGE_TIMEOUT` (add to `shared/config.py` next to `RAG_EMBED_TIMEOUT`). Note `except Exception` also swallows `CancelledError` only on Python < 3.8; on 3.11 `CancelledError` is a `BaseException`, but keep an explicit `except asyncio.CancelledError: raise` first to match `rag.py` line 89.

**Client and model source:** `agent/ws.py` line 720 `client, provider_row = await resolve_client(chat.user_id, payload.provider_id)`; call site `prepare_rag_turn(...)` at lines 796-804. Pass `client` and the model id into `prepare_rag_turn` as new trailing optional args (default `None` -> stages needing an LLM are skipped with reason `no_llm`), so existing tests calling `prepare_rag_turn(session, chat, QUESTION, msgs, ctx, max_tokens)` (tests/test_rag_turn.py line 59) remain valid.

---

### `agent/kb_search.py` (service, request-response)

**Analog:** itself. `search_kb` (lines 65-112): status check -> `load_index_cached` -> `embed_query` -> dim check -> `faiss.normalize_L2` -> `asyncio.to_thread(index.search, ...)` -> filter `chunk_id >= 0` -> load `KbChunk` rows with `KbChunk.kb_id == kb.id` -> dict results `{rank, score, chunk_id, source, title, section, page, text}`.

Core excerpt to refactor around (lines 71-79):
```python
index = await load_index_cached(kb)
vector = await embed_query(kb.embedding_model, query, None, kb.query_prefix)
array = np.asarray([vector], dtype="float32")
got = int(array.shape[1])
if got != index.d or (kb.dim is not None and got != kb.dim):
    logger.error("kb_search_dim_mismatch", kb_id=kb.id, expected=index.d, got=got)
    raise EmbeddingDimMismatchError()
faiss.normalize_L2(array)
scores, ids = await asyncio.to_thread(index.search, array, top_k)
```
Plan: keep `search_kb(session, kb, query, top_k)` signature and return shape unchanged (Phase 13 test-search modal and `tests/test_kb_search.py` depend on it). Add a lower-level helper returning the normalized query vector plus results (so the pipeline can reuse it for FTS-only cosine via `index.reconstruct(chunk_id)` and the cosine drift signal). Tests monkeypatch `kb_search.embed_query` (tests/test_rag_turn.py line 32), so the embedding call must stay inside `kb_search` under that name. Result dicts carry `chunk_id` as the string label (e.g. "12-3") while the FAISS id is the integer `KbChunk.id`; the pipeline must carry both (add `"row_id"` key additively, do not rename existing keys).

---

### `agent/rag_fts.py` (service, CRUD read from SQLite FTS5)

**Analog:** `agent/kb_search.py::search_kb` for session usage and kb_id scoping (`KbChunk.kb_id == kb.id`); no existing FTS code.

Use the research Pattern 4 builder and query; execute with a bound parameter through the session, e.g. `await session.exec(text("SELECT rowid, bm25(kb_chunk_fts) FROM kb_chunk_fts WHERE kb_chunk_fts MATCH :q AND kb_id = :kb ORDER BY bm25(kb_chunk_fts) LIMIT :n"))` (use `sqlalchemy.text`, as in `shared/database.py`). Catch `sqlalchemy.exc.OperationalError` -> stage skip `fts_error`. Always re-load the rows from `KbChunk` filtered by `kb_id` (research Pitfall 3).

---

### `shared/models.py::ChatRagConfig` (model, CRUD)

**Analog:** `ChatRagConfig` (lines 717-733). Add columns with plain defaults, no `sa_column` needed for non-FK fields (match `mode`/`top_k`):
```python
mode: str = Field(default="off", max_length=20)
top_k: int = Field(default=5)
updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```
New: `candidate_k: int = Field(default=20)`, `threshold: float | None = Field(default=None)`, `lexical: bool = Field(default=False)`, `llm_rerank: bool = Field(default=False)`, `hybrid: bool = Field(default=False)`, `rewrite: bool = Field(default=False)`. Do not use `Field(ondelete=...)` (project rule); no new FKs here.

---

### `shared/database.py` migrations (migration, batch)

**Analog:** `migrate_add_message_rag_sources` (lines 125-140) for the column migration; register in `init_db` (lines 272-282) **before** `create_all`.
```python
async def migrate_add_message_rag_sources(conn: Any) -> None:
    """Add rag_sources column to message when missing (idempotent)."""
    table_check = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='message'"),
    )
    if table_check.fetchone() is None:
        return
    result = await conn.execute(text("PRAGMA table_info(message)"))
    columns = [row[1] for row in result.fetchall()]
    if "rag_sources" not in columns:
        logger.info("migrating_message_add_rag_sources")
        await conn.execute(text("ALTER TABLE message ADD COLUMN rag_sources TEXT"))
```
Create `migrate_add_chatragconfig_rank_columns` the same way (table `chatragconfig`, loop over the six columns; the research draft loop is correct and matches this style, but add the `logger.info("migrating_chatragconfig_add_<col>")` line per project convention).

Existing `init_db` body to extend:
```python
async with engine.begin() as conn:
    await migrate_add_context_length(conn)
    ...
    await migrate_add_scheduledtask_provider_id(conn)
    await conn.run_sync(SQLModel.metadata.create_all)
    await _migrate_legacy_strategies(conn)
```
Add the column migration before `create_all`, and a new `ensure_kb_chunk_fts(conn)` after `create_all` (FTS virtual table + two triggers + count-gated backfill, all `IF NOT EXISTS`, via `conn.exec_driver_sql`, per research Pattern 4). **No analog** for virtual-table/trigger DDL in the repo; follow the research SQL verbatim. Triggers (not explicit deletes) cover the three `delete(KbChunk)` sites in `agent/kb_indexer.py` (lines 285, 314, 346) and FK cascades. Test with the idempotency style in `tests/test_database.py`.

---

### `agent/rag_api.py` (route, CRUD)

**Analog:** itself. Keep `_get_owned_chat`, `_config_out`, dependency lists, rollback pattern:
```python
@router.put("/api/v1/chats/{chat_id}/rag",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)])
async def put_rag_config(chat_id: int, body: RagConfigIn,
    session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)) -> RagConfigOut:
    chat = await _get_owned_chat(session, chat_id, current_user.id)
    ...
    try:
        row = await session.get(ChatRagConfig, chat_id)
        if row is None:
            row = ChatRagConfig(chat_id=chat_id)
            session.add(row)
        row.mode = mode
        row.kb_id = body.kb_id
        row.top_k = body.top_k
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
    except SQLAlchemyError:
        await session.rollback()
        raise
```
Changes: new `RagConfigIn` fields default `None` and applied only when in `body.model_fields_set` (research Pitfall 1 and its code example; `threshold: null` explicit = reset to calibrated). `RagConfigOut` gains `candidate_k`, `threshold` (nullable override), `calibrated_threshold` (from the KB's `embedding_model`), `effective_threshold`, and four flags. `_config_out` default branch (row is None, lines 63-67) must include the new defaults (20, None, all False). Add a test: PUT with only the Phase 14 fields keeps the Phase 15 fields. Existing validation test style: `tests/test_rag_api.py` line 68 (`test_config_validation_422`, parametrized body).

---

### `agent/rag_turn.py` (service, request-response)

**Analog:** itself. Keep the single fail-soft envelope: `except asyncio.CancelledError: raise` / `except RagFailure` / `except Exception` producing a payload with a `warning`, then `_log`. Replace the single call
```python
chunks = await retrieve(session, kb, question, config.top_k)
used = _message_tokens(llm_messages) + extra_tokens
budget = rag_budget(context_length, used, max_tokens)
block, kept, dropped = build_rag_block(chunks, budget)
if chunks and block is None:
    raise RagFailure("context_full", MSG_CONTEXT_FULL)
```
with the pipeline call returning `(final_chunks, trace)`. New behavior (D-09): when the pipeline yields no survivors, do NOT raise and do NOT insert a block; instead append a short "nothing relevant found" instruction to the outbound last user message, set `verdict="below_threshold"`, and still return mode `rag` with a payload whose `search` trace lists all candidates. Note the existing guard `if chunks and block is None: raise RagFailure("context_full")` must only apply when there were survivors. After `build_rag_block`, mark candidates beyond `kept` as `over_budget` in the trace. `_log` (lines 160-173) logs counts/ids only; add `verdict` and `candidates` count, never query text.

`RagTurn.sources_json`/`done_payload` already route the payload to both `Message.rag_sources` (ws.py line 993) and `done.rag` (ws.py line 1046), so no change is needed in `ws.py` beyond the extra args at line 796.

---

### `scripts/rag_eval.py` (script, batch) and `scripts/rag_judge.py`

**Analog:** `scripts/rag_eval.py` itself.

**Subcommand wiring** (lines 612-644): `build_parser()` with `sub.add_parser(...)`, `--db`, `--kb LABEL=ID` (`_parse_pairs`), `--fixture`, `--out`; `main()` dispatches by command to an `async def ...(args) -> int` returning `EXIT_OK / EXIT_ERROR / EXIT_PREFLIGHT` (line 57). Add `calibrate` and `ablate` subparsers and extend the dispatch (currently `handler = build_kbs if args.command == "build-kbs" else run_command`, which must become a dict lookup). Default `--out` `eval_out/day23`.

**Preflight + fixture freeze gate** (lines 108-114, 553-577, 585-590): `load_fixture(path, require_frozen=...)` exits 2 when `status != "frozen"`; `_use_scratch_storage(args.db)` before importing `shared.*`; imports of `agent`/`shared` are function-local after the scratch DB env is set; DeepSeek preflight:
```python
if args.provider == "deepseek":
    base_url = args.base_url or DEEPSEEK_BASE_URL
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        print("preflight: DEEPSEEK_API_KEY is not set")
        return EXIT_PREFLIGHT
```
Copy this verbatim for `rag_judge.py` (D-18 checkpoint; `LLMClient(base_url, api_key)`, line 608). Run `init_db()` before hybrid runs on the existing scratch DB (FTS backfill; see research runtime inventory).

**Reusable scoring helpers:** `chunk_hits`, `hit_at_k`, `first_hit_rank`, `parse_article` (lines 60-105), `fixture_sha256` (117-119, record in `run_meta.json`), `_write_json`, `_render_outputs`, `_cell` markdown-escape. `choose_threshold(gold_scores, ooc_top1)` is a pure function in `agent/rag_rank.py` imported by the script. The eval must call the same `run_retrieval_pipeline` as `prepare_rag_turn` (no divergent copy).

---

### `tests/fixtures/rag/calibration_set.json` (fixture, file-I/O)

**Analog:** `tests/fixtures/rag/control_set.json` (frozen, loaded via `load_fixture`, validated by `tests/test_rag_fixture.py`). Copy its schema (`status`, questions with `expected_sources` entries `{file_contains, article}`, out-of-corpus questions with empty `expected_sources`); start as `status: "draft"`, flip to `frozen` after the user checkpoint (D-15). Copy `tests/test_rag_fixture.py` structure for a calibration-set shape test (about 12 answerable / 8 out-of-corpus, no overlap with control questions).

---

### Tests (test, request-response)

**Analog:** `tests/test_rag_turn.py` (lines 1-60): fake embedder via `kb_helpers.install_fake_embedder`, `seed_user`, `seed_kb`, `run_index_job`, monkeypatch `kb_search.embed_query` with `vector_for`, helper `_chat(...)` creating `Chat` + `ChatRagConfig`, `_run(...)` calling `prepare_rag_turn` inside `async_session_factory()`. Reuse these for pipeline tests (threshold cut, below_threshold verdict, per-stage skip with a fake LLM client object exposing `complete_chat_detailed`). Pure-function tests (lexical, RRF, validator, parser, `choose_threshold`, FTS builder) go in new `tests/test_rag_rank.py`; FTS delete/trigger tests in `tests/test_kb_lifecycle.py`; migration idempotency in `tests/test_database.py`. `pytest.ini`: `asyncio_mode = auto` (no decorators needed). HTTP mocking via `respx`. Per project rule, add scenarios to `docs/TESTING_GUIDE.md`.

---

### UI: `ui/static/index.html` / `ui/static/app.js` (component)

**Analog:** the `Message.tool_trace` `<details>` rendering in `app.js` (CONTEXT code_context); Phase 14 plan 06 ("Источники" block, `#rag-k-wrap`, `.planning/phases/14-first-rag-query-day-22/14-UI-SPEC.md` and `14-PATTERNS.md`) is not yet executed in the repo, so there is no RAG analog to read now. **Dependency:** sequence Phase 15 UI plans after 14-06 (and the ablation after 14-07), or state it explicitly. Rules: all text via `textContent`, `createElement` for the candidate table, no new CDN libs, JS syntax guarded by `tests/test_static_js_syntax.py`; status codes map to Russian chips in the UI (stored as stable English codes).

---

## Shared Patterns

### Fail-soft per stage (never raise out of the turn)
**Source:** `agent/rag.py` 77-105, `agent/rag_turn.py` 137-155
**Apply to:** pipeline, rewrite, LLM rerank, FTS, lexical stages. `except asyncio.CancelledError: raise` first; log `error=type(exc).__name__`; record `{stage, reason}` in `trace["skipped"]`.

### Idempotent DDL
**Source:** `shared/database.py` 125-140 and `init_db` 272-282
**Apply to:** `ChatRagConfig` columns, FTS table, triggers, backfill.

### Non-streaming LLM helper with reasoning-off retry
**Source:** `agent/titles.py` 164-215
**Apply to:** rewrite and LLM rerank.

### Prompt-injection delimiting
**Source:** `agent/titles.py` 32-39, 56-70 and `agent/rag.py` 116-118 (`_neutralize`)
**Apply to:** rewrite prompt (question as data) and rerank prompt (chunk text as data).

### Metadata-only trace, versioned payload
**Source:** `agent/rag.py` 159-196
**Apply to:** `rag_sources` v2 and `done.rag`; never put chunk text, query text beyond the stored `search.query`/`rewritten`, or API keys in logs.

### Auth / ownership
**Source:** `agent/rag_api.py` 47-56 (`_get_owned_chat` returns 404, never 403), `agent/rag_turn.py` 73-83 (`_load_kb` treats a foreign KB as missing)
**Apply to:** any new route or KB lookup; every FTS query filters `kb_id`.

### Logging
`logger = get_logger(__name__)`; snake_case event key plus key=value pairs (`rag_turn_prepared`, `rag_retrieved`); no secrets, no tracebacks.

### Threading
CPU-bound work in `asyncio.to_thread` (`agent/kb_search.py` line 79). LLM/embedding calls wrapped in `asyncio.wait_for` with a settings timeout (`settings.RAG_EMBED_TIMEOUT`, rag.py line 87).

## Conventions

Convention derivation skipped (the `gsd-tools.cjs` derive module was not found at the plugin path; `CLAUDE_PLUGIN_ROOT` unset). Manual reading of the analogs shows the Python repo style from CLAUDE.md applies:

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | lowercase_with_underscores.py | ~100% (agent/, shared/, scripts/, tests/) | low | named contract |
| Identifier casing | snake_case functions/vars, PascalCase classes, UPPER_CASE constants | ~100% | low | named contract |
| Export style | module-level public names; leading `_` for private | ~100% | low | named contract |
| Import style | stdlib -> third-party -> local, absolute from project root (`from agent.x import y`), no aliases | ~100% | low | named contract |

(Shares are qualitative estimates from the files read, not tool output.)

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` CJS `module.exports`/`require`; `sdk/src/**` ESM) is a GSD-plugin repo split and does not exist in this Python project; nothing here is contested. Match the local style of each directory (`agent/`, `shared/`, `scripts/`, `tests/`). Within `ui/static/`, vanilla JS only.

## No Analog Found

| File / piece | Role | Data Flow | Reason |
|--------------|------|-----------|--------|
| FTS5 virtual table + triggers + backfill | migration | batch | No virtual tables or triggers in the repo; use research Pattern 4 SQL |
| Lexical scorer, RRF fusion, rerank-output parser | utility | transform | New algorithms; pure-function style follows `agent/titles.py` cleaners |
| "Поиск ⚙" popover, "Детали поиска" table | component | request-response | No RAG UI in `app.js` yet (Phase 14-06 pending); only the `tool_trace` `<details>` pattern exists |
| `rag_judge.py` LLM-judge rubric | script | batch | No judge exists; reuse `run_command` DeepSeek preflight and `LLMClient` |

## Metadata

**Analog search scope:** `agent/`, `shared/`, `scripts/`, `tests/`
**Files read:** `agent/rag.py`, `agent/rag_api.py`, `agent/rag_turn.py`, `agent/kb_search.py`, `agent/titles.py`, `agent/embeddings.py` (prefixes), `agent/ws.py` (call site), `shared/database.py` (migrations/init_db), `shared/models.py` (KbChunk, ChatRagConfig), `scripts/rag_eval.py` (helpers, CLI), `tests/test_rag_turn.py` (head), plus CONTEXT.md and RESEARCH.md
**Pattern extraction date:** 2026-10-03
