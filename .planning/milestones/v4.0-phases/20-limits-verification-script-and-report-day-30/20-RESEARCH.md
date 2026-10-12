# Phase 20: Limits, verification script and report (Day 30) - Research

**Researched:** 2026-10-12
**Domain:** In-process abuse limits for a FastAPI/asyncio app (rate limit, login lockout, concurrency gate), endpoint auth, stdlib-only verification script, report
**Confidence:** HIGH (everything below was read from the current code on branch `Day30`; no external library is involved)

## Summary

Phase 20 adds no dependency and no new architecture. The chat turn already has one shared function, `agent/ws.py::_run_chat_turn(websocket, chat_id, payload)`, which both the WebSocket handler (`_handle_chat_message`, under `chat_locks[chat_id]`) and the HTTP endpoint (`agent/http_chat.py::_run_http_turn`, with a `RecordingSink`) call. It only ever uses `send_json` on its "socket". That gives three natural seams: (1) one shared rate-limit function called by the WS receive loop and by the HTTP endpoint, (2) one wrapper around `_run_chat_turn` that holds the generation slot, announces "queued" and injects queue wait into the `done` frame's `stats`, (3) a login throttle called at the top of `login`.

Several CONTEXT.md assumptions need adjusting after reading the code (details in the sections below): the browser talks to the Agent **directly** on :8001 (no UI proxy), so the socket peer is the real client IP; `Retry-After` is **not** readable by the login page today because `HostMatchCORSMiddleware` has no `expose_headers`; making `/debug/routes` authenticated breaks `ui/supervisor.py::_has_websocket_route`, `tests/test_scoping.py`, `tests/test_network_info.py` and `tests/test_supervisor_host.py`; adding a key to `stats` breaks `tests/test_http_chat.py::test_success_body_has_exact_keys_and_stats`; the UI-SPEC "Stop" button does not exist; and D-18's "short messages one after another" may never hit a low limit with a slow real model because the window is 60 s.

**Primary recommendation:** Put all new in-memory limit logic in one new module `agent/limits.py` (rate check, `LoginThrottle`, `GenerationGate`), keep `ws_rate_limiter` in `agent/state.py` (tests reference it), add the wrapper `ws.run_limited_turn` that both channels call instead of `_run_chat_turn`, acquire the generation slot AFTER the chat lock, and write `scripts/verify_service.py` as a single stdlib file that bypasses system proxies and offers a fast-failing (overflow-based) rate-limit mode as well as the literal short-message mode.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Message rate limit (LIM-01, LIM-02)**
- **D-01:** The limit is messages per minute per chat, set in `.env`, default **30** (today it is the constant `RATE_LIMIT_MAX = 10` in `agent/ws.py`). The value `0` switches the limit off.
- **D-02:** One counter per chat is shared by WebSocket and HTTP chat: a message sent over either channel takes a slot in the same window.
- **D-03:** Every accepted attempt takes a slot, including a turn that later fails (context overflow, model unavailable, model error). A request rejected with 429 or with 409 `CHAT_BUSY` takes no slot.
- **D-04:** Over HTTP an exceeded limit answers 429 with a `Retry-After` header (whole seconds until a slot frees) and the uniform body `detail` + `code` with code `RATE_LIMITED`; nothing is stored in the chat.
- **D-05:** Over WebSocket the error frame carries the same code `RATE_LIMITED` and the same number of seconds (`retry_after`). The UI shows a Russian message of the form «Слишком много сообщений. Повторите через N с», and the text the user typed stays in the input field. No send-button lock and no countdown.
- **D-06:** Effective limits are visible only through `.env`, `.env.example`, `docs/USER_GUIDE.md` and a log line at Agent start with the values in force. No UI display and no REST route exposing them. This applies to all three limits of the phase.

**Login lockout (LIM-03)**
- **D-07:** Failed logins are counted per pair **username + client IP**. A block affects only that machine for that username; the account owner logging in from another machine is not affected.
- **D-08:** Defaults: **5** consecutive wrong passwords block login for **5 minutes**; both values are set in `.env`. A successful login resets the counter. A threshold of `0` switches the lockout off.
- **D-09:** While blocked, login answers 429 with `Retry-After`; the login form shows a Russian message with the remaining time («Слишком много неудачных попыток. Повторите через N мин»). A correct password is refused during the block. The answer is the same for existing and non-existing usernames, so the block does not reveal which usernames exist.
- **D-10:** Counters and blocks live in Agent memory (like the current rate limiter in `agent/state.py`). An Agent restart clears every block — this is the operator's way to unblock. No new table and no migration.

**Concurrent generations and queue (LIM-05)**
- **D-11:** The cap counts chat turns only — WebSocket and HTTP. One slot is held for the whole turn (tool rounds and RAG stages included). The scheduler keeps its own `SCHEDULER_MAX_CONCURRENT_RUNS`; background title generation, fact extraction and KB indexing are outside the cap.
- **D-12:** One cap is shared by all providers (LM Studio and cloud alike), set in `.env`, default **2**. The value `0` means no cap.
- **D-13:** The queue is unbounded in length and in wait time: a queued turn always runs and completes. `LLM_TIMEOUT` starts counting only after the turn leaves the queue. Overfilling is prevented by the per-chat rate limit.
- **D-14:** A turn waiting for a slot is visible in the browser: the WebSocket sends a frame announcing the queue and the chat shows «В очереди, ожидание свободного места…» instead of a silent wait. An HTTP client simply waits for its response.
- **D-15:** The time a turn spent in the queue is reported in `stats` (WebSocket `done` frame and HTTP chat response), so the verification script can show it.

**Verification script (VER-01..VER-03)**
- **D-16:** One file, `scripts/verify_service.py`, using the Python standard library only: no third-party packages and no imports from the project. It is copied to the second machine and run there. The second machine is a **Mac with Python 3.14.7**; the script targets Python 3.11+ (project floor) and must be OS-independent (paths, console encoding). Invocation shape: `python3 verify_service.py --url http://<lan-ip>:8001 --key aia_...`.
- **D-17:** The script creates its own chats with a recognizable mark in the title (N chats for the parallel requests — parallel requests into one chat would hit 409 `CHAT_BUSY` — plus one each for the rate-limit and overflow demonstrations) and deletes them at the end. A `--keep` flag leaves the chats so they can be shown in the UI.
- **D-18:** Rate-limit demonstration: for the live run the operator sets a low limit in `.env` (for example 5). The script sends short messages into one chat one after another until it receives 429, with a ceiling on attempts, and prints the request number and the `Retry-After` value. If the ceiling is reached without a 429 it reports "limit not reached" instead of failing silently. The report states the limit value the run used.
- **D-19:** Output: progress and results in the console, plus two files — ready Markdown tables and raw JSON. The user carries these files from the Mac into `eval_out/day30/` in the repo.

**Report (VER-04, VER-05)**
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

### Deferred Ideas (OUT OF SCOPE)
- Read-only display of the effective limits in the Settings «Сеть» section — considered, not chosen.
- A REST route exposing the limits so the script can read them — considered, not chosen.
- Persisting login lockouts in SQLite across restarts — considered, not chosen.
- Bounded queue (wait timeout or maximum length with 503 "server overloaded") — considered, not chosen.
- Send-button lock with a countdown while rate-limited — considered, not chosen.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| LIM-01 | Per-chat messages-per-minute limit configurable in `.env`, shared by WS and HTTP | `agent/limits.py::check_message_rate`, called from `ws_chat` loop and `post_chat_message`; `RATE_LIMIT_MESSAGES_PER_MINUTE` setting (section "Rate limit") |
| LIM-02 | HTTP chat answers 429 + `Retry-After` | `_error_response` needs a `headers` kwarg; body gets `retry_after`; order relative to 409 (section "Rate limit") |
| LIM-03 | Login blocked after N consecutive wrong passwords | `LoginThrottle` keyed `(username, ip)`; `request.client.host` is the real peer; 429 via `JSONResponse` (section "Login lockout") |
| LIM-04 | `/debug/routes` only for an authenticated user | `Depends(get_current_user)`; supervisor probe, 3 tests to change (section "/debug/routes") |
| LIM-05 | Concurrent generations capped, excess wait in a queue | `GenerationGate` + `run_limited_turn` wrapper, slot after chat lock (section "Generation gate") |
| VER-01 | Script checks reachability and auth | `GET /health` (open), `GET /api/v1/auth/me` (Bearer) (section "Verification script") |
| VER-02 | N parallel real-model requests, successes/errors/timings | `ThreadPoolExecutor` + `urllib`, one chat per request, queue wait from `stats` |
| VER-03 | Demonstrates rate limit and context overflow | rate: 429 + `Retry-After`; overflow: per-chat `no_compression` + `context_length=512` + long message → 413 |
| VER-04 | `Day30_report.md` with live run from second LAN machine | human checkpoint; report structure after `Day25_report.md` |
| VER-05 | Manual UI chat from another device | Already approved by user 2026-10-12 (D-22); record, do not re-run |
| VER-06 | pytest covers bind host, Origin, API keys, HTTP chat, limits; TESTING_GUIDE sections | Existing coverage audited; new limit tests + TESTING_GUIDE "Limits" section (section "Validation Architecture") |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Per-chat message rate limit | API / Backend (Agent) | Browser (message only) | Must apply to WS and HTTP identically; only the Agent sees both channels |
| Login lockout | API / Backend (Agent) | Browser (message only) | Credential check lives in `agent/main.py::login`; browser calls the Agent directly on :8001 |
| `/debug/routes` auth | API / Backend (Agent) | UI process supervisor (readiness probe) | Supervisor is the only unauthenticated internal caller |
| Generation cap and queue | API / Backend (Agent) | Browser (indicator) | Single process, single event loop; in-memory `asyncio.Semaphore` is enough |
| Queue wait reporting | API / Backend (Agent) | — | Injected into `stats` of the `done` frame / HTTP body |
| Verification script | External client (second machine) | — | Runs outside the repo, stdlib only |
| Report | Docs | — | Assembled by hand from script output |

## Standard Stack

No new packages. Everything is built from what `requirements.txt` already ships plus the Python standard library. [VERIFIED: codebase — `requirements.txt` read, hard constraint in CLAUDE.md]

### Core (already installed, versions in this environment)
| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| FastAPI | 0.141.1 | Dependencies (`Depends(get_current_user)`), `JSONResponse` with headers | [VERIFIED: `python -c`] |
| Starlette | 1.6.0 | `Request.client`, `CORSMiddleware(expose_headers=...)` | [VERIFIED: `python -c`] |
| uvicorn | 0.52.4 | Agent server; `--proxy-headers` on by default, trusts `X-Forwarded-For` only from `FORWARDED_ALLOW_IPS` (default `127.0.0.1`) | [VERIFIED: uvicorn.config source] |
| pydantic-settings | existing | New limit settings with `Field(ge=0)` | existing pattern in `shared/config.py` |
| pytest / pytest-asyncio / respx | 9.1.1 / existing / 0.23.1 | Tests | [VERIFIED: pip list] |
| Python stdlib (`asyncio`, `time`, `math`, `urllib.request`, `concurrent.futures`, `argparse`, `json`, `http.server` for script tests) | 3.13.15 local, 3.14.7 on the Mac | Limits and script | stdlib only |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Own sliding window | `slowapi` / `limits` | Forbidden: no new dependencies; the window is 12 lines |
| `asyncio.Semaphore` gate | `asyncio.Queue` + worker | A semaphore is already the codebase pattern (`SchedulerService`), is FIFO and cancellation-safe |
| `httpx` in script | `urllib.request` | Locked by D-16 (nothing to install on the Mac) |

**Installation:** none.

## Package Legitimacy Audit

No external packages are installed or recommended by this phase. slopcheck was not needed.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
 Browser (UI :8000 page)                        Second machine (Mac) script
   |  WS /ws/chat/{id}  (cookie)                    |  HTTP + Bearer key
   |  REST + login      (cookie)                    |  POST /api/v1/chats/{id}/messages
   v   (direct to Agent :8001, no proxy)            v
 +-------------------------------- Agent (uvicorn :8001) --------------------------------+
 |  login ---> LoginThrottle.check(username, ip) --blocked--> 429 + Retry-After           |
 |                |ok                                                                      |
 |                v verify_password -> fail: throttle.record_failure / ok: throttle.reset  |
 |                                                                                         |
 |  WS receive loop                         HTTP post_chat_message                         |
 |     |                                        | _get_owned_chat (404)                    |
 |     |                                        | lock.locked()? --yes--> 409 CHAT_BUSY    |
 |     v                                        v                                          |
 |  check_message_rate(chat_id) ----------- check_message_rate(chat_id)                    |
 |     | over limit: WS error frame RATE_LIMITED        | over limit: 429 + Retry-After    |
 |     v ok (slot taken)                                v ok (slot taken)                  |
 |  async with chat_locks[chat_id]  <-- WS waits / HTTP already holds the lock             |
 |     v                                                                                   |
 |  run_limited_turn(sink, chat_id, payload)                                               |
 |     |  gate.saturated? -> send {"type":"queued"}                                        |
 |     |  async with gate.slot():  (measure wait)                                          |
 |     |     _run_chat_turn(proxy_sink, ...)  -> LLM (LLM_TIMEOUT counts from here)        |
 |     |     done frame: stats += queue_wait_seconds                                       |
 |     v                                                                                   |
 |  WS: frames to socket          HTTP: RecordingSink -> HttpChatResponse(stats)           |
 +-----------------------------------------------------------------------------------------+
 Supervisor (UI process) --> GET /health (open), GET /debug/routes (now 401 without auth)
```

### Current shape of the chat turn after Phase 19 (verified)

| Item | Current location |
|------|------------------|
| Rate constants | `agent/ws.py:74-75` (`RATE_LIMIT_MAX = 10`, `RATE_LIMIT_WINDOW = 60.0`); `_check_rate_limit` `ws.py:163-173`; dict `agent/state.py:8` |
| WS receive loop, rate check | `agent/ws.py:1260-1290`; check at line **1270** (CONTEXT said 1230), runs BEFORE `MessagePayload.model_validate`; error frame `{"type":"error","detail":"Rate limit exceeded"}` without code |
| Chat lock, WS | `_handle_chat_message` `ws.py:1210-1219`: `chat_locks[chat_id]` then `_run_chat_turn` |
| Shared turn | `_run_chat_turn(websocket, chat_id, payload)` `ws.py:842`; caller must hold the lock; uses only `websocket.send_json` (so `RecordingSink` works) |
| `done` frames (2 places) | normal turn `ws.py:1197-1207`; RAG-gated turn `_complete_gated_turn` `ws.py:826-836`; both carry `"stats": stats` from `compute_chat_stats` |
| HTTP endpoint | `agent/http_chat.py::post_chat_message` (line 165): `_get_owned_chat` -> `lock.locked()` -> 409 `CHAT_BUSY` (line 185-186) -> `lock.acquire()` -> background task `_run_http_turn` calls `ws._run_chat_turn(sink, ...)` (line 136), releases the lock in `finally` |
| Error bodies | `_error_response(status, detail, code, user_message_kept, **extra)` returns `JSONResponse` with `detail`, `code`, `user_message_kept` (no `headers` param yet) |
| `stats` content | `compute_chat_stats` returns `current_context_size, context_window_size, usage_percent, message_count, total_request_tokens, total_response_tokens, chat_id` (the API_SPEC example with `context_usage_percent` is stale) |
| `LLM_TIMEOUT` | `agent/llm_client.py:51,255`: `httpx.AsyncClient(timeout=settings.LLM_TIMEOUT)` created per request inside the turn. A float means connect/read/write/pool timeouts for that call, so it cannot include queue time as long as the slot is acquired BEFORE `_run_chat_turn`. D-13 is satisfied by construction. [VERIFIED: codebase] |
| Stop button | **None exists in the chat UI** (no `stop`/`abort` handler in `app.js`; `active_streams` is written but never cancelled). UI-SPEC statements about "Stopping while queued" do not apply |

### Recommended structure
```
agent/
├── limits.py          # NEW: check_message_rate, LoginThrottle, GenerationGate, log_limits_in_force
├── state.py           # keep ws_rate_limiter; add login_throttle / generation_gate singletons (or in limits.py) + reset hook
├── ws.py              # _check_rate_limit removed/thin; run_limited_turn wrapper; WS loop uses limits
├── http_chat.py       # rate check after 409 check; calls ws.run_limited_turn; _error_response(headers=...)
├── main.py            # login throttle, /debug/routes dependency, limits log line in lifespan
shared/config.py       # 4 new settings + friendly exit-2 validation
shared/origins.py      # expose_headers=["Retry-After"]
ui/supervisor.py       # new readiness probe
ui/static/app.js, login.html  # three UI-SPEC touchpoints
scripts/verify_service.py     # NEW, stdlib only
tests/test_rate_limit.py, test_login_lockout.py, test_generation_queue.py, test_verify_service.py  # NEW
```

### Pattern 1: Shared rate limit (D-01..D-05)
**What:** one function returning `None` when allowed (slot recorded) or the integer seconds until the oldest timestamp leaves the window.
```python
# agent/limits.py
RATE_WINDOW_SECONDS = 60.0

def check_message_rate(chat_id: int) -> int | None:
    """Record a message slot for the chat; return Retry-After seconds when the limit is hit."""
    limit = settings.RATE_LIMIT_MESSAGES_PER_MINUTE        # read at call time: tests monkeypatch it
    if limit <= 0:
        return None
    now = time.monotonic()
    stamps = [t for t in ws_rate_limiter.get(chat_id, []) if now - t < RATE_WINDOW_SECONDS]
    if len(stamps) >= limit:
        ws_rate_limiter[chat_id] = stamps
        return max(1, math.ceil(stamps[0] + RATE_WINDOW_SECONDS - now))
    stamps.append(now)
    ws_rate_limiter[chat_id] = stamps
    return None
```
- The check is synchronous (no await), so it cannot interleave between check and append.
- **Order on HTTP (D-03):** `_get_owned_chat` (404 must not consume a slot or leak) -> `lock.locked()` -> 409 (no slot) -> `check_message_rate` -> 429 (no slot) -> `await lock.acquire()`. Awaiting `Lock.acquire()` on an unlocked lock does not yield, so nothing can take the lock between the `locked()` test and the acquire (existing behaviour, keep it). The rate check must NOT precede the 409 check, otherwise a 409 request would consume a slot.
- **Order on WS:** keep the check at its current place (before payload validation). Consequence: an invalid payload takes a slot (cheap flood protection); acceptable, mention in docs. The WS has no busy rejection (waits for the lock), so a slot is taken at receive time.
- WS error frame: `{"type":"error","code":"RATE_LIMITED","detail":"Rate limit exceeded","retry_after":N}`. **Keep the English `detail` text containing "Rate limit"**: `tests/test_ws_security.py::test_ws_rate_limiting` asserts `"Rate limit" in payload["detail"]`.
- HTTP 429: `_error_response(429, "Rate limit exceeded", "RATE_LIMITED", False, retry_after=N, headers={"Retry-After": str(N)})` — add a `headers: dict[str,str] | None` parameter to `_error_response` (`JSONResponse(headers=...)`). Keep `user_message_kept: false` so the body stays uniform with the other chat errors.
- `ws_rate_limiter` is already cleared by `cleanup_chat_caches` and by `tests/conftest.py`; `tests/test_cascade_delete.py` imports the name, so do not rename it.

### Pattern 2: Generation gate and wrapper (D-11..D-15)
**Slot placement (D-11 discretion): acquire the slot AFTER the chat lock, inside the lock.** Reason: acquiring before the chat lock would let a turn that is only waiting for its own chat's lock occupy a slot and starve other chats; lock -> slot is a single global order and slot holders never wait for any lock, so no deadlock is possible. HTTP already holds the chat lock when `_run_http_turn` starts, and `_handle_chat_message` holds it around `_run_chat_turn`, so the wrapper is called in exactly those two places.

```python
# agent/ws.py
class _QueueStatsSink:
    """Pass frames through and add queue wait to the stats of the done frame."""
    def __init__(self, inner: Any, wait_seconds: float) -> None: ...
    async def send_json(self, frame: dict[str, Any]) -> None:
        if frame.get("type") == "done" and isinstance(frame.get("stats"), dict):
            frame = {**frame, "stats": {**frame["stats"], "queue_wait_seconds": self._wait}}
        await self._inner.send_json(frame)

async def run_limited_turn(websocket: Any, chat_id: int, payload: MessagePayload) -> None:
    """Run one turn inside a generation slot; the caller holds the per-chat lock."""
    async with generation_gate.slot(on_wait=lambda: websocket.send_json(
            {"type": "queued", "chat_id": chat_id})) as waited:
        await _run_chat_turn(_QueueStatsSink(websocket, waited), chat_id, payload)
```
- A sink wrapper (not a ContextVar, not editing the two `done` sites) is the smallest change and covers both the normal and the RAG-gated turn. `_run_chat_turn` calls only `send_json`, proven by `RecordingSink` working today.
- `GenerationGate.slot()`: if `MAX_CONCURRENT_GENERATIONS <= 0` yield `0.0` immediately; else, if `semaphore.locked()` (value 0) call `on_wait` once (sending the `queued` frame), then `await semaphore.acquire()` with `time.monotonic()` around it; `release()` in `finally`. `asyncio.Semaphore` is FIFO and removes a cancelled waiter correctly. Report `round(wait, 3)`; `0.0` when no wait.
- **Event-loop binding pitfall:** `asyncio.Semaphore`/`Lock` bind to a loop on first contended use. The test suite creates a new loop per test (`with TestClient(app)`; `asyncio.run` in several tests) and `conftest.py` already clears `chat_locks` for this reason. Create the semaphore lazily (and re-create when the configured cap changes) and add a `generation_gate.reset()` + `login_throttle.clear()` to the autouse `clean_test_db` fixture.
- `http_chat._run_http_turn` must call `ws.run_limited_turn(sink, ...)` instead of `ws._run_chat_turn(...)`. `RecordingSink` stores the `queued` frame harmlessly: the success path reads only `done`, `tool_call` and `error` frames.
- `tests/test_ws_turn_failures.py::test_handle_chat_message_takes_lock_and_delegates` patches `agent_ws._run_chat_turn` by module attribute and passes a `RecordingSink`; the wrapper must look the function up through the module global at call time (normal code does) so the patch still works.
- **Disconnect while queued (WS):** the receive loop is blocked inside `_handle_chat_message`, so a closed socket is noticed only when a frame is sent. The queued turn will still run when a slot frees (persist user message, call the model, then fail on `send_json`). This is the same as today's lock wait; unbounded queue is locked by D-13. Document as a limitation; do not add cancellation machinery.
- **Queue wait vs script timeout:** the script's per-request timeout must exceed `ceil(N / cap) * typical reply time`; default 300 s, flag `--timeout`.

### Pattern 3: Login throttle (D-07..D-10)
- Client IP: `request.client.host` (add `request: Request` to `login`). The browser calls the Agent **directly**: `AGENT_BASE = http://${hostname}:8001` in `ui/static/login.html:41-42` and `app.js:3-5`; the UI process on :8000 serves static files only (`ui/main.py`) and proxies nothing. So the peer address is the real client. uvicorn's proxy-header handling is on by default but trusts `X-Forwarded-For` only from 127.0.0.1, so a LAN client cannot spoof its IP. [VERIFIED: codebase, uvicorn source] Treat `request.client is None` as the key `"unknown"`.
- Test clients: `httpx.ASGITransport` reports `("127.0.0.1", 123)`; Starlette `TestClient` reports `"testclient"`. Both work as keys.
- Key `(body.username, ip)` with the exact username string (the DB lookup is an exact match; `LoginRequest.username` is length-bounded by `USERNAME_MAX_LENGTH`).
- Flow in `login` (before the DB query): `retry = throttle.blocked_seconds(key)`; if set -> `JSONResponse(429, {"detail": "...", "code": "LOGIN_BLOCKED", "retry_after": retry}, headers={"Retry-After": str(retry)})` **without touching the DB or verifying the password** (a correct password is refused during the block, and existing/non-existing usernames are indistinguishable). On wrong password or unknown user: `throttle.record_failure(key)` then the existing 401. On success: `throttle.reset(key)`. Use `JSONResponse`, not `HTTPException`: `HTTPException` drops `code` (the comment in `http_chat.py` documents this). Returning a `Response` from a handler with `response_model` is fine in FastAPI.
- Decide and document: the failure that reaches the threshold still answers 401; the next attempt gets 429. When the block expires the counter is reset.
- Stale-entry cleanup (bounded memory against username spraying): forget failures older than `LOGIN_LOCKOUT_SECONDS` since the last failure, purge expired entries opportunistically on each call (iterate only when `len(dict) > 1000`, or every call — the dict is tiny), and hard-cap entries (e.g. 10 000) dropping the oldest.
- Log `login_failed` with `ip=` and `login_blocked` with `username`, `ip`, `retry_after` (the existing line already logs the username; never log the password).
- Known limit to state in docs: an attacker can try 5 passwords per username per IP window; that is what D-07 chose.

### Pattern 4: `/debug/routes` auth and the supervisor probe (LIM-04)
- Change: `@app.get("/debug/routes", dependencies=[Depends(get_current_user)])` — works for cookie and Bearer. `/health` untouched (the supervisor's `_ping_health` and `HealthResponse` stay open).
- `ui/supervisor.py::_has_websocket_route` (line 153-167) requires HTTP 200 and the `/ws/chat/{chat_id}` path in the JSON. After the change it receives 401, returns False forever and `_wait_for_agent_ready` runs 3 cycles x 20 attempts x 0.5 s (about 30 s plus two forced restarts) on every start; the function does not raise after the cycles, so `start()` still returns, which hides the problem (`tests/test_supervisor.py` would stay green but slow, and `agent_ready` would never be logged).
- **Recommendation:** readiness = `/health` 200 AND unauthenticated `GET /debug/routes` returns **401**. The WebSocket route is registered at import time of `agent/main.py` (`@app.websocket("/ws/chat/{chat_id}")`, line 1382, followed by `logger.info("websocket_routes_registered")`), and uvicorn serves `/health` only after the module is imported, so a 401 from `/debug/routes` (404 would mean a missing route) proves the route table and the auth layer are mounted. Do not add a token or a loopback bypass to `/debug/routes`: that would reintroduce an unauthenticated path to the route list. Rename the method (e.g. `_routes_are_guarded`) or keep the name; either way it must keep the literal `http://127.0.0.1:` because `tests/test_supervisor_host.py::test_probes_stay_on_loopback` inspects its source — update that test if the method is renamed.
- Tests that depend on the open route and must change:
  - `tests/test_scoping.py::test_health_and_debug_routes_stay_open` (expects 200 for `/debug/routes`) -> `/health` 200, `/debug/routes` 401 without a cookie, 200 with `authenticated_client` and with a Bearer key.
  - `tests/test_network_info.py::test_route_registered` uses the unauthenticated `client` -> use `authenticated_client`.
  - `tests/test_supervisor_host.py::test_probes_stay_on_loopback` (see above).
  - `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` spawns a real Agent through `supervisor.start()`; it needs no edit if the probe is fixed, but run it explicitly: it is the only end-to-end check of the new readiness signal.
- Docs mentioning the open route: `docs/API_SPEC.md` (grep `debug/routes`), `.planning/codebase/ARCHITECTURE.md` mentions only history in old phase folders (ignore).

### Pattern 5: CORS exposure of `Retry-After`
`shared/origins.py::HostMatchCORSMiddleware` builds `CORSMiddleware(..., allow_origins=[origin] if allowed else [], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])` with no `expose_headers`. `Retry-After` is not a CORS-safelisted response header, so `resp.headers.get('Retry-After')` in `login.html` (page on :8000 calling :8001, a cross-origin fetch) returns `null` and the UI would always show the fallback text. Fix: add `expose_headers=["Retry-After"]`; also put `retry_after` in the JSON body (UI-SPEC allows header first, body second). Add a test in `tests/test_origins.py` style: a request with an allowed `Origin` to a 429 route carries `access-control-expose-headers` containing `Retry-After`.

### Pattern 6: Settings and validation
Add to `shared/config.py::Settings` (names are discretion; recommended):
```python
# Messages per minute per chat, shared by WebSocket and HTTP chat; 0 = off.
RATE_LIMIT_MESSAGES_PER_MINUTE: int = Field(default=30, ge=0)
# Consecutive wrong passwords per (username, client IP) before login is blocked; 0 = off.
LOGIN_MAX_FAILED_ATTEMPTS: int = Field(default=5, ge=0)
# How long that block lasts, in seconds (tests use small values).
LOGIN_LOCKOUT_SECONDS: int = Field(default=300, ge=1)
# Chat turns allowed to generate at once, all providers together; 0 = no cap.
MAX_CONCURRENT_GENERATIONS: int = Field(default=2, ge=0)
```
- Today `_load_settings()` handles only `BIND_HOST` errors; any other `ValidationError` re-raises a traceback. Extend it with the same pattern (short ASCII message to stderr, `SystemExit(2)`) for these four fields — a typo such as `RATE_LIMIT_MESSAGES_PER_MINUTE=abc` or `-1` should not produce a pydantic traceback. `tests/test_bind_host.py` has the subprocess pattern (`import shared.config` with bad env -> exit 2) to copy.
- Log one line in `agent/main.py::lifespan` (after `agent_starting`): `logger.info("limits_in_force", rate_limit_per_minute=..., login_max_failed=..., login_lockout_seconds=..., max_concurrent_generations=...)` (D-06).
- `.env.example`: add the four keys with comments (the file currently has Process ports / BIND_HOST / DB / LLM / MCP / RAG blocks).
- Note the supervisor passes `os.environ` plus `DB_PATH` to the Agent subprocess and the Agent reads `.env` from the working directory, so a restart of `python run.py` is required after editing `.env` (document in USER_GUIDE).

### Anti-Patterns to Avoid
- **Rate check before the 409 check on HTTP:** consumes a slot on a rejected request (violates D-03).
- **Slot before chat lock:** idle slot holders; also would make the HTTP busy test ambiguous.
- **Module-level `asyncio.Semaphore()` bound once:** breaks across the per-test event loops.
- **Reading settings into module constants at import:** tests (and operators using the same pattern as `BIND_HOST`) need call-time reads so `monkeypatch.setattr(settings, ...)` works.
- **`HTTPException(429, headers=...)` for bodies that need `code`:** drops `code` for the uniform body; use `JSONResponse`.
- **Revealing username existence:** check the block before the DB lookup and count failures for unknown users too.
- **Putting the key into output files or logs** in the script.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Concurrency cap + FIFO queue | A custom queue/worker | `asyncio.Semaphore` (as in `scheduler.py:90`) | FIFO, cancellation-safe, already the project pattern |
| Auth for `/debug/routes` | A new check | `Depends(get_current_user)` | Handles cookie and Bearer, sliding expiry, API key revocation |
| Client IP | Parsing `X-Forwarded-For` | `request.client.host` | uvicorn already resolves trusted proxy headers; no proxy exists |
| Script HTTP client | Raw sockets | `urllib.request` + `concurrent.futures.ThreadPoolExecutor` | stdlib, enough for a few JSON calls |
| Script tables | A formatting library | f-strings and a 15-line `md_table(rows, headers)` | stdlib only |
| Token estimation in the script | tiktoken | Send a clearly oversized message (>= 6000 chars of varied text vs `context_length=512`) | No import allowed; the overflow is checked in tokens server-side, 512 is far below any 6000-char text |

**Key insight:** every protection here is a few dozen lines of in-memory state; the risk is not the algorithm but the interaction with per-test event loops, CORS header visibility, the supervisor probe and tests that assert exact shapes.

## Runtime State Inventory

Not a rename/refactor/migration phase. The only new runtime state is in-memory (rate windows, login failures, semaphore) and is cleared by an Agent restart (D-10). No stored data, service config, OS-registered state, secrets or build artifacts are affected. New `.env` keys are optional with defaults, so existing `.env` files keep working.

## Common Pitfalls

### Pitfall 1: `stats` equality test breaks
**What goes wrong:** `tests/test_http_chat.py::test_success_body_has_exact_keys_and_stats` asserts `data["stats"] == stats.json()` (GET stats endpoint). The `done`/HTTP stats will carry `queue_wait_seconds`, the GET endpoint will not.
**How to avoid:** change the assertion to compare without that key and assert `queue_wait_seconds == 0.0` (or `>= 0`) separately; keep `GET /api/v1/chats/{id}/stats` unchanged (D-06: no new route). Check WS tests that assert `done` stats shape (`tests/test_stats.py` uses `compute_chat_stats` directly, unaffected).

### Pitfall 2: Low limit never reached with a slow model (D-18)
**What goes wrong:** sending short messages sequentially into one chat only hits "limit 5" if 5 full turns complete inside the 60 s window. A local 9B model that answers in 15 s makes the window roll and the script reports "limit not reached" honestly but uselessly.
**How to avoid:** (a) `--rate-mode chat` (default, literal D-18): short prompts ("Ответь одним словом: да"), set the per-chat `max_tokens` to its minimum (256) and a one-word system prompt through per-chat settings; print the elapsed time of the sequence and warn when it exceeds 60 s. (b) `--rate-mode fast`: use the overflow chat — a message sent to a chat with `no_compression` + `context_length=512` answers 413 instantly without calling the model, yet takes a rate slot (D-03: failed attempts count), so the 429 appears on request limit+1 within milliseconds. Report which mode was used. Window semantics also mean `Retry-After` is seconds until the oldest slot expires, not a fixed 60.
**Warning signs:** report row "limit not reached" together with sequence duration > 60 s.

### Pitfall 3: Retry-After invisible to the login page
See Pattern 5. Without `expose_headers` the lockout message always falls back to «Повторите позже».

### Pitfall 4: System proxy captures LAN requests on the Mac
`urllib.request.urlopen` honours `http_proxy` env and macOS system proxy settings (`getproxies()`), so a request to `192.168.x.x` may go through a proxy and fail or look slow. Build an explicit opener: `urllib.request.build_opener(urllib.request.ProxyHandler({}))` and use it for all calls. [ASSUMED — standard urllib behaviour; verify during the live run]

### Pitfall 5: `HTTPError` is the response
For 4xx/5xx `urlopen` raises `urllib.error.HTTPError`; status = `e.code`, headers = `e.headers` (`e.headers.get("Retry-After")`), body = `e.read()`. Connection failures raise `URLError` (reason inside); a read timeout during `resp.read()` can surface as bare `TimeoutError`/`socket.timeout`, `ConnectionResetError`, or `http.client.RemoteDisconnected` (an `OSError`/`ConnectionError`), not wrapped in `URLError`. Catch `(urllib.error.URLError, OSError)` around the whole call + read. A 204 (DELETE chat) has an empty body: do not `json.loads('')`.

### Pitfall 6: Console encoding (Windows vs macOS)
Windows consoles use cp866/cp1251/cp1252 when piped; macOS is UTF-8. Do `for s in (sys.stdout, sys.stderr): if hasattr(s, "reconfigure"): s.reconfigure(encoding="utf-8", errors="replace")` at start, write output files with `encoding="utf-8"`, send JSON bodies with the default `json.dumps` (`ensure_ascii=True`) so request bytes are ASCII, and keep script console strings ASCII-friendly (Russian only inside titles/files). Use `datetime.now(timezone.utc)` (project rule; `utcnow` is deprecated in 3.12+). No syntax newer than 3.11. [VERIFIED: project conventions]

### Pitfall 7: Queue interaction with parallel script requests
N requests go to N different chats (D-17), so no 409. With cap 2 and N=6 the third..sixth requests wait; the script must use a timeout above the total queue time and read `stats.queue_wait_seconds` per request. Server-side `LLM_TIMEOUT` does not apply during the wait. Rate limit is per chat, so N chats do not trip it.

### Pitfall 8: Optimistic user bubble in the UI on RATE_LIMITED
`sendMessage` (`app.js:1799-1830`) appends the user bubble via `appendUserBubble` and clears the input immediately; `appendUserBubble` returns nothing and sets no marker. The server stored nothing (D-04), so the RATE_LIMITED branch must remove that bubble (mark it, e.g. `wrapper.dataset.optimistic = 'true'` in `appendUserBubble`, and remove the marked element in the new branch) and restore `state.lastFailedMessage` into `#message-input` only if the input is empty (same pattern as the `PROVIDER_UNAVAILABLE` branch, `app.js:1767-1773`; `state.lastFailedMessage` is already set at send time, line 1816). `setStreaming(false)` and `removeLoadingBubble()` already run at the top of `case 'error'`.

### Pitfall 9: Test-suite defaults
- `RATE_LIMIT_MESSAGES_PER_MINUTE` default 30 (was 10): `tests/test_ws_security.py::test_ws_rate_limiting` sends 10 messages then expects the 11th to fail. Change it to `monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 3)` (and use `_check` through the new function), assert `code == "RATE_LIMITED"` and `retry_after >= 1`.
- `tests/test_concurrent_ws.py` sends 5 parallel WS messages to one chat: stays under 30 and under cap 2 it simply queues; it must still pass unchanged (it is the regression guard for "excess turns wait and complete").
- `tests/test_auth.py` makes at most one or two wrong-password attempts per test; with the per-(username, ip) lockout at 5 and the autouse reset of `login_throttle` there is no cross-test leakage. Without the reset in `conftest.py`, repeated wrong-password tests under the same username from `127.0.0.1` could block later tests.
- `tests/conftest.py` pins `BIND_HOST`; also pin the four new settings from `.env` so a developer `.env` with `RATE_LIMIT_MESSAGES_PER_MINUTE=5` does not change results (same reason `BIND_HOST` is pinned: `os.environ[...] = ...` before importing `agent.main`).

## Code Examples

### Login throttle skeleton
```python
# agent/limits.py
@dataclass
class _Entry:
    failures: int = 0
    last_failure: float = 0.0
    blocked_until: float = 0.0

class LoginThrottle:
    """Per (username, ip) consecutive-failure counter with a timed block; memory only."""
    MAX_ENTRIES = 10_000
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _Entry] = {}
    def blocked_seconds(self, key: tuple[str, str]) -> int | None:
        threshold = settings.LOGIN_MAX_FAILED_ATTEMPTS
        entry = self._entries.get(key)
        if threshold <= 0 or entry is None:
            return None
        remaining = entry.blocked_until - time.monotonic()
        if remaining > 0:
            return max(1, math.ceil(remaining))
        return None
    def record_failure(self, key: tuple[str, str]) -> None: ...   # reset stale count, ++, set blocked_until at threshold, purge
    def reset(self, key: tuple[str, str]) -> None: self._entries.pop(key, None)
    def clear(self) -> None: self._entries.clear()
```

### Stdlib JSON call returning status, headers, body (script)
```python
# Source: Python docs, urllib.request / urllib.error
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(method: str, url: str, key: str | None, body: dict | None, timeout: float) -> Reply:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw, status, hdrs = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as exc:               # the error IS the response
        raw, status, hdrs = exc.read(), exc.code, exc.headers
    except (urllib.error.URLError, OSError) as exc:     # refused, DNS, timeout, reset
        return Reply(None, {}, None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started
    parsed = json.loads(raw) if raw else None           # 204 has no body
    return Reply(status, dict(hdrs.items()), parsed, elapsed, None)
```

### Frontend: queued frame and RATE_LIMITED (UI-SPEC)
```javascript
case 'queued': {
    appendLoadingBubble();                     // frame may precede the first token
    const bubble = document.querySelector('[data-streaming="true"] .bg-slate-800');
    if (bubble && !bubble.querySelector('.queue-note')) {
        const note = document.createElement('div');
        note.className = 'queue-note mt-1 text-sm text-slate-400';
        note.textContent = 'В очереди, ожидание свободного места…';
        bubble.insertBefore(note, bubble.querySelector('[data-stream-content]'));
    }
    break;
}
// in appendTokenToStream(): document.querySelector('.queue-note')?.remove();
// in case 'error': else if (data.code === 'RATE_LIMITED') { ...remove optimistic bubble, restore input, showToast(..., 'warning') }
```
The loading bubble markup (`app.js:1378-1386`) has the dots container then `[data-stream-content]`; the caption goes between them ("under the dots").

## Verification script design (VER-01..VER-03)

**REST calls (all verified against the code; all work with a Bearer key, none is cookie-only; send `Content-Type: application/json` on POST/PUT; no `Origin` header is sent so the Origin check passes, Phase 18 D-05):**

| Step | Call | Notes |
|------|------|-------|
| Reachability | `GET {url}/health` (no auth) | 200; measure time. Fail -> exit 2 |
| Auth good | `GET {url}/api/v1/auth/me` + Bearer | 200 `{id, username}` |
| Auth bad | same with a deliberately wrong key | 401 `Invalid or revoked API key` (shows keys are checked) |
| Models | `GET {url}/api/v1/llm-providers/models` | list of `{provider_id, name, kind, models:[{id, loaded}], error}`; pick `--provider-id/--model` flags or the first model with `loaded` true (LM Studio) / first model of the first group without `error` |
| Create chat | `POST {url}/api/v1/chats` `{"title": "verify-service <UTC stamp> #k"}` | 201 -> `{id, title, current_leaf_message_id, created_at}` (title max length `TITLE_MAX_LENGTH`) |
| Per-chat settings | `PUT {url}/api/v1/settings` `{"chat_id": id, "strategy": "no_compression", "context_length": 512, "max_tokens": 256}` | `context_length >= 512`, `max_tokens >= 256` are the schema minimums |
| Chat turn | `POST {url}/api/v1/chats/{id}/messages` `{"content", "model", "provider_id"}` | 200 body has `content`, `stats` (incl. `queue_wait_seconds`), 409/413/429/5xx uniform bodies `detail` + `code` |
| Delete chat | `DELETE {url}/api/v1/chats/{id}` | 204 empty body |

**Parallel requests (VER-02):** `ThreadPoolExecutor(max_workers=N)`, one chat per request, each result records `status`, `code`, wall time, `stats.queue_wait_seconds`, reply length; summary table with successes/errors, min/median/max time, and the per-request queue wait (shows which requests waited with cap 2 and N > 2). `statistics.median` is stdlib. A `--n` default of 6 demonstrates a queue with the default cap 2.

**Context overflow (VER-03):** dedicated chat with the settings above, then a single message of >= 6000 characters of varied text (e.g. a repeated paragraph of distinct words) -> expect 413 `CONTEXT_OVERFLOW`, `suggested_strategy: "sliding"`, `user_message_kept: false`. The overflow is detected before any model call (`build_llm_context`: `total_tokens > context_length` under `no_compression`, `agent/context_engine.py:453-465`) but after `resolve_client`, so a valid `model/provider_id` is still required. Report the observed status/code and the detail text.

**Output (D-19):** `--out DIR` default `./verify_out_<UTC stamp>/` containing `verify_results.md` (ready tables: environment, reachability/auth, parallel run, rate limit, overflow, cleanup) and `verify_results.json` (raw: args without the key, key prefix only, Python version, platform, UTC start/end, every request's raw record). The user copies both into `eval_out/day30/`. Console shows progress per step. Exit codes following `scripts/rag_eval.py`: 0 all demonstrations behaved as expected, 1 a run error or an unmet expectation (e.g. rate limit not reached, overflow not 413, any parallel error), 2 preflight failure (unreachable, bad key, no usable model). `--keep` skips deletion and prints the chat ids/titles. Key can come from `--key` or an environment variable (`AIA_API_KEY`) to keep it out of shell history.

**Testing the script without a network:** the file has no package (`scripts/` has no `__init__.py`); tests load it with `importlib.util.spec_from_file_location` as `tests/test_rag_eval.py` does, and run `main()` against a stub `http.server.ThreadingHTTPServer` on `127.0.0.1:0` in a thread with canned responses (200/401/429 with `Retry-After`/413). Add a static guard: the script's imports are only stdlib (`ast` walk against `sys.stdlib_module_names`, available since Python 3.10) and it contains no `import shared|agent|httpx`.

## Report (VER-04, VER-05)

`Day25_report.md` structure to copy: `## Постановка` (goal, config bullets, commands, result files with links), a "Главный результат, коротко" paragraph, result tables copied verbatim from the generated Markdown, per-topic sections, `## Отказы и сбои`, `## Ограничения`. For `Day30_report.md` (Russian): Постановка (server Windows, client Mac with Python 3.14.7, `BIND_HOST=0.0.0.0`, firewall rule, limits in force: **the demo rate limit value**, cap, lockout; the exact script command without the key), Результаты (reachability/auth table, parallel run with queue waits, rate limit, overflow — pasted from `eval_out/day30/verify_results.md`), Ручная проверка UI (VER-05: «подтверждено пользователем 2026-10-12», no invented device details; sources: `18-07-PLAN.md` manual UAT checklist and `docs/TESTING_GUIDE.md` "Manual check from a second device"), Ограничения (in-memory limits cleared by restart, per-pair lockout, unbounded queue, demo limit was lowered on purpose, plain HTTP). Anything not run is written "не запускалось". Planning must hold a `checkpoint:human-verify` for the Mac run and the report is written only from files the user returns.

## Frontend touch points

| Item | File / place | Change |
|------|--------------|--------|
| Rate-limit toast | `ui/static/app.js` `handleWsMessage` `case 'error'` (line 1753): new `else if (data.code === 'RATE_LIMITED')` before the generic `else` (line 1774) | warning toast with N from `data.retry_after` (fallback text if absent/not positive); remove optimistic user bubble; restore input if empty |
| Queue caption | `case 'queued'` in the same switch; `appendTokenToStream` (line 1391) removes `.queue-note`; `appendLoadingBubble` (line 1371) unchanged | see example |
| Login lockout | `ui/static/login.html` submit handler (before `body.detail || 'Ошибка входа'`, line ~75): `resp.status === 429` -> header `Retry-After` else `body.retry_after`, minutes = `Math.max(1, Math.ceil(s / 60))`; add `role="alert"` to `#login-error` | needs Pattern 5 |
| Source guard | `tests/test_static_js_syntax.py` runs a syntax check on `app.js`/inline script (verify it covers `login.html`); add a small source-guard test file in the style of `tests/test_network_ui.py` for the three strings | no Node: the guard is Python-side |

Note on UI-SPEC "Stop": there is no Stop control, so the "cancel while queued" requirement is moot.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Constant `RATE_LIMIT_MAX = 10`, WS-only | `.env` setting, shared WS+HTTP, `RATE_LIMITED` code + `retry_after` | this phase | tests asserting the old text/limit change |
| `/debug/routes` open for the supervisor | Auth required; readiness by 401 probe | this phase | 3 tests + supervisor |

**Deprecated/outdated:** `docs/API_SPEC.md` HTTP chat section states "There is no rate limit in this version" (line 874) and shows `context_usage_percent` in the `stats` example while the code returns `usage_percent`; update both while adding the 429 row, `RATE_LIMITED` and `queue_wait_seconds`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `urllib` on macOS may route LAN requests through a system proxy, so the script should use an empty `ProxyHandler` | Pitfall 4 | Low: the empty ProxyHandler is harmless either way |
| A2 | A 401 from `/debug/routes` is an adequate readiness signal because all routes (incl. the WS route) are registered at module import | Pattern 4 | Low-medium: if routes were ever registered lazily the probe would pass early; the user may prefer a different signal |
| A3 | Local models may take more than 12 s per reply so a 5-message window can roll over | Pitfall 2 | Medium: decides whether the `fast` rate mode is necessary; the mode is cheap to add |
| A4 | The failure that reaches the login threshold answers 401 (the next gets 429) | Pattern 3 | Low: UX choice, user can flip it |

## Open Questions (RESOLVED)

1. **Readiness signal for the supervisor (LIM-04).**
   - What we know: the unauthenticated probe cannot get 200 anymore; 401 proves the app is mounted.
   - What's unclear: whether the user accepts "401 = ready" or prefers a different mechanism.
   - Recommendation: use `/health` 200 + `/debug/routes` 401; update `test_supervisor_host`.
   - RESOLVED: the readiness signal is `/health` 200 plus an unauthenticated `/debug/routes` answered 401 (Claude's discretion per CONTEXT.md). Plan 20-03 Tasks 2-3 implement it (`ui/supervisor.py::_agent_routes_ready`) with a contract test against the real app, a no-restart test and a real-process launch-count test.
2. **Rate demo mode (D-18).** Literal short-message mode may not reach the limit with a slow model.
   - Recommendation: implement both `--rate-mode chat` (default, literal D-18) and `--rate-mode fast` (overflow chat); the report states which was used. Planner should confirm with the user at the human checkpoint.
   - RESOLVED: both modes are implemented in plan 20-05 with `chat` (literal D-18) as the default. The plan 20-08 run sheet recommends `--rate-mode both`; the user can still run `chat` only, so the choice stays with the user at run time and no separate confirmation step is needed. The report names the mode or modes the run used.
3. **Does a rate-limited chat count toward the HTTP `CHAT_BUSY` 409 first?** Yes by D-03 ordering (409 checked first). Document it in API_SPEC.
   - RESOLVED: the order is 404 -> 409 -> 429 in plan 20-02 Task 1 (`post_chat_message`), proven by plan 20-02 Task 2 tests 4-6, and documented in `docs/API_SPEC.md` by plan 20-07.
4. **Queue wait key name** `queue_wait_seconds` (float, seconds, 3 decimals, `0.0` when not queued) is a recommendation; planner may rename but must keep it in both channels and update the `test_http_chat` stats comparison.
   - RESOLVED: the name `queue_wait_seconds` is kept. Plan 20-02 adds it to the WebSocket `done` frame and to the HTTP chat response and updates the `test_http_chat` comparison; plan 20-05 reads it; plan 20-07 documents it.
5. **Mac environment** (Python 3.14.7, network path, firewall rule from Phase 18) is user-reported, not verifiable from here.
   - RESOLVED: accepted as stated by the user (CONTEXT.md D-16, D-22). Plan 20-08 Task 2 is a blocking human checkpoint for the run from the Mac; it prints `platform`, `python_version` and `url` from the returned `verify_results.json`, and the report states those values as they are.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python (Windows dev box) | tests, Agent | ✓ | 3.13.15 | — |
| Python on the Mac | `verify_service.py` | not verifiable here (user states 3.14.7) | 3.14.7 | script targets 3.11+ |
| pytest, respx, httpx | tests | ✓ | 9.1.1, 0.23.1, 0.28.1 | — |
| Real model (LM Studio or cloud provider) | live run VER-04 | operator-side | — | none: live run needs a real model; unit tests use respx |
| Second LAN machine + firewall rule for 8001 | VER-04 | operator-side (Phase 18 manual UAT, VER-05 approved) | — | — |

**Missing dependencies with no fallback:** none for planning; the live run blocks on the user.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.1.1 + pytest-asyncio (`asyncio_mode = auto`), respx |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/test_rate_limit.py tests/test_login_lockout.py tests/test_generation_queue.py -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LIM-01 | One window shared by WS and HTTP; `0` disables; env-driven; counter cleared on chat delete | unit + integration | `pytest tests/test_rate_limit.py -q` | ❌ Wave 0 |
| LIM-02 | HTTP 429 + `Retry-After` + body `RATE_LIMITED`, nothing stored; 409 and 429 take no slot; failed turn (413) takes a slot; WS frame `code`/`retry_after` | integration | same | ❌ Wave 0 (`test_ws_security.py::test_ws_rate_limiting` to update) |
| LIM-03 | Threshold, block, `Retry-After`, correct password refused during block, unknown user same answer, per-IP/per-user isolation, success resets, expiry unblocks, `0` off, CORS exposes `Retry-After` | integration | `pytest tests/test_login_lockout.py -q` | ❌ Wave 0 |
| LIM-04 | `/debug/routes` 401 without auth, 200 with cookie and Bearer; `/health` open; supervisor probe accepts 401 | integration | `pytest tests/test_scoping.py tests/test_supervisor_host.py tests/test_network_info.py -q` | partly (update) |
| LIM-05 | Cap 1 with 3 fake turns: max concurrency 1, all complete, `queued` frame sent for waiters only, `queue_wait_seconds` > 0 for waiters and 0.0 for the first, cancel-while-waiting frees nothing, cap `0` = unlimited, lock -> slot order (no deadlock) | async unit (fake `_run_chat_turn` with `asyncio.sleep`) + WS/HTTP smoke | `pytest tests/test_generation_queue.py -q` | ❌ Wave 0 |
| LIM-05 | 5 parallel WS messages still complete | integration | `pytest tests/test_concurrent_ws.py -q` | ✅ |
| VER-01..03 | Script against a stub server: preflight ok/fail, 401, parallel table, 429 detection, "limit not reached", 413, `--keep`, exit codes, key not in output files; stdlib-only import guard | unit | `pytest tests/test_verify_service.py -q` | ❌ Wave 0 |
| VER-04 | Report exists, records not-run honestly | manual (human checkpoint) | — | manual-only: needs the Mac and a real model |
| VER-05 | Already approved 2026-10-12 | manual | — | done |
| VER-06 | Bind host, Origin, API keys, HTTP chat covered; limits covered; TESTING_GUIDE sections | doc guard | `pytest tests/test_docs_day30.py -q` (extend) | extend |

### VER-06 audit of existing coverage (code is the source of truth)
- **Bind host:** `tests/test_bind_host.py` (normalisation, refusal, exit code 2, singleton), `tests/test_supervisor_host.py`, `tests/test_run_banner.py`, `tests/test_network*.py`: covered, and `docs/TESTING_GUIDE.md` "LAN exposure (Day 30)" already documents them.
- **Origin:** `tests/test_origins.py`, `tests/test_ws_origin_validation.py`, `tests/test_websocket_cors.py`, `tests/test_mcp_origin.py`, `tests/test_cors.py`: covered, documented in the same section.
- **API keys:** `tests/test_api_keys.py`, `tests/test_bearer_auth.py`, `tests/test_api_keys_ui.py`: covered, documented in "API keys and HTTP chat (Day 30)".
- **HTTP chat:** `tests/test_http_chat.py`, `tests/test_http_chat_rag.py`, `tests/test_ws_turn_failures.py`: covered (200, 404, 409, 413, 502/503, events, disconnect). Gaps relevant to this phase: no test that a Bearer key reaches `/debug/routes`; no HTTP 429; no queue.
- **Gaps to close for VER-06:** a new TESTING_GUIDE section "Limits (Day 30)" (rate limit, login lockout, generation queue, `/debug/routes`, verification script, CORS exposure), mention of the updated `test_scoping`/`test_ws_security`/`test_http_chat` assertions, and extending `tests/test_docs_day30.py` so the guide lists every new test file (it already checks this for the Day 30 files). Add `docs/API_SPEC.md` rows: 429 `RATE_LIMITED` (chat), login 429 `LOGIN_BLOCKED`, `queue_wait_seconds`, WS `queued` frame and `RATE_LIMITED` error frame, `/debug/routes` auth; `docs/ARCHITECTURE.md` limits/queue section; `docs/USER_GUIDE.md` settings and how to run the script.
- Do not add real-network tests: existing convention is header-simulated LAN (see TESTING_GUIDE "LAN exposure").

### Sampling Rate
- **Per task commit:** the quick run command for the touched area
- **Per wave merge:** `pytest tests/ -v` (it contains `test_supervisor.py`, which spawns a real Agent: keep it)
- **Phase gate:** full suite green before `/bm:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_rate_limit.py`, `tests/test_login_lockout.py`, `tests/test_generation_queue.py`, `tests/test_verify_service.py`, UI source guard file
- [ ] `tests/conftest.py`: reset `login_throttle`/`generation_gate`; pin the four settings from the environment
- [ ] Updates to `test_ws_security.py`, `test_scoping.py`, `test_network_info.py`, `test_supervisor_host.py`, `test_http_chat.py` (stats equality)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | per (username, IP) lockout; generic 401; block checked before credential verification |
| V3 Session Management | no change | unchanged (HTTP-only cookie) |
| V4 Access Control | yes | `/debug/routes` behind `get_current_user`; `/health` intentionally open |
| V5 Input Validation | yes | `Field(ge=0)` on the new settings, friendly exit 2 on bad values |
| V6 Cryptography | no | nothing new; key stays hash-only server-side |
| V7 Logging | yes | log IP and username on lockout, never passwords or API keys; script never writes the key to files |
| V11 Business logic / DoS | yes | per-chat rate limit, generation cap, bounded login-throttle memory |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Password guessing | Spoofing | lockout per (username, IP); 429 before verification |
| Username enumeration via lockout | Information disclosure | identical 429 for known/unknown names; failures counted for both |
| Spoofed client IP via `X-Forwarded-For` | Spoofing | uvicorn trusts the header only from 127.0.0.1 (`FORWARDED_ALLOW_IPS` default); do not parse it manually |
| Memory growth by username/IP spraying | DoS | TTL purge + hard entry cap |
| Message flood / model exhaustion | DoS | per-chat rate limit + generation cap (note: the rate limit is per chat, so many chats multiply the budget; the cap bounds actual concurrency) |
| Route disclosure | Information disclosure | `/debug/routes` authenticated |
| API key in shell history / output | Information disclosure | accept `AIA_API_KEY` env var, print key prefix only |

## Sources

### Primary (HIGH confidence — current repository, read in this session)
- `agent/ws.py` (turn, lock, rate limiter, WS loop), `agent/http_chat.py`, `agent/state.py`, `agent/main.py` (login, `/debug/routes`, lifespan, settings/chat routes), `agent/dependencies.py`, `agent/headless.py`, `agent/schemas.py`, `agent/context_engine.py`, `agent/llm_client.py`, `agent/scheduler.py`
- `shared/config.py`, `shared/origins.py`, `ui/supervisor.py`, `ui/main.py`, `ui/static/app.js`, `ui/static/login.html`
- `tests/conftest.py`, `test_supervisor*.py`, `test_scoping.py`, `test_network_info.py`, `test_ws_security.py`, `test_concurrent_ws.py`, `test_http_chat.py`, `test_ws_turn_failures.py`, `test_docs_day30.py`, `docs/TESTING_GUIDE.md`, `docs/API_SPEC.md` (HTTP chat), `Day25_report.md`, `scripts/rag_eval.py`
- Installed uvicorn 0.52.4 source (`uvicorn.config`: `proxy_headers=True`, `FORWARDED_ALLOW_IPS` default `127.0.0.1`)

### Secondary / Tertiary
- None. No web research was needed; Python stdlib `urllib` behaviours marked [ASSUMED] above.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; versions read from the environment
- Architecture: HIGH — every seam and line reference verified against the current files
- Pitfalls: HIGH for code-derived ones (CORS, supervisor, test breakage); MEDIUM for urllib/proxy and model-speed ones (assumed, to confirm in the live run)

**Research date:** 2026-10-12
**Valid until:** until the files named above change (line numbers drift quickly); re-grep before editing
