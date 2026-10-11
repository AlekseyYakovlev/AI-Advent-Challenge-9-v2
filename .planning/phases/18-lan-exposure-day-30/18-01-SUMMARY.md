---
phase: 18-lan-exposure-day-30
plan: 01
subsystem: network-config
tags: [bind-host, config, supervisor, fail-fast]
requires: []
provides:
  - "settings.BIND_HOST / settings.is_lan_mode"
  - "AgentSupervisor(host=...)"
affects: [18-02, 18-05, 18-07]
tech-stack:
  added: []
  patterns: ["pydantic before-validator whitelist", "import-time SystemExit(2) with stderr message"]
key-files:
  created: [tests/test_bind_host.py, tests/test_supervisor_host.py]
  modified: [shared/config.py, ui/supervisor.py, tests/conftest.py, .env.example, .planning/REQUIREMENTS.md]
key-decisions:
  - "BIND_HOST whitelist: 127.0.0.1, localhost, 0.0.0.0 (trimmed, case-insensitive); empty = loopback"
  - "Invalid value exits with code 2 at import of shared.config, before port cleanup and admin bootstrap"
metrics:
  duration: ~25min
  tasks: 3
  files: 7
  completed: 2026-10-12
---

# Phase 18 Plan 01: BIND_HOST setting and supervisor host Summary

Validated single `BIND_HOST` setting (loopback or 0.0.0.0) with fail-fast exit code 2 at import, and a `host` parameter on `AgentSupervisor` used for the Agent `--host` argument.

## Commits
- 69e79ac: feat(18-01): add validated BIND_HOST setting with fail-fast exit
- bff9e04: feat(18-01): add host parameter to AgentSupervisor
- c6ca311: docs(18-01): document BIND_HOST and amend NET-01/NET-02

## Verification (observed)
- `pytest tests/test_bind_host.py tests/test_runtime.py tests/test_run_cleanup.py`: 30 passed.
- `pytest tests/test_supervisor_host.py tests/test_supervisor.py tests/test_orphan_cleanup.py`: 6 passed.
- Task 3 python assertion script: printed `ok`.
- Full suite: 2216 passed, 11 skipped with `test_agent_restarts_within_5_seconds` deselected. In the first full run that test timed out once under load; it passes alone (1 passed).

## Deviations from Plan
- TDD RED/GREEN not committed separately: tests and implementation were committed together per task (tests were written first, but no failing-state commit was made).

## Known Stubs
None.

## Self-Check: PASSED
