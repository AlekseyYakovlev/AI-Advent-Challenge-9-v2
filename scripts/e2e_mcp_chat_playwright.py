"""Browser check that a chat turn can call a real MCP filesystem server.

Usage: python scripts/e2e_mcp_chat_playwright.py

Starts an isolated app instance (scratch database, stub LLM on 127.0.0.1:18765) with the
real filesystem MCP server, drives it with Playwright and reports one PASS/FAIL line per check.

Exit codes: 0 all checks passed, 1 a check failed, 2 blocked by preflight (ports busy),
3 skipped (filesystem.exe missing), 4 Playwright not installed.
"""

import asyncio
import json
import os
import secrets
import shutil
import socket
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
UI_PORT: int = 8000
AGENT_PORT: int = 8001
STUB_PORT: int = 18765
UI_URL: str = f"http://{HOST}:{UI_PORT}"
AGENT_URL: str = f"http://{HOST}:{AGENT_PORT}"
FILESYSTEM_EXE: str = os.environ.get(
    "MCP_FILESYSTEM_EXE", r"C:\Users\Aleksey\go\bin\filesystem.exe",
)
ALLOWED_DIR: str = r"C:\Projects\AiAdventAgentV2"
TOOL_SUFFIX: str = "__list_allowed_directories"
BUILTIN_TOOLS: tuple[str, ...] = (
    "save_working_memory",
    "save_long_term_memory",
    "create_task",
    "transition_task",
    "pause_task",
    "resume_task",
)
STARTUP_TIMEOUT: float = 60.0
PLAYWRIGHT_TIMEOUT: float = 120.0
SUPERVISOR_STOP_TIMEOUT: float = 10.0
SWEEP_MAX_SECONDS: float = 15.0
SWEEP_MAX_ITERATIONS: int = 10

EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_SKIPPED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 3, 4

checks: list[tuple[str, bool, str]] = []
recorded_requests: list[dict[str, Any]] = []


def report(label: str, passed: bool, detail: str = "") -> bool:
    """Record and print one check result."""
    checks.append((label, passed, detail))
    suffix = f" - {detail}" if detail else ""
    print(f"{'PASS' if passed else 'FAIL'}: {label}{suffix}")
    return passed


def filesystem_pids() -> set[int]:
    """Return the PIDs of every running filesystem.exe process."""
    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == "filesystem.exe":
            pids.add(proc.pid)
    return pids


def port_is_free(port: int) -> bool:
    """Return True when nothing listens on the port (bind without address reuse)."""
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
    busy = [port for port in (UI_PORT, AGENT_PORT, STUB_PORT) if not port_is_free(port)]
    if busy:
        print(
            f"BLOCKED: port(s) {busy} are in use. run.py kills python processes on 8000/8001, "
            "so this script never starts while they are held.",
        )
        return EXIT_BLOCKED
    if not Path(FILESYSTEM_EXE).exists():
        print(f"SKIPPED: {FILESYSTEM_EXE} not found (set MCP_FILESYSTEM_EXE).")
        return EXIT_SKIPPED
    return None


def build_stub_app() -> Any:
    """Build the stub LLM: models, model control, and deterministic chat completions."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    stub = FastAPI()

    def sse(chunks: list[dict[str, Any]]) -> StreamingResponse:
        def body() -> Any:
            for chunk in chunks:
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    @stub.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"data": [{"id": "stub-model", "loaded": True}]}

    @stub.post("/api/v1/models/load")
    @stub.post("/api/v1/models/unload")
    async def model_control() -> dict[str, Any]:
        return {}

    @stub.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        body: dict[str, Any] = await request.json()
        recorded_requests.append(body)

        if body.get("stream") is not True:
            return JSONResponse(
                {
                    "id": "stub",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "{}"},
                            "finish_reason": "stop",
                        },
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        messages: list[dict[str, Any]] = body.get("messages", [])
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        target = next(
            (
                t["function"]["name"]
                for t in body.get("tools") or []
                if t["function"]["name"].endswith(TOOL_SUFFIX)
            ),
            None,
        )
        if target and not tool_messages:
            return sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_stub_1",
                                            "type": "function",
                                            "function": {"name": target, "arguments": "{}"},
                                        },
                                    ],
                                },
                                "finish_reason": None,
                            },
                        ],
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ],
            )

        tool_text = str(tool_messages[-1].get("content", ""))[:300] if tool_messages else ""
        return sse(
            [
                {
                    "choices": [
                        {"delta": {"content": f"STUB-FINAL: {tool_text}"}, "finish_reason": None},
                    ],
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ],
        )

    return stub


async def start_stub() -> tuple[Any, asyncio.Task[None]]:
    """Run the stub LLM in this event loop and wait until it accepts connections."""
    import uvicorn

    config = uvicorn.Config(build_stub_app(), host=HOST, port=STUB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline and not task.done():
        await asyncio.sleep(0.1)
    if not server.started:
        raise RuntimeError("stub LLM server did not start")
    return server, task


async def seed_scratch_user(db_path: Path, username: str, password: str) -> None:
    """Create the scratch database and its only user; never touches the repo's app.db."""
    os.environ["DB_PATH"] = str(db_path)
    from shared.auth import hash_password
    from shared.config import settings
    from shared.database import async_session_factory, engine, init_db
    from shared.models import User

    if Path(settings.DB_PATH).resolve() != db_path.resolve():
        raise RuntimeError("scratch DB_PATH was not applied")
    if db_path.resolve() == (REPO_ROOT / "app.db").resolve():
        raise RuntimeError("refusing to use the repository app.db")

    await init_db()
    async with async_session_factory() as session:
        session.add(User(username=username, password_hash=hash_password(password)))
        await session.commit()
    await engine.dispose()


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


async def drive_browser(username: str, password: str, scratch: Path) -> None:
    """Log in, connect the real filesystem server, run one chat turn and assert the card."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        server_id: int | None = None
        context = await browser.new_context()
        try:
            page = await context.new_page()
            browser_errors: list[str] = []
            page.on("console", lambda msg: browser_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
            ws_frames: list[str] = []
            page.on("websocket", lambda ws: (
                ws_frames.append(f"OPEN {ws.url}"),
                ws.on("framereceived", lambda payload: ws_frames.append(f"RECV {str(payload)[:140]}")),
                ws.on("framesent", lambda payload: ws_frames.append(f"SENT {str(payload)[:140]}")),
                ws.on("close", lambda _ws: ws_frames.append(f"CLOSE {ws.url}")),
            ))
            await page.goto(f"{UI_URL}/static/login.html")
            await page.fill("#login-username", username)
            await page.fill("#login-password", password)
            await page.click("#login-form button[type=submit]")
            await page.wait_for_url("**/static/index.html", timeout=15000)
            report("logged in through the login page", True)

            headers = {"Origin": UI_URL}
            created = await context.request.post(
                f"{AGENT_URL}/api/v1/mcp/servers",
                data={"name": "Filesystem", "command": FILESYSTEM_EXE, "args": [ALLOWED_DIR]},
                headers=headers,
            )
            server_id = (await created.json()).get("id")
            connected = await context.request.post(
                f"{AGENT_URL}/api/v1/mcp/servers/{server_id}/connect", headers=headers,
            )
            connection = (await connected.json())["connection"]
            tool_names = {tool["name"] for tool in connection.get("tools", [])}
            report(
                "MCP filesystem server connected with list_allowed_directories",
                connection["status"] == "connected" and "list_allowed_directories" in tool_names,
                connection["status"],
            )

            await page.reload()
            await page.click("#btn-new-chat")
            await page.wait_for_function(
                "document.querySelector('#model-select') && document.querySelector('#model-select').value",
                timeout=15000,
            )
            # Sending before the UI has switched to the new chat sends over the old chat's socket
            # and the switch then closes it mid-turn; wait until the socket belongs to the new chat.
            await page.wait_for_function(
                "() => state.chats.length >= 2 && state.currentChatId === state.chats[0].id"
                " && state.ws && state.ws.readyState === WebSocket.OPEN"
                " && state.ws.url.endsWith('/ws/chat/' + state.currentChatId)",
                timeout=15000,
            )
            await page.fill("#message-input", "используй инструмент list_allowed_directories")
            await page.click("#btn-send")

            card = page.locator("details.tool-call-card", has_text="list_allowed_directories")
            await card.first.wait_for(timeout=60000)
            report("tool-call card appeared for list_allowed_directories", True)

            await page.locator(
                "#messages [data-message-id] .message-content", has_text="STUB-FINAL",
            ).first.wait_for(timeout=60000)
            still_there = await card.count() >= 1
            collapsed = still_there and not await card.first.evaluate("el => el.open")
            report("card survives the done re-render", still_there)
            report("card is collapsed by default", collapsed)

            await card.first.locator("summary").click()
            card_text = await card.first.inner_text()
            report(
                "card result contains AiAdventAgentV2",
                "aiadventagentv2" in card_text.lower(),
            )
            await page.screenshot(path=str(scratch / "e2e.png"))
        except Exception:
            # Keep evidence of what the page actually showed, then let the failure propagate.
            await page.screenshot(path=str(scratch / "e2e-failure.png"))
            shown = await page.locator("#messages").inner_text()
            page_state = await page.evaluate(
                "() => ({chat: state.currentChatId, chats: state.chats.map(c => c.id),"
                " messages: state.messages.length, streaming: state.isStreaming})"
            )
            sep = chr(10)
            report_text = sep.join(
                [
                    "messages text:",
                    shown,
                    "",
                    "browser errors:",
                    *browser_errors,
                    "",
                    f"page state: {page_state}",
                    "",
                    "websocket frames:",
                    *ws_frames,
                ],
            )
            (scratch / "e2e-failure.txt").write_text(report_text, encoding="utf-8")
            raise
        finally:
            if server_id is not None:
                try:
                    await context.request.post(
                        f"{AGENT_URL}/api/v1/mcp/servers/{server_id}/disconnect",
                        headers={"Origin": UI_URL},
                    )
                except Exception as exc:
                    print(f"note: disconnect request failed: {type(exc).__name__}")
            await browser.close()


def assert_stub_requests() -> None:
    """Check what the stub LLM saw: the namespaced tool offered, and its result fed back."""
    streamed = [body for body in recorded_requests if body.get("stream") is True]
    if len(streamed) < 2:
        report("stub saw the tool request and the follow-up", False, f"{len(streamed)} streamed")
        return
    first_tools = {t["function"]["name"] for t in streamed[0].get("tools") or []}
    expected = f"mcp__filesystem{TOOL_SUFFIX}"
    report(
        "first LLM request offered the namespaced MCP tool plus built-ins",
        expected in first_tools and set(BUILTIN_TOOLS) <= first_tools,
    )
    tool_messages = [m for m in streamed[1]["messages"] if m.get("role") == "tool"]
    report(
        "follow-up request carried a role=tool result with AiAdventAgentV2",
        any("aiadventagentv2" in str(m.get("content", "")).lower() for m in tool_messages),
    )


def sweep_descendants(snapshot: list[psutil.Process], protected: set[int]) -> None:
    """Kill leftover processes from the run's own tree, leaves first, never by name."""
    known: dict[int, psutil.Process] = {p.pid: p for p in snapshot if p.pid not in protected}
    deadline = time.monotonic() + SWEEP_MAX_SECONDS
    for _ in range(SWEEP_MAX_ITERATIONS):
        alive = [p for p in known.values() if p.is_running()]
        if not alive or time.monotonic() > deadline:
            break
        for proc in alive:
            try:
                for child in proc.children(recursive=True):
                    if child.pid not in protected:
                        known.setdefault(child.pid, child)
            except psutil.NoSuchProcess:
                continue

        def depth(proc: psutil.Process) -> int:
            level, current, seen = 0, proc, set()
            while current.pid not in seen:
                seen.add(current.pid)
                try:
                    parent = current.parent()
                except psutil.NoSuchProcess:
                    break
                if parent is None or parent.pid not in known:
                    break
                level, current = level + 1, parent
            return level

        for proc in sorted(
            (p for p in known.values() if p.is_running()), key=depth, reverse=True,
        ):
            try:
                proc.kill()
                proc.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                continue


async def teardown(
    app_proc: asyncio.subprocess.Process | None,
    stub: tuple[Any, asyncio.Task[None]] | None,
    protected: set[int],
) -> None:
    """Stop the supervisor first (it respawns the Agent), then sweep its process tree."""
    snapshot: list[psutil.Process] = []
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
        sweep_descendants(snapshot, protected)

    if stub is not None:
        server, task = stub
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    await asyncio.sleep(0.5)
    report(
        "ports 8000/8001 are free again",
        port_is_free(UI_PORT) and port_is_free(AGENT_PORT),
    )
    report(
        "pre-existing filesystem.exe processes are still alive",
        all(psutil.pid_exists(pid) for pid in protected),
        f"protected={sorted(protected)}",
    )


async def run_e2e(protected: set[int]) -> None:
    """Start the isolated app, run the browser scenario and always tear everything down."""
    scratch = Path(tempfile.mkdtemp(prefix="mcp_e2e_"))
    db_path = scratch / "e2e.db"
    username, password = "e2e", secrets.token_urlsafe(12)
    app_proc: asyncio.subprocess.Process | None = None
    stub: tuple[Any, asyncio.Task[None]] | None = None
    log_file = None
    try:
        await seed_scratch_user(db_path, username, password)
        report("scratch database is isolated from app.db", True, str(db_path))

        stub = await start_stub()

        # The Agent resolves logs/ relative to its cwd and finds its packages through
        # PYTHONPATH, so running from the scratch directory keeps logs out of the repo.
        env = {
            **os.environ,
            "DB_PATH": str(db_path),
            "LM_STUDIO_BASE_URL": f"http://{HOST}:{STUB_PORT}",
            "DEEPSEEK_API_KEY": "",
            "UI_PORT": str(UI_PORT),
            "AGENT_PORT": str(AGENT_PORT),
            "PYTHONPATH": os.pathsep.join(
                [str(REPO_ROOT), *filter(None, [os.environ.get("PYTHONPATH")])],
            ),
        }
        log_file = open(scratch / "app.log", "wb")
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(REPO_ROOT / "run.py"),
            cwd=str(scratch),
            env=env,
            stdout=log_file,
            stderr=log_file,
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            ready = await wait_for_app(client)
        report("isolated app instance is up (UI + Agent)", ready)
        if ready:
            await asyncio.wait_for(drive_browser(username, password, scratch), PLAYWRIGHT_TIMEOUT)
            assert_stub_requests()
    except Exception as exc:
        report("run completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await teardown(app_proc, stub, protected)
        if log_file is not None:
            log_file.close()
        failed = any(not passed for _label, passed, _detail in checks)
        if failed:
            print(f"scratch kept for inspection: {scratch}")
        else:
            for attempt in range(5):
                try:
                    shutil.rmtree(scratch)
                    break
                except OSError:
                    if attempt == 4:
                        print(f"could not remove scratch dir: {scratch}")
                    time.sleep(0.5)


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

    protected = filesystem_pids()
    print(f"protected filesystem.exe PIDs: {sorted(protected)}")
    asyncio.run(run_e2e(protected))
    sys.exit(EXIT_FAIL if any(not passed for _l, passed, _d in checks) else EXIT_PASS)


if __name__ == "__main__":
    main()
