"""Browser and HTTP check of the /support command on an isolated copy.

Usage: python scripts/e2e_support_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory. Through the real UI it
uploads a documentation folder (README, docs including FAQ.md) as a knowledge base, marks it for
/help, adds the support MCP server through the «MCP серверы» form (the data file is a scratch copy
of mcp_servers/support_data.json) and checks the «Поддержка (/support)» settings section. In the
chat it runs `/support`, `/support #T-1001 ...` and `/support #T-1002 ...`, checks the tool-call
card and the label before and after a reload, and sends the same two questions over HTTP with an
API key. When LM Studio is running it also records whether the answers name the real causes, a
follow-up without identifier, and a status change made in the scratch JSON. Checks that need the
model are recorded as NOT RUN, never as PASS, when LM Studio is down. Ports 8000/8001 of a running
app are never touched; only processes started by this script are ever stopped.

Exit codes: 0 pass, 1 fail, 2 blocked (ports 18000/18001 busy), 4 Playwright not installed.
"""

import asyncio
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_help_playwright as helpe2e  # noqa: E402
import e2e_kb_folder_playwright as kf  # noqa: E402
import e2e_kb_playwright as kb  # noqa: E402
import e2e_mcp_git_playwright as git  # noqa: E402

REPO_ROOT: Path = kb.REPO_ROOT
UI_PORT: int = 18000
AGENT_PORT: int = 18001
UI_URL: str = kb.UI_URL
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
SERVER_NAME: str = "support"
KB_NAME: str = helpe2e.KB_NAME
EMBEDDING_MODEL: str = helpe2e.EMBEDDING_MODEL
Q_T1: str = "/support #T-1001 Почему не работает авторизация?"
Q_T2: str = "/support #T-1002 Почему не работает авторизация?"
Q_FOLLOW: str = "/support а что ещё можно попробовать?"
Q_USAGE: str = "/support"
CARD_NAME: str = "mcp__support__get_ticket"
CARD_TOOL: str = "get_ticket"
LABEL_T1: str = "Поддержка · T-1001"
LABEL_T2: str = "Поддержка · T-1002"
NOTICE_CHIP: str = "Поддержка"
NOTICE_FOOTER: str = helpe2e.NOTICE_FOOTER
T1_FACTS: tuple[str, ...] = ("429", "попыт", "LOGIN_LOCKOUT_SECONDS", "заблок")
T2_FACTS: tuple[str, ...] = ("403", "Origin", "BIND_HOST", "Cross-origin")
TOOL_CARD_SELECTOR: str = helpe2e.TOOL_CARD_SELECTOR
USERNAME: str = "supportuser"
STEP_TIMEOUT_MS: int = 20000
ANSWER_TIMEOUT_S: float = 240.0
HTTP_TIMEOUT_S: float = 300.0
INDEX_TIMEOUT_S: float = 300.0
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

report = kb.report
page_errors: list[str] = kb.page_errors
not_run: list[str] = []
state: dict[str, Any] = {"lm_up": False}


def server_args(script_path: Path | str, data_path: Path | str) -> str:
    """Arguments of the support server, one per line, for the «MCP серверы» form."""
    return f"{script_path}\n--data={data_path}"


def mentions_any(text: str, facts: tuple[str, ...] | list[str]) -> bool:
    """True when the text contains at least one of the facts, ignoring case."""
    lowered = text.lower()
    return any(fact.lower() in lowered for fact in facts)


def live_verdict(lm_up: bool, ok: bool) -> str:
    """Verdict of a model-dependent check: «not run» when LM Studio is down, else passed/failed."""
    if not lm_up:
        return "not run"
    return "passed" if ok else "failed"


def edit_ticket_status(path: Path, ticket_id: str, status: str) -> bool:
    """Rewrite the status of one ticket in a scratch copy of the data file; True when found."""
    data = json.loads(path.read_text(encoding="utf-8"))
    found = False
    for ticket in data.get("tickets", []):
        if ticket.get("id") == ticket_id:
            ticket["status"] = status
            found = True
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return found


def record_live(label: str, ok: bool, detail: str = "") -> None:
    """Record a model-dependent check: PASS/FAIL when LM Studio is up, NOT RUN otherwise."""
    verdict = live_verdict(bool(state["lm_up"]), ok)
    if verdict == "not run":
        not_run.append(label)
        print(f"NOT RUN: {label} (LM Studio is down)", flush=True)
    else:
        report(label, verdict == "passed", detail)


def make_support_docs(scratch: Path) -> Path:
    """Documentation fixture: README.md plus docs with the guides and FAQ.md."""
    root = helpe2e.make_docs_folder(scratch)
    faq = REPO_ROOT / "docs" / "FAQ.md"
    text = faq.read_text(encoding="utf-8") if faq.is_file() else "# FAQ\n\nЧастые вопросы.\n"
    (root / "docs" / "FAQ.md").write_text(text, encoding="utf-8")
    return root


def preflight() -> int | None:
    """Return an exit code when the run must not start, else None; record the LM Studio state."""
    busy = [p for p in (UI_PORT, AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        return git.blocked_exit(f"port(s) {busy} are in use; the isolated copy needs them free")
    try:
        model = git.pick_chat_model()
        data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
        has_embedding = any(
            m.get("id") == EMBEDDING_MODEL and m.get("type") == "embeddings" for m in data
        )
    except (httpx.HTTPError, ValueError) as exc:
        print(f"LM Studio not available ({type(exc).__name__}); model checks will be NOT RUN", flush=True)
        return None
    if model is None or not has_embedding:
        print("LM Studio has no loaded chat model or embedding model; model checks will be NOT RUN",
              flush=True)
        return None
    git.chat_model["id"] = model
    state["lm_up"] = True
    print(f"LM Studio up; chat model: {model}", flush=True)
    return None


async def create_base(page: Any, docs_root: Path) -> bool:
    """Upload README.md and the docs folder through the create form; True when the base is ready."""
    await kf.expand_panel(page)
    await page.click("#btn-kb-new")
    await page.wait_for_selector("#kb-create-modal", state="visible")
    await page.wait_for_function(
        "id => Array.from(document.querySelectorAll('#kb-embedding-model option'))"
        ".some(o => o.value === id)",
        arg=EMBEDDING_MODEL, timeout=30000,
    )
    await page.set_input_files("#kb-files", str(docs_root / "README.md"))
    method = await kf.choose_folder(page, docs_root / "docs")
    titles = sorted(e["title"] for e in await kf.list_entries(page))
    expected = ["README.md", "docs/ARCHITECTURE.md", "docs/FAQ.md", "docs/TESTING_GUIDE.md"]
    ok_list = titles == expected
    report("folder upload: pending list incl. FAQ.md", ok_list, f"{method}; {titles}")
    await page.fill("#kb-name", KB_NAME)
    await page.select_option("#kb-embedding-model", EMBEDDING_MODEL)
    await page.click("#btn-kb-submit")
    await page.wait_for_selector("#kb-create-modal", state="hidden", timeout=60000)
    text = await kb.wait_row(page, KB_NAME, "готово", INDEX_TIMEOUT_S)
    ready = "готово" in text and "ошибка" not in text
    return report("folder upload: base ready", ready and ok_list, text.replace("\n", " | "))


async def mark_base(page: Any) -> None:
    """Select the base in the /help section (the base /support shares)."""
    await git.open_settings(page)
    await page.wait_for_function(
        "name => Array.from(document.querySelectorAll('#help-kb option'))"
        ".some(o => o.textContent === name)",
        arg=KB_NAME, timeout=30000,
    )
    await page.select_option("#help-kb", label=KB_NAME)
    await page.wait_for_function(
        "() => document.getElementById('help-save-status').textContent.includes('Сохранено')",
        timeout=15000,
    )
    marked = (await page.locator("#help-kb option:checked").inner_text()).strip()
    report("base marked", marked == KB_NAME, marked)
    await page.click("#btn-close-settings")
    await page.wait_for_selector("#settings-modal", state="hidden")


async def add_server(page: Any, script: Path, data_copy: Path) -> bool:
    """Add the support server through the «MCP серверы» form; True when it is listed."""
    await git.open_settings(page)
    await page.click("#btn-mcp-add")
    await page.wait_for_selector("#mcp-server-form", state="visible")
    await page.fill("#mcp-name", SERVER_NAME)
    await page.fill("#mcp-command", sys.executable)
    await page.fill("#mcp-args", server_args(script, data_copy))
    await page.fill("#mcp-cwd", str(script.parent.parent))
    await page.click("#btn-mcp-save")
    try:
        await page.wait_for_function(
            "name => document.getElementById('mcp-server-list').textContent.includes(name)",
            arg=SERVER_NAME, timeout=60000,
        )
    except Exception as exc:  # noqa: BLE001 - playwright raises its own TimeoutError type
        print(f"server did not appear in the list: {type(exc).__name__}", flush=True)
        return False
    error = (await page.locator("#mcp-form-error").inner_text()).strip()
    listed = await page.locator("#mcp-server-list").inner_text()
    await page.click("#btn-close-settings")
    await page.wait_for_selector("#settings-modal", state="hidden")
    return not error and SERVER_NAME in listed


async def check_support_section(page: Any, expect_base: bool) -> None:
    """The «Поддержка (/support)» section shows `support` checked and names the base."""
    await git.open_settings(page)
    await page.wait_for_function(
        "() => document.querySelectorAll('#support-mcp-list input[type=checkbox]').length >= 1",
        timeout=30000,
    )
    checked = await page.evaluate(
        """() => {
            const label = Array.from(document.querySelectorAll('#support-mcp-list label'))
                .find((l) => l.textContent.trim() === 'support');
            return label ? document.getElementById(label.htmlFor).checked : null;
        }"""
    )
    report("support section: support checked", checked is True, f"checked={checked}")
    if expect_base:
        # the section is first drawn from the cached config, then redrawn with the fresh one
        try:
            await page.wait_for_function(
                "name => document.getElementById('support-kb-info').textContent.includes(name)",
                arg=KB_NAME, timeout=10000,
            )
        except Exception:  # noqa: BLE001 - playwright raises its own TimeoutError type
            pass
    info = (await page.locator("#support-kb-info").inner_text()).strip()
    if expect_base:
        report("support section: base named", KB_NAME in info, info[:140])
    await page.click("#btn-close-settings")
    await page.wait_for_selector("#settings-modal", state="hidden")


def ticket_cards(page: Any) -> Any:
    """Locate the tool-call cards of the ticket lookup."""
    return page.locator(TOOL_CARD_SELECTOR, has_text=CARD_TOOL)


async def ask_support(page: Any, text: str) -> tuple[str, str, str, int]:
    """Send a /support question; return (answer, bubble, newest card text, new card count)."""
    cards_before = await ticket_cards(page).count()
    rows_before = await helpe2e.assistant_rows(page).count()
    await helpe2e.send(page, text)
    deadline = time.monotonic() + ANSWER_TIMEOUT_S
    while time.monotonic() < deadline:
        rows = await helpe2e.assistant_rows(page).count()
        if rows > rows_before and await page.locator("#btn-send").is_enabled():
            break
        await asyncio.sleep(1.0)
    await asyncio.sleep(1.0)
    rows = helpe2e.assistant_rows(page)
    if await rows.count() <= rows_before:
        return "", "", "", 0
    last = rows.last
    answer = ((await last.locator(".message-content").text_content()) or "").strip()
    bubble = ((await last.text_content()) or "").strip()
    cards = ticket_cards(page)
    total = await cards.count()
    card = ((await cards.last.text_content()) or "").strip() if total else ""
    return answer, bubble, card, total - cards_before


def card_names_ticket_tool(card: str) -> bool:
    """True when the card names the ticket lookup: full name or server plus tool."""
    return CARD_NAME in card or (SERVER_NAME in card and CARD_TOOL in card)


async def check_usage_notice(page: Any) -> None:
    """`/support` alone shows the temporary notice with the «Поддержка» chip and stores nothing."""
    rows_before = await helpe2e.assistant_rows(page).count()
    users_before = await helpe2e.user_rows(page).count()
    await helpe2e.send(page, Q_USAGE)
    try:
        await page.wait_for_selector(".help-notice", timeout=15000)
    except Exception as exc:  # noqa: BLE001 - playwright raises its own TimeoutError type
        report("usage notice", False, f"no notice: {type(exc).__name__}")
        return
    notice = (await page.locator(".help-notice").last.text_content()) or ""
    report(
        "usage notice",
        NOTICE_CHIP in notice and NOTICE_FOOTER in notice
        and await page.locator("#message-input").is_enabled()
        and await helpe2e.assistant_rows(page).count() == rows_before
        and await helpe2e.user_rows(page).count() == users_before,
        notice.strip()[:140],
    )


async def check_ticket(page: Any, question: str, label: str, facts: tuple[str, ...], tag: str) -> str:
    """Ask about one ticket; check the card and the label (structural), the facts (live)."""
    answer, bubble, card, new_cards = await ask_support(page, question)
    report(f"{tag}: tool card", new_cards >= 1 and card_names_ticket_tool(card), card[:100])
    report(f"{tag}: label", label in bubble, bubble[:100])
    record_live(f"{tag}: answer names the cause", mentions_any(answer, facts), answer[:160])
    return answer


async def check_reload(page: Any, chat_id: int) -> None:
    """After a reload the label of the T-1001 answer is still there and the notice is gone."""
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await page.evaluate("id => selectChat(id)", chat_id)
    await page.wait_for_function(
        "() => document.querySelectorAll('#messages > div.justify-start[data-message-id]').length >= 1",
        timeout=30000,
    )
    assistants = [((await helpe2e.assistant_rows(page).nth(i).text_content()) or "").strip()
                  for i in range(await helpe2e.assistant_rows(page).count())]
    report("reload: label survives", any(LABEL_T1 in a for a in assistants),
           str([a[:50] for a in assistants]))
    report("reload: notice gone", await page.locator(".help-notice").count() == 0)


async def live_followups(page: Any, chat_id: int, data_copy: Path) -> None:
    """Follow-up without identifier and a status change in the scratch JSON (model needed)."""
    await page.evaluate("id => selectChat(id)", chat_id)
    await page.wait_for_function(
        "id => state.currentChatId === id", arg=chat_id, timeout=15000,
    )
    cards_before = await ticket_cards(page).count()
    answer, _, _, _ = await ask_support(page, Q_FOLLOW)
    cards_after = await ticket_cards(page).count()
    record_live("follow-up without identifier: answered, no new lookup",
                bool(answer) and cards_after == cards_before, f"cards {cards_before}->{cards_after}")
    changed = edit_ticket_status(data_copy, "T-1001", "closed")
    _, _, card, new_cards = await ask_support(page, Q_T1)
    record_live("status change in scratch JSON is seen",
                changed and new_cards >= 1 and "closed" in card, card[:140])


async def drive_browser(
    scratch: Path, copy_dir: Path, docs_root: Path, data_copy: Path, password: str,
) -> None:
    """Run the browser part of the scenario."""
    from playwright.async_api import async_playwright

    lm_up = bool(state["lm_up"])
    async with async_playwright() as playwright:
        video_dir = os.environ.get("SUPPORT_E2E_VIDEO_DIR", "")
        slow_mo = int(os.environ.get("SUPPORT_E2E_SLOW_MO_MS", "0"))
        browser = await playwright.chromium.launch(headless=not video_dir, slow_mo=slow_mo)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 850},
            **({"record_video_dir": video_dir, "record_video_size": {"width": 1366, "height": 850}}
               if video_dir else {}),
        )
        page = await context.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await kb.login(page, USERNAME, password)
            if lm_up and not await create_base(page, docs_root):
                return
            script = copy_dir / "mcp_servers" / "support_desk.py"
            if not report("support server added via form", await add_server(page, script, data_copy),
                          str(script)):
                return
            if lm_up:
                await mark_base(page)
            await check_support_section(page, expect_base=lm_up)
            if not lm_up:
                chat_id = await create_chat_without_model(page)
                await check_usage_notice(page)
                for label in ("T-1001 card+label", "T-1002 card+label", "reload label", "follow-up",
                              "status change"):
                    record_live(label, False)
                del chat_id
                report("no browser page errors", not page_errors, "; ".join(page_errors[:3]))
                return
            chat_id = await helpe2e.new_chat(page)
            await check_usage_notice(page)
            await check_ticket(page, Q_T1, LABEL_T1, T1_FACTS, "T-1001")
            await check_reload(page, chat_id)
            await helpe2e.new_chat(page)
            await check_ticket(page, Q_T2, LABEL_T2, T2_FACTS, "T-1002")
            await live_followups(page, chat_id, data_copy)
            report("no browser page errors", not page_errors, "; ".join(page_errors[:3]))
        except Exception as exc:  # noqa: BLE001 - any browser failure must be reported, not raised
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await context.close()
            await browser.close()
            if video_dir and page.video:
                print(f"video saved: {await page.video.path()}", flush=True)


async def create_chat_without_model(page: Any) -> int:
    """Create a chat through the UI without picking a model (LM Studio is down)."""
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "() => state.chats.length >= 1 && state.currentChatId === state.chats[0].id", timeout=15000,
    )
    await git.wait_socket(page)
    return int(await page.evaluate("() => state.currentChatId"))


async def create_api_key(password: str) -> str:
    """Log in as the support scratch user and create an API key; the key is never printed."""
    headers = {"Origin": UI_URL}
    async with httpx.AsyncClient(timeout=15.0) as client:
        login = await client.post(
            f"{AGENT_URL}/api/v1/auth/login",
            json={"username": USERNAME, "password": password}, headers=headers,
        )
        login.raise_for_status()
        created = await client.post(
            f"{AGENT_URL}/api/v1/api-keys", json={"name": "e2e-support"}, headers=headers,
        )
        created.raise_for_status()
        return str(created.json()["key"])


async def http_checks(password: str) -> None:
    """Run the HTTP part: the same two questions with an API key."""
    if not state["lm_up"]:
        for label in ("http: T-1001", "http: T-1002"):
            record_live(label, False)
        return
    try:
        key = await create_api_key(password)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            for question, label, facts, tag in (
                (Q_T1, LABEL_T1, T1_FACTS, "http: T-1001"),
                (Q_T2, LABEL_T2, T2_FACTS, "http: T-1002"),
            ):
                chat_id = await helpe2e.http_new_chat(client, key)
                resp = await helpe2e.http_post(client, key, chat_id, question)
                body: dict[str, Any] = resp.json() if resp.status_code == 200 else {}
                calls = body.get("tool_calls") or []
                first = str(calls[0].get("name")) if calls else ""
                info = body.get("support") or {}
                report(f"{tag}: tool call and label",
                       resp.status_code == 200 and first == CARD_NAME and info.get("label") == label,
                       f"status={resp.status_code}; first={first}; label={info.get('label')}")
                record_live(f"{tag}: answer names the cause",
                            mentions_any(str(body.get("content", "")), facts),
                            str(body.get("content", ""))[:160])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        report("http part completed", False, f"{type(exc).__name__}: {exc}")


async def run_e2e() -> int:
    """Start the isolated copy, run the browser and HTTP parts, tear down; return the exit code."""
    scratch = Path(tempfile.mkdtemp(prefix="support_e2e_"))
    db_path = scratch / "e2e_support.db"
    creds = {USERNAME: secrets.token_urlsafe(12)}
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    try:
        docs_root = make_support_docs(scratch)
        copy_dir = kb.prepare_copy(scratch)
        data_copy = scratch / "support_data.json"
        shutil.copyfile(REPO_ROOT / "mcp_servers" / "support_data.json", data_copy)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        await kb.seed_scratch_users(db_path, creds)
        kb_store = scratch / "kb_store"
        kb_store.mkdir()
        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "UI_PORT": str(UI_PORT),
            "AGENT_PORT": str(AGENT_PORT),
            "LM_STUDIO_BASE_URL": LM_URL,
            "KB_STORAGE_DIR": str(kb_store),
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
        kb.app_proc_holder["proc"] = app_proc
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await kb.wait_for_app(client)
        report("isolated app instance is up (UI :18000 + Agent :18001)", ready)
        if ready:
            await asyncio.wait_for(
                drive_browser(scratch, copy_dir, docs_root, data_copy, creds[USERNAME]), 1500,
            )
            await asyncio.wait_for(http_checks(creds[USERNAME]), 1200)
    except Exception as exc:  # noqa: BLE001 - report any setup failure instead of crashing
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await kb.teardown(app_proc)
        if log_file is not None:
            log_file.close()
    failed = [label for label, ok, _ in kb.checks if not ok]
    model = git.chat_model.get("id", "none")
    if not_run:
        print(f"NOT RUN ({len(not_run)}): {', '.join(not_run)}", flush=True)
    if failed:
        print(f"scratch kept for inspection: {scratch}", flush=True)
        print(f"RESULT: FAIL (model {model}; failed: {', '.join(failed)})", flush=True)
        return EXIT_FAIL
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"RESULT: PASS (model {model}; not run: {len(not_run)})", flush=True)
    return EXIT_PASS


def main() -> None:
    """Run preflight, then the isolated end-to-end scenario, and exit with the result code."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed (pip install playwright && playwright install chromium).")
        print("RESULT: NOT RUN (Playwright is not installed)")
        sys.exit(EXIT_NO_PLAYWRIGHT)
    blocked = preflight()
    if blocked is not None:
        sys.exit(blocked)
    sys.exit(asyncio.run(run_e2e()))


if __name__ == "__main__":
    main()
