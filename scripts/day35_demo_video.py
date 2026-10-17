"""Record the Day 35 demo video: /release from the plan card to the draft GitHub Release.

Usage:
    python scripts/day35_demo_video.py --mode sandbox [--out eval_out/day35_video]
    python scripts/day35_demo_video.py --mode project [--out eval_out/day35_video]

Starts the isolated copy (ports 18000/18001) and records one browser video: the release-git
server added through the API, /release, the plan card (changelog, version, title, steps), the
confirmation and the four steps. `sandbox` seeds a scratch repository (RELEASE_SCRATCH_REMOTE,
RELEASE_SCRATCH_TOKEN); `project` runs the REAL release: it clones this repository into a scratch
directory, points the clone at the real origin and pushes the current branch, an annotated tag and
creates a draft GitHub Release with RELEASE_PROJECT_TOKEN. Nothing is written to the working copy
except the video and the token-free run record in --out.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_OUT: Path = REPO_ROOT / "eval_out" / "day35_video"

import e2e_kb_playwright as kb  # noqa: E402
import e2e_release_playwright as e2e  # noqa: E402
import review_control as rc  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

ENV_PROJECT_TOKEN: str = "RELEASE_PROJECT_TOKEN"
HOLD_S: float = 6.0
STEP_PX: int = 380
STEP_DELAY_S: float = 1.4

CAPTION_JS: str = """
(text) => {
  let el = document.getElementById('demo-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'demo-caption';
    el.style.cssText = 'position:fixed;left:50%;top:8px;transform:translateX(-50%);z-index:99999;'
      + 'background:#0ea5e9;color:#fff;font:600 17px system-ui,sans-serif;padding:8px 18px;'
      + 'border-radius:10px;box-shadow:0 4px 14px rgba(0,0,0,.5);max-width:90%;text-align:center';
    document.body.appendChild(el);
  }
  el.textContent = text;
}
"""

STEP_SCROLL_JS: str = """
([sel, step]) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const max = el.scrollHeight - el.clientHeight;
  if (el.scrollTop >= max - 2) return false;
  el.scrollTop = Math.min(max, el.scrollTop + step);
  return true;
}
"""


async def caption(page: Any, text: str) -> None:
    """Show a step caption on top of the page (stays until replaced)."""
    await page.evaluate(CAPTION_JS, text)


async def scroll_chat(page: Any) -> None:
    """Scroll the chat from its current position to its end in readable steps."""
    while await page.evaluate(STEP_SCROLL_JS, ["#messages", STEP_PX]):
        await asyncio.sleep(STEP_DELAY_S)
    await asyncio.sleep(1.0)


def load_secrets(mode: str, model: str) -> str | None:
    """Put the model key into the environment, register every secret; return the GitHub token."""
    if model.startswith("deepseek") and not os.environ.get("DEEPSEEK_API_KEY"):
        key = dotenv_values(REPO_ROOT / ".env").get("DEEPSEEK_API_KEY") or ""
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
    token = os.environ.get(ENV_PROJECT_TOKEN if mode == "project" else e2e.ENV_TOKEN) or None
    for secret in (token, os.environ.get("DEEPSEEK_API_KEY")):
        if secret and secret not in rc._SECRETS:  # noqa: SLF001 - reuse the shared scrubber
            rc._SECRETS.append(secret)  # noqa: SLF001
    return token


def prepare_project_clone(workdir: Path) -> e2e.ScratchRepo:
    """Clone this repository into workdir and point the clone at the real origin."""
    origin = e2e.project_origin()
    branch = e2e.run_git(REPO_ROOT, "branch", "--show-current").stdout.strip()
    clone = workdir / "project-clone"
    e2e.run_git(workdir, "clone", "--no-hardlinks", str(REPO_ROOT), str(clone))
    e2e.run_git(clone, "remote", "set-url", "origin", origin)
    e2e.run_git(clone, "checkout", branch)
    for ref in e2e.run_git(clone, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").stdout.split():
        e2e.run_git(clone, "update-ref", "-d", ref)
    return e2e.ScratchRepo(clone, origin, branch, 0, "", "")


async def record(workdir: Path, out_dir: Path, mode: str, model: str, token: str | None) -> int:
    """Run the recorded scenario and return an exit code."""
    from playwright.async_api import async_playwright

    if mode == "project":
        repo = prepare_project_clone(workdir)
    else:
        repo = e2e.setup_scratch_repo(os.environ[e2e.ENV_REMOTE], workdir)
    e2e.say(f"repository prepared: {repo.clone} branch {repo.branch}")
    app = await e2e.start_isolated_app(workdir)
    if app is None:
        return 3
    record_data: dict[str, Any] = {"mode": mode, "model": model, "branch": repo.branch}
    exit_code = 1
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False, slow_mo=250)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 850},
                record_video_dir=str(out_dir), record_video_size={"width": 1366, "height": 850},
            )
            page = await context.new_page()
            page.set_default_timeout(e2e.STEP_TIMEOUT_MS)
            frames = e2e.capture_frames(page)
            try:
                await kb.login(page, app.user, app.password)
                await caption(page, "День 35. Релиз одной командой: /release")
                await asyncio.sleep(HOLD_S / 2)
                env = {e2e.TOKEN_ENV_NAME: token} if token else {}
                server_id = await e2e.add_release_server(page, app.copy_dir, repo.clone, env)
                if server_id is None:
                    raise RuntimeError("release-git was not connected")
                await caption(page, "Git-сервер release-git (с записью) подключён; токен хранится только в его окружении")
                await asyncio.sleep(HOLD_S)
                ctx = e2e.Ctx(page, frames, app, repo, token, server_id)
                await e2e.new_chat(page)
                await caption(page, "Команда /release: ассистент читает историю через MCP и строит план")
                _, plan = await e2e.plan_for(ctx)
                if not plan or plan.get("type") != "release_plan" or plan.get("blocked"):
                    raise RuntimeError(f"plan was not built: {e2e.plan_detail(plan, ctx)}")
                record_data["plan"] = {k: plan.get(k) for k in ("range", "version", "title", "branch")}
                await caption(page, "План: диапазон, версия, название, changelog и четыре шага")
                await e2e.last_card(ctx).scroll_into_view_if_needed()
                await asyncio.sleep(HOLD_S)
                await scroll_chat(page)
                await caption(page, "Без подтверждения ничего не записывается. Подтверждаем релиз")
                await asyncio.sleep(HOLD_S / 2)
                start = await e2e.click_confirm(ctx)
                result = await e2e.wait_frame(frames, start, e2e.RESULT_TYPES, e2e.CONFIRM_TIMEOUT)
                if result is None:
                    raise RuntimeError("no release result")
                await caption(page, "Коммит CHANGELOG.md, тег, push и черновик GitHub Release")
                await asyncio.sleep(HOLD_S)
                await scroll_chat(page)
                steps = e2e.step_map(result)
                record_data["result"] = {
                    "outcome": result.get("outcome"), "steps": steps, "version": result.get("version"),
                    "tag_message": result.get("tag_message"), "release_url": result.get("release_url"),
                }
                tag = str(result.get("tag"))
                remote_line = e2e.run_git(repo.clone, "ls-remote", "origin", f"refs/tags/{tag}").stdout.split()
                record_data["tag_on_remote"] = bool(remote_line)
                if token:
                    is_draft, detail = e2e.draft_release_exists(repo.remote, token, tag)
                    record_data["draft_release"] = {"is_draft": is_draft, "detail": detail}
                await caption(page, f"Готово: {tag} на удалённом репозитории, Release — черновик")
                await asyncio.sleep(HOLD_S)
                exit_code = 0 if all(steps.get(k) == "done" for k in ("commit", "tag", "push", "release")) else 1
            finally:
                video = page.video
                await context.close()
                await browser.close()
                if video:
                    e2e.say(f"video saved: {await video.path()}")
    finally:
        await kb.teardown(app.proc)
        app.log_file.close()
    record_data["finished_at"] = datetime.now(timezone.utc).isoformat()
    rc.write_json(out_dir / f"release_run_{mode}.json", record_data)
    e2e.say(f"run record: {json.dumps(record_data, ensure_ascii=False)[:1200]}")
    return exit_code


def main() -> int:
    """Parse arguments, check the inputs, record in a scratch directory, clean up."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sandbox", "project"), default="sandbox")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    token = load_secrets(args.mode, args.model)
    if not token:
        e2e.say(f"BLOCKED: no GitHub token for mode {args.mode}")
        return 2
    if args.mode == "sandbox":
        reason = e2e.unsafe_remote_reason(os.environ.get(e2e.ENV_REMOTE, ""), e2e.project_origin())
        if reason:
            e2e.say(f"BLOCKED: {reason}")
            return 2
    if args.model.startswith("deepseek") and not os.environ.get("DEEPSEEK_API_KEY"):
        e2e.say("BLOCKED: DEEPSEEK_API_KEY is not set")
        return 2
    busy = [p for p in (e2e.UI_PORT, e2e.AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        e2e.say(f"BLOCKED: ports {busy} are busy")
        return 2
    e2e.chat_model["id"] = args.model
    args.out.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="day35_demo_"))
    try:
        return asyncio.run(record(workdir, args.out, args.mode, args.model, token))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
