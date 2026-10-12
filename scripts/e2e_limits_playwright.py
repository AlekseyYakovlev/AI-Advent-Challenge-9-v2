"""Limits, queue and verification-script check on an isolated copy of the app.

Usage: python scripts/e2e_limits_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it in loopback mode with a scratch database, low demo limits (5 messages per minute per
chat, 2 parallel generations, 3 failed logins, 120 s lockout) and a deliberately slow stub
OpenAI-compatible provider on 127.0.0.1:18766. It checks the supervisor start-up with the guarded
route, the limits log line and the route guard, runs scripts/verify_service.py against the copy
as a separate process, and drives headless Chromium through the login lockout message, the
rate-limit toast and the queue caption. One PASS/FAIL line is printed per check.

This is a rehearsal on one machine through loopback, not the live run from a second LAN machine.
Nothing is written under eval_out/. The default ports of a running app, the repository .env,
app.db and the firewall are never touched; only processes started by this script are stopped, and
the scratch directory (results.json, app.log, verify output, screenshots) is kept and its path
printed.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy),
4 Playwright not installed.
"""

import asyncio
import json
import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_api_keys_playwright as keys  # noqa: E402
import e2e_llm_providers_playwright as prov  # noqa: E402

UI_URL: str = prov.UI_URL
AGENT_URL: str = prov.AGENT_URL
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4
STEP_TIMEOUT_MS: int = prov.STEP_TIMEOUT_MS
PLAYWRIGHT_TIMEOUT: float = 240.0

STUB_DELAY: float = 3.0
DEMO_RATE_LIMIT: int = 5
DEMO_MAX_CONCURRENT: int = 2
DEMO_LOGIN_ATTEMPTS: int = 3
DEMO_LOCKOUT_SECONDS: int = 120
PARALLEL_N: int = 6
STARTUP_LIMIT_SECONDS: float = 30.0
VERIFY_TIMEOUT: float = 240.0
VERIFY_TITLE_PREFIX: str = "verify-service"
QUEUE_TEXT: str = "В очереди, ожидание свободного места…"
RATE_TOAST_RE = re.compile(r"Слишком много сообщений\. Повторите через (\d+) с")
LOCKOUT_RE = re.compile(r"Слишком много неудачных попыток\. Повторите через (\d+) мин")

report = prov.report
checks = prov.checks

stub_state: dict[str, int] = {"in_flight": 0, "max_in_flight": 0}
timings: dict[str, Any] = {}


def build_slow_stub_app() -> Any:
    """Build the stub provider whose streamed answers take STUB_DELAY seconds."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    stub = FastAPI()

    @stub.get("/v1/models")
    async def models(request: Request) -> Any:
        if request.headers.get("authorization", "") != f"Bearer {prov.STUB_KEY_VALUE}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return {"data": [{"id": "stub-model"}]}

    @stub.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        if request.headers.get("authorization", "") != f"Bearer {prov.STUB_KEY_VALUE}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body: dict[str, Any] = await request.json()
        if body.get("stream") is not True:
            text = json.dumps(body.get("messages", []), ensure_ascii=False)
            content = prov.STUB_TITLE if "<user_message>" in text else "{}"
            return JSONResponse(
                {
                    "id": "stub",
                    "object": "chat.completion",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"},
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        async def stream() -> Any:
            stub_state["in_flight"] += 1
            stub_state["max_in_flight"] = max(stub_state["max_in_flight"], stub_state["in_flight"])
            try:
                await asyncio.sleep(STUB_DELAY)
                chunk = {"choices": [{"delta": {"content": prov.STUB_ANSWER},
                                      "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n"
                done = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
                yield f"data: {json.dumps(done)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                stub_state["in_flight"] -= 1

        return StreamingResponse(stream(), media_type="text/event-stream")

    return stub


async def start_slow_stub() -> tuple[Any, asyncio.Task[None]]:
    """Run the slow stub in this event loop and wait until it accepts connections."""
    import uvicorn

    config = uvicorn.Config(
        build_slow_stub_app(), host=prov.HOST, port=prov.STUB_PORT, log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline and not task.done():
        await asyncio.sleep(0.1)
    if not server.started:
        raise RuntimeError("slow stub provider did not start")
    return server, task


def user_app_pids() -> set[int]:
    """Read-only: PIDs listening on the default app ports 8000 and 8001."""
    pids: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if (conn.laddr and conn.laddr.port in (8000, 8001)
                    and conn.status == psutil.CONN_LISTEN and conn.pid):
                pids.add(conn.pid)
    except psutil.AccessDenied:
        pass
    return pids


def log_has_value(line: str, key: str, value: int) -> bool:
    """Return True when the log line holds key with the given integer value."""
    return re.search(rf"{key}\W{{1,4}}{value}\b", line) is not None


async def create_api_key(username: str, password: str) -> str:
    """Log in with a cookie client and create an API key for the scratch user."""
    headers = {"Origin": UI_URL}
    async with httpx.AsyncClient(timeout=15.0) as client:
        login = await client.post(
            f"{AGENT_URL}/api/v1/auth/login",
            json={"username": username, "password": password}, headers=headers,
        )
        login.raise_for_status()
        created = await client.post(
            f"{AGENT_URL}/api/v1/api-keys", json={"name": "limits-e2e"}, headers=headers,
        )
        created.raise_for_status()
        return str(created.json()["key"])


async def network_checks(
    copy_dir: Path, startup_seconds: float, app_log: Path, username: str, password: str,
    provider_id: int,
) -> str | None:
    """Run S1-S4 and return the API key (None when it could not be made)."""
    log_text = app_log.read_text(encoding="utf-8", errors="replace")
    report(
        "S1 supervisor reports the Agent ready on the first launch with the guarded route",
        "agent_ready" in log_text and "agent_startup_incomplete" not in log_text
        and "agent_not_ready" not in log_text and startup_seconds < STARTUP_LIMIT_SECONDS,
        f"startup={startup_seconds:.1f}s",
    )
    timings["startup_seconds"] = round(startup_seconds, 1)

    agent_log = copy_dir / "logs" / "agent.log"
    agent_text = agent_log.read_text(encoding="utf-8", errors="replace") if agent_log.exists() else ""
    lines = [ln for ln in agent_text.splitlines() if "limits_in_force" in ln]
    line = lines[0] if lines else ""
    report(
        "S2 Agent log holds limits_in_force with the demo values",
        len(lines) == 1 and log_has_value(line, "rate_limit_per_minute", DEMO_RATE_LIMIT)
        and log_has_value(line, "login_max_failed_attempts", DEMO_LOGIN_ATTEMPTS)
        and log_has_value(line, "login_lockout_seconds", DEMO_LOCKOUT_SECONDS)
        and log_has_value(line, "max_concurrent_generations", DEMO_MAX_CONCURRENT),
        f"lines={len(lines)}",
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        health = await client.get(f"{AGENT_URL}/health")
        guarded = await client.get(f"{AGENT_URL}/debug/routes")
    report(
        "S3 /health is 200 and /debug/routes is 401 without credentials",
        health.status_code == 200 and guarded.status_code == 401,
        f"health={health.status_code}, routes={guarded.status_code}",
    )

    key: str | None = None
    try:
        key = await create_api_key(username, password)
        async with httpx.AsyncClient(timeout=10.0) as client:
            routes = await client.get(f"{AGENT_URL}/debug/routes", headers=keys.bearer(key))
        listed = "/ws/chat/{chat_id}" in routes.text
        report(
            "S4 API key created; /debug/routes with the key is 200 and lists the chat socket",
            routes.status_code == 200 and listed,
            f"status={routes.status_code}, provider_id={provider_id}, listed={listed}",
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        report("S4 API key created for the scratch user", False, f"{type(exc).__name__}: {exc}")
    return key


async def verification_run(key: str, provider_id: int, scratch: Path) -> None:
    """Run scripts/verify_service.py as a separate process and check V1."""
    out_dir = scratch / "verify"
    env = {**os.environ, "AIA_API_KEY": key, "PYTHONIOENCODING": "utf-8"}
    stub_state["max_in_flight"] = stub_state["in_flight"]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(prov.REPO_ROOT / "scripts" / "verify_service.py"),
        "--url", f"http://{prov.HOST}:{prov.AGENT_PORT}",
        "--provider-id", str(provider_id), "--model", "stub-model",
        "--n", str(PARALLEL_N), "--rate-mode", "both",
        "--server-rate-limit", str(DEMO_RATE_LIMIT),
        "--server-max-concurrent", str(DEMO_MAX_CONCURRENT),
        "--timeout", "120", "--out", str(out_dir),
        cwd=str(scratch), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), VERIFY_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        report("V1 verification script finished in time", False, f"over {VERIFY_TIMEOUT}s")
        return
    output = stdout_b.decode("utf-8", errors="replace") + stderr_b.decode("utf-8", errors="replace")
    (scratch / "verify_stdout.txt").write_text(output, encoding="utf-8")
    report("V1a verification script exit code 0", proc.returncode == 0, f"exit={proc.returncode}")

    json_path, md_path = out_dir / "verify_results.json", out_dir / "verify_results.md"
    report("V1b both output files exist", json_path.exists() and md_path.exists(), str(out_dir))
    if not json_path.exists() or not md_path.exists():
        return
    json_text = json_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(json_text)

    report(
        "V1c reachability and both authentication checks passed",
        data["reachability"].get("ok") is True and data["authentication"].get("ok") is True,
        f"reachability={data['reachability'].get('status')}, "
        f"good={data['authentication'].get('good_status')}, "
        f"bad={data['authentication'].get('bad_status')}",
    )

    records = data["parallel"].get("records", [])
    successes = [r for r in records if r.get("status") == 200]
    report(f"V1d {PARALLEL_N} of {PARALLEL_N} parallel requests succeeded",
           len(records) == PARALLEL_N and len(successes) == PARALLEL_N,
           f"records={len(records)}, successes={len(successes)}")

    waits = [float(r["queue_wait_seconds"] or 0) for r in records]
    queued = [w for w in waits if w > 0]
    timings["queue_waits"] = [round(w, 2) for w in waits]
    report(
        "V1e at least 3 requests waited in the queue, the longest about one stub turn",
        len(queued) >= 3 and max(waits, default=0) >= STUB_DELAY - 0.5,
        f"waits={timings['queue_waits']}",
    )
    report(
        f"V1f the cap of {DEMO_MAX_CONCURRENT} held (stub saw at most that many streams)",
        0 < stub_state["max_in_flight"] <= DEMO_MAX_CONCURRENT,
        f"max_in_flight={stub_state['max_in_flight']}",
    )

    sequences = {s.get("mode"): s for s in data["rate_limit"].get("sequences", [])}
    chat_seq, fast_seq = sequences.get("chat", {}), sequences.get("fast", {})
    retry = chat_seq.get("retry_after_header")
    retry_ok = str(retry).isdigit() and 1 <= int(retry) <= 60
    report(
        "V1g chat mode: 429 on request 6 with Retry-After between 1 and 60",
        chat_seq.get("limit_request") == DEMO_RATE_LIMIT + 1 and retry_ok
        and chat_seq.get("code") == "RATE_LIMITED",
        f"limit_request={chat_seq.get('limit_request')}, retry_after={retry}",
    )
    fast_retry = fast_seq.get("retry_after_header")
    report(
        "V1h fast mode: 429 on request 6 of the overflow chat with Retry-After",
        fast_seq.get("limit_request") == DEMO_RATE_LIMIT + 1
        and str(fast_retry).isdigit() and 1 <= int(fast_retry) <= 60,
        f"limit_request={fast_seq.get('limit_request')}, retry_after={fast_retry}",
    )
    overflow = data["overflow"]
    report("V1i overflow answered 413 CONTEXT_OVERFLOW",
           overflow.get("status") == 413 and overflow.get("code") == "CONTEXT_OVERFLOW",
           f"status={overflow.get('status')}, code={overflow.get('code')}")

    md_text = md_path.read_text(encoding="utf-8")
    report("V1j the full key is absent from the output and both files",
           key not in output and key not in json_text and key not in md_text)

    async with httpx.AsyncClient(timeout=15.0) as client:
        listing = await client.get(f"{AGENT_URL}/api/v1/chats", headers=keys.bearer(key))
    leftovers = [c for c in (listing.json() if listing.status_code == 200 else [])
                 if str(c.get("title", "")).startswith(VERIFY_TITLE_PREFIX)]
    report("V1k the script's chats are gone afterwards",
           listing.status_code == 200 and not leftovers,
           f"status={listing.status_code}, leftovers={len(leftovers)}")


async def select_stub_model(page: Any) -> None:
    """Pick the stub provider's model in the model selector."""
    await page.wait_for_function(
        "() => Array.from(document.querySelectorAll('#model-select option'))"
        ".some(o => o.textContent === 'Stub · stub-model')",
        timeout=STEP_TIMEOUT_MS,
    )
    value = await page.evaluate(
        "() => Array.from(document.querySelectorAll('#model-select option'))"
        ".find(o => o.textContent === 'Stub · stub-model').value",
    )
    await page.select_option("#model-select", value)


async def new_chat(page: Any) -> None:
    """Create a chat in the browser and wait until its socket is open."""
    before = await page.evaluate("state.chats.length")
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "(before) => state.chats.length > before && state.currentChatId === state.chats[0].id"
        " && state.ws && state.ws.readyState === WebSocket.OPEN"
        " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
        arg=before, timeout=15000,
    )
    await select_stub_model(page)


async def login_lockout(page: Any, scratch: Path, username: str, password: str) -> None:
    """B1: wrong passwords beyond the threshold show the lockout message."""
    ghost = f"ghost-{secrets.token_hex(3)}"
    await page.goto(f"{UI_URL}/static/login.html")
    normal: list[str] = []
    for _ in range(DEMO_LOGIN_ATTEMPTS):
        await page.fill("#login-username", ghost)
        await page.fill("#login-password", "wrong-" + secrets.token_hex(3))
        await page.click("#login-form button[type=submit]")
        await page.wait_for_function(
            "document.getElementById('login-error').textContent.length > 0", timeout=10000,
        )
        normal.append((await page.inner_text("#login-error")).strip())
        await page.evaluate("document.getElementById('login-error').textContent = ''")
    await page.fill("#login-username", ghost)
    await page.fill("#login-password", "wrong-" + secrets.token_hex(3))
    await page.click("#login-form button[type=submit]")
    await page.wait_for_function(
        "document.getElementById('login-error').textContent.length > 0", timeout=10000,
    )
    blocked = (await page.inner_text("#login-error")).strip()
    match = LOCKOUT_RE.fullmatch(blocked)
    minutes = int(match.group(1)) if match else -1
    button_enabled = await page.is_enabled("#login-form button[type=submit]")
    await prov.shot(page, scratch, "B1")

    probe = await page.evaluate(
        """async (args) => {
          const r = await fetch(args.url, {method: 'POST', credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: args.username, password: 'x'})});
          return {status: r.status, retry: r.headers.get('Retry-After')};
        }""",
        {"url": f"{AGENT_URL}/api/v1/auth/login", "username": ghost},
    )
    retry = probe.get("retry")
    report(
        "B1 wrong passwords beyond the threshold show the lockout message and the header is readable",
        all(t and not LOCKOUT_RE.search(t) for t in normal) and match is not None
        and 1 <= minutes <= 2 and probe.get("status") == 429
        and retry is not None and str(retry).isdigit() and button_enabled,
        f"normal={normal[:1]}, blocked={blocked!r}, probe={probe}, button_enabled={button_enabled}",
    )

    await page.fill("#login-username", username)
    await page.fill("#login-password", password)
    await page.click("#login-form button[type=submit]")
    try:
        await page.wait_for_url("**/static/index.html", timeout=15000)
        await page.wait_for_function(
            "document.querySelector('#model-select') !== null", timeout=15000,
        )
        other_ok = True
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        other_ok = False
        print(f"note: login of the other user ended with {type(exc).__name__}")
    report("B1b a block on one username does not stop another from logging in", other_ok)
    await asyncio.sleep(1.0)  # let the events socket connect


async def rate_limit_toast(page: Any, scratch: Path) -> None:
    """B2: the message over the per-chat limit shows the toast and returns the text."""
    await new_chat(page)
    for number in range(1, DEMO_RATE_LIMIT + 1):
        await page.fill("#message-input", f"Сообщение номер {number}")
        await page.click("#btn-send")
        await page.wait_for_function(
            "(n) => document.querySelectorAll('#messages [data-message-id]').length === n"
            " && !state.isStreaming",
            arg=2 * number, timeout=60000,
        )
    sixth = f"Шестое сообщение {secrets.token_hex(3)}"
    await page.evaluate("window.__toasts = []")
    await page.fill("#message-input", sixth)
    await page.click("#btn-send")
    toast = ""
    try:
        await page.wait_for_function(
            "() => window.__toasts.some(t => t.includes('Слишком много сообщений'))",
            timeout=10000,
        )
        toasts = await page.evaluate("window.__toasts")
        toast = next((t for t in toasts if "Слишком много сообщений" in t), "")
    except Exception as exc:  # noqa: BLE001 - reported below
        print(f"note: toast wait ended with {type(exc).__name__}")
    await asyncio.sleep(2.5)
    toasts_later = await page.evaluate("window.__toasts")
    first_text = toast.strip()
    stable = all(t.strip() == first_text for t in toasts_later if "Слишком много" in t)
    match = RATE_TOAST_RE.search(toast)
    seconds = int(match.group(1)) if match else -1
    input_value = await page.input_value("#message-input")
    bubbles = await page.locator("#messages .flex.justify-end").count()
    loading = await page.locator('#messages [data-streaming="true"]').count()
    send_enabled = await page.is_enabled("#btn-send")
    countdown = await page.locator("[class*=countdown], [id*=countdown]").count()
    await prov.shot(page, scratch, "B2")
    report(
        "B2 over-limit message shows the toast, keeps the text and leaves no bubble",
        match is not None and 1 <= seconds <= 60 and input_value == sixth
        and bubbles == DEMO_RATE_LIMIT and loading == 0 and send_enabled
        and countdown == 0 and stable,
        f"toast={toast!r}, input_kept={input_value == sixth}, bubbles={bubbles}, "
        f"loading={loading}, send_enabled={send_enabled}, countdown_elements={countdown}",
    )


async def queue_caption(
    page: Any, scratch: Path, key: str, provider_id: int,
) -> None:
    """B3: a turn that waits for a generation slot shows the queue caption, then a reply."""
    await new_chat(page)
    chat_ids: list[int] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for index in range(DEMO_MAX_CONCURRENT):
            made = await client.post(
                f"{AGENT_URL}/api/v1/chats", json={"title": f"e2e-queue-{index}"},
                headers=keys.bearer(key),
            )
            chat_ids.append(int(made.json()["id"]))
    background = [
        asyncio.create_task(keys.post_http_message(key, cid, provider_id, "занять слот"))
        for cid in chat_ids
    ]
    await asyncio.sleep(0.5)
    await page.fill("#message-input", "Сообщение в очереди")
    await page.click("#btn-send")
    note_text = ""
    try:
        note = page.locator('[data-streaming="true"] .queue-note')
        await note.first.wait_for(state="visible", timeout=2000)
        note_text = (await note.first.inner_text()).strip()
    except Exception as exc:  # noqa: BLE001 - reported below
        print(f"note: queue caption wait ended with {type(exc).__name__}")
    await prov.shot(page, scratch, "B3")
    replied = False
    try:
        await page.locator(
            "#messages [data-message-id] .message-content", has_text=prov.STUB_ANSWER,
        ).first.wait_for(timeout=60000)
        await page.wait_for_function(
            "() => !state.isStreaming && document.querySelectorAll('#messages [data-message-id]')"
            ".length === 2",
            timeout=30000,
        )
        replied = True
    except Exception as exc:  # noqa: BLE001 - reported below
        print(f"note: reply wait ended with {type(exc).__name__}")
    notes_left = await page.locator(".queue-note").count()
    statuses = await asyncio.gather(*background)
    report(
        "B3 queued turn shows the queue caption, then a reply, and the caption is gone",
        note_text == QUEUE_TEXT and replied and notes_left == 0 and statuses == [200, 200],
        f"note={note_text!r}, replied={replied}, notes_left={notes_left}, background={statuses}",
    )


async def nothing_about_limits(page: Any) -> None:
    """B4: Settings shows no limit names and no configured numbers as limits."""
    await page.click("#btn-settings")
    await asyncio.sleep(0.5)
    text = await page.evaluate("document.body.innerText")
    names = ("RATE_LIMIT_MESSAGES_PER_MINUTE", "MAX_CONCURRENT_GENERATIONS",
             "LOGIN_MAX_FAILED_ATTEMPTS", "LOGIN_LOCKOUT_SECONDS")
    found = [n for n in names if n.lower() in text.lower()]
    numbers = re.search(r"(лимит|limit)[^\n]{0,40}\b(5|2|3|120)\b", text, re.IGNORECASE)
    report("B4 Settings shows no limit names or configured numbers",
           not found and numbers is None, f"names={found}, numbers={bool(numbers)}")
    await page.click("#btn-close-settings")


async def drive_browser(
    username: str, password: str, scratch: Path, key: str, provider_id: int,
) -> None:
    """Run the browser scenarios B1-B4 and the page error check."""
    from playwright.async_api import async_playwright

    page_errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.add_init_script(prov.TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await login_lockout(page, scratch, username, password)
            await rate_limit_toast(page, scratch)
            await queue_caption(page, scratch, key, provider_id)
            await nothing_about_limits(page)
            report("B5 no uncaught page errors during B1-B4", not page_errors,
                   f"errors={page_errors}")
        except Exception as exc:
            report("browser scenario completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
            try:
                await prov.shot(page, scratch, "failure")
            except Exception as inner:  # noqa: BLE001 - evidence capture is best effort
                print(f"note: failure capture failed: {type(inner).__name__}")
        finally:
            await browser.close()


async def run_e2e() -> None:
    """Start the isolated copy, run the checks and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="limits_e2e_"))
    print(f"scratch directory: {scratch}")
    db_path = scratch / "limits.db"
    username, password = "limits30", secrets.token_urlsafe(12)
    app_proc: asyncio.subprocess.Process | None = None
    stub: Any = None
    log_file = None
    before_pids = user_app_pids()
    try:
        copy_dir = prov.prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        await prov.seed_scratch_user(db_path, username, password)
        report("scratch database is isolated from app.db", True, str(db_path))
        stub = await start_slow_stub()

        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "UI_PORT": str(prov.UI_PORT),
            "AGENT_PORT": str(prov.AGENT_PORT),
            "BIND_HOST": prov.HOST,
            "LM_STUDIO_BASE_URL": f"http://{prov.HOST}:{prov.CLOSED_LM_PORT}",
            "DEEPSEEK_API_KEY": "",
            "RATE_LIMIT_MESSAGES_PER_MINUTE": str(DEMO_RATE_LIMIT),
            "MAX_CONCURRENT_GENERATIONS": str(DEMO_MAX_CONCURRENT),
            "LOGIN_MAX_FAILED_ATTEMPTS": str(DEMO_LOGIN_ATTEMPTS),
            "LOGIN_LOCKOUT_SECONDS": str(DEMO_LOCKOUT_SECONDS),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(
                [str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])],
            ),
        }
        log_file = open(scratch / "app.log", "wb")
        started = time.monotonic()
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable, str(copy_dir / "run.py"),
            cwd=str(copy_dir), env=env, stdout=log_file, stderr=log_file,
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await prov.wait_for_app(client)
        startup_seconds = time.monotonic() - started
        report("isolated app instance is up (UI :18000 + Agent :18001)", ready,
               f"{startup_seconds:.1f}s")
        if ready:
            provider_id = await keys.register_stub_provider(username, password)
            report("stub provider registered for the scratch user", True,
                   f"provider_id={provider_id}")
            log_file.flush()
            key = await network_checks(
                copy_dir, startup_seconds, scratch / "app.log", username, password, provider_id,
            )
            if key is not None:
                await verification_run(key, provider_id, scratch)
                await asyncio.wait_for(
                    drive_browser(username, password, scratch, key, provider_id),
                    PLAYWRIGHT_TIMEOUT,
                )
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await prov.teardown(app_proc, stub)
        if log_file is not None:
            log_file.close()
        after_pids = user_app_pids()
        report("ports 8000/8001 untouched", before_pids == after_pids,
               f"before={sorted(before_pids)}, after={sorted(after_pids)}")
        (scratch / "results.json").write_text(
            json.dumps(
                [{"check": c, "passed": p, "detail": d} for c, p, d in checks],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"timings: {timings}")
        print(f"scratch kept for inspection: {scratch}")


def main() -> None:
    """Run preflight, then the isolated end-to-end scenario, and exit with the result code."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed (pip install playwright && playwright install chromium).")
        sys.exit(EXIT_NO_PLAYWRIGHT)

    blocked = prov.preflight()
    if blocked is not None:
        sys.exit(blocked)

    asyncio.run(run_e2e())
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
