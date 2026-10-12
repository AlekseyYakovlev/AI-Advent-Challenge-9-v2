# Phase 22: Project MCP servers (Day 31) - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning

<domain>
## Phase Boundary

The assistant can read the live state of the project's working copy through MCP, and the official GitHub server is connected read-only as the base for Pull Request work. In scope: an own Python stdio MCP server with read-only git tools (current branch, status, diff, tracked files, recent commits), confined to a configured repository root; documentation with the exact values for the existing "MCP серверы" form; documentation and a recorded check of `github-mcp-server` in read-only mode with the `repos` and `pull_requests` toolsets (PGIT-01..04, GHUB-01..02).

Out of scope here: git write tools (PGIT-05), `github-mcp-server` in write mode (GHUB-03), per-tool enable/disable in Settings (MCPS-01), the `/help` command and `Day31_report.md` (Phase 23), any change to the MCP form or to the MCP client beyond what a bug found during the phase requires.

</domain>

<decisions>
## Implementation Decisions

### Launch and repository root
- **D-01:** The server lives in a new top-level directory `mcp_servers/` (for example `mcp_servers/project_git.py`). It is a separate process, not part of `agent/` or `ui/`; later write tools go into the same place.
- **D-02:** The repository root comes from a `--root <path>` launch argument; when the argument is absent, the process working directory is used (the form's "Рабочая директория" field).
- **D-03:** If the root does not exist, or `git` is not found in PATH, the server does not start: it writes the reason to stderr and exits with an error, so the user sees a connection error with the stderr tail in Settings.
- **D-04:** If the root is not a git repository, the server also does not start, and the error text says that the folder is not a git repository and that one can be created by running `git init` in it. The server itself never runs `git init`.
- **D-05:** The server name documented for the form is `local-git`, so tools appear to the model and in tool-call cards as `mcp__local-git__<tool>`.

### Tool shape
- **D-06:** Exactly five tools, matching the phase success criteria: current branch, status, diff, tracked files, recent commits.
- **D-07:** Diff is one tool with parameters: `staged` (boolean), `ref` (optional, diff against that ref), `path` (optional, limit to a file or folder). No separate tools per mode.
- **D-08:** The tracked-files tool takes an optional `path`. Without it the whole list is returned (truncated with the marker and the total file count); with it only the files under that folder. The repository has 740 tracked files, about 35 700 characters as a list, so the unfiltered list never fits.
- **D-09:** The recent-commits tool takes `count` (default 10, maximum 50), optional `path` and optional `ref` (history of another branch or tag). One line per commit: short hash, date, author, subject.
- **D-10:** Results are git text preceded by a one-line summary written by the server ("Branch: Day31", "3 changed files", "Working tree clean", "No differences"). A result is never an empty string.

### Output limits and errors
- **D-11:** The server's own truncation limit is configurable through a `--max-chars` launch argument. The default is about 18 000 characters, deliberately below the app's `MCP_TOOL_RESULT_MAX_CHARS` (20 000), so that the server's marker always reaches the model and the app's own cut never removes it.
- **D-12:** The truncation marker is explicit and actionable: it says how much was shown out of how much and advises narrowing the call with `path`.
- **D-13:** When a diff does not fit, the model gets a per-file summary first (as `git diff --stat`: which files, how many lines changed), then the diff text up to the limit, then the marker. The model always learns the full list of touched files. This means the diff tool runs two fixed git commands; this is an accepted refinement of the PGIT-02 wording "one fixed `git` command" — both commands are fixed, shell-free and confined to the root.
- **D-14:** Each git command has a timeout of 10 seconds by default, configurable through a `--timeout` launch argument. It is below the app's `MCP_TOOL_CALL_TIMEOUT` (30 s), so the server kills a hung git and returns its own clear error first.
- **D-15:** A rejected call (path outside the root, argument starting with `-`, unknown ref, timeout) is returned as an MCP error (`isError`) whose English text names the reason and how to fix the call, for example "path must stay inside the repository root; use a relative path such as agent/ws.py".

### GitHub server
- **D-16:** The documentation tells the reader to download the ready Windows release of `github-mcp-server` from its GitHub releases page and put `github-mcp-server.exe` in `C:\Users\Aleksey\go\bin` next to `filesystem.exe`. `go install` is not documented. The release version used for the check is stated.
- **D-17:** The documented token is a fine-grained personal access token limited to the repository `AlekseyYakovlev/AI-Advent-Challenge-9-v2`, with Contents and Pull requests set to read and write (reserve for later Pull Request days) and Metadata read. In this phase the server is still started with `--read-only`; read-only behaviour rests on that flag, and the documentation says so.
- **D-18:** The user creates the token and enters it in the form. The token is never written to a repository file, a planning document, a log or a test fixture.

### Verification
- **D-19:** The recorded check for GHUB-02 is a pytest test against the real binary, modelled on `tests/test_mcp_real_filesystem.py` (skipped when the binary or the token environment variable is missing), plus the saved output of `scripts/mcp_list_tools.py` with the tool list in the phase verification document. Phase 23 carries the result into `Day31_report.md`.
- **D-20:** PGIT-04 is confirmed on the local LM Studio model through a Playwright run on the isolated copy (ports 18000/18001): the server is added through the form, the question about the branch is asked, the branch in the copy is switched, and the answer must follow. If LM Studio is not running, the check is recorded as not run, not as passed.

### Claude's Discretion
- Exact tool names and the English wording of tool and parameter descriptions.
- The format of the status output (porcelain or human) under the summary line.
- Which ref forms are accepted and how a ref is validated before it reaches git.
- Whether the truncation budget for a diff is split between the summary and the diff text, and how.
- Which document carries the form values (`docs/USER_GUIDE.md` is the expected place; VER-09 requires the three documents to describe the servers as built) and whether `README.md` links to it.
- Whether the model needs a hint about the difference between `local-git` (working copy) and `github` (remote); `agent/tool_guard.py::MULTI_STEP_TOOL_HINT` already carries a similar hint for filesystem and GitLab tools.
- The name of the environment variable that gates the real-binary GitHub test.

</decisions>

<specifics>
## Specific Ideas

- The default chat context is 65 536 tokens (`shared/models.py`, `Settings.context_length`), not 16 384; a note in `.planning/STATE.md` about 16 384 is outdated. A 20 000 character tool result is roughly a tenth of the context, so the limit is about a readable marker, not about saving context.
- The demonstration path for PGIT-04: ask "на какой ветке проект?", see the tool-call card for `mcp__local-git__…`, switch the branch, ask again, get the new branch.
- The form values to document are of this shape: name `local-git`; command `python`; args `mcp_servers/project_git.py --root <repository path>`; working directory the repository path; optional `--max-chars` and `--timeout`.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/REQUIREMENTS.md` — PGIT-01..04, GHUB-01..02, the Future Requirements (PGIT-05, GHUB-03, MCPS-01) and the Out of Scope table
- `.planning/ROADMAP.md` — Phase 22 goal and success criteria
- `.planning/PROJECT.md` — MCP server constraint (stdio subprocesses, native binaries and Node allowed) and the two v5.0 key decisions on the own git server and the official GitHub server

### MCP client as built
- `docs/ARCHITECTURE.md` — MCP client, tool namespacing, tool-call rounds
- `docs/API_SPEC.md` — MCP server config routes and the env masking rule
- `docs/USER_GUIDE.md` — the "MCP серверы" form as described for users
- `docs/TESTING_GUIDE.md` — required scenario coverage for MCP tests
- `.planning/milestones/v2.0-ROADMAP.md` — Phase 7 (MCP connection) decisions
- `.planning/phases/21-project-docs-in-rag-day-31/21-CONTEXT.md` — sibling phase of the same day; `README.md` written there may need a link to the new server

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/fixtures/mcp_stdio_server.py` — a working stdio server built on `mcp.server.fastmcp.FastMCP` from the pinned `mcp==1.30.0`; the pattern for the new server with no new dependency
- `scripts/mcp_list_tools.py` — CLI that connects to any stdio server and prints its tools; success criterion 1 and the GHUB-02 record use it
- `agent/mcp_client.py` — `open_session` validates `cwd`, spawns with `StdioServerParameters(command, args, env, cwd)`, and turns a failed start into a readable error with the stderr tail (`read_stderr_tail`, `spawn_failure_reason`)
- `agent/mcp_tools.py` — `server_slug` (slug up to 20 characters), `MCP_TOOL_PREFIX = "mcp__"`, `call_mcp_tool` (app-side truncation at `MCP_TOOL_RESULT_MAX_CHARS` with the marker `…[truncated N chars]`)
- `agent/mcp_config.py` — `ENV_MASK`, `merge_env`, `EnvMaskError`: the token masking required by GHUB-01 already exists
- `tests/test_mcp_real_filesystem.py` — real-binary test gated by `skipif`; the model for the GitHub test
- `scripts/e2e_mcp_chat_playwright.py` — isolated-copy browser script for an MCP tool call in a chat; the model for the PGIT-04 run

### Established Patterns
- `shared/config.py`: `MCP_CONNECT_TIMEOUT = 10.0`, `MCP_TOOL_CALL_TIMEOUT = 30.0`, `MCP_TOOL_RESULT_MAX_CHARS = 20000`, `MCP_AUTO_CONNECT = True`; not overridden in the local `.env`
- Enabled servers connect lazily at the first chat turn; a tool error returned with `isError` triggers the app's tool-error reminder to the model (`agent/tool_guard.py`)
- `shared/models.py::McpServerConfig` stores `name`, `command`, `args_json`, `env_json`, `cwd`, `enabled`, `auto_connect` per user; the form in `ui/static/index.html` has matching fields including env (KEY=VALUE per line) and working directory
- Subprocesses are started with asyncio only (no `multiprocessing`, no shell); structlog for logging in app code. A stdio MCP server must keep stdout clean for the protocol, so its diagnostics go to stderr.
- Browser checks run on an isolated copy at ports 18000/18001; the user's app on 8000/8001 is never stopped.

### Integration Points
- New directory `mcp_servers/` at the repository root; no change to `agent/` is expected for the server to work
- The git remote is `git@AiAdventAgentV2:AlekseyYakovlev/AI-Advent-Challenge-9-v2.git` — the repository the GitHub token is scoped to
- `github-mcp-server` and `gh` are not installed on the machine; Go is installed and `C:\Users\Aleksey\go\bin` is in PATH
- New tests sit next to `tests/test_mcp_*.py`; VER-07 (tracked in Phase 23) requires coverage of the tools, root confinement and bad arguments, written alongside this phase

</code_context>

<deferred>
## Deferred Ideas

- A "Создать репозиторий" button in Settings that runs `git init` when the configured root is not a git repository — asked for by the user during discussion; a new UI element, a new Agent route and a write operation, so it belongs with the git write tools (PGIT-05). In this phase the error text only suggests the command (D-04).
- Git write tools (stage, commit, branch, push, pull) — PGIT-05.
- `github-mcp-server` in write mode for Pull Requests — GHUB-03; the token from D-17 already has the rights.
- Per-tool enable/disable of MCP tools in Settings — MCPS-01.

</deferred>

---

*Phase: 22-project-mcp-servers-day-31*
*Context gathered: 2026-10-13*
