---
phase: 18-lan-exposure-day-30
plan: 05
subsystem: network
tags: [lan, bind-host, banner, network-api]
requires: ["18-01", "18-02"]
provides:
  - "shared.network.reachable_urls; BIND_HOST drives both servers; LAN banner; GET /api/v1/network"
affects: [18-03, 18-07]
tech-stack:
  added: []
  patterns: ["single source of reachable URLs for console and API"]
key-files:
  created: [shared/network.py, agent/network_api.py, tests/test_network.py, tests/test_run_banner.py, tests/test_network_info.py]
  modified: [run.py, ui/main.py, agent/main.py]
key-decisions:
  - "Link-local addresses are not filtered (D-08)"
  - "Endpoint returns {mode, addresses}; addresses empty in loopback mode"
metrics:
  duration: ~20min
  tasks: 3
  files: 8
  completed: 2026-10-12
requirements-completed: [NET-01, NET-03, NET-05]
---

# Phase 18 Plan 05: LAN binding, banner and network endpoint Summary

`settings.BIND_HOST` now binds both the UI (uvicorn in run.py) and the Agent (AgentSupervisor in ui/main.py). LAN mode prints a framed ASCII warning plus one URL per reachable address, and an authenticated `GET /api/v1/network` returns the same list from `shared.network.reachable_urls`.

## Commits
- 2302663: feat(18-05): add reachable_urls network address discovery
- 329258c: feat(18-05): bind both servers to BIND_HOST and print LAN banner
- a0ae9ae: feat(18-05): add authenticated GET /api/v1/network endpoint

## Verification (observed)
- tests/test_network.py: 13 passed; port-literal scan of shared/network.py printed ok.
- tests/test_run_banner.py + test_run_cleanup.py + test_bootstrap_admin.py: 17 passed (golden loopback output equality, LAN layout, host args).
- tests/test_network_info.py + test_network.py + test_cors.py: 22 passed; test_supervisor.py + test_network_info.py: 7 passed.
- Full suite: 2300 passed, 11 skipped, 1 failed (tests/test_supervisor.py::test_agent_restarts_within_5_seconds, timeout). It passed when re-run alone (7 passed); the failing run overlapped with a second pytest process sharing test_app.db and CPU, so I treat it as contention flakiness, not a regression.
- Not run: real sockets on all interfaces or a browser on a LAN address (assigned to plan 18-07); `python run.py` was not started.

## Deviations from Plan

**1. [Rule 3 - Blocking] Overlapping pytest runs**
- I started a targeted run while the full suite was running in the background; both use test_app.db, so the targeted run errored with PermissionError. Re-ran after the suite finished; no code change.

state.record-metric rejected my call (invalid argument shape) and state.update-progress found no Progress field, so those two STATE.md updates were not applied. Requirements NET-01/03/05 were already marked complete.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
