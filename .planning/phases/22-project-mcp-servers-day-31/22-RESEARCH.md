# Phase 22: Project MCP servers (Day 31) - Research

**Researched:** 2026-10-13
**Domain:** Python stdio MCP server (FastMCP, `mcp==1.30.0`) wrapping read-only `git` on Windows; official `github-mcp-server` binary in read-only mode
**Confidence:** HIGH (everything below tagged VERIFIED was executed on this machine in this session; open items are listed in the Assumptions Log)

## Summary

The phase needs no new Python dependency and no change to `agent/` or `ui/`. The new server is one self-contained file (`mcp_servers/project_git.py`) built on `mcp.server.fastmcp.FastMCP`, exactly like `tests/fixtures/mcp_stdio_server.py`. Raising an exception inside a tool is the way to return an MCP error: FastMCP turns it into `isError=true` with the text `Error executing tool <name>: <message>`; the app's `call_mcp_tool` passes that text to the model unchanged and sets `ok=false`. Startup failures (bad root, git missing, not a repo) are plain `sys.exit` with the reason on stderr before `server.run()`; the app then shows `PROCESS_EXITED` plus the last 20 stderr lines.

The Windows-specific traps that matter most (all reproduced here): (1) `shared/logger.py` logs JSON to **stdout**, so the server must not import `shared.*`; (2) `C:\Program Files\Git\cmd\git.exe` is a wrapper that spawns `mingw64\bin\git.exe`, so killing only the wrapper on timeout orphans the real git, and the whole process tree must be killed (psutil is already a dependency); (3) `core.quotepath` must be off or non-ASCII paths come back as octal escapes; (4) git writes `warning: ... LF will be replaced by CRLF` to **stderr** on this machine, so stderr must never be merged into the result; (5) a synchronous tool would block the event loop and the app's 2 s liveness ping, so tools must be `async` and use `asyncio.create_subprocess_exec`.

`github-mcp-server` v2.0.2 (published 2026-10-08) was downloaded to a scratch folder, checksum-verified, and driven through the project's own `scripts/mcp_list_tools.py`/MCP SDK. With `stdio --read-only --toolsets=repos,pull_requests` it lists exactly 16 read-only tools. Environment variables `GITHUB_TOOLSETS` and `GITHUB_READ_ONLY` work as equivalents. The handshake and tool listing succeed even with an invalid token (verified), so the GHUB-02 test must additionally call a read tool to prove the token works.

**Primary recommendation:** Build `mcp_servers/project_git.py` as a stdlib + `mcp` (+ optional `psutil`) single file with five async `git_*` tools, one shared `run_git()` that kills the process tree on timeout, a single `resolve_inside_root()` path gate, a `rev-parse --verify --end-of-options` ref gate, and a total-result budget of `--max-chars` (18 000) that includes header and marker; document `local-git` and `github` form values in a new "MCP серверы" section of `docs/USER_GUIDE.md`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Launch and repository root**
- **D-01:** The server lives in a new top-level directory `mcp_servers/` (for example `mcp_servers/project_git.py`). It is a separate process, not part of `agent/` or `ui/`; later write tools go into the same place.
- **D-02:** The repository root comes from a `--root <path>` launch argument; when the argument is absent, the process working directory is used (the form's "Рабочая директория" field).
- **D-03:** If the root does not exist, or `git` is not found in PATH, the server does not start: it writes the reason to stderr and exits with an error, so the user sees a connection error with the stderr tail in Settings.
- **D-04:** If the root is not a git repository, the server also does not start, and the error text says that the folder is not a git repository and that one can be created by running `git init` in it. The server itself never runs `git init`.
- **D-05:** The server name documented for the form is `local-git`, so tools appear to the model and in tool-call cards as `mcp__local-git__<tool>`.

**Tool shape**
- **D-06:** Exactly five tools, matching the phase success criteria: current branch, status, diff, tracked files, recent commits.
- **D-07:** Diff is one tool with parameters: `staged` (boolean), `ref` (optional, diff against that ref), `path` (optional, limit to a file or folder). No separate tools per mode.
- **D-08:** The tracked-files tool takes an optional `path`. Without it the whole list is returned (truncated with the marker and the total file count); with it only the files under that folder. The repository has 740 tracked files, about 35 700 characters as a list, so the unfiltered list never fits.
- **D-09:** The recent-commits tool takes `count` (default 10, maximum 50), optional `path` and optional `ref` (history of another branch or tag). One line per commit: short hash, date, author, subject.
- **D-10:** Results are git text preceded by a one-line summary written by the server ("Branch: Day31", "3 changed files", "Working tree clean", "No differences"). A result is never an empty string.

**Output limits and errors**
- **D-11:** The server's own truncation limit is configurable through a `--max-chars` launch argument. The default is about 18 000 characters, deliberately below the app's `MCP_TOOL_RESULT_MAX_CHARS` (20 000), so that the server's marker always reaches the model and the app's own cut never removes it.
- **D-12:** The truncation marker is explicit and actionable: it says how much was shown out of how much and advises narrowing the call with `path`.
- **D-13:** When a diff does not fit, the model gets a per-file summary first (as `git diff --stat`: which files, how many lines changed), then the diff text up to the limit, then the marker. The model always learns the full list of touched files. This means the diff tool runs two fixed git commands; this is an accepted refinement of the PGIT-02 wording "one fixed `git` command" — both commands are fixed, shell-free and confined to the root.
- **D-14:** Each git command has a timeout of 10 seconds by default, configurable through a `--timeout` launch argument. It is below the app's `MCP_TOOL_CALL_TIMEOUT` (30 s), so the server kills a hung git and returns its own clear error first.
- **D-15:** A rejected call (path outside the root, argument starting with `-`, unknown ref, timeout) is returned as an MCP error (`isError`) whose English text names the reason and how to fix the call, for example "path must stay inside the repository root; use a relative path such as agent/ws.py".

**GitHub server**
- **D-16:** The documentation tells the reader to download the ready Windows release of `github-mcp-server` from its GitHub releases page and put `github-mcp-server.exe` in `C:\Users\Aleksey\go\bin` next to `filesystem.exe`. `go install` is not documented. The release version used for the check is stated.
- **D-17:** The documented token is a fine-grained personal access token limited to the repository `AlekseyYakovlev/AI-Advent-Challenge-9-v2`, with Contents and Pull requests set to read and write (reserve for later Pull Request days) and Metadata read. In this phase the server is still started with `--read-only`; read-only behaviour rests on that flag, and the documentation says so.
- **D-18:** The user creates the token and enters it in the form. The token is never written to a repository file, a planning document, a log or a test fixture.

**Verification**
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

### Deferred Ideas (OUT OF SCOPE)
- A "Создать репозиторий" button in Settings that runs `git init` when the configured root is not a git repository (belongs with PGIT-05). In this phase the error text only suggests the command (D-04).
- Git write tools (stage, commit, branch, push, pull) — PGIT-05.
- `github-mcp-server` in write mode for Pull Requests — GHUB-03.
- Per-tool enable/disable of MCP tools in Settings — MCPS-01.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PGIT-01 | Python stdio MCP server on the pinned `mcp` SDK, read-only tools: branch, status, diff, tracked files, recent commits | FastMCP pattern verified (Standard Stack, Pattern 1); exact command lines (Pattern 3) |
| PGIT-02 | Fixed git command lines, no shell, confined to root, argument starting with `-` rejected, long output truncated with marker | Path gate and ref gate (Patterns 4, 5), truncation budget (Pattern 6), process runner (Pattern 2) |
| PGIT-03 | User adds the server through the existing form; tools callable in a chat; documentation gives exact form values | Form values table, slug check (`local-git` survives), docs targets |
| PGIT-04 | Chat asks which branch, gets the branch checked out at that moment, tool call visible | E2E design on isolated copy with a temp repo (Test Plan) |
| GHUB-01 | Documentation for `github-mcp-server` read-only with `repos` and `pull_requests`; token masked after saving | Verified release, command line, tool list, env masking (all values masked: UI gets `env_keys` only) |
| GHUB-02 | App connects to `github-mcp-server` read-only and lists its tools | Verified 16-tool list; real-binary test design incl. a read call |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Reading live git state of the working copy | MCP server subprocess (`mcp_servers/project_git.py`) | — | Own process, spawned by the Agent over stdio; keeps git access outside `agent/` |
| Root confinement, argument validation, truncation | MCP server subprocess | — | Security boundary must live next to the git calls, not in the app |
| Tool discovery, namespacing, dispatch, app-side cap and timeout | API / Backend (`agent/mcp_client.py`, `agent/mcp_tools.py`) | — | Already built; unchanged |
| Token storage and masking | API / Backend (`agent/mcp_config.py`) + Database | Browser (form shows `KEY=•••`) | Env is stored per user; API returns `env_keys` only |
| Server registration form | Browser (`ui/static/index.html` "MCP серверы") | API | Existing; unchanged |
| GitHub remote access | External binary `github-mcp-server.exe` (stdio) | GitHub REST API | Official server; app only spawns it |
| Form values and setup instructions | Documentation (`docs/USER_GUIDE.md`) | `docs/ARCHITECTURE.md`, `docs/API_SPEC.md` | VER-09 |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `mcp` | 1.30.0 (pinned in `requirements.txt`, installed 1.30.0) [VERIFIED: pip list] | `FastMCP` server over stdio | Same SDK as the client; pattern already in `tests/fixtures/mcp_stdio_server.py` |
| Python stdlib (`asyncio`, `argparse`, `os`, `shutil`, `sys`, `re`) | Python 3.13.15 locally; project minimum 3.11 | Process runner, arg parsing, path gate | No new dependency |
| `psutil` | `>=6.0.0` already in `requirements.txt` [VERIFIED: requirements.txt] | Kill the git process tree on timeout | Needed because `cmd\git.exe` spawns a child (see Pitfall 2) |
| `git` | 2.39.1.windows.1 locally [VERIFIED: `git --version`] | The actual data source | Needs >= 2.24 for `--end-of-options` [CITED: git release notes 2.24, ASSUMED version boundary, see A2] |

### Supporting
| Item | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| `github-mcp-server.exe` | v2.0.2, commit `eb47a99d`, released 2026-10-08, asset `github-mcp-server_Windows_x86_64.zip` (9 428 417 bytes, sha256 `4ae64087501650752f21c9d5af5378107a5116d94ee1cf37786b22b8c4bd99df`) [VERIFIED: GitHub releases API + local sha256sum, 2026-10-13] | Official GitHub MCP server | GHUB-01/02; not installed on this machine yet (D-16: user puts exe into `C:\Users\Aleksey\go\bin`) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Own git server | `mcp-server-git` (PyPI) | Rejected by milestone decision (own server, no new dependency) |
| `asyncio.create_subprocess_exec` | sync `subprocess.run(timeout=)` | Sync tools run on the event loop in FastMCP and would block pings up to 10 s; app's `LIVENESS_TIMEOUT` is 2 s |
| psutil tree-kill | `taskkill /T /F` | psutil is already installed and portable; no extra process |

**Installation:** none. `pip install -r requirements.txt` already provides `mcp==1.30.0` and `psutil`.

## Package Legitimacy Audit

No new Python or npm packages are introduced. `mcp` and `psutil` are already pinned in `requirements.txt`. The only external artifact is the official `github-mcp-server` Windows binary from `github.com/github/github-mcp-server` releases (GitHub organisation, tag v2.0.2; asset digest published by the GitHub API matched the local `sha256sum`). slopcheck is not applicable (no registry packages); the planner needs no `checkpoint:human-verify` for packages, but the documentation should tell the reader to compare the zip's SHA-256 with `github-mcp-server_2.0.2_checksums.txt` from the same release.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
User (Settings form: name=local-git, command, args, cwd)
        |  POST /api/v1/mcp/servers  (stored per user, env values never returned)
        v
Agent process (:8001)  agent/mcp_client.py::open_session
        |  spawn: python mcp_servers/project_git.py --root <repo>   (stdio, no shell)
        v
+---------------------- project_git.py (own process) -----------------------+
| startup: argparse -> root exists? -> git on PATH? -> is git work tree?     |
|          any failure -> stderr reason, exit 1 (before server.run())        |
| FastMCP stdio loop                                                         |
|   tools/call  --> validate args --+--> path gate (resolve_inside_root)     |
|   (async)                         +--> ref gate (git rev-parse --verify)   |
|                                   +--> reject leading "-"                  |
|              --> run_git(argv)  (create_subprocess_exec, timeout,          |
|                                  kill process tree, stdout only)           |
|              --> summary line + git text, budget = --max-chars             |
|              --> text result, or raise -> isError "Error executing tool …" |
+----------------------------------------------------------------------------+
        |  CallToolResult text
        v
agent/mcp_tools.py::call_mcp_tool  (cap 20 000, timeout 30 s)  -> role=tool message -> LLM
        |
        v
UI tool-call card  mcp__local-git__git_current_branch

Second server, same path:  github-mcp-server.exe stdio --read-only --toolsets=repos,pull_requests
        env GITHUB_PERSONAL_ACCESS_TOKEN=<from form, masked>  -> api.github.com
```

### Recommended Project Structure
```
mcp_servers/
├── __init__.py          # empty, so tests can `from mcp_servers import project_git`
└── project_git.py       # the server; stdlib + mcp + psutil only, never imports shared/ or agent/
tests/
├── test_mcp_project_git.py          # unit + stdio tests with a temp repo
├── test_mcp_real_github.py          # real-binary test, skipif exe/token missing
scripts/
└── e2e_mcp_git_playwright.py        # PGIT-04 on the isolated copy (model: e2e_kb_playwright.py)
docs/USER_GUIDE.md                   # new section "MCP серверы"
```

### Pattern 1: FastMCP tool, error by raising
**What:** Declare tools with `@server.tool(structured_output=False)`, make them `async`, and raise an exception to produce `isError`.
**Verified behaviour** [VERIFIED: local run against mcp 1.30.0 via `stdio_client`/`ClientSession`]:
- raise `ValueError("msg")` -> `isError=True`, text `Error executing tool boom: msg`, `structuredContent=None`.
- a wrong argument type -> `isError=True`, text starts with `Error executing tool <name>: 1 validation error ...`.
- an unknown tool -> `isError=True`, text `Unknown tool: nope`.
- a plain `-> str` tool with default structured output returns text content **and** `structuredContent={"result": ...}`. `agent/mcp_tools.py::_flatten_content` prefers text, so both work, but `structured_output=False` avoids a duplicate payload and an `outputSchema` in the listing.
- `FastMCP(..., log_level="WARNING")` suppresses the SDK's per-request INFO lines on stderr (default `INFO` prints `Processing request of type ...` for every call, which would fill the 20-line stderr tail shown on failures).
**Example:**
```python
# Source: mcp 1.30.0 mcp/server/fastmcp/server.py; pattern from tests/fixtures/mcp_stdio_server.py
from mcp.server.fastmcp import FastMCP

server = FastMCP("local-git", log_level="WARNING")

@server.tool(structured_output=False)
async def git_current_branch() -> str:
    """Name of the branch checked out in the LOCAL working copy (not the GitHub remote)."""
    ...
    raise ToolInputError("path must stay inside the repository root; use a relative path such as agent/ws.py")
```
`ToolInputError(ValueError)` is a local class; any exception works, `ToolError` from `mcp.server.fastmcp.exceptions` gets the same wrapping (it is wrapped a second time by `Tool.run`, so prefer a plain `ValueError` subclass).

**App side** [VERIFIED: agent/mcp_tools.py]: `call_mcp_tool` flattens `content` text, truncates at `settings.MCP_TOOL_RESULT_MAX_CHARS` (marker `\n…[truncated N chars]`), and returns `ok = not result.isError`. A timeout or `McpError` becomes `_error(...)`. `agent/tool_guard.py` adds `TOOL_ERROR_REMINDER` for error results, so the model is told to fix the arguments and retry. No app change is needed.

### Pattern 2: One process runner for all git calls
**What:** `async def run_git(args: list[str], *, cwd: str, timeout: float) -> GitResult`; no shell, stdin closed, stdout and stderr captured **separately**, process tree killed on timeout.
**Rules (each verified or reproduced):**
- Use the absolute path from `shutil.which("git")` resolved once at startup (D-03).
- `stdin=asyncio.subprocess.DEVNULL` (a git command that waits on stdin would otherwise hang until the timeout).
- Environment: copy `os.environ`, **delete every key starting with `GIT_`**, then set `GIT_OPTIONAL_LOCKS=0` (status must not write `index.lock` or refresh the index while the user works), `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `LC_ALL=C` (stable English messages for the not-a-repo check). Stripping inherited `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` matters when the app was started from a git hook or a tool that exports them: they would silently redirect every command away from `--root`.
- Global options before the subcommand: `--no-pager --literal-pathspecs -c core.quotepath=off -c core.fsmonitor=false -c color.ui=false`. `--literal-pathspecs` was verified to turn `*.txt` and `:(top)x` into literal names (without it `git diff -- '*.txt'` expanded the glob). `core.fsmonitor=false` prevents starting a filesystem-monitor daemon or hook from user config. For `diff` also pass `--no-ext-diff --no-textconv` so repository config cannot execute external programs.
- Decode `stdout` as UTF-8 with `errors="replace"`; normalise `\r\n` to `\n` in diff/patch text (token economy; CRLF files otherwise show `^M` noise).
- On `asyncio.TimeoutError`: kill the **tree**, then `await proc.wait()`, then raise a clear `ToolInputError`-like error ("git diff timed out after 10s; narrow the call with path"). Kill helper:
```python
# psutil is already a project dependency
def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            with contextlib.suppress(psutil.Error):
                child.kill()
        parent.kill()
    except psutil.Error:
        pass
```
  Collect the child list **before** killing the parent (children are re-parented afterwards).
- Bound memory: read stdout in chunks up to a hard cap (recommended 4 MiB = `_HARD_CAP_BYTES`); if exceeded, kill the process and report "more than N characters" in the marker instead of an exact total. `communicate()` without a cap would load an entire huge diff into memory.
- `asyncio.create_subprocess_exec` works under FastMCP/anyio on Windows (Proactor loop is the default) [VERIFIED: prototype spawning a subprocess from an async FastMCP tool, killed on `wait_for` timeout].

### Pattern 3: Exact fixed command lines
`BASE = [GIT, "--no-pager", "--literal-pathspecs", "-c", "core.quotepath=off", "-c", "core.fsmonitor=false", "-c", "color.ui=false"]`, `cwd=ROOT`. `<hash>` is the 40-hex output of the ref gate (never the raw user text). `<rel>` is the normalised relative path from the path gate.

| Tool (recommended name) | Command(s) | Notes |
|-------------------------|-----------|-------|
| `git_current_branch` | `symbolic-ref --short -q HEAD`; on exit 1 (detached): `rev-parse --short HEAD` | Verified: detached HEAD makes `branch --show-current` print an empty line with exit 0, while `symbolic-ref -q` exits 1; an unborn repo (no commits) still returns the branch name via `symbolic-ref`. Result: `Branch: Day31` or `Detached HEAD at ada5505`; add ` (no commits yet)` when `rev-parse --verify -q HEAD` fails. |
| `git_status` | `status --porcelain=v1 --branch --untracked-files=normal` | Raw lines under the summary; first line `## branch...upstream [ahead N]`. Summary built by the server: counts of lines not starting with `##`, `N changed files` or `Working tree clean`. Put a one-line legend in the tool description (`XY path`: X index, Y worktree, `??` untracked). Porcelain v1 is stable and short; `--short` is the same format. |
| `git_diff` | patch: `diff --no-color --no-ext-diff --no-textconv [--cached] [<hash>] [-- <rel>]`; when it does not fit: stat `diff --stat=120,80 --no-ext-diff --no-textconv` with the same selectors | Flags: `staged` -> `--cached`; `ref` -> resolved hash (working tree vs commit, or index vs commit when staged). Document in the description that "all uncommitted changes" is `ref="HEAD"`, that untracked files appear only in status. `path` always after `--`. |
| `git_tracked_files` | `ls-files -- <rel>` (or no pathspec) | With `quotepath=off` Cyrillic names come back verbatim (verified). Count lines for the total. |
| `git_recent_commits` | `log -n <count> --no-color --no-show-signature --date=short --format=%h%x09%ad%x09%an%x09%s [<hash>] [-- <rel>]` | `count` default 10, clamp to 1..50 (state the clamp in the summary line). Unborn repo: `log` fails with "does not have any commits yet" (verified, exit 128): return `No commits yet` rather than an error. Ref uses the same gate. |

Optional, recommended: let `ref` for `git_diff` and `git_recent_commits` also accept `A..B` / `A...B` by splitting on `...` first then `..`, validating each side with the ref gate and passing `<hashA>...<hashB>`. This is the form a later Pull Request day needs (`main...HEAD`). It is optional for this phase; if omitted, the tool description must say that ranges are not supported.

### Pattern 4: Path gate (PGIT-02/03)
One function used by every tool that takes `path`. Verified matrix on this Windows machine (root `C:\Projects\AiAdventAgentV2`):
```python
def resolve_inside_root(root_real: str, raw: str) -> str:
    """Return a repo-relative POSIX path, or raise ToolInputError."""
    if raw.startswith("-"):                       # PGIT-02, checked first
        raise ToolInputError('path must not start with "-"; use a path such as agent/ws.py')
    if "\x00" in raw or "\n" in raw:
        raise ToolInputError("path contains control characters")
    drive, _tail = os.path.splitdrive(raw)
    if (drive and not os.path.isabs(raw)) or ":" in raw[len(drive):]:
        raise ToolInputError("drive-relative paths and ':' are not allowed; use a relative path")
    try:
        full = os.path.realpath(os.path.join(root_real, raw))   # joins; absolute raw replaces root
        common = os.path.commonpath([os.path.normcase(root_real), os.path.normcase(full)])
        if common != os.path.normcase(root_real):
            raise ToolInputError("path must stay inside the repository root; use a relative path such as agent/ws.py")
        rel = os.path.relpath(full, root_real)
    except (ValueError, OSError):                 # other drive, device names such as NUL, bad path
        raise ToolInputError("path must stay inside the repository root; use a relative path such as agent/ws.py")
    return "." if rel == "." else rel.replace(os.sep, "/")
```
Results of the prototype [VERIFIED: local run]: `agent/ws.py`, `agent\ws.py`, `AGENT/WS.PY` (case-insensitive) and an absolute path inside the root all normalise to `agent/ws.py`; `../x`, `..\..\Windows`, `C:\Windows`, `D:foo`, `\\server\share\x`, `//server/share/x`, `\Windows` are rejected (`outside root`); `agent/../shared` becomes `shared`; a path to a deleted file (`nonexistent/deleted.py`) is accepted (needed for diffs of removed files); `agent/ws.py.` (trailing dot) normalises to `agent/ws.py`. Two prototype defects found and fixed in the snippet above: `C:foo` (drive-relative) was accepted as `foo`, and the device name `NUL` raised `ValueError` from `relpath` (hence the wrapped `try`). Pass the **normalised** `rel`, never the raw string, to git. `realpath` also resolves symlinks and junctions, so a tracked link that points outside the root is rejected. Always put `--` before the path; `--literal-pathspecs` disables glob/magic. Compute `root_real = os.path.realpath(root)` once at startup.

### Pattern 5: Ref gate (Claude's discretion: recommendation)
- Reject before git: empty, starting with `-`, longer than 255, containing whitespace, control characters, a backslash.
- Resolve with one fixed command: `rev-parse --verify --quiet --end-of-options "<ref>^{commit}"` (cwd=ROOT). Exit 0 gives a 40-hex hash that all later commands use; non-zero -> `ToolInputError("unknown ref '<ref>'; use a branch, tag, short commit hash or HEAD~N")`. Verified locally: `HEAD~2`, `Day31`, `origin/main`, `HEAD:agent/main.py` (a blob, peels with `^{commit}` to an error, so rejected), `-x` (rejected by `--end-of-options`/"Needed a single revision").
- `git check-ref-format --branch` is NOT suitable as the gate: it validates names of refs to create, rejects forms like `HEAD~2` and `origin/main^`, and does not prove the ref exists.
- Accepted forms therefore: branch, tag, remote-tracking branch, full or short hash, `HEAD~N`, `HEAD^`, `@{-1}`. Not accepted: blobs/trees (`HEAD:path`), ranges unless the optional range support above is built.

### Pattern 6: Truncation budget (D-11..D-13)
- Invariant to test: `len(result_text) <= max_chars` for every tool, **including** summary line and marker. With 18 000 vs the app's 20 000 the app never cuts and `truncated=false`; the `…[truncated N chars]` suffix can only appear if the operator raises `--max-chars` above `MCP_TOOL_RESULT_MAX_CHARS` (log a stderr warning at startup if `--max-chars > 20000`; validate `--max-chars >= 1000`, `--timeout > 0`).
- Marker (about 150 chars, English, actionable): `[output truncated: showing 16,812 of 52,310 characters. Call again with a narrower "path" (a file or folder) to see the rest.]`. When the hard read cap was hit use `more than` instead of the exact total.
- Cut on a line boundary (last `\n` at or before the limit) so a hunk header is not split mid-line.
- Diff: run the patch command first. If `header + patch` fits, return it without a stat. If not, run `diff --stat=120,80`, keep the stat's last line (`N files changed, X insertions(+), Y deletions(-)`), cap the stat block at `min(len(stat), max_chars // 4)` (about 4 500 characters; if cut, replace the dropped file lines with `... N more files not shown`), then give the patch whatever remains after header, stat, a blank separator and the marker. Order in the result: summary line, stat, patch text, marker.
- Tracked files: if the list does not fit, prepend a per-top-level-directory count table (cheap, helps the model choose a `path`), then the first lines that fit, then the marker with `showing N of M files`. Summary line always carries the total (`752 tracked files` here; CONTEXT says 740).
- Status/log are bounded by `count <= 50` and by the same truncation helper; a status with thousands of untracked entries is cut by it.

### Pattern 7: Startup validation order (D-02..D-04)
`argparse(allow_abbrev=False)` with `--root` (default `os.getcwd()`), `--max-chars` (default 18000), `--timeout` (default 10.0) -> root `is_dir` -> `shutil.which("git")` -> `git rev-parse --is-inside-work-tree` with the same env (stdout `true`; exit 128 with "not a git repository" -> the D-04 message `"<root> is not a git repository. Run 'git init' in that folder to create one."`; other failures such as `detected dubious ownership` -> print git's own stderr line; stdout `false` = bare repo -> "has no working tree") -> `server.run(transport="stdio")`. Each failure: `sys.stderr.write(reason + "\n"); sys.exit(1)`; the reason line must be the **last** thing written, because the app shows only the last 20 stderr lines. argparse errors exit 2 with usage on stderr, which is acceptable. Verified on this machine: a server that exits at startup yields `ERROR PROCESS_EXITED: Процесс сервера неожиданно завершился.` plus the stderr tail in `scripts/mcp_list_tools.py` (same path as the Settings form).
- A root that is a subdirectory of a repository is accepted; git runs with `cwd=root`, pathspecs are relative to it and `status` porcelain paths remain repository-root relative (note in docs; confinement boundary is `--root`).

### Anti-Patterns to Avoid
- **Importing `shared.logger` / `shared.config` in the server:** `setup_logging()` writes JSON logs to `sys.stdout` [VERIFIED: shared/logger.py], which would corrupt the JSON-RPC stream; `shared.config` also loads `.env` and, when launched as `python mcp_servers/project_git.py`, `sys.path[0]` is `mcp_servers/` so `shared` is not importable anyway. Use `sys.stderr.write`/stdlib `logging` to stderr. This is a deliberate, documented exception to the "structlog, never print" convention (CLAUDE.md).
- **`print()` anywhere in the server** (stdout is the protocol).
- **Sync tools with blocking `subprocess.run`:** blocks the event loop and ping replies.
- **`shell=True`, or building one command string.**
- **Passing raw user text to git** (always the normalised path after `--` and a resolved hash).
- **Merging stderr into stdout:** git prints `warning: ... LF will be replaced by CRLF` to stderr on this machine [VERIFIED].
- **Using `go install` or the repo `.exe` in docs** (D-16 says use the release zip).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MCP protocol, tool schemas, error wrapping | JSON-RPC loop | `mcp.server.fastmcp.FastMCP` | Already pinned; verified error semantics |
| Process-tree kill | `taskkill` shelling | `psutil.Process.children(recursive=True)` | Already a dependency; no shell |
| Ref validation | regex for ref syntax | `git rev-parse --verify --end-of-options` | Git itself defines the revision grammar |
| Pathspec escaping | custom quoting | `--literal-pathspecs` + normalised relative path after `--` | Disables glob and magic in git |
| Commit/date formatting | parsing `git log` output | `--format=%h%x09%ad%x09%an%x09%s --date=short` | Stable, one line per commit |
| GitHub access | own GitHub client | `github-mcp-server` (official) | GHUB scope is connecting it |
| Env masking of the token | new masking code | existing `agent/mcp_config.py` (UI only ever receives `env_keys`) | Already built and tested |

**Key insight:** every safety property here comes from composing existing guarantees (git's own parser, `--` and `--literal-pathspecs`, OS path normalisation, FastMCP error wrapping), not from writing validators of git syntax.

## Common Pitfalls

### Pitfall 1: stdout pollution
**What goes wrong:** the connection dies with a protocol error right after start.
**Why:** `shared.logger` and any `print()` go to stdout.
**How to avoid:** stdlib-only imports plus `mcp`/`psutil`; add a test that runs the server and asserts a clean handshake plus a test that `grep`s the module for `print(` and `shared`.
**Warning signs:** `PROTOCOL_ERROR` in `scripts/mcp_list_tools.py`.

### Pitfall 2: Orphaned git after timeout (Windows wrapper)
**What goes wrong:** the timeout fires, `proc.kill()` returns, but a `mingw64\bin\git.exe` keeps running (and may hold `index.lock` if not for `GIT_OPTIONAL_LOCKS=0`).
**Why:** `C:\Program Files\Git\cmd\git.exe` is a launcher that spawns the real git. Reproduced: after `Popen([cmd\git.exe, "hash-object", "--stdin"]).kill()` the child (pid listed by psutil) was still alive. `shutil.which("git")` in this shell found `mingw64\bin\git.exe` first, but the Agent's Windows PATH can resolve the `cmd` wrapper.
**How to avoid:** kill the tree (Pattern 2); test with a Python child that itself spawns a sleeping grandchild.
**Warning signs:** stray `git.exe` in Task Manager after tests.

### Pitfall 3: Blocking the event loop
**What goes wrong:** the app's `LIVENESS_TIMEOUT` (2 s) ping times out while a diff runs; the server is classified unresponsive.
**How to avoid:** all tools `async`, all waits via asyncio.

### Pitfall 4: Octal-escaped non-ASCII paths
**What goes wrong:** `git status`/`ls-files` print `"\320\277..."` for Cyrillic names [VERIFIED]; the model cannot use them as `path`.
**How to avoid:** `-c core.quotepath=off` on every call; test with a `папка/файл.txt` fixture.

### Pitfall 5: Relative script path in the form
**What goes wrong:** args `mcp_servers/project_git.py` resolve against the working directory. That is the repository itself for this project, but breaks if the user points the working directory at another repo.
**How to avoid:** the documentation gives the absolute script path as the safe form (`C:\Projects\AiAdventAgentV2\mcp_servers\project_git.py`) and `--root` as a single line `--root=C:\Path\To\Repo`. The form takes **one argument per line**, so `--root` and its value on separate lines also works, but `--root=...` is harder to mistype. `command` `python` resolves through the Agent's PATH (`shutil.which`; SDK also tries `.cmd/.bat/.exe`). If the Agent is ever run from a venv, use the interpreter's absolute path.

### Pitfall 6: Handshake succeeds with a bad GitHub token
**What goes wrong:** GHUB-02 "passes" with an expired token.
**Why:** with `ghp_dummy` the server logs `failed to fetch token scopes ... invalid or expired token` on stderr but still serves the 16 tools [VERIFIED]; `list_branches` then returns `isError` with `401 Bad credentials`.
**How to avoid:** the real-binary test lists tools **and** calls one read tool (`list_branches` for `AlekseyYakovlev/AI-Advent-Challenge-9-v2`) and asserts `ok`.

### Pitfall 7: Environment of spawned servers is minimal
**What goes wrong:** variables set in the shell or the test process do not reach the server.
**Why:** the SDK passes only a whitelist (on Windows: `APPDATA, HOMEDRIVE, HOMEPATH, LOCALAPPDATA, PATH, PATHEXT, PROCESSOR_ARCHITECTURE, SYSTEMDRIVE, SYSTEMROOT, TEMP, USERNAME, USERPROFILE`) plus the form's env [VERIFIED: mcp/client/stdio/__init__.py]. `scripts/mcp_list_tools.py` passes **no** env at all, so a token exported in the shell is NOT visible to the binary there (my first env experiment was invalid for this reason).
**How to avoid:** for the saved GHUB-02 output, either use the REST route (`POST /api/v1/mcp/servers/{id}/connect`, uses the stored env) or run a tiny Python wrapper that passes `env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ[...]}` to `connect_once_and_list(command, args, env=...)`. Tool **listing** does not need a valid token, so `python scripts/mcp_list_tools.py <exe> stdio --read-only --toolsets=repos,pull_requests` is enough for the recorded tool list and never touches a secret.

### Pitfall 8: git "dubious ownership" / safe.directory
`git rev-parse --is-inside-work-tree` exits 128 when the repo owner differs from the current user. Do not add `-c safe.directory=*`; show git's own stderr line as the startup reason.

### Pitfall 9: Switching branches in the real repository during the E2E
The PGIT-04 script switches branches. Never do this in `C:\Projects\AiAdventAgentV2` (dirty tree, user's branch `Day31`). Create a temp git repo (two branches) inside the scratch directory and point `--root` there; `copytree` of the app is only for the app code.

### Pitfall 10: Existing e2e models use stub LLM and ports 8000/8001
`scripts/e2e_mcp_chat_playwright.py` has `UI_PORT = 8000`, a stub LLM and `preflight()` that blocks when 8000/8001 are busy, so it is the model for the tool-card assertions only. For PGIT-04 use `scripts/e2e_kb_playwright.py` as the skeleton (copy the tree, patch `const AGENT_PORT = 8001;` in `ui/static/app.js` and `login.html`, ports 18000/18001, real LM Studio, preflight that returns `EXIT_BLOCKED` when LM Studio is down). LM Studio is running now with `qwen/qwen3.5-9b` loaded [VERIFIED: GET /api/v0/models], so the run is possible.

## Runtime State Inventory

Not a rename/refactor/migration phase. None -- verified: the phase adds files and documentation only; no stored string, service config, OS registration, secret name or build artifact is renamed. (Form rows created by the user live in `McpServerConfig`; the phase adds none to the repository.)

## Form values to document (PGIT-03, GHUB-01)

`server_slug("local-git", id)` -> `_sanitize` keeps `[A-Za-z0-9_-]`, lowercases, slug `local-git` (9 chars, limit 20) unchanged [VERIFIED: agent/mcp_tools.py]. Exposed tool names `mcp__local-git__<tool>` are 16 characters of prefix plus the tool name, well under 64. A second server named `github` becomes `mcp__github__<tool>`; the longest listed github tool (`list_repository_collaborators`, 29) fits.

**local-git** (settings form "MCP серверы"; args are one per line):

| Field | Value |
|-------|-------|
| Название | `local-git` |
| Команда | `python` |
| Аргументы | line 1 `C:\Projects\AiAdventAgentV2\mcp_servers\project_git.py`; line 2 `--root=C:\Projects\AiAdventAgentV2`; optional lines `--max-chars=18000`, `--timeout=10` |
| Переменные окружения | empty |
| Рабочая директория | `C:\Projects\AiAdventAgentV2` (also the fallback root when `--root` is omitted) |

**github** (D-16..D-18):

| Field | Value |
|-------|-------|
| Название | `github` |
| Команда | `C:\Users\Aleksey\go\bin\github-mcp-server.exe` (absolute path; matches how `filesystem.exe` is used by the existing test) |
| Аргументы | `stdio`, `--read-only`, `--toolsets=repos,pull_requests` (three lines) |
| Переменные окружения | `GITHUB_PERSONAL_ACCESS_TOKEN=<token created by the user>` |
| Рабочая директория | empty |

Verified equivalences with the SDK client passing explicit env [VERIFIED: local run, v2.0.2]: `stdio --read-only --toolsets=repos,pull_requests` -> 16 tools; `--toolsets repos,pull_requests` (two args) -> 16; env `GITHUB_TOOLSETS=repos,pull_requests` + `GITHUB_READ_ONLY=1` with plain `stdio` -> 16; flag `--read-only` + env toolsets -> 16; env toolsets only (no read-only) -> 29; read-only only (default toolsets) -> 27; no options -> 46. Documentation should show the flags (D-17: read-only rests on the `--read-only` flag) and mention the env alternative only as a note.

**Read-only tool list, v2.0.2 with `repos,pull_requests`** (16, saved for the verification document): `get_commit`, `get_file_contents`, `get_latest_release`, `get_release_by_tag`, `get_tag`, `list_branches`, `list_commits`, `list_pull_requests`, `list_releases`, `list_repository_collaborators`, `list_tags`, `pull_request_read`, `search_code`, `search_commits`, `search_pull_requests`, `search_repositories`. The README's toolset section is partial for `repos`; this list is the observed truth. The 13 write tools hidden by `--read-only` include `create_pull_request`, `merge_pull_request`, `update_pull_request`, `create_branch`, `create_or_update_file`, `push_files`-style tools. Server banner: `github-mcp-server`, version `2.0.2`, MCP protocol `2025-11-25`.

**Install steps for the docs:** download `github-mcp-server_Windows_x86_64.zip` from `https://github.com/github/github-mcp-server/releases/tag/v2.0.2`; compare SHA-256 with `github-mcp-server_2.0.2_checksums.txt`; the zip contains `LICENSE`, `README.md`, `github-mcp-server.exe` (30 MB) [VERIFIED: extracted in an empty scratch folder]; copy the exe to `C:\Users\Aleksey\go\bin`. Token: GitHub -> Settings -> Developer settings -> Fine-grained tokens, repository access "Only select repositories" = `AlekseyYakovlev/AI-Advent-Challenge-9-v2`, permissions Metadata read (automatic), Contents read and write, Pull requests read and write (D-17). Read-only tools need only the read parts [ASSUMED, see A1]; `search_code`/`search_repositories` on a fine-grained token only see the selected repository. After saving, the API returns only `env_keys`, the form shows `GITHUB_PERSONAL_ACCESS_TOKEN=•••` (`ui/static/app.js:2376`), and `merge_env` keeps the stored value when the mask is resubmitted [VERIFIED: agent/mcp_config.py].

## Code Examples

### Tool with budgeted output (shape, not full code)
```python
# Source: pattern derived from tests/fixtures/mcp_stdio_server.py + verified FastMCP semantics
@server.tool(structured_output=False)
async def git_diff(staged: bool = False, ref: str | None = None, path: str | None = None) -> str:
    """Show uncommitted changes of the LOCAL working copy as a unified diff ... (English description,
    states: not the GitHub remote; HEAD for all uncommitted changes; path narrows; long output is truncated)."""
    rel = resolve_inside_root(ROOT_REAL, path) if path else None
    commit = await resolve_ref(ref) if ref else None
    selectors = (["--cached"] if staged else []) + ([commit] if commit else []) + (["--", rel] if rel else [])
    patch = (await run_git(["diff", "--no-color", "--no-ext-diff", "--no-textconv", *selectors])).text
    if not patch.strip():
        return "No differences"
    summary = f"{patch.count(chr(10) + 'diff --git ') + patch.startswith('diff --git ')} changed files"
    if len(summary) + 1 + len(patch) <= MAX_CHARS:
        return f"{summary}\n{patch}"
    stat = (await run_git(["diff", "--stat=120,80", "--no-ext-diff", "--no-textconv", *selectors])).text
    return compose_truncated(summary_from_stat(stat), stat, patch, MAX_CHARS)
```

### Startup (fail fast before the stdio loop)
```python
def main() -> None:
    args = parse_args()                      # argparse; usage + error on stderr, exit 2
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        fail(f"repository root does not exist or is not a folder: {root}")
    git = shutil.which("git") or fail("git was not found in PATH; install Git for Windows")
    check_work_tree(git, root)               # D-04 message on "not a git repository"
    configure(root, git, args.max_chars, args.timeout)
    server.run(transport="stdio")

def fail(message: str) -> NoReturn:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    sys.exit(1)
```

### Listing tools (success criterion 1)
```
python scripts/mcp_list_tools.py python mcp_servers/project_git.py --root=C:\Projects\AiAdventAgentV2
```
Works because `connect_once_and_list(command, args)` takes `argv[0]` as command and the rest as args; without `--root` the CLI's own cwd is the root.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `go install github.com/github/github-mcp-server/cmd/github-mcp-server@latest` / Docker | Prebuilt release zips per OS (Windows x86_64, arm64, i386) | current v2.0.2 (2026-10-08) | D-16: download zip, no Go, no Docker |
| Older default toolsets | Default `context, copilot, issues, pull_requests, repos, users` (CLI help v2.0.2); README prose differs slightly | v2.x | Always pass `--toolsets` explicitly; without options 46 tools are exposed even with `--read-only` (27) |
| stdio server reading token only from env | Also OAuth / GitHub App flags | v2.x | Not used; PAT via `GITHUB_PERSONAL_ACCESS_TOKEN` |

**Deprecated/outdated:** `.planning/STATE.md` note about a 16 384 default context (actual 65 536, per CONTEXT.md). `docs/ARCHITECTURE.md` says only one tool round per turn, while `tool_guard.MAX_TOOL_ROUNDS = 15`; confirm when documenting (not part of this phase, mention only if the docs section touches it).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Fine-grained PAT permissions needed by the 16 read tools are Metadata + Contents read + Pull requests read (`list_repository_collaborators` may need more, e.g. Administration read) | Form values | A tool returns 403; irrelevant to GHUB-02 because the test calls `list_branches` (Contents). D-17 already grants read/write on Contents and Pull requests |
| A2 | `--end-of-options` needs git >= 2.24 | Standard Stack | Only matters on very old git; local git is 2.39.1. Startup version check optional |
| A3 | `GIT_OPTIONAL_LOCKS=0` fully prevents `status` from writing the index | Pattern 2 | Rare index.lock contention with the user's own git; documented git behaviour from training, not re-measured |
| A4 | `LC_ALL=C` keeps git messages English on Git for Windows | Pattern 2 / 7 | The "not a git repository" detection falls back to exit code 128 plus generic message; not measured with a localized git |
| A5 | The Agent's Windows PATH may resolve `cmd\git.exe` (the wrapper); only observed that the wrapper orphans its child | Pitfall 2 | Tree-kill is harmless either way |
| A6 | Name of the GHUB-02 gate variable: `GITHUB_PERSONAL_ACCESS_TOKEN` (same as the server's) plus `GITHUB_MCP_SERVER_EXE` for the binary path (default `C:\Users\Aleksey\go\bin\github-mcp-server.exe`) | Test Plan | Naming only; planner may change |

## Open Questions

1. **Range support (`A...B`) in `git_diff`/`git_recent_commits`**
   - Known: a Pull Request day will want `main...HEAD`; cost is a few lines plus 2 tests.
   - Unclear: whether to include it now (CONTEXT does not mention it).
   - Recommendation: include; if not, say so in the tool descriptions.
2. **Tool names with or without a `git_` prefix**
   - Recommendation: `git_current_branch`, `git_status`, `git_diff`, `git_tracked_files`, `git_recent_commits` (exposed as `mcp__local-git__git_status`), because bare `status`/`diff` are ambiguous next to the GitHub server's names.
3. **Local-vs-remote hint for the model**
   - Recommendation: no change to `agent/`. Put "LOCAL working copy, not the GitHub remote" at the start of every git tool description and "REMOTE GitHub repository" is already implied by the github tool descriptions (`_describe` prefixes each with `[MCP server: <name>]`). `MULTI_STEP_TOOL_HINT` is 144 words and `tests/test_tool_guard.py:413` caps it at 170, so editing it is possible but not needed.
4. **Real GitHub token for the GHUB-02 test run**
   - The user must create the fine-grained token; until then the test is skipped and the listing is recorded without a token (listing does not need a valid one). Record "tool call with token: not run" honestly in the verification doc if no token is available (consistent with D-20's "not run, not passed" rule).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| git | the server, tests | yes | 2.39.1.windows.1 | — |
| Python / `mcp` | server and tests | yes | 3.13.15 / mcp 1.30.0 | — |
| psutil | tree-kill | yes (in requirements) | >=6.0.0 | plain `proc.kill()` (leaves orphan on `cmd\git.exe`) |
| Node.js | not needed | v22.23.2 present | — | — |
| `github-mcp-server.exe` | GHUB-01/02 | not installed in `C:\Users\Aleksey\go\bin` (v2.0.2 zip was downloaded to a scratch folder only, outside the repo) | — | user downloads it (D-16); test skips |
| GitHub fine-grained PAT | GHUB-02 real call | not available to me; must never be stored (D-18) | — | listing-only record; call test skipped |
| LM Studio | PGIT-04 E2E | running at `localhost:1234`, `qwen/qwen3.5-9b` loaded (vlm) | — | record "not run" |
| Playwright (Python) | PGIT-04 E2E | used by existing scripts (exit 4 if missing) | — | record "not run" |
| `gh` CLI | not needed | not installed | — | — |

**Missing dependencies with no fallback:** none for the server itself.
**Missing dependencies with fallback:** github-mcp-server binary and PAT (skip real-call test; docs still complete).

## Test Plan

`workflow.nyquist_validation` is `false` in `.planning/config.json`, so no formal Validation Architecture section; the test list is given for the planner because VER-07 asks for coverage written alongside this phase.

**Framework:** pytest 8.3 + pytest-asyncio (`asyncio_mode = auto`), `pythonpath = .`, no new config. Quick run: `pytest tests/test_mcp_project_git.py -v`; full: `pytest tests/ -v`.

**What `docs/TESTING_GUIDE.md` requires:** it has no MCP-specific section at all (grep found no `test_mcp_*` entries; only scheduler and API-key notes mention MCP). There is therefore no mandated scenario list; add a short "test_mcp_project_git.py / test_mcp_real_github.py" subsection there (not required by VER-09 but consistent with how other test files are described). Related existing tests to keep green: `tests/test_mcp_*.py` (12 files), fixture `tests/fixtures/mcp_stdio_server.py`.

**Fixture:** `git_repo(tmp_path)` that builds a repository with plain `subprocess.run`, an env stripped of `GIT_*`, `-c user.name=T -c user.email=t@t -c core.autocrlf=false -c init.defaultBranch=main`: commits an ASCII file, a `папка/файл.txt` (non-ASCII), a file with a space, then makes one modified tracked file, one staged file and one untracked file; a second branch `feature`. Spawn the server through `agent.mcp_client.open_session(sys.executable, [SERVER, "--root", str(repo)], None, None)` and call tools with `handle.session.call_tool(...)`, closing with `close_handle` (this exercises exactly the app path); pure-function tests import `mcp_servers.project_git` directly.

| Scenario | Type | Notes |
|----------|------|-------|
| `list_tools` returns exactly the 5 tools, each with description, `scripts/mcp_list_tools.py` exit 0 (PGIT-01, criterion 1) | stdio | use `cli.run([...])` like `test_mcp_cli.py` |
| current branch returns `main`, then after `git checkout feature` returns `feature` without restarting the server (PGIT-04 core) | stdio | proves "live state" |
| detached HEAD -> `Detached HEAD at <hash>`; unborn repo -> branch + "no commits yet" | stdio | |
| status: modified/staged/untracked lines and counts; clean tree -> `Working tree clean`; non-ASCII name appears verbatim | stdio | |
| diff: unstaged, `staged=True`, `ref="HEAD"`, `path` limited to one file; no changes -> `No differences` (never empty) | stdio | |
| tracked files: all, `path="папка"`, total count in summary; empty folder match -> explicit "No tracked files under …" | stdio | |
| recent commits: default 10, `count=2`, `count=500` clamped to 50 and noted, `path`, `ref="feature"`, line format `hash<TAB>date<TAB>author<TAB>subject` | stdio | |
| root confinement: `../x`, absolute path outside, `C:\Windows`, `D:foo`, UNC, `\Windows`, `NUL`; absolute path inside root accepted and normalised; case-insensitive match; symlink out of the root rejected (skip when symlink creation is not permitted) | unit on `resolve_inside_root` + 2 stdio calls checking `isError` and message text | message names reason and fix (D-15) |
| leading `-`: `path="--output=x"`, `ref="--upload-pack=x"`, `ref="-p"` rejected before git runs (monkeypatch `run_git` to fail if called) | unit | PGIT-02 |
| glob/magic neutralised: `path=":(top)x"` rejected, `path="*.py"` matches nothing literal (not expanded) | unit | |
| unknown ref / blob ref (`HEAD:file`) -> `isError` with "unknown ref" | stdio | |
| truncation: file with ~60 000 characters -> result length `<= max_chars`, contains marker with shown/total numbers and the word `path`, stat precedes patch and lists the file; `--max-chars=2000` honoured | stdio | D-11..13 |
| result below the app cap: `len(result) <= 18000` and through `call_mcp_tool` `truncated is False` | stdio + binding | optional |
| timeout: `run_git`/runner with a Python child that sleeps and spawns a sleeping grandchild, `timeout=0.5` -> error within ~1.5 s, both pids gone (psutil) | unit | no real hung git needed; runner takes argv[0] |
| startup failures: root missing; `git` missing (run server with `PATH` pointing to an empty dir via `env`); root not a repo (message mentions `git init`); bad `--max-chars` -> each ends with `PROCESS_EXITED` and a stderr tail containing the reason, process exits non-zero, nothing on stdout | stdio via `connect_once_and_list` | D-03/D-04 |
| stdout cleanliness: no output on stdout before the first request (spawn raw, read nothing within 1 s) and source has no `print(`/`shared` import | unit | Pitfall 1 |
| GIT_* env stripped: set `GIT_DIR` to another repo in the server's env, results still reflect `--root` | stdio | |
| Name check: `server_slug("local-git", 1) == "local-git"` and exposed name `mcp__local-git__git_status` via `build_toolset_from_servers` | unit | PGIT-03 |
| Real chat dispatch like `test_mcp_real_filesystem.py`: create server row with `mcp_config.create_server`, `build_mcp_toolset`, `dispatch_tool_calls` for `mcp__local-git__git_current_branch` against the temp repo | integration | PGIT-03 without LLM |

**Real GitHub binary (`tests/test_mcp_real_github.py`, D-19):** module-level `pytestmark = skipif(not exe exists or not os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"))`. Test 1: `connect_server` with command=exe, args `["stdio","--read-only","--toolsets=repos,pull_requests"]`, env `{"GITHUB_PERSONAL_ACCESS_TOKEN": token}` -> CONNECTED, tool set equals the 16-name set (or at least contains `list_branches`, `pull_request_read`, `list_pull_requests`) and contains none of `create_pull_request`, `merge_pull_request`, `create_or_update_file`. Test 2: dispatch `mcp__github__list_branches` for `AlekseyYakovlev/AI-Advent-Challenge-9-v2` and assert `ok`. Test 3: API round trip with `authenticated_client`: create the server with the token, `GET /api/v1/mcp/servers` returns `env_keys` and no token substring (masking). The pids cleanup assertion pattern from `test_mcp_real_filesystem.py` (psutil, `github-mcp-server.exe`) should be reused. Never print or assert-with-message the token. A second, token-free test can always run when only the exe exists: listing with a dummy env is NOT recommended in pytest (it hits `api.github.com` for scope fetch and logs an error); keep it to the recorded `mcp_list_tools.py` output.

**PGIT-04 E2E (`scripts/e2e_mcp_git_playwright.py`):** skeleton from `e2e_kb_playwright.py` (isolated copy, ports 18000/18001, scratch DB, preflight BLOCKED if LM Studio unreachable -> report "not run"). Steps: create temp repo with branches `alpha` (checked out) and `beta`; log in; add the server through the form fields `#mcp-name`, `#mcp-command`, `#mcp-args`, `#mcp-cwd` and `#btn-mcp-save` (form ids verified in `ui/static/index.html`); new chat; ask "на какой ветке проект?"; assert a tool-call card with `mcp__local-git__git_current_branch` and `alpha` in the answer; `git checkout beta` in the temp repo; ask again in a new turn; assert the card appears again and the answer contains `beta`. A real 9B model is non-deterministic: allow one retry, and record the first failing transcript.

### Wave 0 Gaps
- [ ] `mcp_servers/__init__.py`, `mcp_servers/project_git.py`
- [ ] `tests/test_mcp_project_git.py` (+ shared `git_repo` fixture; put it in the test file, not `conftest.py`, unless reused)
- [ ] `tests/test_mcp_real_github.py`
- [ ] `scripts/e2e_mcp_git_playwright.py`
- No framework installation needed.

## Security Domain

`security_enforcement` is absent in config, so it applies.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (server is a local stdio child; app auth unchanged) | — |
| V3 Session Management | no | — |
| V4 Access Control | yes | Root confinement: `resolve_inside_root`, realpath + `commonpath` with `normcase`; ref gate; read-only fixed commands |
| V5 Input Validation | yes | Reject leading `-`, control chars, drive-relative; `--end-of-options`; `--`; `--literal-pathspecs`; typed FastMCP parameters (pydantic validates `staged: bool`, `count: int`) |
| V6 Cryptography | no | Token stored by the existing app mechanism; no new crypto |
| V7/V8 Logging, data protection | yes | Never log the token or `env`; server logs to stderr only, never the diff text; docs forbid storing the token in files (D-18) |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Argument injection (`--output=…`, `--upload-pack=…`, `-c …`) through `ref`/`path` | Tampering | Leading-`-` rejection + resolved hash + `--` + no shell |
| Path traversal (`..`, absolute, other drive, UNC, symlink/junction, case tricks, ADS `file:stream`) | Information disclosure | Normalised realpath confinement; `:` rejected |
| Pathspec magic/glob abuse (`:(top)`, `:(glob)`, `*`) | Information disclosure | `--literal-pathspecs` |
| Repository config executing programs (`diff.external`, textconv, `core.fsmonitor`, pager) | Elevation of privilege | `--no-ext-diff --no-textconv -c core.fsmonitor=false --no-pager`; inherited `GIT_*` stripped |
| Inherited `GIT_DIR`/`GIT_WORK_TREE` redirecting git outside the root | Tampering | Strip `GIT_*` from the child env |
| Output flooding / hung git | Denial of service | `--max-chars`, hard byte cap, `--timeout` + tree-kill |
| Prompt injection via diff/commit text | Tampering | Out of scope; results reach the model as `role=tool` content (documented residual risk in ARCHITECTURE.md) |
| Token leakage (logs, files, tests, planning docs) | Information disclosure | Env field only; masked in API; D-18; tests read it from the process environment and never print it |
| Write via GitHub server | Tampering | `--read-only` flag (verified: 16 vs 29 tools); remaining risk: the PAT itself has write rights (D-17), so removing the flag would enable writes |

## Documentation targets

- **`docs/USER_GUIDE.md`**: contains **no** MCP content today (grep for "mcp" finds nothing; the guide mixes English and Russian section titles, e.g. "База знаний", "Память задачи"). Add a new section "MCP серверы" before "Tips"/"Troubleshooting": how the form works (one argument per line, env `KEY=VALUE`, working directory, enabled), the `local-git` table, the `github` table with download/checksum/token steps, example chat questions, masking note, read-only note, and troubleshooting rows (not a git repository, git not in PATH, timeout, path outside root). Add 2-3 lines to "Troubleshooting".
- **`docs/ARCHITECTURE.md`**: section "Chat tool calls (built-in + MCP)" (around lines 173-235) describes naming, limits, risks. Add a short subsection "Bundled MCP servers": `mcp_servers/project_git.py` (own process, stdio, five read-only tools, root confinement, 18 000 / 10 s defaults vs app 20 000 / 30 s, why it avoids `shared.*`), and the external `github-mcp-server` (read-only, toolsets, token via env). Mention the risk-note update: git tools are read-only, GitHub server is read-only by flag only.
- **`docs/API_SPEC.md`**: MCP routes and `/api/v1/mcp/servers` appear in the chat `tool_call` frame example (line ~118, `mcp__filesystem__list_allowed_directories`) and env settings table (~1089-1104). No new routes. Add a short "Bundled servers" example payload for `POST /api/v1/mcp/servers` with `local-git` (and the masked github env), and optionally the server's own tool schemas and error format. Keep the env table unchanged (no new settings).
- **`docs/TESTING_GUIDE.md`**: optional new subsection for the two new test files (no MCP section exists).
- **`README.md`**: does not exist yet in the repository root (Phase 21 writes it, per 21-CONTEXT D-01). Do not create it in this phase; if Phase 21 has landed, add one line linking to the USER_GUIDE section; otherwise leave a note for Phase 23 (it also owns `/help`).
- VER-09 (tracked in Phase 23) requires the three docs to describe the servers as built, so write them against the final tool names.

## Sources

### Primary (HIGH confidence)
- Local execution on 2026-10-13: FastMCP prototype (error semantics, async subprocess kill) against `mcp 1.30.0`; git 2.39.1 experiments (quotepath, detached/unborn HEAD, literal pathspecs, `rev-parse --verify --end-of-options`); psutil process-tree experiment with `cmd\git.exe`; path-confinement prototype on Windows.
- Installed SDK source: `C:\Python\Python313\Lib\site-packages\mcp\server\fastmcp\{server.py,tools/base.py}`, `mcp\server\lowlevel\server.py`, `mcp\client\stdio\__init__.py`, `mcp\os\win32\utilities.py`.
- Repository code: `agent/mcp_client.py`, `agent/mcp_tools.py`, `agent/mcp_config.py`, `agent/tool_guard.py`, `shared/logger.py`, `scripts/mcp_list_tools.py`, `scripts/e2e_mcp_chat_playwright.py`, `scripts/e2e_kb_playwright.py`, `tests/test_mcp_real_filesystem.py`, `tests/fixtures/mcp_stdio_server.py`, `ui/static/index.html`, `ui/static/app.js`.
- `https://api.github.com/repos/github/github-mcp-server/releases/latest` (v2.0.2, 2026-10-08, asset names, digests) and the downloaded Windows zip with its `--help` output; tool counts measured through the MCP SDK client.

### Secondary (MEDIUM confidence)
- `https://raw.githubusercontent.com/github/github-mcp-server/main/README.md` (flags, `GITHUB_TOOLSETS`, `GITHUB_READ_ONLY` for Docker, default toolsets; `repos` toolset list partial).

### Tertiary (LOW confidence)
- Fine-grained PAT permission-to-tool mapping (A1) from general knowledge; the README only lists classic scopes.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH, installed and executed
- Architecture / error semantics / command lines: HIGH, prototyped on this machine and Windows git
- GitHub server facts (version, flags, tool list): HIGH, executed v2.0.2; PAT permissions: LOW
- Pitfalls: HIGH for 1-6, 9, 10 (reproduced or read from code); MEDIUM for 8

**Research date:** 2026-10-13
**Valid until:** 2026-11-12 for the repository facts; `github-mcp-server` moves fast (v2.0.2 is 5 days old), so re-check the release and the 16-tool list before the final verification run (7 days).
