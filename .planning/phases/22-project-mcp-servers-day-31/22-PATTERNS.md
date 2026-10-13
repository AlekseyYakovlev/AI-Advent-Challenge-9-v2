# Phase 22: Project MCP servers (Day 31) - Pattern Map

**Mapped:** 2026-10-13
**Files analyzed:** 8 new + 3 modified docs (11)
**Analogs found:** 10 / 11 (the git-runner part of the server has no analog; see "No Analog Found")

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `mcp_servers/__init__.py` | config (package marker) | n/a | `agent/__init__.py`, `shared/__init__.py` (empty markers) | exact |
| `mcp_servers/project_git.py` | service (stdio MCP server) | request-response (stdio JSON-RPC) + subprocess I/O | `tests/fixtures/mcp_stdio_server.py::_run_fastmcp_server` | role-match |
| `tests/test_mcp_project_git.py` | test | request-response (spawn server, call tools) | `tests/test_mcp_cli.py` + `tests/test_mcp_real_filesystem.py` | role-match |
| `tests/test_mcp_real_github.py` | test (real binary, skipif) | request-response | `tests/test_mcp_real_filesystem.py` | exact |
| `scripts/e2e_mcp_git_playwright.py` | script (browser E2E, isolated copy) | event-driven (browser + WebSocket) | `scripts/e2e_kb_playwright.py` (skeleton) + `scripts/e2e_mcp_chat_playwright.py` (card assertions) | exact |
| `docs/USER_GUIDE.md` (new section "MCP серверы") | docs | n/a | existing sections "База знаний", "Память задачи" in the same file | role-match |
| `docs/ARCHITECTURE.md` (subsection "Bundled MCP servers") | docs | n/a | "Chat tool calls (built-in + MCP)" section, lines 173-235 | exact |
| `docs/API_SPEC.md` (example payload) | docs | n/a | existing MCP examples (~line 118, ~1089-1104) | exact |
| `docs/TESTING_GUIDE.md` (optional subsection) | docs | n/a | existing per-test-file subsections | role-match |
| Phase verification doc (tool list record) | docs | n/a | output of `scripts/mcp_list_tools.py` | exact |
| `scripts/mcp_list_tools.py` | script | n/a | **unchanged**, used as-is | n/a |

Not modified: `agent/`, `ui/`, `shared/` (no change expected; `agent/tool_guard.py::MULTI_STEP_TOOL_HINT` is capped at 170 words by `tests/test_tool_guard.py:413`, edit only if needed).

---

## Pattern Assignments

### `mcp_servers/project_git.py` (service, stdio request-response)

**Analog:** `tests/fixtures/mcp_stdio_server.py` (FastMCP server over stdio; pinned `mcp==1.30.0`)

**FastMCP construction and run** (`tests/fixtures/mcp_stdio_server.py` lines 27-47, 69):
```python
def _run_fastmcp_server(extra: bool = False) -> None:
    """Serve echo and add over stdio using the MCP SDK's FastMCP, plus more when extra is set."""
    from mcp.server.fastmcp import FastMCP

    sys.stderr.write("fixture-server starting\n")
    sys.stderr.flush()

    server = FastMCP("fixture-server")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the given text back."""
        return text
    ...
        @server.tool()
        async def slow(seconds: float) -> str:
            """Sleep for the given seconds without blocking the server loop."""
            await asyncio.sleep(seconds)
            return "slow-done"
    ...
    server.run(transport="stdio")
```
Deltas required by RESEARCH (do not copy blindly): `FastMCP("local-git", log_level="WARNING")`, `@server.tool(structured_output=False)`, all tools `async`, errors by `raise` of a `ValueError` subclass (FastMCP converts to `isError=True`, text `Error executing tool <name>: <msg>`), tool names `git_current_branch`, `git_status`, `git_diff`, `git_tracked_files`, `git_recent_commits`, each description starts with "LOCAL working copy, not the GitHub remote".

**Fail-fast startup pattern** (fixture `_mode_stderr_exit`, lines 89-94; failing server writes reason to stderr then exits non-zero, which the app turns into `PROCESS_EXITED` plus the stderr tail):
```python
def _mode_stderr_exit() -> None:
    for index in range(1, 26):
        sys.stderr.write(f"stderr line {index}\n")
    sys.stderr.flush()
    sys.exit(1)
```
Apply to D-03/D-04: `sys.stderr.write(reason + "\n"); sys.stderr.flush(); sys.exit(1)` before `server.run()`; the reason must be the last stderr line (app shows only the last 20 lines, `agent/mcp_client.py::read_stderr_tail`, line 125).

**Module/CLI conventions** (fixture lines 1-12, 165-187): single-line module docstring plus usage block, `main()` dispatcher with `if __name__ == "__main__": main()`, one-line docstrings on every function, type hints everywhere, UPPER_CASE module constants with annotations.

**Deliberate deviations from project conventions (document in the module docstring):**
- Do NOT import `shared.logger`/`shared.config`: `shared/logger.py` (`setup_logging`) writes JSON to stdout, which would corrupt the JSON-RPC stream, and `sys.path[0]` is `mcp_servers/` when run as a script so `shared` is not importable anyway. Log to stderr with `sys.stderr.write` or stdlib `logging` on stderr; no `print()` (stdout is the protocol). This is a documented exception to "structlog, never print".
- Imports: stdlib -> third-party (`mcp`, `psutil`) -> none local.

**Process runner / tree-kill / path gate / ref gate / truncation:** no codebase analog; copy the verified snippets from `22-RESEARCH.md` Patterns 2-6 (`_kill_tree` with psutil, `resolve_inside_root`, `rev-parse --verify --end-of-options "<ref>^{commit}"`, budget `len(result) <= max_chars` including summary and marker). Closest in-repo flavour for the asyncio subprocess + psutil style: `scripts/e2e_kb_playwright.py::teardown` (lines 661-685):
```python
snapshot = psutil.Process(app_proc.pid).children(recursive=True)   # collect children BEFORE killing parent
...
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
```
Note `except TimeoutError` (builtin, Python 3.11+) is the project style, also used in `agent/mcp_client.py::open_session` line 267.

**Error-handling pattern (app side, unchanged, for reference):** `agent/mcp_tools.py::call_mcp_tool` flattens text content, caps at `MCP_TOOL_RESULT_MAX_CHARS` (20000, marker `…[truncated N chars]`), and returns `ok = not result.isError`. Server default `--max-chars=18000` stays below that.

---

### `mcp_servers/__init__.py` (package marker)

Empty file, so tests can `from mcp_servers import project_git` (pytest.ini has `pythonpath = .`).

---

### `tests/test_mcp_project_git.py` (test, stdio request-response)

**Analogs:** `tests/test_mcp_cli.py` (CLI + fixture server path handling), `tests/test_mcp_real_filesystem.py` (end-to-end dispatch through the app path), `agent/mcp_client.py::open_session` / `close_handle`.

**Constants + loading script pattern** (`tests/test_mcp_cli.py` lines 1-25):
```python
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPT: Path = REPO_ROOT / "scripts" / "mcp_list_tools.py"
FIXTURE: str = str(REPO_ROOT / "tests" / "fixtures" / "mcp_stdio_server.py")

def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_list_tools", SCRIPT)
    ...
cli: ModuleType = _load_script()
```
Use the same shape: `SERVER: str = str(REPO_ROOT / "mcp_servers" / "project_git.py")`, and `cli.run([sys.executable, SERVER, f"--root={repo}"])` for the `list_tools` scenario (assert `code == 0`, `"Server: local-git"`, `"Tools (5):"` via `capsys`).

**Startup-failure assertion pattern** (`tests/test_mcp_cli.py` lines 55-60):
```python
code = await cli.run([sys.executable, FIXTURE, "stderr_exit"])
captured = capsys.readouterr()
```
Reuse for root missing / not a repo (message must contain `git init`) / git missing (empty `PATH` via env) / bad `--max-chars`: expect `code == 1`, `PROCESS_EXITED` in `captured.err`, reason in the stderr tail, `captured.out == ""`.

**Session usage (app path):** `agent/mcp_client.py` line 234 `open_session(command, args, env, cwd, timeout_seconds=None) -> (SessionHandle | None, McpConnectResult)`; call tools via `handle.session.call_tool(name, args)`; always `await close_handle(handle)` in `finally`. Pytest style: `asyncio_mode = auto`, so bare `async def test_...`, no decorator.

**Real-chat-dispatch scenario** (copy from `tests/test_mcp_real_filesystem.py` lines 42-90): `_create_user` from `tests.conftest`, `mcp_config.create_server(session, user_id, "local-git", sys.executable, [SERVER, f"--root={repo}"], {}, None, True)`, `mcp_client.connect_server(user_id, server_id, command, args, None, None)`, `build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))`, assert `"mcp__local-git__git_current_branch" in toolset.bindings`, `dispatch_tool_calls(session, user_id, chat_id, [call], mcp_bindings=toolset.bindings)`, `finally: await mcp_client.disconnect_server(user_id, server_id)`:
```python
call = {
    "id": "call_real_1",
    "type": "function",
    "function": {"name": EXPOSED_NAME, "arguments": ""},
}
results = await dispatch_tool_calls(session, user_id, chat_id, [call], mcp_bindings=toolset.bindings)
(result,) = results
assert result["ok"] is True, result["content"]
```
Result content is JSON: `json.loads(result["content"])["content"]`.

**Fixture:** `git_repo(tmp_path)` defined in the test file (not `conftest.py`), built with plain `subprocess.run`, env stripped of `GIT_*`, `-c user.name=T -c user.email=t@t -c core.autocrlf=false -c init.defaultBranch=main`; includes `папка/файл.txt`, a file with a space, one modified, one staged, one untracked, branch `feature`. Full scenario table: `22-RESEARCH.md` "Test Plan" (lines 485-506).

**Process-cleanup assertion pattern** (for the timeout/grandchild test; `tests/test_mcp_real_filesystem.py` lines 83-89):
```python
deadline = asyncio.get_running_loop().time() + CLEANUP_WAIT_SECONDS
leftover = _filesystem_pids() - protected
while leftover and asyncio.get_running_loop().time() < deadline:
    await asyncio.sleep(0.2)
    leftover = _filesystem_pids() - protected
assert not leftover, f"... still alive: {sorted(leftover)}"
```
Use psutil pid checks on the sleeper + grandchild instead of image names.

---

### `tests/test_mcp_real_github.py` (test, real binary gated by skipif)

**Analog:** `tests/test_mcp_real_filesystem.py` (exact role and data flow)

**Gate + constants** (lines 19-39):
```python
FILESYSTEM_EXE: str = os.environ.get(
    "MCP_FILESYSTEM_EXE", r"C:\Users\Aleksey\go\bin\filesystem.exe",
)
...
pytestmark = pytest.mark.skipif(
    not Path(FILESYSTEM_EXE).exists(),
    reason="filesystem.exe not installed",
)

def _filesystem_pids() -> set[int]:
    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name") or ""
        if name.lower() == "filesystem.exe":
            pids.add(proc.pid)
    return pids
```
Adapt: `GITHUB_MCP_EXE = os.environ.get("GITHUB_MCP_SERVER_EXE", r"C:\Users\Aleksey\go\bin\github-mcp-server.exe")`, token read from `os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]`, `skipif(not exe exists or not token)` with a reason that contains no secret; process-name matcher `github-mcp-server.exe`. Args `["stdio", "--read-only", "--toolsets=repos,pull_requests"]`, env `{"GITHUB_PERSONAL_ACCESS_TOKEN": token}`.

**Body:** same as `test_list_allowed_directories_with_empty_arguments_via_chat_dispatch` (lines 42-89): `create_server` -> `connect_server` -> `assert connection.status == McpConnectionStatus.CONNECTED` -> tool-name set checks (contains `list_branches`, `pull_request_read`, `list_pull_requests`; none of `create_pull_request`, `merge_pull_request`, `create_or_update_file`) -> dispatch `mcp__github__list_branches` with `{"owner": "AlekseyYakovlev", "repo": "AI-Advent-Challenge-9-v2"}` and assert `result["ok"] is True` (handshake succeeds with a bad token, so a real read call is mandatory) -> `disconnect_server` -> pid cleanup loop. Third test: API round trip using `authenticated_client` (see `tests/conftest.py`) asserting `env_keys` is returned and the token substring is absent from the response. Never print the token or put it in an assert message (D-18).

---

### `scripts/e2e_mcp_git_playwright.py` (script, browser E2E on isolated copy)

**Analog:** `scripts/e2e_kb_playwright.py` (skeleton) and `scripts/e2e_mcp_chat_playwright.py` (tool-call card assertions). Do NOT use `e2e_mcp_chat_playwright.py` as skeleton: it uses `UI_PORT = 8000` and a stub LLM (RESEARCH Pitfall 10).

**Reuse verbatim from `e2e_kb_playwright.py`:**
- Docstring with exit codes (lines 1-13) and `EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4` (line 61).
- Ports and URLs (lines 35-40): `UI_PORT = 18000`, `AGENT_PORT = 18001`, `LM_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")`.
- `report()` (69-74), `port_is_free()` (77-90), `wait_for_app()` (174-186), `seed_scratch_users()` (146-171, scratch DB, refuses `app.db`), `login()` (342), `teardown()` (661-685), `run_e2e()` (688-743), `main()` (746-762).
- `prepare_copy()` (115-143): copies tree, patches `const AGENT_PORT = 8001;` in `ui/static/app.js` and `ui/static/login.html`, then scans for leftover literal 8000/8001. `COPY_IGNORE` (56-59) excludes `scripts`, `tests`, `.git`, `.env`, `*.db`; `mcp_servers/` is NOT excluded so it is copied. Point the form's script arg at the copy's absolute `mcp_servers/project_git.py` (or the repo's, which is read-only to this run).
- Preflight (93-112): adapt to return `EXIT_BLOCKED` when ports are busy or `GET {LM_URL}/api/v0/models` fails (`httpx.get(..., timeout=5.0)`); remove the PDF/embedding checks. LM Studio down -> "not run", never "passed" (D-20).
- App spawn (714-717):
```python
app_proc = await asyncio.create_subprocess_exec(
    sys.executable, str(copy_dir / "run.py"),
    cwd=str(copy_dir), env=env, stdout=log_file, stderr=log_file,
)
```
  with `env` carrying `DB_PATH`, `UI_PORT`, `AGENT_PORT`, `LM_STUDIO_BASE_URL`, `PYTHONIOENCODING`, `PYTHONPATH` (lines 701-711).

**Reuse from `e2e_mcp_chat_playwright.py` (lines 255-333):** login via `#login-username`, `#login-password`, `#login-form button[type=submit]`, `wait_for_url("**/static/index.html")`; new chat `#btn-new-chat`; wait until `state.ws.url.endsWith('/ws/chat/' + state.currentChatId)` before sending (lines 304-311, avoids a mid-turn socket close); `#message-input` + `#btn-send`; tool card locator:
```python
card = page.locator("details.tool-call-card", has_text="git_current_branch")
await card.first.wait_for(timeout=60000)
```
and answer locator `#messages [data-message-id] .message-content`.

**New for this phase:** create a temp git repo (branches `alpha` checked out, `beta`) in the scratch dir, never switch branches in `C:\Projects\AiAdventAgentV2` (Pitfall 9); add the server through form ids `#mcp-name`, `#mcp-command`, `#mcp-args` (one arg per line), `#mcp-cwd`, `#btn-mcp-save` (ids verified in `ui/static/index.html` per RESEARCH); ask "на какой ветке проект?", assert card `mcp__local-git__git_current_branch` and `alpha` in the answer; run `git checkout beta` via `subprocess` in the temp repo; ask again in the same chat; assert a new card and `beta`. Allow one retry for the 9B model; keep the first failing transcript.

---

### `docs/USER_GUIDE.md` (new section "MCP серверы")

**Analog:** neighbouring sections of the same file ("База знаний" line 141, "Память задачи" line 215; headings mix English and Russian). The file currently has no MCP content. Insert before "## Tips" (line 383); add 2-3 rows to its Troubleshooting. Content and the exact form-value tables are given in `22-RESEARCH.md` "Form values to document" (lines 348-376): `local-git` (command `python`, args one per line: absolute script path, `--root=<repo>`, optional `--max-chars=18000`, `--timeout=10`, cwd = repo) and `github` (command `C:\Users\Aleksey\go\bin\github-mcp-server.exe`, args `stdio`, `--read-only`, `--toolsets=repos,pull_requests`, env `GITHUB_PERSONAL_ACCESS_TOKEN=<token>`), download/checksum steps for v2.0.2, fine-grained PAT permissions (D-17), masking note (`GITHUB_PERSONAL_ACCESS_TOKEN=•••`). Never include a real token.

### `docs/ARCHITECTURE.md` (subsection "Bundled MCP servers")

**Analog:** section "Chat tool calls (built-in + MCP)" (lines 173-235): bold-labelled paragraphs plus a bullet list (Naming, Limits, Failures...) and a "Known risks" paragraph. Add the subsection after "Child processes on Agent restart" / before "## Scheduler": own process, stdio, five read-only tools, root confinement, 18000 chars / 10 s defaults vs app 20000 / 30 s, why it avoids `shared.*`; external `github-mcp-server` read-only by flag only. Pre-existing inaccuracy to be aware of: "Single tool round" bullet (line ~196) conflicts with `tool_guard.MAX_TOOL_ROUNDS = 15` per RESEARCH; confirm before editing, not part of this phase.

### `docs/API_SPEC.md`

**Analog:** existing MCP content: the `tool_call` frame example (~line 118, `mcp__filesystem__list_allowed_directories`) and the env settings table (~1089-1104). No new routes. Add a short `POST /api/v1/mcp/servers` example for `local-git` and a masked github env example (response carries `env_keys` only). Keep the env table unchanged.

### `docs/TESTING_GUIDE.md`

No MCP section exists. Add a short subsection for `test_mcp_project_git.py` and `test_mcp_real_github.py` listing the scenarios from RESEARCH Test Plan and the skip conditions (exe missing / token env var missing).

---

## Shared Patterns

### stdout is the protocol (applies to `mcp_servers/project_git.py` only)
**Source:** `tests/fixtures/mcp_stdio_server.py` lines 31-32 (diagnostics via `sys.stderr.write` + `flush`).
Never `print()`, never import `shared.logger`. Guard with a test that greps the module source for `print(` and `shared`, and a test that no stdout appears before the first request.

### Async-only subprocess, no shell
**Source:** `scripts/e2e_kb_playwright.py` lines 714-717 and `agent/mcp_client.py` (`asyncio.create_subprocess_exec` style). Project constraint: no `multiprocessing`, no `os.fork`. Server uses `asyncio.create_subprocess_exec(*argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, cwd=ROOT, env=cleaned_env)`, never `shell=True`.

### Error to model: raise, do not return error strings
**Source:** fixture `fail` tool (lines 48-51) `raise ValueError("fixture failure")` -> `isError`. App-side handling in `agent/mcp_tools.py::call_mcp_tool` and `agent/tool_guard.py::TOOL_ERROR_REMINDER`. English message naming the reason and the fix (D-15).

### Test session lifecycle
**Source:** `tests/test_mcp_real_filesystem.py` lines 57-81: `try: connect ... finally: await mcp_client.disconnect_server(user_id, server_id)` followed by a psutil leftover-pid wait loop.

### Isolated-copy browser runs
**Source:** `scripts/e2e_kb_playwright.py`. Ports 18000/18001 only; stop only processes this script started; preflight returns `EXIT_BLOCKED` (2) and the result is recorded "not run" (memory rule: never kill the user's app on 8000/8001).

### Secrets
**Source:** D-18 plus `agent/mcp_config.py` (`ENV_MASK`, `merge_env`, `EnvMaskError`): token only via the form env field or process environment in tests; never in files, logs, fixtures, planning docs, or assert messages.

### Python code style (all new .py files)
Single-line module docstring; one-line function docstrings; type hints on all params and returns; `str | None` unions; `datetime.now(timezone.utc)` if dates are needed; imports stdlib -> third-party -> local; no bare `except:`.

---

## Conventions

Convention derivation skipped (`gsd-tools.cjs` was not resolvable in this environment: `CLAUDE_PLUGIN_ROOT` empty and no plugin cache directory found). Observed manually from the analogs and `CLAUDE.md`:

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | `lowercase_with_underscores.py` (`test_<module>.py`, `mcp_*.py`) | ~100% (Python files) | low | named contract |
| Identifier casing | functions/vars `snake_case`, constants `UPPER_CASE`, classes `PascalCase`, private `_prefix` | ~100% | low | named contract |
| Export style | plain module-level definitions, no `__all__`, direct imports (`from agent.main import app`) | dominant | low | named contract |
| Import style | absolute from project root, stdlib -> third-party -> local, no path aliases | dominant | low | named contract |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver (`bin/lib/**` is CJS `module.exports`/`require`; `sdk/src/**` is ESM `export`/`import`) is the prototype intentional-contested split: each half is internally consistent per directory, contested only repo-wide; match the directory's local style. Not relevant to this Python phase. Local contested point for this phase: `mcp_servers/` deliberately deviates from the repo's structlog/`shared` convention (stdout must stay clean); match the fixture server's stderr-only style there.

---

## No Analog Found

| File / Part | Role | Data Flow | Reason |
|-------------|------|-----------|--------|
| git process runner in `project_git.py` (`run_git`, tree-kill on timeout, byte cap, env scrubbing of `GIT_*`) | service | subprocess I/O | No existing code runs `git` or a bounded external command; use `22-RESEARCH.md` Pattern 2 (partial style analog: `e2e_kb_playwright.py::teardown`) |
| `resolve_inside_root` path gate and ref gate | utility | validation | No path-confinement code exists in the app; use RESEARCH Patterns 4-5 (verified prototypes) |
| Truncation/budget composer (summary + stat + patch + marker) | utility | transform | App-side truncation (`agent/mcp_tools.py`) is a single suffix cut only; use RESEARCH Pattern 6 |

## Metadata

**Analog search scope:** `tests/`, `tests/fixtures/`, `scripts/`, `agent/mcp_client.py`, `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md`, project `CLAUDE.md`
**Files scanned:** about 14 (read fully: fixture server, real filesystem test, list-tools CLI; partially: kb/mcp e2e scripts, test_mcp_cli, mcp_client, ARCHITECTURE, USER_GUIDE headings)
**Existing MCP tests (keep green):** `tests/test_mcp_{api,auto_connect,chat_ws,cli,client,config,logout,origin,orphan,real_filesystem,registry_robustness,tools}.py`
**Pattern extraction date:** 2026-10-13
