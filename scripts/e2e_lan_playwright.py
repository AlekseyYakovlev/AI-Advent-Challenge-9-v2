"""LAN-mode check on an isolated copy of the app, reached through the machine's LAN address.

Usage: python scripts/e2e_lan_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with BIND_HOST=0.0.0.0, a scratch database and a stub OpenAI-compatible provider, then
checks sockets, the startup banner, the Origin rules and (browser part) the real UI opened by the
machine's LAN IPv4 address. The ports 8000/8001 of a running app, the repository .env and the
firewall are never touched; only processes started by this script are stopped. Windows may show a
Security Alert when the copy binds 0.0.0.0; the checks work without answering it because the
connections come from the same machine.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy or no LAN
address), 4 Playwright not installed.
"""

import asyncio
import json
import os
import secrets
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_llm_providers_playwright as prov  # noqa: E402

REPO_ROOT: Path = prov.REPO_ROOT

from shared.network import lan_ipv4_addresses  # noqa: E402

UI_PORT: int = prov.UI_PORT
AGENT_PORT: int = prov.AGENT_PORT
FOREIGN_PORT: int = 18770
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4
PLAYWRIGHT_TIMEOUT: float = 300.0
STEP_TIMEOUT_MS: int = 20000
EVIL_ORIGIN: str = "http://evil.example:18000"

report = prov.report
checks = prov.checks
port_is_free = prov.port_is_free


def preflight() -> tuple[int | None, list[str]]:
    """Return (exit code, LAN addresses); a code means the run must not start."""
    ports = (UI_PORT, AGENT_PORT, prov.STUB_PORT, prov.CLOSED_LM_PORT, FOREIGN_PORT)
    busy = [p for p in ports if not port_is_free(p)]
    if busy:
        print(f"BLOCKED: port(s) {busy} are in use; the isolated copy needs them free.")
        return EXIT_BLOCKED, []
    addresses = lan_ipv4_addresses()
    if not addresses:
        print("BLOCKED: no non-loopback IPv4 address found; LAN mode cannot be exercised.")
        return EXIT_BLOCKED, []
    return None, addresses


async def wait_for_agent(client: httpx.AsyncClient) -> bool:
    """Poll the loopback health endpoint of the copy's Agent until it answers 200."""
    deadline = time.monotonic() + prov.STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            resp = await client.get(f"http://127.0.0.1:{AGENT_PORT}/health")
            ui = await client.get(f"http://127.0.0.1:{UI_PORT}/")
            if resp.status_code == 200 and ui.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    return False


async def pick_lan_ip(client: httpx.AsyncClient, addresses: list[str]) -> str | None:
    """Return the first LAN address whose Agent /health answers 200 within 3 seconds."""
    for address in addresses:
        try:
            resp = await client.get(f"http://{address}:{AGENT_PORT}/health", timeout=3.0)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            return address
    return None


def listening_addresses(port: int) -> list[str]:
    """Return the local addresses of LISTEN sockets bound to the port."""
    found: list[str] = []
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
            found.append(conn.laddr.ip)
    return found


async def preflight_origin(client: httpx.AsyncClient, base: str, origin: str) -> str | None:
    """Send a CORS preflight to base/health and return the allow-origin header (or None)."""
    resp = await client.options(
        f"{base}/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    return resp.headers.get("access-control-allow-origin")


async def network_checks(
    lan_ip: str, username: str, password: str, scratch: Path, copy_dir: Path,
) -> None:
    """Run the httpx-level checks 1-7 against the LAN address."""
    lan_ui = f"http://{lan_ip}:{UI_PORT}"
    lan_agent = f"http://{lan_ip}:{AGENT_PORT}"
    app_log = (scratch / "app.log").read_text(encoding="utf-8", errors="replace")

    # 1 banner
    report(
        "1 startup banner lists LAN mode and the LAN address",
        "WARNING: LAN mode is ON" in app_log
        and "Starting UI on:" in app_log
        and f"    http://{lan_ip}:{UI_PORT}" in app_log
        and f"Starting UI on http://127.0.0.1:{UI_PORT}" not in app_log,
        f"lan_ip={lan_ip}",
    )

    # 2 sockets
    ui_bound = listening_addresses(UI_PORT)
    agent_bound = listening_addresses(AGENT_PORT)
    report(
        "2 UI and Agent listen on 0.0.0.0",
        "0.0.0.0" in ui_bound and "0.0.0.0" in agent_bound,
        f"ui={ui_bound}, agent={agent_bound}",
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 3 reachable
        ui = await client.get(f"{lan_ui}/")
        health = await client.get(f"{lan_agent}/health")
        report(
            "3 UI root and Agent health answer on the LAN address",
            ui.status_code == 200 and health.status_code == 200,
            f"ui={ui.status_code}, health={health.status_code}",
        )

        # 4 CORS preflight
        own = await preflight_origin(client, lan_agent, lan_ui)
        evil = await preflight_origin(client, lan_agent, EVIL_ORIGIN)
        report(
            "4 preflight reflects the LAN origin and not a foreign one",
            own == lan_ui and evil is None,
            f"own={own}, evil={evil}",
        )

        # 5 machine name
        hostname = socket.gethostname()
        try:
            named = await client.get(f"http://{hostname}:{AGENT_PORT}/health", timeout=3.0)
            resolvable = named.status_code == 200
        except httpx.HTTPError:
            resolvable = False
        if resolvable:
            reflected = await preflight_origin(
                client, f"http://{hostname}:{AGENT_PORT}", f"http://{hostname}:{UI_PORT}",
            )
            report(
                "5 preflight reflects the machine-name origin",
                reflected == f"http://{hostname}:{UI_PORT}",
                f"hostname={hostname}, reflected={reflected}",
            )
        else:
            report("5 preflight reflects the machine-name origin", True,
                   "skipped: hostname not resolvable")

        # 6 login and network info
        login = await client.post(
            f"{lan_agent}/api/v1/auth/login",
            json={"username": username, "password": password},
            headers={"Origin": lan_ui},
        )
        has_cookie = bool(client.cookies)
        info = await client.get(f"{lan_agent}/api/v1/network")
        body: dict[str, Any] = info.json() if info.status_code == 200 else {}
        async with httpx.AsyncClient(timeout=10.0) as anonymous:
            anon = await anonymous.get(f"{lan_agent}/api/v1/network")
        report(
            "6 login over the LAN address, /api/v1/network reports lan mode",
            login.status_code == 200 and has_cookie
            and body.get("mode") == "lan" and lan_ui in body.get("addresses", [])
            and anon.status_code == 401,
            f"login={login.status_code}, mode={body.get('mode')}, anonymous={anon.status_code}",
        )

        # 7 mutation origin rules
        payload = {
            "name": "lan-throwaway",
            "base_url": f"http://127.0.0.1:{prov.STUB_PORT}",
            "api_key_env": "STUB_KEY",
            "enabled": False,
        }
        created = await client.post(
            f"{lan_agent}/api/v1/llm-providers", json=payload, headers={"Origin": lan_ui},
        )
        deleted_status = 0
        if created.status_code == 201:
            deleted = await client.delete(
                f"{lan_agent}/api/v1/llm-providers/{created.json()['id']}",
                headers={"Origin": lan_ui},
            )
            deleted_status = deleted.status_code
        foreign_statuses: list[tuple[int, str]] = []
        for origin in (EVIL_ORIGIN, f"http://{lan_ip}:{AGENT_PORT}"):
            resp = await client.post(
                f"{lan_agent}/api/v1/llm-providers", json=payload, headers={"Origin": origin},
            )
            foreign_statuses.append((resp.status_code, resp.text))
        report(
            "7 own-origin mutation succeeds, foreign-origin mutations get 403",
            created.status_code == 201 and deleted_status == 204
            and all(
                code == 403 and "Cross-origin request rejected" in text
                for code, text in foreign_statuses
            ),
            f"create={created.status_code}, delete={deleted_status}, "
            f"foreign={[code for code, _t in foreign_statuses]}",
        )


WS_PROBE_JS: str = """
(url) => new Promise((resolve) => {
  let opened = false;
  const ws = new WebSocket(url);
  ws.onopen = () => { opened = true; };
  ws.onclose = (event) => resolve({opened, code: event.code});
  setTimeout(() => resolve({opened, code: 'timeout'}), 10000);
})
"""

FETCH_JS: str = """
async ([url, method, body]) => {
  try {
    const init = {method, credentials: 'include'};
    if (body !== null) {
      init.headers = {'Content-Type': 'application/json'};
      init.body = body;
    }
    const resp = await fetch(url, init);
    return 'status ' + resp.status;
  } catch (err) {
    return 'blocked';
  }
}
"""

FOREIGN_PAGE_HTML: str = "<!doctype html><title>foreign</title><p>foreign page</p>"


async def start_foreign_server() -> tuple[Any, asyncio.Task[None]]:
    """Serve one static page on 0.0.0.0:18770, standing in for an attacker's site."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    foreign = FastAPI()

    @foreign.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(FOREIGN_PAGE_HTML)

    config = uvicorn.Config(foreign, host="0.0.0.0", port=FOREIGN_PORT, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline and not task.done():
        await asyncio.sleep(0.1)
    if not server.started:
        raise RuntimeError("foreign page server did not start")
    return server, task


async def stop_foreign_server(foreign: tuple[Any, asyncio.Task[None]] | None) -> None:
    """Stop the extra static server."""
    if foreign is None:
        return
    server, task = foreign
    server.should_exit = True
    try:
        await asyncio.wait_for(task, 10)
    except (TimeoutError, asyncio.CancelledError):
        task.cancel()


async def drive_browser(lan_ip: str, username: str, password: str, scratch: Path) -> None:
    """Run the browser scenarios L1-L7 against the LAN address."""
    from playwright.async_api import async_playwright

    lan_ui = f"http://{lan_ip}:{UI_PORT}"
    lan_agent = f"http://{lan_ip}:{AGENT_PORT}"
    foreign_url = f"http://{lan_ip}:{FOREIGN_PORT}/"
    foreign_host_url = f"http://localhost:{FOREIGN_PORT}/"
    page_errors: list[str] = []
    ws_urls: list[str] = []
    foreign: tuple[Any, asyncio.Task[None]] | None = None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.add_init_script(prov.TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("websocket", lambda ws: ws_urls.append(ws.url))
        try:
            foreign = await start_foreign_server()

            # L1
            await page.goto(f"{lan_ui}/static/login.html")
            await page.fill("#login-username", username)
            await page.fill("#login-password", password)
            await page.click("#login-form button[type=submit]")
            await page.wait_for_url("**/static/index.html", timeout=15000)
            await page.wait_for_function(
                "document.querySelector('#model-select') !== null", timeout=15000,
            )
            secure = await page.evaluate("window.isSecureContext")
            hostname = await page.evaluate("window.location.hostname")
            report(
                "L1 login over the LAN address in a non-secure context",
                secure is False and hostname == lan_ip,
                f"isSecureContext={secure}, hostname={hostname}",
            )
            await prov.shot(page, scratch, "L1")

            # L2
            await page.click("#btn-settings")
            await page.wait_for_function(
                "document.querySelector('#network-status').textContent"
                " === 'Открыт в локальной сети'",
                timeout=15000,
            )
            texts = await page.locator("#network-address-list button").all_inner_texts()
            api_addresses = await page.evaluate(
                "async (url) => (await (await fetch(url, {credentials: 'include'})).json())"
                ".addresses",
                f"{lan_agent}/api/v1/network",
            )
            report(
                "L2 'Сеть' section shows the LAN address list from the API",
                lan_ui in texts and texts[-1] == f"http://127.0.0.1:{UI_PORT}"
                and texts == api_addresses,
                f"buttons={texts}",
            )
            await prov.shot(page, scratch, "L2")

            # L3
            await page.locator("#network-address-list button", has_text=lan_ui).first.click()
            await page.wait_for_function(
                "(text) => window.__toasts.includes(text)",
                arg=f"Скопировано: {lan_ui}",
                timeout=10000,
            )
            report(
                "L3 clicking an address copies it over plain HTTP",
                not page_errors,
                f"toast ok, pageerrors={len(page_errors)}",
            )

            # L4
            await page.click("#btn-llm-provider-add")
            await prov.fill_form(
                page, "Stub", f"http://{prov.HOST}:{prov.STUB_PORT}/v1/", "STUB_KEY",
            )
            await page.click("#btn-llm-provider-save")
            await prov.wait_card(page, "Stub", "доступен")
            await page.click("#btn-close-settings")
            await page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#model-select optgroup'))"
                ".some(g => g.label === 'Stub')",
                timeout=30000,
            )
            chats_before = await page.evaluate("state.chats.length")
            await page.click("#btn-new-chat")
            await page.wait_for_function(
                "(before) => state.chats.length > before"
                " && state.currentChatId === state.chats[0].id"
                " && state.ws && state.ws.readyState === WebSocket.OPEN"
                " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
                arg=chats_before,
                timeout=15000,
            )
            stub_value = await page.evaluate(
                "() => Array.from(document.querySelectorAll('#model-select option'))"
                ".find(o => o.textContent === 'Stub · stub-model').value",
            )
            await page.select_option("#model-select", stub_value)
            await page.fill("#message-input", "Привет по локальной сети")
            await page.click("#btn-send")
            bubble = page.locator(
                "#messages [data-message-id] .message-content", has_text=prov.STUB_ANSWER,
            )
            await bubble.first.wait_for(timeout=60000)
            chat_id = await page.evaluate("state.currentChatId")
            lan_ws = [u for u in ws_urls if u.startswith(f"ws://{lan_ip}:{AGENT_PORT}/ws/chat/")]
            report(
                "L4 streamed reply over a WebSocket on the LAN address",
                bool(lan_ws),
                f"answer={prov.STUB_ANSWER!r}, ws={lan_ws[:1]}",
            )
            await prov.shot(page, scratch, "L4")
            app_errors = list(page_errors)

            # L5
            await page.goto(foreign_url)
            read = await page.evaluate(FETCH_JS, [f"{lan_agent}/api/v1/chats", "GET", None])
            write = await page.evaluate(
                FETCH_JS,
                [
                    f"{lan_agent}/api/v1/llm-providers",
                    "POST",
                    json.dumps({
                        "name": "foreign-created",
                        "base_url": f"http://{prov.HOST}:{prov.STUB_PORT}",
                        "api_key_env": "STUB_KEY",
                        "enabled": False,
                    }),
                ],
            )
            events_ws = await page.evaluate(WS_PROBE_JS, f"ws://{lan_ip}:{AGENT_PORT}/ws/events")
            chat_ws = await page.evaluate(
                WS_PROBE_JS, f"ws://{lan_ip}:{AGENT_PORT}/ws/chat/{chat_id}",
            )
            await page.goto(f"{lan_ui}/static/index.html")
            names = await page.evaluate(
                "async (url) => (await (await fetch(url, {credentials: 'include'})).json())"
                ".map(p => p.name)",
                f"{lan_agent}/api/v1/llm-providers",
            )
            agent_log = (scratch / "app" / "logs" / "agent.log").read_text(
                encoding="utf-8", errors="replace",
            )
            logged = (
                "ws_origin_rejected" in agent_log and f"http://{lan_ip}:{FOREIGN_PORT}" in agent_log
            )
            report(
                "L5 foreign page with the session cookie is blocked on REST and WebSockets",
                read == "blocked" and write == "blocked" and "foreign-created" not in names
                and not events_ws["opened"] and not chat_ws["opened"] and logged,
                f"read={read}, write={write}, events={events_ws}, chat={chat_ws}, logged={logged}",
            )

            # L6
            await page.goto(foreign_host_url)
            host_ws = await page.evaluate(WS_PROBE_JS, f"ws://{lan_ip}:{AGENT_PORT}/ws/events")
            report(
                "L6 page on a foreign host cannot open the events WebSocket",
                not host_ws["opened"],
                f"result={host_ws}",
            )

            # L7
            report(
                "L7 no browser errors on the app page",
                not app_errors,
                f"pageerrors={app_errors}",
            )
        except Exception as exc:
            report("browser scenario completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
            try:
                await prov.shot(page, scratch, "failure")
            except Exception as inner:  # noqa: BLE001 - evidence capture is best effort
                print(f"note: failure capture failed: {type(inner).__name__}")
        finally:
            await browser.close()
            await stop_foreign_server(foreign)


async def run_e2e(addresses: list[str]) -> None:
    """Start the isolated copy in LAN mode, run the checks and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="lan_e2e_"))
    db_path = scratch / "lan30.db"
    username, password = "lan30", secrets.token_urlsafe(12)
    app_proc: asyncio.subprocess.Process | None = None
    stub: Any = None
    log_file = None
    try:
        copy_dir = prov.prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        # shared.config was already imported for the address discovery, so repoint it explicitly
        # before shared.database is first imported by the seeding helper.
        from shared.config import settings as app_settings

        app_settings.DB_PATH = str(db_path)
        await prov.seed_scratch_user(db_path, username, password)
        report("scratch database is isolated from app.db", True, str(db_path))
        stub = await prov.start_stub()

        env = {
            **os.environ,
            "BIND_HOST": "0.0.0.0",
            "DB_PATH": str(db_path),
            "UI_PORT": str(UI_PORT),
            "AGENT_PORT": str(AGENT_PORT),
            "LM_STUDIO_BASE_URL": f"http://{prov.HOST}:{prov.CLOSED_LM_PORT}",
            "DEEPSEEK_API_KEY": "",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(
                [str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])],
            ),
        }
        log_file = open(scratch / "app.log", "wb")
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable, str(copy_dir / "run.py"),
            cwd=str(copy_dir), env=env, stdout=log_file, stderr=log_file,
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await wait_for_agent(client)
            report("isolated app instance is up in LAN mode (UI :18000 + Agent :18001)", ready)
            lan_ip = await pick_lan_ip(client, addresses) if ready else None
        if ready:
            log_file.flush()
            report("agent reachable through a LAN address", lan_ip is not None,
                   f"candidates={addresses}, chosen={lan_ip}")
        if lan_ip is not None:
            await network_checks(lan_ip, username, password, scratch, copy_dir)
            await asyncio.wait_for(
                drive_browser(lan_ip, username, password, scratch), PLAYWRIGHT_TIMEOUT,
            )
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await prov.teardown(app_proc, stub)
        if log_file is not None:
            log_file.close()
        (scratch / "results.json").write_text(
            json.dumps(
                [{"check": c, "passed": p, "detail": d} for c, p, d in checks],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"scratch kept for inspection: {scratch}")


def main() -> None:
    """Run preflight, then the isolated LAN scenario, and exit with the result code."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed (pip install playwright && playwright install chromium).")
        sys.exit(EXIT_NO_PLAYWRIGHT)

    blocked, addresses = preflight()
    if blocked is not None:
        sys.exit(blocked)

    asyncio.run(run_e2e(addresses))
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
