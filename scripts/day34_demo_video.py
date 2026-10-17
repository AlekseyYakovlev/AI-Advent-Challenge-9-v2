"""Record the Day 34 demo video: chat goals with diff cards, then the reproducible script run.

Usage:
    python scripts/day34_demo_video.py [--out eval_out/day34_video]

Starts the isolated copy (ports 18000/18001) against a scratch clone, then records one browser
video: the project-files server added through the form, a find-usages goal and an ADR goal in an
ordinary chat (diff cards opened), then the same ADR goal re-run through scripts/files_task.py
on a reset clone, with run.json, run.patch and answer.md shown at the end. Nothing is written to
the working copy except the video and the copied script artifacts in --out.
"""

import argparse
import asyncio
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_OUT: Path = REPO_ROOT / "eval_out" / "day34_video"
os.environ.setdefault("FILES_E2E_VIDEO_DIR", str(DEFAULT_OUT))

import e2e_kb_playwright as kb  # noqa: E402
import e2e_project_files_playwright as pf  # noqa: E402
import files_eval as fe  # noqa: E402
import files_task  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

USAGES_GOAL: str = (
    fe.SCENARIOS["usages"] + " Отвечай на русском языке."
)
ADR_GOAL: str = (
    "Напиши ADR на русском языке о разделении приложения на два процесса (UI и Agent): "
    "контекст, решение, последствия. Прочитай docs/ARCHITECTURE.md, не больше трёх файлов, "
    "затем сразу создай новый файл docs/adr/001-ui-agent-split.md (имя латиницей) и в конце "
    "перечисли созданные или изменённые файлы. Отвечай на русском языке."
)
HOLD_S: float = 6.0
STEP_PX: int = 380
STEP_DELAY_S: float = 1.4
FALLBACK_MARK: str = "модель не дала ответа"
FOLLOWUP_GOAL: str = "Перечисли созданные или изменённые файлы."

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

INNER_SCROLL_JS: str = """
(el, step) => {
  const max = el.scrollHeight - el.clientHeight;
  if (el.scrollTop >= max - 2) return false;
  el.scrollTop = Math.min(max, el.scrollTop + step);
  return true;
}
"""


async def caption(page: Any, text: str) -> None:
    """Show a step caption on top of the page (stays until replaced)."""
    await page.evaluate(CAPTION_JS, text)


async def scroll_through(page: Any, selector: str) -> None:
    """Scroll an element from its current position to its end in readable steps."""
    while await page.evaluate(STEP_SCROLL_JS, [selector, STEP_PX]):
        await asyncio.sleep(STEP_DELAY_S)
    await asyncio.sleep(1.0)


async def show_card(page: Any, index: int) -> None:
    """Open one tool card, bring it to the top of the chat and scroll through all of it."""
    await page.evaluate(
        """(i) => { const c = document.querySelectorAll('details.tool-call-card')[i];
            c.open = true; const box = document.getElementById('messages');
            box.scrollTop = box.scrollTop + c.getBoundingClientRect().top - 140; }""",
        index,
    )
    await asyncio.sleep(1.5)
    card = page.locator("details.tool-call-card").nth(index)
    for _ in range(40):
        box = page.locator("#messages")
        bottom = await card.evaluate("e => e.getBoundingClientRect().bottom")
        limit = await box.evaluate("e => e.getBoundingClientRect().bottom")
        if bottom <= limit - 20:
            break
        if not await page.evaluate(STEP_SCROLL_JS, ["#messages", STEP_PX]):
            break
        await asyncio.sleep(STEP_DELAY_S)
    inner = card.locator(f"{pf.DIFF_REGION}, pre")
    for i in range(await inner.count()):
        handle = inner.nth(i)
        if await handle.evaluate("e => e.scrollHeight > e.clientHeight + 4"):
            await handle.scroll_into_view_if_needed()
            while await handle.evaluate(INNER_SCROLL_JS, STEP_PX):
                await asyncio.sleep(STEP_DELAY_S)
    await asyncio.sleep(1.5)
    await card.evaluate("e => { e.open = false; }")


async def tour_cards(page: Any, text: str, tools: tuple[str, ...]) -> None:
    """Walk the cards of the chosen tools from the first to the last, then show the answer."""
    await caption(page, text)
    summaries = await page.evaluate(
        "() => Array.from(document.querySelectorAll('details.tool-call-card'))"
        ".map(c => c.querySelector('summary').textContent)"
    )
    for index, summary in enumerate(summaries):
        if pf.SERVER_NAME in summary and any(t in summary for t in tools):
            await show_card(page, index)
    await page.evaluate(
        "() => { const b = document.getElementById('messages'); b.scrollTop = b.scrollHeight; }"
    )
    await asyncio.sleep(HOLD_S)


async def last_answer(page: Any) -> str:
    """Text of the last assistant message."""
    rows = pf.assistant_rows(page)
    if not await rows.count():
        return ""
    return ((await rows.last.locator(".message-content").text_content()) or "").strip()


async def ensure_model(page: Any) -> None:
    """Pick the chat model and make sure the header picker really shows it."""
    # This LM Studio build lists models without a `loaded` flag, so the picker's change handler
    # would ask «Загрузить?» for an already loaded model; select it directly instead.
    await page.wait_for_function(
        "id => Array.from(document.querySelectorAll('#model-select option'))"
        ".some(o => o.textContent.includes(id))",
        arg=pf.chat_model["id"], timeout=60000,
    )
    for _ in range(3):
        shown = await page.evaluate(
            """(id) => {
              const s = document.getElementById('model-select');
              const opt = Array.from(s.options).find(o => o.textContent.includes(id));
              s.value = opt.value;
              const parsed = parseModelValue(opt.value);
              selectModelEntry(parsed.providerId, parsed.modelId);
              return s.options[s.selectedIndex].textContent + ' | ' + state.selectedModel;
            }""",
            pf.chat_model["id"],
        )
        await asyncio.sleep(1.5)
        if shown.endswith(pf.chat_model["id"]) and pf.chat_model["id"] in shown.split(" | ")[0]:
            print(f"model picker shows: {shown}", flush=True)
            return
    options = await page.evaluate(
        "() => Array.from(document.querySelectorAll('#model-select option')).map(o => o.textContent)"
    )
    print(f"model options: {options}", flush=True)
    raise RuntimeError("model picker does not show " + pf.chat_model["id"])


def results_page(out_dir: Path) -> str:
    """Build a plain HTML page with the command, the run record summary, the patch and the answer."""
    record = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    summary = {key: record.get(key) for key in ("goal", "commit", "model", "status", "tool_call_count")}
    summary["patch_files"] = record.get("patch", {}).get("files")
    blocks = [
        ("Команда", "python scripts/files_task.py --root <clone> --goal \"<цель>\" --out <dir>"),
        ("run.json (сводка)", json.dumps(summary, ensure_ascii=False, indent=2)),
        ("run.patch", (out_dir / "run.patch").read_text(encoding="utf-8")[:9000]),
        ("answer.md", (out_dir / "answer.md").read_text(encoding="utf-8")),
    ]
    body = "".join(f"<h2>{html.escape(t)}</h2><pre>{html.escape(c)}</pre>" for t, c in blocks)
    return (
        "<!doctype html><meta charset=utf-8><title>files_task</title>"
        "<style>body{font:15px Consolas,monospace;background:#0f172a;color:#e2e8f0;padding:16px}"
        "h2{color:#38bdf8}pre{white-space:pre-wrap;background:#1e293b;padding:12px}</style>"
        f"<h1>Воспроизводимый прогон: scripts/files_task.py</h1>{body}"
    )


async def run_script(clone: Path, api_key: str, out_dir: Path, provider_id: int) -> int:
    """Run files_task.py as a subprocess on the clone and return its exit code."""
    env = {**os.environ, "FILES_TASK_API_KEY": api_key, "PYTHONIOENCODING": "utf-8"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SCRIPTS_DIR / "files_task.py"), "--root", str(clone),
        "--goal", ADR_GOAL, "--out", str(out_dir), "--title", "day34-demo-adr",
        "--provider-id", str(provider_id), "--model", pf.chat_model["id"],
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await proc.communicate()
    print(output.decode("utf-8", "replace")[-1500:], flush=True)
    return proc.returncode or 0


async def record(scratch: Path, out_dir: Path) -> int:
    """Run the whole recorded scenario and return an exit code."""
    from playwright.async_api import async_playwright

    clone = fe.make_clone(scratch)
    commit = files_task.git_head(clone)["commit"]
    script_out = scratch / "script_out"
    async with fe.isolated_app(scratch) as (run_env, client):
        api_key = await fe.create_api_key(client)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False, slow_mo=pf.VIDEO_SLOW_MO)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 850},
                record_video_dir=str(out_dir), record_video_size={"width": 1366, "height": 850},
            )
            page = await context.new_page()
            page.set_default_timeout(pf.STEP_TIMEOUT_MS)
            page.on("dialog", lambda dialog: print(f"dialog: {dialog.message}", flush=True))
            try:
                await kb.login(page, run_env.username, run_env.password)
                await caption(page, "День 34. Ассистент по файлам проекта: чтение, поиск, запись и diff")
                await asyncio.sleep(HOLD_S / 2)
                script = run_env.copy_dir / "mcp_servers" / "project_git.py"
                await pf.add_server(page, script, clone)
                await caption(page, "Сервер project-files (с записью) добавлен через форму «MCP серверы»")
                await asyncio.sleep(HOLD_S)
                await page.click("#btn-new-chat")
                await page.wait_for_function("() => state.chats.length >= 1", timeout=15000)
                await pf.wait_socket(page)
                await ensure_model(page)
                await caption(page, "Цель 1 (поиск по проекту): где используется cleanup_chat_caches?")
                await pf.ask(page, USAGES_GOAL)
                await tour_cards(page, "Цель 1: вызовы поиска и итоговый ответ ассистента",
                                 ("find_usages", "search_text"))
                await caption(page, "Цель 2 (создание файла): написать ADR о разделении UI и Agent")
                await pf.ask(page, ADR_GOAL)
                if FALLBACK_MARK in await last_answer(page):
                    await caption(page, "Модель не дала итог — просим перечислить файлы")
                    await pf.ask(page, FOLLOWUP_GOAL)
                await tour_cards(page, "Цель 2: запись файла и цветной diff (+добавлено, −удалено)",
                                 ("write_file", "edit_file"))
                await caption(page, "Цель 2 ещё раз через scripts/files_task.py (идёт прогон...)")
                fe.reset_clone(clone, commit)
                provider_id = await page.evaluate("() => state.selectedProviderId")
                code = await run_script(clone, api_key, script_out, int(provider_id))
                if (script_out / "run.json").is_file():
                    page_file = scratch / "results.html"
                    page_file.write_text(results_page(script_out), encoding="utf-8")
                    await page.goto(page_file.as_uri())
                    await caption(page, "Воспроизводимость: цель, коммит, модель, вызовы, patch и ответ записаны")
                    await asyncio.sleep(HOLD_S)
                    await scroll_through(page, "html")
                    await asyncio.sleep(HOLD_S)
                    shutil.copytree(script_out, out_dir / "files_task_run", dirs_exist_ok=True)
                print(f"files_task exit code: {code}", flush=True)
            finally:
                video = page.video
                await context.close()
                await browser.close()
                if video:
                    print(f"video saved: {await video.path()}", flush=True)
    return 0


def main() -> int:
    """Parse arguments, run the recording in a scratch directory, clean up."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--model", default="", help="model id to use, e.g. deepseek-flash (default: local LM Studio model)",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.model.startswith("deepseek"):
        key = dotenv_values(REPO_ROOT / ".env").get("DEEPSEEK_API_KEY") or ""
        if not key or key == "your_deepseek_api_key_here":
            print("DEEPSEEK_API_KEY is not set in .env")
            return 2
        os.environ["DEEPSEEK_API_KEY"] = key
        pf.chat_model["id"] = args.model
    else:
        pf.chat_model["id"] = args.model or pf.pick_chat_model() or ""
    if not pf.chat_model["id"]:
        print("no loaded chat model in LM Studio")
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="day34_demo_"))
    try:
        return asyncio.run(record(scratch, args.out))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
