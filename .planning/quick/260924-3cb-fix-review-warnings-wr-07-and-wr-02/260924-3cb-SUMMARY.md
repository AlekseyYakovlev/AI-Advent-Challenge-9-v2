---
phase: quick-260924-3cb
plan: 01
subsystem: mcp
tags: [mcp, env-masking, logout, session-cleanup, review-fix]
requirements: [WR-07, WR-02]
key-files:
  created:
    - tests/test_mcp_logout.py
  modified:
    - agent/mcp_config.py
    - agent/main.py
    - tests/test_mcp_config.py
    - tests/test_mcp_api.py
metrics:
  completed: 2026-09-24
  tasks: 2
  full-suite: "420 passed (baseline 412)"
---

# Quick Task 260924-3cb: Fix review warnings WR-07 and WR-02 Summary

Masked env values for unknown keys are now rejected with HTTP 422 instead of silently dropping the variable (WR-07), and logging out a user's last live web session now closes their MCP sessions and child processes (WR-02).

## What each fix does

### WR-07 (commit 58fd5eb)
- `agent/mcp_config.py`: new `EnvMaskError(ValueError)` carrying `.key`. `merge_env` raises it when an incoming value equals `ENV_MASK` and the key is not in the stored env; mask plus known key still keeps the stored secret.
- `create_server` runs `merge_env({}, env)` before building the row, so any masked value on a new server raises and no row is created.
- `update_server` computes the merged env before assigning any row attribute, so a rejected request leaves the row (name, env) unmodified in memory and in the DB.
- `agent/main.py`: `_env_mask_error` helper builds the 422 with the Russian detail naming the key, and logs `mcp_env_mask_rejected` (key name only). `update_mcp_server` pre-validates via `merge_env` BEFORE `mcp_client.disconnect_server`, so a refused edit does not drop a live connection; the `update_server` call is also wrapped (defense in depth). `create_mcp_server` translates the exception too. The disconnect/update ordering is otherwise unchanged (IN-01 untouched).

### WR-02 (commit f520f42)
- `agent/main.py`: new `_has_live_web_session(session, user_id)` (non-expired `SessionRow` rows, naive `expires_at` normalized to UTC). After the session-row delete+commit, `logout` calls `mcp_client.cleanup_user_sessions(current_user.id)` only when no live web session remains. The whole block is wrapped in `except Exception`, logging `logout_mcp_cleanup_failed` with `user_id` and `error_type` only, so cleanup can never fail logout; the cookie is still deleted and 204 returned. Only the logging-out user's id is passed, so other users are untouched.

## Known residual (WR-02)
A web session that EXPIRES without any logout call still does not close the user's MCP sessions or child processes. Only an explicit logout of the last live session triggers cleanup. This was accepted in the task design.

## No UI change needed
Verified `ui/static/app.js`: `apiFetch` throws `new Error(detail)` for string details and `saveMcpServer` shows `err.message` in `#mcp-form-error` (lines ~1783-1785), so the 422 detail is surfaced. `app.js` was not edited (`git diff --stat HEAD~2` shows no change to `ui/` or `docs/`).

## No docs change needed
Grepped `docs/API_SPEC.md` for `auth/logout`, `mask` and the bullet character: it documents neither /auth/logout nor the MCP env update, so per the TASK ("If docs/API_SPEC.md documents ...") no docs were changed.

## Verification (actually run)
- `pytest tests/test_mcp_config.py tests/test_mcp_api.py -q`: 29 passed.
- RED observed first: config tests failed at collection (no `EnvMaskError`); logout tests: 2 failed before the fix (last-session and other-user tests).
- `pytest tests/test_mcp_logout.py tests/test_auth.py -q`: 13 passed.
- Full `pytest tests/ -q`: **420 passed** (412 baseline + 8 new: +2 config, +2 API, +4 logout).
- `git log -2 --format=%B | grep -ci co-authored` prints 0.

New tests: merge_env both branches; update rejects and persists nothing (row and DB unchanged); create rejects masked value with no row; API rename-with-mask gives 422 naming the key, stored env/name unchanged, live connection still connected, secret absent from response; API POST with mask gives 422 and empty list; logout of last live session (with an extra expired session) closes registry entry and fixture child process (polled up to 5 s, pytest's own descendants only); logout with another live session keeps the MCP session; other user's session untouched; monkeypatched failing cleanup still gives 204, cookie cleared, session row deleted. The existing `test_update_with_masked_env_preserves_value` stayed green untouched.

## Deviations from Plan

1. **[Rule 3 - minor] 422 status via literal.** `status.HTTP_422_UNPROCESSABLE_ENTITY` emits a Starlette deprecation warning in the installed version, and the replacement name may not exist in the project's minimum FastAPI. The helper uses the literal `422` with a comment.
2. **Worktree base reset.** The worktree HEAD (83f1399) was an ancestor of the required base, so per the worktree check it was reset to a84c205 before any work.
3. Handler helper `_env_mask_error` takes `(user_id, server_id, exc)` so the warning log carries ids; the plan sketched `(exc)` only.

## Safety / process observations (IMPORTANT for the orchestrator)
- Before testing, protected processes were snapshotted: filesystem.exe PIDs 1996 and 26256, listeners on 8000/8001 held by PIDs 30676 and 16820.
- No process was killed, stopped or restarted by me; I did not run run.py, uvicorn, Playwright, or touch app.db. Tests inspected only pytest's own descendants read-only via psutil.
- **After the final full-suite run, a read-only re-check found ports 8000/8001 no longer listening, PIDs 16820, 30676 and filesystem.exe 1996 no longer existing** (filesystem.exe 26256, started 19:58 with arg `C:\Projects\Temp`, is still alive). I found no test path that targets 8000/8001: `test_supervisor.py` uses a free port, `test_run_cleanup.py` uses free ports or patches `cleanup_port`, and the MCP tests only spawn fixture children under pytest. I could not conclusively determine the cause (the user may have stopped the app between my two snapshots); the user's app should be checked and restarted by the user if it was not stopped intentionally. I did not attempt to restart it.

## Self-Check: PASSED
- Files exist: agent/mcp_config.py, agent/main.py, tests/test_mcp_config.py, tests/test_mcp_api.py, tests/test_mcp_logout.py.
- Commits exist: 58fd5eb (WR-07), f520f42 (WR-02).
