# Phase 27: AI release pipeline (Day 35) - Pattern Map

**Mapped:** 2026-10-17
**Files analyzed:** 19 (new or modified)
**Analogs found:** 18 / 19 (1 partial: the step-state machine has no direct analog)

Note on the ws.py line numbers: Phase 25 is editing `agent/ws.py`; the numbers below are from the current working copy and will shift. Locate by symbol name.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `mcp_servers/project_git.py` (modify: `--allow-release`, `register_release_tools()`, 5 tools) | MCP tool server | request-response (subprocess + HTTP) | same file: `git_status`, `git_recent_commits`, `resolve_ref`, `run_git`, `main()` | exact |
| `agent/release_command.py` (new: grammar, notices, `ReleasePlan`) | command parser + dataclass | request-response | `agent/help_command.py` (`_HELP_PATTERN`, `parse_help`, `is_help_command`) + `agent/command_turn.py::notice_frame` | exact |
| `agent/release_pipeline.py` (new: range, filter, grouping, candidates, prompts, LLM calls, validators) | service | transform + batch | `agent/review_pipeline.py::_complete_once` / `agent/rag_llm.py::_complete`, `complete_stage` | role-match |
| `agent/release_steps.py` (new: MCP call helper emitting tool cards, step executor) | service | event-driven (frames) + sequential writes | `agent/review_github.py` (`resolve_github_server`, `_live_session`, `_read`) + `agent/mcp_tools.py::call_mcp_tool` + `agent/ws.py::_tool_call_frame` | role-match (state machine: no analog) |
| `agent/state.py` (modify: `release_plans`, cleanup) | state | in-memory | same file (`chat_locks`, `title_tasks`, `cleanup_chat_caches`) | exact |
| `agent/ws.py` (modify: thin hook before `MessagePayload.model_validate`, extract `_tool_call_frame`) | controller (WebSocket) | request-response + streaming frames | same file: `ws_chat` `is_help_command` branch, `_tool_call_frame`, `_handle_chat_message` | exact |
| `ui/static/app.js` (modify: `buildReleasePlanCard`, `release_plan`/`release_progress`/`release_result` cases) | component | event-driven | `buildHelpNotice` (L256), `buildToolCallCard`/`appendToolCallCard` (L1477-1523), `handleWsMessage` `help_notice` case | exact |
| `tests/test_mcp_release_tools.py` (new: tools against temp repo + bare remote + respx) | test | request-response | `tests/test_mcp_project_git_core.py` (`git_repo`, `configured` fixtures), `tests/test_mcp_project_git.py` | exact |
| `tests/test_release_command.py`, `tests/test_release_pipeline.py` (new: pure logic) | test | transform | `tests/test_help_command.py`, `tests/test_command_turn.py` | role-match |
| `tests/test_release_ws.py` (new: WS plan/confirm flow) | test | WebSocket request-response | `tests/test_help_ws.py` | exact |
| `tests/test_cascade_delete.py` (modify: assert `release_plans` cleared) | test | in-memory cleanup | same file `test_delete_chat_cleans_in_memory_caches` | exact |
| `tests/test_release_ui.py` (new, optional) | test | static checks | `tests/test_help_ui.py` | role-match |
| `scripts/e2e_release_playwright.py` (new) | script | browser e2e on isolated copy | `scripts/e2e_mcp_git_playwright.py` (reuses `e2e_kb_playwright as kb`) | exact |
| `scripts/release_control.py` (new, optional control-run helper) | script | batch | `scripts/review_control.py` (exit codes, `NOT_RUN`, `github_call`) | role-match |
| `Day35_report.md` (new) | doc | n/a | `Day32_report.md` (+ `Day31_report.md`) | exact |
| `CHANGELOG.md` (created by the pipeline, not by hand) | doc | file-I/O | none (does not exist) | no analog |
| `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `README.md` (modify) | doc | n/a | existing sections of the same files (`/help` and `local-git` sections) | exact |
| Step status state machine (`StepState`, retry-skips-done) | model | state transition | none | no analog |

## Pattern Assignments

### `mcp_servers/project_git.py` (MCP tool server, request-response)

**Analog:** the same file. It is self-contained on purpose (no `shared.logger`, stdout is the protocol channel, diagnostics via `sys.stderr.write`, no `print`).

**Imports pattern** (lines 12-24): stdlib then `psutil`, `mcp.server.fastmcp`. Add `httpx` here (third-party) for the Release tool; do NOT import `shared.*` or `agent.*`.
```python
import argparse
import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import NoReturn

import psutil
from mcp.server.fastmcp import FastMCP
```

**Constants/regex pattern** (lines 26-35): module constants `UPPER_CASE`, compiled `_UPPER` regex. Add `_VERSION_TAG = re.compile(r"v(\d+)\.(\d+)")`, `RELEASE_PUSH_TIMEOUT`, `PAGE_SIZE = 100`.

**Config + flag pattern** (lines 52-105): frozen `ServerConfig` dataclass, `configure(...)` global, `parse_args` with `allow_abbrev=False` and `parser.error(...)`. Extend `ServerConfig` with `allow_release: bool` and add:
```python
parser.add_argument("--allow-release", action="store_true", help="expose release write tools")
```
Keep `configure(root, git, max_chars, timeout)` call sites in `tests/test_mcp_project_git_core.py` working (give the new param a default).

**Error pattern** (lines 38-40, 394-398): domain errors are `ToolInputError`, mapped to `isError`; git failures use `_git_error`.
```python
class ToolInputError(ValueError):
    """Raised when a tool argument is rejected; the message names the reason and the fix."""

def _git_error(result: GitResult, name: str) -> ToolInputError:
    return ToolInputError(
        f"git {name} failed: {_last_line(result.stderr, f'exit code {result.returncode}')}"
    )
```

**Fixed-argv runner** (lines 182-234): no shell, bounded time and output, kills the process tree on timeout. `run_git` uses `cfg.timeout`; for `release_push` call `run_command(base_argv(cfg.git) + argv, cwd=cfg.root, env=git_env(), timeout=RELEASE_PUSH_TIMEOUT, label="git push")` directly (global `--timeout` default is 10 s, too short for push).

**Ref gate** (lines 267-306): always run user refs through `check_ref_syntax` / `resolve_ref` (uses `--end-of-options`). New tools take `commit`, `branch`, `tag` -> validate with these plus `_VERSION_TAG.fullmatch`.

**Read tool shape** (lines 574-609, `git_recent_commits`): `@server.tool(structured_output=False)`, docstring starting `LOCAL working copy (...)`, summary line via `_plural`, `fit_text(summary, body, max_chars, lower_bound=result.capped)`. Model `release_range_commits(range, page)` on it, but emit machine-parseable JSON lines (100 per page + explicit `next_page`) so the agent can parse the full text (tool results are cut at 20 000 chars).
```python
argv = [
    "log", "-n", str(count), "--no-color", "--no-show-signature", "--date=short",
    "--format=%h%x09%ad%x09%an%x09%s",
]  # fmt: skip
if resolved:
    argv.append(resolved)
result = await run_git(argv)
if result.returncode != 0:
    raise _git_error(result, "log")
```

**Status tool (reuse for dirty-tree check, D-21 strict)** (lines 441-465): `git status --porcelain=v1 --branch --untracked-files=normal`; the agent counts lines not starting with `##` as dirty (includes `??` untracked) and lists the paths in the block message.

**Conditional registration (new)**: `@server.tool` at import time would expose write tools without the flag. Define the release tools inside `register_release_tools()` and call it from `main()` only when `args.allow_release`, after `configure(...)` and before `server.run` (lines 639-656):
```python
configure(root, git, args.max_chars, args.timeout)
if args.allow_release:
    register_release_tools()
server.run(transport="stdio")
```
Existing tests assert exactly five tools (`FIVE_TOOLS` in `tests/test_mcp_project_git.py`); they must stay green without the flag.

**Write-tool core** (follow RESEARCH Code Examples; argv exact):
- `release_commit_changelog`: resolve `CHANGELOG.md` via `resolve_inside_root`, write UTF-8 `\n`, `git add -- CHANGELOG.md`, refuse if anything else is staged (`git diff --cached --name-only`), `git commit -m ... -- CHANGELOG.md`, return `git rev-parse HEAD`. Never `add -A` / `commit -a`.
- `release_create_tag`: `git tag -a <v> -m <msg> <sha>`; refuse when `git tag -l -- <v>` is non-empty.
- `release_push`: `["push", "origin", f"refs/heads/{branch}:refs/heads/{branch}", f"refs/tags/{tag}:refs/tags/{tag}"]`; no `+`, no `--force*`, no `--tags`, no `--delete`.
- `github_create_draft_release`: `httpx.AsyncClient(timeout=30.0)` `POST https://api.github.com/repos/{owner}/{repo}/releases` with `draft: True`, `target_commitish=<sha>`; token from `os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]`, absent -> `ToolInputError`. Header set to copy from `scripts/review_control.py` (`GITHUB_HEADERS`, line 61) and the `Bearer` pattern at line 263. Map 401/403/404/422 to English reasons, never echo the token, return `html_url`.

**Test-isolation note:** `git_env()` strips every `GIT_*` variable and sets `GIT_TERMINAL_PROMPT=0`, so push fails instead of hanging; tests need local `user.name`/`user.email` per repo.

---

### `agent/release_command.py` (command parser + plan dataclass, request-response)

**Analog:** `agent/help_command.py` (lines 1-80)

**Imports pattern** (lines 1-30): stdlib, then third-party, then `from agent ...` absolute imports, `logger = get_logger(__name__)`.

**Grammar pattern** (help_command lines 62-77): compile a regex, `parse_*` returns None for non-matches, `is_*_command(raw: Any)` guards a raw WS dict before Pydantic validation.
```python
_HELP_PATTERN = re.compile(r"^\s*/help(?:\s+(?P<q>.*))?$", re.IGNORECASE | re.DOTALL)

def parse_help(content: str) -> str | None:
    match = _HELP_PATTERN.match(content)
    if match is None:
        return None
    return (match.group("q") or "").strip()

def is_help_command(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    content = raw.get("content")
    return isinstance(content, str) and parse_help(content) is not None
```
Mirror for `/release [range]` and `/release confirm [vX.Y] [title...]`. Return a small frozen dataclass (`ReleaseCommand(kind: "plan"|"confirm", range_spec, version, title)`), not a bare string. Also accept the typed `{"type": "release_confirm", ...}` control frame (RESEARCH Open Q4).

**Notice frame** (`agent/command_turn.py` lines 44, 82-89): reuse the shape for "Нет плана релиза" etc.; the ephemeral frame has `"stored": False`.
```python
def notice_frame(kind: str, text: str, command: str | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": NOTICE_FRAME_TYPE, "kind": kind, "text": text, "stored": False}
    if command is not None:
        frame["command"] = command
    return frame
```
Do NOT route `/release` through `CommandSpec`/`prepare_command_turn` (KB-retrieval specific); only reuse `notice_frame`. Russian user-facing constants live as module-level `UPPER_CASE` strings (see `HELP_USAGE_TEXT`, `HELP_NO_BASE_TEXT`); take the exact copy from 27-UI-SPEC "Copywriting Contract".

---

### `agent/release_pipeline.py` (service, transform + LLM calls)

**Analog:** `agent/rag_llm.py` (lines 1-100) and `agent/review_pipeline.py::_complete_once` (lines 444-466)

**LLM call with reasoning toggle + 400/422 retry** (rag_llm.py lines 43-72) - import is private, so either import `complete_stage` (public) or copy this shape:
```python
async def _complete(client, messages, model, max_tokens) -> ChatCompletionResult:
    try:
        return await client.complete_chat_detailed(
            messages=messages, model=model, temperature=STAGE_TEMPERATURE,
            max_tokens=max_tokens, extra_body={"reasoning_effort": STAGE_REASONING_EFFORT},
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code not in REJECTED_STATUS_CODES:
            raise
        logger.info("rag_stage_reasoning_control_rejected", model=model, status_code=status_code)
    return await client.complete_chat_detailed(..., extra_body=None)
```
**Timeout + failure-to-reason pattern** (`complete_stage`, lines 75-98): `asyncio.wait_for`, re-raise `CancelledError`, map `TimeoutError`/other exceptions to a reason code and log `error=type(exc).__name__` only. Use this directly: `complete_stage("release_changelog", client, messages, model, max_tokens, timeout)`.

**Unusable-reply logging** (rag_llm.py lines 101-111): log `finish_reason`, `content_empty`, `has_reasoning`, never the text. Use for empty `content` from local reasoning models (D-16 uses LM Studio).

**Result type** (rag_llm.py lines 33-39): frozen `StageOutcome(value, reason)`. Use the same for validated changelog / version decision.

Client resolution (from `agent/ws.py::_run_chat_turn`): `client, provider_row = await resolve_client(chat.user_id, payload.provider_id)` catching `ProviderUnavailableError` -> `{"type": "error", "code": "PROVIDER_UNAVAILABLE", "detail": exc.message}`.

Pure functions (filter, grouping, `parse_version`, candidates, `validate_version`) stay free of I/O so `tests/test_release_pipeline.py` needs no fixtures. Version helpers from RESEARCH:
```python
_VERSION_TAG = re.compile(r"v(\d+)\.(\d+)")
def parse_version(tag: str) -> tuple[int, int] | None:
    m = _VERSION_TAG.fullmatch(tag); return (int(m[1]), int(m[2])) if m else None
```

---

### `agent/release_steps.py` (service, code-initiated MCP calls with tool cards)

**Analog:** `agent/review_github.py` (code reads MCP server rows and sessions) + `agent/mcp_tools.py::call_mcp_tool`

**Server resolution** (review_github.py lines 101-119): `mcp_config.list_servers(session, user_id)`, match by name and `enabled`; raise a domain error with a Russian detail when missing. Default name `release-git`, fallback error "Сервер release-git не настроен" (RESEARCH Open Q5); optional read-only `github` entry reuses `DEFAULT_SERVER_NAME = "github"` and the same function.
```python
for candidate in await mcp_config.list_servers(session, user_id):
    if candidate.name == DEFAULT_SERVER_NAME and candidate.enabled:
        row = candidate
        break
```
**Live session** (lines 122-138):
```python
result = await ensure_connected(
    user_id, row.id, row.command, mcp_config.load_args(row),
    mcp_config.load_env(row) or None, row.cwd,
)
if result.status != McpConnectionStatus.CONNECTED:
    raise _unavailable(MSG_SERVER_NOT_CONNECTED)
live = get_live_session(user_id, row.id)
```
**Call with explicit timeout** (lines 160-190): use `asyncio.wait_for(session.call_tool(...), 120.0)` for push (settings.MCP_TOOL_CALL_TIMEOUT is shorter), catching `TimeoutError` and `McpError`. For normal calls build the binding and use `call_mcp_tool`:
```python
# agent/mcp_tools.py lines 40-48, 283-
@dataclass(frozen=True)
class McpToolBinding:
    exposed_name: str; user_id: int; server_id: int; server_name: str; tool_name: str

outcome = await call_mcp_tool(binding, arguments)   # {"ok", "is_error", "text", "truncated"}
```
**Tool-card frame** (`agent/ws.py::_tool_call_frame`, lines 104-121): build the same frame so the unchanged frontend `case 'tool_call'` renders it. It takes a dispatch-result dict (`tool_call_id, name, mcp:{server_name,tool}, arguments, ok, result_text, truncated, content`); extract it into a shared helper (or import it) rather than copy. Previews are cut at `TOOL_EVENT_PREVIEW_CHARS`; parse results in code from the full text, never from the preview.

**Error-to-reason mapping** (review_github.py lines 21-43, 151-157): marker tuples (`TOKEN_REJECTED_MARKERS`, `NOT_FOUND_MARKERS`) mapped to distinct Russian messages; use for 401/403/404 on the Release step -> status `unavailable`.

**Repo slug validation** (lines 41, 91-98): reuse `REPO_PATTERN` and `_validate_repo`-style checks when parsing `owner/repo` from the `origin` URL (SSH host alias: take the path after `:`).

**Step state machine:** no analog. Define `StepState` as a `str, Enum` (`PENDING, RUNNING, DONE, SKIPPED, FAILED, UNAVAILABLE`) following the project enum convention (`class ContextStrategy(str, Enum)`), executed in order commit -> tag -> push -> release under `state.chat_locks[chat_id]`; skip DONE steps on retry; lock version/title once step 1 is DONE.

---

### `agent/state.py` (state, in-memory per chat)

**Analog:** the same file (lines 1-33)
```python
chat_locks: dict[int, asyncio.Lock] = {}
title_tasks: dict[int, asyncio.Task[None]] = {}
...
def cleanup_chat_caches(chat_id: int) -> None:
    active_streams.pop(chat_id, None)
    ws_rate_limiter.pop(chat_id, None)
    chat_locks.pop(chat_id, None)
```
Add `release_plans: dict[int, Any] = {}` (type `Any` or a forward-ref; `state.py` must not import `agent.release_command` to avoid cycles - same trick as `kb_index_cache: dict[int, Any]`) and `release_plans.pop(chat_id, None)` in `cleanup_chat_caches`. CLAUDE.md requires cleanup on chat delete.

---

### `agent/ws.py` (WebSocket controller; thin hook only)

**Analog:** the `is_help_command` branch in `ws_chat` (lines 1466-1490) and `_handle_chat_message` (lines 1387-1397)
```python
if help_command.is_help_command(raw):
    try:
        payload = MessagePayload.model_validate(raw)
    except ValidationError as exc:
        await websocket.send_json({"type": "error", "detail": exc.errors()})
        continue
    async with async_session_factory() as db:
        notice = await help_command.resolve_help_notice(db, user.id, ...)
    if notice is not None:
        await websocket.send_json(notice)
        continue
```
Add a sibling branch before it: `if release_command.is_release_command(raw): ... await release_handler(websocket, chat_id, user.id, cmd); continue`. Keep logic out of `ws.py` (1 500 lines). Use the per-chat lock pattern:
```python
if chat_id not in chat_locks:
    chat_locks[chat_id] = asyncio.Lock()
async with chat_locks[chat_id]:
    ...
```
Wrap `websocket.receive_json()` is already in `asyncio.wait_for` in this loop (CLAUDE.md rule); do not add new bare receives. Check `_reject_rate_limited(websocket, chat_id)` is applied to `/release` like other messages.

---

### `ui/static/app.js` (component, event-driven frames)

**Analogs:** `buildHelpNotice` (L256-266), `buildToolCallCard` / `appendToolCallCard` (L1477-1523), `handleWsMessage` cases `tool_call` / `help_notice` (L1791-1836).

**Construction rule** (UI-SPEC item 9: `createElement` + `textContent` only). The helper `mcpEl(tag, className, text)` is used by `buildHelpNotice`:
```javascript
function buildHelpNotice(data) {
    const box = mcpEl('div', 'help-notice rounded-lg border border-slate-700 bg-slate-900 text-sm text-slate-300 px-3 py-2 max-w-[75%] self-start');
    box.setAttribute('role', 'status');
    box.appendChild(mcpEl('span', RAG_CHIP_NEUTRAL + ' text-xs', 'Справка по проекту'));
    ...
    box.appendChild(mcpEl('div', 'mt-1 text-xs text-slate-500', 'Это сообщение не сохраняется в чате.'));
    return box;
}
```
Copy this shape for `buildReleasePlanCard(plan)`; set `role="region"` and `aria-label="План релиза"` (UI-SPEC), header chip `RAG_CHIP_NEUTRAL + ' text-xs'` (defined L5803).

**Insert-before-streaming-bubble** (L1514-1523): reuse for inserting the plan card so it sits after the read tool cards:
```javascript
const streaming = container.querySelector('[data-streaming="true"]');
if (streaming) { container.insertBefore(wrapper, streaming); } else { container.appendChild(wrapper); }
container.scrollTop = container.scrollHeight;
```
**Frame handling** (L1827-1836, `help_notice` case): each terminal frame clears `state.pendingToolCalls`, `setStreaming(false)`, `removeLoadingBubble()`, `unblockInput()`, `state.lastFailedMessage = null`. A `release_plan` case must do the same; `release_progress`/`release_result` update the existing card in place (no stream reset). Keep a `state.releaseCards` map keyed by plan id to mark older cards "План устарел".

Markdown in the changelog preview is the only HTML path: `DOMPurify.sanitize(marked.parse(text))` (existing markdown rendering call sites, CLAUDE.md rule). Release link `href` must start with `https://github.com/`, with `rel="noopener noreferrer"`.

---

### `tests/test_mcp_release_tools.py` (test, tools against temp repo + bare remote)

**Analog:** `tests/test_mcp_project_git_core.py` (lines 199-222) and `tests/test_mcp_project_git.py` (lines 1-80)
```python
@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "agent").mkdir(parents=True)
    _git(repo, "init")
    ...
    return repo

@pytest.fixture
def configured(git_repo: Path) -> Path:
    project_git.configure(os.path.realpath(git_repo), shutil.which("git") or "git", 18000, 10.0)
    return git_repo
```
`tests/test_mcp_project_git.py` shows the `_git` helper with `-c user.name=T -c user.email=t@t -c core.autocrlf=false -c init.defaultBranch=main`, `_clean_env()` stripping `GIT_*`, `pytestmark = skipif(shutil.which("git") is None)`, and the stdio round trip (`cli`, `open_session`, `connect_once_and_list`). Add a bare repo (`git init --bare`) as `origin` for push/tag tests; mock GitHub REST with `respx` (`respx.mock` + `httpx` as used in `tests/test_help_ws.py`). Add an assertion that without `--allow-release` the tool list is still `FIVE_TOOLS`, and a source-cleanliness check like `test_module_source_is_clean` (line 171 of the core test file) so no `--force` string or `print` appears.

---

### `tests/test_release_ws.py` (test, WS plan/confirm flow)

**Analog:** `tests/test_help_ws.py` (lines 1-60)
```python
from starlette.testclient import TestClient
from agent.main import app
from tests.conftest import login_test_client
from tests.test_memory_ws import _plain_content_response, _queue_responses, _tool_calls_response

BASE_URL = settings.LM_STUDIO_BASE_URL
COMPLETIONS = f"{BASE_URL}/v1/chat/completions"
WS_ORIGIN = "http://localhost:8000"
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
FRAME_END = ("done", "error", "help_notice")

@pytest.fixture(autouse=True)
def _help_setup(monkeypatch):
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)
```
Extend `FRAME_END` with `release_plan`/`release_result`. Mock the LM Studio completion with `respx` (as that file does), point a `release-git` MCP server row at `mcp_servers/project_git.py --root=<tmp repo> --allow-release`. Never touch the project repo or its remote.

**Cascade test** - `tests/test_cascade_delete.py` lines 25-37: add `release_plans[chat_id] = object()` next to `chat_locks[chat_id] = asyncio.Lock()` and assert it is popped after DELETE.

---

### `scripts/e2e_release_playwright.py` (script, isolated-copy browser e2e)

**Analog:** `scripts/e2e_mcp_git_playwright.py` (lines 1-60). Copy the structure wholesale:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_kb_playwright as kb  # noqa: E402

UI_PORT: int = 18000
AGENT_PORT: int = 18001
report = kb.report
checks = kb.checks
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4
```
Same docstring contract (exit codes, ports 18000/18001, scratch DB, only own processes stopped), `pick_chat_model()` preferring `qwen/qwen3.5-9b` (D-16 local only). Extra rules for this phase: the script must assert `origin` URL != the project remote before confirming (RESEARCH Security table) and record un-runnable preconditions (no LM Studio, no scratch token) as "не запускалось"/blocked (exit 2), never pass.

**Control-run helper** (`scripts/review_control.py`): copy the `NOT_RUN = "не запускалось"` constants, exit-code set (0 done, 1 failed, 3 not run, 4 manual), `github_call(...)` with `Bearer` header and token scrub (`scrub(...)`) and the `ALLOWED_GIT` allow-list idea.

---

### `Day35_report.md` (doc)

**Analog:** `Day32_report.md` (Russian; headings `## Задание`, `## Как устроен пайплайн`, `## Безопасность ...`, numbered pipeline steps citing decision IDs like `(D-01)`). Follow the same honest-recording style (D-19) and add an explicit paragraph on the D-20 deviation (no create-release tool in `github-mcp-server` v2.0.2, own `github_create_draft_release` tool behind `--allow-release`). Cover the four sandbox scenarios of D-18, the code check and written quality comment of D-17.

---

## Shared Patterns

### Code calls tools, model never does (D-03)
**Source:** `agent/review_github.py` (code-side MCP reads) + `agent/mcp_tools.py::call_mcp_tool`
**Apply to:** `release_steps.py`, ws hook. Every code-initiated call emits a `tool_call` frame via the extracted `_tool_call_frame`.

### Rejected MCP calls return `isError` with an English reason
**Source:** `mcp_servers/project_git.py` `ToolInputError` / `_git_error` (lines 38-40, 394-398)
**Apply to:** all five new server tools. Reasons are shown verbatim on cards (UI-SPEC).

### Fixed argv, no shell, no force, ref gates
**Source:** `project_git.py` `run_command`, `check_ref_syntax`, `resolve_ref`, `git_env`
**Apply to:** all write tools. Explicit refspecs, `--end-of-options` for refs, `git add -- CHANGELOG.md` only.

### Logging
**Source:** `agent/review_github.py` / `agent/rag_llm.py`: `logger = get_logger(__name__)`, snake_case event keys with `key=value` pairs (`logger.warning("review_github_failed", error_type="TimeoutError")`). Log `type(exc).__name__`/`str(exc)`, never tokens, headers, or model output text. `project_git.py` is the exception: stderr only.

### Secrets
**Source:** CLAUDE.md + `scripts/review_control.py` `scrub(...)`. Token only in the MCP server's env from the «MCP серверы» form (`mcp_config.load_env(row)`); never in results, logs, cards or `Day35_report.md`. The MCP SDK child environment is a whitelist, so the token must be in the form env.

### DB/async conventions (if any DB read is needed, e.g. server rows)
**Source:** CLAUDE.md: `await session.commit()` after writes, `await session.rollback()` in except handlers, `.is_(None)` for NULL filters. The release feature itself needs no new table (plan lives in `state.release_plans`).

### Ephemeral command notices
**Source:** `agent/command_turn.py::notice_frame` (lines 82-89) and `ui/static/app.js` `help_notice` case. `stored: False`; plan card is live-only (UI-SPEC item 8).

### Frontend security
**Source:** `buildHelpNotice` (`createElement`/`textContent`), CLAUDE.md DOMPurify rule. Commit subjects, AI text, titles and error reasons are untrusted.

## Conventions

Derived with `gsd-tools verify conventions --derive --scope agent` (`agent/`, Python, 64 files). Export/import-style axes had no data (Python repo; JS-oriented axes).

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake (44 snake / 19 camel-ish / 1 other) | 68.8% | 0.62 | contested hotspot (the tool counts single-word lowercase names as camel; project rule in CLAUDE.md is `lowercase_with_underscores`, which is the effective contract) |
| Identifier casing (classes) | Pascal | 88.4% | 0.35 | named contract |
| Export style | insufficient data | n/a | n/a | n/a |
| Import style | insufficient data (Python: absolute 100%, explicit 100%) | n/a | 0 | named contract: absolute imports, no wildcard imports |

**Contested hotspots (author's choice):** the CJS<->SDK dual resolver is not present in this repo (Python project); the analogous intentional split here is that `mcp_servers/project_git.py` is a self-contained module (no `shared.*`/`agent.*` imports, stderr instead of `structlog`, no `print`), while everything under `agent/` uses `structlog` via `get_logger(__name__)` and absolute imports. Match the local style of the directory you edit: new `release_*` modules in `agent/` follow `agent/` style; new tools in `mcp_servers/project_git.py` follow that file's self-contained style.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| Step state machine (`StepState`, resumable executor with skip-done retry) in `agent/release_steps.py` | model/service | sequential writes with partial failure | No multi-step resumable operation exists; only the enum convention (`str, Enum`) and the chat-lock pattern carry over. Use RESEARCH Pattern 5. |
| `CHANGELOG.md` | doc | file-I/O | File does not exist; created by the tool with the Keep a Changelog header. Format defined in D-08 and RESEARCH Pattern 8. |
| Plan card in-place update (`release_result` frame updating an earlier card) | component | event-driven | Existing notices/cards are append-only; use a keyed map `state.releaseCards` and mutate the card, per UI-SPEC section 4. |

## Metadata

**Analog search scope:** `mcp_servers/`, `agent/`, `ui/static/app.js`, `tests/`, `scripts/`, root `Day3*_report.md`
**Files scanned/read:** about 18 (project_git.py, ws.py sections, state.py, help_command.py, command_turn.py, review_github.py, mcp_tools.py, rag_llm.py, review_pipeline.py section, app.js sections, 5 test files, 2 scripts, Day32_report.md)
**Pattern extraction date:** 2026-10-17
**Caveat:** `agent/ws.py` is being edited by Phase 25 (`Day33`); schedule the `ws.py` hook after Phase 25 lands or expect a merge conflict.
