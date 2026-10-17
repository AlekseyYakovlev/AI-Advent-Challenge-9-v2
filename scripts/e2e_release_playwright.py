"""Browser check of the /release pipeline on an isolated copy against a scratch GitHub repository.

Usage: python scripts/e2e_release_playwright.py [--out PATH] [--keep-temp] [--dry-check]

Inputs come from the environment only and are never printed or written:
  RELEASE_SCRATCH_REMOTE  SSH URL (or ~/.ssh/config host alias) of a scratch GitHub repository
  RELEASE_SCRATCH_TOKEN   fine-grained PAT with Contents: write on that scratch repository only

The script clones the scratch repository into a temporary directory, creates a fresh
`e2e-<timestamp>` branch with seeded commits and tags, copies the working tree to a temporary
directory, repoints the copy to UI :18000 / Agent :18001 with a scratch database, adds the
bundled git server as `release-git` (with --allow-release) pointed at the clone and drives the
four sandbox scenarios of /release through the browser with the real local LM Studio model.
The project repository, the ports 8000/8001 of a running app and the working copy are never
touched; only processes started by this script are ever stopped. Nothing is ever force-pushed.

--dry-check runs only the safety preflight and prints the result.

Exit codes: 0 pass, 1 fail, 2 blocked (remote missing or unsafe, ports 18000/18001 busy,
LM Studio unreachable, no loaded chat model, git missing), 4 Playwright not installed.
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_kb_playwright as kb  # noqa: E402
import review_control as rc  # noqa: E402

REPO_ROOT: Path = kb.REPO_ROOT
sys.path.insert(0, str(REPO_ROOT))

UI_PORT: int = 18000
AGENT_PORT: int = 18001
UI_URL: str = kb.UI_URL
AGENT_URL: str = kb.AGENT_URL
LM_URL: str = kb.LM_URL
SERVER_NAME: str = "release-git"
PROJECT_REPO_MARKER: str = "ai-advent-challenge-9-v2"
PREFERRED_MODEL: str = "qwen/qwen3.5-9b"
DEFAULT_OUT: str = ".planning/phases/27-ai-release-pipeline-day-35/27-CONTROL-RUNS.json"
ENV_REMOTE: str = "RELEASE_SCRATCH_REMOTE"
ENV_TOKEN: str = "RELEASE_SCRATCH_TOKEN"
TOKEN_ENV_NAME: str = "GITHUB_PERSONAL_ACCESS_TOKEN"
STEP_TIMEOUT_MS: int = 20000
GIT_TIMEOUT: int = 120
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4

chat_model: dict[str, str] = {}
page_errors: list[str] = []


@dataclass
class ScratchRepo:
    """A cloned scratch repository with its release branch and seeded tags."""

    clone: Path
    remote: str
    branch: str
    major: int
    baseline_tag: str
    taken_tag: str


@dataclass
class AppHandle:
    """The isolated app copy: process, directories and the scratch credentials."""

    scratch: Path
    copy_dir: Path
    proc: asyncio.subprocess.Process
    log_file: Any
    user: str
    password: str


@dataclass
class Frames:
    """WebSocket frames received by the page, in arrival order."""

    items: list[dict[str, Any]] = field(default_factory=list)


def register_secrets() -> None:
    """Register the scratch token with the scrubber so no output or file can contain it."""
    rc.register_secrets()
    token = os.environ.get(ENV_TOKEN, "")
    if token and token not in rc._SECRETS:  # noqa: SLF001 - reuse the shared scrubber's list
        rc._SECRETS.append(token)  # noqa: SLF001


def say(message: str) -> None:
    """Print one console line without secrets."""
    print(rc.scrub(message), flush=True)


def pick_chat_model() -> str | None:
    """Return the preferred loaded chat model, else the first loaded non-embedding one."""
    data = httpx.get(f"{LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
    loaded = [m["id"] for m in data if m.get("state") == "loaded" and m.get("type") != "embeddings"]
    if PREFERRED_MODEL in loaded:
        return PREFERRED_MODEL
    return loaded[0] if loaded else None


def blocked_exit(reason: str) -> int:
    """Print the blocked reason and the NOT RUN result line, return the blocked exit code."""
    say(f"BLOCKED: {reason}")
    say(f"RESULT: {rc.NOT_RUN} ({reason})")
    return EXIT_BLOCKED


def git_env() -> dict[str, str]:
    """Process environment without GIT_* overrides and without any terminal prompt."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git in the scratch clone with a fixed identity; output is scrubbed on failure."""
    cmd = [
        "git", "-c", "user.name=E2E", "-c", "user.email=e2e@local",
        "-c", "core.autocrlf=false", *args,
    ]
    done = subprocess.run(
        cmd, cwd=str(cwd), env=git_env(), capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT,
    )
    if check and done.returncode != 0:
        raise RuntimeError(rc.scrub(f"git {args[0]}: {done.stderr.strip()[:300]}"))
    return done


def owner_repo(remote: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from an SSH or scp-like remote; None when it has no such path."""
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", remote.strip())
    return (match.group(1), match.group(2)) if match else None


def project_origin() -> str:
    """Return the project's own origin URL, or an empty string when it cannot be read."""
    done = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=str(REPO_ROOT), env=git_env(),
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def unsafe_remote_reason(remote: str, origin: str) -> str | None:
    """Return why a remote must not be used for the run, or None when it is acceptable."""
    if not remote.strip():
        return f"{ENV_REMOTE} не задан"
    lowered = remote.strip().lower()
    if lowered.startswith(("http://", "https://")):
        return "адрес scratch-репозитория должен быть SSH, HTTPS с данными доступа не допускается"
    if PROJECT_REPO_MARKER in lowered:
        return "scratch-репозиторий указывает на репозиторий проекта"
    if origin:
        same = owner_repo(remote) is not None and owner_repo(remote) == owner_repo(origin)
        if same or lowered.rstrip("/") == origin.lower().rstrip("/"):
            return "scratch-репозиторий совпадает с origin проекта"
    return None


def safety_preflight(require_playwright: bool) -> int | None:
    """Return an exit code when the run must not start, else None."""
    remote = os.environ.get(ENV_REMOTE, "")
    reason = unsafe_remote_reason(remote, project_origin())
    if reason:
        return blocked_exit(reason)
    if not os.environ.get(ENV_TOKEN):
        say(f"{rc.NOT_RUN}: {ENV_TOKEN} не задан; сценарии с черновиком Release не запустятся")
    busy = [p for p in (UI_PORT, AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        return blocked_exit(f"порты {busy} заняты; изолированной копии нужны свободные")
    if shutil.which("git") is None:
        return blocked_exit("git не установлен")
    try:
        model = pick_chat_model()
    except (httpx.HTTPError, ValueError) as exc:
        return blocked_exit(f"LM Studio недоступен ({type(exc).__name__})")
    if model is None:
        return blocked_exit("в LM Studio нет загруженной чат-модели")
    chat_model["id"] = model
    say(f"chat model: {model}")
    if require_playwright:
        try:
            import playwright  # noqa: F401
        except ImportError:
            say("Playwright is not installed (pip install playwright && playwright install chromium).")
            say(f"RESULT: {rc.NOT_RUN} (Playwright is not installed)")
            return EXIT_NO_PLAYWRIGHT
    return None


def remote_tags(clone: Path) -> set[str]:
    """Return the tag names that exist on the scratch remote right now."""
    out = run_git(clone, "ls-remote", "--refs", "origin", "refs/tags/*").stdout
    return {line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line}


def highest_major(tags: set[str]) -> int:
    """Return the highest major among vN.M tags, or 0 when there are none."""
    majors = [int(m.group(1)) for t in tags if (m := re.fullmatch(r"v(\d+)\.\d+", t))]
    return max(majors, default=0)


def commit_file(clone: Path, subject: str, text: str) -> None:
    """Append a line to .planning/p.md and commit it with the given subject."""
    path = clone / ".planning" / "p.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    run_git(clone, "add", ".planning/p.md")
    run_git(clone, "commit", "-m", subject)


def setup_scratch_repo(remote: str, workdir: Path) -> ScratchRepo:
    """Clone the scratch remote and seed an `e2e-<timestamp>` branch, a baseline tag and a taken tag."""
    clone = workdir / "scratch-clone"
    done = subprocess.run(
        ["git", "clone", remote, str(clone)], env=git_env(), capture_output=True, text=True,
        encoding="utf-8", stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT,
    )
    if done.returncode != 0:
        raise RuntimeError(rc.scrub(f"git clone: {done.stderr.strip()[:300]}"))
    major = highest_major(remote_tags(clone))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    branch = f"e2e-{stamp}"
    baseline = f"v{major + 1}.0"
    taken = f"v{major + 2}.0"
    run_git(clone, "checkout", "-b", branch)
    commit_file(clone, "chore(e2e): baseline", "baseline")
    run_git(clone, "tag", "-a", baseline, "-m", f"{baseline} Week {major + 3}: e2e baseline")

    # The next-major tag lives on a commit that is not part of the release branch (D-13).
    run_git(clone, "checkout", "-b", f"{branch}-taken")
    commit_file(clone, "chore(e2e): taken marker", "taken")
    run_git(clone, "tag", taken)
    run_git(clone, "checkout", branch)
    run_git(clone, "branch", "-D", f"{branch}-taken")

    commit_file(clone, "feat(90-01): add alpha", "alpha")
    commit_file(clone, "fix(90-02): fix alpha", "alpha fix")
    commit_file(clone, "docs(90-01): plan", "plan")
    run_git(clone, "checkout", "-b", f"{branch}-side")
    commit_file(clone, "feat(91-01): add beta", "beta")
    run_git(clone, "checkout", branch)
    commit_file(clone, "chore: bump", "bump")
    run_git(clone, "merge", "--no-ff", f"{branch}-side", "-m", "Merge branch 'Day90': e2e merge")
    run_git(clone, "branch", "-D", f"{branch}-side")

    run_git(clone, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
    for tag in (baseline, taken):
        run_git(clone, "push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    run_git(clone, "branch", "--set-upstream-to", f"origin/{branch}", branch)
    return ScratchRepo(clone, remote, branch, major, baseline, taken)


def capture_frames(page: Any) -> Frames:
    """Record every parsed JSON frame received on the chat WebSocket into the returned holder."""
    frames = Frames()

    def on_frame(payload: Any) -> None:
        raw = payload.get("payload") if isinstance(payload, dict) else payload
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return
        if isinstance(data, dict):
            frames.items.append(data)

    def on_socket(ws: Any) -> None:
        if "/ws/chat/" in ws.url:
            ws.on("framereceived", on_frame)

    page.on("websocket", on_socket)
    return frames


async def api(page: Any, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an Agent REST endpoint from the logged-in page; returns {status, body}."""
    script = (
        "async ([url, method, body]) => {"
        " const r = await fetch(url, {method, credentials: 'include',"
        " headers: {'Content-Type': 'application/json'},"
        " body: body === null ? undefined : JSON.stringify(body)});"
        " let data = null; try { data = await r.json(); } catch (e) { data = null; }"
        " return {status: r.status, body: data}; }"
    )
    return await page.evaluate(script, [f"{AGENT_URL}{path}", method, body])


async def add_release_server(
    page: Any, copy_dir: Path, clone: Path, env: dict[str, str] | None,
) -> int | None:
    """Create and connect the release-git entry for the clone; return its id, None on failure."""
    script = copy_dir / "mcp_servers" / "project_git.py"
    created = await api(page, "POST", "/api/v1/mcp/servers", {
        "name": SERVER_NAME,
        "command": sys.executable,
        "args": [str(script), f"--root={clone}", "--allow-release", "--timeout=60"],
        "env": env or {},
        "cwd": str(clone),
    })
    if created["status"] not in (200, 201) or not created["body"]:
        say(f"release-git was not created: HTTP {created['status']}")
        return None
    server_id = int(created["body"]["id"])
    return server_id if await connect_server(page, server_id) else None


async def connect_server(page: Any, server_id: int) -> bool:
    """Connect an MCP server entry and report whether it is connected."""
    result = await api(page, "POST", f"/api/v1/mcp/servers/{server_id}/connect")
    body = result["body"] or {}
    status = (body.get("connection") or {}).get("status")
    return result["status"] == 200 and status == "connected"


async def update_release_server_env(page: Any, server_id: int, env: dict[str, str]) -> bool:
    """Replace the server env (the edit disconnects it) and reconnect."""
    result = await api(page, "PUT", f"/api/v1/mcp/servers/{server_id}", {"env": env})
    return result["status"] == 200 and await connect_server(page, server_id)


async def start_isolated_app(workdir: Path) -> AppHandle | None:
    """Copy the working tree, seed a scratch database, start the copy; None when it is not up."""
    scratch = workdir
    db_path = scratch / "e2e_release.db"
    creds = {"releaseuser": secrets.token_urlsafe(12)}
    copy_dir = kb.prepare_copy(scratch)
    kb.report("isolated copy patched to ports 18000/18001", True, str(copy_dir))
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
    for name in (ENV_TOKEN, ENV_REMOTE):
        env.pop(name, None)
    log_file = open(scratch / "app.log", "wb")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(copy_dir / "run.py"),
        cwd=str(copy_dir), env=env, stdout=log_file, stderr=log_file,
    )
    kb.app_proc_holder["proc"] = proc
    async with httpx.AsyncClient(timeout=3.0) as client:
        ready = await kb.wait_for_app(client)
    kb.report("isolated app instance is up (UI :18000 + Agent :18001)", ready)
    if not ready:
        await kb.teardown(proc)
        log_file.close()
        return None
    return AppHandle(scratch, copy_dir, proc, log_file, "releaseuser", creds["releaseuser"])


PLAN_TYPES: tuple[str, ...] = ("release_plan", "release_result", "notice")
RESULT_TYPES: tuple[str, ...] = ("release_result", "notice")
PLAN_TIMEOUT: float = 480.0
CONFIRM_TIMEOUT: float = 300.0
SCENARIO_TITLES: dict[int, str] = {
    1: "Полный путь: коммит, тег, push, черновик Release",
    2: "Нет доступа к GitHub: частичный результат и повтор",
    3: "Блокировки: грязное дерево, смена HEAD, версия",
    4: "Ручной диапазон, версия и название",
}
REASON_NO_TOKEN: str = "no_token"
MANUAL_TITLE: str = "E2E ручное название"
CONFIRM_BUTTON = re.compile("Подтвердить релиз|Повторить оставшиеся шаги")


@dataclass
class Ctx:
    """Everything a scenario needs: the page, the captured frames and the scratch repository."""

    page: Any
    frames: Frames
    app: AppHandle
    repo: ScratchRepo
    token: str | None
    server_id: int
    plans: list[dict[str, Any]] = field(default_factory=list)


def new_scenario(sid: int) -> dict[str, Any]:
    """Create the result record of one scenario."""
    return {
        "id": sid, "title": SCENARIO_TITLES[sid], "status": "passed", "checks": [],
        "reason": "", "reason_code": "", "release_url": None, "tag": None,
        "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
    }


def check(sc: dict[str, Any], label: str, ok: bool, detail: str = "") -> bool:
    """Record one check on a scenario and print it; a failed check fails the scenario."""
    shown = rc.scrub(detail)[:300]
    kb.report(f"[{sc['id']}] {label}", ok, shown)
    sc["checks"].append({"label": label, "ok": ok, "detail": shown})
    if not ok:
        sc["status"] = "failed"
    return ok


def mark_not_run(sc: dict[str, Any], reason: str, code: str = "") -> None:
    """Record a scenario (or its remaining part) as not run unless it already failed."""
    sc["reason"], sc["reason_code"] = reason, code
    if sc["status"] != "failed":
        sc["status"] = rc.NOT_RUN


async def wait_frame(
    frames: Frames, start: int, types: tuple[str, ...], timeout: float,
) -> dict[str, Any] | None:
    """Wait for the first frame of one of the types received after index `start`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in frames.items[start:]:
            if frame.get("type") in types:
                return frame
        await asyncio.sleep(0.5)
    return None


def frames_text(frames: Frames, start: int) -> str:
    """Return all frames received after `start` as one JSON string for substring checks."""
    return json.dumps(frames.items[start:], ensure_ascii=False)


def step_map(frame: dict[str, Any]) -> dict[str, str]:
    """Return step key -> status of a plan or result frame."""
    return {s["key"]: s["status"] for s in frame.get("steps", [])}


async def wait_socket(page: Any) -> None:
    """Wait until the WebSocket belongs to the current chat."""
    await page.wait_for_function(
        "() => state.ws && state.ws.readyState === WebSocket.OPEN"
        " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
        timeout=15000,
    )


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


async def new_chat(page: Any) -> None:
    """Open a fresh chat with the chosen local model selected."""
    await page.click("#btn-new-chat")
    await page.wait_for_function(
        "() => state.chats.length >= 1 && state.currentChatId === state.chats[0].id",
        timeout=15000,
    )
    await wait_socket(page)
    await select_chat_model(page)


async def send_text(ctx: Ctx, text: str) -> int:
    """Send a chat message and return the index of the first frame that can answer it."""
    start = len(ctx.frames.items)
    await wait_socket(ctx.page)
    await ctx.page.fill("#message-input", text)
    await ctx.page.click("#btn-send")
    return start


def last_card(ctx: Ctx) -> Any:
    """Locate the newest release plan card."""
    return ctx.page.locator(".release-plan").last


async def click_confirm(ctx: Ctx) -> int:
    """Click the confirm / retry button of the newest card; return the frame start index."""
    start = len(ctx.frames.items)
    await last_card(ctx).get_by_role("button", name=CONFIRM_BUTTON).click()
    return start


def record_plan(
    ctx: Ctx, sc: dict[str, Any], frame: dict[str, Any], phases: list[str], days: list[str],
) -> None:
    """Run the D-17 code check on a plan frame and keep the plan for the results file."""
    from agent.release_pipeline import ALLOWED_HEADINGS, changelog_coverage, parse_changelog

    body = str(frame.get("changelog") or "")
    headings = [line.strip() for line in body.splitlines() if line.lstrip().startswith("#")]
    headings_ok = all(h in ALLOWED_HEADINGS for h in headings)
    parsed = parse_changelog(body)
    missing = changelog_coverage(body, phases, days)
    check(sc, "D-17: журнал изменений разобран, заголовки допустимы",
          parsed is not None and headings_ok, f"headings={headings}")
    say(f"[{sc['id']}] D-17 покрытие: не упомянуто {missing or 'ничего'}")
    ctx.plans.append({
        "scenario": sc["id"], "range": frame.get("range"), "version": frame.get("version"),
        "candidates": frame.get("candidates"), "title": frame.get("title"),
        "rationale": frame.get("rationale"), "counts": frame.get("counts"),
        "changelog": body, "coverage_missing": missing,
        "coverage_missing_frame": frame.get("coverage_missing"),
        "changelog_valid": parsed is not None and headings_ok,
        "model": chat_model.get("id"),
    })


async def plan_for(ctx: Ctx, text: str = "/release") -> tuple[int, dict[str, Any] | None]:
    """Send /release and wait for the plan frame (or a notice)."""
    start = await send_text(ctx, text)
    return start, await wait_frame(ctx.frames, start, PLAN_TYPES, PLAN_TIMEOUT)


def draft_release_exists(remote: str, token: str, tag: str) -> tuple[bool, str]:
    """Check through the GitHub REST API that a draft Release exists for the tag."""
    pair = owner_repo(remote)
    if pair is None:
        return False, "не удалось определить owner/repo"
    headers = {**rc.GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    url = f"https://api.github.com/repos/{pair[0]}/{pair[1]}/releases?per_page=100"
    try:
        response = httpx.get(url, headers=headers, timeout=30.0, trust_env=False)
    except httpx.HTTPError as exc:
        return False, type(exc).__name__
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    for release in response.json():
        if release.get("tag_name") == tag:
            return bool(release.get("draft")), f"draft={release.get('draft')}"
    return False, "Release для тега не найден"


async def scenario_blocks(ctx: Ctx) -> dict[str, Any]:
    """Scenario 3: dirty tree, moved HEAD, taken and not-greater versions (D-09, D-13, D-21)."""
    sc = new_scenario(3)
    clone, repo = ctx.repo.clone, ctx.repo
    await new_chat(ctx.page)
    tags_before = remote_tags(clone)

    dirty = clone / "untracked_e2e.txt"
    dirty.write_text("x\n", encoding="utf-8")
    try:
        start, frame = await plan_for(ctx)
        check(sc, "грязное дерево: имя файла названо в ответе",
              "untracked_e2e.txt" in frames_text(ctx.frames, start))
        if frame is not None and frame["type"] == "release_plan":
            text = await last_card(ctx).inner_text()
            check(sc, "карточка со статусом «Заблокировано»", "Заблокировано" in text)
    finally:
        dirty.unlink(missing_ok=True)

    start, frame = await plan_for(ctx)
    if not check(sc, "чистое дерево: план построен", bool(frame and frame["type"] == "release_plan"
                                                          and not frame.get("blocked"))):
        return sc
    record_plan(ctx, sc, frame, ["90", "91"], ["Day90"])
    commit_file(clone, "chore(e2e): move head", "head move")
    start = await click_confirm(ctx)
    result = await wait_frame(ctx.frames, start, RESULT_TYPES, CONFIRM_TIMEOUT)
    check(sc, "смена HEAD: блокировка «HEAD изменился»",
          result is not None and "HEAD изменился" in frames_text(ctx.frames, start))
    recreate = await last_card(ctx).get_by_role("button", name="Пересоздать план").count()
    check(sc, "кнопка «Пересоздать план» показана", recreate > 0)

    start, frame = await plan_for(ctx)
    if not check(sc, "новый план построен", bool(frame and frame["type"] == "release_plan")):
        return sc
    record_plan(ctx, sc, frame, ["90", "91"], ["Day90"])
    check(sc, f"занятый тег {repo.taken_tag} не предложен кандидатом",
          repo.taken_tag not in (frame.get("candidates") or []), str(frame.get("candidates")))
    plan_id = str(frame["plan_id"])
    await ctx.page.fill(f"#release-version-{plan_id}", repo.baseline_tag)
    error = await ctx.page.locator(f"#release-version-error-{plan_id}").inner_text()
    disabled = await last_card(ctx).get_by_role("button", name=CONFIRM_BUTTON).is_disabled()
    check(sc, "версия не больше последнего тега: ошибка и кнопка отключена",
          "Версия должна быть больше последнего тега" in error and disabled, error)
    start = await send_text(ctx, f"/release confirm {repo.taken_tag}")
    result = await wait_frame(ctx.frames, start, RESULT_TYPES, CONFIRM_TIMEOUT)
    check(sc, "занятая версия: блокировка «уже существует как тег»",
          result is not None
          and f"Версия {repo.taken_tag} уже существует как тег" in frames_text(ctx.frames, start))
    check(sc, "на удалённом репозитории не появилось новых тегов",
          remote_tags(clone) == tags_before)
    return sc


async def scenario_no_access(ctx: Ctx) -> dict[str, Any]:
    """Scenario 2: no GitHub write access gives a partial result and a retry (D-05)."""
    sc = new_scenario(2)
    clone = ctx.repo.clone
    await new_chat(ctx.page)
    start, frame = await plan_for(ctx)
    if not check(sc, "план построен", bool(frame and frame["type"] == "release_plan"
                                           and not frame.get("blocked"))):
        return sc
    record_plan(ctx, sc, frame, ["90", "91"], ["Day90"])
    start = await click_confirm(ctx)
    result = await wait_frame(ctx.frames, start, ("release_result",), CONFIRM_TIMEOUT)
    if not check(sc, "получен результат подтверждения", result is not None):
        return sc
    steps = step_map(result)
    sc["tag"] = result.get("tag")
    check(sc, "коммит, тег и push выполнены",
          all(steps.get(k) == "done" for k in ("commit", "tag", "push")), str(steps))
    check(sc, "черновик Release недоступен", steps.get("release") == "unavailable", str(steps))
    check(sc, "итог «partial» и текст релиза для ручного создания",
          result.get("outcome") == "partial" and bool(result.get("release_text")))
    check(sc, "заголовок карточки «Частично выполнено»",
          "Частично выполнено" in await last_card(ctx).inner_text())
    if not ctx.token:
        mark_not_run(sc, f"{ENV_TOKEN} не задан: повтор с токеном не выполнен", REASON_NO_TOKEN)
        return sc

    updated = await update_release_server_env(ctx.page, ctx.server_id, {TOKEN_ENV_NAME: ctx.token})
    check(sc, "токен добавлен в окружение сервера, сервер переподключён", updated)
    start = await click_confirm(ctx)
    result = await wait_frame(ctx.frames, start, ("release_result",), CONFIRM_TIMEOUT)
    if not check(sc, "получен результат повтора", result is not None):
        return sc
    steps = step_map(result)
    check(sc, "шаги 1-3 пропущены, Release выполнен",
          all(steps.get(k) == "skipped" for k in ("commit", "tag", "push"))
          and steps.get("release") == "done", str(steps))
    url = str(result.get("release_url") or "")
    sc["release_url"] = url
    check(sc, "ссылка на Release ведёт на github.com", url.startswith("https://github.com/"))
    link = await last_card(ctx).get_by_role("link", name="Черновик GitHub Release").count()
    check(sc, "ссылка «Черновик GitHub Release» показана", link > 0)
    is_draft, detail = draft_release_exists(ctx.repo.remote, ctx.token, str(result.get("tag")))
    check(sc, "по данным GitHub REST Release является черновиком", is_draft, detail)
    return sc


async def scenario_full(ctx: Ctx) -> dict[str, Any]:
    """Scenario 1: new commit, plan, confirm, all four steps done."""
    sc = new_scenario(1)
    clone, repo = ctx.repo.clone, ctx.repo
    if not ctx.token:
        mark_not_run(sc, f"{ENV_TOKEN} не задан", REASON_NO_TOKEN)
        return sc
    commit_file(clone, "feat(92-01): add gamma", "gamma")
    await new_chat(ctx.page)
    start, frame = await plan_for(ctx)
    if not check(sc, "план построен", bool(frame and frame["type"] == "release_plan"
                                           and not frame.get("blocked"))):
        return sc
    record_plan(ctx, sc, frame, ["92"], [])
    start = await click_confirm(ctx)
    result = await wait_frame(ctx.frames, start, ("release_result",), CONFIRM_TIMEOUT)
    if not check(sc, "получен результат подтверждения", result is not None):
        return sc
    steps = step_map(result)
    sc["tag"], sc["release_url"] = result.get("tag"), result.get("release_url")
    check(sc, "все четыре шага выполнены",
          all(steps.get(k) == "done" for k in ("commit", "tag", "push", "release")), str(steps))
    check(sc, "заголовок карточки «Готово»", "Готово" in await last_card(ctx).inner_text())
    link = await last_card(ctx).get_by_role("link", name="Черновик GitHub Release").count()
    check(sc, "ссылка на черновик Release показана", link > 0)
    check(sc, "тег существует на удалённом репозитории", str(result.get("tag")) in remote_tags(clone))
    head = run_git(clone, "show", f"origin/{repo.branch}:CHANGELOG.md").stdout[:400]
    check(sc, "CHANGELOG.md в отправленной ветке начинается с новой версии",
          str(result.get("version")) in head, head[:80])
    return sc


async def scenario_manual(ctx: Ctx) -> dict[str, Any]:
    """Scenario 4: explicit range, overridden version and title reach the tag message."""
    sc = new_scenario(4)
    clone, repo = ctx.repo.clone, ctx.repo
    if not ctx.token:
        mark_not_run(sc, f"{ENV_TOKEN} не задан", REASON_NO_TOKEN)
        return sc
    spec = f"{repo.baseline_tag}..HEAD"
    await new_chat(ctx.page)
    start, frame = await plan_for(ctx, f"/release {spec}")
    if not check(sc, "план построен для ручного диапазона", bool(
            frame and frame["type"] == "release_plan" and not frame.get("blocked"))):
        return sc
    record_plan(ctx, sc, frame, ["90", "91", "92"], ["Day90"])
    check(sc, "диапазон из команды показан в плане", frame.get("range") == spec,
          str(frame.get("range")))
    plan_id = str(frame["plan_id"])
    others = [c for c in frame.get("candidates") or [] if c != frame.get("version")]
    version = others[0] if others else str(frame.get("version"))
    await ctx.page.fill(f"#release-version-{plan_id}", version)
    await ctx.page.fill(f"#release-title-{plan_id}", MANUAL_TITLE)
    start = await click_confirm(ctx)
    result = await wait_frame(ctx.frames, start, ("release_result",), CONFIRM_TIMEOUT)
    if not check(sc, "получен результат подтверждения", result is not None):
        return sc
    sc["tag"], sc["release_url"] = result.get("tag"), result.get("release_url")
    check(sc, "релиз завершён с выбранной версией",
          result.get("outcome") == "done" and result.get("version") == version,
          f"{result.get('outcome')} {result.get('version')}")
    check(sc, "название в сообщении тега", MANUAL_TITLE in str(result.get("tag_message")))
    local_msg = run_git(clone, "tag", "-n99", "-l", version).stdout
    local_sha = run_git(clone, "rev-parse", f"refs/tags/{version}").stdout.strip()
    remote_line = run_git(clone, "ls-remote", "origin", f"refs/tags/{version}").stdout.split()
    check(sc, "аннотированный тег содержит название", MANUAL_TITLE in local_msg, local_msg[:100])
    check(sc, "тег на удалённом репозитории совпадает с локальным",
          bool(remote_line) and remote_line[0] == local_sha)
    return sc


async def run_scenarios(ctx: Ctx) -> list[dict[str, Any]]:
    """Run the four scenarios in the order 3, 2, 1, 4; one failing does not stop the others."""
    results: list[dict[str, Any]] = []
    for runner in (scenario_blocks, scenario_no_access, scenario_full, scenario_manual):
        try:
            sc = await runner(ctx)
        except Exception as exc:  # noqa: BLE001 - one broken scenario must not hide the others
            sid = {"scenario_blocks": 3, "scenario_no_access": 2,
                   "scenario_full": 1, "scenario_manual": 4}[runner.__name__]
            sc = new_scenario(sid)
            check(sc, "сценарий выполнен без ошибки", False,
                  f"{type(exc).__name__}: {rc.scrub(str(exc))}")
        sc["finished_at"] = datetime.now(timezone.utc).isoformat()
        results.append(sc)
    return results


async def drive_browser(
    app: AppHandle, repo: ScratchRepo, token: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Log in, add the release-git entry and run the scenarios; return results and plans."""
    from playwright.async_api import async_playwright

    results: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        frames = capture_frames(page)
        try:
            await kb.login(page, app.user, app.password)
            server_id = await add_release_server(page, app.copy_dir, repo.clone, None)
            kb.report("release-git added and connected", server_id is not None)
            if server_id is not None:
                ctx = Ctx(page, frames, app, repo, token, server_id)
                try:
                    results = await run_scenarios(ctx)
                finally:
                    plans = ctx.plans
        except Exception as exc:  # noqa: BLE001 - any browser failure must be reported, not raised
            kb.report("scenario run aborted", False, f"{type(exc).__name__}: {rc.scrub(str(exc))}")
            await page.screenshot(path=str(app.scratch / "failure.png"))
        finally:
            await browser.close()
    return results, plans


async def run_e2e(args: argparse.Namespace) -> int:
    """Set up the scratch repo and the isolated app, run everything, tear down."""
    started = datetime.now(timezone.utc).isoformat()
    workdir = Path(tempfile.mkdtemp(prefix="release_e2e_"))
    app: AppHandle | None = None
    results: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    try:
        repo = setup_scratch_repo(os.environ[ENV_REMOTE], workdir)
        kb.report("scratch branch and tags seeded", True, f"{repo.branch} {repo.baseline_tag}")
        app = await start_isolated_app(workdir)
        if app is not None:
            results, plans = await asyncio.wait_for(
                drive_browser(app, repo, os.environ.get(ENV_TOKEN) or None), 3000,
            )
    except Exception as exc:  # noqa: BLE001 - report any setup failure instead of crashing
        kb.report("run completed without an unexpected error", False,
                  f"{type(exc).__name__}: {rc.scrub(str(exc))}")
    finally:
        if app is not None:
            await kb.teardown(app.proc)
            app.log_file.close()
        if args.keep_temp:
            say(f"temp kept for inspection: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return finish(results, plans, args, started)


def complete_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exactly four scenario records; absent ones are recorded as not run."""
    by_id = {r["id"]: r for r in results}
    for sid in (1, 2, 3, 4):
        if sid not in by_id:
            sc = new_scenario(sid)
            mark_not_run(sc, "прогон прерван до запуска сценария")
            by_id[sid] = sc
    return [by_id[sid] for sid in (1, 2, 3, 4)]


def finish(
    results: list[dict[str, Any]], plans: list[dict[str, Any]], args: argparse.Namespace,
    started: str,
) -> int:
    """Write the token-free results file and return the exit code."""
    scenarios = complete_results(results)
    out = Path(args.out)
    out = out if out.is_absolute() else REPO_ROOT / out
    rc.write_json(out, {
        "scenarios": scenarios, "plans": plans, "model": chat_model.get("id"),
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "pytest": None,
    })
    say(f"results written: {out}")
    for sc in scenarios:
        say(f"SCENARIO {sc['id']}: {sc['status']} {sc['reason']}".rstrip())
    bad = [
        s for s in scenarios
        if s["status"] == "failed" or (s["status"] == rc.NOT_RUN and s["reason_code"] != REASON_NO_TOKEN)
    ]
    ok = not bad and any(s["status"] == "passed" for s in scenarios)
    say(f"RESULT: {'PASS' if ok else 'FAIL'} (model {chat_model.get('id', '?')})")
    return EXIT_PASS if ok else EXIT_FAIL


def main() -> None:
    """Parse arguments, run the safety preflight, then the end-to-end run."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Isolated-copy browser e2e of /release.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="path of the results JSON")
    parser.add_argument("--keep-temp", action="store_true", help="keep temporary directories")
    parser.add_argument("--dry-check", action="store_true", help="only run the safety preflight")
    args = parser.parse_args()
    register_secrets()
    blocked = safety_preflight(require_playwright=True)
    if blocked is not None:
        sys.exit(blocked)
    if args.dry_check:
        say("DRY-CHECK: OK")
        sys.exit(EXIT_PASS)
    sys.exit(asyncio.run(run_e2e(args)))


if __name__ == "__main__":
    main()
