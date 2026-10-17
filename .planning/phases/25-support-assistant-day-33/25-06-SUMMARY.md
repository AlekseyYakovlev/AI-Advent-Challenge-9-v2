---
phase: 25-support-assistant-day-33
plan: 06
subsystem: agent
tags: [support, settings, rest, fastapi]
requires: ["25-04"]
provides:
  - "agent/support_api.py: GET/PUT /api/v1/support/config (SupportConfigIn/Out)"
affects: [25-08]
tech-stack:
  added: []
  patterns: ["per-user settings route mirroring help_api without base selection"]
key-files:
  created:
    - agent/support_api.py
    - tests/test_support_api.py
  modified:
    - agent/main.py
key-decisions:
  - "The /help base is exposed read-only (help_kb_*) and only when owned by the caller; /support never writes HelpConfig (D-02, D-04)"
requirements-completed: [SUP-03, SUP-12]
duration: 10min
completed: 2026-10-17
---

# Phase 25 Plan 06: Support settings routes Summary

Authenticated, origin-checked `GET/PUT /api/v1/support/config` storing per-user `/support` search settings and whole-server tool choice (default: only the server named `support`), with read-only `help_kb_*` and `support_server_present` fields and `help_api.py` untouched.

## Tasks
1. `agent/support_api.py` and router registration in `agent/main.py`: commit 5528811
2. `tests/test_support_api.py` (22 tests incl. parametrized validation): commit fc2a02d

## Verification (observed)
- `pytest tests/test_support_api.py tests/test_help_api.py -q`: 42 passed.
- `git diff --stat -- agent/help_api.py`: empty.
- The plan's verify command (`app.routes ... r.path`) fails with `AttributeError: '_IncludedRouter' object has no attribute 'path'` under the installed Starlette; the equivalent check through `app.openapi()['paths']` printed `['/api/v1/support/config']`.
- The full suite was not run for this plan.

## Deviations from Plan

**1. [Rule 3 - Blocking] Verify command incompatible with installed Starlette**
- **Issue:** `app.routes` contains `_IncludedRouter` objects without `.path`.
- **Fix:** verified route registration through the OpenAPI paths instead; no code change.

**2. [Rule 1 - Bug] Test assumption about calibrated_threshold**
- **Issue:** the first test version asserted `calibrated_threshold` is not None, but the function legitimately returns None for the test model.
- **Fix:** assert equality with `calibrated_threshold(kb.embedding_model)`; also use `session.exec` for the cascade delete.
- **Files modified:** tests/test_support_api.py
- **Commit:** fc2a02d

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
