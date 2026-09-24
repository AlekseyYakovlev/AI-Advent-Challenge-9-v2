---
phase: quick-260924-4xr
plan: 01
status: complete
commits: [faa7d94, 7c0d43e, 10c7444]
---

# Quick 260924-4xr: Phase 7 review INFO findings (IN-01..IN-06)

## Fixes
- **IN-01** (`agent/main.py::update_mcp_server`, `agent/mcp_client.py`): `disconnect_server` now runs only after `mcp_config.update_server` returned (committed), so a failed commit keeps the live connection and stored config. The WR-07 env-mask pre-validation still runs first. New `_drop_lock_if_idle` (shared with `cleanup_server`) removes idle lock entries, so repeated edits of an unconnected server no longer grow `_locks`.
- **IN-02** (`ui/static/app.js`): `state.mcpSaving` re-entry guard plus disabled save button, reset in `finally`.
- **IN-03**: dead `.catch` removed at the `connectMcpServer` call site; `connectMcpServer` toasts errors once itself. The client-only "connecting" indicator being lost on modal reopen was intentionally left.
- **IN-04**: `shared/runtime.py::check_python_version` (MIN_PYTHON 3.11), called first in `run.py::main` and at module level in `agent/main.py`. `requirements.txt` header and the two "Python 3.8+" lines in `CLAUDE.md` updated; nothing else in `CLAUDE.md` changed.
- **IN-05**: generic `#mcp-command` placeholder (`/path/to/mcp-server`); `test_get_status_detects_exited_process` polls with a 10 s deadline instead of `sleep(2.5)`.
- **IN-06**: multi-line `logger.error` in `connect_mcp_server` reformatted.

## Verification
- `pytest tests/ -q`: **477 passed** (baseline 467 + 10 new: 3 API, 1 registry, 6 runtime).
- `scripts/e2e_mcp_chat_playwright.py`: ports 8000/8001/18765 were free, exit code **0**, all checks PASS (isolated scratch DB). This covers the app loading with the edited `app.js`/`index.html`, but not the IN-02 double-click guard itself, which was **not browser-verified**.
- `filesystem.exe` PID snapshot before and after: `[]` / `[]` (none running, nothing killed).
- The plan's static check `'connectMcpServer(server).catch' not in s` false-matches `disconnectMcpServer(server).catch` (kept on purpose); verified with a `(?<!dis)` regex instead.
- CRLF preserved in every touched file. Not pushed or merged.
