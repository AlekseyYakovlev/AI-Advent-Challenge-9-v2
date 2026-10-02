# Phase 12: LLM providers section in Settings (Day 21) - Research

**Researched:** 2026-10-02
**Domain:** Per-provider routing of OpenAI-compatible LLM calls in a FastAPI/SQLModel agent + vanilla-JS Settings UI
**Confidence:** HIGH on codebase integration points (all verified by reading/grepping the repo); MEDIUM on external provider behaviour (DeepSeek `/v1/models`)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** A provider has: **name, base URL, API key reference, enabled flag**. Generic OpenAI-compatible provider (DeepSeek, OpenAI, OpenRouter, ...). New user-scoped table (by `user_id`), like `McpServerConfig`.
- **D-02:** The API key is stored as a **reference to a `.env` variable name** (e.g. `DEEPSEEK_API_KEY`), never the key itself. The Agent resolves the value from the environment/`shared.config` at call time. The key never touches the DB or any API response. Providers without a key (LM Studio) leave the reference empty.
- **D-03:** **Auto-seed a DeepSeek provider** (name "DeepSeek", `https://api.deepseek.com`, key ref `DEEPSEEK_API_KEY`) when `.env` has the key. Seeding is idempotent and per user (no duplicates on restart).
- **D-04:** The env-var name must be a settable setting (so `shared/config.py` must expose or look up arbitrary env names safely); the planner decides how to resolve names (e.g. `os.environ`/dotenv lookup) and must reject empty/invalid names.
- **D-05:** **LM Studio is a regular provider row** (seeded, editable, base URL `LM_STUDIO_BASE_URL`, no key). The LM Studio-specific load/unload control API and "loaded ✓" marks apply only to the provider recognized as LM Studio (planner decides the discriminator, e.g. a `kind` field or base-URL match); the confirm-to-load flow in `onModelSelect` is preserved for it.
- **D-06:** Model identity uses a **separate `provider_id` field** next to the model string (not a composite id). It must be threaded through: WS chat payload, per-chat/global Settings, scheduled job rows (D-03 of Phase 8 stored only a model id), and the title call. Existing rows with no `provider_id` must keep working and resolve to the seeded LM Studio provider (migration/back-compat, planner designs it).
- **D-07:** **All LLM flows route through the selected provider:** chat streaming (`agent/ws.py`), auto-title (`agent/titles.py`), scheduler headless runs (`agent/headless.py`), and summaries / fact extraction / invariant self-critique (`agent/context_engine.py`, `agent/invariants.py`). The module-level `llm_client` singleton is replaced by a resolver returning a client per (user, provider) — token counting (`count_tokens`, tiktoken) stays provider-independent.
- **D-08:** The check is **`GET {base_url}/v1/models` with the resolved key**. Success = HTTP 200 with a model list; errors are classified into readable messages (bad key 401/403, unreachable, timeout, other HTTP). It runs **automatically after a provider is saved** and via a **manual "Проверить" button** per provider. Result shown as a status indicator (success/error badge + message) in the provider row, in the same visual vocabulary as MCP status badges.
- **D-09:** The model list is **fetched live and cached in memory** (per process): refreshed on page load, after save/check, and via manual re-check. A failing provider is skipped with a small non-blocking warning, never a blocking error for the whole picker.
- **D-10:** Picker entries are **"Provider · model", grouped by provider**. Unreachable or disabled providers contribute **no entries**. LM Studio entries keep the `✓` loaded mark.
- **D-11:** **Deleting/disabling a provider is always allowed.** Chats/jobs still referencing it fail with a clear "provider unavailable" error; scheduled runs end `failed` (consistent with Phase 8 D-03: no fallback). The chat UI falls back to the first available picker entry.
- **D-12:** Everything is **user-scoped** (`user_id`); REST under `/api/v1/llm-providers` (planner picks exact shape) with session auth; foreign ids → 404. UI labels in Russian.

### Claude's Discretion
Table/column names, `kind` discriminator, exact REST/response shapes, picker value encoding (`<option value>`), cache TTL/invalidations, error-message wording, Tailwind markup, empty-state copy, and test plan (success + failure checks via `respx`). How the 999.11 DeepSeek UAT is folded in (e.g. a documented manual check or an opt-in live test).

### Deferred Ideas (OUT OF SCOPE)
- API-key encryption at rest / key entry in UI — not needed with env-var references.
- Per-provider usage/cost stats — future phase.
- Non-OpenAI-compatible providers — future phase.
</user_constraints>

<phase_requirements>
## Phase Requirements

Roadmap lists requirements as "TBD". The planner must add the following to `.planning/REQUIREMENTS.md` (proposed IDs, `[ASSUMED]` wording — confirm names with the user only if desired):

| ID | Description | Research Support |
|----|-------------|------------------|
| PROV-01 | User can add/edit/delete/enable LLM providers (name, base URL, `.env` key-variable name) in Settings; data is `user_id`-scoped, foreign ids 404, key value never stored or returned | Data model + CRUD pattern (McpServerConfig/mcp_config.py), env resolver |
| PROV-02 | DeepSeek and LM Studio are auto-seeded as regular providers, idempotently, without resurrecting deleted ones | Seeding section (tombstone table) |
| PROV-03 | After save and on demand, a connection check (`GET /v1/models`) runs and shows an ok/error badge with a classified Russian message | Check service + error taxonomy |
| PROV-04 | Provider models are fetched live, cached in memory, and shown in the picker as "Provider · model" optgroups; unreachable/disabled providers add no entries | Models endpoint + frontend picker refactor |
| PROV-05 | Chat WS, auto-title, headless scheduler runs, fact extraction and invariant self-critique all route through the selected provider via `provider_id` (legacy rows resolve to LM Studio) | Resolver + call-site inventory |
| PROV-06 | Deleted/disabled provider yields a clear "provider unavailable" error (WS error frame; scheduled run `failed`); UI falls back to first picker entry | D-11 handling section |
| PROV-07 | DeepSeek title call verified through the new routing (closes backlog 999.11) | 999.11 section |
</phase_requirements>

## Summary

Today the Agent has exactly one `LLMClient` (`agent/llm_client.py:392`) bound to `LM_STUDIO_BASE_URL` with `DEEPSEEK_API_KEY` as bearer; the `payload.model` string only changes the JSON `model` field, never the HTTP target. So DeepSeek has never actually been reachable through the app. The phase replaces that singleton with a per-(user, provider) resolver. `LLMClient` already takes `base_url`/`api_key` per instance, so the client itself needs no change; the work is (a) a new `LlmProvider` table + CRUD + seeding + env-secret resolver + connection-check/model-list service (`agent/providers.py` or similar), (b) threading `provider_id` through ~6 `stream_chat` call sites in `agent/ws.py`, the title job, fact extraction, invariant self-critique, scheduler task rows and the headless runner, and (c) a frontend refactor of the model picker (currently flat `state.models` of LM Studio models, also reused by the scheduler's `#scheduler-model` select) plus a new Settings section cloned from `#mcp-section`.

Three facts in CONTEXT.md do not match the code and change the plan: (1) **`Settings` has no `model` column** (grep of `shared/models.py`: `model` exists only on `ScheduledTask`/`TaskRun`). The model is client-side state (`state.selectedModel`) sent in each WS payload; so "thread `provider_id` through per-chat/global Settings" is a no-op — do NOT add it. (2) `summarize_if_needed` in `agent/context_engine.py` is a stub that never calls the LLM, so "summaries" has no call site; the real LLM call sites in `context_engine.py` are `_extract_facts` (via `extract_and_update_facts`) and token counting. (3) `shared/config.py` has `extra="ignore"` and pydantic-settings does **not** export `.env` entries into `os.environ`, so `os.environ.get("MY_KEY")` will NOT see a key that lives only in `.env`; the resolver must read the `.env` file via `dotenv_values` (python-dotenv is a hard dependency of pydantic-settings) in addition to `os.environ`.

**Primary recommendation:** Add one new module `agent/providers.py` (resolver `get_client(session, user_id, provider_id)`, `ProviderUnavailableError`, check/list service, in-memory model cache, lazy `ensure_seeded`), keep `agent.llm_client.llm_client` only as a token-counting alias to avoid churning ~8 test files, resolve the provider once per WS turn **before** persisting the user message, and store `ScheduledTask.provider_id` as a plain INTEGER with **no FK / no SET NULL** so a deleted provider can never silently degrade into the legacy "NULL means LM Studio" fallback.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Provider CRUD, ownership (`user_id`, 404 on foreign) | API / Backend (Agent) | Database | Same as MCP servers; session-cookie auth via `get_current_user` |
| API-key resolution from `.env` name | API / Backend | — | Key must never reach DB rows' API output or the browser |
| Connection check + live model list + cache | API / Backend | — | Browser cannot call providers directly (CORS, secrets) |
| Seeding DeepSeek / LM Studio | API / Backend | Database | Lazy per-user, needs DB tombstone |
| Provider routing for chat/title/headless/facts/critique | API / Backend | — | Resolver at each call site |
| LM Studio load/unload + `loaded` mark | API / Backend (existing `LMStudioClient`) | Browser (confirm dialog) | Control API is local; isolate per base URL |
| Settings section UI, status badges | Browser / Client (vanilla JS) | — | Clone of MCP section; DOM via `textContent` |
| Model picker (optgroups, selection persistence) | Browser / Client | API (models endpoint) | `state.selectedModel` + new `state.selectedProviderId` live client-side |
| Persisting model/provider per scheduled job | Database | API | New nullable `provider_id` column |

## Standard Stack

No new packages. Everything uses what is already installed and pinned/used in the repo.

### Core
| Library | Version (installed) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.141.1 [VERIFIED: pip list] | REST endpoints under `/api/v1/llm-providers` | Existing app framework |
| SQLModel | 0.0.42 [VERIFIED: pip list] | `LlmProvider` table, `sa_column` FK cascade | Project convention (CLAUDE.md) |
| httpx | 0.28.1 [VERIFIED: pip list] | `GET /v1/models` check; error classification via `ConnectError`/`TimeoutException`/`HTTPStatusError` | Already used by `LLMClient`; does not follow redirects by default (so bearer is not forwarded to a redirect target) [ASSUMED: httpx default `follow_redirects=False`; confirm by not passing the flag] |
| python-dotenv | 1.2.3 [VERIFIED: pip list] | `dotenv_values(".env")` to read the key variable that pydantic-settings does not export | Hard dependency of pydantic-settings (`pip show pydantic-settings` -> Requires python-dotenv) |
| respx | 0.23.1 [VERIFIED: pip list] | Mock `GET {base}/v1/models` and chat completions in tests | Project test convention |
| tiktoken | existing | Provider-independent `count_tokens` | CONTEXT D-07 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `dotenv_values` | Add a `model_config extra="allow"` to `Settings` and `getattr` | `extra="allow"` loads only vars present at import time and lets arbitrary names onto the settings object; `dotenv_values` is explicit and re-readable. Use `dotenv_values`. |
| Tombstone table for seeding | Marker column on `User` | Column needs an `ALTER TABLE` migration; a new table is created by `create_all` with zero migration code. Either works; table recommended. |

**Installation:** none. **Version verification:** `pip list` run in this session (above). No external packages are recommended, so the Package Legitimacy Gate is not applicable.

## Package Legitimacy Audit

No new external packages are installed by this phase (python-dotenv is already present as a transitive dependency of pydantic-settings; import it as `from dotenv import dotenv_values`). **Packages removed:** none. **Packages flagged [SUS]:** none. Optional hygiene: add `python-dotenv` explicitly to `requirements.txt` since the code will import it directly (planner discretion; it is already resolved by pydantic-settings).

## Architecture Patterns

### System Architecture Diagram

```
Browser (Settings modal)                       Browser (header #model-select, #scheduler-model)
  "+ Добавить провайдера" form                   GET /api/v1/llm-providers/models?refresh=1
  POST/PUT/DELETE /api/v1/llm-providers          options "{id}::{model}" in <optgroup>
  POST /api/v1/llm-providers/{id}/check                |
        |                                               |
        v                                               v
+------------------------- Agent (FastAPI, :8001) -----------------------------+
| llm_providers router (get_current_user, 404 on foreign id)                   |
|   CRUD ---> agent/providers.py ---> LlmProvider table (user_id, cascade)     |
|   check ---> resolve_secret(api_key_env) --+--> os.environ / .env file       |
|            httpx GET {base}/v1/models  ---> external provider / LM Studio    |
|            classify error -> {status, message, model_count}                  |
|            store in MODEL_CACHE[(user_id, provider_id)]                      |
|                                                                              |
| Chat turn:  WS payload {content, model, provider_id?}                        |
|   _handle_chat_message: chat -> resolve client (BEFORE persisting user msg)  |
|     fail -> {"type":"error","code":"PROVIDER_UNAVAILABLE"}  (no orphan msg)  |
|     ok   -> client.stream_chat(...)  x6 call sites in ws.py (via _ToolTurn)  |
|          -> run_self_critique(client), extract_and_update_facts(provider_id) |
|          -> schedule_title_generation(provider_id)  (resolves own client)    |
| Scheduler: ScheduledTask.provider_id -> headless._ToolTurn.client            |
|   missing/disabled -> run FAILED "Провайдер недоступен (удалён или отключён)"|
| LM Studio load/unload -> LMStudioClient registry keyed by normalized base_url|
+------------------------------------------------------------------------------+
```

### Recommended Project Structure
```
agent/
├── providers.py          # NEW: CRUD, ensure_seeded, resolve_secret, get_client, check_provider, list_models, cache
├── providers_api.py      # NEW (or inline in main.py): APIRouter /api/v1/llm-providers
├── schemas.py            # + LlmProviderCreate/Update/Response/CheckResult/ModelGroup; MessagePayload.provider_id
├── llm_client.py         # keep LLMClient/LMStudioClient; llm_client singleton -> token-count alias only
shared/
├── models.py             # + LlmProvider, LlmProviderSeed (tombstone); ScheduledTask.provider_id
├── database.py           # + migrate_add_scheduledtask_provider_id
├── config.py             # + LLM_PROVIDER_CHECK_TIMEOUT; resolve_env_secret()
ui/static/
├── index.html            # + <section id="llm-providers-section"> after #mcp-section
└── app.js                # + providers block; refactor loadModels/populateModelSelect/onModelSelect/scheduler select
tests/
├── test_llm_providers_api.py, test_llm_providers_check.py, test_llm_providers_routing.py (new)
```

### Pattern 1: User-scoped CRUD (clone McpServerConfig)
Model: `user_id` via `sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)`; `get_x(session, user_id, id)` returns None for foreign owner -> endpoint raises 404 (never 403); commit/rollback try/except; `logger.info("llm_provider_created", ...)`. [VERIFIED: agent/mcp_config.py, shared/models.py:553]

Suggested columns: `id, user_id, name(100), base_url(500), kind (str: "openai"|"lm_studio"), api_key_env (nullable, 100), enabled, created_at, updated_at`. Add `UniqueConstraint("user_id", "name")` only if duplicate names are unwanted; picker prefix ambiguity argues for it (return 409). `kind` is the D-05 discriminator (explicit field beats base-URL matching; seeded LM Studio gets `kind="lm_studio"`; user-added providers default to `"openai"`; allow the form to stay as in UI-SPEC, no kind field in the UI - planner may add a hidden default).

### Pattern 2: Env-secret resolver (D-02/D-04)
```python
# shared/config.py  (new)
import os, re
from dotenv import dotenv_values

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")

def is_valid_env_name(name: str) -> bool:
    return bool(_ENV_NAME_RE.fullmatch(name))

def resolve_env_secret(name: str | None) -> str | None:
    """Return the value of a named variable from the process env, then the .env file; None if unset/empty."""
    if not name or not is_valid_env_name(name):
        return None
    value = os.environ.get(name)           # real env wins, same precedence as pydantic-settings
    if value is None:
        value = dotenv_values(".env").get(name)
    return value or None
```
Never log the value; never include it in exceptions. "Not found" => check result `error_code="env_missing"` (UI-SPEC copy: «Переменная {ENV_NAME} не найдена в .env...»). Note the Agent is started by the supervisor with CWD = repo root (same assumption pydantic-settings already makes with `env_file=".env"`). Because `.env` is re-read per call, adding the key does not require restart if read at call time (the UI-SPEC copy says "перезапустите приложение" — harmless, but with `dotenv_values` a restart is not needed; planner may soften the copy). `[ASSUMED]` that reading `.env` each call is acceptable cost (tiny file).

### Pattern 3: Resolver + back-compat (D-06)
```python
class ProviderUnavailableError(Exception):
    def __init__(self, name: str | None): ...   # message: Провайдер «{name}» недоступен...

async def get_provider_row(session, user_id, provider_id: int | None) -> LlmProvider:
    await ensure_seeded(session, user_id)                      # lazy, per user, idempotent
    if provider_id is None:                                    # legacy payload / legacy job row
        row = first enabled row with kind == "lm_studio" for user (lowest id)
    else:
        row = await get_provider(session, user_id, provider_id)
    if row is None or not row.enabled:
        raise ProviderUnavailableError(row.name if row else None)
    return row

def build_client(row) -> LLMClient:
    return LLMClient(base_url=row.base_url, api_key=resolve_env_secret(row.api_key_env) or "")
```
- `LLMClient.__init__` calls `tiktoken.get_encoding("cl100k_base")` each time; tiktoken caches the encoding object internally so construction is cheap, but a small `functools.lru_cache`d `_encoding` at module level is trivial and avoids repeated lookups. Token counting: expose `count_tokens(text)` module function in `agent/llm_client.py`; keep `llm_client = LLMClient(base_url=settings.LM_STUDIO_BASE_URL)` ONLY so `llm_client.count_tokens(...)` in ~8 test files and `context_engine.py` keep working. Replace the production uses of `llm_client.stream_chat/complete_chat*` only.
- **Lazy seeding is required, not optional**: ~20 existing WS tests create users directly (`_create_user` in conftest) and send `{content, model}` with respx mocks on `{LM_STUDIO_BASE_URL}/v1/chat/completions`. If the resolver calls `ensure_seeded` (LM Studio row with `base_url = settings.LM_STUDIO_BASE_URL`), those tests keep passing with no `provider_id`. Seeding only in `lifespan`/`create_user` would break them.
- Chats with `chat.user_id is None` (legacy "unowned chat" path exists in ws.py:729): fall back to a settings-built client (LM Studio URL + empty key) so they keep working.

### Pattern 4: Seeding without resurrection (D-03 vs D-11)
D-11 allows deleting the seeded providers; "idempotent seeding" must therefore NOT re-create a deleted row on the next restart/list. Use a tombstone table `LlmProviderSeed(user_id FK cascade, seed_key str, PK(user_id, seed_key))`. `ensure_seeded`: for each of `lm_studio` (always) and `deepseek` (only if `resolve_env_secret("DEEPSEEK_API_KEY")` truthy): if no marker, insert provider row + marker in ONE commit. Race: two concurrent requests seeding the same user -> second insert hits the PK on the marker -> catch `IntegrityError`, rollback, ignore. A per-user `asyncio.Lock` dict is also fine. Cheap fast path: one `SELECT` of markers per call; cache "seeded users" in a process set to skip it.
If the DeepSeek key is absent at first seed, no marker is written, so adding the key later seeds DeepSeek on the next call (matches D-03 "when `.env` has the key").
`[ASSUMED]` The tombstone design is a recommendation; CONTEXT only says "idempotent and per user".

### Pattern 5: Connection check + live models (D-08/D-09)
```python
async def check_provider(row: LlmProvider) -> CheckResult:
    base = normalize_base_url(row.base_url)          # strip trailing "/" and a trailing "/v1"
    headers = {}
    if row.api_key_env:
        key = resolve_env_secret(row.api_key_env)
        if key is None: return error("env_missing", env_name=row.api_key_env)
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=settings.LLM_PROVIDER_CHECK_TIMEOUT) as c:
            r = await c.get(f"{base}/v1/models", headers=headers)
        r.raise_for_status()
        data = r.json().get("data")
        if not isinstance(data, list): -> error("bad_response")
    except httpx.ConnectError: "unreachable"
    except httpx.TimeoutException: "timeout"
    except httpx.HTTPStatusError as e: 401/403 -> "bad_key"; else "http" (status)
    except (ValueError, httpx.HTTPError): "bad_response"/"unreachable"
```
- Use a **short dedicated timeout** (new setting `LLM_PROVIDER_CHECK_TIMEOUT`, e.g. 10 s). `settings.LLM_TIMEOUT` is 60 s, far too long for something that gates the picker. For the picker, check all enabled providers concurrently (`asyncio.gather(..., return_exceptions=False)` over per-provider functions that never raise) so one hung cloud provider does not delay LM Studio entries.
- For `kind == "lm_studio"`, reuse `LMStudioClient.list_models()` semantic (it returns the raw `data` list which the existing UI reads `.loaded` from) instead of a bare httpx call, so the `✓ loaded` mark keeps working.
- **Base-URL normalization pitfall:** `LLMClient` always posts to `{base_url}/v1/chat/completions` and the check uses `{base}/v1/models`. OpenAI (`https://api.openai.com/v1`) and OpenRouter (`https://openrouter.ai/api/v1`) base URLs are conventionally given WITH `/v1`; stored as-is they would produce `/v1/v1/...`. Normalize on save (strip trailing slashes and one trailing `/v1`) and show the normalized value back. DeepSeek docs give base URL `https://api.deepseek.com` and list models at `GET /models` [CITED: api-docs.deepseek.com/api/list-models]; the `/v1` prefix is documented as an OpenAI-SDK compatibility alias with no relation to model version [CITED: community summaries of api-docs.deepseek.com, MEDIUM] — the app's existing completions path already depends on `/v1/chat/completions`, so `{base}/v1/models` is consistent; verify live in the 999.11 UAT.
- **Cache:** module dict `MODEL_CACHE: dict[tuple[int,int], CacheEntry(status, message, models, checked_at)]`. Invalidate on PUT/DELETE of that provider, populate on every check. `GET /api/v1/llm-providers` returns last cached status (or `not_checked`). `GET /api/v1/llm-providers/models?refresh=true` re-fetches all enabled providers (page load / manual / after save); without `refresh` it may serve the cache. No TTL needed beyond "refresh on page load" (D-09); an optional 60 s TTL is Claude's discretion. Clear cache entries on user deletion is unnecessary (no delete-user endpoint seen). Add a clear in `tests/conftest.py::clean_test_db` (same reason `title_tasks` etc. are cleared there).

### Pattern 6: Threading `provider_id` (call-site inventory, verified by grep)
| Site | File:line | Change |
|------|-----------|--------|
| WS payload | `agent/schemas.py:290 MessagePayload` | add `provider_id: int | None = None` (None = legacy -> LM Studio) |
| Resolve once per turn | `agent/ws.py:_handle_chat_message` (L670) | after loading `chat`, **before** `_persist_user_message` (L688): `client = await get_client(...)`; on `ProviderUnavailableError` send `{"type":"error","detail":..., "code":"PROVIDER_UNAVAILABLE"}` and return. (Other failure paths there delete the user message afterward; resolving first avoids creating it at all.) |
| `stream_chat` x6 | ws.py L315 (`_stream_action_claim_retry`, takes `payload`, no turn), L392 (`_stream_follow_up`, has `turn`), L637 (rejection explanation, has `turn`), L755, L775 (main stream), L904 (justify/retract) | add `client: LLMClient` field to `_ToolTurn` (L336); pass `client` as a parameter to `_stream_action_claim_retry` and the two inline sites |
| Self-critique | `agent/invariants.py:299`, called at ws.py:887 | add `client` param to `run_self_critique` (keeps fail-open behaviour) |
| Facts | `context_engine.py:645 _extract_facts`, `extract_and_update_facts` (called ws.py:953), `_run_debounced_facts` | debounced background task opens its own session: pass `user_id`+`provider_id`, resolve inside `_run_debounced_facts`; failures already swallowed (`facts_extraction_failed`) |
| Title | `titles.py:158 _complete_title`, `request_title`, `generate_and_apply_title`, `schedule_title_generation` (called ws.py:963) | add `provider_id`; resolve own client in the background task via `async_session_factory()`; `user_id` is already a parameter |
| Token counting | `context_engine.py` (7 sites), `ws.py:192,217` | switch to module `count_tokens` (provider-independent, D-07) |
| scheduler_tools | `agent/state.py:20 current_chat_model` ContextVar, `scheduler_tools.py:65` | add sibling `current_chat_provider_id` ContextVar set at ws.py:676; `_schedule_task` passes it to `create_scheduled_task` |
| ScheduledTask | `shared/models.py:435` + `scheduler_ops.create_scheduled_task`, `scheduler_schemas.py` (`ScheduledTaskOut`, create body), `scheduler.py:341` (`run_headless_turn(run_session, user_id, prompt, model)`), `headless.py:222` | add `provider_id: int | None` column (see Pitfall 2), pass to `run_headless_turn`, build `_ToolTurn(client=...)` there; `TaskRun.model` stays as-is (optionally add `provider_name` display, not required) |
| Headless errors | `headless.py:163 _map_llm_error` | message currently hard-codes "LM Studio не запущен"; use provider name (`Модель недоступна: сервер «{name}» не отвечает`); add `ProviderUnavailableError` -> `HeadlessRunError("Провайдер недоступен (удалён или отключён)")` (UI-SPEC copy). Resolve before `_prepare` so the run fails fast. |

`stream_chat` failures already surface as `{"type":"error","code":"LLM_ERROR","detail":"LLM error: ..."}` and delete the user message (ws.py:783-800). With remote providers a 401/402/429 will show `LLM error: Client error '401 Unauthorized' for url 'https://...'`. Recommend a small mapper for `httpx.HTTPStatusError` 401/403 in this except-block to a friendlier Russian message mentioning the provider; str(exc) from httpx contains the URL but not headers (no key leak). Do not log `exc.request.headers`.

### Pattern 7: LM Studio control per provider (D-05)
`agent/main.py:95 lm_studio_client = LMStudioClient()` is global with `model_switch_lock` and `_current_loaded_model`. Keep the existing `/api/v1/lm-studio/models|load-model|unload-model` routes working, and make the instance lookup go through `get_lm_studio_client(base_url)` — a dict keyed by **normalized base URL, not by user**, so two users (or two provider rows) pointing at one LM Studio share ONE lock and one loaded-model state. Add optional `provider_id` (query/body) to the load/unload endpoints, resolved with ownership check + `kind == "lm_studio"` else 400. `ModelLoadRequest` (schemas.py:311) gains `provider_id: int | None = None`.

### Pattern 8: Frontend refactor (UI-SPEC is the visual contract; this is the wiring)
- Replace `state.models` (flat LM Studio list) by `state.modelGroups = [{provider_id, name, kind, status, models:[{id, loaded}]}]` from `GET /api/v1/llm-providers/models`. Consumers to update (grep-verified): `loadModels`, `populateModelSelect`, `refreshModelSelector`, `onModelSelect`, `populateSchedulerModelSelect` (app.js:2250, uses `state.models`/`state.selectedModel`), the `model_loaded`/`model_event` WS handler (L1288), `sendMessage` (L1332 must send `provider_id`), scheduler create body (L2327-2329 must send `provider_id`), `trySendPending` (needs `selectedProviderId` too).
- Option value encoding: `${providerId}::${modelId}`, parse by splitting at the FIRST `::` only (model ids can contain `/`, `:` and even `::`; provider id is an integer). Alternatively keep `value` an index and store entries in an array (safest). `[ASSUMED]` either is fine.
- Existing bug to fix while here: `populateModelSelect` always overrides the selection with the first loaded model (L1397-1404), so any refresh discards the user's choice. UI-SPEC requires "previously selected (if still present) > first loaded LM Studio model > first entry".
- `onModelSelect` currently refetches LM Studio models and prompts load only when `!model.loaded`; keep that flow but only for entries whose group `kind === 'lm_studio'`; other providers set `selectedModel`/`selectedProviderId` immediately. Also reset `state.contextWindow = null` as today.
- Settings: `openSettingsModal` (L2684) already calls `loadMcpServers()`; add `loadLlmProviders()`. Build rows with `mcpEl(...)`/`textContent` only (provider name, URL, messages are user/remote-controlled -> XSS surface). Reuse `MCP_NEUTRAL_BTN_CLASSES`; define `LLM_PROVIDER_STATUS_*` maps (UI-SPEC). Disabled-while-checking via `state.llmProviderChecking = new Set()`.
- Save flow: POST/PUT -> close form -> toast -> `POST /{id}/check` -> re-render -> refresh model picker. Provider-related picker toasts: one non-blocking toast per load for failed providers (use the `error` field of each group), not blocking the select.
- `tests/test_static_js_syntax.py` is the only automated JS check (brace/paren/string structural scan). Run it after JS edits; behaviour must be verified via Playwright on an isolated copy at ports 18000/18001 (project memory: never touch 8000/8001).

### Anti-Patterns to Avoid
- **FK `ondelete="SET NULL"` on `ScheduledTask.provider_id`:** a deleted provider would turn into NULL = "legacy -> LM Studio", silently running a DeepSeek job on a local model, contradicting D-11 ("no fallback"). Use a plain nullable INTEGER, no FK; a dangling id is "provider unavailable".
- **Seeding on every `list` without a tombstone:** resurrects deleted providers.
- **Reading keys with `os.environ.get` only:** misses `.env`-only keys (the normal setup here).
- **Returning the key or `api_key_env`-resolved value anywhere** (responses, logs, errors). Returning the variable NAME is fine and required by UI-SPEC.
- **Using `settings.LLM_TIMEOUT` (60 s) for checks/model lists.**
- **`innerHTML` with provider strings.**
- **Cached module-level provider clients that bake in an old key/base URL** — build the client per resolve (cheap) or key any cache by `(base_url, key_hash)`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| `.env` parsing | custom file parser | `dotenv_values(".env")` | quoting/comments/BOM edge cases; already a dependency |
| User-scoped CRUD + status badges | new patterns | clone `agent/mcp_config.py`, `McpServerConfig`, `renderMcpServerRow`, `MCP_STATUS_*` | consistent conventions; UI-SPEC says no new visual language |
| Idempotent migrations | ad-hoc SQL at runtime | `migrate_add_*` helper pattern in `shared/database.py` (`PRAGMA table_info` guard) | existing, tested pattern |
| HTTP error classification | string matching on messages | `httpx.ConnectError / TimeoutException / HTTPStatusError(.response.status_code)` | same classes used in `main.py` and `headless.py` |
| Token counting | per-provider tokenizers | existing tiktoken `cl100k_base` | CONTEXT D-07 |
| Mocking providers in tests | live calls | `respx` | project convention; paid DeepSeek call only as opt-in UAT |

## Runtime State Inventory

This is an additive feature, but it changes how existing persisted/runtime state is interpreted, so the categories are answered explicitly:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `scheduledtask` rows store only `model` (no provider). Existing dev DB `app.db` has users, chats, jobs. No `Settings.model` exists. | Add `provider_id INTEGER NULL` via idempotent `migrate_add_scheduledtask_provider_id` (call from `init_db` before `create_all`). Existing rows keep NULL = legacy -> seeded LM Studio. New tables `llmprovider`, `llmproviderseed` are created by `create_all`. Data seeding is lazy (no backfill job). |
| Live service config | LM Studio at `LM_STUDIO_BASE_URL`; DeepSeek key in `.env` (`DEEPSEEK_API_KEY` present, non-empty in the dev `.env` per a boolean-only check) | None beyond seeding reads these at seed time. Note: seeded LM Studio `base_url` is a snapshot of `LM_STUDIO_BASE_URL` at first seed; later `.env` edits do not update it (user edits the row in UI). |
| OS-registered state | None — verified: no Task Scheduler/pm2 entries are involved; supervisor spawns the Agent as a subprocess | None |
| Secrets/env vars | `DEEPSEEK_API_KEY` stays as is; new provider rows reference arbitrary names | Code must not require the name to be a declared `Settings` field (pydantic `extra="ignore"` drops others) |
| Build artifacts | None | None |

## Common Pitfalls

### Pitfall 1: Stale model after provider deletion/disable
**What goes wrong:** Browser keeps `selectedProviderId` of a deleted provider; next message errors.
**How to avoid:** After delete/disable/check, refresh the picker; if previous selection absent, fall back to first entry and show `Провайдер недоступен. Выбрана другая модель.` (UI-SPEC). Server side still returns `PROVIDER_UNAVAILABLE` for a race.

### Pitfall 2: NULL-means-legacy collides with deletion
See Anti-Patterns. Also applies to `provider_id=None` in WS payload: that means "client did not send one" (old tabs/tests) -> LM Studio; the new frontend must always send it.

### Pitfall 3: Existing tests that patch `llm_client`
`tests/test_titles.py` monkeypatches `titles.llm_client.complete_chat_detailed` (~15 sites) and `test_titles_ws.py:436` patches `"agent.titles.llm_client.complete_chat_detailed"`. After routing, titles no longer use that singleton. Plan an explicit test-migration task: introduce a seam (e.g. `agent.titles._get_client(user_id, provider_id)` or patch `agent.providers.get_client`) and update those fixtures. Token-counting imports in 8 test files keep working if the `llm_client` alias remains.

### Pitfall 4: Double `/v1`
See base-URL normalization. Add a test: base `https://x/v1/` is stored as `https://x`.

### Pitfall 5: One slow provider blocks the picker or page load
Timeout 10 s and `gather` concurrency; LM Studio-down is an immediate `ConnectError`.

### Pitfall 6: `LMStudioClient` state split
Two clients for the same LM Studio would each have their own lock/`_current_loaded_model`; registry by base URL (Pattern 7).

### Pitfall 7: DeepSeek model naming drift
Current DeepSeek docs list `deepseek-flash` / `deepseek-v4-pro` (legacy names still accepted) [CITED: api-docs.deepseek.com]; the 09-HUMAN-UAT command hard-codes `deepseek-chat`. Because the picker is live-fetched (`/models`), the UAT must choose a model id from the picker/list response, and `reasoning_effort: "none"` acceptance by the new models is exactly what 999.11 must observe (existing 400/422 retry covers rejection; an ignored field with empty content would log `chat_title_llm_unusable`). `[ASSUMED]` that `deepseek-chat` still works; do not hard-code it in tests.

### Pitfall 8: Reasoning models over remote APIs may return empty `content`
Already handled for titles (fallback title). Not new, but more likely with DeepSeek; no extra work.

### Pitfall 9: Header select cannot render long prefixed names
`max-w-xs truncate` select; optgroup + prefix per UI-SPEC. Nothing to build; just keep `title` attribute optional.

### Pitfall 10: Security — env-name indirection can exfiltrate any secret
Any authenticated user can create a provider with `api_key_env=DEEPSEEK_API_KEY` and an attacker-controlled base URL; the check/chat then sends the key as bearer to that host. Accepted by D-02/D-04 under the project's "all accounts equal admin" model, but flag it. Cheap mitigations the planner may add: resolve only names present in the `.env` FILE (not arbitrary `os.environ` such as `PATH`/`USERPROFILE`), and do not follow redirects (httpx default). `[ASSUMED]` policy; confirm with user if a stricter allowlist is wanted.

## Code Examples

### Models + migration (project conventions)
```python
# shared/models.py
class LlmProvider(SQLModel, table=True):
    """User-scoped OpenAI-compatible LLM provider (API key stored as .env variable NAME only)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True))
    name: str = Field(max_length=100)
    base_url: str = Field(max_length=500)
    kind: str = Field(default="openai", max_length=20)           # "openai" | "lm_studio"
    api_key_env: Optional[str] = Field(default=None, max_length=100)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_llmprovider_user_name"),)

class LlmProviderSeed(SQLModel, table=True):
    """Tombstone: a seeded provider was created once for this user, so deleting it sticks."""
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True))
    seed_key: str = Field(primary_key=True, max_length=50)

# ScheduledTask: plain integer, deliberately NO ForeignKey (a deleted provider must stay "unavailable")
provider_id: Optional[int] = Field(default=None)
```
```python
# shared/database.py — follow migrate_add_message_tool_trace
async def migrate_add_scheduledtask_provider_id(conn): ...  # PRAGMA table_info(scheduledtask); ALTER TABLE ... ADD COLUMN provider_id INTEGER
# init_db: call it before conn.run_sync(SQLModel.metadata.create_all)
# Also import the new models in shared/database.py's model import line (create_all only sees imported tables).
```
Note `shared/database.py:18` imports a subset of models for metadata registration; `LlmProvider*` must be imported (directly or via `agent` modules loaded before `init_db`) — in tests `from agent.main import app` already imports everything, but import them explicitly to be safe.

### Response shapes (suggested)
```json
GET /api/v1/llm-providers ->
[{"id":1,"name":"DeepSeek","base_url":"https://api.deepseek.com","kind":"openai",
  "api_key_env":"DEEPSEEK_API_KEY","enabled":true,
  "check":{"status":"ok|error|not_checked","code":null,"message":null,"model_count":3,"checked_at":"..."}}]
POST /api/v1/llm-providers/{id}/check -> same item (fresh check)
GET  /api/v1/llm-providers/models?refresh=true ->
[{"provider_id":1,"name":"DeepSeek","kind":"openai","models":[{"id":"...","loaded":null}],"error":null}]
```
Error codes for `check.code`: `env_missing`, `bad_key` (401/403), `unreachable`, `timeout`, `http` (other status; include status number), `bad_response`. Messages are the Russian strings from UI-SPEC "Copywriting Contract", built server-side (so tests assert them) with `{ENV_NAME}` and `{status}` interpolated.

### respx test pattern (existing convention)
```python
@respx.mock
async def test_check_ok(client_authed):
    respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]}))
    # monkeypatch.setenv("EXAMPLE_KEY", "sk-test"); assert request header Authorization == "Bearer sk-test"
```
Required test coverage: success (200 + N models, bearer header sent, key absent from response JSON), 401, 403, ConnectError, TimeoutException, 500, malformed JSON, env var missing, disabled provider skipped in `/models`, foreign provider id -> 404, name conflict -> 409, base-URL normalization, seeding idempotent + not resurrected after delete, legacy payload without `provider_id` routes to LM Studio URL, `provider_id` of DeepSeek routes request to `https://api.deepseek.com/v1/chat/completions` with bearer, deleted provider -> `PROVIDER_UNAVAILABLE` frame and NO persisted user message, scheduled run with dangling `provider_id` ends `failed` with the UI-SPEC message, title call uses the selected provider.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single `llm_client` bound to LM Studio (+ DeepSeek key as bearer to the wrong host) | Resolver per (user, provider) | This phase | Fixes the latent "DeepSeek never reachable" gap (recorded in 02-05-SUMMARY) |
| `deepseek-chat` / `deepseek-reasoner` model names | `deepseek-flash`, `deepseek-v4-pro` (legacy names still served) [CITED: api-docs.deepseek.com] | per current docs | Live `/models` list drives the picker; don't hard-code names |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | httpx default does not follow redirects (so bearer isn't forwarded) | Standard Stack | Key could leak on a 30x to another host; fix by passing `follow_redirects=False` explicitly (do this anyway) |
| A2 | Reading `.env` on every resolve is acceptable cost / semantics (no restart needed) | Pattern 2 | Minor; UI copy says restart |
| A3 | Tombstone-table seeding design | Pattern 4 | Alternative designs also satisfy CONTEXT; low |
| A4 | `{base}/v1/models` works for `https://api.deepseek.com` (docs show `/models`; `/v1` alias from secondary sources) | Pattern 5 | Check fails for DeepSeek; fallback: try `/models` when `/v1/models` returns 404 |
| A5 | `deepseek-chat` still accepted | Pitfall 7 | UAT command fails; use a model from the live list |
| A6 | Proposed `PROV-01..07` requirement split | Phase Requirements | Cosmetic |
| A7 | Env-name exfiltration accepted under equal-admin model; resolve `.env`-file names only is an optional hardening | Pitfall 10 | Cross-user key theft on shared deployments |
| A8 | `UniqueConstraint(user_id, name)` with 409 on conflict | Pattern 1 | Duplicate names make picker prefixes ambiguous otherwise |

## Open Questions

1. **Should `/models` fall back to `{base}/models` when `/v1/models` is 404?**
   - Known: D-08 fixes `{base_url}/v1/models`; DeepSeek docs document `/models`.
   - Recommendation: implement the single `/v1/models` path per D-08; add the 404 fallback only if the 999.11 live check shows DeepSeek needs it.
2. **Restrict env-var names to `.env`-file keys?** (Pitfall 10) Recommendation: yes, cheap and consistent with "reference to a .env variable name"; the real-env lookup is then only for the declared `DEEPSEEK_API_KEY`. Needs a one-line user confirmation or planner decision.
3. **Per-chat model persistence:** not required (no `Settings.model`); selection stays client-side. Confirm no one expects it to persist across reloads (today it does not).
4. **999.11 closure:** recommend (a) keep the manual API-level UAT in `09-HUMAN-UAT.md` test 1 but updated to pick the model id from `/models`, and (b) an end-to-end browser check on the isolated copy (:18000/:18001) that selects `DeepSeek · <model>`, sends one message and observes `chat_title_set source=llm`. Do NOT add a live paid test to CI; an opt-in `pytest -m live_deepseek` skipped without `DEEPSEEK_API_KEY` is acceptable discretion.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | — |
| pytest / pytest-asyncio / respx | tests | yes | 9.1.1 / 1.4.0 / 0.23.1 | — |
| python-dotenv | env resolver | yes (transitive) | 1.2.3 | — |
| `DEEPSEEK_API_KEY` in `.env` | seeding, UAT | yes, non-empty (boolean check only; value not read/printed) — may still be the example placeholder (earlier phase notes said it was a placeholder) | — | Seeding skipped; UAT blocked until a real key is set |
| LM Studio on :1234 | LM Studio provider live checks / UAT | not probed (tests use respx) | — | respx mocks |
| Network to api.deepseek.com | 999.11 UAT | not probed; paid call, user-run | — | opt-in only |
| Node / npm | — | not needed (project forbids) | — | — |

**Missing with no fallback:** none for implementation. UAT with real DeepSeek needs a valid key (user-provided).

## Validation Architecture

`workflow.nyquist_validation` is `false` in `.planning/config.json` — section omitted by config. Test conventions to follow anyway: `pytest tests/ -v`, `asyncio_mode=auto`, per-test DB reset in `conftest.py::clean_test_db` (also clear `providers.MODEL_CACHE` there), authenticated client helpers `login_test_client` / `_create_user`, JS structural check `tests/test_static_js_syntax.py`. Check `docs/TESTING_GUIDE.md` before adding tests (per CLAUDE.md).

## Security Domain

`security_enforcement` is not set to false -> applicable.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no change | existing cookie session |
| V3 Session Management | no change | existing |
| V4 Access Control | yes | `user_id` ownership, foreign id -> 404 (never 403), same helper pattern as `_get_mcp_server_or_404` |
| V5 Input Validation | yes | Pydantic: name 1-100, base_url must match `^https?://`, env name regex `^[A-Za-z_][A-Za-z0-9_]*$` (max 100), normalized URL; reject empty |
| V6 Cryptography / secrets | yes | never persist or return key values; never log them; no hand-rolled crypto (encryption deferred) |
| V12/V13 SSRF-ish | yes (low) | base URL is user-controlled and the Agent fetches it; project already runs arbitrary MCP commands for the same trust level; no redirects; response size not unbounded (read JSON only from `/v1/models`) |

### Known Threat Patterns
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Key exfiltration via env-name + attacker base URL | Information disclosure | Restrict resolvable names (Open Q2); `follow_redirects=False` |
| XSS via provider name / remote error text / model ids in the UI | Tampering | `textContent`/`createElement` only (UI-SPEC rule); DOMPurify if any HTML |
| IDOR on provider id | Information disclosure | 404 on foreign id; tests for cross-user access |
| Key leakage in logs/errors | Information disclosure | log `provider_id`, `error_code`, status only; never `str(headers)` |
| CSRF on cookie auth for new POST/PUT/DELETE | Tampering | reuse existing `require_allowed_origin` / `require_json_content_type` dependencies like other mutating routes (`agent/dependencies.py`) |

## Project Constraints (from CLAUDE.md)

- Python 3.11+, type hints everywhere, `X | None` syntax, `async/await` for all I/O; `structlog` via `get_logger(__name__)` (no `print`); `datetime.now(timezone.utc)`.
- SQLModel FK cascade only via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`, never `Field(ondelete=...)`; `.is_(None)` in `where()`; always `await session.commit()` and `await session.rollback()` in except handlers; no bare `except:`.
- No Docker/npm/Node/Redis/Celery, no `multiprocessing`/`os.fork`; IPC is REST + WS; in-memory state per process only; frontend vanilla JS + CDN (Tailwind, Marked, DOMPurify), no bundler; never insert HTML without `DOMPurify.sanitize()`; no hardcoded secrets (`.env` via `shared/config.py`).
- All new data `user_id`-scoped; session via HTTP-only cookie; foreign ids 404.
- Deleting a chat must clear in-memory caches via `agent.state.cleanup_chat_caches` (provider cache is user-keyed, not chat-keyed — no change needed).
- Settings fallback global/per-chat must be preserved (not touched by this phase since no `Settings.model`).
- Tests: separate DB, `respx` for HTTP, consult `docs/TESTING_GUIDE.md`.
- Workflow: commits must NOT include a `Co-Authored-By: Claude` line (user's global CLAUDE.md + memory) even though the harness reminder suggests one; push + merge phase branch to `main` after completion without asking (project memory); E2E via Playwright on an isolated copy at ports 18000/18001, never kill the user's 8000/8001 app.
- UI-SPEC (approved): on-scale spacing (4/8/16), only font weights 400/600, accent reserved for the add/save buttons and focus rings; copy strings in Russian as listed; Enter inside the provider form must not submit `#settings-form` (form is outside it).

## Sources

### Primary (HIGH confidence)
- Repository code read this session: `agent/llm_client.py`, `agent/ws.py` (L280-430, 660-980), `agent/titles.py`, `agent/headless.py`, `agent/context_engine.py`, `agent/invariants.py`, `agent/mcp_config.py`, `agent/main.py` (lifespan, auth, lm-studio endpoints), `agent/schemas.py`, `agent/state.py`, `agent/scheduler*.py`, `shared/models.py`, `shared/config.py`, `shared/database.py`, `ui/static/app.js` (picker, MCP, scheduler, settings), `ui/static/index.html`, `tests/conftest.py`, `requirements.txt`; CONTEXT.md, UI-SPEC.md, ROADMAP.md, REQUIREMENTS.md, 09-HUMAN-UAT.md.
- `pip list` / `pip show pydantic-settings` (installed versions; python-dotenv dependency).
- https://api-docs.deepseek.com/api/list-models — `GET /models`, response `data[].id/name/context_window/...`.
- https://api-docs.deepseek.com/ — base URL `https://api.deepseek.com`, Bearer auth, current model names.

### Secondary (MEDIUM confidence)
- Web search summary: `/v1` suffix on the DeepSeek base URL is an OpenAI-SDK compatibility alias unrelated to model version (froala.com DeepSeek integration guide, theneuralbase.com base-url guides).

### Tertiary (LOW confidence)
- None relied upon for decisions.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; versions verified locally.
- Architecture / call-site inventory: HIGH — verified by grep/read of every `llm_client` usage and `model` threading.
- Pitfalls: HIGH for codebase-derived items (FK/NULL fallback, `.env` vs `os.environ`, tests patching `llm_client`, `Settings.model` absence); MEDIUM for DeepSeek endpoint/model-name behaviour.

**Research date:** 2026-10-02
**Valid until:** 2026-10-16 for DeepSeek model names/endpoints (fast-moving); codebase findings valid until the touched files change.
