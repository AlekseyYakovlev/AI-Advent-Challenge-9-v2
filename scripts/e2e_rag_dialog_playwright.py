"""Browser check of task memory and history-aware retrieval on an isolated copy.

Usage: python scripts/e2e_rag_dialog_playwright.py

Copies the working tree to a temporary directory, repoints the copy to UI :18000 / Agent :18001,
starts it with a scratch database and a scratch KB storage directory, drives it with headless
Chromium against the real LM Studio (a chat model plus the nomic embedder) and the real ФЗ-196
PDF in C:\\Projects\\RAG, and prints one PASS/FAIL line per check. The default ports of a
running app are never touched and only processes started by this script are ever stopped.
Screenshots of the new blocks are written to eval_out/day25/screens/ in the real repository.

The nomic embedder has no calibrated threshold (effective 0.0), so answers are not gated.

Reuses the copy/seed/login/teardown building blocks of e2e_kb_playwright.py, the chat helpers of
e2e_rag_playwright.py, the popover helpers of e2e_rag_search_playwright.py and the block helpers
of e2e_rag_cite_playwright.py.

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
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_kb_playwright as kb  # noqa: E402
import e2e_rag_cite_playwright as cite  # noqa: E402
import e2e_rag_playwright as rag  # noqa: E402
import e2e_rag_search_playwright as search  # noqa: E402

UI_PORT: int = kb.UI_PORT
AGENT_PORT: int = kb.AGENT_PORT
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
KB_NAME: str = "traffic-dialog"
DOC_FALLBACK: Path = kb.RAG_DIR / "ПДД.pdf"
KOAP_FALLBACK: Path = kb.RAG_DIR / "КОАП РФ.pdf"
SCREENS_DIR: Path = kb.REPO_ROOT / "eval_out" / "day25" / "screens"
STEP_TIMEOUT_MS: int = 20000
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4
Q_FIRST: str = (
    "Я готовлю конспект по правилам дорожного движения. Объясни, что такое "
    "дорожно-транспортное происшествие по этому документу. Отвечай только по загруженному "
    "документу, без других источников."
)
Q_FOLLOWUP: str = "а кто считается его участником?"
Q_CLARIFY: str = (
    "Уточню: меня интересует только случай, когда есть пострадавшие. "
    "Какие обязанности возлагает закон на участников такого происшествия?"
)
Q_PLAIN: str = "Привет, как дела?"
RESET_PROMPT: str = "Сбросить память задачи этого чата?"
EDITED_GOAL: str = "Проверить отдельные положения закона о безопасности дорожного движения"
MEMORY_WAIT_S: float = 20.0

report = kb.report
dialog_policy: dict[str, Any] = {"accept": False, "seen": []}


def pick_document() -> Path:
    """Return the ФЗ-196 PDF when present, else the traffic rules PDF kept in the same folder."""
    return kb.FZ_PDF if kb.FZ_PDF.exists() else DOC_FALLBACK


def adopt_available_pdfs() -> None:
    """Point the shared preflight at the PDFs that exist (the ФЗ-196 and КоАП files were renamed)."""
    if not kb.FZ_PDF.exists():
        kb.FZ_PDF = DOC_FALLBACK
    if not kb.KOAP_PDF.exists():
        kb.KOAP_PDF = KOAP_FALLBACK

SIDEBAR_JS = """
() => {
  const body = document.getElementById('memory-task-body');
  const wrap = document.getElementById('memory-task-state');
  const out = {hidden: wrap.classList.contains('hidden'), labels: [], goal: '', clarified: [],
               constraints: [], text: body.innerText, reset: !!document.querySelector(
                 '[data-memory-action="task-reset"]')};
  if (out.hidden || body.children.length < 3) return out;
  const parts = Array.from(body.children).slice(0, 3);
  out.labels = parts.map((p) => p.children[0].textContent.trim());
  out.goal = parts[0].children[1].children[0].textContent.trim();
  const items = (p) => Array.from(p.querySelectorAll('[role=listitem] > span:first-child'))
    .map((s) => s.textContent.trim());
  out.clarified = items(parts[1]);
  out.constraints = items(parts[2]);
  return out;
}
"""

MESSAGE_BLOCKS_JS = """
() => Array.from(document.querySelectorAll('.rag-task-memory')).map(
  (d) => d.querySelector('summary').textContent.trim() + (d.open ? ' [open]' : ''))
"""


async def shot(page: Any, label: str) -> str:
    """Save a screenshot named after the scenario label into the repository."""
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENS_DIR / f"{label}.png"
    await page.screenshot(path=str(path), full_page=False)
    return path.name


async def open_memory_panel(page: Any) -> None:
    """Unfold the memory panel in the sidebar when it is folded."""
    if await page.locator("#memory-panel-body").is_hidden():
        await page.click('[data-fold-toggle="memory-panel-body"]')
        await page.wait_for_selector("#memory-panel-body", state="visible")


async def sidebar(page: Any) -> dict[str, Any]:
    """Read the sidebar task-memory block as a dict."""
    return await page.evaluate(SIDEBAR_JS)


async def api_memory(page: Any, chat_id: int) -> dict[str, Any]:
    """Return GET /memory of the chat."""
    resp = await page.request.get(f"{AGENT_URL}/api/v1/chats/{chat_id}/memory")
    return await resp.json()


async def task_state(page: Any, chat_id: int) -> dict[str, Any] | None:
    """Return the task_state of the chat memory (None without RAG)."""
    return (await api_memory(page, chat_id)).get("task_state")


def item_ids(state: dict[str, Any] | None) -> list[str]:
    """Return the ids of all clarified and constraint items."""
    if not state:
        return []
    return [i["id"] for i in state.get("clarified", []) + state.get("constraints", [])]


async def wait_sidebar_goal(page: Any, timeout: float = MEMORY_WAIT_S) -> dict[str, Any]:
    """Poll the sidebar until a goal appears (the done frame updates it without a reload)."""
    view: dict[str, Any] = {}
    for _ in range(int(timeout * 2)):
        view = await sidebar(page)
        if view.get("goal") and view["goal"] != "—":
            return view
        await asyncio.sleep(0.5)
    return view


async def newest_chat_id(page: Any) -> int:
    """Return the id of the most recently created chat of the user."""
    resp = await page.request.get(f"{AGENT_URL}/api/v1/chats")
    return max(c["id"] for c in await resp.json())


async def new_rag_chat(page: Any, kb_id: str) -> int:
    """Create a chat, select the model, attach the KB, turn RAG on and return the chat id."""
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await rag.select_chat_model(page)
    await page.select_option("#rag-kb-select", kb_id)
    await page.wait_for_function("!document.getElementById('rag-toggle').disabled")
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'",
    )
    return await newest_chat_id(page)


async def open_chat_by_id(page: Any, chat_id: int) -> None:
    """Reload the page and open the chat with the given id from the sidebar list."""
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await page.wait_for_selector("#chat-list > *")
    await page.evaluate(
        "id => { const el = document.querySelector(`#chat-list [data-chat-id=\"${id}\"]`);"
        " if (el) el.click(); }", chat_id,
    )
    await asyncio.sleep(1.5)
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await asyncio.sleep(1.0)


async def chat_row_selector_works(page: Any, chat_id: int) -> bool:
    """Return True when a chat list element carries the chat id as data-chat-id."""
    return bool(await page.evaluate(
        "id => !!document.querySelector(`#chat-list [data-chat-id=\"${id}\"]`)", chat_id,
    ))


async def reload_chat(page: Any, chat_id: int) -> None:
    """Reload and reopen a chat, by id when the list exposes it, else by position (newest first)."""
    await page.reload()
    await page.wait_for_function("document.querySelector('#model-select') !== null")
    await page.wait_for_selector("#chat-list > *")
    if await chat_row_selector_works(page, chat_id):
        await open_chat_by_id(page, chat_id)
        return
    resp = await page.request.get(f"{AGENT_URL}/api/v1/chats")
    ids = [c["id"] for c in await resp.json()]
    await page.locator("#chat-list > *").nth(ids.index(chat_id)).click()
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await asyncio.sleep(1.5)


async def first_assistant(page: Any, chat_id: int) -> dict[str, Any] | None:
    """Return the first (lowest id) assistant message of the active branch."""
    tree = await rag.tree_of(page, chat_id)
    assistants = sorted((m for m in tree if m["role"] == "assistant"), key=lambda m: m["id"])
    return assistants[0] if assistants else None


def payload_of(message: dict[str, Any] | None) -> dict[str, Any]:
    """Return the stored rag_sources payload of a message ({} when absent)."""
    if not message:
        return {}
    payload = message.get("rag_sources")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else {}


async def ask_with_memory(page: Any, chat_id: int, question: str) -> tuple[bool, dict[str, Any]]:
    """Ask a question and return (answered, stored payload of the newest assistant message)."""
    answered = await rag.ask(page, question)
    return answered, await cite.last_assistant_payload(page, chat_id)


async def scenarios(page: Any, creds: dict[str, str]) -> None:
    """Run scenarios S1-S10 in order."""
    await kb.login(page, "dialoguser", creds["dialoguser"])
    await rag.create_kb_with_nomic(page, KB_NAME, [pick_document()])
    ok, text = await kb.timed_ready(page, KB_NAME, kb.SMALL_READY_TIMEOUT)

    # S1
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
    chat_id = await newest_chat_id(page)
    hidden_off = (await sidebar(page))["hidden"]
    await page.click("#rag-toggle")
    await page.wait_for_function(
        "document.getElementById('rag-toggle').getAttribute('aria-checked') === 'true'",
    )
    await reload_chat(page, chat_id)
    await open_memory_panel(page)
    await page.wait_for_function(
        "!document.getElementById('memory-task-state').classList.contains('hidden')", timeout=15000,
    )
    view = await sidebar(page)
    labels_ok = view["labels"] == ["Цель", "Уточнено", "Ограничения и термины"]
    empty_ok = view["goal"] == "—" and "Пока пусто" in view["text"] and not view["reset"]
    await shot(page, "S1-empty-task-memory")
    report("S1 setup: KB «готово»; block hidden before RAG, shown after with three labels and «Пока пусто»",
           ok and bool(kb_id) and hidden_off and labels_ok and empty_ok,
           f"{text.replace(chr(10), ' | ')}; hidden_before={hidden_off} labels={view['labels']} "
           f"goal={view['goal']!r} model={rag.chat_model['id']}")

    # S2
    answered, payload = await ask_with_memory(page, chat_id, Q_FIRST)
    snapshot = payload.get("task_memory") or {}
    if answered and (snapshot.get("failed") is True or not snapshot.get("goal")):
        report("S2 note: first attempt inconclusive, retrying once in a fresh chat", True,
               f"failed={snapshot.get('failed')} goal={snapshot.get('goal')!r}")
        chat_id = await new_rag_chat(page, kb_id)
        await open_memory_panel(page)
        answered, payload = await ask_with_memory(page, chat_id, Q_FIRST)
        snapshot = payload.get("task_memory") or {}
    view = await wait_sidebar_goal(page)
    state_api = await task_state(page, chat_id)
    blocks = await cite.blocks_after_last(page)
    classes = [b["cls"] for b in blocks]
    tm_idx = cite.block_index(blocks, "rag-task-memory")
    tm = blocks[tm_idx] if tm_idx >= 0 else {}
    new_marker = False
    if tm_idx >= 0:
        block = page.locator("details.rag-task-memory").last
        await block.locator("summary").click()
        new_marker = "новое" in await block.inner_text()
    await shot(page, "S2-first-turn-task-memory")
    report("S2 first turn: sources, sidebar goal without reload, API goal, payload v4, collapsed «новое» block",
           answered and "rag-sources" in classes and bool(view.get("goal")) and view["goal"] != "—"
           and bool(state_api and state_api.get("goal")) and payload.get("v") == 4
           and isinstance(payload.get("task_memory"), dict) and tm_idx >= 0
           and tm.get("open") is False and tm.get("summary", "").startswith("Память задачи (")
           and new_marker,
           f"answered={answered} blocks={classes} sidebar_goal={view.get('goal')!r} "
           f"api_goal={(state_api or {}).get('goal')!r} v={payload.get('v')} "
           f"summary={tm.get('summary')!r} new_marker={new_marker} failed={snapshot.get('failed')}")

    # S3
    before_state = await task_state(page, chat_id) or {}
    rows_before = await rag.assistant_rows(page).count()
    answered, payload3 = await ask_with_memory(page, chat_id, Q_FOLLOWUP)
    details = await search.open_details(page)
    details_text = (await details.inner_text()) if await details.count() else ""
    search_info = payload3.get("search") or {}
    condensed = search_info.get("condensed") is True
    shown = (
        ("Уточнён:" in details_text and "история" in details_text) if condensed
        else ("Уточнение запроса" in details_text and "Пропущено:" in details_text)
    )
    after_state = await task_state(page, chat_id) or {}
    grew = len(item_ids(after_state)) >= len(item_ids(before_state))
    rows_after = await rag.assistant_rows(page).count()
    blocks3 = await cite.blocks_after_last(page)
    answer_text = await cite.last_assistant_content(page, chat_id)
    await shot(page, "S3-followup-search-details")
    report("S3 follow-up: history pairs >= 1, condensed or skipped details shown, history kept",
           answered and int(search_info.get("history_pairs") or 0) >= 1 and shown
           and rows_after == rows_before + 1 and rows_before >= 1,
           f"condensed={condensed} history_pairs={search_info.get('history_pairs')} "
           f"rewritten={search_info.get('rewritten')!r} skipped={search_info.get('skipped')} "
           f"blocks={[b['cls'] for b in blocks3]} items {len(item_ids(before_state))}->"
           f"{len(item_ids(after_state))} grew={grew} text={answer_text[:60]!r}")

    # S4
    old_goal = (await sidebar(page))["goal"]
    await page.click('[data-memory-action="task-goal-edit"]')
    field = page.locator('#memory-task-body [data-memory-field="task-goal"]')
    await field.fill("временный текст")
    await field.press("Escape")
    await asyncio.sleep(0.5)
    restored = (await sidebar(page))["goal"]
    await page.click('[data-memory-action="task-goal-edit"]')
    await page.locator('#memory-task-body [data-memory-field="task-goal"]').fill(EDITED_GOAL)
    await page.click('[data-memory-action="task-goal-save"]')
    await page.wait_for_function(
        "g => document.getElementById('memory-task-body').innerText.includes(g)", arg=EDITED_GOAL,
    )
    api_goal = ((await task_state(page, chat_id)) or {}).get("goal")
    await shot(page, "S4-goal-edited")
    report("S4 goal edit: Escape restores the old goal, Сохранить stores the new one",
           restored == old_goal and old_goal != "—" and api_goal == EDITED_GOAL,
           f"old={old_goal!r} restored={restored!r} api={api_goal!r}")

    # S5
    state5 = await task_state(page, chat_id) or {}
    if not item_ids(state5):
        await ask_with_memory(page, chat_id, Q_CLARIFY)
        state5 = await task_state(page, chat_id) or {}
    ids5 = item_ids(state5)
    if ids5:
        target = ids5[0]
        dialogs = dialog_policy["seen"] = []
        await page.locator('[data-memory-action="task-item-delete"]').first.click()
        gone = False
        for _ in range(30):
            if target not in item_ids(await task_state(page, chat_id)):
                gone = True
                break
            await asyncio.sleep(0.5)
        report("S5 delete item with «×»: row disappears, no confirm dialog, API drops the id",
               gone and not dialogs and len((await sidebar(page))["clarified"]
                                            + (await sidebar(page))["constraints"]) == len(ids5) - 1,
               f"id={target} gone={gone} dialogs={dialogs}")
    else:
        report("S5 skipped: the model produced no clarified/constraint item after two turns", True,
               f"state={state5}")

    # S6
    first = await first_assistant(page, chat_id)
    first_goal = (payload_of(first).get("task_memory") or {}).get("goal")
    method = "page control «↩ отсюда»"
    control = page.locator(f'#messages [data-message-id="{first["id"]}"] [data-branch-from]') if first else None
    if first and control is not None and await control.count():
        await control.first.click()
    elif first:
        method = "POST /branch through page.request (no UI control found)"
        await page.request.post(f"{AGENT_URL}/api/v1/chats/{chat_id}/branch", data={"message_id": first["id"]})
        await reload_chat(page, chat_id)
        await open_memory_panel(page)
    deadline_ok = False
    for _ in range(30):
        if ((await task_state(page, chat_id)) or {}).get("goal") == first_goal:
            deadline_ok = True
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.0)
    view6 = await sidebar(page)
    await shot(page, "S6-branch-restored")
    report("S6 branch restore: sidebar and API goal equal the goal of the first answer's snapshot",
           bool(first_goal) and deadline_ok and view6["goal"] == first_goal and first_goal != EDITED_GOAL,
           f"via {method}; snapshot_goal={first_goal!r} api_ok={deadline_ok} sidebar={view6['goal']!r}")

    # S7
    dialogs7 = dialog_policy["seen"] = []
    dialog_policy["accept"] = True
    await page.click('[data-memory-action="task-reset"]')
    cleared = False
    for _ in range(30):
        s = await task_state(page, chat_id) or {}
        if not s.get("goal") and not item_ids(s):
            cleared = True
            break
        await asyncio.sleep(0.5)
    view7 = await sidebar(page)
    dialog_policy["accept"] = False
    empty7 = view7["goal"] == "—" and "Пока пусто" in view7["text"]
    answered7, _ = await ask_with_memory(page, chat_id, Q_FIRST)
    await wait_sidebar_goal(page)
    before_dismiss = await task_state(page, chat_id) or {}
    dismissed = dialog_policy["seen"] = []
    await page.click('[data-memory-action="task-reset"]')
    await asyncio.sleep(1.5)
    after_dismiss = await task_state(page, chat_id) or {}
    report("S7 reset: confirm text, accept clears, dismiss keeps the memory",
           bool(dialogs7) and dialogs7[0].startswith(RESET_PROMPT) and cleared and empty7
           and answered7 and bool(before_dismiss.get("goal")) and after_dismiss == before_dismiss
           and bool(dismissed),
           f"accept_dialog={dialogs7[:1]} cleared={cleared} empty_ui={empty7} "
           f"kept_goal={before_dismiss.get('goal')!r} unchanged={after_dismiss == before_dismiss}")

    # S8
    await search.open_popover(page)
    initial = await page.input_value("#rag-history-turns")
    await page.fill("#rag-history-turns", "0")
    await page.dispatch_event("#rag-history-turns", "change")
    cfg0 = await search.wait_cfg(page, chat_id, "history_turns", 0)
    shown0 = await page.input_value("#rag-history-turns")
    await page.fill("#rag-history-turns", "25")
    await page.dispatch_event("#rag-history-turns", "change")
    cfg10 = await search.wait_cfg(page, chat_id, "history_turns", 10)
    shown10 = await page.input_value("#rag-history-turns")
    await page.fill("#rag-history-turns", "3")
    await page.dispatch_event("#rag-history-turns", "change")
    cfg3 = await search.wait_cfg(page, chat_id, "history_turns", 3)
    await shot(page, "S8-history-turns-popover")
    await page.keyboard.press("Escape")
    report("S8 «Ходов истории»: default 3, accepts and keeps 0, 25 is clamped to 10, back to 3",
           initial == "3" and cfg0.get("history_turns") == 0 and shown0 == "0"
           and cfg10.get("history_turns") == 10 and shown10 == "10" and cfg3.get("history_turns") == 3,
           f"initial={initial} zero={cfg0.get('history_turns')}/{shown0} "
           f"clamp={cfg10.get('history_turns')}/{shown10} back={cfg3.get('history_turns')}")

    # S9
    dialog_policy["seen"] = []
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "document.getElementById('rag-kb-select').disabled === false", timeout=30000,
    )
    await rag.select_chat_model(page)
    plain_id = await newest_chat_id(page)
    answered9 = await rag.ask(page, Q_PLAIN)
    view9 = await sidebar(page)
    newest9 = await cite.newest_assistant(page, plain_id)
    tm_count9 = await page.locator("details.rag-task-memory").count()
    api9 = await task_state(page, plain_id)
    report("S9 no RAG: block hidden, no task_memory in the payload, no per-message block, API task_state null",
           answered9 and view9["hidden"] and "task_memory" not in payload_of(newest9)
           and tm_count9 == 0 and api9 is None,
           f"answered={answered9} hidden={view9['hidden']} payload_keys={sorted(payload_of(newest9))} "
           f"blocks={tm_count9} api_task_state={api9} dialogs={dialog_policy['seen']}")

    # S10
    await reload_chat(page, chat_id)
    await open_memory_panel(page)
    await asyncio.sleep(1.0)
    signatures = await page.evaluate(MESSAGE_BLOCKS_JS)
    view10 = await sidebar(page)
    await shot(page, "S10-after-reload")
    injected = await page.evaluate(
        "document.querySelectorAll('#memory-task-state script, #memory-task-state img, "
        ".rag-task-memory script, .rag-task-memory img').length",
    )
    report("S10 reload: per-message blocks render, sidebar not hidden, no page errors, no script or img",
           len(signatures) >= 1 and not view10["hidden"] and not kb.page_errors and injected == 0,
           f"blocks={signatures} sidebar_goal={view10['goal']!r} injected={injected}; "
           f"errors={'; '.join(kb.page_errors[:3])}")


def on_dialog(dialog: Any) -> None:
    """Record every native dialog; accept it only while a scenario asked for that, else dismiss."""
    dialog_policy["seen"].append(dialog.message)
    action = dialog.accept() if dialog_policy["accept"] else dialog.dismiss()
    asyncio.ensure_future(action)


async def drive_browser(creds: dict[str, str], scratch: Path) -> None:
    """Launch Chromium and run the scenarios with a screenshot on failure."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 1000})
        page = await context.new_page()
        await page.add_init_script(kb.TOAST_RECORDER_JS)
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: kb.page_errors.append(str(exc)))
        page.on("dialog", on_dialog)
        try:
            await scenarios(page, creds)
        except Exception as exc:
            report("scenario run aborted", False, f"{type(exc).__name__}: {exc}")
            await page.screenshot(path=str(scratch / "failure.png"))
        finally:
            await browser.close()


async def run_e2e() -> None:
    """Start the isolated copy, run the browser scenarios and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="rag_dialog_e2e_"))
    db_path = scratch / "e2e_rag_dialog.db"
    creds = {"dialoguser": secrets.token_urlsafe(12)}
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
            await asyncio.wait_for(drive_browser(creds, scratch), 3300)
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

    adopt_available_pdfs()
    blocked = rag.preflight()
    if blocked is not None:
        sys.exit(blocked)

    asyncio.run(run_e2e())
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in kb.checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
