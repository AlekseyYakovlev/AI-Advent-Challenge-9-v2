"""Browser check of the knowledge-base feature on an isolated copy of the app.

Usage: python scripts/e2e_kb_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory, drives it with headless
Chromium against the real LM Studio and the two real PDFs in C:\\Projects\\RAG, and prints one
PASS/FAIL line per check. The ports 8000/8001 of a running app are never touched and only
processes started by this script are ever stopped.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy, LM Studio
or the PDFs missing), 4 Playwright not installed.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

HOST: str = "127.0.0.1"
UI_PORT: int = 18000
AGENT_PORT: int = 18001
UI_URL: str = f"http://localhost:{UI_PORT}"
AGENT_URL: str = f"http://localhost:{AGENT_PORT}"
LM_URL: str = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")
NOMIC: str = "text-embedding-nomic-embed-text-v1.5"
GIGA: str = "giga-embeddings-instruct-480m-0826"
RAG_DIR: Path = Path(r"C:\Projects\RAG")
FZ_PDF: Path = RAG_DIR / "19951210_20260626_FZ_N_196_FZ.pdf"
KOAP_PDF: Path = RAG_DIR / (
    "Kodex_ot_30_12_2001_N_195-FZ_Kodex_Rossiyskoy_Federatsii_ob_administrativnyh_"
    "pravonarusheniyah_s..._Text.pdf"
)
FZ_QUERY: str = "С какого возраста можно получить право на управление транспортным средством?"
KOAP_QUERY: str = "Какой штраф за превышение скорости?"
STARTUP_TIMEOUT: float = 60.0
SUPERVISOR_STOP_TIMEOUT: float = 10.0
STEP_TIMEOUT_MS: int = 20000
SMALL_READY_TIMEOUT: float = 300.0
KOAP_READY_TIMEOUT: float = 900.0
COPY_IGNORE: tuple[str, ...] = (
    ".git", ".claude", ".planning", ".env", "scripts", "*.db", "*.db-*", "tests", "docs", "logs",
    "__pycache__", ".pytest_cache", "*.pyc", "test_*.db*", "*_kb",
)

EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

checks: list[tuple[str, bool, str]] = []
durations: dict[str, float] = {}
page_errors: list[str] = []
app_proc_holder: dict[str, Any] = {}


def report(label: str, passed: bool, detail: str = "") -> bool:
    """Record and print one check result."""
    checks.append((label, passed, detail))
    suffix = f" - {detail}" if detail else ""
    print(f"{'PASS' if passed else 'FAIL'}: {label}{suffix}", flush=True)
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
    busy = [p for p in (UI_PORT, AGENT_PORT) if not port_is_free(p)]
    if busy:
        print(f"BLOCKED: port(s) {busy} are in use; the isolated copy needs them free.")
        return EXIT_BLOCKED
    missing = [str(p) for p in (FZ_PDF, KOAP_PDF) if not p.exists()]
    if missing:
        print(f"BLOCKED: real PDFs not found: {missing}")
        return EXIT_BLOCKED
    try:
        data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
    except (httpx.HTTPError, ValueError) as exc:
        print(f"BLOCKED: LM Studio not available ({type(exc).__name__})")
        return EXIT_BLOCKED
    nomic = [m for m in data if m.get("id") == NOMIC and m.get("type") == "embeddings"]
    if not nomic:
        print(f"BLOCKED: LM Studio does not list {NOMIC} with type embeddings")
        return EXIT_BLOCKED
    return None


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
    (target / ".env").write_text("", encoding="utf-8")
    return target


async def seed_scratch_users(db_path: Path, users: dict[str, str]) -> dict[str, int]:
    """Create the scratch database and its users; never touches the repo's app.db."""
    os.environ["DB_PATH"] = str(db_path)
    from sqlmodel import select

    from shared.auth import hash_password
    from shared.config import settings
    from shared.database import async_session_factory, engine, init_db
    from shared.models import User

    if Path(settings.DB_PATH).resolve() != db_path.resolve():
        raise RuntimeError("scratch DB_PATH was not applied")
    if db_path.resolve() == (REPO_ROOT / "app.db").resolve():
        raise RuntimeError("refusing to use the repository app.db")

    await init_db()
    ids: dict[str, int] = {}
    async with async_session_factory() as session:
        for name, password in users.items():
            session.add(User(username=name, password_hash=hash_password(password)))
        await session.commit()
        for name in users:
            row = (await session.exec(select(User).where(User.username == name))).one()
            ids[name] = row.id
    await engine.dispose()
    return ids


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


def make_fixtures(scratch: Path) -> dict[str, Path]:
    """Create the scan PDF and two files with identical content under different names."""
    import pymupdf

    fixtures: dict[str, Path] = {}
    scan = scratch / "scan.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 300, 300), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    doc.save(str(scan))
    doc.close()
    fixtures["scan"] = scan
    body = "Тестовый абзац для проверки загрузки файлов. " * 5
    for name in ("dup_a.txt", "dup_b.txt", "small.txt"):
        path = scratch / name
        path.write_text(body, encoding="utf-8")
        fixtures[name] = path
    return fixtures


def kb_row(page: Any, name: str) -> Any:
    """Locate the KB card whose title is exactly name."""
    return page.locator("#kb-list > div").filter(
        has=page.locator("span.font-semibold", has_text=re.compile(f"^{re.escape(name)}$")),
    )


async def row_text(page: Any, name: str) -> str:
    """Return the text of the KB card ('' when absent)."""
    row = kb_row(page, name)
    if await row.count() == 0:
        return ""
    return await row.first.inner_text()


async def wait_row(
    page: Any, name: str, needle: str | re.Pattern[str], timeout: float = 30.0,
) -> str:
    """Wait until the card text contains/matches needle; return the last observed text."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        text = await row_text(page, name)
        hit = needle.search(text) if isinstance(needle, re.Pattern) else needle in text
        if hit or (needle == "готово" and "ошибка" in text):
            return text
        await asyncio.sleep(1.0)
    return text


async def open_modal(page: Any) -> None:
    """Expand the KB panel if needed and open the create modal."""
    if await page.locator("#kb-panel-body").is_hidden():
        await page.click('[data-fold-toggle="kb-panel-body"]')
    await page.click("#btn-kb-new")
    await page.wait_for_selector("#kb-create-modal", state="visible")
    # The picker is rebuilt once the model list arrives; wait for the default to settle.
    await page.wait_for_function(
        f"document.getElementById('kb-embedding-model').value === '{NOMIC}'",
        timeout=30000,
    )


async def fill_modal(
    page: Any, name: str, files: list[Path], strategy: str = "fixed",
    size: str = "1000", overlap: str = "150", model: str | None = None,
) -> None:
    """Fill the create modal fields."""
    await page.fill("#kb-name", name)
    await page.set_input_files("#kb-files", [str(f) for f in files])
    await page.select_option("#kb-strategy", strategy)
    if strategy == "fixed":
        await page.fill("#kb-chunk-size", size)
        await page.fill("#kb-chunk-overlap", overlap)
    if model:
        if model == GIGA:
            await page.check("#kb-show-all-models")
        await page.select_option("#kb-embedding-model", model)


async def create_kb(
    page: Any, name: str, files: list[Path], strategy: str = "fixed", **kwargs: Any,
) -> None:
    """Open the modal, fill it and submit; the modal closes on success."""
    await open_modal(page)
    await fill_modal(page, name, files, strategy, **kwargs)
    await page.click("#btn-kb-submit")
    await page.wait_for_selector("#kb-create-modal", state="hidden", timeout=60000)


async def modal_error(page: Any) -> str:
    """Click submit and return the inline error text."""
    await page.click("#btn-kb-submit")
    await page.wait_for_function(
        "document.getElementById('kb-create-error').textContent.trim().length > 0",
        timeout=30000,
    )
    return (await page.locator("#kb-create-error").inner_text()).strip()


async def close_modal(page: Any) -> None:
    """Close the create modal through its X button."""
    await page.click("#btn-close-kb-create")
    await page.wait_for_selector("#kb-create-modal", state="hidden")


async def run_search(page: Any, name: str, query: str) -> list[list[str]]:
    """Open test search for a ready KB, run the query, return the span texts of each card."""
    await kb_row(page, name).first.get_by_role("button", name="Тест поиска").click()
    await page.wait_for_selector("#kb-search-modal", state="visible")
    await page.fill("#kb-search-query", query)
    await page.click("#btn-kb-search")
    await page.wait_for_function(
        "document.querySelectorAll('#kb-search-results > div').length > 0"
        " || document.getElementById('kb-search-status').textContent.includes('Ничего')"
        " || document.getElementById('kb-search-status').classList.contains('text-red-400')",
        timeout=120000,
    )
    cards = page.locator("#kb-search-results > div")
    out: list[list[str]] = []
    for i in range(await cards.count()):
        spans = cards.nth(i).locator("div.text-xs > span")
        out.append([await spans.nth(j).inner_text() for j in range(await spans.count())])
    return out


async def close_search(page: Any) -> None:
    """Close the search modal through its X button."""
    await page.click("#btn-close-kb-search")
    await page.wait_for_selector("#kb-search-modal", state="hidden")


def check_cards(cards: list[list[str]], source_part: str, need_section: bool) -> tuple[bool, str]:
    """Validate 5 result cards: rank, numeric score, source, section, chunk id."""
    if len(cards) != 5:
        return False, f"{len(cards)} cards"
    for spans in cards:
        if len(spans) < 4:
            return False, f"short card {spans}"
        try:
            float(spans[1])
        except ValueError:
            return False, f"score not numeric: {spans[1]}"
        if source_part not in spans[2]:
            return False, f"source {spans[2]!r}"
        if need_section and not (len(spans) >= 5 and "Статья" in spans[3]):
            return False, f"section missing {spans}"
        if not spans[-1].strip():
            return False, "empty chunk id"
    return True, f"top1 {cards[0]}"


async def login(page: Any, username: str, password: str) -> None:
    """Log in through the login form and wait for the chat page."""
    await page.goto(f"{UI_URL}/static/login.html")
    await page.fill("#login-username", username)
    await page.fill("#login-password", password)
    await page.click("#login-form button[type=submit]")
    await page.wait_for_url("**/static/index.html", timeout=15000)
    await page.wait_for_function("document.querySelector('#model-select') !== null")


async def api_kbs(page: Any) -> list[dict[str, Any]]:
    """List the KBs of the logged-in user through the page's cookie jar."""
    resp = await page.request.get(f"{AGENT_URL}/api/v1/kb")
    return await resp.json()


def agent_listener() -> psutil.Process | None:
    """Return the process listening on the Agent port when it descends from our app."""
    root = app_proc_holder.get("proc")
    if root is None:
        return None
    try:
        ancestors = {c.pid for c in psutil.Process(root.pid).children(recursive=True)}
        for conn in psutil.net_connections(kind="tcp"):
            if (conn.laddr and conn.laddr.port == AGENT_PORT
                    and conn.status == psutil.CONN_LISTEN and conn.pid in ancestors):
                return psutil.Process(conn.pid)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return None


async def poll_health(stop: asyncio.Event, log: list[tuple[float, int]]) -> None:
    """Poll the Agent /health once a second, recording latency and status until stopped."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        while not stop.is_set():
            started = time.monotonic()
            try:
                resp = await client.get(f"{AGENT_URL}/health")
                log.append((time.monotonic() - started, resp.status_code))
            except httpx.HTTPError:
                log.append((time.monotonic() - started, 0))
            await asyncio.sleep(1.0)


def vram_reading() -> str:
    """Return an nvidia-smi memory reading and the models LM Studio reports as loaded."""
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        smi = f"nvidia-smi unavailable ({type(exc).__name__})"
    try:
        data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
        loaded = [m["id"] for m in data if m.get("state") == "loaded"]
    except (httpx.HTTPError, ValueError):
        loaded = []
    return f"nvidia-smi {smi}; loaded in LM Studio: {loaded}"


async def timed_ready(page: Any, name: str, timeout: float) -> tuple[bool, str]:
    """Wait for «готово», record the duration, return (ok, row text)."""
    started = time.monotonic()
    text = await wait_row(page, name, "готово", timeout)
    durations[name] = time.monotonic() - started
    return "готово" in text, text


async def scenarios(
    page: Any, ctx_factory: Any, scratch: Path, creds: dict[str, str], ids: dict[str, int],
) -> None:
    """Run scenarios 1-12 in order."""
    fx = make_fixtures(scratch)
    kb_root = scratch / "e2e_kb_kb"
    u1, u2 = "kbuser1", "kbuser2"

    # 1
    await login(page, u1, creds[u1])
    await page.click('[data-fold-toggle="kb-panel-body"]')
    empty = await page.locator("#kb-list").inner_text()
    report("1 KB panel expands with an empty state", await page.locator("#kb-panel-body").is_visible(),
           empty.strip()[:80])

    # 2
    opts = await page.evaluate(
        "() => Array.from(document.querySelectorAll('#model-select option')).map(o => o.value + '|' + o.textContent)",
    )
    report("2 chat model picker hides the embeddings model", not any(NOMIC in o for o in opts),
           f"{len(opts)} options")

    # 3
    await open_modal(page)
    default_model = await page.input_value("#kb-embedding-model")
    await page.check("#kb-show-all-models")
    await page.select_option("#kb-embedding-model", GIGA)
    await page.click("#btn-kb-embed-check")
    await page.wait_for_function(
        "document.getElementById('kb-embed-check-result').textContent.trim().length > 0", timeout=60000,
    )
    giga_msg = await page.locator("#kb-embed-check-result").inner_text()
    await page.select_option("#kb-embedding-model", NOMIC)
    await page.click("#btn-kb-embed-check")
    await page.wait_for_function(
        "document.getElementById('kb-embed-check-result').textContent.includes('Размерность')",
        timeout=120000,
    )
    nomic_msg = await page.locator("#kb-embed-check-result").inner_text()
    report("3 default model is nomic; giga check fails (D-24); nomic check gives dim 768",
           default_model == NOMIC and "не поддерживает эмбеддинги" in giga_msg and "Размерность: 768" in nomic_msg,
           f"default={default_model}; giga={giga_msg!r}; nomic={nomic_msg!r}")
    await close_modal(page)

    # 4
    await open_modal(page)
    await fill_modal(page, "val", [fx["small.txt"]], "fixed", "50", "10")
    e_small = await modal_error(page)
    await page.fill("#kb-chunk-size", "2500")
    e_large = await modal_error(page)
    await page.fill("#kb-chunk-size", "1000")
    await page.fill("#kb-chunk-overlap", "600")
    e_overlap = await modal_error(page)
    await page.set_input_files("#kb-files", [str(fx["dup_a.txt"]), str(fx["dup_b.txt"])])
    await page.fill("#kb-chunk-overlap", "150")
    e_dup = await modal_error(page)
    still_open = await page.locator("#kb-create-modal").is_visible()
    listed = await page.locator("#kb-file-list > li").count()
    report("4 inline validation keeps the modal open",
           "не меньше 100" in e_small and "не должен превышать 2000" in e_large
           and "Перекрытие" in e_overlap and "уже добавлен" in e_dup and still_open and listed >= 2,
           f"{e_small!r} | {e_large!r} | {e_overlap!r} | {e_dup!r} | files listed={listed}")
    await close_modal(page)

    # 5 / 6
    await create_kb(page, "fz-fixed", [FZ_PDF], "fixed")
    first = await wait_row(page, "fz-fixed", re.compile("в очереди|индексация|разбор|загрузка"), 30)
    ok, text = await timed_ready(page, "fz-fixed", SMALL_READY_TIMEOUT)
    match = re.search(r"\d+ файл\w* · (\d+) чанк\w*", text)
    report("5 ФЗ-196 fixed reaches «готово» with file and chunk counts",
           ok and bool(match) and int(match.group(1)) > 0,
           f"first={first.splitlines()[:2]}; {text.replace(chr(10), ' | ')}; {durations['fz-fixed']:.0f}s")

    await create_kb(page, "fz-struct", [FZ_PDF], "structural")
    ok, text = await timed_ready(page, "fz-struct", SMALL_READY_TIMEOUT)
    disabled_ok = await kb_row(page, "scan-pending").count() == 0
    cards = await run_search(page, "fz-struct", FZ_QUERY)
    good, detail = check_cards(cards, "196", True)
    toggle = page.locator("#kb-search-results > div").first.locator("button[aria-expanded]")
    await toggle.click()
    expanded = await toggle.get_attribute("aria-expanded")
    await close_search(page)
    report("6 ФЗ-196 structural ready; test search returns 5 cards; expand toggles",
           ok and good and expanded == "true" and disabled_ok,
           f"{detail}; {durations['fz-struct']:.0f}s")

    # 7
    health: list[tuple[float, int]] = []
    stop = asyncio.Event()
    poller = asyncio.create_task(poll_health(stop, health))
    await create_kb(page, "koap-fixed", [KOAP_PDF], "fixed")
    seen_progress = False
    vram = ""
    started = time.monotonic()
    deadline = started + KOAP_READY_TIMEOUT
    text = ""
    while time.monotonic() < deadline:
        text = await row_text(page, "koap-fixed")
        if re.search(r"индексация \d+ из \d+", text):
            seen_progress = True
            if not vram:
                vram = vram_reading()
        if "готово" in text or "ошибка" in text:
            break
        await asyncio.sleep(1.0)
    durations["koap-fixed"] = time.monotonic() - started
    stop.set()
    await poller
    slow = [round(lat, 2) for lat, code in health if code != 200 or lat > 2.0]
    report("7a КоАП fixed: live progress seen and /health stays 200 within 2s while indexing",
           seen_progress and not slow and len(health) > 3 and "готово" in text,
           f"{len(health)} polls, bad={slow[:5]}, {durations['koap-fixed']:.0f}s; {text.replace(chr(10), ' | ')}")
    report("7b VRAM reading with the embedder loaded (D-18 item c)", bool(vram), vram)

    await create_kb(page, "koap-struct", [KOAP_PDF], "structural")
    ok, text = await timed_ready(page, "koap-struct", KOAP_READY_TIMEOUT)
    cards = await run_search(page, "koap-struct", KOAP_QUERY)
    good, detail = check_cards(cards, "Kodex", True)
    await close_search(page)
    report("7c КоАП structural ready; test search returns 5 cards with sections",
           ok and good, f"{detail}; {durations['koap-struct']:.0f}s; {text.replace(chr(10), ' | ')}")

    # 8
    await create_kb(page, "scan-kb", [fx["scan"]], "fixed")
    text = await wait_row(page, "scan-kb", "ошибка", 120)
    await kb_row(page, "scan-kb").first.locator("button[aria-expanded]").click()
    text = await row_text(page, "scan-kb")
    report("8 scan PDF fails with the scan message",
           "Возможно, это скан без текстового слоя" in text, text.replace("\n", " | ")[:200])

    # 9
    await create_kb(page, "giga-kb", [fx["small.txt"]], "fixed", model=GIGA)
    text = await wait_row(page, "giga-kb", "ошибка", 120)
    text = await row_text(page, "giga-kb")
    report("9 giga-embeddings KB ends in «ошибка» with the D-24 message",
           "ошибка" in text and "эмбеддинг" in text, text.replace("\n", " | ")[:200])

    # 10
    await create_kb(page, "orphan-kb", [KOAP_PDF], "fixed")
    await wait_row(page, "orphan-kb", re.compile(r"индексация \d+ из \d+|разбор|загрузка"), 120)
    proc = agent_listener()
    killed = False
    if proc is not None:
        proc.kill()
        killed = True
    recovered = False
    deadline = time.monotonic() + 60
    async with httpx.AsyncClient(timeout=2.0) as client:
        await asyncio.sleep(3)
        while time.monotonic() < deadline:
            try:
                if (await client.get(f"{AGENT_URL}/health")).status_code == 200:
                    recovered = True
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    if await page.locator("#kb-panel-body").is_hidden():
        await page.click('[data-fold-toggle="kb-panel-body"]')
    text = await wait_row(page, "orphan-kb", "ошибка", 60)
    await kb_row(page, "orphan-kb").first.locator("button[aria-expanded]").click()
    text = await row_text(page, "orphan-kb")
    report("10 killing the Agent mid-job: supervisor restarts it and the KB shows the restart error",
           killed and recovered and "прервана перезапуском" in text,
           f"killed={killed} recovered={recovered}; {text.replace(chr(10), ' | ')[:200]}")

    # 11
    await create_kb(page, "delmid-kb", [KOAP_PDF], "fixed")
    await wait_row(page, "delmid-kb", re.compile(r"индексация \d+ из \d+|разбор|загрузка"), 120)
    kbs = {k["name"]: k["id"] for k in await api_kbs(page)}
    mid_dir = kb_root / str(ids[u1]) / str(kbs["delmid-kb"])
    existed = mid_dir.exists()
    await kb_row(page, "delmid-kb").first.get_by_role("button", name="Удалить").click()
    await kb_row(page, "delmid-kb").first.get_by_role("button", name="Точно удалить?").click()
    await page.wait_for_function(
        "name => !Array.from(document.querySelectorAll('#kb-list span.font-semibold')).some(s => s.textContent === name)",
        arg="delmid-kb", timeout=30000,
    )
    await asyncio.sleep(2)
    mid_gone = not mid_dir.exists()
    ready_id = kbs["fz-struct"]
    ready_dir = kb_root / str(ids[u1]) / str(ready_id)
    ready_existed = ready_dir.exists()
    report("11a delete during indexing removes the row and the directory",
           existed and mid_gone, f"dir existed={existed}, removed={mid_gone}")

    # 12 (before deleting fz-struct)
    ctx2 = await ctx_factory()
    page2 = await ctx2.new_page()
    await login(page2, u2, creds[u2])
    other_list = await api_kbs(page2)
    resp = await page2.request.get(f"{AGENT_URL}/api/v1/kb/{ready_id}")
    report("12 second user sees no KBs and gets 404 for the first user's KB",
           other_list == [] and resp.status == 404, f"list={other_list} status={resp.status}")
    await ctx2.close()

    await kb_row(page, "fz-struct").first.get_by_role("button", name="Удалить").click()
    await kb_row(page, "fz-struct").first.get_by_role("button", name="Точно удалить?").click()
    await page.wait_for_function(
        "name => !Array.from(document.querySelectorAll('#kb-list span.font-semibold')).some(s => s.textContent === name)",
        arg="fz-struct", timeout=30000,
    )
    toasts = await page.evaluate("window.__toasts || []")
    await asyncio.sleep(1)
    report("11b delete of a ready KB removes the row, the toast and the directory",
           ready_existed and not ready_dir.exists() and "База знаний удалена" in " ".join(toasts),
           f"dir existed={ready_existed}, removed={not ready_dir.exists()}")

    report("no browser page errors", not page_errors, "; ".join(page_errors[:3]))


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


async def drive_browser(creds: dict[str, str], ids: dict[str, int], scratch: Path) -> None:
    """Launch Chromium and run the scenarios with a screenshot on failure."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()

        async def ctx_factory() -> Any:
            return await browser.new_context()

        page = await context.new_page()
        await page.add_init_script(TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await scenarios(page, ctx_factory, scratch, creds, ids)
        except Exception as exc:
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()


async def teardown(app_proc: asyncio.subprocess.Process | None) -> None:
    """Stop only what this script started: the copy's supervisor tree."""
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
        # The supervisor may have respawned the Agent after the snapshot.
        for proc in psutil.Process().children(recursive=True):
            if proc.pid == app_proc.pid:
                continue
    await asyncio.sleep(1.0)
    report("isolated ports are free again", port_is_free(UI_PORT) and port_is_free(AGENT_PORT))


async def run_e2e() -> None:
    """Start the isolated copy, run the browser scenarios and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="kb_e2e_"))
    db_path = scratch / "e2e_kb.db"
    creds = {"kbuser1": secrets.token_urlsafe(12), "kbuser2": secrets.token_urlsafe(12)}
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    try:
        copy_dir = prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        ids = await seed_scratch_users(db_path, creds)
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
        app_proc_holder["proc"] = app_proc
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await wait_for_app(client)
        report("isolated app instance is up (UI :18000 + Agent :18001)", ready)
        if ready:
            await asyncio.wait_for(drive_browser(creds, ids, scratch), 5400)
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await teardown(app_proc)
        if log_file is not None:
            log_file.close()
        print("durations (s):", {k: round(v) for k, v in durations.items()})
        failed = sum(1 for _l, p, _d in checks if not p)
        print(f"summary: {len(checks) - failed} passed, {failed} failed")
        results = scratch / "results.json"
        results.write_text(
            json.dumps([{"check": c, "passed": p, "detail": d} for c, p, d in checks],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        keep = failed > 0
        if keep:
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
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
