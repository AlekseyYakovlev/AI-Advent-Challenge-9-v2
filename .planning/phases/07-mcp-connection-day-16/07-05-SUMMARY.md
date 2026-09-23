---
phase: 07-mcp-connection-day-16
plan: 05
subsystem: ui
tags: [mcp, vanilla-js, settings-modal, tailwind, xss-safe]

requires:
  - phase: 07-mcp-connection-day-16
    provides: "/api/v1/mcp/servers REST API (07-03)"
provides:
  - "MCP серверы section in the Settings modal (markup)"
  - "MCP controller in app.js: list, status badges, CRUD, connect/disconnect, tools with params and raw JSON, error and stderr display"
affects: [07-06]

tech-stack:
  added: []
  patterns:
    - "Dynamic rows built with createElement and textContent only; fold state kept in state.mcpExpanded so it survives re-renders"

key-files:
  created: []
  modified:
    - ui/static/index.html
    - ui/static/app.js

key-decisions:
  - "MCP section is a sibling of #settings-form (not inside it), so it is independent of the modal Save button and per-chat checkbox"
  - "No innerHTML anywhere in the MCP block; every server-provided string uses textContent"
  - "Empty cwd is sent as null so an edit can clear a previously stored cwd"

requirements-completed: [MCP-01, MCP-02, MCP-03, MCP-04]

duration: 20min
completed: 2026-09-24
---

# Phase 7 Plan 05: MCP Settings UI Summary

**"MCP серверы" section in the Settings modal: server list with status badges, inline add/edit form with masked env, connect/disconnect, serverInfo, collapsible tool list with params and raw inputSchema JSON, and Russian error plus stderr display, all built via textContent.**

## Accomplishments

- `ui/static/index.html`: settings panel widened to `max-w-2xl` with `max-h-[90vh] overflow-y-auto`; new `#mcp-section` placed after `</form>` with header, add button, inline form (`#mcp-name`, `#mcp-command`, `#mcp-args`, `#mcp-env`, `#mcp-cwd`, `#mcp-enabled`, `#mcp-form-error`, save/cancel), empty state and `#mcp-server-list`. UI-SPEC copy and input classes used verbatim; no new `<style>` block.
- `ui/static/app.js`: block between `// ---- MCP servers (Phase 7) ----` and `// ---- end MCP servers ----` with 14 required functions plus small helpers (`mcpEl`, `replaceMcpServer`, `renderMcpToolRow`, `renderMcpParamLine`, `renderMcpError`). `openSettingsModal()` calls `loadMcpServers()` without awaiting it (lazy liveness, does not block modal display). Buttons wired in `bindEvents()`.
- Env edit shows `KEY=•••` and sends the mask back so the server keeps stored values; connect uses a `connecting` badge and disables all row buttons in flight; Подключить is disabled for disabled servers.

## Task Commits

1. Task 1: MCP section markup - `b265f7e`
2. Task 2: MCP controller in app.js - `b08cbfc`

## Verification (observed)

- Task 1 verify command printed `ok`; `grep -c "MCP серверы"` = 1, `grep -c "+ Добавить сервер"` = 1, `grep -c "<style"` = 1 (unchanged).
- Task 2 verify command printed `ok` (14 functions present, no unsanitized innerHTML, openSettingsModal calls loadMcpServers). `Показать журнал сервера (stderr)` count = 1, `Инструменты (` count = 1, no `shlex`/`split(' ')` matches for MCP, `/api/v1/mcp/servers` occurrences = 6.
- `node --check ui/static/app.js`: syntax OK.
- Pure helpers exercised under node: `describeMcpParams` (array types joined with `|`, missing type -> `any`, null schema -> `[]`), `parseMcpArgs` (drops blank lines, strips `\r`, no shell parsing), `parseMcpEnv` (splits at first `=`, `Строка N: ожидается KEY=VALUE` on missing `=` or empty key).
- `pytest tests/ -q`: 372 passed.
- Not verified: rendering and interaction in a real browser (fold toggles, buttons, modal scroll, live connect against a real MCP server). That is the scope of 07-06.

## Deviations from Plan

**1. Worktree base reset** - HEAD started at `83f1399` (ancestor of the required base); ran `git reset --hard 0b2dcf4` after the per-agent branch check passed.

**2. [Rule 3 - Blocking] CRLF line endings** - `index.html` and `app.js` use CRLF; edits were applied with a script that preserves CRLF so the diffs contain only the intended additions (46 and 355 lines).

**3. Minor additions** - `renderMcpError` also shows the optional `detail` field (slate-500, textContent) beneath the error message, per the UI-SPEC's mention of error `detail`.

## Assumption Drift (advisory)

None.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. T-07-20 mitigated (textContent only, verify script rejects other innerHTML), T-07-21 mitigated (KEY=••• masking, mask sent back unchanged), T-07-22 accepted.

## Self-Check: PASSED

- ui/static/index.html and ui/static/app.js modified; this summary exists
- Commits b265f7e and b08cbfc exist
