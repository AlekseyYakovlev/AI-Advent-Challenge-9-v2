# Phase 20: Limits, verification script and report (Day 30) - Context

**Gathered:** 2026-10-12
**Status:** Ready for planning

<domain>
## Phase Boundary

The service gets its protective limits and its proof of work. Limits: a per-chat messages-per-minute limit configured in `.env` and shared by WebSocket and HTTP chat (HTTP answers 429 with `Retry-After`); a temporary login block after consecutive wrong passwords; `/debug/routes` behind authentication while `/health` stays open; a cap on concurrent generations with a queue in which excess turns wait and then complete. Verification: a script that, given the server address and an API key, checks reachability and auth, runs N parallel requests against a real model, and demonstrates the rate limit and context overflow. Report: `Day30_report.md` with a live run from a second LAN machine plus a manual UI chat from another device; pytest coverage for bind host, Origin, API keys, HTTP chat and limits with matching `docs/TESTING_GUIDE.md` sections.

Covers LIM-01..LIM-05 and VER-01..VER-06.

Locked by REQUIREMENTS.md / PROJECT.md / Phases 18-19 (not re-discussed): no IP filters, no HTTPS, no new dependencies, branch `Day30`, UI text in Russian; a busy chat over HTTP answers 409 `CHAT_BUSY` immediately while WebSocket waits for the chat lock (Phase 19 D-16); HTTP error bodies are `detail` + `code`, context overflow is 413 `CONTEXT_OVERFLOW` (Phase 19 D-17); a request without `Origin` passes the Origin check (Phase 18 D-05); WebSocket endpoints stay cookie-only.

</domain>

<decisions>
## Implementation Decisions

### Message rate limit (LIM-01, LIM-02)
- **D-01:** The limit is messages per minute per chat, set in `.env`, default **30** (today it is the constant `RATE_LIMIT_MAX = 10` in `agent/ws.py`). The value `0` switches the limit off.
- **D-02:** One counter per chat is shared by WebSocket and HTTP chat: a message sent over either channel takes a slot in the same window.
- **D-03:** Every accepted attempt takes a slot, including a turn that later fails (context overflow, model unavailable, model error). A request rejected with 429 or with 409 `CHAT_BUSY` takes no slot.
- **D-04:** Over HTTP an exceeded limit answers 429 with a `Retry-After` header (whole seconds until a slot frees) and the uniform body `detail` + `code` with code `RATE_LIMITED`; nothing is stored in the chat.
- **D-05:** Over WebSocket the error frame carries the same code `RATE_LIMITED` and the same number of seconds (`retry_after`). The UI shows a Russian message of the form «Слишком много сообщений. Повторите через N с», and the text the user typed stays in the input field. No send-button lock and no countdown.
- **D-06:** Effective limits are visible only through `.env`, `.env.example`, `docs/USER_GUIDE.md` and a log line at Agent start with the values in force. No UI display and no REST route exposing them. This applies to all three limits of the phase.

### Login lockout (LIM-03)
- **D-07:** Failed logins are counted per pair **username + client IP**. A block affects only that machine for that username; the account owner logging in from another machine is not affected.
- **D-08:** Defaults: **5** consecutive wrong passwords block login for **5 minutes**; both values are set in `.env`. A successful login resets the counter. A threshold of `0` switches the lockout off.
- **D-09:** While blocked, login answers 429 with `Retry-After`; the login form shows a Russian message with the remaining time («Слишком много неудачных попыток. Повторите через N мин»). A correct password is refused during the block. The answer is the same for existing and non-existing usernames, so the block does not reveal which usernames exist.
- **D-10:** Counters and blocks live in Agent memory (like the current rate limiter in `agent/state.py`). An Agent restart clears every block — this is the operator's way to unblock. No new table and no migration.

### Concurrent generations and queue (LIM-05)
- **D-11:** The cap counts chat turns only — WebSocket and HTTP. One slot is held for the whole turn (tool rounds and RAG stages included). The scheduler keeps its own `SCHEDULER_MAX_CONCURRENT_RUNS`; background title generation, fact extraction and KB indexing are outside the cap.
- **D-12:** One cap is shared by all providers (LM Studio and cloud alike), set in `.env`, default **2**. The value `0` means no cap.
- **D-13:** The queue is unbounded in length and in wait time: a queued turn always runs and completes. `LLM_TIMEOUT` starts counting only after the turn leaves the queue. Overfilling is prevented by the per-chat rate limit.
- **D-14:** A turn waiting for a slot is visible in the browser: the WebSocket sends a frame announcing the queue and the chat shows «В очереди, ожидание свободного места…» instead of a silent wait. An HTTP client simply waits for its response.
- **D-15:** The time a turn spent in the queue is reported in `stats` (WebSocket `done` frame and HTTP chat response), so the verification script can show it.

### Verification script (VER-01..VER-03)
- **D-16:** One file, `scripts/verify_service.py`, using the Python standard library only: no third-party packages and no imports from the project. It is copied to the second machine and run there. The second machine is a **Mac with Python 3.14.7**; the script targets Python 3.11+ (project floor) and must be OS-independent (paths, console encoding). Invocation shape: `python3 verify_service.py --url http://<lan-ip>:8001 --key aia_...`.
- **D-17:** The script creates its own chats with a recognizable mark in the title (N chats for the parallel requests — parallel requests into one chat would hit 409 `CHAT_BUSY` — plus one each for the rate-limit and overflow demonstrations) and deletes them at the end. A `--keep` flag leaves the chats so they can be shown in the UI.
- **D-18:** Rate-limit demonstration: for the live run the operator sets a low limit in `.env` (for example 5). The script sends short messages into one chat one after another until it receives 429, with a ceiling on attempts, and prints the request number and the `Retry-After` value. If the ceiling is reached without a 429 it reports "limit not reached" instead of failing silently. The report states the limit value the run used.
- **D-19:** Output: progress and results in the console, plus two files — ready Markdown tables and raw JSON. The user carries these files from the Mac into `eval_out/day30/` in the repo.

### Report (VER-04, VER-05)
- **D-20:** `Day30_report.md` is written in Russian in the style of `Day22_report.md`..`Day25_report.md`: setup, configuration, commands, result tables copied from the script's files, honest conclusions and limitations. It is assembled from the files in `eval_out/day30/` after the user's live run; the script does not write the report itself.
- **D-21:** The live run from the Mac and the manual UI chat from another device are done by the user. Planning must include a human checkpoint for them; results that were not actually obtained are recorded as not run, never as passed.

- **D-22:** VER-05 status as of 2026-10-12: the user reported the manual UI chat from another device as approved (before Phase 20 planning, on the Phase 18-19 build). The report records it as confirmed by the user on that date; no device details or screenshots were supplied, so none are to be invented. The live script run from the Mac (VER-04) is still pending and keeps its human checkpoint.

### Claude's Discretion
- Names of the new `.env` settings, their validation (negative or non-numeric values), and the wording of all messages and UI strings.
- The limiter algorithm (the existing sliding window of timestamps is the baseline) and where the shared check lives so both channels call it; the order of the rate check relative to the chat-busy check, as long as D-03 holds.
- How the client IP is obtained for D-07 (the app has no reverse proxy, so the socket peer address is the baseline) and cleanup of stale lockout entries.
- How `/debug/routes` is closed (LIM-04) — the same auth dependency as other routes, so both cookie and API key work — and how `ui/supervisor.py::_has_websocket_route`, which polls that route without auth during Agent startup, gets its readiness signal afterwards.
- Name and payload of the "queued" WebSocket frame, the field name for queue wait time in `stats`, and where the slot is acquired relative to the per-chat lock.
- Script details: default N for parallel requests, model selection (flag or first loaded model), request timeouts, exit codes, how context overflow is provoked (baseline: per-chat settings with `no_compression` and a small `context_length`, then a long message → 413), file names inside the output directory.
- The checklist for the manual UI check from another device, and the section structure of the report.
- Split of pytest files for limits and the matching `docs/TESTING_GUIDE.md` sections (VER-06); tests for bind host, Origin, API keys and HTTP chat written in Phases 18-19 are audited for gaps here.

</decisions>

<specifics>
## Specific Ideas

- The second LAN machine is a Mac (macOS, Python 3.14.7); the server is the Windows machine. The script is copied there as a single file — no repository clone, no venv, no `pip install`.
- The run with a lowered message limit is a deliberate demo configuration; the report must say so and name the value.
- `--keep` exists so the chats created by the script can be shown in the browser on video.
- The queue should be demonstrable by the script: with cap 2 and N > 2 parallel requests, the per-request queue wait time from `stats` shows which requests waited.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` — LIM-01..LIM-05, VER-01..VER-06, Out of Scope table
- `.planning/ROADMAP.md` §Phase 20 — goal and the five success criteria
- `.planning/PROJECT.md` — milestone v4.0 goal and hard constraints (two-process model, vanilla JS, cookie session, no new dependencies)

### Prior phase decisions
- `.planning/phases/19-api-keys-and-http-chat-day-30/19-CONTEXT.md` — HTTP chat endpoint, `CHAT_BUSY` (D-16), status mapping and error body (D-17), Bearer auth (D-01..D-04), response `stats` (D-13)
- `.planning/phases/19-api-keys-and-http-chat-day-30/19-02-PLAN.md` and `19-04-PLAN.md` — extraction of the shared chat turn and the HTTP endpoint the limits attach to
- `.planning/phases/18-lan-exposure-day-30/18-CONTEXT.md` — Origin rule, `BIND_HOST` validation pattern, LAN mode
- `.planning/phases/18-lan-exposure-day-30/18-07-PLAN.md` — isolated LAN-mode checks and the pending manual second-device UAT

### Docs to update
- `docs/API_SPEC.md` — 429 responses (chat and login), `RATE_LIMITED` code, queue wait in `stats`, `/debug/routes` auth
- `docs/TESTING_GUIDE.md` — sections for bind host, Origin, API keys, HTTP chat and limits (VER-06)
- `docs/USER_GUIDE.md` — the limit settings and how to run the verification script from another machine
- `docs/ARCHITECTURE.md` — limits and the generation queue
- `.env.example` — the new limit settings with comments

### Report style
- `Day25_report.md` (and `Day22_report.md`..`Day24_report.md`) — structure and tone to follow for `Day30_report.md`

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md` — process split, supervisor, WebSocket chat flow, state
- `.planning/codebase/TESTING.md` — fixtures and test isolation

No external specs or ADRs — the rest is captured in the decisions above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/ws.py::_check_rate_limit` with `RATE_LIMIT_MAX = 10`, `RATE_LIMIT_WINDOW = 60.0` (lines 74-75, 163-173) and `agent/state.py::ws_rate_limiter` (`dict[int, list[float]]`, cleared in `cleanup_chat_caches`) — the existing per-chat sliding window; it becomes configurable and shared with HTTP.
- `agent/scheduler.py` — `asyncio.Semaphore(settings.SCHEDULER_MAX_CONCURRENT_RUNS)` (lines 90, 507): the existing pattern for a concurrency cap driven by a setting.
- `shared/config.py::Settings` — pydantic-settings class with commented fields and the `BIND_HOST` validator plus `_load_settings` exit path: model for new limit settings and their validation.
- `agent/dependencies.py::get_current_user` — single auth dependency (cookie, and Bearer key after Phase 19); attaching it to `/debug/routes` satisfies LIM-04.
- `agent/main.py:478` `login` — where failed attempts are detected (`login_failed` log) and where the lockout check goes.
- `scripts/rag_dialog.py`, `scripts/rag_eval.py` — conventions for run scripts (argparse, exit codes, output under `eval_out/dayNN/`, `run_meta.json`); unlike them the new script must not import the project or `httpx`.
- `scripts/e2e_lan_playwright.py` and the isolated-copy pattern (ports 18000/18001) — for automated checks that must not touch the user's running app.

### Established Patterns
- The rate check sits in the WebSocket receive loop (`agent/ws.py:1230`), outside the turn, and today sends `{"type": "error", "detail": "Rate limit exceeded"}` with no code.
- WebSocket error frames carry machine codes (`CONTEXT_OVERFLOW`, `PROVIDER_UNAVAILABLE`, `LLM_ERROR`, ...); HTTP chat reuses the same codes in `detail` + `code` bodies.
- `/debug/routes` (`agent/main.py:456`) is open today and is polled without credentials by `ui/supervisor.py::_has_websocket_route` (line 155) inside `_wait_for_agent_ready`; `/health` is polled by `_ping_health`.
- Per-chat `context_length` accepts values from 512 (`agent/schemas.py:95`), and `no_compression` turns an overflow into a hard `CONTEXT_OVERFLOW` — the cheap way to provoke overflow from the script.
- In-memory per-process state lives in `agent/state.py`; per-chat entries are removed in `cleanup_chat_caches`.

### Integration Points
- `shared/config.py`, `.env.example` — new settings.
- `agent/state.py`, `agent/ws.py`, the HTTP chat endpoint from plan 19-04 — shared rate limit, generation queue, "queued" frame, queue wait in `stats`.
- `agent/main.py` — login lockout, `/debug/routes` auth; `ui/supervisor.py` — readiness probe.
- `ui/static/app.js`, `ui/static/login.html` — rate-limit message with seconds, "в очереди" indicator, lockout message on the login form.
- `scripts/verify_service.py` (new), `eval_out/day30/`, `Day30_report.md` (new).
- `tests/` — new limit tests; existing `tests/test_concurrent_ws.py`, `tests/test_supervisor.py`, `tests/test_auth.py` are affected by the rate-limit default, the readiness probe and the lockout.

</code_context>

<deferred>
## Deferred Ideas

- Read-only display of the effective limits in the Settings «Сеть» section — considered, not chosen.
- A REST route exposing the limits so the script can read them — considered, not chosen.
- Persisting login lockouts in SQLite across restarts — considered, not chosen.
- Bounded queue (wait timeout or maximum length with 503 "server overloaded") — considered, not chosen.
- Send-button lock with a countdown while rate-limited — considered, not chosen.

</deferred>

---

*Phase: 20-limits-verification-script-and-report-day-30*
*Context gathered: 2026-10-12*
