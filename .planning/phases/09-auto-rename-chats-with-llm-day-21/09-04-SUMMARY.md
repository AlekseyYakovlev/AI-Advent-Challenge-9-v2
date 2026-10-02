---
phase: 09-auto-rename-chats-with-llm-day-21
plan: 04
subsystem: docs
tags: [docs, regression, uat]
requires: [09-01, 09-02, 09-03]
provides: [docs for chat auto-titling, full-suite regression result]
affects: [docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md, docs/USER_GUIDE.md]
key-files:
  modified: [docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md, docs/USER_GUIDE.md]
requirements-completed: [TITLE-01, TITLE-02, TITLE-03, TITLE-04, TITLE-05, TITLE-06]
metrics:
  tasks: 2 of 2 completed (task 2 = browser UAT, no code commit)
---

# Phase 9 Plan 04: Docs sync and regression gate Summary

API_SPEC, ARCHITECTURE, TESTING_GUIDE and USER_GUIDE now describe the shipped auto-title behavior (constants taken from `agent/titles.py`), and the full suite is green; the browser UAT scenarios A-F were run in headless Chromium on an isolated copy and all pass.

## Tasks

1. Docs sync + full-suite gate: 925c40e
2. Browser UAT on isolated copy (UI 18000 / Agent 18001): executed, results below; no code commit.

## Verification (observed)

- `python -m pytest tests/ -q`: `990 passed, 9 warnings in 197.30s (0:03:17)` (includes tests/test_static_js_syntax.py).
- grep checks: `chat_title_updated` in API_SPEC = 3 matches, `test_titles_ws.py` in TESTING_GUIDE = 2, `New Chat` in USER_GUIDE = 1; ARCHITECTURE contains "## Chat auto-titling", `title_tasks` and `title = 'New Chat'`.

## Browser UAT

Environment: copy of `agent/ shared/ ui/ run.py` in `%TEMP%/aiadvent-uat-09` (outside the repo, no `.env`), ports patched only in the copy (`app.js`, `login.html`, `agent/state.py` CORS origins); the pre-start grep showed no remaining 8000/8001 except the `shared/config.py` defaults, overridden by env (`UI_PORT=18000`, `AGENT_PORT=18001`, scratch `DB_PATH`). Users `uat1`/`uat2` seeded via `shared.database`/`shared.auth`. Headless Chromium via Python Playwright. LLM: LM Studio `qwen/qwen3.5-9b` (already loaded, not touched). A `page.on("dialog")` handler was registered; **no dialog was recorded in any run**.

| Scenario | Result | Observed |
| -------- | ------ | -------- |
| A live rename | PASS | After the first answer (no reload) sidebar and header both showed `Как настроить WebSocket в FastAPI?` (34 chars, no newline, not "New Chat"); a `chat_title_updated` WS frame was seen. |
| B second turn | PASS | After a second message the title stayed `Как настроить WebSocket в FastAPI?`. |
| C reload | PASS | After `reload()` sidebar and header still show `Как настроить WebSocket в FastAPI?`. |
| D cross-chat delivery | PASS (rerun) | Second chat sent "Посоветуй рецепт плова"; the first chat was clicked the moment the answer ended. Header stayed `Как настроить WebSocket в FastAPI?` while the second chat's sidebar button became `Посоветуй рецепт плова` (no "New Chat" left). The first attempt (prompt "борща") delivered `Посоветуй рецепт борща` via `chat_title_updated`, but my script's click selector was malformed (non-ASCII JSON-escaped), so that attempt was a script error, not an app failure; D was rerun separately with the "плова" prompt. |
| E XSS probe | PASS | `<img src=x onerror=alert(1)> что такое HTML?` produced title `что такое HTML?`; 0 dialogs, 0 `img` in `#chat-list` / `#chat-title`, no `<` or `>` in the title. (The user message body renders an `<img src=x>` through the sanitized markdown path, causing harmless 404s on `/static/x`; no `onerror` fired, no dialog.) |
| F isolation | PASS | `uat2` (separate browser context) sidebar showed only its own `New Chat`; none of uat1's titles (`что такое HTML?`, `Посоветуй рецепт борща`, `Как настроить WebSocket в FastAPI?`) appeared. |

Script artifacts hit and fixed on the way (not app failures): (1) the login page is at `/static/login.html`; (2) the app's `init()` creates the first chat itself and my script clicked "new chat" while it was still loading, creating two chats and leaving the WS on a different chat than the header (only reachable by clicking "new chat" within ~25 ms of page init); (3) the first sends did not wait for streaming to start before waiting for the input to re-enable. Earlier runs on a dirty scratch DB were discarded; the results above come from runs on a cleaned DB.

Cleanup: the copy's process tree (run.py + agent, only the PIDs I started) was stopped; nothing listens on 18000/18001 any more (only TIME_WAIT sockets) and `/health` on 18001 no longer answers; the scratch DB was deleted. Screenshots (A-F, D2) and the scripts are in `%TEMP%/aiadvent-uat-09/shots` and `uat*.py`, outside the repo. Ports 8000/8001 were never started, stopped, killed or reconfigured.

Verified: A-F as described. Not verified: other models (e.g. DeepSeek); title generation under LM Studio failure or timeout in a real browser (covered by pytest only).

## Open items for the user

None blocking. Optional: create a chat in your own app, send a first message and watch the sidebar rename itself.

## Deviations from Plan

- Worktree base was reset to 12fb080 per the branch check (merge-base was 64d13b5).
- Task 2 was first skipped (LM Studio unreachable), then executed in a continuation run once LM Studio answered.
- Worktree base was reset again to b35b298 for the continuation run.

## Known Stubs

None.

## Self-Check: PASSED
