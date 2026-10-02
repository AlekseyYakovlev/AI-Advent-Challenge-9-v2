---
phase: 12-llm-providers-section-in-settings-day-21
plan: 01
subsystem: backend-providers
tags: [llm-providers, env-secrets, sqlmodel, httpx, respx]
requires: []
provides:
  - "shared.config.resolve_env_secret / is_valid_env_name"
  - "LlmProvider, LlmProviderSeed tables; ScheduledTask.provider_id"
  - "agent.providers service (CRUD, seeding, resolver, check, model cache)"
  - "agent.llm_client.count_tokens, get_lm_studio_client"
affects: [12-02, 12-03, 12-04]
tech-stack:
  added: []
  patterns: ["key stored only as .env variable name", "lazy per-user idempotent seeding with marker table"]
key-files:
  created:
    - agent/providers.py
    - tests/test_llm_providers_config.py
    - tests/test_llm_providers_service.py
  modified:
    - shared/config.py
    - shared/models.py
    - shared/database.py
    - agent/schemas.py
    - agent/llm_client.py
    - agent/state.py
    - tests/conftest.py
    - tests/test_database.py
key-decisions:
  - "resolve_env_secret resolves only names declared in the .env file or the builtin DEEPSEEK_API_KEY; process variables like PATH return None"
  - "Seed markers (LlmProviderSeed) make deletion of seeded providers permanent"
  - "check_provider uses follow_redirects=False and never logs or returns the key"
requirements-completed: [PROV-01, PROV-02, PROV-03, PROV-05]
duration: ~25 min
completed: 2026-10-02
---

# Phase 12 Plan 01: Provider backend foundation Summary

Env-name-based secret resolver, `LlmProvider`/`LlmProviderSeed` tables with a `scheduledtask.provider_id` migration, and `agent/providers.py` (CRUD, idempotent seeding, resolver, connection check with Russian error taxonomy, model cache).

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Resolver, tables, migration, test env isolation | c65ec57 | `pytest tests/test_llm_providers_config.py`: 21 passed |
| 2 Schemas, normalize_base_url, count_tokens, LM Studio registry, ContextVar | 61b1568 | plan verify command OK; 17 existing LLM client tests passed; `import agent.main` OK |
| 3 agent/providers.py + tests | 672fc3f | `tests/test_llm_providers_service.py`: 41 passed |

Final full suite: `pytest tests/ -q` gave 1091 passed, 1 failed (see Deviations); after the fix `tests/test_database.py` and the service tests pass (49 passed). The full suite was not re-run end to end after the one-line test fix.

## Deviations from Plan

**1. [Rule 1 - Bug] Updated expected table set in tests/test_database.py**
- **Found during:** Task 3 full-suite run
- **Issue:** `test_init_db_creates_all_tables` asserts the exact set of tables; the new `llmprovider` / `llmproviderseed` tables broke it.
- **Fix:** Added both names to the expected set.
- **Commit:** 672fc3f

## Assumption Drift (advisory)

- **Found during:** Task 2. Planned: dropping the DeepSeek key from the `llm_client` singleton is harmless because nothing routes via providers yet. Actual: existing chat/title/context call sites still use `llm_client`, so cloud DeepSeek calls from them are keyless until plans 12-03/12-04 reroute them. This is inherent to the plan's ordering (tests pass because they mock HTTP); not browser-verified.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model.

## Not verified

- No live DeepSeek/LM Studio calls; all checks are respx-mocked.
- Full-suite re-run after the test_database fix was not performed.

## Self-Check: PASSED

Files agent/providers.py, tests/test_llm_providers_config.py, tests/test_llm_providers_service.py exist; commits c65ec57, 61b1568, 672fc3f exist.
