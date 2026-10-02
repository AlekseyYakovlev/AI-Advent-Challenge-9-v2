---
phase: 12-llm-providers-section-in-settings-day-21
plan: 04
subsystem: scheduler-provider-routing
tags: [llm-providers, scheduler, headless, ownership-check]
requires: ["12-01", "12-03"]
provides:
  - "ScheduledTask.provider_id written by REST create and the schedule_task tool, exposed in ScheduledTaskOut"
  - "Provider-routed headless run with fail-fast resolution (no fallback)"
affects: [12-05]
tech-stack:
  added: []
  patterns: ["resolve provider before any read or LLM call in the unattended runner", "ownership check at job creation, re-check at run time"]
key-files:
  created:
    - tests/test_scheduler_providers.py
  modified:
    - agent/scheduler_ops.py
    - agent/scheduler_schemas.py
    - agent/scheduler_tools.py
    - agent/scheduler.py
    - agent/headless.py
    - tests/test_scheduler_service.py
key-decisions:
  - "A disabled provider is accepted at job creation; the run fails later with the UI-SPEC message (D-11)"
  - "Connection errors on non-LM-Studio providers name the provider; LM Studio keeps its legacy message"
requirements-completed: [PROV-05, PROV-06]
duration: ~20 min
completed: 2026-10-02
---

# Phase 12 Plan 04: Scheduler provider routing Summary

Scheduled jobs store the provider of the chat turn (or the REST form) and run against it; a deleted or disabled provider fails the run with "Провайдер недоступен (удалён или отключён)" and sends no request.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 provider_id in scheduler create/out/tool/execute_run; provider-routed headless turn | e136f21 | `pytest tests/test_scheduler_service.py tests/test_scheduler_runner.py tests/test_scheduler_api.py tests/test_scheduler_tools.py`: 189 passed |
| 2 Scheduler provider tests | 7eb3f9c | `tests/test_scheduler_providers.py`: 12 passed; full `pytest tests/ -q`: 1148 passed, 0 failed (223 s) |

## Deviations from Plan

**1. [Rule 3 - Blocking] Tool test needs a real origin chat**
- `schedule_task` stores `origin_chat_id` with an FK, so the tool test creates a Chat row instead of using chat id 0. Test-only change.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-12-19 (ownership 422 test), T-12-20 (deleted/disabled provider, no fallback, tests for both and via execute_run) and T-12-22 (messages carry provider name only) are implemented and tested.

## Not verified

- No live DeepSeek or LM Studio calls; all network behaviour is respx-mocked.
- No browser check (frontend is plan 12-05).

## Self-Check: PASSED

tests/test_scheduler_providers.py exists; commits e136f21 and 7eb3f9c exist.
