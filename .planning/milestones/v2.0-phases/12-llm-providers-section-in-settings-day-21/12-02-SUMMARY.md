---
phase: 12-llm-providers-section-in-settings-day-21
plan: 02
subsystem: backend-providers-api
tags: [llm-providers, rest, fastapi, csrf, lm-studio]
requires: ["12-01"]
provides:
  - "REST /api/v1/llm-providers (list, models, create, update, delete, check), user-scoped"
  - "Provider-aware LM Studio endpoints (optional provider_id)"
affects: [12-03, 12-05]
tech-stack:
  added: []
  patterns: ["404-never-403 ownership check", "check failures returned as data in a 200 body"]
key-files:
  created:
    - agent/providers_api.py
    - tests/test_llm_providers_api.py
  modified:
    - agent/main.py
    - tests/test_scoping.py
key-decisions:
  - "Legacy LM Studio calls without provider_id use get_lm_studio_client(LM_STUDIO_BASE_URL)"
  - "PUT distinguishes an omitted api_key_env from an explicit null or empty value (clears it) via model_dump(exclude_unset)"
requirements-completed: [PROV-01, PROV-02, PROV-03, PROV-04]
duration: ~15 min
completed: 2026-10-02
---

# Phase 12 Plan 02: Providers REST API Summary

Session-authenticated, user-scoped REST surface for LLM providers (`GET/POST /api/v1/llm-providers`, `PUT/DELETE /{id}`, `POST /{id}/check`, `GET /models?refresh=`), plus LM Studio list/load/unload routes that accept an optional `provider_id` resolved through the client registry.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Router, registration, provider-aware LM Studio endpoints | 14d8579 | route presence check OK; `test_scoping`, `test_lm_studio_client`, `test_model_switch_lock`: 37 passed |
| 2 REST tests + scoping rows | 7d9ad05 | `test_llm_providers_api.py`: 24 passed; with `test_scoping.py`: 59 passed |

Full suite: `pytest tests/ -q` gave 1122 passed, 0 failed.

## Deviations from Plan

**1. [Rule 3 - Blocking] Route-presence verify command adapted**
- **Issue:** The plan's `{r.path for r in app.routes}` check fails on the installed FastAPI (`_IncludedRouter` has no `path`).
- **Fix:** Verified the same four paths via `app.openapi()["paths"]` instead. Not a code change.

**2. Settings name in main.py**
- `agent/main.py` imports settings as `app_config`, so `_lm_studio_for` uses `app_config.LM_STUDIO_BASE_URL`.
- `LMStudioClient` import was kept in main.py because it is the helper's return type.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (T-12-09 to T-12-12 mitigated and tested: foreign ids 404, foreign Origin 403, text/plain 415, no key value in any body, 401 without a session).

## Not verified

- No live provider, DeepSeek or LM Studio calls; all network behaviour is respx-mocked.
- No browser check (frontend arrives in a later plan).

## Self-Check: PASSED

Files agent/providers_api.py and tests/test_llm_providers_api.py exist; commits 14d8579 and 7d9ad05 exist.
