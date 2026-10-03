# Phase 12: LLM providers section in Settings (Day 21) - Context

**Gathered:** 2026-10-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Settings gets a new "Провайдеры LLM" section, styled like "MCP серверы" (`#mcp-section` in `ui/static/index.html`): a "+ Добавить провайдера" button with the already-added providers listed below. After a provider is saved, a connection check runs and shows a success or error indicator. The LLMs a provider exposes appear in the model picker (`#model-select`), each entry prefixed with the provider name. Branch `Day21`. Depends on Phase 8.

Today the Agent has ONE global `llm_client` (`agent/llm_client.py`, bound to `LM_STUDIO_BASE_URL` + `DEEPSEEK_API_KEY`) and the picker lists only LM Studio models (`/api/v1/lm-studio/models`). This phase introduces per-provider routing.

**Not in this phase:** non-OpenAI-compatible APIs (Anthropic native, Gemini native), per-provider pricing/usage stats, API-key encryption, a separate title-model setting. Requirement IDs are "TBD" in the roadmap — the planner should define `PROV-xx` in REQUIREMENTS.md. Closes backlog 999.11 (DeepSeek backend check for auto-title) as a side effect of routing titles through DeepSeek.
</domain>

<decisions>
## Implementation Decisions

### Provider config & secrets
- **D-01:** A provider has: **name, base URL, API key reference, enabled flag**. Generic OpenAI-compatible provider (DeepSeek, OpenAI, OpenRouter, ...). New user-scoped table (by `user_id`), like `McpServerConfig`.
- **D-02:** The API key is stored as a **reference to a `.env` variable name** (e.g. `DEEPSEEK_API_KEY`), never the key itself. The Agent resolves the value from the environment/`shared.config` at call time. The key never touches the DB or any API response. Providers without a key (LM Studio) leave the reference empty.
- **D-03:** **Auto-seed a DeepSeek provider** (name "DeepSeek", `https://api.deepseek.com`, key ref `DEEPSEEK_API_KEY`) when `.env` has the key. Seeding is idempotent and per user (no duplicates on restart).
- **D-04:** The env-var name must be a settable setting (so `shared/config.py` must expose or look up arbitrary env names safely); the planner decides how to resolve names (e.g. `os.environ`/dotenv lookup) and must reject empty/invalid names.

### LM Studio & routing
- **D-05:** **LM Studio is a regular provider row** (seeded, editable, base URL `LM_STUDIO_BASE_URL`, no key). The LM Studio-specific load/unload control API and "loaded ✓" marks apply only to the provider recognized as LM Studio (planner decides the discriminator, e.g. a `kind` field or base-URL match); the confirm-to-load flow in `onModelSelect` is preserved for it.
- **D-06:** Model identity uses a **separate `provider_id` field** next to the model string (not a composite id). It must be threaded through: WS chat payload, per-chat/global Settings, scheduled job rows (D-03 of Phase 8 stored only a model id), and the title call. Existing rows with no `provider_id` must keep working and resolve to the seeded LM Studio provider (migration/back-compat, planner designs it).
- **D-07:** **All LLM flows route through the selected provider:** chat streaming (`agent/ws.py`), auto-title (`agent/titles.py`), scheduler headless runs (`agent/headless.py`), and summaries / fact extraction / invariant self-critique (`agent/context_engine.py`, `agent/invariants.py`). The module-level `llm_client` singleton is replaced by a resolver returning a client per (user, provider) — token counting (`count_tokens`, tiktoken) stays provider-independent.

### Connection check & model list
- **D-08:** The check is **`GET {base_url}/v1/models` with the resolved key**. Success = HTTP 200 with a model list; errors are classified into readable messages (bad key 401/403, unreachable, timeout, other HTTP). It runs **automatically after a provider is saved** and via a **manual "Проверить" button** per provider. Result shown as a status indicator (success/error badge + message) in the provider row, in the same visual vocabulary as MCP status badges.
- **D-09:** The model list is **fetched live and cached in memory** (per process): refreshed on page load, after save/check, and via manual re-check. A failing provider is skipped with a small non-blocking warning, never a blocking error for the whole picker.

### Picker entries & lifecycle
- **D-10:** Picker entries are **"Provider · model", grouped by provider**. Unreachable or disabled providers contribute **no entries**. LM Studio entries keep the `✓` loaded mark.
- **D-11:** **Deleting/disabling a provider is always allowed.** Chats/jobs still referencing it fail with a clear "provider unavailable" error; scheduled runs end `failed` (consistent with Phase 8 D-03: no fallback). The chat UI falls back to the first available picker entry.
- **D-12:** Everything is **user-scoped** (`user_id`); REST under `/api/v1/llm-providers` (planner picks exact shape) with session auth; foreign ids → 404. UI labels in Russian.

### Claude's Discretion
- Table/column names, `kind` discriminator, exact REST/response shapes, picker value encoding (`<option value>`), cache TTL/invalidations, error-message wording, Tailwind markup, empty-state copy, and test plan (success + failure checks via `respx`).
- How the 999.11 DeepSeek UAT is folded in (e.g. a documented manual check or an opt-in live test).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & requirements
- `.planning/ROADMAP.md` §Phase 12 and §Backlog 999.11 — goal, behavior, branch `Day21`, DeepSeek check procedure
- `.planning/phases/09-auto-rename-chats-with-llm-day-21/09-HUMAN-UAT.md` (test 1) — DeepSeek title-call check to close with this phase
- `.planning/REQUIREMENTS.md` — add `PROV-xx` requirements here
- `.planning/PROJECT.md` §Constraints — vanilla JS + CDN only, `user_id` scoping, REST + WS IPC, no Docker/Node/broker

### Prior-phase decisions to stay consistent with
- `.planning/phases/07-mcp-connection-day-16/07-CONTEXT.md` — Settings section pattern, user-scoped CRUD, env masking, status badges, Russian labels
- `.planning/phases/08-scheduler-day-18/08-CONTEXT.md` — D-03 (job stores model id, no fallback), headless runner
- `.planning/STATE.md` §Decisions

### Code to read
- `agent/llm_client.py` — `LLMClient`, `LMStudioClient`, global `llm_client`
- `agent/main.py` (lm-studio endpoints ~L1195-1240), `agent/ws.py`, `agent/titles.py`, `agent/headless.py`, `agent/context_engine.py`, `agent/invariants.py`
- `agent/mcp_config.py`, `agent/schemas.py` — pattern for user-scoped config CRUD
- `ui/static/app.js` (`loadModels`/`populateModelSelect`/`onModelSelect` ~L1372-1460; MCP servers section ~L1461+), `ui/static/index.html` (`#model-select` L183, `#mcp-section` L317)
- `shared/models.py`, `shared/config.py`, `shared/database.py` (migrations)
- `CLAUDE.md`, `.planning/codebase/*.md`, `docs/TESTING_GUIDE.md`, `docs/API_SPEC.md`

No external specs beyond the above.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `LLMClient(base_url, api_key)` already takes per-instance URL/key — a per-provider client is cheap to build.
- `McpServerConfig` + `agent/mcp_config.py` + `#mcp-section` UI: template for user-scoped CRUD, status badges, add/edit form.
- `MCP_STATUS_BADGE_CLASSES` / `MCP_STATUS_LABELS` in `app.js` for check indicators.
- `/ws/events` user-level socket (Phase 8) can push provider-check results if wanted.

### Established Patterns
- SQLModel FK cascade via `sa_column=Column(ForeignKey(..., ondelete=...))`; commit/rollback pattern; structlog; `httpx.ConnectError` classification.
- `LMStudioClient` has a `model_switch_lock` and `_current_loaded_model` state — keep LM Studio specifics isolated.

### Integration Points
- Single global `llm_client` imported in `ws.py`, `titles.py`, `invariants.py`, `context_engine.py`, tests (`tests/test_llm_complete_chat.py`, context-engine tests) — replacing it touches many call sites and test fixtures.
- Model strings flow through WS payload, Settings, scheduled-job rows, title generation.
- `trySendPending()` in `populateModelSelect` depends on `state.selectedModel` being set.

</code_context>

<specifics>
## Specific Ideas

- Section looks and behaves like "MCP серверы": "+ Добавить провайдера" on top, list of providers below, each with status indicator.
- Picker prefix example: `DeepSeek · deepseek-chat`.

</specifics>

<deferred>
## Deferred Ideas

- API-key encryption at rest / key entry in UI — not needed with env-var references.
- Per-provider usage/cost stats — future phase.
- Non-OpenAI-compatible providers — future phase.

</deferred>

---

*Phase: 12-llm-providers-section-in-settings-day-21*
*Context gathered: 2026-10-02*
