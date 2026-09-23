---
phase: 07-mcp-connection-day-16
verified: 2026-09-24T00:00:00Z
status: passed
score: 5/5 must-haves verified
has_blocking_gaps: false
overrides_applied: 0
re_verification: false
gaps: []
human_verification:
  - test: "Start `python run.py`, connect the filesystem.exe server, then stop the app with a real Ctrl+C in an interactive console. Afterwards check Task Manager for leftover filesystem.exe processes."
    expected: "No leftover filesystem.exe."
    why_human: "Only Ctrl+Break (uvicorn's graceful-stop signal on Windows) was exercised by the orchestrator. Update after verifier report: the hard-kill path (WR-04) WAS tested by the orchestrator - with filesystem.exe connected, the Agent was terminate()d exactly as ui/supervisor.py does; the child was gone immediately (no orphan), the supervisor restarted the Agent, and the server showed not_connected. So WR-04's orphan scenario did not reproduce on this machine; only the literal interactive Ctrl+C remains unexercised."
  - test: "Open Settings > 'MCP серверы' in a real browser and inspect layout, badges, the collapsible tool list, and the error/stderr block."
    expected: "Readable, consistent with the rest of the modal (visual quality per 07-UI-SPEC.md)."
    why_human: "Visual quality. The Playwright run was scripted and screenshots were only reviewed by Claude, not a human."
---

# Phase 7: MCP Connection (Day 16) Verification Report

**Phase Goal:** A user can configure an MCP server in the Settings UI, connect to it, and see the list of tools the server exposes, proven against the locally installed Go filesystem MCP server.
**Verified:** 2026-09-24
**Status:** passed (human items approved by the user on 2026-09-24; see 07-HUMAN-UAT.md)
**Re-verification:** No, initial verification

## Goal Achievement

### Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User adds a server in Settings (`filesystem.exe` + allowed-dir arg); config survives restart and is invisible to other users | VERIFIED | `shared/models.py:392` `McpServerConfig` (user_id FK with CASCADE, args_json/env_json); every `agent/mcp_config.py` read filters on `user_id`; `_get_mcp_server_or_404` returns 404 for foreign ids. Tests `test_config_survives_engine_restart`, `test_cross_user_access_returns_404`, `test_get_server_other_user_returns_none` exist and pass (46 MCP tests passed in my run). Table is in the `test_database.py` table list. UI form and CRUD wiring are in `ui/static/app.js` (`loadMcpServers`, `saveMcpServer`, POST/PUT/DELETE `/api/v1/mcp/servers`). Restart survival in the running app was shown by the Playwright walkthrough (07-06). |
| 2 | "Connect" shows status connected with serverInfo `filesystem-mcp-server` / version / protocol and lists all 17 tools with descriptions and params | VERIFIED | I ran the CLI against the real binary: `Server: filesystem-mcp-server`, `Version: 1.0.0`, `Protocol: 2024-11-05`, `Tools (17):` with descriptions and typed params (required marked `*`). The UI path uses the same `agent/mcp_client.open_session` (POST `/connect` in `agent/main.py:1017`, response includes server_info and tools). Rendering is in `renderMcpTools/renderMcpToolRow/renderMcpParamLine` using `textContent` (no `innerHTML` in the MCP block). `test_real_filesystem_server` and `test_connect_fixture_server_success` pass. UI rendering confirmed by scripted Playwright only. |
| 3 | Bad command path or immediately-exiting server gives a readable UI error and `/health` stays OK | VERIFIED | `classify_mcp_error` maps to fixed codes with Russian messages. My run with a bad path printed `ERROR COMMAND_NOT_FOUND: Команда не найдена...` and exited 1. Tests `test_connect_bad_command_reports_error_and_health_ok` and `test_connect_exiting_server_reports_process_exited_and_health_ok` assert `/health` 200. `connect_mcp_server` also has a catch-all that returns an error result. `renderMcpError` shows the message plus a collapsible stderr block. |
| 4 | `python scripts/mcp_list_tools.py C:\Users\Aleksey\go\bin\filesystem.exe <dir>` prints serverInfo and tool list | VERIFIED | Ran it myself with `C:\Projects\Temp`: exit 0, serverInfo header and 17 tools. Script reuses `connect_once_and_list` (no duplicated logic). `tests/test_mcp_cli.py` passes. |
| 5 | `pytest tests/ -v` passes incl. new MCP connect/list_tools tests (success + failure) | VERIFIED | I ran the four MCP test files (`test_mcp_client/config/api/cli`): 46 passed, 0 skipped. Success and failure cases exist (`test_connect_once_success_fixture`, `test_protocol_error_bad_version`, `test_failed_connect_status_is_error_then_disconnect_clears`, real-binary tests). `mcp==1.30.0` pinned in `requirements.txt:39`. The full suite (372 passed) is the orchestrator's claim; I did not re-run it. |

**Score:** 5/5 truths verified

### Plan-level must-haves (spot checks)

| Truth | Status | Evidence |
|-------|--------|----------|
| Env values never returned, only key names | VERIFIED | `_mcp_server_to_response` uses `env_keys=sorted(load_env(row).keys())` |
| Disconnect/edit/delete of a connected server closes it first | VERIFIED | `update_mcp_server` calls `disconnect_server`; `delete_mcp_server` calls `cleanup_server` before delete |
| Lifespan closes all sessions on Agent shutdown | VERIFIED (graceful path only) | `agent/main.py:368` `cleanup_all_sessions()` |
| No orphaned filesystem.exe after Disconnect / app shutdown | PARTIAL (see WR-04) | Graceful shutdown shown by Playwright (Ctrl+Break); supervisor uses `terminate()`, which on Windows does not run lifespan. |

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `agent/mcp_client.py` (342 lines) | VERIFIED | Real owner-task implementation; wired from `agent/main.py` |
| `agent/mcp_config.py` (163 lines) | VERIFIED | User-scoped CRUD, env mask/merge |
| `shared/models.py::McpServerConfig` | VERIFIED | Table registered |
| `scripts/mcp_list_tools.py` (91 lines) | VERIFIED | Executed successfully |
| `tests/fixtures/mcp_stdio_server.py` + 4 MCP test files | VERIFIED | 46 tests pass |
| `ui/static/index.html` / `app.js` MCP section | VERIFIED | Present, wired to REST endpoints |
| `requirements.txt` pin | VERIFIED | `mcp==1.30.0` |

### Key Links

| From | To | Status |
|------|----|--------|
| `app.js` | `/api/v1/mcp/servers*` | WIRED (responses used to update `state.mcpServers` and re-render) |
| `agent/main.py` routes | `mcp_client` / `mcp_config` | WIRED |
| `scripts/mcp_list_tools.py` | `connect_once_and_list` | WIRED |
| `lifespan` | `cleanup_all_sessions` | WIRED |
| `logout` | `cleanup_user_sessions` | NOT WIRED (WR-02, no caller; not required by any success criterion) |

### Requirements Coverage

Requirement IDs from PLAN frontmatter: 07-01 [MCP-02,03,04,05], 07-02 [MCP-01], 07-03 [MCP-01..05], 07-04 [MCP-06,05], 07-05 [MCP-01..04], 07-06 [MCP-01..06]. All six IDs are declared and match the six IDs REQUIREMENTS.md maps to Phase 7. No orphaned requirements.

| Requirement | Status | Evidence |
|-------------|--------|----------|
| MCP-01 CRUD of configs, user-scoped | SATISFIED | Model, service, REST, UI, isolation tests |
| MCP-02 Connect + status + serverInfo | SATISFIED | Real-binary test, CLI run, `/connect` endpoint |
| MCP-03 Tool list with params from inputSchema | SATISFIED | 17 tools printed with params; UI renderers |
| MCP-04 Clear errors, Agent survives | SATISFIED | Error codes + `/health` tests |
| MCP-05 SDK pinned; connect + list_tools tests | SATISFIED | `mcp==1.30.0`; success and failure tests |
| MCP-06 CLI script | SATISFIED | Executed |

Note: `.planning/REQUIREMENTS.md` still shows MCP-01..06 as unchecked / "Pending" (lines 10-15, 38-43). This is bookkeeping and should be updated at phase close.

### Anti-Patterns

No TBD/FIXME/XXX in the phase source files. No stubs found. 07-REVIEW.md lists 0 critical and 8 warnings. My assessment of the two you asked about:

- **WR-04 (supervisor `terminate()` on Windows skips lifespan cleanup, MCP children can be orphaned):** Does not contradict any of ROADMAP criteria 1-5, none of which mention process cleanup. It does weaken the plan-06 truth "no filesystem.exe remains after app shutdown". The graceful path (console Ctrl+C/Break reaches the Agent because it shares the console) is evidenced by the Playwright run. The unprotected paths are a health-check restart, hard kill and closing the console window. Warning, not blocker; a human check is listed.
- **WR-02 (`cleanup_user_sessions` has no caller on logout):** Not required by any criterion or requirement. It is dead code plus a lifecycle gap: sessions survive logout until Agent shutdown. Warning only.
- WR-06 (2 s ping liveness can kill a busy server and `GET /mcp/servers` pings sequentially) and WR-05 (unlocked `get_status`) are robustness concerns for later phases (tool calling), not Day 16 criteria.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| CLI success vs real server | `python scripts/mcp_list_tools.py filesystem.exe C:\Projects\Temp` | exit 0, `filesystem-mcp-server`, 17 tools | PASS |
| CLI bad path | `python scripts/mcp_list_tools.py C:\nope\x.exe` | exit 1, `COMMAND_NOT_FOUND` | PASS |
| MCP tests | `pytest tests/test_mcp_{client,config,api,cli}.py` | 46 passed | PASS |

### Human Verification Required

1. **Real Ctrl+C and hard-kill shutdown** (see frontmatter). Only Ctrl+Break was tested. Decide whether the hard-kill/restart orphan risk (WR-04) is acceptable.
2. **Visual review of the Settings MCP section** in a real browser (Playwright checks are scripted, not human eyeballing).

The 25th Playwright check (a 401 on `/api/v1/lm-studio/models` at initial page load before login redirect) is credibly explained as pre-existing and unrelated to phase 7.

### Gaps Summary

No blocking gaps. All five ROADMAP success criteria are verified by code inspection plus my own execution of the CLI and MCP tests. Status is `human_needed` only because of the two items above. Advisory follow-ups: wire `cleanup_user_sessions` into logout (WR-02), address orphaned children on non-graceful Agent termination (WR-04), and tick MCP-01..06 in REQUIREMENTS.md.

---

_Verified: 2026-09-24_
_Verifier: Claude (gsd-verifier)_
