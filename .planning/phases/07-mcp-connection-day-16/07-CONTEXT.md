# Phase 7: MCP Connection (Day 16) - Context

**Gathered:** 2026-09-23
**Status:** Ready for planning

<domain>
## Phase Boundary

A user can add/edit/delete MCP server configs (per `user_id`) in the Settings UI, press "Connect" to have the Agent open a stdio MCP session (initialize handshake) against that server, and see connection status, serverInfo (name, version, protocol version) and the server's tool list (name, description, parameters from inputSchema). Failures are reported clearly without crashing the Agent. A standalone CLI script (`scripts/mcp_list_tools.py`) proves the same connect + list_tools flow from the console. Proven against the local Go filesystem server `C:\Users\Aleksey\go\bin\filesystem.exe` (17 tools, serverInfo `filesystem-mcp-server`).

**Not in this phase:** LLM calling MCP tools in chat (MCP-F1), non-stdio transports (MCP-F3), MCP resources/prompts, Node/npx servers.
</domain>

<decisions>
## Implementation Decisions

### UI placement & tool view
- **D-01:** MCP server management is a new **"MCP серверы" section inside the existing Settings modal** (`ui/static/index.html` `#settings-modal`), not a sidebar panel or modal tabs. The modal may widen and/or scroll to fit. The section is **user-scoped and independent of the "Настройки для текущего чата" per-chat checkbox** and of the modal's Save button — MCP CRUD/Connect actions hit their own REST endpoints immediately.
- **D-02:** **Multiple servers per user**, shown as a list of rows; each row has its own Edit / Delete / Connect (Disconnect when connected) buttons plus a status badge (not connected / connecting / connected / error).
- **D-03:** After a successful connect, the row shows serverInfo (name, version, protocolVersion) and the tool list as **collapsible per-tool rows**: collapsed = tool name + one-line description; expanded = params list derived from `inputSchema` (param name, type, required marker `*`, description).
- **D-04:** Each tool also has a **"JSON" toggle** showing the raw `inputSchema` (pretty-printed, inserted as text / via DOMPurify — never raw HTML).

### Connection lifecycle
- **D-05:** **Persistent session**: Connect opens a stdio MCP session and keeps it alive in an Agent in-memory registry keyed by `(user_id, server_id)` until the user presses Disconnect. Must be designed so later days (LLM tool calls) can reuse the live session. Registry lives in Agent process memory only (consistent with the `agent/state.py` pattern; no external broker).
- **D-06:** The registry must be cleaned up on: Disconnect, server config delete, config edit (D-08), disable (D-13), user deletion (cascade), and **Agent shutdown (lifespan)** — no orphaned `filesystem.exe` child processes.
- **D-07:** **After an app restart all servers show "not connected"** — only the config persists (success criterion 1). No auto-reconnect, no cached tool list in the DB.
- **D-08:** **Saving edits to a connected server auto-disconnects it** (status → not connected; user reconnects). Deleting a connected server also disconnects it first.
- **D-09:** **Liveness is checked lazily on status fetch**: when the UI requests server status (e.g. modal open / list refresh), the Agent checks the session/process is still alive and flips it to `error` with a "server exited" message if not. No background polling loop, no push over WS.

### Config input shape
- **D-10:** Config fields: `name`, `command`, `args`, `env`, `cwd`, `enabled`. Stored in a new user-scoped SQLModel table (FK `user.id` via `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`).
- **D-11:** **Args entered one per line** in a textarea, stored as a JSON list of strings (no shell/shlex parsing — Windows paths with spaces need no quoting).
- **D-12:** **Env vars** entered as `KEY=VALUE` one per line, stored as a JSON object, **plaintext in SQLite but masked (`•••`) in the UI after save** and never logged. Editing a server leaves existing env values untouched unless the user retypes them. Env is merged over the Agent's environment when spawning (so PATH etc. still work) — exact merge semantics at the planner's discretion.
- **D-13:** **`enabled` toggle**: a disabled server's Connect button is disabled; disabling a currently connected server disconnects it. (Later days: disabled servers are also excluded from LLM tool exposure.)
- **D-14:** Optional **working directory** (`cwd`) passed to the subprocess; empty = Agent's default.
- **D-15:** **No command-path validation on save** — any non-empty command is accepted; a bad path surfaces as a clear Connect-time error (this is success criterion 3's demo path).

### Error & timeout reporting
- **D-16:** Handshake timeout (spawn + initialize + list_tools) defaults to **10 s**, configurable via a new `MCP_CONNECT_TIMEOUT` setting in `shared/config.py` (`.env`-overridable). Not per-server.
- **D-17:** Failures are classified into **fixed error codes** — at least `COMMAND_NOT_FOUND`, `PROCESS_EXITED`, `HANDSHAKE_TIMEOUT`, `PROTOCOL_ERROR` — each with a Russian user-facing message plus a raw `detail` string. Tests assert on the code. SDK exceptions (incl. anyio `ExceptionGroup`s) must be unwrapped/mapped, never leaked raw to the UI.
- **D-18:** The server's **stderr is captured**, and on failure the **last ~20 lines are shown in a collapsible block** under the error message in the UI (e.g. the Go server complaining that the directory does not exist).
- **D-19:** Connection failures never crash the Agent: `/health` must stay OK after any failed connect (success criterion 3).
- **D-20:** **CLI `scripts/mcp_list_tools.py <command> [args…]`**: prints a human-readable serverInfo header and each tool with description and params; on failure prints error code + message (and stderr tail) to stderr and exits with code 1. It **reuses the same Agent-side connect/list function** as the REST path (single implementation), not a separate copy. No `--json` flag.

### Claude's Discretion
- Exact REST endpoint shapes (e.g. `/api/v1/mcp/servers` CRUD + `/connect`, `/disconnect`, status), module name (e.g. `agent/mcp_client.py`) and table name.
- How the persistent session is held open with the `mcp` SDK (`stdio_client` / `ClientSession` are async context managers — likely a dedicated long-lived task per session so exit happens in the same task that entered). Researcher should confirm the correct pattern for `mcp` 1.30.x on Windows.
- How stderr is captured on Windows (SDK `errlog` param vs temp file / pipe).
- Exact version pin for `mcp` in `requirements.txt` (installed: 1.30.0).
- Whether a concurrent Connect on the same server is rejected or serialized (a per-server `asyncio.Lock` is the likely answer).
- Tailwind markup details, Russian label wording, and whether tool rows show a count badge.

</decisions>

<specifics>
## Specific Ideas

- Demo target: `C:\Users\Aleksey\go\bin\filesystem.exe` (Go `portertech/filesystem-mcp-server`) with an allowed-directory arg → expect serverInfo `filesystem-mcp-server` and **17 tools**.
- Failure demos for criterion 3: a nonexistent command path, and a server that exits immediately (e.g. filesystem.exe pointed at a nonexistent directory).
- The raw-JSON toggle exists partly to prove in the demo video that the tool data came from the server.
- Existing UI is in Russian — new labels should be Russian too.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & requirements
- `.planning/ROADMAP.md` §Phase 7: MCP Connection (Day 16) — goal, branch `Day16`, success criteria 1–5
- `.planning/REQUIREMENTS.md` §MCP Connection (Day 16) — MCP-01..MCP-06; §Out of Scope; §Future Requirements (MCP-F1..F3 — design for reuse, don't implement)
- `.planning/PROJECT.md` §Constraints — MCP servers as stdio subprocesses via the SDK (no `multiprocessing`), native binaries allowed, no Node/npx; `user_id` scoping; vanilla JS frontend

### Codebase conventions & architecture
- `CLAUDE.md` — hard constraints, SQLModel FK cascade rule, commit/rollback pattern, structlog, `asyncio.wait_for` timeouts
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md` — layering, naming, pytest fixture patterns
- `.planning/milestones/v1.0-phases/03-personalization-day-12/03-CONTEXT.md` — precedent: user-controlled config via direct REST (not LLM tools), user-scoped table pattern

### External
- Model Context Protocol Python SDK (`mcp` 1.30.x) — `mcp.client.stdio.stdio_client`, `StdioServerParameters`, `mcp.ClientSession.initialize()` / `list_tools()`; researcher should consult current docs (no local spec file)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shared/models.py::Profile` — template for a user-scoped table with `ForeignKey("user.id", ondelete="CASCADE")` via `sa_column`.
- `agent/dependencies.py::get_current_user` — auth dependency for new REST endpoints (every MCP endpoint must be user-scoped).
- `agent/main.py::lifespan` (~line 318) — hook for closing all live MCP sessions on Agent shutdown.
- `agent/state.py` — home/pattern for per-process in-memory registries and cleanup helpers.
- `ui/static/index.html` `#settings-modal` / `#settings-form` (~line 234) and the settings handlers in `ui/static/app.js` — where the new section and its JS go.
- `shared/config.py` — add `MCP_CONNECT_TIMEOUT`.

### Established Patterns
- REST CRUD with try/except → `await session.rollback()`; `HTTPException` with specific status codes.
- Frontend: vanilla JS, Tailwind CDN, all dynamic HTML through `DOMPurify.sanitize()`; collapsible sections via `data-fold-toggle`.
- Tests: pytest-asyncio auto mode; separate test DB (`tests/conftest.py`). MCP tests can use a tiny Python stdio MCP server fixture written with the same `mcp` SDK (portable), and optionally spawn the real `filesystem.exe` when present (skip otherwise) — planner's call.

### Integration Points
- New `scripts/` directory (does not exist yet) for `mcp_list_tools.py`.
- `requirements.txt` — add a pinned `mcp` entry.
- User deletion cascade + `agent/state.py` cleanup should also tear down that user's live MCP sessions.

</code_context>

<deferred>
## Deferred Ideas

- LLM invoking MCP tools during chat turns (MCP-F1) — later Week 4 day; the persistent session registry (D-05) is designed to be reused.
- Non-stdio transports (Streamable HTTP / SSE) — MCP-F3.
- Auto-reconnect on startup / cached "last seen" tool list — rejected for this phase (D-07).
- Encrypting stored env secrets — not needed for local-first scope.

</deferred>

---

*Phase: 07-mcp-connection-day-16*
*Context gathered: 2026-09-23*
