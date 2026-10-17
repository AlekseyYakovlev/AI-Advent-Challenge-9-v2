"""Browser check of the project files assistant with a real local model on an isolated copy.

Usage: python scripts/e2e_project_files_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database, clones this repository into a scratch folder and adds the
bundled `project-files` server through the «MCP серверы» form with that clone as `--root` and
`--allow-write`. A goal typed as an ordinary chat message has to make the real LM Studio model
call the file tools: the write card must show the coloured «Diff изменения» region and the clone
must contain the new file. Model-independent render checks call `buildToolCallCard` in the page
with a fixed diff. The ports of a running app (8000/8001) and the working copy are never touched;
only processes started by this script are ever stopped, and the write root is the scratch clone.

Exit codes: 0 pass, 1 fail, 2 not run (ports 18000/18001 busy, git missing, or the model part is
unavailable: LM Studio unreachable / no loaded chat model), 4 Playwright not installed.
"""

import asyncio
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_kb_playwright as kb  # noqa: E402

REPO_ROOT: Path = kb.REPO_ROOT
UI_PORT: int = 18000
AGENT_PORT: int = 18001
UI_URL: str = kb.UI_URL
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
SERVER_NAME: str = "project-files"
WRITE_TOOLS: tuple[str, ...] = ("write_file", "edit_file")
READ_TOOLS: tuple[str, ...] = ("search_text", "find_usages")
TARGET_FILE: str = "docs/notes/e2e-files-check.md"
WRITE_GOAL: str = (
    "Создай файл docs/notes/e2e-files-check.md: в нём заголовок и два-три предложения о том, "
    "какие MCP серверы лежат в папке mcp_servers. Сначала посмотри файлы этой папки. "
    "В конце перечисли созданные или изменённые файлы."
)
READ_GOAL: str = "Где в проекте используется функция cleanup_chat_caches? Ничего не меняй."
PREFERRED_MODEL: str = "qwen/qwen3.5-9b"
ANSWER_TIMEOUT_MS: int = 600000
STEP_TIMEOUT_MS: int = 20000
MAX_ATTEMPTS: int = 2
DIFF_REGION: str = '[role="region"][aria-label="Diff изменения"]'
OUT_FILE: Path = REPO_ROOT / "eval_out" / "day34" / "e2e.md"
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

report = kb.report
checks = kb.checks
chat_model: dict[str, str] = {}
page_errors: list[str] = []
model_state: dict[str, Any] = {"available": True, "reason": ""}

RENDER_JS: str = """
() => {
  const diff = [
    'Updated docs/x.sql (+2 -2)',
    '--- a/docs/x.sql',
    '+++ b/docs/x.sql',
    '@@ -1,3 +1,3 @@',
    ' keep',
    '-old line',
    '--- removed sql comment',
    '+new line',
    '+<img src=x onerror=alert(1)>',
  ];
  const text = diff.join('\\n');
  const okCard = buildToolCallCard({ok: true, server: 'project-files', tool: 'edit_file',
    arguments: '{}', result: text, truncated: false});
  const failCard = buildToolCallCard({ok: false, server: 'project-files', tool: 'edit_file',
    arguments: '{}', result: text, truncated: false});
  const plainCard = buildToolCallCard({ok: true, server: 'project-files', tool: 'read_file',
    arguments: '{}', result: 'just some plain text', truncated: false});
  const region = okCard.querySelector('[aria-label="Diff изменения"]');
  const rows = region ? Array.from(region.children).map(r => ({text: r.textContent, cls: r.className})) : [];
  return {
    rows,
    summary: okCard.querySelector('summary').textContent,
    failRegion: failCard.querySelectorAll('[aria-label="Diff изменения"]').length,
    plainRegion: plainCard.querySelectorAll('[aria-label="Diff изменения"]').length,
    imgs: okCard.querySelectorAll('img').length,
  };
}
"""


def pick_chat_model() -> str | None:
    """Return the preferred loaded chat model, else the first loaded non-embedding one."""
    data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
    loaded = [m["id"] for m in data if m.get("state") == "loaded" and m.get("type") != "embeddings"]
    if PREFERRED_MODEL in loaded:
        return PREFERRED_MODEL
    return loaded[0] if loaded else None


def blocked_exit(reason: str) -> int:
    """Print the blocked reason and the NOT RUN result line, return the blocked exit code."""
    print(f"BLOCKED: {reason}", flush=True)
    print(f"RESULT: NOT RUN ({reason})", flush=True)
    return EXIT_BLOCKED


def preflight() -> int | None:
    """Return an exit code when the run must not start; mark the model part unavailable if so."""
    busy = [p for p in (UI_PORT, AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        return blocked_exit(f"port(s) {busy} are in use; the isolated copy needs them free")
    if shutil.which("git") is None:
        return blocked_exit("git is not installed")
    try:
        model = pick_chat_model()
    except (httpx.HTTPError, ValueError) as exc:
        model_state.update(available=False, reason=f"LM Studio not available ({type(exc).__name__})")
        return None
    if model is None:
        model_state.update(available=False, reason="no loaded chat model in LM Studio")
        return None
    chat_model["id"] = model
    print(f"chat model: {model}", flush=True)
    return None


def git_env() -> dict[str, str]:
    """Return the process environment without any GIT_* variable."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git_status(repo: Path) -> str:
    """Return `git status --porcelain` of a repository."""
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        env=git_env(), capture_output=True, text=True, timeout=60, check=True,
    )
    return result.stdout


def ensure_scratch_clone(path: Path) -> None:
    """Refuse any write root that is the real repository, inside it, or not the scratch clone."""
    resolved = path.resolve()
    real = REPO_ROOT.resolve()
    assert resolved != real and real not in resolved.parents, f"{resolved} is inside REPO_ROOT"
    assert resolved.name == "files-root", f"{resolved} is not the scratch clone"


def make_clone(scratch: Path) -> Path:
    """Clone this repository into the scratch folder without hardlinks."""
    clone = scratch / "files-root"
    ensure_scratch_clone(clone)
    subprocess.run(
        ["git", "clone", "--local", "--no-hardlinks", str(REPO_ROOT), str(clone)],
        env=git_env(), check=True, capture_output=True, timeout=300,
    )
    return clone


async def open_settings(page: Any) -> None:
    """Open the Settings modal."""
    await page.click("#btn-settings")
    await page.wait_for_selector("#settings-modal", state="visible")


async def add_server(page: Any, script_path: Path, clone: Path) -> bool:
    """Add the project-files server through the «MCP серверы» form; True when it is listed."""
    ensure_scratch_clone(clone)
    await open_settings(page)
    await page.click("#btn-mcp-add")
    await page.wait_for_selector("#mcp-server-form", state="visible")
    await page.fill("#mcp-name", SERVER_NAME)
    await page.fill("#mcp-command", sys.executable)
    await page.fill("#mcp-args", f"{script_path}\n--root={clone}\n--allow-write")
    await page.fill("#mcp-cwd", str(clone))
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


async def render_checks(page: Any) -> bool:
    """Model-independent checks of the diff card rendering through buildToolCallCard."""
    data = await page.evaluate(RENDER_JS)
    rows = {row["text"]: row["cls"] for row in data["rows"]}
    ok = True
    ok &= report("render: file header line is meta",
                 "text-slate-400" in rows.get("--- a/docs/x.sql", ""))
    ok &= report("render: hunk line has the hunk class",
                 "text-indigo-300" in rows.get("@@ -1,3 +1,3 @@", ""))
    ok &= report("render: removed line has the removed class",
                 "text-red-300" in rows.get("-old line", ""))
    ok &= report("render: removed line starting with `-- ` is removed, not meta",
                 "text-red-300" in rows.get("--- removed sql comment", ""))
    ok &= report("render: added line has the added class",
                 "text-green-300" in rows.get("+new line", ""))
    ok &= report("render: summary ends with the +A −R suffix",
                 data["summary"].endswith(" · +2 −2"), data["summary"])
    ok &= report("render: ok:false card has no diff region", data["failRegion"] == 0)
    ok &= report("render: plain text result has no diff region", data["plainRegion"] == 0)
    ok &= report("render: <img onerror> inside a diff line creates no img element",
                 data["imgs"] == 0, f"img count {data['imgs']}")
    return bool(ok)


def tool_cards(page: Any) -> Any:
    """Locate the tool-call cards of the chat."""
    return page.locator("details.tool-call-card")


def assistant_rows(page: Any) -> Any:
    """Locate the assistant message wrappers in the chat."""
    return page.locator("#messages > div.justify-start[data-message-id]")


async def select_chat_model(page: Any) -> None:
    """Select the chosen chat model in the header picker and wait for it to apply."""
    await page.wait_for_function(
        "id => Array.from(document.querySelectorAll('#model-select option'))"
        ".some(o => o.textContent.includes(id))",
        arg=chat_model["id"], timeout=60000,
    )
    value = await page.evaluate(
        "id => Array.from(document.querySelectorAll('#model-select option'))"
        ".find(o => o.textContent.includes(id)).value",
        chat_model["id"],
    )
    await page.select_option("#model-select", value)
    await asyncio.sleep(1.0)


async def wait_socket(page: Any) -> None:
    """Wait until the WebSocket belongs to the current chat."""
    await page.wait_for_function(
        "() => state.ws && state.ws.readyState === WebSocket.OPEN"
        " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
        timeout=15000,
    )


async def ask(page: Any, goal: str) -> tuple[int, list[str], str]:
    """Send a goal; return (cards before, texts of all cards after, last assistant text)."""
    cards_before = await tool_cards(page).count()
    rows_before = await assistant_rows(page).count()
    await wait_socket(page)
    await page.fill("#message-input", goal)
    await page.click("#btn-send")
    deadline = time.monotonic() + ANSWER_TIMEOUT_MS / 1000
    await asyncio.sleep(3.0)
    while time.monotonic() < deadline:
        rows = await assistant_rows(page).count()
        if rows > rows_before and await page.locator("#btn-send").is_enabled():
            break
        await asyncio.sleep(2.0)
    await asyncio.sleep(1.0)
    cards = tool_cards(page)
    texts = [(await cards.nth(i).text_content() or "").strip() for i in range(await cards.count())]
    answer = ""
    rows_loc = assistant_rows(page)
    if await rows_loc.count():
        answer = (await rows_loc.last.locator(".message-content").text_content()) or ""
    return cards_before, texts, answer.strip()


async def diff_regions_in_write_cards(page: Any) -> int:
    """Count diff regions inside cards of a project-files write tool (open state is irrelevant)."""
    return await page.evaluate(
        """([server, tools, region]) => Array.from(document.querySelectorAll('details.tool-call-card'))
            .filter(c => c.textContent.includes(server) && tools.some(t => c.textContent.includes(t)))
            .filter(c => c.querySelector(region)).length""",
        [SERVER_NAME, list(WRITE_TOOLS), DIFF_REGION],
    )


async def scenario(page: Any, clone: Path) -> tuple[bool, list[str]]:
    """Run one attempt in a new chat; return the overall result and a transcript."""
    transcript: list[str] = []
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "() => state.chats.length >= 1 && state.currentChatId === state.chats[0].id",
        timeout=15000,
    )
    first_chat = await page.evaluate("() => state.currentChatId")
    await wait_socket(page)
    await select_chat_model(page)

    before, texts, answer = await ask(page, WRITE_GOAL)
    transcript += [f"goal: {WRITE_GOAL}", *[f"card: {t[:200]}" for t in texts], f"answer: {answer}"]
    write_card = any(
        SERVER_NAME in t and any(w in t for w in WRITE_TOOLS) for t in texts[before:]
    )
    ok1 = report("goal in an ordinary chat: project-files write card is visible", write_card,
                 f"{len(texts) - before} new card(s)")
    ok2 = report("write card contains the «Diff изменения» region",
                 await diff_regions_in_write_cards(page) >= 1)
    changed = (clone / TARGET_FILE).is_file() or bool(git_status(clone).strip())
    ok3 = report("clone contains the new or changed file", changed, TARGET_FILE)

    cards_before, texts2, answer2 = await ask(page, READ_GOAL)
    transcript += [f"goal: {READ_GOAL}", *[f"card: {t[:200]}" for t in texts2[cards_before:]],
                   f"answer: {answer2}"]
    report("read goal: a search_text / find_usages card appeared (not required)",
           any(any(r in t for r in READ_TOOLS) for t in texts2[cards_before:]))

    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "id => state.currentChatId !== id", arg=first_chat, timeout=15000,
    )
    await page.click(f'#chat-list [data-chat-id="{first_chat}"]')
    await page.wait_for_function(
        "id => state.currentChatId === id", arg=first_chat, timeout=15000,
    )
    await asyncio.sleep(2.0)
    ok4 = report("diff card survives a chat switch within the session",
                 await diff_regions_in_write_cards(page) >= 1)
    return bool(ok1 and ok2 and ok3 and ok4), transcript


async def drive_browser(
    username: str, password: str, scratch: Path, copy_dir: Path, clone: Path,
) -> tuple[bool, bool]:
    """Log in, run render checks, then the model part; return (render ok, model attempt passed)."""
    from playwright.async_api import async_playwright

    render_ok = False
    model_ok = False
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await kb.login(page, username, password)
            render_ok = await render_checks(page)
            if not model_state["available"]:
                return render_ok, False
            script = copy_dir / "mcp_servers" / "project_git.py"
            if not report("project-files added through the «MCP серверы» form with --allow-write",
                          await add_server(page, script, clone), str(script)):
                return render_ok, False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                ok, transcript = await scenario(page, clone)
                if ok:
                    model_ok = True
                    break
                print(f"--- failed attempt {attempt} transcript ---", flush=True)
                print("\n".join(transcript), flush=True)
        except Exception as exc:  # noqa: BLE001 - any browser failure must be reported, not raised
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()
    return render_ok, model_ok


def write_out(result_line: str) -> None:
    """Write the recorded check lines and the RESULT line (no secrets) to OUT_FILE."""
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Project files assistant: isolated-copy e2e",
        "",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Model: {chat_model.get('id', 'not run')}",
        "",
        *[f"- {'PASS' if ok else 'FAIL'}: {label}" + (f" - {detail}" if detail else "")
          for label, ok, detail in checks],
        "",
        result_line,
        "",
    ]
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")


async def run_e2e() -> int:
    """Start the isolated copy, run the browser scenario, tear down; return the exit code."""
    scratch = Path(tempfile.mkdtemp(prefix="project_files_e2e_"))
    db_path = scratch / "e2e_project_files.db"
    creds = {"filesuser": secrets.token_urlsafe(12)}
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    render_ok = False
    model_ok = False
    own_before = git_status(REPO_ROOT)
    try:
        clone = make_clone(scratch)
        report("write root is a scratch clone outside the working copy", True, str(clone))
        copy_dir = kb.prepare_copy(scratch)
        report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
        await kb.seed_scratch_users(db_path, creds)
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
            render_ok, model_ok = await asyncio.wait_for(
                drive_browser("filesuser", creds["filesuser"], scratch, copy_dir, clone), 1500,
            )
    except Exception as exc:  # noqa: BLE001 - report any setup failure instead of crashing
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await kb.teardown(app_proc)
        if log_file is not None:
            log_file.close()
    unchanged = report("user's working copy unchanged", git_status(REPO_ROOT) == own_before)
    all_pass = render_ok and unchanged and (model_ok or not model_state["available"])
    if all_pass and model_ok:
        shutil.rmtree(scratch, ignore_errors=True)
    else:
        print(f"scratch kept for inspection: {scratch}", flush=True)
    model = chat_model.get("id", "not run")
    if not model_state["available"]:
        result = f"RESULT: NOT RUN (model part: {model_state['reason']})"
        code = EXIT_BLOCKED
    elif render_ok and model_ok and unchanged:
        result = f"RESULT: PASS (model {model})"
        code = EXIT_PASS
    else:
        result = f"RESULT: FAIL (model {model})"
        code = EXIT_FAIL
    print(result, flush=True)
    write_out(result)
    return code


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
