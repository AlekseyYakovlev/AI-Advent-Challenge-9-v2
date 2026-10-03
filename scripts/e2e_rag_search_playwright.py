"""Browser check of the search settings popover and the retrieval details on an isolated copy.

Usage: python scripts/e2e_rag_search_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory, drives it with headless
Chromium against the real LM Studio (a chat model plus the nomic embedder) and the real ФЗ-196
PDF in C:\\Projects\\RAG, and prints one PASS/FAIL line per check. The default ports of a
running app are never touched and only processes started by this script are ever stopped.

Reuses the copy/seed/login/teardown building blocks of e2e_kb_playwright.py and the chat helpers
of e2e_rag_playwright.py.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy, LM Studio,
a chat model or the PDF missing), 4 Playwright not installed.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_kb_playwright as kb  # noqa: E402
import e2e_rag_playwright as rag  # noqa: E402

UI_PORT: int = kb.UI_PORT
AGENT_PORT: int = kb.AGENT_PORT
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
KB_NAME: str = "fz196-search"
Q_AGE: str = "С какого возраста можно получить право на управление транспортным средством категории B?"
Q_ARTICLE: str = "Что сказано в ст. 26 о допуске к управлению транспортными средствами?"
Q_DOCS: str = "Какие документы подтверждают право на управление транспортными средствами?"
STEP_TIMEOUT_MS: int = 20000
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

report = kb.report


async def rag_cfg(page: Any, chat_id: int) -> dict[str, Any]:
    """Fetch the chat RAG configuration through the page's cookie jar."""
    resp = await page.request.get(f"{AGENT_URL}/api/v1/chats/{chat_id}/rag")
    return await resp.json()


async def open_popover(page: Any) -> None:
    """Open the search popover when it is closed."""
    if await page.locator("#rag-search-popover").is_hidden():
        await page.click("#rag-search-btn")
        await page.wait_for_selector("#rag-search-popover", state="visible")


async def set_switch(page: Any, selector: str, on: bool) -> None:
    """Flip a visually hidden switch through its visible label when its state differs."""
    box = page.locator(selector)
    if await box.is_checked() != on:
        await page.locator("label").filter(has=box).click()


async def wait_cfg(page: Any, chat_id: int, field: str, expected: Any) -> dict[str, Any]:
    """Poll the REST config until the field has the expected value (the UI saves on change)."""
    cfg: dict[str, Any] = {}
    for _ in range(30):
        cfg = await rag_cfg(page, chat_id)
        if cfg.get(field) == expected:
            return cfg
        await asyncio.sleep(0.5)
    return cfg


def threshold_expectation(cfg: dict[str, Any]) -> tuple[str, str]:
    """Return the expected (value text, note) of an unset threshold for this KB."""
    effective = cfg.get("effective_threshold")
    value = f"{float(effective):.2f}" if effective is not None else "0.00"
    note = "(калибр.)" if cfg.get("calibrated_threshold") is not None else "(нет калибровки)"
    return value, note


async def last_details(page: Any) -> Any:
    """Return the details.rag-details locator of the last message (a sibling of its bubble row)."""
    return page.locator("#messages details.rag-details").last


async def open_details(page: Any) -> Any:
    """Open the last message's «Детали поиска» block and return its locator."""
    details = await last_details(page)
    if await details.count() and await details.get_attribute("open") is None:
        await details.locator("summary").click()
    return details


async def details_text(details: Any, label: str) -> str:
    """Return the value text of the detail row with the given label ('' when absent)."""
    row = details.locator("div.flex").filter(has=details.page.locator("span", has_text=label))
    if await row.count() == 0:
        return ""
    return (await row.first.inner_text()).replace("\n", " ")


async def table_headers(details: Any) -> list[str]:
    """Return the candidate table header texts."""
    return [t.strip() for t in await details.locator("thead th").all_inner_texts()]


async def status_chips(details: Any) -> list[str]:
    """Return the first chip text of every candidate row."""
    return [
        (await cell.locator("span").first.inner_text()).strip()
        for cell in await details.locator("tbody tr td:last-child").all()
    ]


async def scenarios(page: Any, creds: dict[str, str]) -> None:
    """Run scenarios S1-S11 in order."""
    await kb.login(page, "searchuser", creds["searchuser"])
    await rag.create_kb_with_nomic(page, KB_NAME, [kb.FZ_PDF])
    ok, text = await kb.timed_ready(page, KB_NAME, kb.SMALL_READY_TIMEOUT)
    report("setup: ФЗ-196 KB reaches «готово»", ok, f"{text.replace(chr(10), ' | ')}")

    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await rag.select_chat_model(page)
    kb_id = await page.evaluate(
        "name => { const o = Array.from(document.querySelectorAll('#rag-kb-select option'))"
        ".find(x => x.textContent === name); return o ? o.value : ''; }", KB_NAME,
    )
    await page.select_option("#rag-kb-select", kb_id)
    await page.wait_for_function("!document.getElementById('rag-toggle').disabled")
    chat_id = await rag.chat_id_of(page)

    # S1
    hidden_off = await page.locator("#rag-search-btn").is_hidden()
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'",
    )
    visible_on = await page.locator("#rag-search-btn").is_visible()
    label = (await page.locator("#rag-search-btn").inner_text()).strip()
    report("S1 «Поиск ⚙» is hidden with RAG off and shown with RAG on",
           hidden_off and visible_on and label == "Поиск ⚙",
           f"hidden_off={hidden_off} visible_on={visible_on} label={label!r}; model={rag.chat_model['id']}")

    # S2
    cfg = await rag_cfg(page, chat_id)
    await page.click("#rag-search-btn")
    await page.wait_for_selector("#rag-search-popover", state="visible")
    pop = page.locator("#rag-search-popover")
    role = await pop.get_attribute("role")
    aria = await pop.get_attribute("aria-label")
    cand = await page.input_value("#rag-candidate-k")
    switches = [await page.locator(f"#rag-stage-{s}").is_checked()
                for s in ("lexical", "llm", "hybrid", "rewrite")]
    value, note = threshold_expectation(cfg)
    got_value = await page.input_value("#rag-threshold")
    got_note = (await page.locator("#rag-threshold-note").inner_text()).strip()
    report("S2 popover: dialog «Настройки поиска», K=20, threshold and note from the API, switches off",
           role == "dialog" and aria == "Настройки поиска" and cand == "20" and not any(switches)
           and got_value == value and got_note == note,
           f"role={role} aria={aria!r} cand={cand} switches={switches} "
           f"threshold={got_value}/{value} note={got_note!r}/{note!r}")

    # S3
    await page.keyboard.press("Escape")
    closed_esc = await page.locator("#rag-search-popover").is_hidden()
    focused = await page.evaluate("document.activeElement && document.activeElement.id")
    await page.click("#rag-search-btn")
    await page.wait_for_selector("#rag-search-popover", state="visible")
    await page.click("#messages")
    closed_click = await page.locator("#rag-search-popover").is_hidden()
    report("S3 Escape closes the popover and returns focus; a click outside closes it too",
           closed_esc and focused == "rag-search-btn" and closed_click,
           f"esc={closed_esc} focus={focused} click={closed_click}")

    # S4
    await open_popover(page)
    await page.fill("#rag-candidate-k", "30")
    await page.dispatch_event("#rag-candidate-k", "change")
    await wait_cfg(page, chat_id, "candidate_k", 30)
    await set_switch(page, "#rag-stage-lexical", True)
    cfg = await wait_cfg(page, chat_id, "lexical", True)
    classes = await page.get_attribute("#rag-search-btn", "class") or ""
    await reopen(page)
    await open_popover(page)
    cand_after = await page.input_value("#rag-candidate-k")
    lex_after = await page.locator("#rag-stage-lexical").is_checked()
    report("S4 K=30 and lexical on persist independently across a reload; button gets the indigo border",
           cfg.get("candidate_k") == 30 and cfg.get("lexical") is True
           and not cfg.get("llm_rerank") and not cfg.get("hybrid") and not cfg.get("rewrite")
           and "border-indigo-500" in classes and cand_after == "30" and lex_after,
           f"cfg={ {k: cfg.get(k) for k in ('candidate_k', 'lexical', 'llm_rerank', 'hybrid', 'rewrite')} } "
           f"after_reload={cand_after}/{lex_after}")

    # S5
    await page.fill("#rag-threshold", "0.99")
    await page.dispatch_event("#rag-threshold", "change")
    cfg = await wait_cfg(page, chat_id, "threshold", 0.99)
    reset_visible = await page.locator("#rag-threshold-reset").is_visible()
    await page.click("#rag-threshold-reset")
    cfg_reset = await wait_cfg(page, chat_id, "threshold", None)
    value, note = threshold_expectation(cfg_reset)
    note_back = (await page.locator("#rag-threshold-note").inner_text()).strip()
    report("S5 threshold override shows «сбросить»; reset returns to the calibrated/no-calibration text",
           reset_visible and cfg.get("threshold_source") == "user" and cfg_reset.get("threshold") is None
           and note_back == note and await page.input_value("#rag-threshold") == value,
           f"override={cfg.get('threshold')}/{cfg.get('threshold_source')} reset_visible={reset_visible} "
           f"note={note_back!r}")
    await page.keyboard.press("Escape")

    # S6
    answered = await rag.ask(page, Q_AGE)
    details = await open_details(page)
    has_block = await details.count() == 1
    summary = (await details.locator("summary").inner_text()) if has_block else ""
    query_line = await details_text(details, "Запрос:") if has_block else ""
    stages_line = await details_text(details, "Этапы:") if has_block else ""
    headers = await table_headers(details) if has_block else []
    chips = await status_chips(details) if has_block else []
    yellow = await details.locator('[class*="yellow"]').count() if has_block else -1
    long_cells = await details.locator("td").evaluate_all(
        "cells => cells.filter(c => c.textContent.length > 120).length",
    ) if has_block else -1
    rows_before = len(chips)
    report("S6 answer carries a collapsed «Детали поиска» with the candidate table and no chunk text",
           answered and has_block and summary == "Детали поиска" and "Запрос:" in query_line
           and "Этапы:" in stages_line and "✓ в ответе" in chips and yellow == 0 and long_cells == 0
           and all(h in headers for h in ("было→стало", "cos", "lex", "Источник", "Статус")),
           f"answered={answered} headers={headers} chips={chips} yellow={yellow} long_cells={long_cells}")

    # S7
    await reopen(page)
    details = await open_details(page)
    rows_after = len(await status_chips(details)) if await details.count() else -1
    report("S7 «Детали поиска» survives a reload with the same rows",
           await details.count() == 1 and rows_after == rows_before and rows_after > 0,
           f"rows {rows_before} -> {rows_after}")

    # S8
    await open_popover(page)
    await set_switch(page, "#rag-stage-hybrid", True)
    await wait_cfg(page, chat_id, "hybrid", True)
    await page.keyboard.press("Escape")
    answered = await rag.ask(page, Q_ARTICLE)
    details = await open_details(page)
    headers = await table_headers(details) if await details.count() else []
    stages_line = await details_text(details, "Этапы:") if await details.count() else ""
    report("S8 hybrid on: the table has an «FTS» column and «FTS5» is in the stages line",
           answered and "FTS" in headers and "FTS5" in stages_line,
           f"answered={answered} headers={headers} stages={stages_line!r}")

    # S9
    await open_popover(page)
    await set_switch(page, "#rag-stage-hybrid", False)
    await wait_cfg(page, chat_id, "hybrid", False)
    await page.fill("#rag-threshold", "0.99")
    await page.dispatch_event("#rag-threshold", "change")
    await wait_cfg(page, chat_id, "threshold", 0.99)
    await page.keyboard.press("Escape")
    toasts_before = len(await page.evaluate("window.__toasts || []"))
    sources_before = await page.locator("details.rag-sources").count()
    answered = await rag.ask(page, Q_AGE)
    toasts_after = len(await page.evaluate("window.__toasts || []"))
    last = rag.assistant_rows(page).last
    grey = last.locator("div", has_text=re.compile(r"^Фрагменты не прошли порог \(лучший "))
    grey_text = (await grey.first.inner_text()) if await grey.count() else ""
    own_sources = await page.locator("details.rag-sources").count() - sources_before
    yellow = await last.locator('[class*="yellow"]').count()
    details = await open_details(page)
    chips = await status_chips(details) if await details.count() else []
    await reopen(page)
    grey_after = await rag.assistant_rows(page).last.locator(
        "div", has_text=re.compile(r"^Фрагменты не прошли порог \(лучший "),
    ).count()
    report("S9 threshold 0.99: grey line, no sources block, all rows «ниже порога», no toast or warning",
           answered and bool(grey_text) and own_sources == 0 and bool(chips)
           and all(c == "ниже порога" for c in chips) and toasts_after == toasts_before
           and yellow == 0 and grey_after > 0,
           f"grey={grey_text!r} sources={sources_before}/{own_sources} chips={set(chips)} "
           f"toasts={toasts_before}->{toasts_after} yellow={yellow} grey_after_reload={grey_after}")

    # S10
    await open_popover(page)
    await page.click("#rag-threshold-reset")
    await wait_cfg(page, chat_id, "threshold", None)
    await set_switch(page, "#rag-stage-rewrite", True)
    await wait_cfg(page, chat_id, "rewrite", True)
    await set_switch(page, "#rag-stage-llm", True)
    await wait_cfg(page, chat_id, "llm_rerank", True)
    await page.keyboard.press("Escape")
    toasts_before = len(await page.evaluate("window.__toasts || []"))
    answered = await rag.ask(page, Q_DOCS)
    toasts_after = len(await page.evaluate("window.__toasts || []"))
    last = rag.assistant_rows(page).last
    yellow = await last.locator('[class*="yellow"]').count()
    details = await open_details(page)
    have = await details.count() == 1
    stages_line = await details_text(details, "Этапы:") if have else ""
    skip_rows = await details.locator("div.flex").filter(
        has=page.locator("span", has_text="Пропущено:"),
    ).all_inner_texts() if have else []
    skips = " | ".join(s.replace("\n", " ") for s in skip_rows)
    llm_ran, rewrite_ran = "LLM" in stages_line, "rewrite" in stages_line
    llm_skip = "↷ LLM-реранк" in skips
    rewrite_skip = "↷ Переписывание" in skips
    rewritten = await details_text(details, "Переписан:") if have else ""
    outcome = (
        f"llm={'ran' if llm_ran else 'skipped (' + skips + ')' if llm_skip else 'MISSING'}; "
        f"rewrite={'ran' if rewrite_ran else 'skipped (' + skips + ')' if rewrite_skip else 'MISSING'}"
        f"{'; rewritten=' + rewritten if rewritten else ''}"
    )
    report("S10 rewrite + LLM rerank: each stage ran or shows a «↷» skip line; no toast or warning",
           answered and have and (llm_ran or llm_skip) and (rewrite_ran or rewrite_skip)
           and yellow == 0 and toasts_after == toasts_before,
           f"{outcome}; yellow={yellow} toasts={toasts_before}->{toasts_after}")

    # S11
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'false'",
    )
    report("S11 RAG off hides the button and closes the popover",
           await page.locator("#rag-search-btn").is_hidden()
           and await page.locator("#rag-search-popover").is_hidden())

    report("no browser page errors", not kb.page_errors, "; ".join(kb.page_errors[:3]))


async def reopen(page: Any) -> None:
    """Reload the page and reselect the only chat."""
    await rag.reopen_chat(page)
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'"
        " || document.getElementById('rag-toggle').getAttribute('aria-checked') === 'false'",
    )


async def drive_browser(creds: dict[str, str], scratch: Path) -> None:
    """Launch Chromium and run the scenarios with a screenshot on failure."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.add_init_script(kb.TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: kb.page_errors.append(str(exc)))
        try:
            await scenarios(page, creds)
        except Exception as exc:
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()


async def run_e2e() -> None:
    """Start the isolated copy, run the browser scenarios and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="rag_search_e2e_"))
    db_path = scratch / "e2e_rag_search.db"
    creds = {"searchuser": secrets.token_urlsafe(12)}
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
            await asyncio.wait_for(drive_browser(creds, scratch), 3000)
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

    blocked = rag.preflight()
    if blocked is not None:
        sys.exit(blocked)

    asyncio.run(run_e2e())
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in kb.checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
