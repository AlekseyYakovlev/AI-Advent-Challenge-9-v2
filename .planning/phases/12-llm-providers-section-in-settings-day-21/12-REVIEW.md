---
phase: 12-llm-providers-section-in-settings-day-21
reviewed: 2026-10-02T00:00:00Z
depth: standard
files_reviewed: 25
files_reviewed_list:
  - agent/context_engine.py
  - agent/headless.py
  - agent/invariants.py
  - agent/llm_client.py
  - agent/main.py
  - agent/providers.py
  - agent/providers_api.py
  - agent/scheduler.py
  - agent/scheduler_ops.py
  - agent/scheduler_schemas.py
  - agent/scheduler_tools.py
  - agent/schemas.py
  - agent/state.py
  - agent/titles.py
  - agent/ws.py
  - shared/config.py
  - shared/database.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
  - scripts/e2e_llm_providers_playwright.py
  - tests/test_llm_providers_service.py
  - tests/test_llm_providers_api.py
  - tests/test_llm_providers_routing.py
  - tests/test_scheduler_providers.py
findings:
  critical: 0
  warning: 6
  info: 3
  total: 9
status: issues_found
---

# Phase 12: Code Review Report

**Reviewed:** 2026-10-02
**Depth:** standard
**Files Reviewed:** 25
**Status:** issues_found

## Summary

The provider feature is well structured. API keys are stored only as variable names, responses never carry key values, redirects are not followed on checks, and ownership is enforced with 404s. No blocker was found. The main concerns are secret exfiltration through a user-controlled base URL, provider id reuse that silently re-targets scheduled tasks, and a few consistency gaps. The tests and the e2e script were skimmed only. They have no reliability defects that I could prove.

## Warnings

### WR-01: Any .env-declared secret can be sent to an arbitrary URL

**File:** `agent/providers.py:374-377`, `shared/config.py:58-73`
**Issue:** `base_url` is user-supplied, and `api_key_env` may name any variable declared in `.env` (plus `DEEPSEEK_API_KEY`). A check sends `Authorization: Bearer <secret>` to that URL. An account holder can create a provider with `base_url=http://attacker` and `api_key_env=DEEPSEEK_API_KEY`, or any other `.env` secret, and click "Проверить". That exfiltrates the operator's secret. Accounts are equal-privilege, so this is a trust-boundary issue rather than a remote attack. The same primitive also gives blind SSRF to internal hosts, with model ids echoed back.
**Fix:** Restrict which env names provider keys may reference. For example, require a prefix such as `LLM_KEY_`, or use an allowlist setting. Optionally block private and link-local address ranges for non-LM-Studio providers.

### WR-02: SQLite rowid reuse can silently re-target scheduled tasks

**File:** `shared/models.py:458`, `agent/providers.py:221-231`
**Issue:** The comment says a deleted provider "must stay unavailable", but the table is created without AUTOINCREMENT. SQLite reuses the highest rowid after that row is deleted. If the newest provider is deleted and another is then created, it gets the same id. Scheduled tasks and the `provider_id` held by open chats then run against the new, unrelated provider instead of failing. The model name is sent to a different server.
**Fix:** Use `sqlite_autoincrement=True` in `__table_args__` for `LlmProvider`, or soft-delete providers. Note that a migration is needed for existing databases.

### WR-03: `_lm_studio_for(None)` ignores the user's seeded LM Studio row

**File:** `agent/main.py:1200-1203`
**Issue:** With `provider_id=None` it uses `settings.LM_STUDIO_BASE_URL`. `providers.get_provider_row(None)` resolves the user's seeded `lm_studio` row, whose `base_url` the user may have edited. The model list and load or unload calls can therefore hit a different host than chat does for legacy clients. Disabled providers are also accepted by this endpoint, while chat rejects them.
**Fix:** For `None`, call `providers.get_provider_row(session, user_id, None)` and use its `base_url`. Reject `not row.enabled` for explicit ids.

### WR-04: Synchronous .env file read on every secret resolution

**File:** `shared/config.py:64`
**Issue:** `dotenv_values()` re-reads and parses the file on each call. It is called from `ensure_seeded` (fast path, `providers.py:279`), `check_provider`, `build_client` and `resolve_client`. That is blocking disk I/O inside the event loop on every chat turn, title, facts and critique call. The path `.env` is also relative to the working directory, so it can differ from the pydantic `env_file` resolution.
**Fix:** Cache the parsed values keyed by file mtime, or load them once at startup. Use an absolute path.

### WR-05: Stale cache can be re-populated after an update or delete

**File:** `agent/providers.py:216, 230, 419`
**Issue:** `update_provider` and `delete_provider` pop `MODEL_CACHE`. A `check_provider` call that started before the edit then writes its result for the old URL or key into the cache afterwards. The UI shows "ok" and a stale model list for the edited provider. After a delete the entry is orphaned. `MODEL_CACHE` is also never cleared on user deletion.
**Fix:** Store a fingerprint of `(base_url, api_key_env, updated_at)` in `CheckResult` and ignore cache hits that don't match the row. Alternatively, skip the cache write when the row's `updated_at` changed during the check.

### WR-06: Model list is re-fetched from every provider on each selection and edit

**File:** `ui/static/app.js:1393-1450`, `1560`
**Issue:** `loadModels`, `refreshModelSelector`, `onModelSelect` and `checkLlmProvider` all call `/models?refresh=true`. Each selection change, check and delete therefore blocks on up to 10 s per unreachable provider. `onModelSelect` for LM Studio is the worst case. Concurrent refreshes (`checkLlmProvider` followed by `refreshModelSelector`) can finish out of order and overwrite `state.modelGroups` with older data. This is a correctness risk rather than a performance one.
**Fix:** Use `refresh=false` except for explicit user refreshes. Add a request sequence counter and drop stale responses.

## Info

### IN-01: `_insert_seed` logs "seeded" even when the provider was not created

**File:** `agent/providers.py:241-256`
**Issue:** After an `IntegrityError` and the marker-only fallback, `llm_provider_seeded` is still logged. This is misleading in the name-collision and concurrent-seeder cases.
**Fix:** Log a separate `llm_provider_seed_skipped` event on the fallback path.

### IN-02: Over-long line and a leftover blank line

**File:** `agent/scheduler.py:349`, `agent/scheduler_ops.py:113`, `agent/main.py:94-96`
**Issue:** Lines well over 100 characters, plus three consecutive blank lines left where `lm_studio_client` was removed.
**Fix:** Wrap the lines and remove the extra blank lines.

### IN-03: `Any`-typed provider parameters

**File:** `agent/headless.py:166`, `agent/ws.py:341`, `agent/ws.py:368-369`
**Issue:** `provider: Any | None` and `provider_row: Any | None` lose type checking, even though `LlmProvider` is available. `ws._ToolTurn.client` is typed `LLMClient | None`, so callers need an assert or a narrowing check.
**Fix:** Annotate these as `LlmProvider | None`, and give `client` a default factory.

---

_Reviewed: 2026-10-02_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
