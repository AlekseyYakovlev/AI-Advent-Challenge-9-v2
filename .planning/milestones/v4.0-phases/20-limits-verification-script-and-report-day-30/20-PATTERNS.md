# Phase 20: Limits, verification script and report (Day 30) - Pattern Map

**Mapped:** 2026-10-12
**Files analyzed:** 27 (6 new code/test/doc files, about 21 modified)
**Analogs found:** 25 / 27 (the two without a close analog: `scripts/verify_service.py` is only a partial match, and `agent/limits.py::GenerationGate` is a partial match)

Line numbers were read on branch `Day30` on 2026-10-12. Re-grep before editing; they drift.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/limits.py` (NEW: `check_message_rate`, `LoginThrottle`, `GenerationGate`, limits log helper) | service / utility | in-memory state, request-response | `agent/ws.py::_check_rate_limit` (163-173) + `agent/state.py` + `agent/scheduler.py` Semaphore (90, 507) | role-match (composite) |
| `shared/config.py` (4 settings + `_load_settings` exit path) | config | request-response (startup) | same file: `BIND_HOST` field/validator + `_load_settings` (80-108) | exact |
| `.env.example` | config | n/a | same file, `BIND_HOST` block and `TASK_MEMORY_*` commented block | exact |
| `agent/state.py` (singletons, cleanup) | store | in-memory | same file (`ws_rate_limiter`, `cleanup_chat_caches`) | exact |
| `agent/ws.py` (WS loop rate check, `run_limited_turn`, `_QueueStatsSink`, `_handle_chat_message`) | controller / handler | streaming, event-driven | same file (1205-1219, 1262-1292) | exact |
| `agent/http_chat.py` (rate check after 409, `_error_response(headers=)`, call `run_limited_turn`) | controller | request-response | same file (39-53, 115-165, 180-200) | exact |
| `agent/main.py` login lockout | controller | request-response | `login` (483-520) in the same file; `JSONResponse` with code from `http_chat._error_response` | exact |
| `agent/main.py` `/debug/routes` dependency | controller | request-response | other routes using `Depends(get_current_user)` (e.g. `post_chat_message`) | exact |
| `agent/main.py` lifespan `limits_in_force` log | config / lifecycle | event-driven | existing `agent_starting` log in `lifespan` | exact |
| `shared/origins.py` (`expose_headers`) | middleware | request-response | same file, `HostMatchCORSMiddleware` (68-74) | exact |
| `ui/supervisor.py` readiness probe | service | request-response (polling) | same file, `_has_websocket_route` (153-167) and `_ping_health` | exact |
| `ui/static/app.js` (`queued`, `RATE_LIMITED`) | component | event-driven (WS frames) | `handleWsMessage` `case 'error'` PROVIDER_UNAVAILABLE branch (1767-1773) | exact |
| `ui/static/login.html` (429 message) | component | request-response | same file submit handler (55-76) | exact |
| `scripts/verify_service.py` (NEW) | utility / CLI | request-response, batch (parallel) | `scripts/rag_eval.py` (argparse, exit codes, md+json output), `scripts/rag_dialog.py` | partial (must be stdlib-only) |
| `tests/test_rate_limit.py` (NEW) | test | request-response | `tests/test_ws_security.py::test_ws_rate_limiting` + `tests/test_http_chat.py` | role-match |
| `tests/test_login_lockout.py` (NEW) | test | request-response | `tests/test_auth.py` | role-match |
| `tests/test_generation_queue.py` (NEW) | test | async unit | `tests/test_concurrent_ws.py`, `tests/test_ws_turn_failures.py` | role-match |
| `tests/test_verify_service.py` (NEW) | test | request-response (stub server) | `tests/test_rag_eval.py` (importlib load, lines 18-21) | role-match |
| UI source-guard test (NEW) | test | file-read | `tests/test_network_ui.py`, `tests/test_api_keys_ui.py` | exact |
| `tests/conftest.py` | test config | n/a | same file `clean_test_db` + env pinning (13-24, 34-62) | exact |
| `tests/test_ws_security.py`, `test_scoping.py`, `test_network_info.py`, `test_supervisor_host.py`, `test_http_chat.py` (updates) | test | n/a | themselves | exact |
| `tests/test_docs_day30.py` (extend) | test | file-read | same file | exact |
| `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md` | docs | n/a | existing "Network (Day 30)" / "HTTP chat (Day 30)" / "API keys and HTTP chat (Day 30)" sections | exact |
| `Day30_report.md` (NEW) | docs | n/a | `Day25_report.md` | exact |

## Pattern Assignments

### `agent/limits.py` (NEW; service, in-memory state)

**Analog 1 (rate window):** `agent/ws.py::_check_rate_limit`, lines 163-173 (to be replaced/thinned)
```python
def _check_rate_limit(chat_id: int) -> bool:
    """Allow up to RATE_LIMIT_MAX messages per chat per minute."""
    now = time.monotonic()
    timestamps = ws_rate_limiter.setdefault(chat_id, [])
    ws_rate_limiter[chat_id] = [
        ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW
    ]
    if len(ws_rate_limiter[chat_id]) >= RATE_LIMIT_MAX:
        return False
    ws_rate_limiter[chat_id].append(now)
    return True
```
New version returns `int | None` (Retry-After seconds) and reads `settings.RATE_LIMIT_MESSAGES_PER_MINUTE` at call time (not a module constant), `0` disables. Keep the dict `ws_rate_limiter` in `agent/state.py` (`tests/test_cascade_delete.py` imports the name). Window math: `math.ceil(stamps[0] + 60 - now)`, min 1. See RESEARCH Pattern 1 for the full body.

**Analog 2 (concurrency cap):** `agent/scheduler.py` lines 90 and 507
```python
self._semaphore = asyncio.Semaphore(settings.SCHEDULER_MAX_CONCURRENT_RUNS)
```
Difference to respect: create the semaphore lazily and re-create it when the cap changes, plus `reset()`; tests make a new event loop per test (see conftest docstring), so a module-level semaphore would break. `0` = no cap (yield immediately).

**Analog 3 (singleton/state conventions):** `agent/state.py` — module docstring, plain module-level dicts, `cleanup_chat_caches` pops per-chat keys.

**Conventions to apply:** module docstring one line, `from shared.config import settings`, `from shared.logger import get_logger` / `logger = get_logger(__name__)`, type hints everywhere, `time.monotonic()` for windows. Log keys snake_case: `login_blocked` (username, ip, retry_after), `limits_in_force`.

---

### `shared/config.py` (config)

**Analog:** same file. Field style (commented, one line above each, lines 41-77):
```python
    # Maximum number of scheduled runs executing at the same time.
    SCHEDULER_MAX_CONCURRENT_RUNS: int = 2
```
Add `RATE_LIMIT_MESSAGES_PER_MINUTE: int = Field(default=30, ge=0)`, `LOGIN_MAX_FAILED_ATTEMPTS` (5, ge=0), `LOGIN_LOCKOUT_SECONDS` (300, ge=1), `MAX_CONCURRENT_GENERATIONS` (2, ge=0); import `Field` from pydantic (currently imports `ValidationError, field_validator` at line 8).

**Fail-fast exit path** (lines 98-108):
```python
def _load_settings() -> Settings:
    """Build settings; an invalid BIND_HOST prints a short message and exits with code 2."""
    try:
        return Settings()
    except ValidationError as exc:
        for error in exc.errors():
            if error["loc"] == ("BIND_HOST",):
                # Logging is not configured at import time, so stderr is used directly.
                print(str(error["ctx"]["error"]), file=sys.stderr)
                raise SystemExit(2) from None
        raise
```
Extend: for `error["loc"][0]` in the four new names print a short ASCII message (`ERROR: invalid <NAME> value ...`) and `raise SystemExit(2) from None`. Note `Field(ge=0)` errors have no `ctx["error"]`, so build the message from `error["loc"]`/`error["msg"]`, not by copying `error["ctx"]["error"]`.

**Test for this** copies `tests/test_bind_host.py` lines 16-25 (`_run_python` subprocess helper with `env={**os.environ, NAME: value}`, `cwd=REPO_ROOT`, expect returncode 2 and short stderr).

---

### `.env.example`

**Analog:** the `BIND_HOST` comment block and the commented `TASK_MEMORY_*` block (file tail):
```
# Timeout in seconds for the post-turn task-memory extraction call
# TASK_MEMORY_TIMEOUT=30.0
```
Add a "Limits" block with the four keys, a comment line each (what, unit, `0` = off), and a note that a restart of `python run.py` is required.

---

### `agent/state.py` (store)

**Analog:** same file. Singletons beside `ws_rate_limiter` (line 8); extend `cleanup_chat_caches` only if per-chat keys are added (the existing `ws_rate_limiter.pop(chat_id, None)` already covers the shared window). If `login_throttle` / `generation_gate` live in `limits.py`, `state.py` needs no change except possibly re-export for conftest.

---

### `agent/ws.py` (handler, streaming)

**Analog:** same file.

Current chat-lock wrapper (1205-1219):
```python
async def _handle_chat_message(websocket, chat_id, payload) -> None:
    """Process one inbound chat message under the per-chat lock."""
    if chat_id not in chat_locks:
        chat_locks[chat_id] = asyncio.Lock()
    async with chat_locks[chat_id]:
        await _run_chat_turn(websocket, chat_id, payload)
```
Replace the inner call with `await run_limited_turn(websocket, chat_id, payload)`. Slot is acquired AFTER the chat lock (lock -> slot order, RESEARCH Pattern 2). Look up `_run_chat_turn` through the module global at call time because `tests/test_ws_turn_failures.py::test_handle_chat_message_takes_lock_and_delegates` patches `agent_ws._run_chat_turn`.

Current rate check in the receive loop (1270-1277):
```python
            if not _check_rate_limit(chat_id):
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": "Rate limit exceeded",
                    },
                )
                continue
```
New: `retry = check_message_rate(chat_id)`; if not None send `{"type": "error", "code": "RATE_LIMITED", "detail": "Rate limit exceeded", "retry_after": retry}`. Keep the English text "Rate limit" (asserted by `test_ws_rate_limiting`). Remove `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` constants (74-75) and the now-unused imports.

Sink wrapper (new; `_run_chat_turn` uses only `send_json`, which `RecordingSink` in `http_chat.py` already relies on): wrap the sink and add `queue_wait_seconds` to the `stats` of the `done` frame (covers both `done` sites at ~1197-1207 and 826-836). Frame names for WS error codes follow the existing `CONTEXT_OVERFLOW` / `PROVIDER_UNAVAILABLE` convention.

---

### `agent/http_chat.py` (controller, request-response)

**Analog:** same file.

Error body builder (39-53) — add a `headers: dict[str, str] | None = None` keyword and pass `JSONResponse(status_code=..., content=content, headers=headers)`:
```python
def _error_response(status_code, detail, code, user_message_kept, **extra) -> JSONResponse:
    """Build the uniform error body; a JSONResponse keeps `code` that HTTPException drops."""
    content = {"detail": detail, "code": code, "user_message_kept": user_message_kept, **extra}
    return JSONResponse(status_code=status_code, content=content)
```
Note `headers` must be a named parameter, not swallowed by `**extra` (which goes into the body).

Endpoint order (post_chat_message, 180-200). Insert the rate check between the 409 check and `lock.acquire()` (D-03: 404 and 409 take no slot):
```python
    lock = chat_locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        return _error_response(409, MSG_CHAT_BUSY, CODE_CHAT_BUSY, False)
    # NEW: retry = check_message_rate(chat_id)
    #      if retry is not None: return _error_response(429, "Rate limit exceeded", "RATE_LIMITED",
    #          False, retry_after=retry, headers={"Retry-After": str(retry)})
    await lock.acquire()
```
Turn call (`_run_http_turn`, line 136): change `await ws._run_chat_turn(sink, chat_id, payload)` to `await ws.run_limited_turn(sink, chat_id, payload)`. The `RecordingSink` stores the `queued` frame harmlessly (success path reads only `done`, `tool_call`, `error`).

---

### `agent/main.py` login lockout (controller, request-response)

**Analog:** `login`, lines 483-497:
```python
@app.post("/api/v1/auth/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> UserResponse:
    """Verify credentials and issue a DB-backed HTTP-only session cookie."""
    result = await session.exec(select(User).where(User.username == body.username))
    user = result.first()
    if user is None or not verify_password(body.password, user.password_hash):
        logger.info("login_failed", username=body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверное имя пользователя или пароль")
```
Changes: add `request: Request`; key `(body.username, request.client.host if request.client else "unknown")`; check the block BEFORE the DB query and return `JSONResponse(429, {"detail": ..., "code": "LOGIN_BLOCKED", "retry_after": n}, headers={"Retry-After": str(n)})` (use `JSONResponse`, not `HTTPException`, which drops `code`; see the comment in `http_chat.py`). On failure call `login_throttle.record_failure(key)` before the existing 401 and add `ip=` to the `login_failed` log; on success `login_throttle.reset(key)`. Never log the password.

**`/debug/routes` (line 461):** `@app.get("/debug/routes")` -> `@app.get("/debug/routes", dependencies=[Depends(get_current_user)])` (same dependency `post_chat_message` takes as `current_user: User = Depends(get_current_user)`). `/health` stays open.

**Lifespan:** add `logger.info("limits_in_force", rate_limit_per_minute=..., login_max_failed=..., login_lockout_seconds=..., max_concurrent_generations=...)` after the existing `agent_starting` line (key=value log convention).

---

### `shared/origins.py` (middleware)

**Analog:** lines 68-74:
```python
        cors = CORSMiddleware(
            self.app,
            allow_origins=[origin] if allowed else [],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
```
Add `expose_headers=["Retry-After"]`. Without it the login page (on :8000, calling :8001 cross-origin) cannot read the header. Test in the style of `tests/test_origins.py`: allowed-Origin request to a 429 response carries `access-control-expose-headers` containing `Retry-After`.

---

### `ui/supervisor.py` (service, polling)

**Analog:** `_has_websocket_route` (153-167) and `_ping_health` (145-151):
```python
    async def _has_websocket_route(self) -> bool:
        url = f"http://127.0.0.1:{self.agent_port}/debug/routes"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code != 200:
                    return False
                routes = resp.json().get("routes", [])
                return any(route.get("path") == "/ws/chat/{chat_id}" for route in routes)
        except httpx.HTTPError:
            return False
```
New readiness: unauthenticated GET returns **401** (route table and auth layer are mounted; 404 = missing). Keep the literal `http://127.0.0.1:` inside the probe (`tests/test_supervisor_host.py::test_probes_stay_on_loopback` inspects its source at line 55-58 via `inspect.getsource(AgentSupervisor._has_websocket_route)`; if the method is renamed update that test). Same `except httpx.HTTPError: return False` shape. No token or loopback bypass on the route.

---

### `ui/static/app.js` (component, event-driven)

**Analog:** `handleWsMessage` `case 'error'` (1753-1778):
```javascript
            } else if (data.code === 'PROVIDER_UNAVAILABLE') {
                const input = $('message-input');
                if (input && !input.value && state.lastFailedMessage) {
                    input.value = state.lastFailedMessage;
                }
                showToast(data.detail || 'Провайдер недоступен', 'error');
                refreshModelSelector();
            } else {
                showToast(data.detail || 'Ошибка', 'error');
            }
```
Insert `else if (data.code === 'RATE_LIMITED')` before the generic `else`: remove the optimistic user bubble (mark it in `appendUserBubble`, e.g. `wrapper.dataset.optimistic = 'true'`), restore the input only if empty (copy the `!input.value && state.lastFailedMessage` guard), `showToast('Слишком много сообщений. Повторите через N с', 'warning')` with fallback text when `retry_after` is missing or not positive. `setStreaming(false)` and `removeLoadingBubble()` already run at the top of the case.

New `case 'queued'` in the same switch, using `appendLoadingBubble()` and a `.queue-note` element with `'В очереди, ожидание свободного места…'` inserted before `[data-stream-content]`; remove `.queue-note` in `appendTokenToStream` (~line 1391). Sources: RESEARCH "Frontend" example and `20-UI-SPEC.md`. No Stop button exists in the chat UI, so ignore UI-SPEC lines about stopping while queued. Use `textContent` (no HTML insertion; DOMPurify rule).

---

### `ui/static/login.html` (component, request-response)

**Analog:** same file submit handler (lines ~55-76):
```javascript
            const body = await resp.json().catch(() => ({}));
            errorEl.textContent = body.detail || 'Ошибка входа';
```
Insert before it: `if (resp.status === 429) { read resp.headers.get('Retry-After') else body.retry_after; minutes = Math.max(1, Math.ceil(s/60)); errorEl.textContent = 'Слишком много неудачных попыток. Повторите через N мин'; return; }` (fallback «Повторите позже»). Add `role="alert"` to `#login-error`. `textContent` only.

---

### `scripts/verify_service.py` (NEW; CLI, request-response + parallel batch)

**Analog (partial):** `scripts/rag_eval.py` — `build_parser()`/argparse, `main() -> int` + `sys.exit(main())` (lines 1932-1947), exit-code convention, output tables + JSON under `eval_out/dayNN/`. Differences: no project imports, no `httpx`, no `asyncio` need; Python 3.11+ stdlib only; synchronous, `ThreadPoolExecutor` for parallel requests.

Required skeleton from RESEARCH (stdlib call helper; copy as is):
```python
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # bypass system proxy

def call(method, url, key, body, timeout) -> Reply:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None: headers["Content-Type"] = "application/json"
    if key: headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ...
    except urllib.error.HTTPError as exc:   # the error IS the response
        raw, status, hdrs = exc.read(), exc.code, exc.headers
    except (urllib.error.URLError, OSError) as exc:
        return Reply(None, {}, None, elapsed, f"{type(exc).__name__}: {exc}")
    parsed = json.loads(raw) if raw else None   # 204 has no body
```
Other rules: reconfigure stdout/stderr to UTF-8 at start; `datetime.now(timezone.utc)`; key from `--key` or `AIA_API_KEY`, never written to files (prefix only); exit codes 0 / 1 (unmet expectation) / 2 (preflight); outputs `verify_results.md` + `verify_results.json`; `--keep`, `--n` (default 6), `--timeout` (default 300), `--rate-mode chat|fast`, `--out`. REST call table (health, auth/me, llm-providers/models, chats, settings, chats/{id}/messages, DELETE chat) is in RESEARCH "Verification script design".

**Test analog:** `tests/test_rag_eval.py` lines 18-21:
```python
REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_eval", REPO_ROOT / "scripts" / "rag_eval.py")
rag_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_eval)
```
Copy for `verify_service`; run `main()` against a `http.server.ThreadingHTTPServer` stub on `127.0.0.1:0`; add the `ast` stdlib-only import guard (`sys.stdlib_module_names`).

---

### `tests/test_rate_limit.py`, `tests/test_login_lockout.py`, `tests/test_generation_queue.py` (NEW)

**Analog for rate/WS:** `tests/test_ws_security.py::test_ws_rate_limiting` (47-73): `@respx.mock`, `TestClient(app)`, `login_test_client(client)`, chat create, `ws_rate_limiter.pop(chat_id, None)`, `client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN})`, loop `send_json` until `done`/`error`. Update to `monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 3)`, then assert `code == "RATE_LIMITED"`, `retry_after >= 1`.

**Analog for HTTP:** `tests/test_http_chat.py` — `_ok_response`, `_Upstream` mock class (lines 38-60), `ASGITransport(app=app)` client, `_create_user` from `tests.conftest`, `RESPONSE_KEYS`. For 429: assert `Retry-After` header, body `code == "RATE_LIMITED"`, nothing stored, a 409 and a 429 take no slot, a 413 takes a slot.

**Analog for lockout:** `tests/test_auth.py` login tests (wrong password path). Monkeypatch `settings.LOGIN_MAX_FAILED_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS`. Cover: correct password refused during block, same answer for unknown user, per-IP and per-user isolation, success resets, expiry unblocks, `0` off.

**Analog for queue:** `tests/test_concurrent_ws.py` (5 parallel WS messages, must stay green unchanged) and `tests/test_ws_turn_failures.py` (patching `agent_ws._run_chat_turn`, `RecordingSink`). Use async unit tests with a fake `_run_chat_turn` that `await asyncio.sleep`; cap monkeypatched via `settings.MAX_CONCURRENT_GENERATIONS`.

---

### `tests/conftest.py`

**Analog:** same file. Env pinning at import (lines 13-24):
```python
os.environ["BIND_HOST"] = "127.0.0.1"
```
Add `os.environ[...] =` pins for the four new settings BEFORE `from agent.main import app`. In `clean_test_db` (34-62), beside `agent_state.ws_rate_limiter.clear()` add `login_throttle.clear()` and `generation_gate.reset()`; update the docstring list.

---

### Updated existing tests

- `tests/test_scoping.py::test_health_and_debug_routes_stay_open`: `/health` 200; `/debug/routes` 401 without cookie, 200 with `authenticated_client` and with a Bearer key.
- `tests/test_network_info.py::test_route_registered`: use `authenticated_client`.
- `tests/test_supervisor_host.py::test_probes_stay_on_loopback` (55-58): keep the substring assertions valid.
- `tests/test_http_chat.py::test_success_body_has_exact_keys_and_stats`: compare `data["stats"]` to the stats endpoint without `queue_wait_seconds`, assert that key is `0.0` separately.
- `tests/test_supervisor.py::test_agent_restarts_within_5_seconds`: no edit; run explicitly (only end-to-end check of the new readiness signal).
- `tests/test_docs_day30.py`: extend (`_read_doc`, ordered-heading check, `parametrize` needles) with a "Limits (Day 30)" section and the new test file names.
- UI source guard: new file in the style of `tests/test_network_ui.py` / `tests/test_api_keys_ui.py` asserting the three UI strings; check `tests/test_static_js_syntax.py` coverage of `login.html`.

---

### Docs

- `docs/API_SPEC.md`: extend the "HTTP chat (Day 30)" section (429 `RATE_LIMITED`, `Retry-After`, `queue_wait_seconds`; remove the stale "no rate limit" sentence near line 874 and the `context_usage_percent` example vs real `usage_percent`), login 429 `LOGIN_BLOCKED`, WS `queued` frame and `RATE_LIMITED` frame, `/debug/routes` auth, environment table rows. Keep the heading order asserted by `test_api_spec_day30_sections_in_order`.
- `docs/TESTING_GUIDE.md`: new `## Limits (Day 30)` section with `### test_*.py` subsections, same style as "API keys and HTTP chat (Day 30)" (line 468+, `### test_http_chat.py` etc.).
- `docs/USER_GUIDE.md`: the four settings, restart note, how to run the script from another machine.
- `docs/ARCHITECTURE.md`: limits and generation queue section (lock -> slot order, in-memory, cleared by restart).

---

### `Day30_report.md` (NEW)

**Analog:** `Day25_report.md`. Structure: `## Постановка` (goal; bullet config incl. demo rate-limit value; "Команды"; "Файлы результатов" with relative links to `eval_out/day30/`), a one-paragraph "Главный результат, коротко", result tables copied verbatim from the script's Markdown file, then topic sections, `## Отказы и сбои`, `## Ограничения`. Russian. Items not run are written «не запускалось». VER-05 recorded as «подтверждено пользователем 2026-10-12», with no invented device details. Write only from files the user returns from the Mac (human checkpoint).

## Shared Patterns

### Uniform HTTP error body (`detail` + `code`)
**Source:** `agent/http_chat.py::_error_response` (39-53)
**Apply to:** HTTP 429 chat, login 429. Use `JSONResponse` (not `HTTPException`) so `code` survives; add `Retry-After` header and `retry_after` in the body.

### Call-time settings reads
**Source:** `agent/scheduler.py` (`settings.SCHEDULER_MAX_CONCURRENT_RUNS`) and `shared/config.py` singleton `settings`
**Apply to:** all limit functions. Read `settings.X` inside the function so `monkeypatch.setattr(settings, ...)` works in tests; never freeze into module constants.

### Per-test event-loop hygiene
**Source:** `tests/conftest.py::clean_test_db` docstring and body
**Apply to:** `GenerationGate` (lazy semaphore + `reset()`), `LoginThrottle.clear()`, any other new in-memory state.

### Logging
**Source:** CLAUDE.md conventions; `logger = get_logger(__name__)`, `logger.info("snake_case_event", key=value)`
**Apply to:** `agent/limits.py`, `agent/main.py` login/lifespan. Never log passwords, API keys.

### Exit-code-2 friendly startup failure
**Source:** `shared/config.py::_load_settings` (98-108) + `tests/test_bind_host.py::_run_python`
**Apply to:** the four new settings.

### Script and test isolation
**Source:** `tests/test_rag_eval.py` (importlib load), `scripts/e2e_lan_playwright.py` (isolated copy on ports 18000/18001). No real-network tests (TESTING_GUIDE "LAN exposure" convention).

## Conventions

Convention derivation skipped (`gsd-tools.cjs` was not found at the resolved plugin root; the `$ROOT` lookup returned the Git `usr/bin` directory). Conventions below are taken from CLAUDE.md and the files read; they were not computed by majority-vote.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case (`agent/http_chat.py`, `tests/test_rate_limit.py`) | not computed | not computed | named contract (per CLAUDE.md) |
| Identifier casing | snake_case functions/vars, PascalCase classes, UPPER_CASE constants/settings | not computed | not computed | named contract (per CLAUDE.md) |
| Export style | plain module-level names, direct imports (`from agent.main import app`), no barrels | not computed | not computed | named contract |
| Import style | stdlib, third-party, local; no path aliases | not computed | not computed | named contract |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` is CJS `module.exports`/`require`; `sdk/src/**` is ESM `export`/`import`) is the prototype intentional-contested split; it does not occur in this Python repo. Within this phase the one real split is `scripts/verify_service.py` (stdlib-only, no project imports, runs on a Mac) versus the rest of the repo (project imports, `structlog`, `httpx`): match the local style of the directory, and for this one file follow the stdlib-only rule from D-16. Note that `scripts/rag_eval.py` uses `asyncio`/`httpx`-style code that must NOT be copied into the new script.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `scripts/verify_service.py` | CLI | request-response, parallel | Only partial analog (`scripts/rag_eval.py` for argparse/exit codes/outputs). No existing stdlib-only urllib client in the repo; use RESEARCH call-helper skeleton. |
| `agent/limits.py::GenerationGate` / `LoginThrottle` | service | in-memory queue / throttle | Only fragments exist (Semaphore in `scheduler.py`, window in `ws.py`); no FIFO gate with "queued" notification or per-key block timer exists. Use RESEARCH skeletons. |

## Metadata

**Analog search scope:** `agent/`, `shared/`, `ui/`, `ui/static/`, `scripts/`, `tests/`, `docs/`, `Day25_report.md`, `.env.example`
**Files scanned:** about 30 read or grepped
**Pattern extraction date:** 2026-10-12
