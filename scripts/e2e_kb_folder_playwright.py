"""Browser check of folder upload and file addition on an isolated copy of the app.

Usage: python scripts/e2e_kb_folder_playwright.py

Reuses the harness of scripts/e2e_kb_playwright.py: copies the working tree to a temporary
directory, repoints the copy to UI :18000 / Agent :18001, starts it with a scratch database and a
scratch KB storage directory and drives it with headless Chromium. Part A (no LM Studio) checks the
pending list, relative-path source names, the multipart file names and the limit state. Part B
(needs LM Studio with the nomic embedding model) creates a base from the list, adds a changed
folder to it and checks the replacement summary. The ports of a running app are never touched and
only processes started by this script are ever stopped.

Exit codes: 0 every check passed, 1 a check failed, 2 nothing ran because port 18000 or 18001 is
busy, 3 part A passed and part B was skipped (LM Studio or the embedding model is unavailable),
4 Playwright is not installed.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from e2e_kb_playwright import (
    AGENT_PORT,
    AGENT_URL,
    LM_URL,
    NOMIC,
    STEP_TIMEOUT_MS,
    UI_PORT,
    UI_URL,
    checks,
    close_search,
    kb_row,
    login,
    open_modal,
    page_errors,
    port_is_free,
    prepare_copy,
    report,
    row_text,
    run_search,
    seed_scratch_users,
    teardown,
    wait_for_app,
    wait_row,
)

from agent.kb_limits import MAX_FILES

EXIT_PASS, EXIT_FAIL, EXIT_PORTS_BUSY, EXIT_PART_B_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 3, 4

BASE_NAME: str = "docs-base"
OLD_MARKER: str = "маркер-первой-версии"
NEW_MARKER: str = "маркер-второй-версии"
EXPECTED_SOURCES: set[str] = {
    "README.md",
    "docs/GUIDE.md",
    "docs/notes.txt",
    "docs/a/README.md",
    "docs/b/README.md",
    "docs/.hidden/secret.md",
}
SAMPLE_INTERVAL: float = 0.5
ADD_TIMEOUT: float = 180.0
INDEX_TIMEOUT: float = 300.0


def make_fixtures(scratch: Path) -> dict[str, Path]:
    """Create README.md, the docs folder and the over-limit folder under the scratch directory."""
    readme = scratch / "README.md"
    readme.write_text("Корневой файл проекта. Он описывает назначение приложения.\n", encoding="utf-8")
    docs = scratch / "docs"
    files: dict[str, str] = {
        "GUIDE.md": f"# Руководство\n\nЭто первая версия руководства. {OLD_MARKER}.\n",
        "notes.txt": "Заметки о настройке окружения и запуске приложения.\n",
        "a/README.md": "# Раздел A\n\nОдинаковое содержимое двух файлов с одним именем.\n",
        "b/README.md": "# Раздел A\n\nОдинаковое содержимое двух файлов с одним именем.\n",
        ".hidden/secret.md": "# Скрытый каталог\n\nФайл лежит в каталоге с точкой в имени.\n",
        "img/logo.png": "not really a png",
        "data.json": "{\"unsupported\": true}",
    }
    for rel, text in files.items():
        path = docs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    many = scratch / "many"
    many.mkdir()
    for index in range(MAX_FILES + 1):
        (many / f"f{index:03d}.txt").write_text(f"Файл номер {index}.\n", encoding="utf-8")
    return {"readme": readme, "docs": docs, "many": many}


async def fetch_limits(page: Any) -> dict[str, Any]:
    """Read /api/v1/kb/limits from inside the page, as the page itself does."""
    return await page.evaluate(
        "async (url) => (await fetch(url, {credentials: 'include'})).json()",
        f"{AGENT_URL}/api/v1/kb/limits",
    )


async def expand_panel(page: Any) -> None:
    """Expand the knowledge base panel when it is folded."""
    if await page.locator("#kb-panel-body").is_hidden():
        await page.click('[data-fold-toggle="kb-panel-body"]')


async def choose_folder_substitute(page: Any, directory: Path, add: bool) -> None:
    """Page-side stand-in: build File objects with webkitRelativePath and feed the controller."""
    items = [
        {"path": f"{directory.name}/{p.relative_to(directory).as_posix()}", "text": p.read_text(encoding="utf-8")}
        for p in sorted(directory.rglob("*")) if p.is_file()
    ]
    await page.evaluate(
        """([items, key]) => {
            const files = items.map((item) => {
                const file = new File([item.text], item.path.split('/').pop());
                Object.defineProperty(file, 'webkitRelativePath', {value: item.path});
                return file;
            });
            state[key].addSelection(files, true);
        }""",
        [items, "kbAddPending" if add else "kbPending"],
    )


async def choose_folder(page: Any, directory: Path, add: bool = False) -> str:
    """Choose a directory in the modal and return the method that worked."""
    input_id = "#kb-add-folder" if add else "#kb-folder"
    label = 'label[for="kb-add-folder"]' if add else 'label[for="kb-folder"]'
    try:
        await page.set_input_files(input_id, str(directory))
        return "set_input_files"
    except Exception as exc:  # Playwright raises a plain Error for unsupported inputs
        first_error = f"{type(exc).__name__}: {exc}"
    try:
        async with page.expect_file_chooser() as info:
            await page.click(label)
        chooser = await info.value
        await chooser.set_files(str(directory))
        return "file_chooser"
    except Exception as exc:
        report(
            "A3 directory accepted on a webkitdirectory input", False,
            f"playwright {playwright_version()}; set_input_files: {first_error}; "
            f"file chooser: {type(exc).__name__}: {exc}",
        )
    await choose_folder_substitute(page, directory, add)
    return "page_side"


def playwright_version() -> str:
    """Return the installed Playwright version."""
    from importlib.metadata import version

    return version("playwright")


async def list_entries(page: Any, list_id: str = "#kb-file-list") -> list[dict[str, str]]:
    """Return label text and title attribute of each pending list entry."""
    return await page.evaluate(
        """(selector) => Array.from(document.querySelectorAll(selector + ' > li')).map((li) => {
            const span = li.querySelector('span');
            return {text: span.textContent, title: span.title};
        })""",
        list_id,
    )


async def lm_studio_ready() -> bool:
    """Return True when LM Studio lists the nomic embedding model with type embeddings."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            data = (await client.get(f"{LM_URL}/api/v0/models")).json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return False
    return any(m.get("id") == NOMIC and m.get("type") == "embeddings" for m in data)


def stored_filenames(db_path: Path, name: str) -> list[str]:
    """Read the document source names of a base from the scratch database (read-only)."""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT d.filename FROM kbdocument d JOIN knowledgebase k ON k.id = d.kb_id "
            "WHERE k.name = ?",
            (name,),
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


async def part_a(page: Any, fx: dict[str, Path], lm_ok: bool) -> int | None:
    """Run the checks that need no LM Studio; return the status of the create request."""
    await login(page, "folderuser", CREDS["folderuser"])
    await expand_panel(page)
    await page.click("#btn-kb-new")
    await page.wait_for_selector("#kb-create-modal", state="visible")
    limits = await fetch_limits(page)
    await page.wait_for_function("document.getElementById('kb-limits-hint').textContent.includes('До ')")
    hint = await page.locator("#kb-limits-hint").inner_text()
    report("A1 limits hint equals GET /api/v1/kb/limits",
           f"До {limits['max_files']} файлов" in hint and limits["max_files"] == MAX_FILES, hint)

    await page.set_input_files("#kb-files", str(fx["readme"]))
    entries = await list_entries(page)
    report("A2 one file chosen gives one list entry named README.md",
           len(entries) == 1 and entries[0]["text"].startswith("README.md · "), str(entries))

    method = await choose_folder(page, fx["docs"])
    if method != "page_side":
        report("A3 directory accepted on a webkitdirectory input", True,
               f"{method}; playwright {playwright_version()}")
    folder_report = (await page.locator("#kb-folder-report").inner_text()).strip()
    entries = await list_entries(page)
    titles = {e["title"] for e in entries}
    labels_ok = all(e["text"].startswith(e["title"] + " · ") for e in entries)
    report("A3 folder entries carry relative paths and the taken/skipped report",
           folder_report == "Из папки «docs» взято 5 файлов, пропущено 2 файла неподдерживаемого типа."
           and len(entries) == 6 and titles == EXPECTED_SOURCES and labels_ok
           and not any("\\" in e["text"] for e in entries),
           f"{folder_report!r}; {sorted(titles)}")

    await choose_folder(page, fx["docs"])
    folder_report = (await page.locator("#kb-folder-report").inner_text()).strip()
    entries = await list_entries(page)
    report("A4 choosing the same folder again adds nothing",
           len(entries) == 6 and folder_report == "Уже в списке: 5 файлов — повторно не добавлено.",
           f"{len(entries)} entries; {folder_report!r}")

    await page.click('button[aria-label="Убрать файл README.md"]')
    count_text = await page.locator("#kb-file-count").inner_text()
    after_remove = len(await list_entries(page))
    await page.set_input_files("#kb-files", str(fx["readme"]))
    after_readd = len(await list_entries(page))
    clear_visible = await page.locator("#btn-kb-clear-list").is_visible()
    report("A5 removal and re-adding update the list and the header",
           after_remove == 5 and count_text.startswith("В списке: 5 файлов")
           and after_readd == 6 and clear_visible,
           f"{after_remove} -> {after_readd}; {count_text!r}; clear visible={clear_visible}")

    await page.fill("#kb-name", BASE_NAME)
    if lm_ok:
        try:
            await page.wait_for_function(
                f"document.getElementById('kb-embedding-model').value === '{NOMIC}'", timeout=30000,
            )
        except Exception:
            print("note: the embedding model default did not settle before submit")
    async with page.expect_request(
        lambda r: r.method == "POST" and r.url.split("?")[0].rstrip("/").endswith("/api/v1/kb"),
    ) as info:
        await page.click("#btn-kb-submit")
    request = await info.value
    body = (request.post_data_buffer or b"").decode("utf-8", errors="replace")
    source = "playwright post_data_buffer"
    if not body:
        # Chromium does not expose bodies that contain File parts to Playwright, so read the
        # multipart text the browser itself encoded from the same FormData.
        recorded = await page.evaluate("window.__multipart")
        body = next((r["text"] for r in reversed(recorded) if r["url"].rstrip("/").endswith("/api/v1/kb")), "")
        source = "browser-encoded FormData"
    response = await request.response()
    status = response.status if response else None
    needed = [f'filename="{name}"' for name in
              ("docs/a/README.md", "docs/b/README.md", "docs/.hidden/secret.md", "README.md")]
    missing = [n for n in needed if n not in body]
    report("A6 the multipart request carries relative paths as file names",
           not missing, f"status={status}; body source={source}; missing={missing}; body_len={len(body)}")

    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await expand_panel(page)
    await page.click("#btn-kb-new")
    await page.wait_for_selector("#kb-create-modal", state="visible")
    await page.wait_for_function("document.getElementById('kb-limits-hint').textContent.includes('До ')")
    await choose_folder(page, fx["many"])
    count_text = await page.locator("#kb-file-count").inner_text()
    header_class = await page.locator("#kb-file-list-header").get_attribute("class") or ""
    submit_disabled = await page.locator("#btn-kb-submit").is_disabled()
    report("A7 a folder over the limit turns the header red and disables submit",
           f"Слишком много файлов: {MAX_FILES + 1} из допустимых {MAX_FILES}" in count_text
           and "text-red-400" in header_class and submit_disabled,
           f"{count_text[:80]!r}; disabled={submit_disabled}")
    await page.click("#btn-close-kb-create")
    await page.wait_for_selector("#kb-create-modal", state="hidden")

    report("A8 no browser page errors in part A", not page_errors, "; ".join(page_errors[:3]))
    return status


async def create_base_from_list(page: Any, fx: dict[str, Path]) -> None:
    """Create the docs base through the create modal when the part A request did not."""
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await open_modal(page)
    await page.fill("#kb-name", BASE_NAME)
    await page.set_input_files("#kb-files", str(fx["readme"]))
    await choose_folder(page, fx["docs"])
    await page.click("#btn-kb-submit")
    await page.wait_for_selector("#kb-create-modal", state="hidden", timeout=60000)


async def search_texts(page: Any, kb_id: int, query: str) -> list[str]:
    """Run the search route from inside the page and return the fragment texts."""
    data = await page.evaluate(
        """async ([url, query]) => {
            const resp = await fetch(url, {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query, top_k: 20}),
            });
            return resp.json();
        }""",
        [f"{AGENT_URL}/api/v1/kb/{kb_id}/search", query],
    )
    return [item.get("text", "") for item in data.get("results", [])]


async def part_b(
    page: Any, fx: dict[str, Path], db_path: Path, create_status: int | None,
) -> None:
    """Run the checks that need LM Studio: create, store, search, add, replace."""
    if create_status != 202:
        await create_base_from_list(page, fx)
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await expand_panel(page)
    text = await wait_row(page, BASE_NAME, "готово", INDEX_TIMEOUT)
    report("B1 the base reaches «готово» with 6 files",
           "готово" in text and "6 файлов" in text, text.replace("\n", " | "))

    names = stored_filenames(db_path, BASE_NAME)
    report("B2 the stored source names are the six relative paths",
           sorted(names) == sorted(EXPECTED_SOURCES) and len(names) == 6, str(sorted(names)))

    cards = await run_search(page, BASE_NAME, OLD_MARKER)
    await close_search(page)
    report("B3 test search finds docs/GUIDE.md for the first marker",
           any("docs/GUIDE.md" in " ".join(spans) for spans in cards), str(cards[:2]))

    guide = fx["docs"] / "GUIDE.md"
    guide.write_text(f"# Руководство\n\nЭто вторая версия руководства. {NEW_MARKER}.\n", encoding="utf-8")
    (fx["docs"] / "NEW.md").write_text("# Новый документ\n\nДобавлен после создания базы.\n", encoding="utf-8")

    await kb_row(page, BASE_NAME).first.get_by_role("button", name="Добавить файлы").click()
    await page.wait_for_selector("#kb-add-modal", state="visible")
    title = await page.locator("#kb-add-title").inner_text()
    selects = await page.locator("#kb-add-modal select").count()
    await page.wait_for_function("document.getElementById('kb-add-limits-hint').textContent.includes('До ')")
    await choose_folder(page, fx["docs"], add=True)
    await page.click("#btn-kb-add-submit")

    samples: list[str] = []
    search_disabled: list[bool] = []
    deadline = time.monotonic() + ADD_TIMEOUT
    while time.monotonic() < deadline:
        card = await row_text(page, BASE_NAME)
        samples.append(card)
        button = kb_row(page, BASE_NAME).first.get_by_role("button", name="Тест поиска")
        search_disabled.append(await button.is_disabled())
        if "Добавлено:" in card:
            break
        await asyncio.sleep(SAMPLE_INTERVAL)
    bad = [s for s in samples if re.search("индексация|в очереди|ошибка", s) or "готово" not in s]
    report("B4 the add modal names the base, has no select, and the card never leaves «готово»",
           BASE_NAME in title and selects == 0 and samples and not bad and not any(search_disabled),
           f"title={title!r}; selects={selects}; samples={len(samples)}; bad={bad[:1]}")

    card = samples[-1] if samples else ""
    names = stored_filenames(db_path, BASE_NAME)
    report("B5 the summary reads added 1, replaced 1, unchanged 4 and the table matches",
           "Добавлено: 1, заменено: 1, без изменений: 4" in card and "7 файлов" in card
           and names.count("docs/GUIDE.md") == 1 and len(names) == 7,
           f"{card.replace(chr(10), ' | ')}; rows={len(names)}")

    cards = await run_search(page, BASE_NAME, NEW_MARKER)
    await close_search(page)
    kb_id = next(k["id"] for k in await page.evaluate(
        "async (url) => (await fetch(url, {credentials: 'include'})).json()", f"{AGENT_URL}/api/v1/kb",
    ) if k["name"] == BASE_NAME)
    old_hits = [t for t in await search_texts(page, kb_id, OLD_MARKER) if OLD_MARKER in t]
    report("B6 the new marker is found in docs/GUIDE.md and the old one is gone",
           any("docs/GUIDE.md" in " ".join(spans) for spans in cards) and not old_hits,
           f"cards={len(cards)}; old marker hits={len(old_hits)}")
    report("B7 no browser page errors", not page_errors, "; ".join(page_errors[:3]))


async def drive_browser(
    scratch: Path, db_path: Path, fx: dict[str, Path], lm_ok: bool,
) -> bool:
    """Launch Chromium, run part A and, when LM Studio is available, part B."""
    from playwright.async_api import async_playwright

    ran_b = False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        await page.add_init_script(MULTIPART_RECORDER_JS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            status = await part_a(page, fx, lm_ok)
            if lm_ok:
                ran_b = True
                await part_b(page, fx, db_path, status)
        except Exception as exc:
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()
    return ran_b


MULTIPART_RECORDER_JS: str = """
window.__multipart = [];
(() => {
  const original = window.fetch;
  window.fetch = async function (input, init) {
    try {
      if (init && init.body instanceof FormData) {
        const text = await new Response(init.body).text();
        window.__multipart.push({url: String(input), text});
      }
    } catch (error) {
      window.__multipart.push({url: String(input), text: ''});
    }
    return original.apply(this, arguments);
  };
})();
"""

CREDS: dict[str, str] = {"folderuser": secrets.token_urlsafe(12)}


async def run_e2e(lm_ok: bool) -> bool:
    """Start the isolated copy, run the browser checks and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="kb_folder_e2e_"))
    db_path = scratch / "e2e_kbf.db"
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    ran_b = False
    try:
        fx = make_fixtures(scratch)
        copy_dir = prepare_copy(scratch)
        report("isolated copy patched to the isolated ports", True, str(copy_dir))
        await seed_scratch_users(db_path, CREDS)
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
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await wait_for_app(client)
        report("isolated app instance is up", ready)
        if ready:
            ran_b = await asyncio.wait_for(drive_browser(scratch, db_path, fx, lm_ok), 1800)
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        from e2e_kb_playwright import app_proc_holder

        app_proc_holder["proc"] = app_proc
        await teardown(app_proc)
        if log_file is not None:
            log_file.close()
        failed = sum(1 for _l, ok, _d in checks if not ok)
        print(f"summary: {len(checks) - failed} passed, {failed} failed", flush=True)
        (scratch / "results.json").write_text(
            json.dumps([{"check": c, "passed": p, "detail": d} for c, p, d in checks],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if failed:
            print(f"scratch kept for inspection: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    return ran_b


def main() -> None:
    """Check the ports, run the isolated checks and exit with the result code."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed (pip install playwright && playwright install chromium).")
        sys.exit(EXIT_NO_PLAYWRIGHT)

    busy = [p for p in (UI_PORT, AGENT_PORT) if not port_is_free(p)]
    if busy:
        print(f"BLOCKED: port(s) {busy} are in use; the isolated copy needs them free.")
        sys.exit(EXIT_PORTS_BUSY)

    lm_ok = asyncio.run(lm_studio_ready())
    ran_b = asyncio.run(run_e2e(lm_ok))
    if any(not passed for _l, passed, _d in checks):
        sys.exit(EXIT_FAIL)
    if not ran_b:
        print("BLOCKED: part B skipped (LM Studio or the embedding model is unavailable)")
        sys.exit(EXIT_PART_B_BLOCKED)
    sys.exit(EXIT_PASS)


if __name__ == "__main__":
    main()
