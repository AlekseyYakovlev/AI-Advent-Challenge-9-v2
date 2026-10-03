"""Browser check of the LLM providers section on an isolated copy of the app.

Usage: python scripts/e2e_llm_providers_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database, a stub OpenAI-compatible provider on 127.0.0.1:18766 and a
closed LM Studio port, drives it with headless Chromium and prints one PASS/FAIL line per check.
The ports 8000/8001 of a running app are never touched.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy),
4 Playwright not installed.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil
from dotenv import dotenv_values

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

HOST: str = "127.0.0.1"
UI_PORT: int = 18000
AGENT_PORT: int = 18001
STUB_PORT: int = 18766
CLOSED_LM_PORT: int = 18767
UI_URL: str = f"http://localhost:{UI_PORT}"
AGENT_URL: str = f"http://localhost:{AGENT_PORT}"
STUB_KEY_VALUE: str = "stub-secret-123"
BAD_KEY_VALUE: str = "wrong-key"
STUB_ANSWER: str = "Ответ заглушки"
STUB_TITLE: str = "Заголовок заглушки"
DEEPSEEK_PLACEHOLDER: str = "your_deepseek_api_key_here"
STARTUP_TIMEOUT: float = 60.0
PLAYWRIGHT_TIMEOUT: float = 240.0
SUPERVISOR_STOP_TIMEOUT: float = 10.0
STEP_TIMEOUT_MS: int = 20000
COPY_IGNORE: tuple[str, ...] = (
    ".git", ".claude", ".planning", ".env", "scripts", "*.db", "*.db-*", "tests", "docs", "logs",
    "__pycache__", ".pytest_cache", "*.pyc", "test_*.db*",
)

EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

checks: list[tuple[str, bool, str]] = []
stub_auth_log: list[tuple[str, str]] = []
api_bodies: list[str] = []
dialog_texts: list[str] = []
secret_values: list[str] = [STUB_KEY_VALUE, BAD_KEY_VALUE]


def report(label: str, passed: bool, detail: str = "") -> bool:
    """Record and print one check result."""
    checks.append((label, passed, detail))
    suffix = f" - {detail}" if detail else ""
    print(f"{'PASS' if passed else 'FAIL'}: {label}{suffix}")
    return passed


def port_is_free(port: int) -> bool:
    """Return True when nothing listens on the port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((HOST, port))
    except OSError:
        return False
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                return False
    except psutil.AccessDenied:
        pass
    return True


def preflight() -> int | None:
    """Return an exit code when the run must not start, else None."""
    busy = [p for p in (UI_PORT, AGENT_PORT, STUB_PORT, CLOSED_LM_PORT) if not port_is_free(p)]
    if busy:
        print(f"BLOCKED: port(s) {busy} are in use; the isolated copy needs them free.")
        return EXIT_BLOCKED
    return None


def real_deepseek_key() -> str | None:
    """Return the repository's real DeepSeek key, or None for a missing or placeholder value."""
    key = dotenv_values(REPO_ROOT / ".env").get("DEEPSEEK_API_KEY")
    if not key or key == DEEPSEEK_PLACEHOLDER:
        return None
    return key


def build_stub_app() -> Any:
    """Build the stub OpenAI-compatible provider that requires a bearer key."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    stub = FastAPI()

    @stub.get("/v1/models")
    async def models(request: Request) -> Any:
        auth = request.headers.get("authorization", "")
        stub_auth_log.append(("models", auth))
        if auth != f"Bearer {STUB_KEY_VALUE}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return {"data": [{"id": "stub-model"}]}

    @stub.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        stub_auth_log.append(("chat", request.headers.get("authorization", "")))
        body: dict[str, Any] = await request.json()
        if body.get("stream") is not True:
            text = json.dumps(body.get("messages", []), ensure_ascii=False)
            content = STUB_TITLE if "<user_message>" in text else "{}"
            return JSONResponse(
                {
                    "id": "stub",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        },
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        def stream() -> Any:
            chunk = {"choices": [{"delta": {"content": STUB_ANSWER}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return stub


async def start_stub() -> tuple[Any, asyncio.Task[None]]:
    """Run the stub provider in this event loop and wait until it accepts connections."""
    import uvicorn

    config = uvicorn.Config(build_stub_app(), host=HOST, port=STUB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline and not task.done():
        await asyncio.sleep(0.1)
    if not server.started:
        raise RuntimeError("stub provider did not start")
    return server, task


def prepare_copy(scratch: Path) -> Path:
    """Copy the working tree and repoint only the copy to the isolated ports."""
    target = scratch / "app"
    shutil.copytree(REPO_ROOT, target, ignore=shutil.ignore_patterns(*COPY_IGNORE))

    def patch(rel: str, old: str, new: str) -> None:
        path = target / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise RuntimeError(f"cannot patch {rel}: {old!r} not found")
        path.write_text(text.replace(old, new), encoding="utf-8")

    patch("ui/static/app.js", "const AGENT_PORT = 8001;", f"const AGENT_PORT = {AGENT_PORT};")
    patch("ui/static/login.html", "const AGENT_PORT = 8001;", f"const AGENT_PORT = {AGENT_PORT};")
    patch("agent/state.py", '"http://localhost:8000"', f'"http://localhost:{UI_PORT}"')
    patch("agent/state.py", '"http://127.0.0.1:8000"', f'"http://127.0.0.1:{UI_PORT}"')

    leftovers: list[str] = []
    for path in target.rglob("*"):
        if path.suffix not in (".py", ".js", ".html") or not path.is_file():
            continue
        rel = path.relative_to(target).as_posix()
        if rel in ("shared/config.py", "agent/ws.py") or "/vendor/" in rel:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(?<![\d.])800[01](?!\d)", line):
                leftovers.append(f"{rel}:{number}")
    if leftovers:
        raise RuntimeError(f"literal 8000/8001 left in the copy: {leftovers}")

    (target / ".env").write_text(
        f"STUB_KEY={STUB_KEY_VALUE}\nSTUB_BAD={BAD_KEY_VALUE}\n", encoding="utf-8",
    )
    return target


async def seed_scratch_user(db_path: Path, username: str, password: str) -> None:
    """Create the scratch database and its only user; never touches the repo's app.db."""
    os.environ["DB_PATH"] = str(db_path)
    from shared.auth import hash_password
    from shared.config import settings
    from shared.database import async_session_factory, engine, init_db
    from shared.models import User

    if Path(settings.DB_PATH).resolve() != db_path.resolve():
        raise RuntimeError("scratch DB_PATH was not applied")
    if db_path.resolve() == (REPO_ROOT / "app.db").resolve():
        raise RuntimeError("refusing to use the repository app.db")

    await init_db()
    async with async_session_factory() as session:
        session.add(User(username=username, password_hash=hash_password(password)))
        await session.commit()
    await engine.dispose()


async def wait_for_app(client: httpx.AsyncClient) -> bool:
    """Poll the UI root and the Agent health endpoint until both answer 200."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            ui = await client.get(f"{UI_URL}/")
            agent = await client.get(f"{AGENT_URL}/health")
            if ui.status_code == 200 and agent.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    return False


TOAST_RECORDER_JS: str = """
window.__toasts = [];
new MutationObserver((records) => {
  for (const r of records) for (const n of r.addedNodes) {
    if (n.nodeType === 1 && n.parentElement && n.parentElement.id === 'toast-container') {
      window.__toasts.push(n.textContent);
    }
  }
}).observe(document, {childList: true, subtree: true});
"""


async def card_text(page: Any, name: str) -> str:
    """Return the text of the provider card whose title is exactly name ('' when absent)."""
    cards = page.locator("#llm-provider-list > div").filter(
        has=page.locator("span.font-semibold", has_text=re.compile(f"^{re.escape(name)}$")),
    )
    if await cards.count() == 0:
        return ""
    return await cards.first.inner_text()


async def wait_card(page: Any, name: str, needle: str, timeout: float = 20.0) -> str:
    """Wait until the card text contains needle; return the last observed text."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        text = await card_text(page, name)
        if needle in text:
            return text
        await asyncio.sleep(0.3)
    return text


def card_button(page: Any, name: str, label: str) -> Any:
    """Locate a button on the provider card with the given title."""
    card = page.locator("#llm-provider-list > div").filter(
        has=page.locator("span.font-semibold", has_text=re.compile(f"^{re.escape(name)}$")),
    )
    return card.first.get_by_role("button", name=label, exact=True)


async def option_labels(page: Any) -> list[tuple[str, str]]:
    """Return (optgroup label, option text) pairs of the model picker."""
    return await page.evaluate(
        "() => Array.from(document.querySelectorAll('#model-select option'))"
        ".map(o => [o.parentElement.label || '', o.textContent])",
    )


async def fill_form(page: Any, name: str, url: str, key_env: str, enabled: bool = True) -> None:
    """Fill the provider form fields."""
    await page.fill("#llm-provider-name", name)
    await page.fill("#llm-provider-base-url", url)
    await page.fill("#llm-provider-key-env", key_env)
    await page.set_checked("#llm-provider-enabled", enabled)


async def shot(page: Any, scratch: Path, name: str) -> None:
    """Save a screenshot of the current page state."""
    await page.screenshot(path=str(scratch / f"{name}.png"))


async def drive_browser(username: str, password: str, scratch: Path, deepseek: bool) -> None:
    """Run the browser scenarios S1-S11."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.add_init_script(TOAST_RECORDER_JS)

        async def on_response(resp: Any) -> None:
            if f":{AGENT_PORT}/api/v1/llm-providers" in resp.url:
                try:
                    api_bodies.append(await resp.text())
                except Exception as exc:  # noqa: BLE001 - body of a closed page may be gone
                    api_bodies.append(f"<unreadable {type(exc).__name__}>")

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        async def on_dialog(dialog: Any) -> None:
            dialog_texts.append(dialog.message)
            await dialog.accept()

        page.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
        page.set_default_timeout(STEP_TIMEOUT_MS)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))

        try:
            await page.goto(f"{UI_URL}/static/login.html")
            await page.fill("#login-username", username)
            await page.fill("#login-password", password)
            await page.click("#login-form button[type=submit]")
            await page.wait_for_url("**/static/index.html", timeout=15000)
            await page.wait_for_function(
                "document.querySelector('#model-select') !== null", timeout=15000,
            )

            # S1
            await page.click("#btn-settings")
            heading = page.locator("#llm-providers-section h3")
            await heading.wait_for()
            lm_text = await wait_card(page, "LM Studio", "LM Studio")
            ds_text = await card_text(page, "DeepSeek")
            report(
                "S1 settings shows 'Провайдеры LLM' with the seeded providers",
                (await heading.inner_text()) == "Провайдеры LLM"
                and bool(lm_text) and (bool(ds_text) == deepseek),
                f"LM Studio card={bool(lm_text)}, DeepSeek card={bool(ds_text)}",
            )
            await shot(page, scratch, "S1")

            # S2
            await page.click("#btn-llm-provider-add")
            await page.fill("#llm-provider-name", "Stub")
            await page.fill("#llm-provider-base-url", "stub.local")
            await page.click("#btn-llm-provider-save")
            error_text = await page.locator("#llm-provider-form-error").inner_text()
            report(
                "S2 invalid Base URL shows the validation message",
                error_text == "Base URL должен начинаться с http:// или https://.",
                error_text,
            )

            # S3
            await fill_form(page, "Stub", f"http://{HOST}:{STUB_PORT}/v1/", "STUB_KEY")
            await page.click("#btn-llm-provider-save")
            text = await wait_card(page, "Stub", "доступен")
            toasts = await page.evaluate("window.__toasts")
            report(
                "S3 saving a provider triggers an automatic check that ends ok",
                "Провайдер сохранён" in toasts
                and "доступен · 1 моделей" in text
                and f"http://{HOST}:{STUB_PORT}" in text
                and "/v1" not in text
                and "ключ: STUB_KEY" in text,
                text.replace("\n", " | "),
            )
            await shot(page, scratch, "S3")

            # S4
            await card_button(page, "Stub", "Изменить").click()
            await fill_form(page, "Stub", f"http://{HOST}:{STUB_PORT}", "STUB_BAD")
            await page.click("#btn-llm-provider-save")
            bad = await wait_card(page, "Stub", "Неверный или отсутствующий API-ключ")
            bad_ok = "ошибка" in bad and "Неверный или отсутствующий API-ключ" in bad
            await card_button(page, "Stub", "Изменить").click()
            await fill_form(page, "Stub", f"http://{HOST}:{STUB_PORT}", "STUB_KEY")
            await page.click("#btn-llm-provider-save")
            await wait_card(page, "Stub", "доступен")
            await card_button(page, "Stub", "Проверить").click()
            again = await wait_card(page, "Stub", "доступен")
            report(
                "S4 wrong key variable gives the error badge, restoring it gives ok again",
                bad_ok and "доступен" in again,
                bad.replace("\n", " | "),
            )
            await shot(page, scratch, "S4")

            # S5
            # The page-load model refresh can still be waiting on the closed LM Studio port when
            # the card list is rendered, so the badge is produced by an explicit manual check.
            await card_button(page, "LM Studio", "Проверить").click()
            lm = await wait_card(page, "LM Studio", "Сервер недоступен")
            report(
                "S5 LM Studio card shows an error without blocking the page",
                "ошибка" in lm and "Сервер недоступен" in lm,
                lm.replace("\n", " | "),
            )

            # S6
            await page.click("#btn-close-settings")
            # The picker is rebuilt after the (slow, LM Studio is unreachable) model refresh.
            await page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#model-select optgroup'))"
                ".some(g => g.label === 'Stub')",
                timeout=30000,
            )
            labels = await option_labels(page)
            stub_options = [text for group, text in labels if group == "Stub"]
            ds_options = [text for group, text in labels if group == "DeepSeek"]
            s6 = "Stub · stub-model" in stub_options
            if deepseek:
                s6 = s6 and bool(ds_options) and all(o.startswith("DeepSeek · ") for o in ds_options)
            report(
                "S6 picker groups models by provider as 'Provider · model'",
                s6,
                f"Stub={stub_options}, DeepSeek={len(ds_options)} options",
            )
            await shot(page, scratch, "S6")

            # S7
            chats_before = await page.evaluate("state.chats.length")
            await page.click("#btn-new-chat")
            await page.wait_for_function(
                "(before) => state.chats.length > before && state.currentChatId === state.chats[0].id"
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
            await page.fill("#message-input", "Привет")
            await page.click("#btn-send")
            bubble = page.locator("#messages [data-message-id] .message-content", has_text=STUB_ANSWER)
            await bubble.first.wait_for(timeout=60000)
            await page.wait_for_function(
                f"document.querySelector('#chat-title').textContent === {json.dumps(STUB_TITLE)}",
                timeout=30000,
            )
            chat_auth = [auth for kind, auth in stub_auth_log if kind == "chat"]
            report(
                "S7 message answered by the stub provider with its bearer key; title routed too",
                bool(chat_auth) and all(a == f"Bearer {STUB_KEY_VALUE}" for a in chat_auth),
                f"chat requests={len(chat_auth)}, title={STUB_TITLE!r}",
            )
            await shot(page, scratch, "S7")

            # S8
            await page.click("#btn-settings")
            await card_button(page, "Stub", "Изменить").click()
            await page.set_checked("#llm-provider-enabled", False)
            await page.click("#btn-llm-provider-save")
            await wait_card(page, "Stub", "отключён")
            await page.wait_for_function(
                "() => !Array.from(document.querySelectorAll('#model-select optgroup'))"
                ".some(g => g.label === 'Stub')",
                timeout=15000,
            )
            toasts = await page.evaluate("window.__toasts")
            labels = await option_labels(page)
            fallback_toast = "Провайдер недоступен. Выбрана другая модель." in toasts
            placeholder = [text for _g, text in labels] == ["Нет доступных моделей"]
            report(
                "S8 disabling the provider removes its entries and falls back",
                (fallback_toast or placeholder) and not any(g == "Stub" for g, _t in labels),
                f"fallback toast={fallback_toast}, placeholder={placeholder}",
            )
            await shot(page, scratch, "S8")

            # S9
            await card_button(page, "Stub", "Удалить").click()
            await page.wait_for_function(
                "() => !Array.from(document.querySelectorAll('#llm-provider-list span'))"
                ".some(s => s.textContent === 'Stub')",
                timeout=15000,
            )
            toasts = await page.evaluate("window.__toasts")
            report(
                "S9 deleting a provider asks for confirmation and removes the card",
                bool(dialog_texts)
                and dialog_texts[-1].startswith("Удалить провайдера «Stub»?")
                and "Провайдер удалён" in toasts,
                dialog_texts[-1][:40] if dialog_texts else "no dialog",
            )
            await shot(page, scratch, "S9")

            # S10
            joined = "\n".join(api_bodies)
            leaked = [v for v in secret_values if v and v in joined]
            report(
                "S10 no API response contains a key value",
                not leaked and len(api_bodies) > 0,
                f"{len(api_bodies)} responses inspected, leaked={len(leaked)}",
            )

            # S11
            if deepseek:
                await page.click("#btn-close-settings")
                await run_deepseek_turn(page, scratch)
            else:
                print("S11 SKIPPED: no real DEEPSEEK_API_KEY in the repository .env (placeholder)")
        except Exception as exc:
            report("browser scenario completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
            try:
                await shot(page, scratch, "failure")
                body = await page.locator("body").inner_text()
                (scratch / "failure.txt").write_text(
                    f"{body}\n\nerrors: {browser_errors}\ntoasts: "
                    f"{await page.evaluate('window.__toasts')}\n",
                    encoding="utf-8",
                )
            except Exception as inner:  # noqa: BLE001 - evidence capture is best effort
                print(f"note: failure capture failed: {type(inner).__name__}")
        finally:
            await browser.close()


async def run_deepseek_turn(page: Any, scratch: Path) -> None:
    """S11: chat through the real DeepSeek provider and check the title log line."""
    labels = await option_labels(page)
    entries = [text for group, text in labels if group == "DeepSeek"]
    if not entries:
        print("S11 SKIPPED: DeepSeek check did not end ok (no DeepSeek models in the picker)")
        return
    value = await page.evaluate(
        "(text) => Array.from(document.querySelectorAll('#model-select option'))"
        ".find(o => o.textContent === text).value",
        entries[0],
    )
    chats_before = await page.evaluate("state.chats.length")
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "(before) => state.chats.length > before && state.currentChatId === state.chats[0].id"
        " && state.ws && state.ws.readyState === WebSocket.OPEN"
        " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
        arg=chats_before,
        timeout=15000,
    )
    await page.select_option("#model-select", value)
    await page.fill("#message-input", "Как настроить WebSocket в FastAPI?")
    await page.click("#btn-send")
    bubble = page.locator("#messages [data-message-id] .message-content").last
    await page.wait_for_function(
        "() => { const el = Array.from(document.querySelectorAll("
        "'#messages [data-message-id] .message-content')).pop();"
        " return el && el.textContent.trim().length > 0; }",
        timeout=60000,
    )
    await asyncio.sleep(8)
    chat_id = await page.evaluate("state.currentChatId")
    log_text = ""
    for log in (scratch / "app" / "logs").glob("*.log"):
        log_text += log.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf'chat_title_set[^\n]*"chat_id": {chat_id}[^\n]*', log_text)
    source = re.search(r'"source": "(\w+)"', match.group(0)) if match else None
    report(
        "S11 DeepSeek answer is non-empty and the title was set",
        len((await bubble.inner_text()).strip()) > 0 and match is not None,
        f"chat_title_set source={source.group(1) if source else 'not logged'}",
    )
    await shot(page, scratch, "S11")


async def teardown(app_proc: asyncio.subprocess.Process | None, stub: Any) -> None:
    """Stop only what this script started: the copy's supervisor tree and the stub."""
    if app_proc is not None:
        try:
            snapshot = psutil.Process(app_proc.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            snapshot = []
        if app_proc.returncode is None:
            app_proc.terminate()
            try:
                await asyncio.wait_for(app_proc.wait(), SUPERVISOR_STOP_TIMEOUT)
            except TimeoutError:
                app_proc.kill()
        for proc in reversed(snapshot):
            try:
                proc.kill()
                proc.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                continue
    if stub is not None:
        server, task = stub
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
    await asyncio.sleep(0.5)
    report("isolated ports are free again", port_is_free(UI_PORT) and port_is_free(AGENT_PORT))


async def run_e2e() -> None:
    """Start the isolated copy, run the browser scenario and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="providers_e2e_"))
    db_path = scratch / "uat12.db"
    username, password = "uat12", secrets.token_urlsafe(12)
    app_proc: asyncio.subprocess.Process | None = None
    stub: Any = None
    log_file = None
    deepseek_key = real_deepseek_key()
    if deepseek_key:
        secret_values.append(deepseek_key)
    try:
        copy_dir = prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        await seed_scratch_user(db_path, username, password)
        report("scratch database is isolated from app.db", True, str(db_path))
        stub = await start_stub()

        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "UI_PORT": str(UI_PORT),
            "AGENT_PORT": str(AGENT_PORT),
            "LM_STUDIO_BASE_URL": f"http://{HOST}:{CLOSED_LM_PORT}",
            "DEEPSEEK_API_KEY": deepseek_key or "",
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
            ready = await wait_for_app(client)
        report("isolated app instance is up (UI :18000 + Agent :18001)", ready)
        if ready:
            await asyncio.wait_for(
                drive_browser(username, password, scratch, deepseek_key is not None),
                PLAYWRIGHT_TIMEOUT,
            )
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await teardown(app_proc, stub)
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
