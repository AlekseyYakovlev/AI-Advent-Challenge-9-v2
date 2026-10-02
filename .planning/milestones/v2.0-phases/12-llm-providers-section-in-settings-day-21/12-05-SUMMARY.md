---
phase: 12-llm-providers-section-in-settings-day-21
plan: 05
subsystem: frontend-providers
tags: [llm-providers, vanilla-js, settings-ui, model-picker]
requires: ["12-02", "12-03"]
provides:
  - "Settings section 'Провайдеры LLM' (add/edit/delete, auto-check after save, manual check)"
  - "Provider-grouped model picker and scheduler select; provider_id in WS and scheduler payloads"
affects: [12-06]
tech-stack:
  added: []
  patterns: ["DOM-only rendering (createElement/textContent/optgroup.label)", "option value '<provider_id>::<model_id>' split at first '::'"]
key-files:
  modified:
    - ui/static/index.html
    - ui/static/app.js
key-decisions:
  - "Both tasks landed in one commit because they edit the same app.js regions"
requirements-completed: [PROV-01, PROV-03, PROV-04, PROV-06]
duration: ~25 min
completed: 2026-10-02
---

# Phase 12 Plan 05: Providers UI Summary

The Settings modal has a "Провайдеры LLM" section following 12-UI-SPEC.md. The header model picker and the scheduler select are now built from `/api/v1/llm-providers/models`, grouped in one optgroup per provider, and both send `provider_id` next to `model`.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Section markup and Settings JS block | 0299176 | done |
| 2 Provider-aware picker, scheduler select, WS and scheduler payloads, PROVIDER_UNAVAILABLE | 0299176 | done |

## Verification (observed)

- `pytest tests/ -q`: 1148 passed, 0 failed. This includes `tests/test_static_js_syntax.py`.
- Static checks, run by script:
  - The new index.html section contains none of `font-medium`, `p-3`, `p-5`, `space-y-3`, `py-1.5`, `py-0.5`.
  - `renderLlmProviderRow`, `renderLlmProviderList`, `populateModelSelect`, `populateSchedulerModelSelect`, `loadModels` and `refreshModelSelector` contain no `innerHTML`.
  - `state.models` and `/api/v1/lm-studio/models` no longer appear in app.js.
  - `provider_id: state.selectedProviderId` is in the WS payload.
  - `indexOf('::')` is used to split option values.

## Deviations from Plan

**1. [Process] Single commit for both tasks.** Both tasks change overlapping parts of app.js (shared helpers, `refreshModelSelector` called from the Settings block), so they were committed together as 0299176.

**2. [Rule 1 - Bug, UX] The picker no longer overrides the user's choice on refresh.** This is intended by the plan. The old `populateModelSelect` always selected the first loaded model.

## Not verified

- No browser run. Rendering, the confirm flow, toasts and the PROVIDER_UNAVAILABLE fallback were checked only by code review and syntax and static checks. Playwright E2E is plan 12-06's job.
- The user's app on ports 8000/8001 was not started or touched.

## Known Stubs

None.

## Threat Flags

None. T-12-23 (no innerHTML with provider strings) and T-12-24 (only env var names are shown or requested) are met.

## Self-Check: PASSED

ui/static/index.html and ui/static/app.js are modified, and commit 0299176 exists.
