---
phase: 07-mcp-connection-day-16
plan: 06
subsystem: testing
tags: [mcp, e2e, playwright, acceptance]
requires: [07-04, 07-05]
provides:
  - End-to-end acceptance evidence for ROADMAP Phase 7 success criteria 1-5
key-files:
  created: []
  modified: []
requirements-completed: [MCP-01, MCP-02, MCP-03, MCP-04, MCP-05, MCP-06]
status: complete
---

# Phase 7 Plan 06: End-to-End Acceptance Summary

Acceptance of Phase 7 against the real Go `filesystem.exe` MCP server. No code changes.

## Task 1: Automated evidence (all pass)

| Check | Result |
|---|---|
| Full suite `pytest tests/` | 372 passed |
| MCP test files (client, config, api, cli) | 46 passed, 0 skipped (both real-binary tests ran) |
| CLI vs `filesystem.exe` | exit 0, `Server: filesystem-mcp-server`, `Tools (17):` |
| CLI `filesystem.exe -list <dir>` | exit 1, `ERROR PROCESS_EXITED` |
| CLI bad path | exit 1, `ERROR COMMAND_NOT_FOUND` |
| Orphan check | PID 26256 present but pre-existing (started before phase 7 work, parent is the user's own PowerShell, args `C:\Projects\Temp`); not from this code. No new filesystem.exe left by any run. |

## Task 2: UI walkthrough (steps 1-10)

**Executed by Claude via headless Chromium (Playwright), at the user's explicit request
("Самостоятельно выполни тестирование через Playwright") - not a manual human walkthrough.**
Run against an isolated instance (scratch `DB_PATH`, default ports) so the user's real `app.db`
was not used. Script and screenshots are in the session scratchpad, not committed.

24/25 scripted checks passed; the one flagged check is a harness artifact, not a defect:

- Steps 1-10 all behaved as specified: empty state; add with masked env (`DEMO_TOKEN=•••`);
  connect shows `filesystem-mcp-server · v1.0.0 · MCP 2024-11-05`, 17 tool rows, `read_file`
  params with required `*`, raw JSON schema; app stopped gracefully (Ctrl+Break) leaves no
  filesystem.exe, restart keeps the server listed as "не подключён"; second user sees an empty
  list; bad path -> readable COMMAND_NOT_FOUND message with /health OK; `-list` -> "Процесс
  сервера неожиданно завершился." with /health OK; edit-while-connected -> disconnect toast and
  "не подключён"; disabled server -> Connect disabled; delete-while-connected -> confirm text
  mentions disconnect-before-delete and the row disappears.
- Flagged check: one `401 GET /api/v1/lm-studio/models` in step 1. Cause: the scripted
  `goto("/")` loads `index.html`, whose `init()` runs before the login redirect. Not touched
  by phase 7 (0 diff matches for that endpoint); a reload with a valid session (step 5)
  produced no 401. WebSocket refusals seen in the step 5 window are the deliberate restart.

## Caveats (not verified)

- Shutdown was tested with Ctrl+Break (graceful uvicorn stop), not a hard kill of the process tree.
- A human has not eyeballed the UI; screenshots were reviewed by Claude only.
- Stderr block for MCP errors was not exercised: `filesystem.exe -list` wrote nothing to stderr.

## Deviations

- Two harness bugs fixed while building the Playwright script (post-login URL regex, tool-row
  selector that also counted the "Инструменты (17)" header). Both were script errors, app unchanged.
