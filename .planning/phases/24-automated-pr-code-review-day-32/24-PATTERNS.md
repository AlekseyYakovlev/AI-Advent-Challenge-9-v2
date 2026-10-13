# Phase 24: Automated PR code review (Day 32) - Pattern Map

**Mapped:** 2026-10-13
**Files analyzed:** 22 new/modified
**Analogs found:** 19 exact or role-match / 22 (3 have no in-repo analog)

Line numbers refer to the working tree on branch Day31 (including uncommitted Phase 23 files `agent/help_api.py`, `shared/models.py`, `tests/test_help_api.py`; these are valid analogs but must not be modified).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/review_api.py` (router: `POST /api/v1/reviews`, settings GET/PUT, refresh) | route/controller | request-response (long, shielded) | `agent/http_chat.py` (review route), `agent/help_api.py` (settings routes) | exact |
| `agent/review_pipeline.py` (budget, split, retrieval, calls, merge, sanitize) | service | transform / request-response | `agent/rag_llm.py::_complete` + `agent/rag_pipeline.py::run_retrieval_pipeline` | role-match |
| `agent/review_github.py` (in-code GitHub MCP caller, paging) | service/client | request-response | `agent/mcp_tools.py::call_mcp_tool` + `agent/mcp_client.py::ensure_connected` | exact |
| `agent/code_index.py` (git snapshot, ls-tree diff, incremental commit) | service | batch / file-I/O | `agent/kb_indexer.py` (`_commit_addition`, `run_index_job`) + `mcp_servers/project_git.py` (git exec) | role-match |
| `agent/code_chunking.py` (ast chunker + line windows) | utility | transform (pure) | `agent/kb_chunking.py` (`ChunkDraft`, `chunk_fixed`) | role-match |
| `agent/review_prompts.py` (Russian prompt, delimiters, neutralisation) | utility | transform | `scripts/rag_judge.py` (`_neutralise`, `build_judge_messages`) + `agent/rag.py` (`_neutralize`) | role-match |
| `shared/models.py` (+ `ReviewConfig`, + code-KB link table) | model | CRUD | `HelpConfig` (lines 781-812) | exact |
| `shared/database.py` / `agent/main.py` (register router, create table) | config | wiring | `app.include_router(help_router)` (main.py:478) | exact |
| `ui/static/index.html` (`#review-settings-section`) | component | request-response | `#mcp-section` (line 342), `#llm-providers-section` (line 387) | exact |
| `ui/static/app.js` (review settings load/save/refresh) | component | request-response | `loadMcpServers` (line 2135) and the providers section code | role-match |
| `.github/workflows/pr-review.yml` | config (CI) | event-driven | none in repo (`.github/` absent) | no analog |
| `scripts/pr_review_ci.py` (stdlib caller, comment post/replace) | script | request-response | `scripts/verify_service.py` (`call`, `scrub`, `_OPENER`) | exact (HTTP part) |
| `scripts/review_judge.py` (DeepSeek judge over frozen set) | script | batch | `scripts/rag_judge.py` | exact |
| `scripts/review_control.py` (optional: create/close frozen PRs, collect comments) | script | batch | `scripts/rag_dialog.py` / `scripts/verify_service.py` | partial |
| `tests/test_review_api.py` | test | request-response | `tests/test_help_api.py`, `tests/test_http_chat.py` | exact |
| `tests/test_code_chunking.py` | test | transform | `tests/test_kb_chunking.py` | exact |
| `tests/test_code_index.py` | test | batch | `tests/test_kb_indexer.py` | role-match |
| `tests/test_review_github.py` | test | request-response | `tests/test_mcp_tools.py`, `tests/test_mcp_real_github.py` | role-match |
| `tests/test_review_pipeline.py` | test | transform | `tests/test_http_chat.py` (respx LLM mocking) | role-match |
| `tests/test_pr_review_ci.py` | test | request-response | `tests/test_verify_service.py` | exact |
| `tests/test_database.py` (add new table names to asserted list) | test | CRUD | existing `helpconfig` entry in `test_init_db_creates_all_tables` | exact |
| `Day32_report.md`, `eval_out/day32/`, `tests/fixtures/review/` manifest | report/fixture | batch | `Day30_report.md`, `Day25_report.md` | role-match |
| `docs/API_SPEC.md`, `ARCHITECTURE.md`, `USER_GUIDE.md`, `TESTING_GUIDE.md`, `README.md` | docs | n/a | prior phase doc sections | n/a |

## Pattern Assignments

### `agent/review_api.py` (route, request-response)

**Analog:** `agent/http_chat.py` (the review route) and `agent/help_api.py` (settings routes).

**Imports pattern** (`http_chat.py` 3-22; stdlib, third-party, local):
```python
import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from shared.database import async_session_factory, get_session
from shared.logger import get_logger
from shared.models import User

logger = get_logger(__name__)
router = APIRouter(tags=["http-chat"])
```

**Error code constants and uniform error body** (`http_chat.py` 26-59). Copy the shape for the review codes (`REVIEW_NOT_CONFIGURED`, `GITHUB_MCP_UNAVAILABLE`, `PR_NOT_FOUND`, `BASE_UNAVAILABLE`, `EMBEDDER_UNAVAILABLE`, `LM_STUDIO_UNAVAILABLE`, `LLM_UNREACHABLE`, `LLM_ERROR`, `REVIEW_BAD_OUTPUT`, `REVIEW_BUSY`, `INTERNAL_ERROR`):
```python
CODE_LM_STUDIO_UNAVAILABLE = "LM_STUDIO_UNAVAILABLE"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
MSG_INTERNAL = "Internal error while processing the message"

def _error_response(status_code: int, detail: str, code: str, user_message_kept: bool | None,
                    headers: dict[str, str] | None = None, **extra: Any) -> JSONResponse:
    """Build the uniform error body; a JSONResponse keeps `code` that HTTPException drops."""
    content: dict[str, Any] = {"detail": detail, "code": code, "user_message_kept": user_message_kept, **extra}
    return JSONResponse(status_code=status_code, content=content, headers=headers)

def _internal_error() -> JSONResponse:
    return _error_response(500, MSG_INTERNAL, CODE_INTERNAL_ERROR, None)   # fixed text, never exception text
```
Drop `user_message_kept` for the review route (no chat), keep `detail` + `code`.

**Auth + route decorator** (`http_chat.py` 161-172): API key via `get_current_user` (Bearer), JSON content type, origin check:
```python
@router.post(
    "/api/v1/chats/{chat_id}/messages",
    response_model=HttpChatResponse,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def post_chat_message(body: HttpChatRequest, session: AsyncSession = Depends(get_session),
                            current_user: User = Depends(get_current_user)) -> Any:
```
Settings and refresh routes are session-only: use `get_session_user` (`agent/dependencies.py` line 148, refuses API-key callers) as `agent/api_keys_api.py` does.

**Busy guard, rate limit, shielded background task** (`http_chat.py` 35-36, 185-208). Copy the strong-reference set and `asyncio.shield(task)` so a cancelled runner does not leave half-written index state; replace the chat lock with a per-PR (and per-KB) `asyncio.Lock` returning `REVIEW_BUSY` 409:
```python
_http_turns: set[asyncio.Task[Any]] = set()   # strong references
...
lock = chat_locks.setdefault(chat_id, asyncio.Lock())
if lock.locked():
    return _error_response(409, MSG_CHAT_BUSY, CODE_CHAT_BUSY, False)
await lock.acquire()
task = asyncio.create_task(_run_http_turn(..., lock))
_http_turns.add(task)
task.add_done_callback(_http_turns.discard)
result = await asyncio.shield(task)
```
Lock release lives in the task's `finally` (`_run_http_turn` 157-158).

**Provider-unreachable mapping** (`http_chat.py` 73-80): reuse for review LLM failures:
```python
if frame.get("cause") == "unreachable":
    if frame.get("provider_kind") == KIND_LM_STUDIO:
        return _error_response(503, MSG_LM_STUDIO_DOWN, CODE_LM_STUDIO_UNAVAILABLE, False)
    return _error_response(503, detail, CODE_LLM_UNREACHABLE, False)
return _error_response(502, detail, "LLM_ERROR", False)
```

**Settings GET/PUT** (`help_api.py` 36-195). Copy: `ReviewConfigIn` with all-optional fields and `model_fields_set` to tell "omitted" from "null" (lines 36-50, 156-185); `ReviewConfigOut` built by an async `_config_out` that shows a KB only when `kb.user_id == user_id` (75-111); ownership/readiness check on `kb_id` before storing (157-160); try/except with rollback (173-190):
```python
class HelpConfigIn(BaseModel):
    kb_id: int | None = Field(default=None, ge=1)
    lexical: StrictBool | None = None
...
if body.kb_id is not None:
    kb = await _get_owned_kb(session, body.kb_id, current_user.id)
    if kb.status != KbStatus.READY:
        raise _unprocessable(MSG_KB_NOT_READY)
...
try:
    row = await session.get(HelpConfig, current_user.id)
    if row is None:
        row = HelpConfig(user_id=current_user.id)
        session.add(row)
    ...
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
except SQLAlchemyError:
    await session.rollback()
    raise
logger.info("help_config_updated", user_id=current_user.id, kb_id=row.kb_id, changed=changed)
```
Add validation the research requires: `repo` regex `owner/name`, `pr_number >= 1`, `head_sha` 40-hex or empty; repo path comes only from settings, never from the request body.

---

### `agent/review_github.py` (service, request-response)

**Analog:** `agent/mcp_tools.py::call_mcp_tool` (lines 283-329) and `agent/mcp_client.py::ensure_connected` / `get_live_session` (lines 334-378, 413-417).

Do NOT call `call_mcp_tool`: it truncates at `settings.MCP_TOOL_RESULT_MAX_CHARS` (line 325-329) and needs an `McpToolBinding`. Copy its error handling and call the session directly:

```python
session = get_live_session(user_id, server_id)
if session is None:
    return _error(f"MCP server '{name}' is not connected")
try:
    result = await asyncio.wait_for(
        session.call_tool("pull_request_read", arguments={...}),
        settings.MCP_TOOL_CALL_TIMEOUT,   # use a longer review-specific timeout for get_diff
    )
except TimeoutError: ...
except McpError as exc: ...
except Exception as exc:
    logger.warning("mcp_tool_call_failed", user_id=..., server_id=..., tool=..., error_type=type(exc).__name__)
```
Lazy connect first (returns a remembered failure instead of retrying):
```python
result = await ensure_connected(user_id, row.id, row.command, mcp_config.load_args(row),
                                mcp_config.load_env(row) or None, row.cwd)
```
Own flattener modelled on `_flatten_content` (lines 267-280) but without the truncation. Check `isError` (401 Bad credentials) to distinguish "not connected" from "token rejected" -> `GITHUB_MCP_UNAVAILABLE`. Log tool name and sizes only, never arguments/secrets. Page `get_files` at `perPage=100`.

---

### `agent/code_index.py` (service, batch/file-I/O)

**Analogs:** `agent/kb_indexer.py` (commit, index build, job semaphore) and `mcp_servers/project_git.py` (git subprocess).

**Semaphore and background job** (`kb_indexer.py` 72-98): single-slot indexing semaphore created lazily in the running loop, `spawn_*_job` keeping strong refs in `kb_jobs`.
```python
_semaphore: asyncio.Semaphore | None = None
def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(1)
    return _semaphore
```

**Chunk rows reuse** (`kb_indexer.py` 151-168): call `_chunk_rows(kb_id, doc, drafts)` directly (`source=doc.filename` is the repo path).

**First build** (`kb_indexer.py` 243-294): `ensure_embedding_model(...)` -> insert chunks -> `_embed_all` (batches of `EMBED_BATCH_SIZE`, throttled progress via `_update_kb`) -> `_build_and_write_index` (`build_index` + `write_index_bytes`) -> status READY with `dim`, `query_prefix/doc_prefix` from `prefixes_for(model)`. Failures call `_fail(...)` (all-or-nothing). Error classes to map: `KbLoadError`, `EmbeddingError`.

**Incremental commit** (`kb_indexer.py` 387-508, `_commit_addition`). Model on it, but it cannot be reused as is (needs staged files, no pure-delete path). Keep this order and the atomic swap:
```python
# Inserts come first so every new chunk id exceeds every existing id (SQLite reuses rowids after deletes).
session.add(doc); await session.flush(); ... rows = _chunk_rows(kb_id, doc, drafts)
old_chunk_ids = list((await session.exec(select(KbChunk.id).where(KbChunk.kb_id == kb_id, KbChunk.document_id.in_(replaced_ids)))).all())
await session.exec(delete(KbChunk).where(KbChunk.kb_id == kb_id, KbChunk.document_id.in_(replaced_ids)))
await session.exec(delete(KbDocument).where(...))
index = await asyncio.to_thread(patch_index, path, old_chunk_ids, matrix, new_ids)
if int(index.ntotal) != chunk_count:
    raise RuntimeError("index size does not match chunk count")
pending = await asyncio.to_thread(write_index_pending, path, index)
...
await session.commit(); committed = True
# No await between the commit and the swap
promote_index(pending, path)
kb_index_cache[kb_id] = index
```
on exception: if committed -> `kb_index_cache.pop`, raise; else `await session.rollback()` and unlink pending. `KbDocument.sha256` holds the git blob id (40 hex fits `max_length=64`); `stored_name` is non-null so store the blob id as a placeholder; never unlink `uploads_dir()/stored_name` for code KBs.

**Git subprocess (no shell)** (`project_git.py` 115-139, 182-222, 267-281). Copy `git_env()` (drops inherited `GIT_*`, sets `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `LC_ALL=C`), `base_argv()` (`--no-pager --literal-pathspecs -c core.quotepath=off ...`), `run_command` (`asyncio.create_subprocess_exec`, stdin DEVNULL, bounded read, `_kill_tree` on timeout via psutil), and `check_ref_syntax` / `resolve_ref` (`--end-of-options`, `^{commit}`). The agent process cannot import `mcp_servers.project_git` safely if that module exposes global config; copy the small helpers into the agent module rather than importing them.
```python
proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd, env=env)
...
await asyncio.wait_for(work(), timeout)
```
Commands needed: `remote get-url origin` (owner/repo guard), `fetch --no-tags --quiet origin refs/heads/<base_ref>` then `rev-parse FETCH_HEAD^{commit}` under a per-repo-path lock, `ls-tree -r -z -l <commit>`, `cat-file --batch`. Validate `base_ref` (charset `[A-Za-z0-9._/-]`, no leading `-`, no `..`, <= 255; mirrors `check_ref_syntax` and `MAX_REF_LENGTH`).

---

### `agent/code_chunking.py` (utility, transform)

**Analog:** `agent/kb_chunking.py`.

Reuse the `ChunkDraft` dataclass (line 27; fields consumed by `_chunk_rows`: `text`, `section`, `page_start`, `char_start`, `char_end`) so output feeds `_chunk_rows` unchanged. Pure functions, unit-testable, same style as `chunk_fixed(text, size, overlap, page_offsets)` (line 73). Constraints from research that the analog does not enforce: every chunk <= 2000 chars including header (`embed_texts` raises above 2064), slice `ast` nodes by lines never by `col_offset`, fall back to line windows on `SyntaxError`/`ValueError`/`RecursionError`, `section` carries `name (L<start>-<end>)`. `ast` has no repo analog; use the snippet in RESEARCH "Python chunker core".

---

### `agent/review_pipeline.py` (service, transform)

**Analogs:** `agent/rag_pipeline.py::run_retrieval_pipeline` (lines 357-447) and `agent/rag_llm.py::_complete` (lines 46-71).

**Retrieval once per base** with its own `PipelineConfig` (lines 61-73):
```python
PipelineConfig(candidate_k=..., top_k=..., threshold=..., lexical=False, llm_rerank=False, hybrid=False, rewrite=False)
chunks, trace = await run_retrieval_pipeline(session, kb, query, config, client, model)
```
Docs quota (e.g. `top_k=4`) and code quota (`top_k=6`) separately; use an explicit low threshold for code (the calibrated `bge-m3` 0.67 is for prose, assumption A5). Cap query text at about 1500 chars. Failures arrive as `RagFailure(code, text)`; map to review error codes.

**Model call, reasoning off, retry on 400/422** (`rag_llm.py` 46-71): copy verbatim for review and merge calls, but use `max_tokens` for the answer and a review-specific client timeout (`LLMClient(base_url, api_key, timeout=...)`; `providers.build_client` does not take one):
```python
try:
    return await client.complete_chat_detailed(messages=messages, model=model, temperature=..., max_tokens=max_tokens,
                                               extra_body={"reasoning_effort": STAGE_REASONING_EFFORT})
except httpx.HTTPStatusError as exc:
    if exc.response.status_code not in REJECTED_STATUS_CODES:
        raise
return await client.complete_chat_detailed(..., extra_body=None)
```
Wrap every call in `async with limits.generation_gate.slot():` (`agent/limits.py` 178-222). Resolve provider and model with `providers.get_provider_row` / `resolve_client(user_id, provider_id)`. NO tools: do not use `run_headless_turn` / `_prepare`. Handle `content is None` and strip `<think>` (`THINK_RE` pattern in `scripts/rag_judge.py`). Render fragments with `rag.render_rag_block` / `_neutralize` and budget with `rag.build_rag_block`; token estimate with `CYRILLIC_SAFETY = 1.15`.

---

### `agent/review_prompts.py` (utility, transform)

**Analog:** `scripts/rag_judge.py` lines 101-153: tag-wrapped untrusted data with angle brackets neutralised.
```python
def _neutralise(text: str) -> str:
    """Replace angle brackets so inputs cannot close or open the prompt tags."""
    return text.replace("<", "‹").replace(">", "›")

parts = [f"<question>{_neutralise(question)}</question>", ...]
return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]
```
Apply to diff, code fragments and doc fragments; system prompt states that tagged content is data, and requires exactly the three Russian `###` headings.

---

### `shared/models.py` (model, CRUD)

**Analog:** `HelpConfig` (lines 781-812): per-user table with `user_id` as FK primary key and cascade via `sa_column`, nullable `kb_id` with `SET NULL`, `updated_at` default factory.
```python
user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True))
kb_id: int | None = Field(default=None, sa_column=Column(Integer, ForeignKey("knowledgebase.id", ondelete="SET NULL"), nullable=True))
updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```
`ReviewConfig`: `user_id` PK, `repo_path`, `docs_kb_id` (SET NULL; fall back to `HelpConfig.kb_id` when null), `provider_id`, `model`, `github_server_id`, `context_length`. Code-KB link table (`ReviewCodeIndex`: user_id, repo_key, base_ref, kb_id with CASCADE on `knowledgebase.id`, indexed_commit, updated_at) follows the `KbDocument` index/`__table_args__` style (lines 706). Code KB itself is a normal `KnowledgeBase` row (strategy `FIXED`; `KnowledgeBase` lines 670-695). Never use `Field(ondelete=...)`. Add new table names (`reviewconfig`, `reviewcodeindex`) to `tests/test_database.py::test_init_db_creates_all_tables`, as Phase 23 did for `helpconfig`.

---

### `agent/main.py` (wiring)

**Analog:** lines 88 and 474-482. Add `from agent.review_api import router as review_router` next to the `help_router` import and `app.include_router(review_router)` after `help_router`.

---

### `ui/static/index.html` + `ui/static/app.js` (component, request-response)

**Analog:** `#llm-providers-section` (index.html line 387: `class="border-t border-slate-700 p-4 space-y-4"`) and `#mcp-section` (line 342). UI-SPEC S1 prescribes `<section id="review-settings-section" class="border-t border-slate-700 p-4 space-y-4">`, `h3.text-lg.font-semibold` "Ревью Pull Request", `label.block.text-sm.text-slate-400.mb-1` + control, accent only on "Сохранить настройки ревью", status/error lines with `aria-live="polite"`. In `app.js` mirror the loader/render split of `loadMcpServers` (line 2135) and `replaceMcpServer`; server strings only via `textContent`, any HTML through `DOMPurify.sanitize()`. Use source-level UI tests like earlier phases.

---

### `scripts/pr_review_ci.py` (script, request-response)

**Analog:** `scripts/verify_service.py` lines 43-133: stdlib-only HTTP with a proxy-less opener, secret scrubber, `Reply` dataclass.
```python
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SECRETS: list[str] = []

def scrub(text: str) -> str:
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "[key]")
    return text

def call(method: str, url: str, key: str | None, body: Any, timeout: float) -> Reply:
    headers = {"Accept": "application/json"}
    ...
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response: ...
    except urllib.error.HTTPError as exc: ...
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        return Reply(None, {}, None, time.perf_counter() - start, f"{type(exc).__name__}: {exc}")
```
Reuse the same function for the GitHub REST comment calls (add `Authorization: Bearer <GITHUB_TOKEN>` and `X-GitHub-Api-Version`). Register API key and token in `_SECRETS` first. Also copy the `NOT_RUN = "не запускалось"` convention for the summary. Note: `print` is acceptable here because `verify_service.py` uses `say()` for CLI output; in the repo, `print` is banned only in app code. Read config only from env (`APP_URL`, `API_KEY`, `GITHUB_TOKEN`, `REPO`, `PR_NUMBER`, `HEAD_SHA`). Comment replace: match marker `<!-- ai-pr-review -->` AND `user.login == "github-actions[bot]"`. Make the pure functions (build comment, sanitize, truncate to < 65 536, map error code to the S3 line) importable for `tests/test_pr_review_ci.py`.

---

### `scripts/review_judge.py` (script, batch)

**Analog:** `scripts/rag_judge.py`. Copy: imports (lines 15-27: argparse, asyncio, csv, json, re, httpx), `_neutralise`, `build_*_messages`, `parse_judge_reply` with `THINK_RE` / `VERDICT_RE` / `REASON_RE` (156-171), `check_access` preflight (385-416) returning `(exit_code, message)` where a refused key reports only the HTTP status:
```python
async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as http:
    response = await http.get(f"{root}/v1/models", headers=headers)
if response.status_code in (401, 403):
    return _rejected(response.status_code)
...
except (httpx.ConnectError, httpx.TimeoutException):
    return EXIT_PREFLIGHT, "check: DeepSeek is unreachable"
```
Judge input: the recorded reference (planted defects) and the review text; output per defect found / not found plus count of false remarks. Key from env only, never printed.

---

### Tests

- `tests/test_review_api.py`: copy `tests/test_help_api.py` (fixtures `authenticated_client`, `client`; `seed_kb`; `_row` helper; defaults test; 404 foreign-KB; 422 validation parametrize; unauthenticated 401; cross-origin rejection) and `tests/test_http_chat.py` (API-key client built with `ASGITransport` + `_create_user`, `respx` mocking of `COMPLETIONS`, `wait_until`, response-key set assertion, busy 409, error-code mapping).
- `tests/test_code_chunking.py`: `tests/test_kb_chunking.py`; add a 5 000-line function, Cyrillic source, syntax-error fallback, decorators.
- `tests/test_code_index.py`: `tests/test_kb_indexer.py` for index/commit assertions; use a temp git repo built with `git` subprocess.
- `tests/test_review_github.py`: `tests/test_mcp_tools.py` with the fixture server `tests/fixtures/mcp_stdio_server.py` (as `test_help_api.py` line 17); real-binary check modelled on `tests/test_mcp_real_github.py` (skip when absent).
- `tests/test_pr_review_ci.py`: `tests/test_verify_service.py`.
- Check `docs/TESTING_GUIDE.md` before finalizing coverage lists.

---

## Shared Patterns

### API-key authentication and origin/content-type guards
**Source:** `agent/dependencies.py` (`get_current_user`, `get_session_user`, `require_allowed_origin`, `require_json_content_type`)
**Apply to:** `agent/review_api.py` (review route: Bearer allowed; settings/refresh routes: session-only).

### Error body that keeps `code`
**Source:** `agent/http_chat.py` 39-59
**Apply to:** every non-200 path of the review route; fixed text for 500, never exception text.

### Ownership scoping by `user_id`
**Source:** `agent/help_api.py` 75-80, 157-160 (`_get_owned_kb`, show a KB only when owned)
**Apply to:** `ReviewConfig`, docs KB, code KB, GitHub server row lookup (`mcp_config.list_servers(session, user_id)`).

### DB write with rollback
**Source:** `agent/help_api.py` 173-190 and `kb_indexer.py::_update_kb` 101-119
```python
try:
    ...
    await session.commit()
except SQLAlchemyError:
    await session.rollback()
    raise
```
**Apply to:** all writes in review modules.

### Logging
**Source:** every module: `logger = get_logger(__name__)`; `snake_case_action` + key=value (`logger.info("help_config_updated", user_id=..., changed=...)`). Log ids, counts and sizes only; never diff text, code, key, token, or exception tracebacks (`error=str(exc)` / `type(exc).__name__`).

### Untrusted-data handling
**Source:** `scripts/rag_judge.py::_neutralise`, `agent/rag.py::_neutralize`
**Apply to:** `review_prompts.py`, `review_pipeline.py`; plus output sanitising (strip `@mentions`, Markdown images, raw HTML, cap length) before the response is returned.

### Generation queue
**Source:** `agent/limits.py` `generation_gate.slot()` (178-222)
**Apply to:** every model call in `review_pipeline.py`.

### Conventions (derivation skipped)
The `gsd-tools.cjs verify conventions --derive` module could not be run (plugin root not resolvable in this shell: `Cannot find module ...\gsd-tools.cjs`), so no derived table is given. Observed conventions from CLAUDE.md and the files read: snake_case modules, `PascalCase` classes, `UPPER_CASE` constants, `_private` helpers, single-line module docstrings, `str | None` unions, direct module-path imports (no barrels), stdlib -> third-party -> local import order. Contested hotspot (author's choice): the CJS/SDK dual resolver (`bin/lib/**` CJS vs `sdk/src/**` ESM) is internally consistent per directory and contested only repo-wide; this phase touches Python only, so match the directory's local style.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `.github/workflows/pr-review.yml` | CI config | event-driven | `.github/` does not exist; use RESEARCH Pattern 6 (SHA-pinned `actions/checkout`, `permissions`, `concurrency`, `if` same-repo guard, secrets via `env:`) |
| `agent/code_chunking.py` AST splitting core | utility | transform | No `ast` usage in the repo; use RESEARCH "Python chunker core" |
| `agent/code_index.py` git-object reading (`ls-tree`, `cat-file --batch`, `fetch`) | service | file-I/O | `project_git.py` has the exec scaffold only; the commands are new |

## Metadata

**Analog search scope:** `agent/`, `shared/`, `mcp_servers/`, `scripts/`, `tests/`, `ui/static/`
**Files read:** `http_chat.py`, `help_api.py`, `kb_indexer.py`, `shared/models.py` (KB and HelpConfig regions), `project_git.py` (git exec region), `rag_pipeline.py`, `rag_llm.py`, `mcp_tools.py`, `mcp_client.py`, `scripts/rag_judge.py`, `scripts/verify_service.py`, `tests/test_help_api.py`, `tests/test_http_chat.py` (head), CONTEXT, RESEARCH, UI-SPEC
**Pattern extraction date:** 2026-10-13
