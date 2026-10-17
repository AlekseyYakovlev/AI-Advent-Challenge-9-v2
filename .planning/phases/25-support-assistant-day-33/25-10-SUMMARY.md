---
phase: 25-support-assistant-day-33
plan: 10
subsystem: testing
tags: [e2e, playwright, support, mcp]
requires:
  - phase: 25-support-assistant-day-33
    provides: /support command, support MCP server, /support UI, retrieval probe
provides:
  - scripts/e2e_support_playwright.py isolated-copy browser and HTTP check of /support
  - tests/test_e2e_support_script.py unit tests of the script helpers
  - 25-E2E.md recorded run
affects: []
tech-stack:
  added: []
  patterns: [model-dependent checks recorded as NOT RUN when no chat model is loaded]
key-files:
  created: [scripts/e2e_support_playwright.py, tests/test_e2e_support_script.py, .planning/phases/25-support-assistant-day-33/25-E2E.md]
  modified: []
key-decisions:
  - "Live-model checks go through live_verdict and are NOT RUN, never PASS, when no chat model is loaded"
  - "Did not load a model into the user's LM Studio to force a fuller run"
requirements-completed: [SUP-13, SUP-07]
duration: ~30min
completed: 2026-10-17
---

# Phase 25 Plan 10: /support e2e Summary

Isolated-copy e2e script for `/support` (browser and HTTP) was written and run; the structural UI checks pass, the model-dependent checks did NOT run because no chat model was loaded in LM Studio.

## Tasks

1. Script and helper tests, commit f8b5438. `pytest tests/test_e2e_support_script.py`: 4 passed. Grep gates: 0 literal 8000/8001 URLs, "not run" appears 4 times.
2. Run and record, commit 2b1b484. `python scripts/e2e_support_playwright.py` exit 0, result line `PASS (model none; not run: 7)`.

## Observed run result (see 25-E2E.md)

Verified in a real browser on ports 18000/18001: app starts, support server added through the real «MCP серверы» form with `--data=<scratch copy>`, «Поддержка (/support)» section shows `support` checked, `/support` alone shows the notice with chip «Поддержка» and the footer, no page errors, ports freed afterwards.

NOT verified (NOT RUN, LM Studio had only the embedding model loaded): documentation base creation and marking, the `mcp__support__get_ticket` card and «Поддержка · T-100x» labels for T-1001/T-1002, label after reload, the HTTP `tool_calls[0].name` and `support.label` check, the cause-naming answers, the follow-up without identifier, and the scratch-JSON status change. Load a chat model (for example `qwen/qwen3.5-9b`) and re-run to turn these into PASS/FAIL.

Full suite: `pytest tests/ -q` 3715 passed, 14 skipped.

## Deviations from Plan

**1. [Rule 3 - Blocking] Reduced mode when no chat model is loaded.** The plan's reused preflight style would exit "blocked" without a chat model; the plan's D-17 requires a recorded "not run" instead. The script therefore runs the model-free checks (form, settings section, usage notice) and records the rest as NOT RUN. Documentation base creation is also skipped in this mode because it needs the model path.

No product-code bugs were found in plans 06-08 during the run.

## Known Stubs

None.

## Self-Check: PASSED

Files found: scripts/e2e_support_playwright.py, tests/test_e2e_support_script.py, 25-E2E.md. Commits f8b5438 and 2b1b484 exist.
