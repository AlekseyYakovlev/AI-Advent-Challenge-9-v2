"""Browser check of citations, the «не знаю» gate and the strict switch on an isolated copy.

Usage: python scripts/e2e_rag_cite_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory, drives it with headless
Chromium against the real LM Studio (a chat model plus the nomic embedder) and the real ФЗ-196
PDF in C:\\Projects\\RAG, and prints one PASS/FAIL line per check. The default ports of a
running app are never touched and only processes started by this script are ever stopped.

The nomic embedder has no calibrated threshold, so the gate is forced by a threshold of 0.99.

Reuses the copy/seed/login/teardown building blocks of e2e_kb_playwright.py, the chat helpers of
e2e_rag_playwright.py and the popover helpers of e2e_rag_search_playwright.py.

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
import e2e_rag_playwright as rag  # noqa: E402
import e2e_rag_search_playwright as search  # noqa: E402

UI_PORT: int = kb.UI_PORT
AGENT_PORT: int = kb.AGENT_PORT
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
KB_NAME: str = "fz196-cite"
QUESTION: str = search.Q_ARTICLE
STEP_TIMEOUT_MS: int = 20000
MAX_ATTEMPTS: int = 3
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4
STATE_CHIPS: tuple[str, ...] = ("✓ подтверждена", "≈ почти дословно", "✗ не подтверждена")
GATE_PREFIX: str = "Не знаю:"
TEMPLATE_IDK: str = "Не знаю: в базе знаний нет достаточно подходящих"
GREY_LINE: str = "Фрагменты не прошли порог"

report = kb.report

BLOCKS_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('#messages > div.justify-start[data-message-id]'));
  const last = rows[rows.length - 1];
  const out = [];
  if (!last) return out;
  let el = last.nextElementSibling;
  while (el && !el.matches('div[data-message-id]')) {
    const d = el.querySelector('details');
    if (d) {
      out.push({
        cls: d.className.split(' ')[0],
        open: d.open,
        summary: d.querySelector('summary').textContent.trim(),
      });
    }
    el = el.nextElementSibling;
  }
  return out;
}
"""

SIGNATURE_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('#messages > div.justify-start[data-message-id]'));
  return rows.map((row) => {
    const blocks = [];
    let el = row.nextElementSibling;
    while (el && !el.matches('div[data-message-id]')) {
      const d = el.querySelector('details');
      if (d) blocks.push(d.className.split(' ')[0] + ':' + d.querySelector('summary').textContent.trim());
      el = el.nextElementSibling;
    }
    const text = row.innerText;
    return {
      blocks: blocks,
      grey: text.includes('Фрагменты не прошли порог'),
      tail: text.includes('Цитаты:'),
      idk: text.trimStart().startsWith('Не знаю:'),
    };
  });
}
"""


async def blocks_after_last(page: Any) -> list[dict[str, Any]]:
    """Return the details blocks that follow the last assistant message, in DOM order."""
    return await page.evaluate(BLOCKS_JS)


def block_index(blocks: list[dict[str, Any]], cls: str) -> int:
    """Return the position of the first block with this class name, or -1."""
    for i, block in enumerate(blocks):
        if block["cls"] == cls:
            return i
    return -1


async def newest_assistant(page: Any, chat_id: int) -> dict[str, Any] | None:
    """Return the assistant message with the highest id of the active branch (the tree is not ordered)."""
    tree = await rag.tree_of(page, chat_id)
    assistants = [m for m in tree if m["role"] == "assistant"]
    return max(assistants, key=lambda m: m["id"]) if assistants else None


async def last_assistant_payload(page: Any, chat_id: int) -> dict[str, Any]:
    """Return the stored rag_sources payload of the last assistant message ({} when absent)."""
    newest = await newest_assistant(page, chat_id)
    if newest is None:
        return {}
    payload = newest.get("rag_sources")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else {}


async def last_assistant_content(page: Any, chat_id: int) -> str:
    """Return the stored text of the last assistant message."""
    newest = await newest_assistant(page, chat_id)
    return newest["content"] if newest else ""


async def set_threshold(page: Any, chat_id: int, value: str) -> dict[str, Any]:
    """Type a threshold into the popover and wait for the saved value."""
    await search.open_popover(page)
    await page.fill("#rag-threshold", value)
    await page.dispatch_event("#rag-threshold", "change")
    return await search.wait_cfg(page, chat_id, "threshold", float(value))


async def scenarios(page: Any, creds: dict[str, str]) -> None:
    """Run scenarios S1-S9 in order."""
    await kb.login(page, "citeuser", creds["citeuser"])
    await rag.create_kb_with_nomic(page, KB_NAME, [kb.FZ_PDF])
    ok, text = await kb.timed_ready(page, KB_NAME, kb.SMALL_READY_TIMEOUT)
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
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'",
    )
    chat_id = await rag.chat_id_of(page)
    report("S1 setup: ФЗ-196 KB «готово», chat with the KB attached and RAG on",
           ok and bool(kb_id), f"{text.replace(chr(10), ' | ')}; model={rag.chat_model['id']}")

    # S2
    cfg = await search.rag_cfg(page, chat_id)
    await page.click("#rag-search-btn")
    await page.wait_for_selector("#rag-search-popover", state="visible")
    checked = await page.locator("#rag-strict").is_checked()
    first_in_dom = await page.evaluate(
        "() => !!(document.getElementById('rag-strict').compareDocumentPosition("
        "document.getElementById('rag-stage-lexical')) & Node.DOCUMENT_POSITION_FOLLOWING)",
    )
    classes = await page.get_attribute("#rag-search-btn", "class") or ""
    report("S2 strict switch is first in «Поиск ⚙», on by default, no indigo border",
           checked and first_in_dom and cfg.get("strict") is True and "border-indigo-500" not in classes,
           f"checked={checked} before_lexical={first_in_dom} api_strict={cfg.get('strict')} "
           f"indigo={'border-indigo-500' in classes}")
    await page.keyboard.press("Escape")

    # S3
    answered = await rag.ask(page, QUESTION)
    payload = await last_assistant_payload(page, chat_id)
    attempts = 1
    while answered and attempts < MAX_ATTEMPTS and (
        payload.get("answer_empty") is True or payload.get("verdict") == "model_idk"
    ):
        attempts += 1
        answered = await rag.ask(page, QUESTION)
        payload = await last_assistant_payload(page, chat_id)
    bubble_text = (await rag.assistant_rows(page).last.inner_text()) if answered else ""
    blocks = await blocks_after_last(page)
    qi, si, di = (block_index(blocks, c) for c in ("rag-quotes", "rag-sources", "rag-details"))
    quotes = blocks[qi] if qi >= 0 else {}
    n_match = quotes.get("summary", "")
    count_ok = n_match.startswith("Цитаты (") and n_match.endswith(")") and n_match[8:-1].isdigit()
    n_quotes = int(n_match[8:-1]) if count_ok else 0
    rows = page.locator("details.rag-quotes").last.locator(".rag-quote-row")
    row_count = await rows.count()
    chips: list[str] = []
    auto = 0
    for i in range(row_count):
        spans = await rows.nth(i).locator("div").first.locator("span").all_inner_texts()
        chips.append(spans[0].strip() if spans else "")
        auto += "подобрана автоматически" in spans
    chips_ok = row_count > 0 and all(
        c in STATE_CHIPS or c.startswith("✗ не подтверждена") for c in chips
    )
    inconclusive = payload.get("answer_empty") is True or payload.get("verdict") == "model_idk"
    report("S3 strict answer: no «Цитаты:» tail, open «Цитаты (N)», collapsed sources and details, order",
           answered and not inconclusive and "Цитаты:" not in bubble_text and qi >= 0
           and quotes.get("open") is True and count_ok and n_quotes >= 1 and row_count == n_quotes
           and chips_ok and si > qi and di >= 0 and blocks[si]["open"] is False
           and blocks[di]["open"] is False,
           f"attempts={attempts} blocks={[b['cls'] for b in blocks]} summary={n_match!r} chips={chips} "
           f"auto={auto} quotes_ranked={sum(1 for q in payload.get('quotes', []) if isinstance(q.get('rank'), int))}")

    # S4
    sources_by_rank = {s.get("rank"): s for s in payload.get("sources", [])}
    p_quotes = payload.get("quotes", [])
    files_ok = all(
        sources_by_rank.get(q["rank"], {}).get("file") == q.get("file")
        for q in p_quotes if isinstance(q.get("rank"), int)
    )
    states_ok = all(q.get("state") in ("exact", "fuzzy", "unverified") for q in p_quotes)
    report("S4 stored payload: v3, strict, quote states, quote files match the source of their rank",
           payload.get("v") == 3 and payload.get("strict") is True and states_ok and files_ok,
           f"v={payload.get('v')} strict={payload.get('strict')} verdict={payload.get('verdict')} "
           f"states={[q.get('state') for q in p_quotes]} files_ok={files_ok} "
           f"answer_supported={payload.get('answer_supported')}")

    # S5
    cited = payload.get("cited_ranks") or []
    if cited and si >= 0:
        sources_block = page.locator("details.rag-sources").last
        await sources_block.locator("summary").click()
        head_text = await sources_block.locator(".rag-source-row").first.locator("div").first.inner_text()
        report("S5 first row of «Источники» carries «цитируется»", "цитируется" in head_text,
               f"cited_ranks={cited} head={head_text!r}")
    else:
        report("S5 skipped: no cited ranks in this answer (nothing to mark)", True,
               f"cited_ranks={cited} sources_block={si >= 0}")

    # S6
    cfg = await set_threshold(page, chat_id, "0.99")
    await page.keyboard.press("Escape")
    started = time.monotonic()
    answered = await rag.ask(page, QUESTION)
    elapsed = time.monotonic() - started
    payload6 = await last_assistant_payload(page, chat_id)
    content6 = await last_assistant_content(page, chat_id)
    bubble6 = (await rag.assistant_rows(page).last.inner_text()) if answered else ""
    blocks6 = await blocks_after_last(page)
    grey_present = GREY_LINE in bubble6
    report("S6 gate at threshold 0.99: «Не знаю:» + «Уточните», grey line, no quotes/sources, details kept",
           answered and cfg.get("threshold") == 0.99 and bubble6.lstrip().startswith(GATE_PREFIX)
           and "Уточните" in bubble6 and grey_present
           and block_index(blocks6, "rag-quotes") < 0 and block_index(blocks6, "rag-sources") < 0
           and block_index(blocks6, "rag-details") >= 0
           and payload6.get("gated") is True and payload6.get("verdict") == "below_threshold",
           f"elapsed={elapsed:.1f}s (send to done, includes ~1s settle) blocks={[b['cls'] for b in blocks6]} "
           f"gated={payload6.get('gated')} verdict={payload6.get('verdict')} text={content6[:80]!r}")

    # S7
    before = await page.evaluate(SIGNATURE_JS)
    await search.reopen(page)
    after = await page.evaluate(SIGNATURE_JS)
    report("S7 reload: quotes, sources, details and the grey line render the same", before == after
           and len(after) >= 2
           and not any(r["tail"] and any(b.startswith("rag-quotes") for b in r["blocks"]) for r in after),
           f"before={before} after={after}")

    # S8
    await search.open_popover(page)
    await search.set_switch(page, "#rag-strict", False)
    cfg = await search.wait_cfg(page, chat_id, "strict", False)
    await page.keyboard.press("Escape")
    answered = await rag.ask(page, QUESTION)
    payload8 = await last_assistant_payload(page, chat_id)
    content8 = await last_assistant_content(page, chat_id)
    blocks8 = await blocks_after_last(page)
    await search.open_popover(page)
    await page.click("#rag-threshold-reset")
    await search.wait_cfg(page, chat_id, "threshold", None)
    await search.set_switch(page, "#rag-strict", True)
    cfg_back = await search.wait_cfg(page, chat_id, "strict", True)
    await page.keyboard.press("Escape")
    report("S8 strict off: model text (no template, no quotes block), payload strict=false, gated=false; reset",
           answered and cfg.get("strict") is False and not content8.startswith(TEMPLATE_IDK)
           and block_index(blocks8, "rag-quotes") < 0 and payload8.get("strict") is False
           and payload8.get("gated") is False and not payload8.get("quotes")
           and cfg_back.get("strict") is True and cfg_back.get("threshold") is None,
           f"blocks={[b['cls'] for b in blocks8]} strict={payload8.get('strict')} gated={payload8.get('gated')} "
           f"quotes={payload8.get('quotes')} text={content8[:80]!r} back_strict={cfg_back.get('strict')}")

    # S9
    injected = await page.evaluate(
        "document.querySelectorAll('.rag-quote-row script, .rag-quote-row img').length",
    )
    report("S9 hygiene: no page errors, no script or img inside quote rows",
           not kb.page_errors and injected == 0,
           f"injected={injected}; errors={'; '.join(kb.page_errors[:3])}")


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
    scratch = Path(tempfile.mkdtemp(prefix="rag_cite_e2e_"))
    db_path = scratch / "e2e_rag_cite.db"
    creds = {"citeuser": secrets.token_urlsafe(12)}
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
