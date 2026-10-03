"""Browser check of chat RAG (attach a KB, toggle, sources, KB deleted) on an isolated copy.

Usage: python scripts/e2e_rag_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory, drives it with headless
Chromium against the real LM Studio (a chat model plus the nomic embedder) and the real ФЗ-196
PDF in C:\\Projects\\RAG, and prints one PASS/FAIL line per check. The default ports of a
running app are never touched and only processes started by this script are ever stopped.

Reuses the copy/seed/login/KB-creation/teardown building blocks of e2e_kb_playwright.py.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy, LM Studio,
a chat model or the PDF missing), 4 Playwright not installed.
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

import e2e_kb_playwright as kb  # noqa: E402

UI_PORT: int = kb.UI_PORT
AGENT_PORT: int = kb.AGENT_PORT
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
PREFERRED_MODEL: str = "qwen/qwen3.5-9b"
KB_NAME: str = "fz196-rag"
QUESTIONS: tuple[str, str, str] = (
    "С какого возраста можно получить право на управление транспортным средством категории B?",
    "Какие документы подтверждают право на управление транспортными средствами?",
    "Кто допускается к экзаменам на получение права на управление транспортными средствами?",
)
ANSWER_TIMEOUT: float = 420.0
STEP_TIMEOUT_MS: int = 20000
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

report = kb.report
console_errors: list[str] = []
chat_model: dict[str, str] = {}


def pick_chat_model() -> str | None:
    """Return the preferred loaded chat model, else the first loaded non-embedding one."""
    data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
    loaded = [m["id"] for m in data if m.get("state") == "loaded" and m.get("type") != "embeddings"]
    if PREFERRED_MODEL in loaded:
        return PREFERRED_MODEL
    return loaded[0] if loaded else None


def preflight() -> int | None:
    """Return an exit code when the run must not start, else None."""
    blocked = kb.preflight()
    if blocked is not None:
        return blocked
    try:
        model = pick_chat_model()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"BLOCKED: LM Studio not available ({type(exc).__name__})")
        return EXIT_BLOCKED
    if model is None:
        print("BLOCKED: no loaded chat model in LM Studio")
        return EXIT_BLOCKED
    chat_model["id"] = model
    print(f"chat model: {model}", flush=True)
    return None


def assistant_rows(page: Any) -> Any:
    """Locate the assistant message wrappers in the chat."""
    return page.locator("#messages > div.justify-start[data-message-id]")


async def select_chat_model(page: Any) -> None:
    """Select the chosen chat model in the header picker and wait for it to apply."""
    value = await page.evaluate(
        "id => Array.from(document.querySelectorAll('#model-select option'))"
        ".find(o => o.textContent.includes(id)).value",
        chat_model["id"],
    )
    await page.select_option("#model-select", value)
    await asyncio.sleep(1.0)


async def ask(page: Any, question: str) -> bool:
    """Send a question and wait until a new assistant message is rendered and input is free."""
    before = await assistant_rows(page).count()
    await page.fill("#message-input", question)
    await page.click("#btn-send")
    deadline = time.monotonic() + ANSWER_TIMEOUT
    while time.monotonic() < deadline:
        if await assistant_rows(page).count() > before and await page.locator("#btn-send").is_enabled():
            await asyncio.sleep(1.0)
            return True
        await asyncio.sleep(1.0)
    return False


async def reopen_chat(page: Any) -> None:
    """Reload the page and reselect the (only) chat in the sidebar."""
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await page.wait_for_selector("#chat-list > *")
    await page.locator("#chat-list > *").first.click()
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await asyncio.sleep(1.0)


async def chat_id_of(page: Any) -> int:
    """Return the id of the only chat of the logged-in user."""
    resp = await page.request.get(f"{AGENT_URL}/api/v1/chats")
    return (await resp.json())[0]["id"]


async def tree_of(page: Any, chat_id: int) -> list[dict[str, Any]]:
    """Fetch the active-branch message list through the page's cookie jar."""
    resp = await page.request.get(f"{AGENT_URL}/api/v1/chats/{chat_id}/tree")
    return await resp.json()


async def delete_kb_via_panel(page: Any, name: str) -> None:
    """Delete a KB with the two-step confirm in the KB panel."""
    if await page.locator("#kb-panel-body").is_hidden():
        await page.click('[data-fold-toggle="kb-panel-body"]')
    row = kb.kb_row(page, name).first
    await row.get_by_role("button", name="Удалить").click()
    await kb.kb_row(page, name).first.get_by_role("button", name="Точно удалить?").click()
    await page.wait_for_function(
        "name => !Array.from(document.querySelectorAll('#kb-list span.font-semibold'))"
        ".some(s => s.textContent === name)",
        arg=name, timeout=30000,
    )


async def create_kb_with_nomic(page: Any, name: str, files: list[Path]) -> None:
    """Create a structural KB, pinning the nomic embedder (the default depends on loaded models)."""
    if await page.locator("#kb-panel-body").is_hidden():
        await page.click('[data-fold-toggle="kb-panel-body"]')
    await page.click("#btn-kb-new")
    await page.wait_for_selector("#kb-create-modal", state="visible")
    await page.wait_for_function(
        f"Array.from(document.querySelectorAll('#kb-embedding-model option'))"
        f".some(o => o.value === '{kb.NOMIC}')", timeout=30000,
    )
    await page.select_option("#kb-embedding-model", kb.NOMIC)
    await page.fill("#kb-name", name)
    await page.set_input_files("#kb-files", [str(f) for f in files])
    await page.select_option("#kb-strategy", "structural")
    await page.click("#btn-kb-submit")
    await page.wait_for_selector("#kb-create-modal", state="hidden", timeout=60000)


async def scenarios(page: Any, creds: dict[str, str]) -> None:
    """Run scenarios 1-7 in order."""
    user = "raguser"
    await kb.login(page, user, creds[user])

    # 1
    await create_kb_with_nomic(page, KB_NAME, [kb.FZ_PDF])
    ok, text = await kb.timed_ready(page, KB_NAME, kb.SMALL_READY_TIMEOUT)
    report("1 ФЗ-196 structural KB reaches «готово»", ok,
           f"{text.replace(chr(10), ' | ')}; {kb.durations[KB_NAME]:.0f}s")

    # 2
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await select_chat_model(page)
    disabled = await page.locator("#rag-toggle").is_disabled()
    badge = (await page.locator("#rag-badge").inner_text()).strip()
    report("2 new chat: RAG switch disabled and badge «без RAG»",
           disabled and badge == "без RAG", f"disabled={disabled} badge={badge!r}; model={chat_model['id']}")

    # 3
    kb_id = await page.evaluate(
        "name => { const o = Array.from(document.querySelectorAll('#rag-kb-select option'))"
        ".find(x => x.textContent === name); return o ? o.value : ''; }", KB_NAME,
    )
    await page.select_option("#rag-kb-select", kb_id)
    await page.wait_for_function("!document.getElementById('rag-toggle').disabled")
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'",
    )

    async def rag_state() -> tuple[str, str, bool, str, str]:
        return (
            await page.get_attribute("#rag-toggle", "aria-checked") or "",
            (await page.locator("#rag-toggle").inner_text()).strip(),
            await page.locator("#rag-k-wrap").is_visible(),
            await page.input_value("#rag-k-input"),
            (await page.locator("#rag-badge").inner_text()).strip(),
        )

    first = await rag_state()
    await reopen_chat(page)
    second = await rag_state()
    good = first == second and first[:4] == ("true", "с RAG", True, "5") and first[4].startswith("RAG: ")
    report("3 attach KB, switch to «с RAG», K=5, badge; state survives a reload", good,
           f"before={first} after={second}")

    # 4
    chat_id = await chat_id_of(page)
    answered = await ask(page, QUESTIONS[0])
    last = assistant_rows(page).last
    label = (await last.inner_text()) if answered else ""
    sources = page.locator("details.rag-sources")
    n_sources = await sources.count()
    summary = (await sources.first.locator("summary").inner_text()) if n_sources else ""
    snippet = ""
    header = ""
    if n_sources:
        await sources.first.locator("summary").click()
        row = sources.first.locator(".rag-source-row").first
        snip = row.locator(".rag-snippet")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snippet = (await snip.inner_text()).strip()
            if snippet and snippet != "Загрузка фрагмента…":
                break
            await asyncio.sleep(0.5)
        header = await sources.first.locator(".rag-source-row").evaluate_all(
            "rows => rows.map(r => r.firstElementChild.textContent).join(' || ')",
        )
    tree = await tree_of(page, chat_id)
    users = [m for m in tree if m["role"] == "user"]
    raw_ok = bool(users) and users[-1]["content"] == QUESTIONS[0]
    no_leak = not any("=== Фрагменты" in m["content"] for m in tree)
    loaded = bool(snippet) and snippet != "Загрузка фрагмента…" and "недоступен" not in snippet
    report("4 RAG answer: «с RAG · K=5», «Источники (N)», snippet loads, stored question is raw",
           answered and "с RAG · K=5" in label and summary.startswith("Источники (") and loaded
           and ("Статья 19" in header or "196" in header) and raw_ok and no_leak,
           f"answered={answered} summary={summary!r} snippet={snippet[:60]!r} "
           f"header={header[:160]!r} raw_ok={raw_ok} no_leak={no_leak}")

    # 5
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'false'",
    )
    sources_before = await page.locator("details.rag-sources").count()
    answered = await ask(page, QUESTIONS[1])
    label = (await assistant_rows(page).last.inner_text()) if answered else ""
    sources_after = await page.locator("details.rag-sources").count()
    report("5 «без RAG» answer is labelled and adds no sources block",
           answered and "без RAG" in label and "с RAG" not in label and sources_after == sources_before,
           f"answered={answered} sources {sources_before}->{sources_after}")

    # 6
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'",
    )
    await delete_kb_via_panel(page, KB_NAME)
    await asyncio.sleep(2.0)
    answered = await ask(page, QUESTIONS[2])
    last = assistant_rows(page).last
    last_text = (await last.inner_text()) if answered else ""
    warning = page.locator('#messages [role="status"]').last
    warn_text = (await warning.inner_text()) if await page.locator('#messages [role="status"]').count() else ""
    toasts = await page.evaluate("window.__toasts || []")
    await reopen_chat(page)
    warn_after = ""
    if await page.locator('#messages [role="status"]').count():
        warn_after = await page.locator('#messages [role="status"]').last.inner_text()
    report("6 KB deleted: answer still arrives with the warning, toast, «сбой поиска»; warning survives reload",
           answered and "База знаний удалена" in warn_text and "без RAG (сбой поиска)" in last_text
           and any("Поиск по базе знаний не удался" in t for t in toasts)
           and "База знаний удалена" in warn_after,
           f"answered={answered} warn={warn_text!r} toasts={toasts} after_reload={warn_after!r}")

    # 7
    report("7 no console errors (snippet 404s excepted)", not console_errors,
           "; ".join(console_errors[:3]))
    report("no browser page errors", not kb.page_errors, "; ".join(kb.page_errors[:3]))


async def drive_browser(creds: dict[str, str], scratch: Path) -> None:
    """Launch Chromium and run the scenarios with a screenshot on failure."""
    from playwright.async_api import async_playwright

    def on_console(msg: Any) -> None:
        if msg.type == "error" and "404" not in msg.text:
            console_errors.append(msg.text)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.add_init_script(kb.TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: kb.page_errors.append(str(exc)))
        page.on("console", on_console)
        try:
            await scenarios(page, creds)
        except Exception as exc:
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()


async def run_e2e() -> None:
    """Start the isolated copy, run the browser scenarios and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="rag_e2e_"))
    db_path = scratch / "e2e_rag.db"
    creds = {"raguser": secrets.token_urlsafe(12)}
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    try:
        copy_dir = kb.prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        await kb.seed_scratch_users(db_path, creds)
        report("scratch database and KB storage are isolated from app.db", True, str(db_path))

        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "UI_PORT": str(UI_PORT),
            "AGENT_PORT": str(AGENT_PORT),
            "LM_STUDIO_BASE_URL": LM_URL,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(
                [str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])],
            ),
        }
        env.pop("KB_STORAGE_DIR", None)
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
            await asyncio.wait_for(drive_browser(creds, scratch), 2400)
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await kb.teardown(app_proc)
        if log_file is not None:
            log_file.close()
        failed = sum(1 for _l, p, _d in kb.checks if not p)
        print(f"summary: {len(kb.checks) - failed} passed, {failed} failed", flush=True)
        (scratch / "results.json").write_text(
            json.dumps([{"check": c, "passed": p, "detail": d} for c, p, d in kb.checks],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if failed > 0:
            print(f"scratch kept for inspection: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


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
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in kb.checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
