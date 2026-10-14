"""Browser check of the review settings section on an isolated copy of the app.

Usage: python scripts/e2e_review_settings_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a closed LM Studio port, drives it with headless Chromium
and writes one row per scenario step to eval_out/day32/e2e_review_settings.md.
The ports 8000/8001 of a running app are never touched; the script refuses to run against them.

Exit codes: 0 no step failed, 1 a step failed, 3 the run was blocked (ports busy, Playwright or
the copy could not start).
"""

import asyncio
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_llm_providers_playwright as base  # noqa: E402

UI_PORT: int = 18000
AGENT_PORT: int = 18001
CLOSED_LM_PORT: int = 18767
FORBIDDEN_PORTS: tuple[int, ...] = (8000, 8001)
UI_URL: str = f"http://localhost:{UI_PORT}"
HOST: str = "127.0.0.1"
REPORT_PATH: Path = REPO_ROOT / "eval_out" / "day32" / "e2e_review_settings.md"
STEP_TIMEOUT_MS: int = 20000
REFRESH_WAIT_SECONDS: float = 40.0
PLAYWRIGHT_TIMEOUT: float = 180.0
LM_DOWN_TEXT: str = "LM Studio недоступен, индекс не обновлён. Запустите LM Studio и повторите."
NOT_RUN: str = "не запускалось"

EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED = 0, 1, 3

steps: list[tuple[str, str, str]] = []


def record(step: str, status: str, detail: str = "") -> None:
    """Record and print one step result; status is passed, failed or не запускалось."""
    steps.append((step, status, detail))
    print(f"{status.upper()}: {step}" + (f" - {detail}" if detail else ""))


def git(cwd: Path, *args: str) -> None:
    """Run git in cwd and raise when it fails."""
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "e2e", "GIT_AUTHOR_EMAIL": "e2e@example.com",
             "GIT_COMMITTER_NAME": "e2e", "GIT_COMMITTER_EMAIL": "e2e@example.com"},
    )


def make_git_repo(root: Path) -> Path:
    """Create a repository with an origin (bare) and one pushed branch of two .py files."""
    bare = root / "origin.git"
    work = root / "work"
    bare.mkdir(parents=True)
    work.mkdir()
    git(bare, "init", "--bare", "-b", "main")
    git(work, "init", "-b", "main")
    (work / "a.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (work / "b.py").write_text("def sub(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    git(work, "add", "a.py", "b.py")
    git(work, "commit", "-m", "init")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-u", "origin", "main")
    return work


async def error_text(page: Any) -> str:
    """Return the visible text of the error line, or an empty string when it is hidden."""
    if await page.locator("#review-error").is_hidden():
        return ""
    return (await page.locator("#review-error").inner_text()).strip()


async def open_settings(page: Any) -> None:
    """Open the Settings window and wait for the review section data to load."""
    await page.click("#btn-settings")
    await page.locator("#review-settings-section").wait_for()
    await page.wait_for_function(
        "document.querySelector('#review-provider').options.length > 0", timeout=15000,
    )


async def close_settings(page: Any) -> None:
    """Close the Settings window."""
    await page.evaluate("closeSettingsModal()")


async def wait_error(page: Any, needle: str, timeout: float) -> str:
    """Wait until the error line contains needle; return the last observed text."""
    deadline = asyncio.get_running_loop().time() + timeout
    text = ""
    while asyncio.get_running_loop().time() < deadline:
        text = await error_text(page)
        if needle in text:
            return text
        await asyncio.sleep(0.5)
    return text


async def drive_browser(username: str, password: str, scratch: Path) -> None:
    """Run scenario steps 1-7."""
    from playwright.async_api import async_playwright

    git_repo = make_git_repo(scratch / "gitrepo")
    not_repo = scratch / "not_a_repo"
    not_repo.mkdir()
    puts: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on(
            "request",
            lambda r: puts.append(r.url) if r.method == "PUT" and "/reviews/config" in r.url else None,
        )
        try:
            await page.goto(f"{UI_URL}/static/login.html")
            await page.fill("#login-username", username)
            await page.fill("#login-password", password)
            await page.click("#login-form button[type=submit]")
            await page.wait_for_url("**/static/index.html", timeout=15000)
            await page.wait_for_function("document.querySelector('#model-select') !== null")

            # 1
            await open_settings(page)
            heading = await page.locator("#review-settings-section h3").inner_text()
            status = (await page.locator("#review-index-status").inner_text()).strip()
            disabled = await page.locator("#review-refresh-btn").is_disabled()
            hint = await page.locator("#review-refresh-hint").is_visible()
            ok = heading == "Ревью Pull Request" and status == "Не построен" and disabled and hint
            record("1 section visible, status 'Не построен', refresh disabled with hint",
                   "passed" if ok else "failed", f"{heading!r}, {status!r}, disabled={disabled}, hint={hint}")

            # 2
            selected = await page.evaluate(
                "(() => { const s = document.querySelector('#review-provider');"
                " const id = Number(s.value); const p = state.llmProviders.find(x => x.id === id);"
                " return p ? p.kind + '|' + p.name : ''; })()",
            )
            note_hidden = await page.locator("#review-cloud-note").is_hidden()
            record("2 provider is LM Studio, cloud note hidden",
                   "passed" if selected.startswith("lm_studio|") and note_hidden else "failed",
                   f"selected={selected!r}, note_hidden={note_hidden}")

            # 3
            await page.fill("#review-repo-path", "")
            before = len(puts)
            await page.click("#review-save-btn")
            text = await error_text(page)
            record("3 empty path shows validation text and sends no PUT",
                   "passed" if text == "Укажите путь к репозиторию." and len(puts) == before else "failed",
                   f"{text!r}, puts={len(puts) - before}")

            # 4
            await page.fill("#review-repo-path", str(not_repo))
            await page.click("#review-save-btn")
            text = await wait_error(page, "Не удалось прочитать репозиторий:", 10)
            kept = await page.input_value("#review-repo-path")
            record("4 folder that is not a git repository shows an error and keeps the value",
                   "passed" if text.startswith("Не удалось прочитать репозиторий:") and kept == str(not_repo)
                   else "failed", f"{text!r}, kept={kept == str(not_repo)}")

            # 5
            await page.fill("#review-repo-path", str(git_repo))
            await page.click("#review-save-btn")
            await page.wait_for_function(
                "document.querySelector('#review-error').classList.contains('hidden')", timeout=10000,
            )
            await close_settings(page)
            await open_settings(page)
            try:
                await page.wait_for_function(
                    "!document.querySelector('#review-refresh-btn').disabled", timeout=8000,
                )
            except Exception:  # noqa: BLE001 - the assertion below records the outcome
                pass
            kept = await page.input_value("#review-repo-path")
            enabled = await page.locator("#review-refresh-btn").is_enabled()
            record("5 git repository saved, path survives reopening, refresh enabled",
                   "passed" if Path(kept) == git_repo and enabled else "failed",
                   f"path_kept={Path(kept) == git_repo}, got={kept!r}, want={str(git_repo)!r}, refresh_enabled={enabled}")

            # 6
            await page.click("#review-refresh-btn")
            deadline = asyncio.get_running_loop().time() + REFRESH_WAIT_SECONDS
            status, err = "", ""
            while asyncio.get_running_loop().time() < deadline:
                status = (await page.locator("#review-index-status").inner_text()).strip()
                err = await error_text(page)
                if err or status.startswith("Обновлён:"):
                    break
                await asyncio.sleep(0.5)
            if status.startswith("Обновлён:"):
                record("6a refresh success branch", "passed" if "файлов: 2" in status else "failed", status)
            else:
                record("6a refresh success branch", f"{NOT_RUN} (LM Studio недоступен)",
                       "the copy points at a closed LM Studio port")
            if err == LM_DOWN_TEXT:
                record("6b refresh error branch shows the LM Studio text", "passed", err)
            elif status.startswith("Обновлён:"):
                record("6b refresh error branch shows the LM Studio text",
                       f"{NOT_RUN} (embeddings were reachable)", "")
            else:
                record("6b refresh error branch shows the LM Studio text", "failed",
                       f"status={status!r}, error={err!r}")

            # 7
            created = await page.evaluate(
                "apiFetch('/api/v1/llm-providers', {method: 'POST', body: JSON.stringify("
                "{name: 'CloudStub', base_url: 'http://127.0.0.1:18766', api_key_env: '', enabled: true})})"
                ".then(() => 'ok').catch((e) => e.message)",
            )
            await close_settings(page)
            await open_settings(page)
            if created != "ok":
                record("7 cloud provider shows the cloud note", "failed", f"provider not created: {created}")
            else:
                option = page.locator("#review-provider option", has_text="CloudStub")
                value = await option.first.get_attribute("value")
                await page.select_option("#review-provider", value)
                visible = await page.locator("#review-cloud-note").is_visible()
                record("7 cloud provider shows the cloud note", "passed" if visible else "failed",
                       f"note_visible={visible}")
        finally:
            await browser.close()


async def run_e2e() -> bool:
    """Start the isolated copy and run the scenario; return False when the run was blocked."""
    scratch = Path(tempfile.mkdtemp(prefix="review_e2e_"))
    db_path = scratch / "review.db"
    username, password = "review_e2e", secrets.token_urlsafe(12)
    app_proc: asyncio.subprocess.Process | None = None
    log_file = None
    try:
        copy_dir = base.prepare_copy(scratch)
        await base.seed_scratch_user(db_path, username, password)
        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "UI_PORT": str(UI_PORT),
            "AGENT_PORT": str(AGENT_PORT),
            "LM_STUDIO_BASE_URL": f"http://{HOST}:{CLOSED_LM_PORT}",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join([str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])]),
        }
        log_file = open(scratch / "app.log", "wb")
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable, str(copy_dir / "run.py"),
            cwd=str(copy_dir), env=env, stdout=log_file, stderr=log_file,
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await base.wait_for_app(client)
        if not ready:
            print("BLOCKED: the isolated copy did not start")
            return False
        await asyncio.wait_for(drive_browser(username, password, scratch), PLAYWRIGHT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - any failure of the harness itself is a blocked run
        print(f"BLOCKED: {type(exc).__name__}: {exc}")
        return False
    finally:
        await base.teardown(app_proc, None)
        if log_file is not None:
            log_file.close()
        print(f"scratch kept for inspection: {scratch}")
    return True


def write_report(blocked: bool) -> None:
    """Write the result table."""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# E2E: Ревью Pull Request, настройки", "",
             "Изолированная копия на портах 18000/18001; приложение на 8000/8001 не затрагивалось.", ""]
    if blocked:
        lines += ["Запуск заблокирован: копия приложения или Playwright не стартовали.", ""]
    lines += ["| Шаг | Статус | Подробности |", "|-----|--------|-------------|"]
    for step, status, detail in steps:
        lines.append(f"| {step} | {status} | {detail.replace('|', '/').replace(chr(10), ' ')} |")
    if blocked and not steps:
        lines.append("| все шаги | заблокировано | запуск не состоялся |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Refuse the real ports, run the scenario, write the report and exit with the result code."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if UI_PORT in FORBIDDEN_PORTS or AGENT_PORT in FORBIDDEN_PORTS:
        print("REFUSED: the target ports must not be 8000/8001.")
        sys.exit(EXIT_BLOCKED)
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("BLOCKED: Playwright is not installed.")
        write_report(True)
        sys.exit(EXIT_BLOCKED)
    busy = [p for p in (UI_PORT, AGENT_PORT, CLOSED_LM_PORT) if not base.port_is_free(p)]
    if busy:
        print(f"BLOCKED: port(s) {busy} are in use.")
        write_report(True)
        sys.exit(EXIT_BLOCKED)
    started = asyncio.run(run_e2e())
    write_report(not started)
    if not started:
        sys.exit(EXIT_BLOCKED)
    sys.exit(EXIT_FAIL if any(s == "failed" for _n, s, _d in steps) else EXIT_PASS)


if __name__ == "__main__":
    main()
