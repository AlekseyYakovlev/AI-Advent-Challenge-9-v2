"""API keys and plain-HTTP chat check on an isolated copy of the app.

Usage: python scripts/e2e_api_keys_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it in loopback mode with a scratch database, a stub OpenAI-compatible provider on
127.0.0.1:18766 and a closed LM Studio port. Headless Chromium creates an API key in Settings, a
cookie-less HTTP client holds a conversation with that key, the open browser must show the
HTTP-originated exchange without a reload, and revoking the key must make it return 401. One
PASS/FAIL line is printed per check. The default ports of a running app, the repository .env
and app.db are never touched; only processes started by this script are stopped, and the scratch
directory (results.json, app.log, screenshots) is kept and its path printed.

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
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_llm_providers_playwright as prov  # noqa: E402

UI_URL: str = prov.UI_URL
AGENT_URL: str = prov.AGENT_URL
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4
PLAYWRIGHT_TIMEOUT: float = 300.0
STEP_TIMEOUT_MS: int = prov.STEP_TIMEOUT_MS
HTTP_TURN_TIMEOUT: float = 60.0
KEY_NAME: str = "e2e-скрипт"
KEY_LENGTH: int = 47

report = prov.report
checks = prov.checks


def preflight() -> int | None:
    """Return an exit code when the run must not start, else None."""
    return prov.preflight()


async def register_stub_provider(username: str, password: str) -> int:
    """Log in with a cookie client and register the stub provider; return its id."""
    headers = {"Origin": UI_URL}
    async with httpx.AsyncClient(timeout=15.0) as client:
        login = await client.post(
            f"{AGENT_URL}/api/v1/auth/login",
            json={"username": username, "password": password},
            headers=headers,
        )
        login.raise_for_status()
        created = await client.post(
            f"{AGENT_URL}/api/v1/llm-providers",
            json={
                "name": "Stub",
                "base_url": f"http://{prov.HOST}:{prov.STUB_PORT}/v1/",
                "api_key_env": "STUB_KEY",
                "enabled": True,
            },
            headers=headers,
        )
        created.raise_for_status()
        return int(created.json()["id"])


def bearer(key: str) -> dict[str, str]:
    """Headers of a key-only request."""
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def http_checks(key: str, provider_id: int, username: str) -> dict[str, Any]:
    """Run H1-H5 with a client that has no cookies; return ids needed by later steps."""
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=HTTP_TURN_TIMEOUT) as client:
        assert not client.cookies

        me = await client.get(f"{AGENT_URL}/api/v1/auth/me", headers=bearer(key))
        kb = await client.get(f"{AGENT_URL}/api/v1/kb", headers=bearer(key))
        mcp = await client.get(f"{AGENT_URL}/api/v1/mcp/servers", headers=bearer(key))
        me_body: dict[str, Any] = me.json() if me.status_code == 200 else {}
        report(
            "H1 key-only GET auth/me, kb and mcp/servers",
            me_body.get("username") == username and kb.status_code == 200
            and mcp.status_code == 200,
            f"me={me.status_code}:{me_body.get('username')}, kb={kb.status_code}, "
            f"mcp={mcp.status_code}",
        )

        forbidden = await client.post(
            f"{AGENT_URL}/api/v1/api-keys", json={"name": "nested"}, headers=bearer(key),
        )
        report(
            "H2 api-keys route is cookie-only (403 for a key)",
            forbidden.status_code == 403, f"status={forbidden.status_code}",
        )

        chat = await client.post(
            f"{AGENT_URL}/api/v1/chats", json={"title": "http-chat"}, headers=bearer(key),
        )
        chat_id = chat.json().get("id") if chat.status_code in (200, 201) else None
        reply: dict[str, Any] = {}
        status = 0
        if chat_id is not None:
            resp = await client.post(
                f"{AGENT_URL}/api/v1/chats/{chat_id}/messages",
                json={"content": "Привет по HTTP", "model": "stub-model",
                      "provider_id": provider_id},
                headers=bearer(key),
            )
            status = resp.status_code
            reply = resp.json() if status == 200 else {"detail": resp.text[:200]}
        report(
            "H3 key creates a chat and gets one JSON reply with stats",
            chat_id is not None and status == 200 and reply.get("content") == prov.STUB_ANSWER
            and isinstance(reply.get("user_message_id"), int)
            and isinstance(reply.get("assistant_message_id"), int)
            and isinstance(reply.get("stats"), dict),
            f"chat={chat.status_code}, messages={status}, content={reply.get('content')!r}",
        )
        out["chat_id"] = chat_id

        tree_len = -1
        if chat_id is not None:
            tree = await client.get(
                f"{AGENT_URL}/api/v1/chats/{chat_id}/tree", headers=bearer(key),
            )
            tree_len = len(tree.json()) if tree.status_code == 200 else -1
        report("H4 tree with the key returns the two messages", tree_len == 2,
               f"messages={tree_len}")

        wrong = "aia_" + secrets.token_urlsafe(32)[:43]
        denied = await client.get(f"{AGENT_URL}/api/v1/chats", headers=bearer(wrong))
        report("H5 wrong key gets 401", denied.status_code == 401,
               f"status={denied.status_code}")
    return out


async def post_http_message(key: str, chat_id: int, provider_id: int, text: str) -> int:
    """Send one key-authenticated message and return the status code."""
    async with httpx.AsyncClient(timeout=HTTP_TURN_TIMEOUT) as client:
        resp = await client.post(
            f"{AGENT_URL}/api/v1/chats/{chat_id}/messages",
            json={"content": text, "model": "stub-model", "provider_id": provider_id},
            headers=bearer(key),
        )
    return resp.status_code


BUBBLE_SIGNATURE_JS: str = """
() => Array.from(document.querySelectorAll('#messages [data-message-id]')).map((w) => {
  const bubble = w.firstElementChild;
  const content = bubble ? bubble.querySelector('.message-content') : null;
  return [w.className, bubble ? bubble.className : '', content ? content.className : ''];
})
"""


async def key_checks_before_http(page: Any, scratch: Path, page_requests: list[str]) -> str:
    """Run K1-K6 in the open Settings modal and return the created key."""
    await page.click("#btn-settings")
    await page.wait_for_function(
        "!document.querySelector('#api-key-empty').classList.contains('hidden')",
        timeout=STEP_TIMEOUT_MS,
    )
    section = await page.inner_text("#api-keys-section")
    empty = await page.inner_text("#api-key-empty")
    report(
        "K1 API keys section: heading, Authorization: Bearer explainer, empty state",
        "API ключи" in section and "Authorization: Bearer" in section
        and "Ключей пока нет" in empty,
        f"empty={empty.strip()!r}",
    )
    await prov.shot(page, scratch, "K1")

    await page.click("#btn-api-key-add")
    before = len([r for r in page_requests if r == "POST /api/v1/api-keys"])
    await page.fill("#api-key-name", "   ")
    await page.click("#btn-api-key-save")
    error = await page.inner_text("#api-key-form-error")
    after = len([r for r in page_requests if r == "POST /api/v1/api-keys"])
    report(
        "K2 empty name shows the error and sends no request",
        "Введите название ключа" in error and before == after,
        f"error={error!r}, requests={after - before}",
    )

    await page.fill("#api-key-name", KEY_NAME)
    await page.click("#btn-api-key-save")
    await page.wait_for_function(
        "!document.querySelector('#api-key-modal').classList.contains('hidden')",
        timeout=STEP_TIMEOUT_MS,
    )
    key = (await page.inner_text("#api-key-value")).strip()
    curl = await page.inner_text("#api-key-curl")
    warning_visible = await page.get_by_text("Скопируйте ключ сейчас").first.is_visible()
    report(
        "K3 modal shows the one-time key and a curl example for the Agent address",
        key.startswith("aia_") and len(key) == KEY_LENGTH
        and "/api/v1/chats/{chat_id}/messages" in curl
        and f"Authorization: Bearer {key}" in curl
        and str(prov.AGENT_PORT) in curl and warning_visible,
        f"key_len={len(key)}, warning_visible={warning_visible}",
    )
    await prov.shot(page, scratch, "K3")

    await page.evaluate("window.__toasts = []")
    await page.click("#btn-api-key-copy")
    await page.wait_for_function(
        "(t) => window.__toasts.some(x => x.includes(t))", arg="Ключ скопирован", timeout=10000,
    )
    await page.click("#btn-api-key-curl-copy")
    await page.wait_for_function(
        "(t) => window.__toasts.some(x => x.includes(t))",
        arg="Пример запроса скопирован", timeout=10000,
    )
    report("K4 copy buttons show their toasts", True, "both toasts seen")

    await page.keyboard.press("Escape")
    still_escape = await page.is_visible("#api-key-modal")
    await page.mouse.click(5, 5)
    still_backdrop = await page.is_visible("#api-key-modal")
    await page.click("#btn-close-api-key")
    closed = not await page.is_visible("#api-key-modal")
    value_after = await page.inner_text("#api-key-value")
    curl_after = await page.inner_text("#api-key-curl")
    html = await page.content()
    report(
        "K5 modal ignores Escape and backdrop, closes by x, and no trace of the key remains",
        still_escape and still_backdrop and closed and value_after == "" and curl_after == ""
        and key not in html,
        f"escape_kept={still_escape}, backdrop_kept={still_backdrop}, closed={closed}",
    )

    rows = page.locator("#api-key-list > div")
    row_count = await rows.count()
    row_text = await rows.first.inner_text() if row_count else ""
    report(
        "K6 list shows name, prefix and 'использован: никогда'",
        row_count == 1 and KEY_NAME in row_text and f"{key[:8]}…" in row_text
        and "использован: никогда" in row_text,
        f"rows={row_count}, text={row_text.replace(chr(10), ' ')!r}",
    )
    await prov.shot(page, scratch, "K6")
    return key


async def live_checks(
    page: Any, scratch: Path, key: str, chat_id: int, provider_id: int,
) -> None:
    """Run L1-L3 without reloading the page."""
    await page.click("#btn-close-settings")
    await page.wait_for_function(
        "(id) => document.querySelector(`#chat-list [data-chat-id=\"${id}\"]`) !== null",
        arg=chat_id, timeout=STEP_TIMEOUT_MS,
    )
    report("L1 chat created over HTTP appears in the open browser without a reload", True,
           f"chat_id={chat_id}")

    await page.click(f'#chat-list [data-chat-id="{chat_id}"]')
    await page.wait_for_function(
        "() => document.querySelectorAll('#messages [data-message-id]').length === 2",
        timeout=STEP_TIMEOUT_MS,
    )
    status = await post_http_message(key, chat_id, provider_id, "Второе сообщение по HTTP")
    live_ok = False
    try:
        await page.wait_for_function(
            "(answer) => { const w = document.querySelectorAll('#messages [data-message-id]');"
            " return w.length === 4 && w[3].textContent.includes(answer); }",
            arg=prov.STUB_ANSWER, timeout=STEP_TIMEOUT_MS,
        )
        live_ok = True
    except Exception as exc:  # noqa: BLE001 - reported as a failed check below
        print(f"note: live refresh wait ended with {type(exc).__name__}")
    http_signature = await page.evaluate(BUBBLE_SIGNATURE_JS)
    badge = await page.evaluate(
        "() => Array.from(document.querySelectorAll('#messages *'))"
        ".some(e => e.children.length === 0 && e.textContent.trim() === 'API')",
    )
    await prov.shot(page, scratch, "L2-http")

    # typed pair in another chat for the class comparison
    chats_before = await page.evaluate("state.chats.length")
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "(before) => state.chats.length > before && state.currentChatId === state.chats[0].id"
        " && state.ws && state.ws.readyState === WebSocket.OPEN"
        " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
        arg=chats_before, timeout=15000,
    )
    stub_value = await page.evaluate(
        "() => Array.from(document.querySelectorAll('#model-select option'))"
        ".find(o => o.textContent === 'Stub · stub-model').value",
    )
    await page.select_option("#model-select", stub_value)
    await page.fill("#message-input", "Привет из интерфейса")
    await page.click("#btn-send")
    await page.locator(
        "#messages [data-message-id] .message-content", has_text=prov.STUB_ANSWER,
    ).first.wait_for(timeout=60000)
    await page.wait_for_function(
        "() => !state.isStreaming && document.querySelectorAll('#messages [data-message-id]')"
        ".length === 2",
        timeout=30000,
    )
    typed_signature = await page.evaluate(BUBBLE_SIGNATURE_JS)
    same_classes = (
        len(http_signature) == 4 and len(typed_signature) == 2
        and http_signature[0] == typed_signature[0] == http_signature[2]
        and http_signature[1] == typed_signature[1] == http_signature[3]
    )
    report(
        "L2 second HTTP turn shows up live as four typed-looking messages, no API badge",
        status == 200 and live_ok and same_classes and not badge,
        f"http_status={status}, live={live_ok}, same_classes={same_classes}, badge={badge}",
    )

    # back to the HTTP chat: the input must not have been locked by the HTTP turn
    await page.click(f'#chat-list [data-chat-id="{chat_id}"]')
    await page.wait_for_function(
        "() => document.querySelectorAll('#messages [data-message-id]').length === 4",
        timeout=STEP_TIMEOUT_MS,
    )
    input_enabled = await page.is_enabled("#message-input")
    send_enabled = await page.is_enabled("#btn-send")
    report(
        "L3 message input and send button are enabled after the HTTP turn",
        input_enabled and send_enabled, f"input={input_enabled}, send={send_enabled}",
    )


async def revoke_checks(
    page: Any, key: str, chat_id: int, provider_id: int, page_errors: list[str],
) -> None:
    """Run K7, R1, R2 and Z."""
    await page.click("#btn-settings")
    await page.wait_for_function(
        "document.querySelectorAll('#api-key-list > div').length === 1", timeout=STEP_TIMEOUT_MS,
    )
    row_text = await page.locator("#api-key-list > div").first.inner_text()
    report(
        "K7 reopened Settings shows a last-used date",
        "использован: " in row_text and "использован: никогда" not in row_text,
        f"text={row_text.replace(chr(10), ' ')!r}",
    )

    revoke = page.locator("#api-key-list > div").first.get_by_role("button", name="Отозвать")
    page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
    await revoke.click()
    await asyncio.sleep(0.5)
    kept = await page.locator("#api-key-list > div").count() == 1
    await page.evaluate("window.__toasts = []")
    page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
    await revoke.click()
    await page.wait_for_function(
        "(t) => window.__toasts.some(x => x.includes(t))", arg="отозван", timeout=10000,
    )
    await page.wait_for_function(
        "!document.querySelector('#api-key-empty').classList.contains('hidden')",
        timeout=STEP_TIMEOUT_MS,
    )
    report("R1 dismissed confirm keeps the row, accepted confirm revokes it", kept,
           f"row_kept_after_dismiss={kept}, toast and empty state seen")

    async with httpx.AsyncClient(timeout=HTTP_TURN_TIMEOUT) as client:
        listing = await client.get(f"{AGENT_URL}/api/v1/chats", headers=bearer(key))
        posted = await client.post(
            f"{AGENT_URL}/api/v1/chats/{chat_id}/messages",
            json={"content": "после отзыва", "model": "stub-model", "provider_id": provider_id},
            headers=bearer(key),
        )
    report(
        "R2 revoked key gets 401 on GET chats and POST messages",
        listing.status_code == 401 and posted.status_code == 401,
        f"chats={listing.status_code}, messages={posted.status_code}",
    )
    report("Z no uncaught page errors during the run", not page_errors, f"errors={page_errors}")


async def drive_browser(
    username: str, password: str, scratch: Path, provider_id: int,
) -> None:
    """Run the browser and HTTP scenarios K1-K7, H1-H5, L1-L3, R1-R2 and Z."""
    from playwright.async_api import async_playwright

    page_errors: list[str] = []
    page_requests: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=UI_URL)
        page = await context.new_page()
        await page.add_init_script(prov.TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "request",
            lambda req: page_requests.append(f"{req.method} {re.sub(r'^https?://[^/]+', '', req.url)}"),
        )
        try:
            await page.goto(f"{UI_URL}/static/login.html")
            await page.fill("#login-username", username)
            await page.fill("#login-password", password)
            await page.click("#login-form button[type=submit]")
            await page.wait_for_url("**/static/index.html", timeout=15000)
            await page.wait_for_function(
                "document.querySelector('#model-select') !== null", timeout=15000,
            )
            await asyncio.sleep(1.0)  # let the events socket connect

            key = await key_checks_before_http(page, scratch, page_requests)
            http = await http_checks(key, provider_id, username)
            chat_id = http.get("chat_id")
            if chat_id is None:
                report("live checks need the chat from H3", False, "H3 did not create a chat")
                return
            await live_checks(page, scratch, key, int(chat_id), provider_id)
            await revoke_checks(page, key, int(chat_id), provider_id, page_errors)
        except Exception as exc:
            report("browser scenario completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
            try:
                await prov.shot(page, scratch, "failure")
            except Exception as inner:  # noqa: BLE001 - evidence capture is best effort
                print(f"note: failure capture failed: {type(inner).__name__}")
        finally:
            await browser.close()


async def run_e2e() -> None:
    """Start the isolated copy, run the scenario and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="apikeys_e2e_"))
    db_path = scratch / "keys30.db"
    username, password = "keys30", secrets.token_urlsafe(12)
    app_proc: asyncio.subprocess.Process | None = None
    stub: Any = None
    log_file = None
    try:
        copy_dir = prov.prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        await prov.seed_scratch_user(db_path, username, password)
        report("scratch database is isolated from app.db", True, str(db_path))
        stub = await prov.start_stub()

        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "UI_PORT": str(prov.UI_PORT),
            "AGENT_PORT": str(prov.AGENT_PORT),
            "LM_STUDIO_BASE_URL": f"http://{prov.HOST}:{prov.CLOSED_LM_PORT}",
            "DEEPSEEK_API_KEY": "",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(
                [str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])],
            ),
        }
        env.pop("BIND_HOST", None)
        log_file = open(scratch / "app.log", "wb")
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable, str(copy_dir / "run.py"),
            cwd=str(copy_dir), env=env, stdout=log_file, stderr=log_file,
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await prov.wait_for_app(client)
        report("isolated app instance is up (UI :18000 + Agent :18001)", ready)
        if ready:
            provider_id = await register_stub_provider(username, password)
            report("stub provider registered for the scratch user", True,
                   f"provider_id={provider_id}")
            await asyncio.wait_for(
                drive_browser(username, password, scratch, provider_id), PLAYWRIGHT_TIMEOUT,
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
    """Run preflight, then the isolated end-to-end scenario, and exit with the result code."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed (pip install playwright && playwright install chromium).")
        sys.exit(EXIT_NO_PLAYWRIGHT)

    blocked = preflight()
    if blocked is not None:
        sys.exit(blocked)

    asyncio.run(run_e2e())
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
