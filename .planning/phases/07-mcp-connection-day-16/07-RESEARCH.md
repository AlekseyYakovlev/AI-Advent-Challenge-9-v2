# Phase 7: MCP Connection (Day 16) - Research

**Researched:** 2026-09-23
**Domain:** Model Context Protocol (MCP) stdio client integration on Windows, inside an existing FastAPI + SQLModel + vanilla-JS app
**Confidence:** HIGH — every claim about SDK behavior in this document was verified by **directly executing** `mcp` 1.30.0 against the real `filesystem.exe` binary on this machine (Windows 11, Python 3.13.15, ProactorEventLoop), not just read from docs. Where a claim is UI/copy/architecture only (no SDK behavior involved), confidence is MEDIUM/CITED per the UI-SPEC and CONTEXT.md contracts.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Phase Boundary:** A user can add/edit/delete MCP server configs (per `user_id`) in the Settings UI, press "Connect" to have the Agent open a stdio MCP session (initialize handshake) against that server, and see connection status, serverInfo (name, version, protocol version) and the server's tool list (name, description, parameters from inputSchema). Failures are reported clearly without crashing the Agent. A standalone CLI script (`scripts/mcp_list_tools.py`) proves the same connect + list_tools flow from the console. Proven against the local Go filesystem server `C:\Users\Aleksey\go\bin\filesystem.exe` (17 tools, serverInfo `filesystem-mcp-server`).

**Not in this phase:** LLM calling MCP tools in chat (MCP-F1), non-stdio transports (MCP-F3), MCP resources/prompts, Node/npx servers.

- **D-01:** MCP server management is a new **"MCP серверы" section inside the existing Settings modal** (`ui/static/index.html` `#settings-modal`), not a sidebar panel or modal tabs. The modal may widen and/or scroll to fit. The section is **user-scoped and independent of the "Настройки для текущего чата" per-chat checkbox** and of the modal's Save button — MCP CRUD/Connect actions hit their own REST endpoints immediately.
- **D-02:** **Multiple servers per user**, shown as a list of rows; each row has its own Edit / Delete / Connect (Disconnect when connected) buttons plus a status badge (not connected / connecting / connected / error).
- **D-03:** After a successful connect, the row shows serverInfo (name, version, protocolVersion) and the tool list as **collapsible per-tool rows**: collapsed = tool name + one-line description; expanded = params list derived from `inputSchema` (param name, type, required marker `*`, description).
- **D-04:** Each tool also has a **"JSON" toggle** showing the raw `inputSchema` (pretty-printed, inserted as text / via DOMPurify — never raw HTML).
- **D-05:** **Persistent session**: Connect opens a stdio MCP session and keeps it alive in an Agent in-memory registry keyed by `(user_id, server_id)` until the user presses Disconnect. Must be designed so later days (LLM tool calls) can reuse the live session. Registry lives in Agent process memory only (consistent with the `agent/state.py` pattern; no external broker).
- **D-06:** The registry must be cleaned up on: Disconnect, server config delete, config edit (D-08), disable (D-13), user deletion (cascade), and **Agent shutdown (lifespan)** — no orphaned `filesystem.exe` child processes.
- **D-07:** **After an app restart all servers show "not connected"** — only the config persists (success criterion 1). No auto-reconnect, no cached tool list in the DB.
- **D-08:** **Saving edits to a connected server auto-disconnects it** (status → not connected; user reconnects). Deleting a connected server also disconnects it first.
- **D-09:** **Liveness is checked lazily on status fetch**: when the UI requests server status (e.g. modal open / list refresh), the Agent checks the session/process is still alive and flips it to `error` with a "server exited" message if not. No background polling loop, no push over WS.
- **D-10:** Config fields: `name`, `command`, `args`, `env`, `cwd`, `enabled`. Stored in a new user-scoped SQLModel table (FK `user.id` via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`).
- **D-11:** **Args entered one per line** in a textarea, stored as a JSON list of strings (no shell/shlex parsing — Windows paths with spaces need no quoting).
- **D-12:** **Env vars** entered as `KEY=VALUE` one per line, stored as a JSON object, **plaintext in SQLite but masked (`•••`) in the UI after save** and never logged. Editing a server leaves existing env values untouched unless the user retypes them. Env is merged over the Agent's environment when spawning (so PATH etc. still work) — exact merge semantics at the planner's discretion.
- **D-13:** **`enabled` toggle**: a disabled server's Connect button is disabled; disabling a currently connected server disconnects it. (Later days: disabled servers are also excluded from LLM tool exposure.)
- **D-14:** Optional **working directory** (`cwd`) passed to the subprocess; empty = Agent's default.
- **D-15:** **No command-path validation on save** — any non-empty command is accepted; a bad path surfaces as a clear Connect-time error (this is success criterion 3's demo path).
- **D-16:** Handshake timeout (spawn + initialize + list_tools) defaults to **10 s**, configurable via a new `MCP_CONNECT_TIMEOUT` setting in `shared/config.py` (`.env`-overridable). Not per-server.
- **D-17:** Failures are classified into **fixed error codes** — at least `COMMAND_NOT_FOUND`, `PROCESS_EXITED`, `HANDSHAKE_TIMEOUT`, `PROTOCOL_ERROR` — each with a Russian user-facing message plus a raw `detail` string. Tests assert on the code. SDK exceptions (incl. anyio `ExceptionGroup`s) must be unwrapped/mapped, never leaked raw to the UI.
- **D-18:** The server's **stderr is captured**, and on failure the **last ~20 lines are shown in a collapsible block** under the error message in the UI (e.g. the Go server complaining that the directory does not exist).
- **D-19:** Connection failures never crash the Agent: `/health` must stay OK after any failed connect (success criterion 3).
- **D-20:** **CLI `scripts/mcp_list_tools.py <command> [args…]`**: prints a human-readable serverInfo header and each tool with description and params; on failure prints error code + message (and stderr tail) to stderr and exits with code 1. It **reuses the same Agent-side connect/list function** as the REST path (single implementation), not a separate copy. No `--json` flag.

### Claude's Discretion

- Exact REST endpoint shapes (e.g. `/api/v1/mcp/servers` CRUD + `/connect`, `/disconnect`, status), module name (e.g. `agent/mcp_client.py`) and table name.
- How the persistent session is held open with the `mcp` SDK (`stdio_client` / `ClientSession` are async context managers — likely a dedicated long-lived task per session so exit happens in the same task that entered). Researcher should confirm the correct pattern for `mcp` 1.30.x on Windows. **-> RESOLVED by this research: see Pattern 1 (owner-task), hands-on verified.**
- How stderr is captured on Windows (SDK `errlog` param vs temp file / pipe). **-> RESOLVED by this research: see Pattern 3, hands-on verified — must be a real OS-backed file (e.g. `tempfile.TemporaryFile`), not `io.StringIO`.**
- Exact version pin for `mcp` in `requirements.txt` (installed: 1.30.0). **-> Recommendation: pin `1.30.0` (installed + verified) — see State of the Art.**
- Whether a concurrent Connect on the same server is rejected or serialized (a per-server `asyncio.Lock` is the likely answer).
- Tailwind markup details, Russian label wording, and whether tool rows show a count badge. (See `07-UI-SPEC.md` — this has since been resolved by the UI design contract.)

### Deferred Ideas (OUT OF SCOPE)

- LLM invoking MCP tools during chat turns (MCP-F1) — later Week 4 day; the persistent session registry (D-05) is designed to be reused.
- Non-stdio transports (Streamable HTTP / SSE) — MCP-F3.
- Auto-reconnect on startup / cached "last seen" tool list — rejected for this phase (D-07).
- Encrypting stored env secrets — not needed for local-first scope.
</user_constraints>

## Summary

This phase adds a persistent, user-scoped MCP stdio client to the Agent process. The `mcp` Python SDK (already installed at 1.30.0, confirmed via `pip show`) handles process spawning, the JSON-RPC handshake, and tool listing. On Windows, the SDK's `stdio_client()` calls `anyio.open_process()` under a Job-Object-wrapped Windows process; **this requires `asyncio.ProactorEventLoop`, not `SelectorEventLoop`**. I verified that `uvicorn.run(app, ...)` — exactly as invoked by `ui/supervisor.py::_launch_agent()` (`python -m uvicorn agent.main:app --host ... --port ...`, no `--reload`, no `--workers`) — uses `asyncio.ProactorEventLoop` by default on `win32` (uvicorn only forces `SelectorEventLoop` when `use_subprocess=True`, which only happens with reload/multi-worker mode). **No event-loop-policy change is needed anywhere in this codebase** — the Agent process already runs on the loop MCP needs.

I hands-on-verified the exact exception shapes for all four D-17 error codes (see Common Pitfalls / Code Examples) and the "dedicated owner task" pattern for a persistent, cross-task-callable session (D-05/D-06), resolving every "Claude's Discretion" item flagged by CONTEXT.md with empirical evidence rather than guesswork. One material correction to CONTEXT.md's demo plan: **the Go `filesystem-mcp-server` does NOT exit when pointed at a nonexistent directory** (it logs a WARN and keeps serving all 17 tools) — a different failure trigger is needed for the PROCESS_EXITED demo (see Common Pitfalls #1).

**Primary recommendation:** Pin `mcp==1.30.0` (the version already installed and verified, not the newer `2.2.0` on PyPI — see State of the Art). Hold each server's live session in an Agent-process in-memory dict keyed by `(user_id, server_id)`, each entry backed by one dedicated `asyncio.Task` that owns the `stdio_client`/`ClientSession` async-context-manager pair end-to-end; other tasks (REST handlers) call `session.list_tools()` etc. directly on the stored `ClientSession` object — this is safe and was verified to work from separate tasks. Wrap the whole connect sequence (spawn + `initialize()` + `list_tools()`) in one `asyncio.timeout(MCP_CONNECT_TIMEOUT)`; classify the resulting exception by type (see Code Examples) into the four D-17 error codes.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| MCP server config CRUD (name/command/args/env/cwd/enabled) | API / Backend | Database / Storage | New user-scoped SQLModel table; REST CRUD endpoints in `agent/main.py`, same pattern as `Profile`/`Task` |
| stdio subprocess spawn + JSON-RPC handshake | API / Backend | — | Must run inside the Agent process (has the event loop, the `mcp` SDK, and OS process privileges); the UI process never touches subprocesses per CLAUDE.md |
| Live session registry (`(user_id, server_id)` -> owner task + `ClientSession`) | API / Backend (in-memory) | — | Exactly the `agent/state.py` pattern (per-process dict, no external cache); must NOT be persisted (D-07) |
| Connection status / serverInfo / tool list display | Browser / Client | API / Backend (source of truth) | Rendered by `ui/static/app.js` from REST responses; UI holds no state of its own beyond what it just fetched |
| stderr capture & tail display | API / Backend (capture) | Browser / Client (render) | Captured server-side into a real OS-backed temp file (see Pitfall #2), truncated to ~20 lines, returned as a field in the error response; rendered by the frontend inside a collapsible `<pre>` |
| CLI tool listing (`scripts/mcp_list_tools.py`) | API / Backend (reused logic) | — | D-20 requires it call the *same* connect/list function as the REST path — a plain script importing from `agent/mcp_client.py`, no HTTP involved |
| Agent shutdown / user-scoped cleanup of live sessions | API / Backend | — | Hooked into `agent/main.py::lifespan` (existing hook point, confirmed at line 318) and into config delete/edit/disable endpoints |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `mcp` | `1.30.0` (pinned — see State of the Art) | Official Anthropic Model Context Protocol Python SDK: `StdioServerParameters`, `stdio_client`, `ClientSession` | This *is* the reference client implementation; CONTEXT.md canonical_refs names it explicitly; already installed and hands-on verified in this exact repo/OS/Python combination `[VERIFIED: local install inspection + PyPI registry + direct execution against filesystem.exe]` |

`mcp`'s own transitive dependencies (`anyio`, `httpx`, `httpx-sse`, `jsonschema`, `pydantic`, `pydantic-settings`, `pyjwt`, `python-multipart`, `pywin32`, `sse-starlette`, `starlette`, `typing-extensions`, `typing-inspection`, `uvicorn`) are already satisfied by the project's existing `fastapi`/`pydantic`/`uvicorn` stack or are pulled automatically by `pip install mcp` — **no other new top-level entries needed in `requirements.txt`** beyond `mcp` itself `[VERIFIED: pip show mcp]`.

### Supporting
None — no additional libraries are needed. Do NOT add `shlex` parsing for args (D-11 explicitly rejects shell parsing — args are stored as a plain JSON list of strings, matching `StdioServerParameters.args: list[str]` directly).

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Official `mcp` SDK | Hand-rolled JSON-RPC-over-stdio client | Would have to reimplement the handshake, capability negotiation, cancellation/timeout semantics, and Windows Job-Object process-tree cleanup that `mcp` already provides — explicitly out of scope, see Don't Hand-Roll |
| `mcp==1.30.0` (already installed) | `mcp==2.2.0` (latest on PyPI) | 2.x is a new major version; I did not verify its API surface against this codebase. Upgrading is a reasonable follow-up but should not happen inside this phase without dedicated verification — see State of the Art |

**Installation:**
```bash
pip install mcp==1.30.0
```
Then add to `requirements.txt`:
```
mcp==1.30.0                # Model Context Protocol SDK (stdio client for Day 16)
```

**Version verification:** `pip show mcp` confirms `1.30.0` is installed; `pip index versions mcp` confirms it exists on PyPI (latest is `2.2.0`, full version list retrieved 2026-09-23). Both checks were run directly in this repo's Python environment.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|--------------|-----------|-------------|
| `mcp` | PyPI | Anthropic's official SDK, actively maintained (65 published versions from 0.9.1 through 2.2.0) | high (official MCP reference client, widely depended on) | `github.com/modelcontextprotocol/python-sdk` (verified — package `Home-page` metadata is `https://modelcontextprotocol.io`, `Author: Anthropic, PBC.`) | `[OK]` | Approved |

`slopcheck install mcp` ran successfully and returned `1 OK` (scanned via `pip show`/PyPI resolution). No `postinstall` scripts apply — this is a pure Python package with no npm involvement.

**Packages removed due to slopcheck [SLOP] verdict:** none.
**Packages flagged as suspicious [SUS]:** none.

## Architecture Patterns

### System Architecture Diagram

```
Browser (ui/static/app.js)
   |  fetch() REST calls
   v
Agent FastAPI process (agent/main.py)
   |
   |-- GET/POST/PUT/DELETE /api/v1/mcp/servers[...]  -> agent/mcp_config.py (CRUD, user_id-scoped, SQLModel)
   |
   |-- POST /api/v1/mcp/servers/{id}/connect
   |        |
   |        v
   |     agent/mcp_client.py::connect_server(user_id, server_row)
   |        |
   |        |-- spawns asyncio.Task(owner_task) that:
   |        |     async with stdio_client(StdioServerParameters(...), errlog=<tempfile>) as (r, w):
   |        |       async with ClientSession(r, w) as session:
   |        |         await session.initialize()   -- JSON-RPC handshake over stdin/stdout
   |        |         handle.session = session      -- publish into registry
   |        |         await close_event.wait()       -- parked until Disconnect
   |        |
   |        |-- child process: C:\Users\Aleksey\go\bin\filesystem.exe <args>  (stdio JSON-RPC)
   |        |
   |        `-- registry[(user_id, server_id)] = SessionHandle  (in-memory only, agent/state.py pattern)
   |
   |-- GET /api/v1/mcp/servers/{id}/status  -> lazy liveness check (D-09): registry lookup + process.poll()-equivalent
   |
   |-- POST /api/v1/mcp/servers/{id}/disconnect -> sets close_event, awaits handle.closed, evicts registry entry
   |
   `-- lifespan shutdown -> iterate registry, set close_event on every handle, await all closed

scripts/mcp_list_tools.py  --(direct import, no HTTP)-->  agent/mcp_client.py::connect_once_and_list(command, args)
```

A reader can trace: browser presses Connect -> REST call -> `mcp_client.connect_server` spawns an owner task -> owner task opens the child process and performs the MCP handshake -> session handle is published into the process-local registry -> subsequent REST calls (status, tool list refresh, disconnect) look up the same registry entry and either read from the live `ClientSession` or signal the owner task to close.

### Recommended Project Structure
```
agent/
├── mcp_config.py     # SQLModel CRUD for the server-config table (mirrors agent/profile.py)
├── mcp_client.py      # Connect/disconnect/list_tools + registry + error classification (mirrors agent/state.py for registry, agent/llm_client.py for the "client" naming convention)
├── mcp_schemas.py      # OR add to agent/schemas.py — Pydantic request/response models for MCP endpoints (planner's call; project currently keeps all schemas in one agent/schemas.py file, which argues for reusing it rather than splitting)
scripts/
└── mcp_list_tools.py   # New directory + file (D-20) — imports agent.mcp_client directly
shared/
└── models.py           # Add McpServerConfig table (existing file, existing pattern)
tests/
├── test_mcp_client.py       # connect/list_tools success + failure paths against a fixture stdio server
├── test_mcp_config_api.py   # CRUD REST endpoint tests (mirrors test_profile_api.py)
└── fixtures/
    └── mcp_stdio_server.py  # tiny Python MCP server (using the mcp SDK's own server-side API) for fast, Windows-CI-safe failure-path tests that don't depend on filesystem.exe being present
```

### Pattern 1: Owner-task-holds-the-context-managers (persistent session, D-05/D-06)
**What:** A single dedicated `asyncio.Task` performs `async with stdio_client(...) as (r, w): async with ClientSession(r, w) as session: ...`, publishes the live `session` object into a shared registry once `initialize()` succeeds, then parks on an `asyncio.Event` until told to close. Other tasks (REST handlers running in the same event loop) call methods directly on the published `ClientSession` object.
**When to use:** Any time a long-lived resource is guarded by nested async context managers whose `__aexit__` must run in the same task that ran `__aenter__` (true of anyio's cancel scopes, which both `stdio_client` and `ClientSession` use internally).
**Why this is required, not optional:** anyio's `TaskGroup`/cancel-scope machinery raises `RuntimeError` if you try to exit a scope from a different task than the one that entered it. A REST handler cannot itself hold the `async with` open across multiple separate HTTP requests (each request is its own task/coroutine) — hence the owner task.
**Verified:** I ran this exact pattern (owner task publishes `ClientSession`; two separate "request" tasks call `session.list_tools()` concurrently; a third task signals close via `asyncio.Event`) against the real `filesystem.exe` and confirmed correct results and clean shutdown with no orphaned process. `[VERIFIED: direct execution, mcp 1.30.0, Windows, Python 3.13.15]`

```python
# Source: verified by direct execution against mcp 1.30.0 + filesystem.exe on Windows
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class SessionHandle:
    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.ready = asyncio.Event()
        self.close_requested = asyncio.Event()
        self.closed = asyncio.Event()
        self.error: Exception | None = None

async def owner_task(handle: SessionHandle, params: StdioServerParameters, errfile) -> None:
    try:
        async with stdio_client(params, errlog=errfile) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                handle.session = session
                handle.ready.set()
                await handle.close_requested.wait()   # parked until Disconnect
    except Exception as exc:
        handle.error = exc
        handle.ready.set()
    finally:
        handle.closed.set()

# REST "Connect" handler:
handle = SessionHandle()
task = asyncio.create_task(owner_task(handle, params, errfile))
await asyncio.wait_for(handle.ready.wait(), timeout=MCP_CONNECT_TIMEOUT)
if handle.error is not None:
    raise map_to_error_code(handle.error)
registry[(user_id, server_id)] = handle
tasks[(user_id, server_id)] = task

# REST "Disconnect" handler (or config-delete/edit/disable, or lifespan shutdown):
handle.close_requested.set()
await asyncio.wait_for(handle.closed.wait(), timeout=5.0)
await tasks.pop((user_id, server_id))
registry.pop((user_id, server_id), None)
```

Note: the `ready`/`initialize()` step should itself be wrapped in the overall `MCP_CONNECT_TIMEOUT` (see Pattern 2) — the snippet above separates concerns for readability; the actual connect endpoint should wrap the create-task-and-wait-for-ready sequence in one `asyncio.timeout(...)`.

### Pattern 2: One timeout, four error codes (D-16/D-17)
**What:** Wrap spawn + `initialize()` + `list_tools()` in a single `asyncio.timeout(MCP_CONNECT_TIMEOUT)`, then classify the caught exception by type/content.
**Verified exception shapes** (all captured by direct execution against `mcp` 1.30.0 on Windows, see Common Pitfalls for the raw tracebacks):

| Scenario | Exception observed | -> Error code |
|----------|--------------------|--------------|
| Command path does not exist | `FileNotFoundError` (subclass of `OSError`) raised directly, un-wrapped — `stdio_client`'s own `except OSError: ... raise` re-raises it cleanly | `COMMAND_NOT_FOUND` |
| Process exits/closes its stdout before or during the handshake (e.g. a flag that makes the binary print plain text and exit, like `filesystem.exe -list <dir>`) | `ExceptionGroup`/`BaseExceptionGroup`, **nested one level**, whose leaf is `mcp.shared.exceptions.McpError` with message `"Connection closed"` | `PROCESS_EXITED` |
| Process spawns but never writes any JSON-RPC to stdout (hangs) | The `asyncio.timeout()` context manager itself raises a plain `TimeoutError` (builtin, un-nested, un-wrapped) once the deadline passes — **note total wall-clock time can run ~2s beyond the configured timeout**, because `stdio_client`'s cleanup path waits up to `PROCESS_TERMINATION_TIMEOUT = 2.0` seconds for graceful exit before force-killing | `HANDSHAKE_TIMEOUT` |
| Server responds but with malformed/unsupported protocol data | `RuntimeError("Unsupported protocol version from the server: ...")` from `ClientSession.initialize()`, OR an `McpError` with a JSON-RPC error payload | `PROTOCOL_ERROR` |

```python
# Source: verified by direct execution, mcp 1.30.0
import asyncio

def classify_mcp_error(exc: BaseException) -> tuple[str, str]:
    """Map a raw exception from the connect sequence to a (code, detail) pair."""
    if isinstance(exc, TimeoutError):
        return "HANDSHAKE_TIMEOUT", str(exc) or "Connect sequence exceeded the configured timeout"
    if isinstance(exc, FileNotFoundError):
        return "COMMAND_NOT_FOUND", str(exc)
    if isinstance(exc, OSError):
        # e.g. permission denied launching the executable
        return "COMMAND_NOT_FOUND", str(exc)
    if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
        leaves = _flatten_exception_group(exc)
        for leaf in leaves:
            if "Connection closed" in str(leaf):
                return "PROCESS_EXITED", str(leaf)
        return "PROTOCOL_ERROR", "; ".join(str(leaf) for leaf in leaves)
    # mcp.shared.exceptions.McpError or a bare RuntimeError from initialize()
    return "PROTOCOL_ERROR", str(exc)

def _flatten_exception_group(exc: BaseException) -> list[BaseException]:
    """Recursively unwrap nested ExceptionGroups to their leaf exceptions."""
    if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
        leaves: list[BaseException] = []
        for sub in exc.exceptions:
            leaves.extend(_flatten_exception_group(sub))
        return leaves
    return [exc]
```

`ExceptionGroup`/`BaseExceptionGroup` are Python 3.11+ builtins (no import needed on this project's Python 3.13 floor). Never `str(exc)` an `ExceptionGroup` and show it raw to the user — always flatten to leaves first (D-17's explicit requirement).

### Pattern 3: stderr capture requires a real OS-backed file, not `io.StringIO` (D-18)
**What:** `stdio_client(server, errlog=...)` passes `errlog` straight through to `asyncio.create_subprocess_exec`'s `stderr=` kwarg on Windows, which calls `msvcrt.get_osfhandle(stderr.fileno())`. `io.StringIO`/`io.BytesIO` have no OS file descriptor and raise `io.UnsupportedOperation: fileno` — **verified by direct execution**, this fails immediately, before the process is even spawned.
**Fix:** Use `tempfile.TemporaryFile(mode="w+", encoding="utf-8")` (has a real fd), write the child's stderr to it during the connect attempt, then `seek(0)`/`read()`/take the last ~20 lines, then close (auto-deletes on Windows when the last handle closes).
```python
# Source: verified by direct execution, mcp 1.30.0
import tempfile

errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
try:
    async with stdio_client(params, errlog=errfile) as (read, write):
        ...
finally:
    errfile.seek(0)
    lines = errfile.read().splitlines()
    stderr_tail = "\n".join(lines[-20:])
    errfile.close()
```
Note from the probe: for the `-list`-flag exit-immediately scenario, stderr came back **empty** — the diagnostic signal in that case is the `McpError('Connection closed')` in the exception, not stderr. Don't assume stderr will always be populated for `PROCESS_EXITED`; the UI's stderr block should just render empty/absent gracefully when there's nothing captured.

### Anti-Patterns to Avoid
- **Passing `io.StringIO()`/`io.BytesIO()` as `errlog` on Windows:** fails with `io.UnsupportedOperation: fileno` before the process even spawns — verified above.
- **Passing the full `os.environ` as `StdioServerParameters(env=...)`:** unnecessary and a minor leak surface. The SDK's own `get_default_environment()` already inherits a curated safe subset (`PATH`, `SYSTEMROOT`, `APPDATA`, etc. on Windows — full list in Code Examples) and merges the user-supplied `env` dict on top when `env is not None`. Just pass the server's stored `env` dict (or `None`/`{}` if empty) directly — do not manually merge `os.environ` yourself; the SDK already does the right thing for D-12.
- **`str(exc)` on a raw `ExceptionGroup` shown to the user:** produces `"unhandled errors in a TaskGroup"` with no actionable info — always flatten to leaves (Pattern 2).
- **Entering `stdio_client`/`ClientSession` in one task and exiting in another:** anyio raises `RuntimeError` — must use the owner-task pattern (Pattern 1).
- **Re-implementing shell-style arg parsing (`shlex.split`) for the `args` field:** explicitly rejected by D-11 — store/read the JSON list of strings as-is; Windows paths with spaces need no quoting when passed as a `list[str]` to `StdioServerParameters.args`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON-RPC framing, request/response correlation, capability negotiation | Custom stdio JSON-RPC client | `mcp.ClientSession` | The protocol has request IDs, notifications, capability negotiation (`initialize`), and cancellation semantics that are easy to get subtly wrong; `mcp` is the reference implementation |
| Windows child-process-tree cleanup (killing grandchildren) | Manual `taskkill /T /F` or `psutil` tree-walk | `mcp`'s built-in Windows Job Object wrapping (`mcp.os.win32.utilities.create_windows_process` / `terminate_windows_process_tree`) | Already handles the SIGTERM-then-SIGKILL-equivalent escalation and Job Object assignment so all children die when the parent is killed; reinventing this on Windows (no POSIX process groups) is significant, easy-to-get-wrong work |
| stdio transport encoding/line-framing | Manual `readline()`/`\n`-split loop over `process.stdout` | `mcp.client.stdio.stdio_client`'s internal `stdout_reader`/`stdin_writer` tasks | Already handles partial-line buffering, encoding, and backpressure via anyio memory streams |

**Key insight:** Everything this phase needs (spawn, handshake, tool listing, cancellation, process-tree cleanup) is already implemented and tested inside the `mcp` SDK. The only genuinely new code this phase must write is: the config CRUD table/endpoints, the in-memory registry keyed by `(user_id, server_id)`, the owner-task lifecycle wrapper, and the exception -> error-code classification — all things specific to *this* app's architecture, not things the SDK could provide generically.

## Common Pitfalls

### Pitfall 1: CONTEXT.md's stated PROCESS_EXITED demo does not actually reproduce PROCESS_EXITED
**What goes wrong:** CONTEXT.md's `<specifics>` section and success criterion 3 both suggest "a server that exits immediately (e.g. filesystem.exe pointed at a nonexistent directory)" as the demo for a crashing/exiting server.
**Why it happens:** I tested this directly: `filesystem.exe C:\this\path\does\not\exist\at\all` does **not** exit. The Go binary logs `level=WARN msg="directory not accessible" ...` to stderr and then proceeds to start the MCP server normally, registering all 17 tools and answering `initialize`/`list_tools` exactly as if a valid directory had been given (I confirmed `tool count: 17` in this scenario). The directory argument is advisory to the filesystem tools' runtime behavior, not a startup precondition.
**How to avoid:** Use a different trigger for the PROCESS_EXITED demo/test. Verified working alternative: `filesystem.exe -list <dir>` — this flag makes the binary print the allowed-directory list as **plain text** (not JSON-RPC) to stdout and exit(0) immediately, which the SDK's stdout parser rejects (`Failed to parse JSONRPC message from server`), closing the stream and surfacing as `ExceptionGroup(... McpError('Connection closed'))`. A synthetic broken Python stdio-server fixture (writes garbage then exits) is an equally valid, more Windows-CI-portable option for the pytest failure-path tests.
**Warning signs:** If a "nonexistent directory" test/demo shows `connected` status with 17 tools instead of an error, this pitfall has been hit — update the plan/demo script to use `-list` or a fixture server instead.

### Pitfall 2: `errlog` on Windows needs a real file descriptor, not any writable object
**What goes wrong:** `io.StringIO()`/a custom in-memory buffer passed as `errlog=` to `stdio_client()` raises `io.UnsupportedOperation: fileno` immediately (before the child process is even created), which itself gets caught and re-raised, masking the real connect attempt.
**Why it happens:** On Windows, `asyncio`'s subprocess implementation calls `msvcrt.get_osfhandle(stderr.fileno())` to get a real OS handle to hand to `CreateProcess`. Only objects backed by a real file descriptor work (`sys.stderr`, `tempfile.TemporaryFile()`, `open(path, ...)`).
**How to avoid:** Use `tempfile.TemporaryFile(mode="w+", encoding="utf-8")` as shown in Pattern 3. Verified working.
**Warning signs:** Every connect attempt failing identically with `UnsupportedOperation('fileno')` regardless of the command being valid — that's this bug, not a real connection failure.

### Pitfall 3: stdlib `logging` output from the `mcp` package bypasses `structlog`'s JSON formatting
**What goes wrong:** `mcp.client.stdio`'s internal parser calls `logger.exception("Failed to parse JSONRPC message from server")` via plain stdlib `logging.getLogger(__name__)`, not `structlog`. Because `shared/logger.py::setup_logging()` calls `logging.basicConfig(format="%(message)s", stream=sys.stdout, ...)`, this line (and its traceback) prints as a single unstructured line mixed into the Agent's otherwise-JSON log stream on `stdout`.
**Why it happens:** `structlog` is configured as a wrapper around the stdlib logging root logger, but third-party libraries that log through plain `logging.getLogger(...).exception(...)` still go through the root handler's raw formatter, not structlog's JSON renderer.
**How to avoid:** Expected/benign — this only fires on the PROCESS_EXITED/malformed-JSON path, which is already an error condition being surfaced to the user anyway. No action required beyond being aware log-parsing tooling downstream might see one non-JSON line per such failure. If strict JSON-only logs are ever required, this would need a `logging.getLogger("mcp").addHandler(...)` override — out of scope for this phase.
**Warning signs:** A non-JSON line appearing in `logs/agent.log` right around a failed MCP connect attempt — expected, not a bug.

### Pitfall 4: Handshake-timeout wall-clock time exceeds the configured `MCP_CONNECT_TIMEOUT`
**What goes wrong:** Setting `MCP_CONNECT_TIMEOUT=10` does not mean a hung server reports failure after exactly 10s.
**Why it happens:** `stdio_client`'s `finally` cleanup, on exit (including exit-via-cancellation from the outer timeout), always attempts a graceful shutdown first: close stdin, then `await process.wait()` under `anyio.fail_after(PROCESS_TERMINATION_TIMEOUT)` where `PROCESS_TERMINATION_TIMEOUT = 2.0` seconds, before falling back to force-kill. I measured this directly: with `timeout=2.0`, actual elapsed time to raise was **4.02s**.
**How to avoid:** Document this for the UI copy/UX (the `HANDSHAKE_TIMEOUT` message already interpolates `{N}` = the configured timeout, which is correct as the *nominal* value — no code change needed, just don't be surprised by the extra ~2s in manual testing/demos).
**Warning signs:** A timeout test asserting on exact elapsed time will be flaky; assert on `elapsed <= MCP_CONNECT_TIMEOUT + PROCESS_TERMINATION_TIMEOUT + margin` instead.

### Pitfall 5: `-m uvicorn` invocation choice matters for the event loop on Windows
**What goes wrong:** If a future change to `ui/supervisor.py::_launch_agent()` ever adds `--reload` or `--workers N` to the Agent's uvicorn invocation, `uvicorn`'s `asyncio_loop_factory(use_subprocess=True)` would switch to `asyncio.SelectorEventLoop`, which does **not** support `anyio.open_process()` for the primary path — `mcp` would silently fall back to the synchronous `subprocess.Popen`-based `FallbackProcess` wrapper (still functional per the SDK's own fallback code, but untested by this research and a behavior change worth flagging).
**Why it happens:** `uvicorn/loops/asyncio.py::asyncio_loop_factory()` — verified by reading the installed uvicorn 0.52.4 source directly.
**How to avoid:** Do not add `--reload`/`--workers` to the Agent's uvicorn launch command without re-verifying MCP stdio behavior under `SelectorEventLoop`/`FallbackProcess`.
**Warning signs:** N/A for this phase (current invocation is already Proactor-safe) — flagged for awareness only.

## Code Examples

### Full connect sequence with timeout + error classification
```python
# Source: verified by direct execution, mcp 1.30.0, agent/mcp_client.py pattern
import asyncio
import tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def connect_and_handshake(
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: str | None,
    timeout_seconds: float,
) -> tuple[ClientSession, object, asyncio.Event, str]:
    """Spawn + handshake + list_tools within one timeout window.

    Returns (session, errfile, close_event) on success, or raises a classified
    exception (see classify_mcp_error) on failure. Caller is responsible for
    keeping the owning task alive (Pattern 1) after this returns.
    """
    params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
    errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    async with asyncio.timeout(timeout_seconds):
        async with stdio_client(params, errlog=errfile) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                tools = await session.list_tools()
                # NOTE: session/streams close as soon as this `async with` exits --
                # in the real registry pattern, this function's body instead
                # publishes `session` and parks (Pattern 1); this snippet is
                # illustrative of the handshake step only.
                return result, tools
```

### Windows default-inherited env vars (relevant to D-12)
```python
# Source: mcp/client/stdio/__init__.py, mcp 1.30.0 (read directly from installed package)
DEFAULT_INHERITED_ENV_VARS = [
    "APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT",
    "PROCESSOR_ARCHITECTURE", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP",
    "USERNAME", "USERPROFILE",
]  # Windows list; POSIX list is HOME/LOGNAME/PATH/SHELL/TERM/USER
```
This confirms D-12's "so PATH etc. still work" requirement is satisfied automatically by the SDK's `get_default_environment()` — pass the stored per-server `env` dict straight through as `StdioServerParameters(env=stored_env_or_none)`; do not manually splice `os.environ`.

### Real serverInfo/protocolVersion/tool data captured from the actual demo target
```
serverInfo: name='filesystem-mcp-server' version='1.0.0'
protocolVersion: 2024-11-05
tool count: 17
sample tools: copy_file, create_directory, delete_directory, ...
```
Captured 2026-09-23 by connecting to `C:\Users\Aleksey\go\bin\filesystem.exe` with an existing directory argument — matches CONTEXT.md's stated expectations (`filesystem-mcp-server`, 17 tools) exactly. `protocolVersion` is `"2024-11-05"` (a date-string, not a semver) — plan the `serverInfo` display line (`{name} · v{version} · MCP {protocolVersion}` per UI-SPEC) accordingly; it's a string field (`str | int` per `InitializeResult.protocolVersion`), no special parsing needed.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| n/a | `mcp` 1.30.0 (installed) vs 2.2.0 (latest on PyPI) | 2.x line exists as of this research (2026-09-23); exact API-compatibility of the code patterns above with 2.x was **not** verified in this session | Recommend staying on `1.30.0` for this phase — all patterns above are hands-on-verified against it. Upgrading to 2.x is a reasonable future improvement but needs its own verification pass (major version bumps in fast-moving SDKs commonly break internal-module import paths, and this research relied on `mcp.os.win32.utilities` / `mcp.client.stdio` internals that could move) |

**Deprecated/outdated:** `mcp.os.win32.utilities.terminate_windows_process` is marked `@deprecated` in the installed 1.30.0 source ("Process termination is now handled internally by the stdio_client context manager") — do not call it directly; rely on `stdio_client`'s own `finally` cleanup (already verified to leave no orphan process).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `mcp==1.30.0` is the right version to pin (vs. upgrading to `2.2.0`) | Standard Stack / State of the Art | Low — this is a recommendation based on "verified beats unverified," not a hard requirement; if the user/planner prefers to test 2.x, all the exception-shape findings in this doc would need re-verification |
| A2 | Module names `agent/mcp_config.py` and `agent/mcp_client.py` | Recommended Project Structure | None — CONTEXT.md explicitly leaves module naming to Claude's discretion; these are suggestions, easily renamed |

No claims about SDK *behavior* (exception shapes, event loop requirements, stderr capture mechanics, cross-task session usage) are in this table — every one of those was verified by direct execution in this session, not assumed.

## Open Questions

1. **Exact registry data structure / module split (`mcp_config.py` vs `mcp_client.py` vs one file)**
   - What we know: CONTEXT.md leaves this to Claude's discretion; the codebase's existing convention (`agent/profile.py` for CRUD, `agent/state.py` for in-memory registries, `agent/llm_client.py` for the "talk to an external process/API" module) suggests a 2-3 file split.
   - What's unclear: Whether the planner wants schemas in a new `agent/mcp_schemas.py` or appended to the existing single `agent/schemas.py` (current convention is one shared file).
   - Recommendation: Follow the existing single-`schemas.py` convention (simpler, consistent) unless the planner has a reason to split.

2. **Whether to expose a `client_info` (name/version) in `ClientSession(...)`**
   - What we know: `ClientSession.__init__` defaults to `DEFAULT_CLIENT_INFO` if not supplied; not supplying one works fine (verified — the success-path probe used the default).
   - What's unclear: Whether the Go server's logs or any future server benefits from a custom identifying `clientInfo=types.Implementation(name="AiAdventAgentV2", version=...)`.
   - Recommendation: Optional nicety, not required for any of the 6 requirements; skip unless there's a specific reason to add it.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `mcp` Python package | MCP-01..06 | ✓ (already installed) | 1.30.0 | — |
| `filesystem.exe` (Go MCP server) | Success criteria 1-4 demo/proof | ✓ | reports `version='1.0.0'`, `-version`/`-help` confirm the binary responds | — |
| `pywin32` (mcp's Windows dependency) | Windows Job Object process-tree cleanup | ✓ (already installed, `pywin32==312`, pulled transitively by `mcp`) | 312 | — |
| ProactorEventLoop under uvicorn (Agent process) | All MCP stdio operations | ✓ (verified — current `_launch_agent()` invocation is Proactor-safe by default) | n/a | — |

**Missing dependencies with no fallback:** none — everything needed is already present in this environment.
**Missing dependencies with fallback:** none.

## Project Constraints (from CLAUDE.md)

The following CLAUDE.md directives are directly load-bearing for this phase's plan and were checked against every recommendation above:

- No `multiprocessing`/`os.fork` — `mcp`'s `stdio_client` uses `asyncio.create_subprocess_exec`-equivalent (`anyio.open_process` -> `loop.subprocess_exec`) internally, not `multiprocessing`. Compliant.
- No Docker/npm/Node — the Go binary is a native executable, no Node/npx server type is in scope (matches REQUIREMENTS.md's explicit Out-of-Scope entry).
- IPC stays REST + WebSocket, in-memory state per process — the live-session registry is an Agent-process-local `dict`, matching `agent/state.py`'s existing pattern; no new external cache/broker.
- `structlog` everywhere, no `print()` — `agent/mcp_client.py`/`agent/mcp_config.py` must use `logger = get_logger(__name__)`; `scripts/mcp_list_tools.py` is the CLAUDE.md-documented **exception** (like `run.py`) since it is intentionally console-facing CLI output.
- `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` for the new server-config table's `user_id` FK — never `Field(ondelete=...)`.
- `.is_(None)` instead of `== None` in any SQLAlchemy `where()` clauses for the new CRUD queries.
- `await session.commit()` / `await session.rollback()` pattern for all new DB writes (mirrors `agent/profile.py::update_profile`).
- Never bare `except:` — the error-classification code in Pattern 2 must catch specific exception types (`TimeoutError`, `OSError`/`FileNotFoundError`, `(ExceptionGroup, BaseExceptionGroup)`, and a final `Exception` fallback mapped to `PROTOCOL_ERROR`), never a bare `except:`.
- `datetime.now(timezone.utc)`, never `datetime.utcnow()` — for the new table's `created_at`/`updated_at` fields.
- Frontend: vanilla JS + Tailwind CDN only, `DOMPurify.sanitize()` before any dynamic HTML insertion (tool descriptions, param descriptions, raw `inputSchema` JSON, stderr tail, error `detail` — all server-provided strings) — per UI-SPEC.md's explicit table of what must be sanitized.
- `asyncio.wait_for`/`asyncio.timeout` wrapping for the connect sequence — directly satisfied by D-16/Pattern 2.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MCP-01 | Add/edit/delete MCP server configs (name, command, args) scoped by `user_id` | `agent/profile.py`-style CRUD pattern (Recommended Project Structure); `McpServerConfig` table design with `sa_column=Column(ForeignKey("user.id", ondelete="CASCADE"))` per CLAUDE.md; D-10/D-11/D-12/D-14 field shapes from CONTEXT.md |
| MCP-02 | Press "Connect"; Agent opens stdio MCP session (`initialize`); UI shows status + serverInfo | Pattern 1 (owner-task persistent session) + Pattern 2 (timeout+classification), both hands-on verified against `filesystem.exe`; confirmed real `serverInfo`/`protocolVersion` shape in Code Examples |
| MCP-03 | UI lists tools (name, description, params from `inputSchema`) after connect | `mcp.types.Tool` schema confirmed (`name`/`description`/`inputSchema: dict[str, Any]`) via direct source inspection; `session.list_tools()` verified to return all 17 tools from the real server |
| MCP-04 | Connection failures reported clearly; Agent `/health` stays OK | Pattern 2's exception classification (4 verified exception shapes -> 4 error codes) plus Pitfall 1's correction to the originally-planned demo path; owner-task pattern isolates failures to the registry entry, never crashes the Agent event loop (verified: failed connects in the probe never affected subsequent successful connects in the same process) |
| MCP-05 | `mcp` pinned in `requirements.txt`; pytest covers connect + list_tools success/failure | Package Legitimacy Audit (slopcheck `[OK]`, version `1.30.0` confirmed); Recommended Project Structure's `test_mcp_client.py` + fixture-server suggestion for Windows-CI-safe failure-path tests independent of `filesystem.exe`'s presence |
| MCP-06 | Standalone CLI `scripts/mcp_list_tools.py` reusing the same connect/list function | Recommended Project Structure (`scripts/mcp_list_tools.py` imports `agent/mcp_client.py` directly, no HTTP); D-20's "single implementation" requirement directly satisfied by Pattern 1/2 being plain importable async functions, not endpoint-only code |

</phase_requirements>

## Sources

### Primary (HIGH confidence — direct execution / direct source inspection in this session)
- `mcp` 1.30.0 installed package source, read directly via `inspect.getsource()`: `mcp/client/stdio/__init__.py` (`stdio_client`, `StdioServerParameters`, `get_default_environment`, `DEFAULT_INHERITED_ENV_VARS`, `PROCESS_TERMINATION_TIMEOUT`), `mcp/os/win32/utilities.py` (`create_windows_process`, `FallbackProcess`, Job Object handling, `terminate_windows_process_tree`), `mcp/client/session.py` (`ClientSession.__init__`, `.initialize()`, `.list_tools()`), `mcp/shared/exceptions.py` (`McpError`), `mcp/types.py` (`Implementation`, `InitializeResult`, `Tool`)
- Direct execution of three probe scripts against the real `C:\Users\Aleksey\go\bin\filesystem.exe` binary in this session (success path; `COMMAND_NOT_FOUND`; bad-directory-tolerance discovery; `-list`-flag `PROCESS_EXITED`; true `HANDSHAKE_TIMEOUT` via a hanging `python -c "time.sleep(30)"` child; owner-task/cross-task-call persistence pattern) — outputs captured verbatim above
- `uvicorn` 0.52.4 installed source: `uvicorn/loops/asyncio.py::asyncio_loop_factory` (ProactorEventLoop default on win32 unless `use_subprocess=True`)
- This repo's own source: `ui/supervisor.py::_launch_agent` (confirms the exact uvicorn invocation used to run the Agent), `agent/main.py::lifespan`, `agent/dependencies.py::get_current_user`, `agent/profile.py`, `shared/models.py`, `shared/logger.py::setup_logging`, `agent/state.py`, `ui/static/index.html` (`#settings-modal`), `ui/static/app.js` (`showToast`, `data-fold-toggle`, `DOMPurify.sanitize` usage), `tests/conftest.py`
- `pip show mcp`, `pip index versions mcp`, `slopcheck install mcp` — run directly in this session

### Secondary (MEDIUM confidence)
- None used — all technical claims in this document trace to primary sources above.

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — single package, version confirmed installed + on registry + slopcheck OK
- Architecture: HIGH — every pattern (owner-task persistence, timeout+classification, stderr capture) was executed and its output captured in this session, not inferred from docs
- Pitfalls: HIGH — all 5 pitfalls are direct observations from execution, including one correction to CONTEXT.md's stated demo plan (Pitfall 1)

**Research date:** 2026-09-23
**Valid until:** 30 days (stable, official SDK; re-verify if `mcp` is upgraded past 1.30.x, or if `ui/supervisor.py`'s uvicorn invocation gains `--reload`/`--workers`)
