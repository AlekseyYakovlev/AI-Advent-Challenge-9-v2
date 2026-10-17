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


async def run_scenarios(
    page: Any, frames: Frames, app: AppHandle, repo: ScratchRepo, token: str | None,
) -> list[dict[str, Any]]:
    """Run the scenarios; the list is filled in by the scenario functions."""
    return []


async def drive_browser(app: AppHandle, repo: ScratchRepo, token: str | None) -> list[dict[str, Any]]:
    """Log in, add the release-git entry and run the scenarios."""
    from playwright.async_api import async_playwright

    results: list[dict[str, Any]] = []
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
                results = await run_scenarios(page, frames, app, repo, token)
        except Exception as exc:  # noqa: BLE001 - any browser failure must be reported, not raised
            kb.report("scenario run aborted", False, f"{type(exc).__name__}: {rc.scrub(str(exc))}")
            await page.screenshot(path=str(app.scratch / "failure.png"))
        finally:
            await browser.close()
    return results


async def run_e2e(args: argparse.Namespace) -> int:
    """Set up the scratch repo and the isolated app, run everything, tear down."""
    workdir = Path(tempfile.mkdtemp(prefix="release_e2e_"))
    app: AppHandle | None = None
    results: list[dict[str, Any]] = []
    try:
        repo = setup_scratch_repo(os.environ[ENV_REMOTE], workdir)
        kb.report("scratch branch and tags seeded", True, f"{repo.branch} {repo.baseline_tag}")
        app = await start_isolated_app(workdir)
        if app is not None:
            results = await asyncio.wait_for(
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
    return finish(results, args)


def finish(results: list[dict[str, Any]], args: argparse.Namespace) -> int:
    """Return the exit code for the collected scenario results."""
    failed = [r for r in results if r["status"] == "failed"]
    return EXIT_PASS if results and not failed else EXIT_FAIL


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
