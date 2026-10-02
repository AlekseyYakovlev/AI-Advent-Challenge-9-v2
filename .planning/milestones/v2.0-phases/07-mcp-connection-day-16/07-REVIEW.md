---
phase: 07-mcp-connection-day-16
reviewed: 2026-09-24T00:00:00Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - agent/main.py
  - agent/mcp_client.py
  - agent/mcp_config.py
  - agent/schemas.py
  - requirements.txt
  - scripts/mcp_list_tools.py
  - shared/config.py
  - shared/models.py
  - tests/conftest.py
  - tests/fixtures/mcp_stdio_server.py
  - tests/test_database.py
  - tests/test_mcp_api.py
  - tests/test_mcp_cli.py
  - tests/test_mcp_client.py
  - tests/test_mcp_config.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 8
  info: 6
  total: 14
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-09-24
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

Phase 7 adds an MCP stdio client (owner-task pattern), a user-scoped config store, REST endpoints, a CLI, and a settings-modal UI. Ownership scoping is correct (404 on foreign IDs, `(user_id, server_id)` registry keys). Env values are never returned to the client, and the child process gets the SDK's safe default environment, so agent secrets such as `DEEPSEEK_API_KEY` do not leak to it. The UI renders all server-supplied text via `textContent`, so there is no XSS path. `mcp==1.30.0` exists and is installed.

No BLOCKER-tier defects were found. The main weaknesses are process-lifecycle leaks (child processes outliving logout, cancelled requests, or an Agent kill), unlocked mutation of the session registry in `get_status`, an aggressive liveness check, and a silent env-var loss in the edit form.

The convention CLI (`gsd-tools verify conventions`) was not available in this environment, so no CONVENTION findings from the rule packs are included. The CONVENTION items below are manual observations.

## Structural Findings (fallow)

Not provided for this run. One structural fact was confirmed manually: `agent/mcp_client.py::cleanup_user_sessions` has no callers (see WR-02).

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Any authenticated user can run arbitrary commands, with no request-origin defense on the REST endpoints

**File:** `agent/schemas.py:190`, `agent/main.py:966-1046`
**Issue:** `POST /api/v1/mcp/servers` plus `/connect` executes an arbitrary user-supplied executable and args on the host. This is an accepted design decision ("any non-empty command is accepted") under the equal-admin model. It turns any request forgery or XSS elsewhere in the app into remote code execution. The session cookie is `SameSite=Lax`. Ports 8000 and 8001 on localhost are same-site, so any other local web app on another localhost port can send cookie-bearing requests. The WebSocket path has an origin check, but the REST layer has none. FastAPI 0.115 parses a body with no `Content-Type` as JSON, so a "simple" cross-origin request without a preflight is feasible.
**Fix:** Add an `Origin`/`Referer` allow-list check (reuse `CORS_ORIGINS`) as a dependency on the state-changing MCP routes, or on all mutating routes. Optionally reject requests whose `Content-Type` is not `application/json`. At minimum, document the residual risk in `docs/ARCHITECTURE.md`.

### WR-02: MCP sessions and child processes survive logout; `cleanup_user_sessions` is dead code

**File:** `agent/mcp_client.py:329-333`, `agent/main.py:444-466`
**Issue:** `cleanup_user_sessions` is never called. `logout` only deletes the session row. All of the user's MCP child processes keep running until Agent shutdown, and their state (including `stderr_tail`) is served again on the next login. There is no other path that closes sessions when a user's last web session expires.
**Fix:** Either call `await mcp_client.cleanup_user_sessions(current_user.id)` in `logout` (only when the user has no other live sessions, if multi-tab or multi-device use matters), or delete the function and document that sessions are intentionally persistent.

### WR-03: `open_session` leaks the owner task and child process if the request is cancelled

**File:** `agent/mcp_client.py:197-205`
**Issue:** `open_session` awaits `handle.ready.wait()` and catches only `TimeoutError`. If the awaiting request is cancelled (server shutdown, or a future timeout wrapper), `CancelledError` propagates. The owner task keeps running, and after the handshake `deadline.reschedule(None)` lets it park forever on `close_requested`. The handle is never registered in `_sessions`, so no code path can close it. The result is an orphaned MCP server process and an open temp file.
**Fix:**
```python
try:
    await asyncio.wait_for(handle.ready.wait(), timeout + READY_GRACE_SECONDS)
except asyncio.CancelledError:
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await handle.task
    handle.errfile.close()
    raise
except TimeoutError:
    ...
```

### WR-04: Child MCP processes are orphaned when the supervisor stops or restarts the Agent

**File:** `agent/main.py:365-369` (lifespan), `ui/supervisor.py:115-123`
**Issue:** `cleanup_all_sessions` runs only in the FastAPI lifespan shutdown. The supervisor stops the Agent with `process.terminate()` and then `kill()`, and orphan cleanup only kills processes bound to ports 8000/8001. On Windows, `terminate()` is a hard kill, so the lifespan never runs and stdio MCP servers (for example `filesystem.exe`) are left running after an Agent crash, restart, or health-check kill. Each restart can leak processes.
**Fix:** Best-effort: on Agent startup, log a warning. Longer term, launch MCP children in a Windows job object (or with `CREATE_NEW_PROCESS_GROUP` plus a parent-death watchdog), or record child PIDs in a file and reap them in supervisor orphan cleanup.

### WR-05: `get_status` mutates the registry without taking the per-server lock

**File:** `agent/mcp_client.py:298-320`
**Issue:** `connect_server`, `disconnect_server` and `cleanup_server` are serialized by `_lock_for`, but `get_status` runs `close_handle` and writes `_last_results[key]` without it. Concurrent interleavings:
1. `get_status` detects a dead handle and awaits `close_handle` (up to 5 s). Meanwhile the user disconnects, which pops `_last_results`. `get_status` then writes `_last_results[key] = PROCESS_EXITED`, so a server the user just disconnected reports an error.
2. `get_status` writes a stale `PROCESS_EXITED` after a concurrent `connect_server` has already succeeded. The stale entry survives until the next disconnect.
3. `cleanup_server` pops the lock from `_locks` outside the lock. A waiter still holding the old lock and a new caller that recreates it can then run concurrently.

**Fix:** Take `_lock_for(key)` inside `get_status` for the dead-handle branch. Re-check `_sessions.get(key) is handle` after acquiring the lock, and write `_last_results` only if that still holds. In `cleanup_server`, pop the lock while still holding it.

### WR-06: The 2-second ping liveness check will kill healthy but busy servers

**File:** `agent/mcp_client.py:286-295`, `agent/main.py:944-951`
**Issue:** `_is_alive` treats any exception or timeout on `send_ping` (2 s) as "process dead". It then closes the session, which terminates the child process, and reports `PROCESS_EXITED`. A single-threaded server busy with a long tool call or startup indexing will not answer a ping in time and will be killed by the next `GET /mcp/servers` (which is called every time the settings modal opens), even though it is healthy. The message "server exited" is also false in this case. `list_mcp_servers` pings sequentially, so N unresponsive servers cost 2N seconds.
**Fix:** Treat only a finished owner task, a closed handle, or a broken-stream error as "dead". Treat a ping timeout as "unresponsive" (keep the session, or require several consecutive failures). Ping the servers in `list_mcp_servers` concurrently with `asyncio.gather`.

### WR-07: Renaming an env var in the edit form silently drops it

**File:** `agent/mcp_config.py:39-53`, `ui/static/app.js:1518-1526,1541-1557`
**Issue:** The edit form pre-fills `KEY=•••` for every stored variable. If the user renames a key and keeps the mask (`NEW_KEY=•••`), `merge_env` deliberately discards the entry because `NEW_KEY` is not in `stored`, and the old key is dropped as well. The variable, possibly a secret, is lost with no error. The UI shows the "saved" toast and the user cannot recover the value.
**Fix:** Reject with a 422 (or return a clear message) when an incoming value equals the mask for a key that is not in the stored env, instead of silently continuing. Alternatively, have the UI block a mask value on a new or renamed key before submit.

### WR-08: Error classification mislabels non-command OSErrors, and the temp stderr file is unbounded

**File:** `agent/mcp_client.py:82-83`, `agent/mcp_client.py:53-55`
**Issue:**
- (a) Every remaining `OSError` maps to `COMMAND_NOT_FOUND` ("check the path to the executable"). A nonexistent or invalid `cwd` (`FileNotFoundError`/`NotADirectoryError` from `CreateProcess`), `PermissionError`, or "not a valid Win32 application" produce that same misleading message. Users are told to fix the command when the cwd is wrong.
- (b) `handle.errfile` is an anonymous temp file that the child writes stderr into for the whole life of a persistent session. It is only truncated by close. A chatty server grows the file without bound and it is read fully into memory on every failure via `errfile.read()`.

**Fix:** (a) Distinguish `FileNotFoundError` on the command from other `OSError`s (or include the errno and filename in the message, or add a `SPAWN_FAILED` code). Validate `cwd` up front with `os.path.isdir`. (b) Read only the tail (seek to `max(0, size - 64 KiB)` before reading). Consider a size cap or periodic truncation for long-lived sessions.

## Info

### IN-01: Update endpoint disconnects before the DB write can fail

**File:** `agent/main.py:990-1006`
**Issue:** `update_mcp_server` calls `disconnect_server` before `update_server`. If the commit fails, the user loses a working connection and the config is unchanged. Also, `disconnect_server` runs for every PUT and creates a lock in `_locks` that is only removed on delete, so locks accumulate for edited servers.
**Fix:** Disconnect after a successful commit, and drop the lock entry when no session or result exists.

### IN-02: Double-submit creates duplicate servers

**File:** `ui/static/app.js:1544-1565`
**Issue:** `saveMcpServer` has no in-flight guard, and there is no server-side uniqueness on `(user_id, name)`. A double click on "Сохранить" creates two identical configs.
**Fix:** Disable `btn-mcp-save` while the request is running.

### IN-03: Redundant error handling in the connect path

**File:** `ui/static/app.js:1451-1452,1676-1695`
**Issue:** `connectMcpServer` catches and toasts its own errors, so the `.catch(...showToast)` wrapper at the call site is dead. The `connecting` status is client-only, and the per-server "connecting" indicator is lost if the modal is re-opened (`loadMcpServers` re-renders with stale `state.mcpConnecting` handling).
**Fix:** Remove the redundant `.catch`, or rethrow from the function.

### IN-04: Runtime version mismatch with documented baseline

**File:** `agent/mcp_client.py:65,135`
**Issue:** The code uses `asyncio.timeout` and `BaseExceptionGroup`, which need Python 3.11+. CLAUDE.md states Python 3.8+ (and 3.10+ typing syntax). Nothing pins or checks the minimum version.
**Fix:** Document Python 3.11+ in CLAUDE.md, README and requirements, or add a startup assertion.

### IN-05: Personal absolute paths hard-coded in tests and UI placeholder

**File:** `tests/test_mcp_client.py:23-26`, `ui/static/index.html` (mcp-command placeholder)
**Issue:** The default `C:\Users\Aleksey\go\bin\filesystem.exe` is embedded in tests and in the visible input placeholder. The tests skip when the file is missing, so they are safe, but the placeholder is developer-specific in shipped UI. `test_get_status_detects_exited_process` also relies on `sleep(2.5)` for a 1.0 s timer, which is timing-sensitive on slow runners.
**Fix:** Use a generic placeholder (for example `/path/to/mcp-server`). Poll for the expected status with a deadline instead of a fixed sleep.

### IN-06: Minor style deviations (CONVENTION-tier, advisory)

**File:** `agent/main.py:1027-1029`
**Issue:** The multi-line `logger.error(...)` call is wrapped differently from the rest of the file (parameters not one per line, no trailing comma). Deviates from the surrounding style; non-blocking.
**Fix:** Format one kwarg per line with a trailing comma.

---

_Reviewed: 2026-09-24_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
