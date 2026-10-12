---
phase: 20-limits-verification-script-and-report-day-30
plan: 05
subsystem: verification
tags: [stdlib, cli, verification, rate-limit, lan]
requires: []
provides:
  - "scripts/verify_service.py: stdlib-only verification CLI (VER-01, VER-02, VER-03)"
affects: [20-06, 20-07, 20-08]
key-files:
  created:
    - scripts/verify_service.py
    - tests/test_verify_service.py
metrics:
  completed: 2026-10-12
---

# Phase 20 Plan 05: Verification Script Summary

Single-file, standard-library-only CLI that checks reachability and authentication, runs N parallel real-model turns with timings and queue waits, and demonstrates the 429 rate limit (chat/fast/both modes) and the 413 CONTEXT_OVERFLOW answer, writing console output plus verify_results.md and verify_results.json without exposing the key.

## Commits
- 20df140: feat(20-05): add stdlib-only service verification script
- d0e4622: test(20-05): cover verify_service against a local stub server

## Verification actually run
- `python scripts/verify_service.py --help`: exit 0, all 12 flags listed, `--rate-mode` default `chat`.
- AST import check: prints `stdlib only:` (no non-stdlib module).
- No key: exit 2. `AIA_API_KEY=aia_x` against closed port 9: exit 2, "server unreachable" line, both files written.
- `pytest tests/test_verify_service.py`: 26 passed (~18 s) against a loopback stub server.
- `pytest tests/ -q -x`: 1928 passed, 1 failed (`tests/test_supervisor.py::test_agent_restarts_within_5_seconds`, timeout) before stopping; the remaining files after it in the alphabet were run separately and passed. Not run against a real server or model (that is plans 20-06 and 20-08).

## Deviations from Plan
- [Rule 1] Test 20 guard checks `import httpx` / `import requests` instead of the bare words, because the module docstring legitimately contains the word "requests".
- Worktree base was corrected with `git reset --hard 9b0938e` per the startup check (the worktree started at 06402e3, which lacked the phase plan).

## Deferred Issues
- `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` fails with a timeout in this environment; it does not touch the files of this plan (out of scope, not investigated).
- An unrelated modified `.planning/HANDOFF.json` was present in the worktree and was left untouched.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
Both files exist; commits 20df140 and d0e4622 exist; STATE.md and ROADMAP.md not modified.
