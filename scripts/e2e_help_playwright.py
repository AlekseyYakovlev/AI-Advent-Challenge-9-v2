"""Browser and HTTP check of the /help command with a real local model on an isolated copy.

Usage: python scripts/e2e_help_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory. Through the real UI it
uploads a documentation folder as a knowledge base, adds the git MCP server (pointed at a
temporary repository created by this script), marks the base in the Settings section and runs
`/help` in the chat: the usage notice, a documentation answer with its label and sources, a git
answer with a tool-call card, and the same exchanges after a page reload. The same commands are
then sent over HTTP with an API key. Ports 8000/8001 of a running app and the branch of the
working copy are never touched; only processes started by this script are ever stopped.

Exit codes: 0 pass, 1 fail, 2 blocked (ports 18000/18001 busy, LM Studio unreachable, no loaded
chat model, no embedding model, git missing), 4 Playwright not installed.
"""

import asyncio
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

import e2e_kb_folder_playwright as kf  # noqa: E402
import e2e_kb_playwright as kb  # noqa: E402
import e2e_mcp_git_playwright as git  # noqa: E402

REPO_ROOT: Path = kb.REPO_ROOT
UI_PORT: int = 18000
AGENT_PORT: int = 18001
UI_URL: str = kb.UI_URL
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
KB_NAME: str = "e2e-project-docs"
EMBEDDING_MODEL: str = "text-embedding-bge-m3"
Q_DOC: str = "/help как запустить тесты проекта?"
Q_GIT: str = "/help на какой ветке проект?"
Q_USAGE: str = "/help"
DOC_FACT: str = "pytest tests/ -v"
BRANCH: str = "alpha"
CARD_NAME: str = "mcp__local-git__git_current_branch"
TOOL_CARD_SELECTOR: str = "details.tool-call-card"
LABEL_PREFIX: str = "Справка по проекту · "
BADGE_TEXT: str = "Документация проекта"
NOTICE_FOOTER: str = "Это сообщение не сохраняется в чате."
MAX_ATTEMPTS: int = 2
USERNAME: str = "helpuser"
STEP_TIMEOUT_MS: int = 20000
ANSWER_TIMEOUT_S: float = 240.0
HTTP_TIMEOUT_S: float = 300.0
INDEX_TIMEOUT_S: float = 300.0
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

report = kb.report
page_errors: list[str] = kb.page_errors
CheckLine = tuple[str, bool, str]


def make_docs_folder(scratch: Path) -> Path:
    """Create the documentation fixture: README.md plus a docs folder with two guides."""
    root = scratch / "project-docs"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(
        "# Учебный проект\n\nЭто чат-приложение с локальной моделью. "
        "Подробности лежат в каталоге docs.\n",
        encoding="utf-8",
    )
    (docs / "TESTING_GUIDE.md").write_text(
        "# Руководство по тестам\n\nТесты запускаются командой `pytest tests/ -v`. "
        "Один файл запускается командой `pytest tests/test_name.py -v`.\n",
        encoding="utf-8",
    )
    (docs / "ARCHITECTURE.md").write_text(
        "# Архитектура\n\nПриложение состоит из двух процессов: UI на одном порту и "
        "Agent на другом. Процессы общаются только по HTTP и WebSocket.\n",
        encoding="utf-8",
    )
    return root


def preflight() -> int | None:
    """Return an exit code when the run must not start, else None."""
    blocked = git.preflight()
    if blocked is not None:
        return blocked
    try:
        data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
    except (httpx.HTTPError, ValueError) as exc:
        return git.blocked_exit(f"LM Studio not available ({type(exc).__name__})")
    if not any(m.get("id") == EMBEDDING_MODEL and m.get("type") == "embeddings" for m in data):
        return git.blocked_exit(f"LM Studio does not list the embedding model {EMBEDDING_MODEL}")
    return None


def assistant_rows(page: Any) -> Any:
    """Locate the assistant message wrappers in the chat."""
    return page.locator("#messages > div.justify-start[data-message-id]")


def user_rows(page: Any) -> Any:
    """Locate the user message wrappers in the chat."""
    return page.locator("#messages > div.justify-end[data-message-id]")


def source_blocks(page: Any) -> Any:
    """Locate the «Источники» blocks in the chat."""
    return page.locator("#messages details.rag-sources")


async def send(page: Any, text: str) -> None:
    """Type a message into the composer and send it."""
    await git.wait_socket(page)
    await page.fill("#message-input", text)
    await page.click("#btn-send")


async def ask_doc(page: Any, text: str) -> tuple[str, str, str]:
    """Send a /help question; return (answer text, label text, sources text) of the new answer."""
    rows_before = await assistant_rows(page).count()
    sources_before = await source_blocks(page).count()
    await send(page, text)
    deadline = time.monotonic() + ANSWER_TIMEOUT_S
    while time.monotonic() < deadline:
        rows = await assistant_rows(page).count()
        if rows > rows_before and await page.locator("#btn-send").is_enabled():
            break
        await asyncio.sleep(1.0)
    await asyncio.sleep(1.0)
    rows = assistant_rows(page)
    if await rows.count() <= rows_before:
        return "", "", ""
    last = rows.last
    answer = ((await last.locator(".message-content").text_content()) or "").strip()
    bubble = ((await last.text_content()) or "").strip()
    sources = ""
    if await source_blocks(page).count() > sources_before:
        sources = ((await source_blocks(page).last.text_content()) or "").strip()
    return answer, bubble, sources


async def new_chat(page: Any) -> int:
    """Create a chat through the UI, select the model and return the chat id."""
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "() => state.chats.length >= 1 && state.currentChatId === state.chats[0].id",
        timeout=15000,
    )
    await git.wait_socket(page)
    await git.select_chat_model(page)
    return int(await page.evaluate("() => state.currentChatId"))


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
    entries = await kf.list_entries(page)
    titles = sorted(e["title"] for e in entries)
    expected = ["README.md", "docs/ARCHITECTURE.md", "docs/TESTING_GUIDE.md"]
    ok_list = titles == expected
    report("folder upload: pending list", ok_list, f"{method}; {titles}")
    await page.fill("#kb-name", KB_NAME)
    await page.select_option("#kb-embedding-model", EMBEDDING_MODEL)
    await page.click("#btn-kb-submit")
    await page.wait_for_selector("#kb-create-modal", state="hidden", timeout=60000)
    text = await kb.wait_row(page, KB_NAME, "готово", INDEX_TIMEOUT_S)
    ready = "готово" in text and "ошибка" not in text
    return report("folder upload: base ready", ready and ok_list, text.replace("\n", " | "))


async def mark_base(page: Any) -> None:
    """Open Settings, select the base in the /help section and check the saved defaults."""
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
    git_checked = await page.evaluate(
        """() => {
            const label = Array.from(document.querySelectorAll('#help-mcp-list label'))
                .find((l) => l.textContent.trim() === 'local-git');
            if (!label) return null;
            return document.getElementById(label.htmlFor).checked;
        }"""
    )
    memory_checked = await page.locator("#help-tools-memory").is_checked()
    report("default tools", git_checked is True and not memory_checked,
           f"local-git checked={git_checked}; memory checked={memory_checked}")
    await page.click("#btn-close-settings")
    await page.wait_for_selector("#settings-modal", state="hidden")


async def check_badge(page: Any) -> None:
    """The knowledge base card of the marked base carries the documentation badge."""
    await kf.expand_panel(page)
    text = ""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        text = await kb.row_text(page, KB_NAME)
        if BADGE_TEXT in text:
            break
        await asyncio.sleep(1.0)
    report("badge shown", BADGE_TEXT in text, text.replace("\n", " | ")[:140])


async def check_usage_notice(page: Any) -> None:
    """`/help` alone shows the temporary notice and stores nothing."""
    rows_before = await assistant_rows(page).count()
    users_before = await user_rows(page).count()
    await send(page, Q_USAGE)
    try:
        await page.wait_for_selector(".help-notice", timeout=15000)
    except Exception as exc:  # noqa: BLE001 - playwright raises its own TimeoutError type
        report("usage notice", False, f"no notice: {type(exc).__name__}")
        return
    notice = (await page.locator(".help-notice").last.text_content()) or ""
    report(
        "usage notice",
        "/help <вопрос>" in notice and NOTICE_FOOTER in notice
        and await page.locator("#message-input").is_enabled()
        and await assistant_rows(page).count() == rows_before
        and await user_rows(page).count() == users_before,
        notice.strip()[:140],
    )


async def browser_attempt(page: Any, attempt: int) -> tuple[list[CheckLine], list[str], int]:
    """Run the documentation and git questions in a new chat; return checks, transcript, chat id."""
    chat_id = await new_chat(page)
    results: list[CheckLine] = []
    if attempt == 1:
        await check_usage_notice(page)
    answer, bubble, sources = await ask_doc(page, Q_DOC)
    transcript = [f"Q: {Q_DOC}", f"A: {answer}", f"bubble: {bubble[:200]}", f"sources: {sources[:200]}"]
    results.append(("doc answer: label", f"{LABEL_PREFIX}{KB_NAME}" in bubble, bubble[:120]))
    results.append(("doc answer: sources", "Источники" in sources and "TESTING_GUIDE.md" in sources,
                    sources[:120]))
    results.append(("doc answer: fact", "pytest" in answer.lower(), answer[:120]))
    appeared, card, git_answer = await git.ask(page, Q_GIT)
    transcript += [f"Q: {Q_GIT}", f"card: {card}", f"A: {git_answer}"]
    live_cards = await page.locator(TOOL_CARD_SELECTOR, has_text="git_current_branch").count()
    results.append(("git answer: tool card", appeared and git.card_names_the_tool(card)
                    and CARD_NAME in card, f"{live_cards} card(s); {card[:100]}"))
    results.append(("git answer: branch", BRANCH in git_answer.lower(), git_answer[:120]))
    return results, transcript, chat_id


async def check_reload(page: Any, chat_id: int) -> None:
    """After a reload both exchanges keep their label; the sources stay; the notice is gone."""
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await page.evaluate("id => selectChat(id)", chat_id)
    await page.wait_for_function(
        "() => document.querySelectorAll('#messages > div.justify-start[data-message-id]').length >= 2",
        timeout=30000,
    )
    users = [((await user_rows(page).nth(i).text_content()) or "").strip()
             for i in range(await user_rows(page).count())]
    help_users = [u for u in users if u.startswith("/help ")]
    report("reload: two exchanges",
           len(help_users) == 2 and all(u != Q_USAGE for u in users) and len(users) == 2, str(users))
    assistants = [((await assistant_rows(page).nth(i).text_content()) or "").strip()
                  for i in range(await assistant_rows(page).count())]
    labelled = [a for a in assistants if a.startswith(LABEL_PREFIX)]
    report("reload: labels", len(assistants) == 2 and len(labelled) == 2,
           str([a[:50] for a in assistants]))
    blocks = [((await source_blocks(page).nth(i).text_content()) or "")
              for i in range(await source_blocks(page).count())]
    report("reload: sources", any("TESTING_GUIDE.md" in b for b in blocks),
           f"{len(blocks)} sources block(s)")
    report("reload: notice gone", await page.locator(".help-notice").count() == 0)


async def drive_browser(scratch: Path, copy_dir: Path, repo: Path, docs_root: Path, password: str) -> None:
    """Run the browser part of the scenario."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await kb.login(page, USERNAME, password)
            if not await create_base(page, docs_root):
                return
            script = copy_dir / "mcp_servers" / "project_git.py"
            if not report("git server added", await git.add_server(page, script, repo), str(script)):
                return
            await mark_base(page)
            await check_badge(page)
            chat_id = 0
            for attempt in range(1, MAX_ATTEMPTS + 1):
                results, transcript, chat_id = await browser_attempt(page, attempt)
                if all(ok for _, ok, _ in results) or attempt == MAX_ATTEMPTS:
                    for label, ok, detail in results:
                        report(label, ok, detail)
                    break
                print(f"--- failed attempt {attempt} transcript ---", flush=True)
                print("\n".join(transcript), flush=True)
                print("failed: " + ", ".join(label for label, ok, _ in results if not ok), flush=True)
            await check_reload(page, chat_id)
            report("no browser page errors", not page_errors, "; ".join(page_errors[:3]))
        except Exception as exc:  # noqa: BLE001 - any browser failure must be reported, not raised
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()


def bearer(key: str) -> dict[str, str]:
    """Headers of a key-only request."""
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def create_api_key(password: str) -> str:
    """Log in with a cookie client and create an API key; the key is never printed."""
    headers = {"Origin": UI_URL}
    async with httpx.AsyncClient(timeout=15.0) as client:
        login = await client.post(
            f"{AGENT_URL}/api/v1/auth/login",
            json={"username": USERNAME, "password": password}, headers=headers,
        )
        login.raise_for_status()
        created = await client.post(
            f"{AGENT_URL}/api/v1/api-keys", json={"name": "e2e-help"}, headers=headers,
        )
        created.raise_for_status()
        return str(created.json()["key"])


async def http_post(client: httpx.AsyncClient, key: str, chat_id: int, text: str) -> httpx.Response:
    """Send one key-authenticated chat message."""
    return await client.post(
        f"{AGENT_URL}/api/v1/chats/{chat_id}/messages",
        json={"content": text, "model": git.chat_model["id"]}, headers=bearer(key),
    )


async def http_new_chat(client: httpx.AsyncClient, key: str) -> int:
    """Create a chat over REST and return its id."""
    resp = await client.post(f"{AGENT_URL}/api/v1/chats", json={"title": "http-help"}, headers=bearer(key))
    resp.raise_for_status()
    return int(resp.json()["id"])


async def http_attempt(client: httpx.AsyncClient, key: str) -> tuple[list[CheckLine], list[str]]:
    """Ask the documentation and git questions in a new chat over HTTP."""
    chat_id = await http_new_chat(client, key)
    doc = await http_post(client, key, chat_id, Q_DOC)
    doc_body: dict[str, Any] = doc.json() if doc.status_code == 200 else {}
    sources = (doc_body.get("rag") or {}).get("sources") or []
    help_info = doc_body.get("help") or {}
    gitr = await http_post(client, key, chat_id, Q_GIT)
    git_body: dict[str, Any] = gitr.json() if gitr.status_code == 200 else {}
    names = [str(t.get("name")) for t in git_body.get("tool_calls") or []]
    results: list[CheckLine] = [
        ("http: doc answer", doc.status_code == 200 and bool(sources)
         and help_info.get("kb_name") == KB_NAME and bool(doc_body.get("content")),
         f"status={doc.status_code}; sources={len(sources)}; kb_name={help_info.get('kb_name')}"),
        ("http: git answer", gitr.status_code == 200
         and any("git_current_branch" in n for n in names)
         and BRANCH in str(git_body.get("content", "")).lower(),
         f"status={gitr.status_code}; tool_calls={names}"),
    ]
    transcript = [f"Q: {Q_DOC}", f"A: {str(doc_body.get('content'))[:300]}",
                  f"Q: {Q_GIT}", f"A: {str(git_body.get('content'))[:300]}"]
    return results, transcript


async def http_checks(password: str) -> None:
    """Run the HTTP part: the usage body, a documentation answer and a git answer."""
    try:
        key = await create_api_key(password)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            chat_id = await http_new_chat(client, key)
            usage = await http_post(client, key, chat_id, Q_USAGE)
            body: dict[str, Any] = usage.json() if usage.status_code == 200 else {}
            report("http: usage body",
                   usage.status_code == 200 and body.get("stored") is False
                   and (body.get("help") or {}).get("kind") == "usage",
                   f"status={usage.status_code}; stored={body.get('stored')}")
            for attempt in range(1, MAX_ATTEMPTS + 1):
                results, transcript = await http_attempt(client, key)
                if all(ok for _, ok, _ in results) or attempt == MAX_ATTEMPTS:
                    for label, ok, detail in results:
                        report(label, ok, detail)
                    break
                print(f"--- failed attempt {attempt} transcript ---", flush=True)
                print("\n".join(transcript), flush=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        report("http part completed", False, f"{type(exc).__name__}: {exc}")


async def run_e2e() -> int:
    """Start the isolated copy, run the browser and HTTP parts, tear down; return the exit code."""
    scratch = Path(tempfile.mkdtemp(prefix="help_e2e_"))
    db_path = scratch / "e2e_help.db"
    creds = {USERNAME: secrets.token_urlsafe(12)}
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    try:
        repo = git.make_temp_repo(scratch)
        git.ensure_scratch_repo(repo)
        docs_root = make_docs_folder(scratch)
        copy_dir = kb.prepare_copy(scratch)
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
                drive_browser(scratch, copy_dir, repo, docs_root, creds[USERNAME]), 1500,
            )
            await asyncio.wait_for(http_checks(creds[USERNAME]), 1200)
    except Exception as exc:  # noqa: BLE001 - report any setup failure instead of crashing
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await kb.teardown(app_proc)
        if log_file is not None:
            log_file.close()
    failed = [label for label, ok, _ in kb.checks if not ok]
    model = git.chat_model.get("id", "?")
    if failed:
        print(f"scratch kept for inspection: {scratch}", flush=True)
        print(f"RESULT: FAIL (model {model}; failed: {', '.join(failed)})", flush=True)
        return EXIT_FAIL
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"RESULT: PASS (model {model})", flush=True)
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
