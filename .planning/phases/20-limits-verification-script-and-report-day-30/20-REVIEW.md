---
phase: 20-limits-verification-script-and-report-day-30
reviewed: 2026-10-12T19:40:00Z
depth: standard
files_reviewed: 28
files_reviewed_list:
  - .env.example
  - agent/http_chat.py
  - agent/limits.py
  - agent/main.py
  - agent/ws.py
  - scripts/e2e_api_keys_playwright.py
  - scripts/e2e_limits_playwright.py
  - scripts/verify_service.py
  - shared/config.py
  - shared/origins.py
  - tests/conftest.py
  - tests/test_docs_day30.py
  - tests/test_generation_queue.py
  - tests/test_http_chat.py
  - tests/test_limits_config.py
  - tests/test_limits_core.py
  - tests/test_limits_ui.py
  - tests/test_login_lockout.py
  - tests/test_network_info.py
  - tests/test_rate_limit.py
  - tests/test_scoping.py
  - tests/test_supervisor.py
  - tests/test_supervisor_host.py
  - tests/test_verify_service.py
  - tests/test_ws_security.py
  - ui/static/app.js
  - ui/static/login.html
  - ui/supervisor.py
findings:
  critical: 1
  warning: 5
  info: 5
  convention: 3
  total: 11
status: issues_found
---

# Phase 20: Code Review Report

**Reviewed:** 2026-10-12T19:40:00Z
**Depth:** standard
**Files Reviewed:** 28
**Status:** issues_found

## Narrative Findings (AI reviewer)

## Summary

The review was scoped to the phase diff (`c0afe01^..HEAD`). `agent/limits.py`,
`agent/http_chat.py`, `scripts/verify_service.py`, `ui/supervisor.py`, `shared/config.py`
and the phase hunks of `agent/main.py`, `agent/ws.py`, `ui/static/app.js`,
`ui/static/login.html`, `shared/origins.py`, `tests/conftest.py` and `.env.example` were
read in full with their surrounding code paths. Coverage of the rest is thinner and should
not be read as a clean bill: the two Playwright scripts (about 1,170 lines) and the test
files (about 3,000 lines) were pattern-scanned and read only in selected regions
(setup, teardown, timing-sensitive waits), not line by line.

Two findings were confirmed by running throwaway probes against the app in the test
harness (outside the repository, no source file changed): the login lockout race (CR-01)
and the gate ordering violation (WR-02).

What holds up:

- The per-chat rate window is synchronous, shared by both channels, and removed by
  `agent.state.cleanup_chat_caches` on chat delete.
- In `post_chat_message` there is no `await` between `lock.locked()`, the rate check and
  `lock.acquire()`.
- The generation slot is released in a `finally`, the waiter count is restored on
  cancellation, and a failing `on_wait` does not leak a slot.
- A blocked login answers 429 before any credential check, identically for known and
  unknown usernames, and no forwarded header is trusted for the address.
- `verify_service.py` does not write the API key to its output files or console: the
  `key` argument is dropped from the recorded arguments, only an 8-character prefix is
  stored, and all output passes through `scrub()`.
- All frontend additions use `textContent`; no new unsanitized HTML insertion was found.

The main concern is the login lockout: it can be bypassed completely by sending guesses
in parallel, and separately weakened by table eviction.

## Critical Issues

### CR-01: Login lockout is bypassed by parallel requests (check-then-act race)

**File:** `agent/main.py:523-553` (with `agent/limits.py:64-97`)
**Issue:** `login()` calls `blocked_seconds(key)` first, then awaits the user lookup
(`await session.exec(...)`) and the password verification, and only afterwards calls
`record_failure(key)`. Every request that arrives before the first failure has been
recorded passes the block check. A client that sends its guesses concurrently is
therefore not limited by `LOGIN_MAX_FAILED_ATTEMPTS` at all for that burst, and a correct
password inside the burst logs in and resets the counter.

Confirmed by execution: with the threshold patched to 3, 40 concurrent wrong-password
requests for one user from one address returned 40 x 401 and 0 x 429; in a second run,
39 wrong passwords plus the correct one as the 40th concurrent request returned 200 for
the correct one. The existing tests only send attempts sequentially, so they do not see
this. The burst size is limited only by how many requests the server accepts at once, and
the attacker can repeat the burst after each lockout period.

**Fix:** Count the attempt in the same synchronous step as the block check, before any
`await`, and reset on success:

```python
# agent/limits.py
def register_attempt(self, key: tuple[str, str]) -> tuple[int | None, bool]:
    """Return (seconds blocked, block started); count the attempt when it may proceed."""
    blocked = self.blocked_seconds(key)
    if blocked is not None:
        return blocked, False
    return None, self.record_failure(key)

# agent/main.py::login
blocked, started = limits.login_throttle.register_attempt(key)   # no await before this
if blocked is not None:
    ...return the 429...
user = (await session.exec(select(User).where(User.username == body.username))).first()
if user is None or not verify_password(body.password, user.password_hash):
    ...log, raise 401...
limits.login_throttle.reset(key)
```

The attempt that reaches the threshold is still verified, so the visible behavior for
sequential clients is unchanged (N wrong answers get 401, the next gets 429). Add a test
that sends more than the threshold with `asyncio.gather` and asserts that at most
`LOGIN_MAX_FAILED_ATTEMPTS` requests get 401.

## Warnings

### WR-01: A client can evict its own failure counter by flooding the throttle table

**File:** `agent/limits.py:99-122`
**Issue:** When the table exceeds `MAX_ENTRIES` (10,000), `_evict` drops the unblocked
entry with the oldest failure. A client makes `threshold - 1` guesses against a real
account, then sends 10,001 failed logins under throwaway usernames from the same address;
its real counter is the oldest unblocked entry and is evicted, and it starts again from
zero. The flood is cheap because unknown usernames skip password hashing
(`agent/main.py:538-539`). The code comment at lines 99-107 describes exactly this and
accepts it, so this is a known gap rather than an oversight, but it means the lockout does
not hold against a scripted client. Confirmed by reading the eviction logic; not executed.
**Fix:** Bound the table per client address instead of only globally, for example keep a
per-IP count of entries and, once an address holds more than a small number (say 50) of
pairs with failures, answer further failures from that address with the 429 block instead
of inserting new pairs. Verifying a dummy hash for unknown usernames would also make the
flood expensive and close the timing difference between known and unknown usernames.

### WR-02: A later turn can overtake a queued turn despite the "arrival order" contract

**File:** `agent/limits.py:169-180`
**Issue:** A waiter does `await on_wait()` before `await semaphore.acquire()`. While
`on_wait` is suspended the caller is counted in `_waiting` but is not yet in the
semaphore's waiter queue. If a slot is released in that interval, a turn that arrives
afterwards sees `semaphore.locked() == False`, takes the slot and runs first, although the
class docstring promises arrival order. Confirmed by execution with a deliberately slow
`on_wait`: cap 1, A queued and suspended in `on_wait`, slot released, B arrives and runs
before A (`['B', 'A']`). In production `on_wait` is `websocket.send_json`, which only
suspends under write backpressure, so I expect this to be rare; that part is inference.
**Fix:** Enter the semaphore queue before announcing, so the position is fixed first:

```python
acquire = asyncio.ensure_future(semaphore.acquire())
try:
    if on_wait is not None:
        await on_wait()
    await acquire
except BaseException:
    if acquire.done() and not acquire.cancelled() and acquire.exception() is None:
        semaphore.release()
    else:
        acquire.cancel()
    raise
finally:
    self._waiting -= 1
```

### WR-03: A queued WebSocket turn keeps its place and later takes a slot after the client has gone

**File:** `agent/ws.py:1211-1237`, `agent/ws.py:1277-1314`
**Issue:** `ws_chat` awaits `_handle_chat_message` inline, so nothing reads the socket
while a turn waits in the gate, and the wait has no timeout by design. If the browser
closes or reloads while queued, the server does not notice: the turn stays in the queue
holding the per-chat lock, and when it gets a slot `_run_chat_turn` resolves the provider,
persists the user message and builds context before its first `send_json` can fail. With
a small `MAX_CONCURRENT_GENERATIONS`, abandoned turns occupy slots and delay live users,
and they leave a user message with no reply. Running a turn for a disconnected client
existed before the phase; the unbounded queue wait is what the phase adds. Inferred from
reading the code path; not reproduced.
**Fix:** After the slot is acquired and before `_run_chat_turn`, send a small frame (for
example `{"type": "dequeued", "chat_id": ...}`) and let a send failure abort the turn
before anything is persisted. This also gives the frontend an exact signal to remove the
queue note (see IN-01).

### WR-04: `--rate-mode both` can misnumber the request that receives the 429

**File:** `scripts/verify_service.py:457-469` (with `432-438`)
**Issue:** The fast sequence treats the earlier overflow demonstration as request 1 and
starts counting at 2. In `both` mode the chat sequence runs between the overflow request
and the fast sequence; it sends up to the server limit of real model turns and commonly
takes longer than the 60-second window. By then the overflow request has left the window,
so the 429 arrives one request later than the numbering assumes. With
`--server-rate-limit` the report then states "429 expected on request N+1, arrived on
request N+2" against a server that behaves correctly, and `requests_sent` includes a
request that did not count. Inferred from reading; not executed against a live server.
**Fix:** In `both` mode (or whenever more than a few seconds have passed since the
overflow request), start the fast sequence with `first_number=1` and `prior=[]`, or wait
out the window before starting it. Record the overflow timestamp to decide.

### WR-05: The rate-limit sequence keeps sending after transport failures

**File:** `scripts/verify_service.py:402-420`
**Issue:** `sequence()` breaks only on 429. Any other outcome, including `status=None`
(connection refused, timeout) or repeated 5xx, just advances the counter, so if the
server stops responding mid-run the loop issues up to `--rate-max-attempts` (40) more
requests, each waiting up to `--timeout` (300 s by default): about 3 hours 20 minutes in
the worst case before the script reports anything. Confirmed by reading the loop.
**Fix:** Stop the sequence after a small number of consecutive transport errors and
record it as a failure:

```python
if reply.status is None:
    transport_errors += 1
    if transport_errors >= 3:
        result["notes"].append("aborted: server stopped answering")
        break
else:
    transport_errors = 0
```

## Info

### IN-01: The queue note stays visible after the turn has left the queue

**File:** `ui/static/app.js:1411-1428`
**Issue:** The note "В очереди, ожидание свободного места…" is removed only by the first
`token` frame. No frame marks the moment a slot is obtained, so the note stays through
context building, RAG preparation, tool-call rounds and time-to-first-token, while the
turn is already running. A re-render of the message list while streaming re-creates the
loading bubble without the note.
**Fix:** Emit a `dequeued` frame from `run_limited_turn` when `waited > 0` and remove the
note on it (shared fix with WR-03); also call `removeQueueNote()` on `tool_call`.

### IN-02: `verify_service.py` argument and failure handling gaps

**File:** `scripts/verify_service.py:106`, `284`, `629-645`
**Issue:** (a) A URL such as `//host:8001` raises an uncaught `ValueError` from
`urllib.request.Request` and ends in a traceback instead of exit code 2 (confirmed by
running it); a URL without a scheme produces the unhelpful "unknown url type". (b) Giving
only one of `--model` / `--provider-id` is silently ignored and a model is auto-picked,
while the recorded arguments still show the value. (c) Any unexpected exception in
`parallel`/`overflow`/`rate_limit` runs cleanup but skips `write_outputs`, so no result
files exist for the failed run. (d) The default opener follows redirects and resends the
`Authorization` header to the redirect target.
**Fix:** Validate that `--url` starts with `http://` or `https://` and that `--model` and
`--provider-id` are given together (exit 2 otherwise); wrap the run so `write_outputs`
always executes; use an opener with a redirect handler that refuses redirects.

### IN-03: Outcomes that do not affect the exit code

**File:** `scripts/verify_service.py:413-414`, `483-489`
**Issue:** A 429 whose code is not `RATE_LIMITED` is counted as "limit reached" with only
a note, and chats that could not be deleted are only printed. Both leave exit code 0.
**Fix:** Append to `self.failures` in both cases, or state in the module docstring that
they are informational.

### IN-04: Rate-window details on the WebSocket path

**File:** `agent/ws.py:1288-1312`, `agent/limits.py:40`
**Issue:** The rate check runs before `MessagePayload.model_validate`, so malformed
frames use up the chat's quota (same as before the phase). A socket left open on a
deleted chat re-creates the `ws_rate_limiter` entry on its next frame after
`cleanup_chat_caches` removed it; the entry is small and bounded by the limit, but is
never removed.
**Fix:** Validate the payload first; optionally skip writing an empty window back
(`if window: ws_rate_limiter[chat_id] = window`) for a chat with no recorded messages.

### IN-05: Unreachable cleanup branch

**File:** `scripts/verify_service.py:640-642`
**Issue:** The outer `finally` runs cleanup only when chats exist and cleanup has not
run. Chats are created only after `preflight()` succeeds, and that branch always reaches
the inner `finally`, so this code cannot execute.
**Fix:** Remove the outer `try/finally`.

## Convention (advisory, never blocking)

Reported by `gsd-tools verify conventions --check`. All three lines are outside the
phase's hunks in `ui/static/app.js` (the phase changed lines around 1357-1430 and
1808-1820), so they predate this phase.

### CV-01: Swallowed catch block

**File:** `ui/static/app.js:998`
**Deviation:** catch block swallows the error (empty / no rethrow).
**Convention:** architectural-split: error handling — swallowed catches hide failures.
**Suggested fix:** rethrow, wrap (`throw new Error(..., { cause })`), or log and handle deliberately.

### CV-02: Swallowed catch block

**File:** `ui/static/app.js:2194`
**Deviation:** catch block swallows the error (empty / no rethrow).
**Convention:** architectural-split: error handling — swallowed catches hide failures.
**Suggested fix:** rethrow, wrap, or log and handle deliberately.

### CV-03: Swallowed catch block

**File:** `ui/static/app.js:3482`
**Deviation:** catch block swallows the error (empty / no rethrow).
**Convention:** architectural-split: error handling — swallowed catches hide failures.
**Suggested fix:** rethrow, wrap, or log and handle deliberately.

---

_Reviewed: 2026-10-12T19:40:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
