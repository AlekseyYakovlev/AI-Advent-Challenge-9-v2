"""Browser check of the project git MCP server with a real local model on an isolated copy.

Usage: python scripts/e2e_mcp_git_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database, adds the bundled `local-git` server through the «MCP серверы»
form (pointed at a temporary repository created by this script), asks the real LM Studio model
which branch the project is on, switches the branch in that temporary repository and asks again.
The answer has to follow the switch and a tool-call card has to be visible for both questions.
The ports 8000/8001 of a running app and the working copy's branch are never touched; only
processes started by this script are ever stopped.

Exit codes: 0 pass, 1 fail, 2 blocked (ports 18000/18001 busy, LM Studio unreachable, no loaded
chat model, git missing), 4 Playwright not installed.
"""

import asyncio
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
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
SERVER_NAME: str = "local-git"
TOOL_NAME: str = "git_current_branch"
CARD_NAME: str = "mcp__local-git__git_current_branch"
BRANCH_ONE: str = "alpha"
BRANCH_TWO: str = "beta"
QUESTION_ONE: str = "на какой ветке проект?"
QUESTION_TWO: str = "я переключил ветку. на какой ветке проект сейчас?"
PREFERRED_MODEL: str = "qwen/qwen3.5-9b"
ANSWER_TIMEOUT_MS: int = 180000
STEP_TIMEOUT_MS: int = 20000
MAX_ATTEMPTS: int = 2
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

report = kb.report
checks = kb.checks
chat_model: dict[str, str] = {}
page_errors: list[str] = []


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
    """Return an exit code when the run must not start, else None."""
    busy = [p for p in (UI_PORT, AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        return blocked_exit(f"port(s) {busy} are in use; the isolated copy needs them free")
    if shutil.which("git") is None:
        return blocked_exit("git is not installed")
    try:
        model = pick_chat_model()
    except (httpx.HTTPError, ValueError) as exc:
        return blocked_exit(f"LM Studio not available ({type(exc).__name__})")
    if model is None:
        return blocked_exit("no loaded chat model in LM Studio")
    chat_model["id"] = model
    print(f"chat model: {model}", flush=True)
    return None


def git_env() -> dict[str, str]:
    """Return the process environment without any GIT_* variable."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def ensure_scratch_repo(repo: Path) -> None:
    """Refuse any repository that is outside the scratch directory tree or inside the real repo."""
    resolved = repo.resolve()
    real = REPO_ROOT.resolve()
    assert resolved != real and real not in resolved.parents, f"{resolved} is inside REPO_ROOT"
    assert resolved.name == "branch-repo", f"{resolved} is not the scratch repository"


def run_git(repo: Path, *args: str) -> None:
    """Run one git command in the scratch repository with a fixed identity."""
    ensure_scratch_repo(repo)
    cmd = [
        "git", "-c", "user.name=E2E", "-c", "user.email=e2e@local",
        "-c", "core.autocrlf=false", "-c", f"init.defaultBranch={BRANCH_ONE}", *args,
    ]
    subprocess.run(cmd, cwd=str(repo), env=git_env(), check=True, capture_output=True, timeout=30)


def make_temp_repo(scratch: Path) -> Path:
    """Create the scratch repository with branches alpha (checked out) and beta."""
    repo = scratch / "branch-repo"
    repo.mkdir()
    ensure_scratch_repo(repo)
    run_git(repo, "init")
    (repo / "notes.txt").write_text("fixture\n", encoding="utf-8")
    run_git(repo, "add", "notes.txt")
    run_git(repo, "commit", "-m", "initial")
    run_git(repo, "branch", BRANCH_TWO)
    return repo


def switch_branch(repo: Path, name: str) -> None:
    """Check out a branch in the scratch repository."""
    run_git(repo, "checkout", name)


async def open_settings(page: Any) -> None:
    """Open the Settings modal."""
    await page.click("#btn-settings")
    await page.wait_for_selector("#settings-modal", state="visible")


async def add_server(page: Any, script_path: Path, repo: Path) -> bool:
    """Add the local-git server through the «MCP серверы» form; True when it is listed."""
    await open_settings(page)
    await page.click("#btn-mcp-add")
    await page.wait_for_selector("#mcp-server-form", state="visible")
    await page.fill("#mcp-name", SERVER_NAME)
    await page.fill("#mcp-command", sys.executable)
    await page.fill("#mcp-args", f"{script_path}\n--root={repo}")
    await page.fill("#mcp-cwd", str(repo))
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


def branch_cards(page: Any) -> Any:
    """Locate the tool-call cards of the branch tool."""
    return page.locator("details.tool-call-card", has_text=TOOL_NAME)


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


async def wait_socket(page: Any) -> None:
    """Wait until the WebSocket belongs to the current chat."""
    await page.wait_for_function(
        "() => state.ws && state.ws.readyState === WebSocket.OPEN"
        " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
        timeout=15000,
    )


async def ask(page: Any, question: str) -> tuple[bool, str, str]:
    """Send a question; return (new card appeared, newest card text, last assistant text)."""
    cards_before = await branch_cards(page).count()
    rows_before = await assistant_rows(page).count()
    await wait_socket(page)
    await page.fill("#message-input", question)
    await page.click("#btn-send")
    deadline = time.monotonic() + ANSWER_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        cards = await branch_cards(page).count()
        rows = await assistant_rows(page).count()
        if cards > cards_before and rows > rows_before and await page.locator("#btn-send").is_enabled():
            break
        await asyncio.sleep(1.0)
    await asyncio.sleep(1.0)
    cards = branch_cards(page)
    appeared = await cards.count() > cards_before
    card_text = await cards.last.text_content() if await cards.count() else ""
    rows = assistant_rows(page)
    answer = ""
    if await rows.count():
        answer = (await rows.last.locator(".message-content").text_content()) or ""
    return appeared, (card_text or "").strip(), answer.strip()


def card_names_the_tool(card_text: str) -> bool:
    """True when the card names the tool: server and tool in the summary, or the full name."""
    return CARD_NAME in card_text or (SERVER_NAME in card_text and TOOL_NAME in card_text)


async def scenario(page: Any, repo: Path) -> tuple[bool, list[str]]:
    """Run one attempt in a new chat; return the overall result and a transcript."""
    transcript: list[str] = []
    switch_branch(repo, BRANCH_ONE)
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "() => state.chats.length >= 1 && state.currentChatId === state.chats[0].id",
        timeout=15000,
    )
    await wait_socket(page)
    await select_chat_model(page)

    appeared1, card1, answer1 = await ask(page, QUESTION_ONE)
    transcript += [f"Q1: {QUESTION_ONE}", f"card1: {card1}", f"A1: {answer1}"]
    ok1 = report("first question: new tool-call card for the branch tool",
                 appeared1 and card_names_the_tool(card1), card1[:120])
    ok2 = report(f"first answer names the branch {BRANCH_ONE}",
                 BRANCH_ONE in answer1.lower(), answer1[:120])

    switch_branch(repo, BRANCH_TWO)
    appeared2, card2, answer2 = await ask(page, QUESTION_TWO)
    transcript += [f"Q2: {QUESTION_TWO}", f"card2: {card2}", f"A2: {answer2}"]
    ok3 = report("second question: a second new tool-call card",
                 appeared2 and card_names_the_tool(card2), card2[:120])
    ok4 = report(f"second answer follows the switch to {BRANCH_TWO}",
                 BRANCH_TWO in answer2.lower(), answer2[:120])
    return ok1 and ok2 and ok3 and ok4, transcript


async def drive_browser(
    username: str, password: str, scratch: Path, copy_dir: Path, repo: Path,
) -> int:
    """Log in, add the server, run up to MAX_ATTEMPTS attempts; return the attempt number or 0."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        passed_attempt = 0
        try:
            await kb.login(page, username, password)
            script = copy_dir / "mcp_servers" / "project_git.py"
            if not report("local-git added through the «MCP серверы» form",
                          await add_server(page, script, repo), str(script)):
                return 0
            for attempt in range(1, MAX_ATTEMPTS + 1):
                ok, transcript = await scenario(page, repo)
                if ok:
                    passed_attempt = attempt
                    break
                print(f"--- failed attempt {attempt} transcript ---", flush=True)
                print("\n".join(transcript), flush=True)
        except Exception as exc:  # noqa: BLE001 - any browser failure must be reported, not raised
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()
        return passed_attempt


async def run_e2e() -> int:
    """Start the isolated copy, run the browser scenario, tear down; return the exit code."""
    scratch = Path(tempfile.mkdtemp(prefix="mcp_git_e2e_"))
    db_path = scratch / "e2e_mcp_git.db"
    creds = {"gituser": secrets.token_urlsafe(12)}
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    attempt = 0
    try:
        repo = make_temp_repo(scratch)
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
            attempt = await asyncio.wait_for(
                drive_browser("gituser", creds["gituser"], scratch, copy_dir, repo), 900,
            )
    except Exception as exc:  # noqa: BLE001 - report any setup failure instead of crashing
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await kb.teardown(app_proc)
        if log_file is not None:
            log_file.close()
        if attempt:
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            print(f"scratch kept for inspection: {scratch}", flush=True)
    model = chat_model.get("id", "?")
    if attempt:
        print(f"RESULT: PASS (attempt {attempt} of {MAX_ATTEMPTS}, model {model})", flush=True)
        return EXIT_PASS
    print(f"RESULT: FAIL (model {model})", flush=True)
    return EXIT_FAIL


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
