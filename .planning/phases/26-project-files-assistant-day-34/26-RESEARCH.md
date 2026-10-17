# Phase 26: Project files assistant (Day 34) - Research

**Researched:** 2026-10-17
**Domain:** Own stdio MCP server extension (read/search/write file tools), diff rendering in vanilla JS, HTTP run script, isolated-copy verification
**Confidence:** HIGH (all central claims verified against this repository and by experiments with the installed git 2.39.1 / Python 3.13 / mcp 1.30.0)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Entry point**
- **D-01:** There is no new command. The user gives the goal as an **ordinary chat message** in a chat where the file tools are available; the existing tool loop (`agent/ws.py::_run_tool_rounds`, `MAX_TOOL_ROUNDS`) does the multi-step work, and every call is visible as an ordinary tool-call card.
- **D-02:** The same goal can be run **from the command line**: a new script (expected `scripts/files_task.py`) sends the goal to the existing plain HTTP chat endpoint (`agent/http_chat.py`) with an API key, so the chat path and the script path run the same code.
- **D-03:** The project root is the server's `--root` launch argument in the «MCP серверы» form, as for `local-git` (Phase 22 D-02). No root setting is added elsewhere. (User left this to Claude; chosen for consistency with Phase 22.)

**File tools (extension of the local-git server)**
- **D-04:** The tools are added to the **existing own server `mcp_servers/project_git.py`**, not to a new server and not through the external `filesystem.exe`. The server keeps its self-contained rules from Phase 22: no `shared/` imports, diagnostics to stderr, rejected calls return `isError` with an English reason and fix (Phase 22 D-15), results are never empty strings, and output stays under the app's 20 000-character tool-result limit with the explicit truncation marker (Phase 22 D-11, D-12).
- **D-05:** Writing is enabled only by a new launch flag **`--allow-write`**. Without it the server exposes the five git tools plus the new read tools and nothing that writes, so the existing `local-git` entry (and `/help`, which uses it by default, and every other consumer) stays read-only with no change in its configuration. For the assistant the user adds **a second entry of the same server** with `--allow-write` (expected name `project-files`, tools `mcp__project-files__<tool>`); the documentation gives the exact form values.
- **D-06:** New read tools (available with and without the flag):
  - `read_file(path, start_line?, end_line?)` — file text with line numbers, truncated with the marker.
  - `search_text(pattern, regex?, glob?)` — substring or regular-expression search across files; each hit is `path:line: text`; a total hit count and the marker when truncated.
  - `find_usages(symbol)` — Python symbol search through the standard library `ast`: definitions, imports and references/calls, each with `path:line` and its kind, without false hits in comments or strings. Non-Python files are covered by `search_text`.
  The existing tracked-files tool remains the listing tool.
- **D-07:** Read and search cover files tracked by git plus new files that git does not ignore; ignored files (for example `app.db`, `.env`) are not read or searched.
- **D-08:** Write tools (only with `--allow-write`):
  - `write_file(path, content)` — creates a new file (or fully rewrites one).
  - `edit_file(path, old_text, new_text)` — replaces one fragment; `old_text` must occur **exactly once**, otherwise the call is rejected with a reason (not found / found N times, add more surrounding lines).
  There is no delete or rename tool.

**Writes and diff**
- **D-09:** A write call **saves to disk immediately**, and its tool result is the **unified diff of that change** (a new file appears as an all-added diff). Undo is through git. There is no dry-run mode and no confirmation step.
- **D-10:** Writes are rejected (with `isError` and a reason) for: paths outside the root (existing path gate), anything under `.git/`, `.env*` files, paths git ignores, binary files, and content above a size limit.
- **D-11:** In the chat, a tool-call card whose result is a unified diff renders it **as a coloured diff** (added lines green, removed lines red, hunk headers marked) — a change in `ui/static/app.js` card rendering, sanitised as all inserted HTML (DOMPurify). Other cards stay as they are. The model ends its answer by listing the files it created or changed.

**Reproducibility and verification**
- **D-12:** A run is reproducible through the script of D-02: it checks that the working copy of the target root is clean, records the commit, the provider and model, and runs with temperature 0; it sends the goal, then saves a **run log** (goal, commit, provider/model, settings, every tool call with its arguments and result summary, final answer) and the resulting **`git diff` as a `.patch`** file. Repeating a run means the same commit plus the same goal.
- **D-13:** Demonstration and verification runs write into a **separate clone** of the repository (for example in a temporary folder) set as the `--root` of the write-enabled entry; the user's own working copy `C:\Projects\AiAdventAgentV2` is never written by these runs. This fits the isolated-copy rule (ports 18000/18001, scratch database; the user's app on 8000/8001 is never touched).
- **D-14:** Verification is pytest + an isolated-copy end-to-end script + **`Day34_report.md`** (Russian, like earlier reports) with the scenarios run on the **local LM Studio model only**, each with its log and `.patch`. No judge, no second model. Honest recording as before: a run that could not be made (LM Studio not running) is recorded as not run, never as passed; a failed scenario stays in the report with its output.
- **D-15:** The fixed scenarios for the report and the video:
  1. Usages of an API — e.g. «найди все места, где используется `cleanup_chat_caches` (или `get_effective_settings`), и объясни роль каждого».
  2. ADR — e.g. «напиши ADR о разделении UI/Agent на два процесса» → a new file such as `docs/adr/0001-*.md`, based on the code and the documentation.
  3. CHANGELOG — «составь CHANGELOG.md по истории git» (uses the git log tool; prepares Phase 27).
  4. Folder README — «напиши README для `mcp_servers/`» from the servers' code.
- **D-16:** pytest covers at least: each new read tool (found / not found / truncation / ignored files), `find_usages` kinds, the `--allow-write` gate (write tools absent without the flag), `write_file` and `edit_file` results as diffs, `edit_file` with zero and multiple matches, each forbidden write target of D-10, and the run script's log/patch output against a mocked HTTP endpoint. Existing `local-git` and `/help` tests stay green unchanged.

### Claude's Discretion
- Exact tool and parameter names, the English tool descriptions, line-number format in `read_file`, the hit limit and context lines of `search_text`, and which `ast` node kinds `find_usages` reports.
- The size limit for writes, how "binary" is detected, how ignored files are detected (`git check-ignore` vs `git ls-files --others --exclude-standard`), and whether `write_file` may overwrite an existing file or only create (with `edit_file` for changes).
- Whether a hint is added to the system prompt when the write-enabled server is present (as `agent/tool_guard.py::MULTI_STEP_TOOL_HINT` does for filesystem/GitLab tools), and whether duplicate read tools from two entries of the same server (`local-git` + `project-files`) need handling.
- Whether `MAX_TOOL_ROUNDS` (15) is enough for the scenarios or needs to be raised, measured on the real scenarios.
- How the run script sets temperature 0 and the model (per-chat settings through the existing settings API, or request fields), the log format (Markdown or JSON) and where logs and patches are stored (an ignored output folder).
- How the clone for D-13 is created and pointed to by the end-to-end script and the report runs.
- How the diff card detects a diff result (content sniffing vs a marker line from the server).
- The git branch: the convention is one branch per day (`Day34`); Phase 25 (`Day33`) is not merged yet, and Phase 26 is independent of it, so the planner decides what `Day34` is branched from.
- Documentation updates in `docs/USER_GUIDE.md` (form values for the `project-files` entry), `docs/ARCHITECTURE.md`, `docs/API_SPEC.md` if needed, `docs/TESTING_GUIDE.md` and `README.md`, plus the docs guard test.

### Deferred Ideas (OUT OF SCOPE)
- A `/files` slash command with its own prompt, label and Settings section (offered, not chosen — D-01).
- Confirm-before-write: proposed edits applied with an «Применить» button, or a `--dry-run` mode that only shows the diff (offered, not chosen — D-09).
- A unified «Изменения» block under the answer collected by the app (offered, not chosen — D-11).
- Semantic search over the code base from Phase 24 as an extra tool for this assistant.
- File delete/rename tools; git write tools (PGIT-05).
- A two-model run with a judge for Day 34.
</user_constraints>

## Project Constraints (from CLAUDE.md)

- Python 3.11+ (installed: 3.13.15); type hints everywhere; `async`/`await` for all I/O; no linter/formatter configured.
- **No `print()`** in app code. Exception: scripts (`scripts/*.py` use a `report()` helper that prints; `run.py` banner). The MCP server file must contain no `print(` and no `import/from shared|agent` (pinned by `test_module_source_is_clean`).
- `structlog` logging in app modules; the MCP server file is the documented exception (stderr via `sys.stderr.write`).
- No Docker/Redis/multiprocessing/`os.fork`; subprocesses only via `asyncio.create_subprocess_exec` (the server's `run_command` already does this). Node only as a runtime for external MCP servers.
- Frontend: vanilla JS + CDN libs only; **never insert HTML without `DOMPurify.sanitize()`** (UI-SPEC goes further: `textContent` only for diff text).
- `datetime.now(timezone.utc)`; imports stdlib -> third-party -> local; avoid bare `except:`; no hardcoded secrets.
- Tests: `pytest` + `pytest-asyncio` (`asyncio_mode = auto`), `respx` for HTTP mocking; tests use a separate DB; consult `docs/TESTING_GUIDE.md` before adding tests.
- User memory/feedback: planning docs in English, reports in Russian; E2E/UAT runs are done by the agent on an isolated copy at ports 18000/18001 (never touch the user's app on 8000/8001); push + merge the phase branch to `main` on completion without asking; **no Claude `Co-Authored-By` line in commits** (global CLAUDE.md; overrides any attribution reminder).
- `.planning/config.json`: `nyquist_validation: false` (Validation Architecture section intentionally reduced to a test map below), `commit_docs: true`.

<phase_requirements>
## Phase Requirements

ROADMAP has `Requirements: TBD`. Proposed IDs (prefix `PFILE-`, derived from D-01..D-16); the planner should add them to `.planning/REQUIREMENTS.md` and the ROADMAP phase block.

| ID | Description | Decisions | Research Support |
|----|-------------|-----------|------------------|
| PFILE-01 | The own git MCP server offers read tools `read_file`, `search_text`, `find_usages` over git-visible files (tracked + untracked-not-ignored), bounded and truncated with the explicit marker, with and without `--allow-write`. | D-04, D-06, D-07 | `git ls-files --cached --others --exclude-standard`, `git grep` recipe, `ast` visitor recipe, truncation helpers already in the file |
| PFILE-02 | Write tools exist only when the server is started with `--allow-write`; without it the registered tool list has no writer and a direct call is also refused; the existing `local-git` entry and `/help` are unchanged. | D-05 | `FastMCP.add_tool` conditional registration in `main()` + in-function guard |
| PFILE-03 | `write_file` and `edit_file` save to disk immediately and return the unified diff of the change (new file = all-added diff, `edit_file` requires exactly one occurrence). | D-08, D-09 | `difflib.unified_diff` recipe, CRLF handling, atomic write |
| PFILE-04 | Writes are rejected with `isError`, an English reason and a fix for: outside root, `.git/`, `.env*`, git-ignored paths, binary content/files, oversized content. | D-10 | Path gate reuse, `git check-ignore`, Windows hardening list |
| PFILE-05 | A goal typed as an ordinary chat message makes the model list, search, read and write through the file tools using the existing tool loop; every call shows as a tool-call card; the answer lists created/changed files. | D-01, D-03, D-11 | Tool loop analysis, hint/descriptions advice, rounds budget |
| PFILE-06 | A tool-call card whose successful result is a unified diff renders a coloured diff built with `createElement`/`textContent` only; other cards are unchanged. | D-11 + 26-UI-SPEC | Hunk-aware line classifier, card frame caps |
| PFILE-07 | `scripts/files_task.py` sends a goal over the plain HTTP chat endpoint with an API key, requires a clean target root, records commit/provider/model/settings at temperature 0, and saves a run log and a `.patch`. | D-02, D-12 | Script recipe, `git add -N` + `git diff` |
| PFILE-08 | pytest covers the new tools, the write gate, forbidden targets, diff results, the run script against a mocked endpoint; an isolated-copy end-to-end script runs the flow on ports 18000/18001 against a clone. | D-13, D-16 | Test map, the three existing tests that must be edited |
| PFILE-09 | `Day34_report.md` (Russian) records the four scenarios on the local LM Studio model with log and `.patch` each, honestly (not run / failed stay visible). | D-14, D-15 | Report pattern from `Day32_report.md`, `eval_out/` layout |
| PFILE-10 | `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md`, `README.md` (and `docs/API_SPEC.md` only if the HTTP surface changes — it does not) describe the new tools, the `project-files` form values, the diff card and the run script; docs guard test updated. | D-05, 26-CONTEXT | `tests/test_docs_mcp_servers.py` pins |
</phase_requirements>

## Summary

The phase is almost entirely an extension of `mcp_servers/project_git.py` plus two small satellites (a card renderer in `ui/static/app.js`, a script `scripts/files_task.py`) and verification artifacts. No new package is needed: everything uses the standard library (`ast`, `difflib`, `fnmatch`, `os`), the already pinned `mcp==1.30.0`, `psutil`, and the git CLI already required by the server. The chat path needs **no code change**: all enabled MCP servers of the user are merged into every chat turn's toolset (`_load_mcp_toolset` -> `build_mcp_toolset`, no per-chat selection), so a second server entry named `project-files` simply appears as `mcp__project-files__<tool>`.

Three facts shape the plan and are easy to miss. (1) **Adding any tool to `project_git.py` breaks three existing tests** that pin "exactly five tools" (`test_exactly_five_tools_with_local_description`, `test_cli_lists_five_tools` with `Tools (5):`, and `test_user_guide_names_every_registered_tool` with `len(names) == 5`) — this contradicts D-16's "stay green unchanged" and cannot be avoided because D-05 says the read tools exist without the flag; those three assertions must be minimally relaxed (git five still present and still described as LOCAL; docs name every tool). (2) The tool-call frame sent to the browser and returned in the HTTP `tool_calls` list is **capped at 4 000 characters** (`TOOL_EVENT_PREVIEW_CHARS`), arguments at 2 000, while the model sees up to 18 000: a README/ADR diff will be cut in the card and in the run log, so the `.patch` (from `git diff` of the clone) is the only complete artifact, and the diff header + a summary line must come first. (3) Cards are **not persisted** (`state.toolCallsByMessage` is JS memory only), so UI-SPEC item 8 ("reload shows the same diffs") holds for chat switching inside a session, not after F5.

The Windows environment adds correctness traps: `core.autocrlf=true` here (README.md is CRLF), so `edit_file` must match with newlines normalised and write back in the file's own style; `--literal-pathspecs` in the server's `base_argv` breaks `git check-ignore`/glob search and must be dropped for those two commands; NTFS aliases for `.git` (`.git.`, `GIT~1`) and reserved device names must be rejected explicitly.

**Primary recommendation:** Extend `project_git.py` in place (read tools always registered; `write_file`/`edit_file` registered in `main()` only under `--allow-write`, and guarded again inside the function); implement `search_text` with `git grep` (timeout-killable, binary-skipping, ignore-aware) and `find_usages` with an `ast` visitor; return `difflib` unified diffs with a one-line summary first; render with a hunk-aware `textContent` renderer; drive reproducibility through `scripts/files_task.py` (clean-root check, chat settings temperature 0, `git add -N .` + `git diff` for the patch); keep `MAX_TOOL_ROUNDS = 15` and the global hint unchanged unless the measured scenarios fail.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Read / search / usages over project files | MCP server process (`project_git.py`, stdio child) | git CLI | Self-contained server; git decides what is visible (D-07); app never reads project files itself |
| Write gate (`--allow-write`), path rules, diff creation | MCP server process | — | Safety must live where the write happens; the app cannot switch off single tools (REQUIREMENTS Out of Scope) |
| Multi-step goal execution | API / Backend (`agent/ws.py` tool loop, `MAX_TOOL_ROUNDS`) | LLM (LM Studio) | Existing loop; no change |
| Tool-call card + coloured diff | Browser / Client (`ui/static/app.js`) | — | Pure presentation of the frame already sent over WS |
| Reproducible run (clean check, log, patch) | CLI script (`scripts/files_task.py`) | API (`/api/v1/chats/{id}/messages`) + git CLI on the clone | Script drives the same HTTP route as `/help` eval; patch comes from git, not from the model |
| Persistence of changes | Filesystem of the clone (working copy) | git (undo) | D-09: write immediately, undo via git |
| Server registration | Database (`McpServerConfig` via existing «MCP серверы» form / `/api/v1/mcp/servers`) | — | D-03/D-05: second entry of the same script |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `ast` | 3.13.15 (installed) | `find_usages` | D-06 requires `ast`; no false hits in comments/strings by construction [VERIFIED: python 3.13.15 locally] |
| Python stdlib `difflib` | 3.13.15 | Unified diff for write results | Produces `--- a/x` / `+++ b/x` / `@@ -0,0 +1,N @@`, matching the UI-SPEC sniffing rule [VERIFIED: ran `difflib.unified_diff([], ["a\n","b\n"], "/dev/null", "b/x.md")`] |
| `mcp` (FastMCP) | 1.30.0 (pinned in requirements.txt) | Server framework | Already used; `FastMCP.add_tool(fn, structured_output=...)` and `remove_tool` exist [VERIFIED: `inspect.signature` on installed package] |
| git CLI | 2.39.1.windows.1 | `ls-files`, `grep`, `check-ignore`, `diff`, `clone` | Server already requires git; all needed commands tested on this version [VERIFIED: ran each in a temp repo] |
| psutil | >=6.0.0 (existing) | Process-tree kill in existing `run_command` | Reused unchanged |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `httpx` (existing) | >=0.27 | `scripts/files_task.py` HTTP calls | Same as `help_eval.py` |
| `respx` (existing) | >=0.21 | Mock the HTTP endpoint in the run-script test | PFILE-07 test |
| Playwright (existing, optional) | installed | E2E: drive chat + `page.evaluate` of `buildToolCallCard` | Browser verification only |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `git grep` for `search_text` | Python `re` over files | `re` cannot be interrupted: a model-written catastrophic regex would pin the server's event loop. `git grep` runs under the existing timeout + process-tree kill and skips binaries natively |
| `difflib` | `git diff --no-index` | Extra process, output has `a/tmp...` paths and platform quirks; `difflib` is deterministic and in-process |
| New server `project_files.py` | — | Rejected by D-04 |
| Filtering `ls-files` output in Python for `find_usages` | `git ls-files -- '*.py'` | Literal pathspecs are on in `base_argv`; filter with `str.endswith(".py")` instead |

**Installation:** none. No package is added. [VERIFIED: grep of requirements.txt; all imports are stdlib or already pinned]

**Version verification:** `python --version` 3.13.15; `git --version` 2.39.1.windows.1; `pip show mcp` 1.30.0 (matches `requirements.txt` pin) — all checked 2026-10-17 in this environment.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| (none) | — | — | — | — | not applicable | No external package is introduced by this phase |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
 Browser chat (WS /ws/chat/{id})            scripts/files_task.py (CLI)
   user types a GOAL                          clean-root check, commit, settings(T=0)
        |                                          |  POST /api/v1/chats/{id}/messages (Bearer key)
        v                                          v
   agent/ws.py  run_limited_turn -> build_mcp_toolset(ALL enabled servers of user)
        |        _run_tool_rounds: LLM <-> tool dispatch, <= 15 rounds, sequential
        |                    |
        |   mcp__project-files__{read_file|search_text|find_usages|git_*|write_file|edit_file}
        |                    v
        |      MCP stdio child:  python mcp_servers/project_git.py --root=<CLONE> --allow-write
        |        path gate (resolve_inside_root) ──> reject / continue
        |        read tools:  git ls-files --cached --others --exclude-standard   (visibility)
        |                     git grep -n -I -F|-E --untracked --exclude-standard   (search_text)
        |                     ast.parse per .py file                                 (find_usages)
        |        write tools: write gate (.git, .env*, ignored, binary, size, NTFS aliases)
        |                     read old -> compute new -> os.replace(temp) -> difflib diff
        |                     result = summary line + unified diff (<= --max-chars, marker)
        v                                          v
   frames: tool_call {result<=4000 chars, truncated}      HttpChatResponse.tool_calls (same frames)
        v                                          v
   app.js buildToolCallCard -> isUnifiedDiff? -> coloured diff block        run.json + answer
                                                                            git add -N . ; git diff -> run.patch
```

### Recommended Project Structure
```
mcp_servers/project_git.py     # extended in place (read tools, write tools, --allow-write)
scripts/files_task.py          # new: goal -> HTTP chat -> run.json + run.patch
scripts/e2e_project_files_playwright.py   # new: isolated copy 18000/18001 + clone as --root
tests/test_mcp_project_files.py           # new: read/search/usages/write/gate/diff tests
tests/test_files_task.py                  # new: run-script log/patch vs respx-mocked endpoint
tests/test_diff_card_ui.py                # new: app.js source-level guards (pattern: test_api_keys_ui.py)
eval_out/day34/<scenario>/run.json|run.patch|answer.md   # tracked, like eval_out/day32
Day34_report.md                # Russian report
docs/                          # USER_GUIDE (project-files form), ARCHITECTURE, TESTING_GUIDE; README.md
```

### Pattern 1: Conditional tool registration (the write gate)
**What:** Read tools use `@server.tool(structured_output=False)` like the existing five. Write tools are plain `async def` functions registered in `main()` only if `args.allow_write`; the function body also checks `get_config().allow_write` (defence in depth, and makes direct-call unit tests meaningful).
**Why:** `project_git.server.list_tools()` at import time then lists no writer (gate test trivial), and `configure(root, git, max_chars, timeout)` keeps working for all existing test call sites if `allow_write: bool = False` is added as a defaulted parameter and a defaulted `ServerConfig` field.
```python
# Source: installed mcp 1.30.0 signature FastMCP.add_tool(fn, name=None, ..., structured_output=None) [VERIFIED]
def require_write() -> None:
    if not get_config().allow_write:
        raise ToolInputError("writing is disabled; start the server with --allow-write")

async def write_file(path: str, content: str) -> str:
    """Create a file, or fully rewrite one, inside the repository ..."""
    require_write()
    ...

def main() -> None:
    ...
    configure(root, git, args.max_chars, args.timeout, allow_write=args.allow_write)
    if args.allow_write:
        server.add_tool(write_file, structured_output=False)
        server.add_tool(edit_file, structured_output=False)
    server.run(transport="stdio")
```
`parse_args` gains `parser.add_argument("--allow-write", action="store_true", ...)` (note `allow_abbrev=False` is already set, so `--allow` cannot be abbreviated by accident).

### Pattern 2: File visibility = git's view
**What:** One helper returns the set of readable paths: `git ls-files --cached --others --exclude-standard -z` (tested: lists tracked files and new untracked files, omits `.env`, `*.db`). For a single path use the same command with `-- <rel>` (literal pathspec is fine here) and require a non-empty answer **and** `os.path.isfile` (tracked-but-deleted files appear in `--cached`).
**Why:** D-07 in one command; avoids re-implementing `.gitignore` matching. Use `-z` and split on `\0` (no quoting issues; `core.quotepath=off` is already set).
[VERIFIED: experiment in temp repo — `new.md`, `sub/c.py` (untracked) listed; `.env`, `x.db` absent]

### Pattern 3: `search_text` through `git grep`
```python
# argv for the regex / substring search; NOTE: drop the "--literal-pathspecs" element of base_argv()
argv = [a for a in base_argv(cfg.git) if a != "--literal-pathspecs"] + [
    "grep", "-n", "-I", "--no-color", "--untracked", "--exclude-standard",
    "-E" if regex else "-F", *(["-i"] if ignore_case else []),
    "-e", pattern,                       # -e protects patterns that start with "-"
    *(["--", glob] if glob else []),
]
# exit code 0 = hits, 1 = no hits (not an error), >1 = error (bad regex -> rc 128, "fatal: -e option, 'fo(': Unmatched ( or \(")
```
Verified behaviours [VERIFIED: experiments, git 2.39.1]: `-F` and `-E` work; `\bfoo\b` works under `-E` on Git for Windows; default pathspec `*.py` matches nested files (`*` crosses `/`) so models' naive globs work; `--literal-pathspecs` makes a glob match nothing (rc 1), so it must be omitted; `:(exclude)...` magic pathspecs are accepted, so reject `glob` values that start with `:`, contain `..`, an absolute path or control characters. Reject newlines in `pattern` (git treats them as OR). Hit lines are `path:line:text`; reformat to `path:line: text` (strip, cut text at ~200 chars), count hits, cap at e.g. 200 shown, state total and the files count, add the marker via `fit_text`/`truncation_marker(unit="matches", advice="Narrow with glob or a more specific pattern")`. Parsing `path:line:text` with `split(":", 2)` is safe because paths with `:` are not possible on Windows and are rare elsewhere.

### Pattern 4: `find_usages` AST visitor
Kinds recommended (Claude's discretion): `def` (FunctionDef/AsyncFunctionDef/ClassDef name), `import` (alias.name last segment or asname == symbol in Import/ImportFrom), `call` (Call whose func is `Name(symbol)` or `Attribute(attr=symbol)`), `ref` (Name Load, Attribute attr, decorators, annotations), `assign` (Name Store), `param` (`ast.arg`). Mark the func node of a recorded call so it is not double-counted as `ref` (`seen: set[int]` of `id(node)`).
```python
# Source: stdlib ast docs [CITED: docs.python.org/3/library/ast.html]
class _Usages(ast.NodeVisitor):
    def __init__(self, symbol: str) -> None:
        self.symbol, self.hits, self._skip = symbol, [], set()
    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if (isinstance(f, ast.Name) and f.id == self.symbol) or (
            isinstance(f, ast.Attribute) and f.attr == self.symbol):
            self.hits.append((f.lineno, "call")); self._skip.add(id(f))
        self.generic_visit(node)
    def visit_Name(self, node: ast.Name) -> None:
        if node.id == self.symbol and id(node) not in self._skip:
            self.hits.append((node.lineno, "assign" if isinstance(node.ctx, ast.Store) else "ref"))
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == self.symbol and id(node) not in self._skip:
            self.hits.append((node.lineno, "ref"))
        self.generic_visit(node)
```
Decode with `errors="replace"` then `ast.parse(text, filename=rel)`; catch `SyntaxError, ValueError, RecursionError` per file and count skipped files in the summary line. Validate `symbol` (`str.isidentifier()` after taking the last dotted segment; reject empty). Summary first line: `N usages in M files (def 1, import 3, call 5, ref 2)` and, when no `.py` file matched, a non-empty "No usages of X found in N Python files. Use search_text for non-Python files". Output `path:line: kind: <source line stripped, <=160 chars>`; sort by path then line. Use a total time budget (loop with `time.monotonic()`; at the limit stop and add the marker) because the parse loop is CPU-bound in the server's event loop. The repo has 993 tracked files; Python-only parse time is small [ASSUMED: not measured].

### Pattern 5: Write result = summary + unified diff
- Read old text as bytes; reject NUL byte in the first 8 000 bytes or a `UnicodeDecodeError` as "binary" (git's own heuristic; tracked binary files are refused for edit).
- **Newlines:** detect CRLF in the existing file (`b"\r\n" in raw`). Do matching and diffing on LF-normalised text (models send `\n`), and write back with the file's original style. New files: LF. This machine has `core.autocrlf=true` and CRLF files (`README.md`), so skipping this makes every `edit_file` on such files fail with "not found" or rewrite every line ending. [VERIFIED: `git config core.autocrlf` = true; `file README.md` = CRLF]
- **Diff:** split with `text.split("\n")` (drop the final empty element) rather than `splitlines()` (which also splits on `\x0b`, `\x0c`, `\x1c`, `\u2028`), call `difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=3)` and join with `"\n"`. For a new file use `fromfile="/dev/null"` so the diff begins `--- /dev/null` / `+++ b/<path>` / `@@ -0,0 +1,N @@` (verified to match UI-SPEC). `lineterm=""` plus split-without-keepends avoids the known glitch where a last line without newline fuses with the next header line.
- **Identical content** -> `difflib` returns nothing: return a non-empty text ("No changes: <path> already has this content"), never an empty string (D-04).
- **Atomic write:** write to a temp file in the same directory, then `os.replace`; create parent directories with `os.makedirs(..., exist_ok=True)` only after the gate passed; catch `OSError` and raise `ToolInputError("could not write <path>: <reason>")`.
- **Result text:** first line `Created docs/adr/0001-x.md (+52 -0)` / `Updated path (+3 -1)`, then the diff. Pass it through `fit_text(summary, diff, max_chars, advice="The file was saved in full; call read_file to review it.")` so the marker cannot be misread as a failed write.

### Pattern 6: Write gate (D-10), applied to the **normalised relative path returned by `resolve_inside_root`**
Because `resolve_inside_root` uses `realpath`, a symlink that points into `.git/` resolves to a `.git/...` path and is then caught by the component rule.
1. any path component `casefold() == ".git"`; also components that end with `.` or space (NTFS strips them: `.git.` == `.git`) and components matching `^git~\d+$` (8.3 short name) [ASSUMED: known NTFS git-hardening class, not re-verified here]
2. basename `casefold().startswith(".env")`
3. `git check-ignore -q -- <rel>` rc 0 -> ignored (rc 1 = fine, other rc = error). Run it **without** `--literal-pathspecs` (with that flag git aborts: `pathspec magic not supported by this command: 'literal'` — [VERIFIED: experiment]). Verified results: `.env`, `.env.local`, `build/out.txt`, `x.db` -> 0; `docs/adr/0001.md`, `a[1].md`, non-existent new paths -> 1.
4. Windows reserved device names (`con`, `prn`, `aux`, `nul`, `com1-9`, `lpt1-9`, with or without extension) [ASSUMED]
5. target exists and is a directory; existing file is binary (edit) ; content contains NUL; `len(content.encode()) > MAX_WRITE_BYTES` (suggest 200 000, constant in the module)
6. `write_file` may overwrite an existing text file (diff shows it); the description says "prefer `edit_file` for partial changes".

### Pattern 7: `edit_file` exactly-once with help for weak models
`old_text` empty -> reject; `old_text == new_text` -> reject; count on LF-normalised content. `0` matches: reason "old_text was not found" **plus** where the first line of `old_text` does occur (`line N`) if it does, since a 9B model usually mis-copies indentation; `>1`: "old_text occurs N times (lines a, b, c...); add surrounding lines so it is unique". Because tool calls inside a turn run strictly sequentially (STATE.md), read-then-edit is race-free within a turn; the repeated identical failing `edit_file` call in two consecutive rounds is stopped by `tool_loop_detected` (`_call_signatures`), so error texts must tell the model to **change** the arguments.

### Pattern 8: Hunk-aware diff renderer (UI)
Follow 26-UI-SPEC (sniffing, classes, `textContent`). One refinement the spec's flat rule list misses: a **removed line whose content starts with `-- `** (SQL/Lua comment, YAML `---` rule is `----`) appears as `--- ...` and would be mis-coloured as a file header. Classify with a tiny state machine: before the first `@@` line of a file section, `--- `/`+++ ` are headers; after it, the first character decides (`+` added, `-` removed, `\` meta, else context); `diff --git ` resets to the header zone.
```javascript
// Source: design from 26-UI-SPEC "Line classification"; no library
function classifyDiffLine(line, inHunk) {
    if (line.startsWith('diff --git ')) return { kind: 'meta', inHunk: false };
    if (!inHunk && (line.startsWith('--- ') || line.startsWith('+++ ')
        || line.startsWith('index ') || line.startsWith('new file mode'))) return { kind: 'meta', inHunk: false };
    if (line.startsWith('@@')) return { kind: 'hunk', inHunk: true };
    if (!inHunk) return { kind: 'context', inHunk: false };  // summary line, truncation marker before diff
    if (line.startsWith('+')) return { kind: 'added', inHunk: true };
    if (line.startsWith('-')) return { kind: 'removed', inHunk: true };
    if (line.startsWith('\\')) return { kind: 'meta', inHunk: true };
    return { kind: 'context', inHunk: true };
}
```
`isUnifiedDiff(text)` = `evt.ok` and a `--- ` line directly followed by a `+++ ` line and some `@@ ` line. The write tools prepend a summary line, so the sniff must search lines, not test the string start.

### Pattern 9: Run script `scripts/files_task.py`
Flow (all calls with `Authorization: Bearer <key>`; every REST route except five cookie-only ones accepts a key, and chat creation + `PUT /api/v1/settings` are not among those five [VERIFIED: docs/API_SPEC.md "API keys"]):
1. Refuse unless `--root` is a git work tree whose `git status --porcelain` is empty; refuse if `--root` resolves to the repository containing the script unless an explicit `--allow-own-repo` flag is given (D-13 safety).
2. Record `git rev-parse HEAD`, branch, `GET /api/v1/mcp/servers` (names, enabled, connection status, tool names), provider/model (`GET /api/v1/llm-providers/models`, as `help_eval.provider_models`).
3. `POST /api/v1/chats`, then `PUT /api/v1/settings` `{chat_id, temperature: 0, max_tokens, context_length}` (pattern: `help_eval.create_chat`).
4. `POST /api/v1/chats/{id}/messages` `{content: goal, model, provider_id}` with a long timeout (`help_eval` uses 300 s; use >= 900 s for write scenarios on the local model). Handle 409 `CHAT_BUSY`, 429, 503 `LM_STUDIO_UNAVAILABLE` -> exit code "not run/blocked", never "passed".
5. `git -C <root> add -N .` (intent-to-add: untracked **new** files — the main output of the ADR/README/CHANGELOG scenarios — then appear in `git diff`; plain `git diff` would miss them) then `git diff --binary > run.patch`. [VERIFIED: experiment — new files appear as `new file mode` diffs and `git apply --check` of the patch on a fresh clone succeeds]
6. Write `run.json` (goal, commit, branch, provider/model, settings, mcp server list, tool calls with arguments and result summary, answer, elapsed, exit status, patch stats) and `answer.md`. Output folder: use tracked `eval_out/day34/<scenario>/` (the report must point at the artifacts, and `eval_out/day32` shows this repo commits such outputs); `scripts/` may print terminal lines (existing pattern).
7. Secrets: scrub the API key from anything saved (helper `scrub()` in `help_eval.py`).

Pure functions (`build_run_record`, `render_patch_stats`, `check_clean`) make the test against a `respx`-mocked endpoint cheap; use a real temp git repo for the patch assertion.

### Anti-Patterns to Avoid
- **Python `re` on model-supplied patterns** (uninterruptible, can freeze the server) — use `git grep` with the timeout.
- **`git diff` without `add -N`** for the run patch — new files vanish from the patch.
- **`splitlines()` for diffs** — splits on form feed and Unicode separators, corrupts hunks.
- **Comparing `old_text` with raw bytes on CRLF files** — guaranteed mismatch on this machine.
- **Putting write tools in `@server.tool` at import** — then the gate is only a runtime check and `list_tools()` leaks writers.
- **`--literal-pathspecs` with `check-ignore`/glob grep** — fails or matches nothing.
- **Changing the global `MULTI_STEP_TOOL_HINT` first** — pinned to <= 170 words by `test_tool_guard.py` (currently 144 words) and mentions filesystem/GitLab; prefer tool descriptions.
- **Writing to `C:\Projects\AiAdventAgentV2`** during runs — always a clone (D-13).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Which files are visible / ignored | A `.gitignore` parser | `git ls-files --cached --others --exclude-standard`, `git check-ignore` | Nested ignores, negations, global excludes |
| Text search incl. binary skipping and timeout | Python walk + `re` | `git grep -n -I --untracked --exclude-standard` via `run_command` | ReDoS safety, binary detection, speed |
| Python symbol resolution | Regex over source | `ast` visitor | Comments/strings must not match (D-06) |
| Unified diff | Manual hunk builder | `difflib.unified_diff` | Correct hunks, context, `@@` headers |
| Run patch | Concatenating tool diffs | `git add -N . && git diff --binary` | Authoritative, applies with `git apply` |
| Path confinement | New path checks | Existing `resolve_inside_root` (+ the extra component rules above) | Already tested for traversal, drives, UNC, symlinks |
| Truncation | New markers | `fit_text` / `truncation_marker` | Same marker format app-wide (Phase 22 D-11/D-12) |
| Process timeout/kill | New subprocess code | `run_command` / `_kill_tree` | Handles the Windows git launcher child |
| Server registration & HTTP chat | New endpoints | `/api/v1/mcp/servers`, `/api/v1/chats/{id}/messages` | Phase's chat path is deliberately unchanged |

**Key insight:** every hard part (visibility, search safety, diff correctness, patch authority) already has a trustworthy tool in git or the stdlib; the code to write is glue, gating and formatting.

## Runtime State Inventory

Not a rename/refactor/migration phase — omitted. (Related note: the `project-files` entry is **user data** in the app DB table `McpServerConfig` created through the form/API at demo time; nothing is migrated, and `local-git` rows remain untouched.)

## Common Pitfalls

### Pitfall 1: Existing tests pin "five tools" (contradicts D-16 wording)
**What goes wrong:** `tests/test_mcp_project_git.py::test_exactly_five_tools_with_local_description` (sorted names == FIVE_TOOLS and every tool description starts with `LOCAL working copy (not the GitHub remote):`), `::test_cli_lists_five_tools` (`"Tools (5):"`), and `tests/test_docs_mcp_servers.py::test_user_guide_names_every_registered_tool` (`len(names) == 5`) fail as soon as `read_file`/`search_text`/`find_usages` are registered. `test_docs_mcp_servers.py::test_architecture_has_bundled_servers_subsection` and `docs/TESTING_GUIDE.md:626` ("exactly five tools") also describe the old count.
**Why it happens:** D-05 puts the read tools on the default (read-only) path.
**How to avoid:** Plan one explicit task "adapt the three assertions minimally": git five still present, description prefix asserted only for `git_*` tools, read-tools set asserted separately (8 tools without the flag; 10 with, over stdio), docs guard iterates over all registered tools. Record this as a deliberate, necessary exception to "unchanged" in the plan summary. Everything else (`/help` tests, `test_help_*`) is untouched: `/help` selects servers by id/name (`DEFAULT_MCP_SERVER_NAME = "local-git"`), not by tool count.
**Warning signs:** red tests named `*five*` right after adding the first tool.

### Pitfall 2: Card and log see only 4 000 characters
**What goes wrong:** `ws._tool_call_frame` cuts `result` to `TOOL_EVENT_PREVIEW_CHARS = 4000` (arguments 2 000), sets `truncated`. The same frames populate `HttpChatResponse.tool_calls`. A 60-line README diff is ~3-6 k chars -> the card shows a diff cut mid-line (no server marker inside the cut part), and `run.json` has a cut result and cut `write_file` arguments.
**How to avoid:** Put the summary line and diff headers first (already the design); do not raise the global constant (other tests/UI depend on it); the `.patch` is the complete record; the log stores the (cut) previews plus patch stats from git. UI: counts in the summary suffix (` · +A −R`) computed from the visible part are lower bounds when `evt.truncated` — parse the server's first-line `(+A -R)` instead when truncated, or omit the suffix then (UI-SPEC item 5 is silent; choose one and test it).
**Warning signs:** diff card ends abruptly without `(обрезано)` mismatch; log argument JSON is invalid because it was cut.

### Pitfall 3: Cards vanish after a full page reload
**What goes wrong:** `state.toolCallsByMessage` lives in JS memory only (`app.js` lines 44, 203, 1798); nothing persists tool-call frames server-side. UI-SPEC item 8 ("reloaded chat shows the same coloured diffs") is only true for switching chats within the session.
**How to avoid:** Do not promise persistence in docs/tests; verify via chat switch in E2E, not F5. Persisting cards is out of scope for this phase (flag to the user in the report if mentioned).

### Pitfall 4: Duplicate tools and noisy toolset
**What goes wrong:** Every enabled server contributes tools to every chat. With `local-git` and `project-files` both enabled, the model sees two sets of `git_*`, `read_file`, `search_text`, `find_usages`, differing only by prefix (`mcp__local-git__...` vs `mcp__project-files__...`); a 9B model may split calls randomly. Slug `project-files` (13 chars) and names stay below the 64-char limit; `MAX_MCP_TOOLS = 128` is not an issue.
**How to avoid:** In demo/E2E setups register only `project-files` (it exposes the five git tools too, so nothing is lost) or disable `local-git` for the run; the docs state this. The run script records the enabled-server list in `run.json` so duplicates are visible. A hint is optional (see Open Question 3).

### Pitfall 5: Weak-model loop heuristics
**What goes wrong:** `_run_tool_rounds` stops a round whose call signatures repeat the previous round (`tool_loop_detected`); `looks_like_action_announcement` can add one nudge when the final answer contains future-tense verbs («найду», «создам»); `MAX_TOOL_ROUNDS` counts rounds not calls.
**How to avoid:** Error texts of `edit_file`/`read_file` must instruct to change arguments (line range, more context). Paging with distinct `start_line` avoids loop detection. Budget estimate for the scenarios (to be measured): usages 4-8 rounds, ADR 7-11 (ARCHITECTURE.md is 71 k chars = 4+ read pages), README 6-10, CHANGELOG 3-5 (+ more if paging commits). 15 should suffice; `tests/test_tool_guard.py:345` pins `== 15`, so changing it also means editing that test.

### Pitfall 6: `git_recent_commits` is capped at 50
**What goes wrong:** Scenario 3 «CHANGELOG по истории git» sees at most 50 commits per call (repo has 1 191). A changelog "of the whole history" is not achievable in one call.
**How to avoid:** Word the scenario goal as «по последним N коммитам / с момента Day32» or let the model page with `ref=HEAD~50`; state the limitation honestly in the report.

### Pitfall 7: Tool results are large for the local model
**What goes wrong:** Default `--max-chars=18000` (~5 k tokens) per result times several reads fills small contexts; `read_file` of `docs/ARCHITECTURE.md` (71 k chars) is truncated every time.
**How to avoid:** `read_file` default window ~300 lines when `end_line` is omitted, marker advice "call again with start_line=N" (compute N from the last shown line, not just `NARROW_PATH_ADVICE`); optionally launch `project-files` with `--max-chars=12000`. Loaded LM Studio context is 131 072 for `qwen/qwen3.5-9b` [VERIFIED: LM Studio `/api/v0/models` 2026-10-17], but the chat's `context_length` setting (set by the script, 16 384 in `help_eval`) is what the app budgets against — set it explicitly to a larger value in `files_task.py`.

### Pitfall 8: Untracked files and git state in the clone
**What goes wrong:** After scenario 2, `docs/adr/0001-*.md` is untracked; the next scenario in the same clone shares state; a second run of the script would fail the clean check.
**How to avoid:** One fresh clone per scenario (`git clone --local --no-hardlinks <repo> <tmp>/<scenario>`; verified that a patch from one clone applies to a fresh clone). The clone carries `core.autocrlf` from global config, i.e. CRLF working files — another reason for the newline handling. git prints `LF will be replaced by CRLF` warnings on stderr; `run_command` keeps stderr separate, so they are harmless.

### Pitfall 9: `--max-chars`/timeout semantics for new commands
**What goes wrong:** `run_command` raises `ToolInputError("... timed out ...")` with a hint about `path`; for `git grep` the fix is a narrower `glob`/pattern. `HARD_CAP_BYTES` (4 MB) kills `git grep` on huge output and sets `capped` -> pass `lower_bound=True` to the marker like existing tools.
**How to avoid:** Use `label="git grep"`, and customise the advice strings.

## Code Examples

### Visibility helper and gate sketch
```python
# Source: experiments on git 2.39.1 (see Pattern 2/6); structure follows existing helpers in project_git.py
async def visible_paths(rel: str | None = None) -> list[str]:
    args = ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    if rel is not None:
        args += ["--", rel]
    result = await run_git(args)          # literal pathspecs stay ON for this command
    if result.returncode != 0:
        raise _git_error(result, "ls-files")
    return [p for p in result.text.split("\0") if p]

async def is_git_ignored(rel: str) -> bool:
    cfg = get_config()
    argv = [a for a in base_argv(cfg.git) if a != "--literal-pathspecs"] + ["check-ignore", "-q", "--", rel]
    res = await run_command(argv, cwd=cfg.root, env=git_env(), timeout=cfg.timeout, label="git check-ignore")
    if res.returncode in (0, 1):
        return res.returncode == 0
    raise _git_error(res, "check-ignore")
```

### Run patch (script)
```bash
# Source: verified experiment — new + modified files, patch applies to a fresh clone (git apply --check rc 0)
git -C "$ROOT" add -N .
git -C "$ROOT" diff --binary > run.patch
```

### Tool descriptions (English, <= ~600 chars each; the app prefixes `[MCP server: project-files] ` and truncates at 1024)
`edit_file` description should end with: "After all changes are done, list every file you created or changed in your final answer." — this implements D-11's final-answer rule without touching the global hint.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| External `filesystem.exe` MCP server for files | Own self-contained server with an `--allow-write` gate | Phase 22 precedent, Phase 26 decision D-04/D-05 | Per-tool gating is impossible in the app, so the gate is a server launch flag |

**Deprecated/outdated:** none relevant (stdlib and pinned `mcp` APIs are current).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | NTFS aliases of `.git` (`.git.`, `.git `, `GIT~1`) and Windows device names must be rejected in the write gate | Pattern 6 | If wrong, the rules are merely over-strict (harmless); if the aliases are real and skipped, a write could land in `.git/` on Windows |
| A2 | AST parsing of the repo's Python files finishes in a few seconds | Pattern 4 | A slow scan could hit the app's 30 s `MCP_TOOL_CALL_TIMEOUT`; mitigated by the suggested time budget — measure in plan |
| A3 | 15 tool rounds suffice for the four scenarios with qwen3.5-9b | Pitfall 5 | Scenario fails with capped rounds; measure on real runs (CONTEXT already allows raising) |
| A4 | A write-size limit of ~200 kB and a default `read_file` window of ~300 lines are reasonable | Patterns 5/Pitfall 7 | Tuning only |
| A5 | Qwen 9B follows "list changed files in the final answer" from a tool description alone | Code Examples | If it does not, add one sentence to the hint (<= 26 spare words) or restate the goal in the scenario prompts |

## Open Questions

1. **Which assertions of the three "five tools" tests may change?**
   - Known: D-16 says existing tests stay green unchanged, D-05 forces extra tools into the default server; the tests are listed in Pitfall 1.
   - Unclear: whether the user accepts the minimal edit as satisfying D-16's intent (the git tools' behaviour tests all stay as they are).
   - Recommendation: make the minimal edit, flag it in the plan summary and the final report; no alternative preserves D-05.
2. **Branch base for `Day34`.**
   - Known: `main` = merge of Day32; `Day33` has only docs commits on top (verified: the non-`.planning` diff vs `main` is just `.gitkeep` files); the Phase 26 context/UI-SPEC commits (`48fe7cf`, `2d77327`, `6e6be7f`) and the working tree changes in `.planning/` live on `Day33`.
   - Recommendation: create `Day34` from `main` and cherry-pick the three Phase 26 doc commits (skip the STATE.md commit `fb8b374` or resolve it), keeping Phase 25 docs out; if conflict handling is unwanted, branching from current `Day33` HEAD is harmless (docs only) but merges Phase 25 docs into main early. Planner/user decides.
3. **Is a system-prompt hint needed?**
   - Known: `MULTI_STEP_TOOL_HINT` is generic, 144 words with a 170-word test ceiling, mentions filesystem/GitLab.
   - Recommendation: start without a change; descriptions carry the rules (read before edit, unique `old_text`, list changed files). Add a <= 25-word sentence only if the measured scenarios show premature stops.
4. **Where do `eval_out/day34` artifacts live — tracked or ignored?**
   - Known: `eval_out/` is tracked in git (day22..day32 exist); CONTEXT suggests an ignored folder.
   - Recommendation: tracked `eval_out/day34/`, because the report cites each log and patch; strip secrets (`scrub`).
5. **Card persistence after page reload** (Pitfall 3) — out of scope, but the UI-SPEC text promises it; the plan should word the acceptance check as "chat switch" and note the gap.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | — |
| git | server, script, clone | yes | 2.39.1.windows.1 (`core.autocrlf=true`) | — |
| `mcp` SDK | server, tests | yes | 1.30.0 | — |
| pytest, pytest-asyncio, respx | tests | yes | installed | — |
| Playwright (python) | E2E browser script | yes (import works) | not queried | record E2E as "not run" if browsers missing |
| LM Studio | scenario runs for the report | yes, `qwen/qwen3.5-9b` loaded (ctx 131072) | Q6_K | if down at run time: record "not run" (D-14) |
| Ports 18000/18001 | isolated copy | free (only 8000/8001 listening) | — | script preflight blocks otherwise |
| User's app on 8000/8001 | must NOT be touched | running (PIDs seen) | — | never kill it |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** none at research time (LM Studio availability must be re-checked at run time).

## Validation Architecture

`workflow.nyquist_validation` is `false` in `.planning/config.json`, so the formal section is skipped; the test map below is the minimum the planner should schedule (framework: pytest 8 + pytest-asyncio auto mode, config `pytest.ini`, full suite `pytest tests/ -v`).

| Req | Behavior | Test type | Command | Exists |
|-----|----------|-----------|---------|--------|
| PFILE-01 | read_file found/not found/range/truncation/ignored/untracked-visible; search_text -F/-E/glob/bad regex/no hits/truncation; find_usages kinds, comment/string not matched, syntax-error file skipped | unit (temp git repo fixtures from `test_mcp_project_git.py`) | `pytest tests/test_mcp_project_files.py -x` | new |
| PFILE-02 | no writer in `server.list_tools()` by default; over stdio with `--allow-write` exactly two more tools; direct call without flag raises | unit + stdio (`_session` helper) | same | new |
| PFILE-03/04 | write_file new/overwrite/identical, edit_file 0/1/N matches, CRLF file, diff shape (`--- /dev/null`), `.git/`, `.env*`, ignored, binary, oversize, outside root, NTFS alias, reserved name | unit | same | new |
| PFILE-06 | app.js contains the renderer, no `innerHTML` with diff text, `isUnifiedDiff` rule; E2E `page.evaluate(buildToolCallCard({...}))` checks classes for added/removed/hunk and the `-- ` removed-line case | source guard + browser | `pytest tests/test_diff_card_ui.py` / e2e script | new |
| PFILE-07 | clean-root refusal, log fields, patch includes a new file and applies to a fresh clone, 409/503 -> not-run exit | unit with respx + temp repo | `pytest tests/test_files_task.py` | new |
| PFILE-08 | e2e script on 18000/18001 with clone `--root` and `--allow-write`; asserts a diff card and a changed clone | e2e | `python scripts/e2e_project_files_playwright.py` | new |
| PFILE-10 | docs guard: tools named in USER_GUIDE, `project-files` values present, TESTING_GUIDE lists new files | unit | `pytest tests/test_docs_mcp_servers.py` | edit |
| Regression | `test_mcp_project_git*.py`, `test_help_*`, `test_tool_guard.py`, `test_multistep_tools_ws.py` | unit | `pytest tests/ -x -q` | existing (three assertions edited, see Pitfall 1) |

Wave 0: none beyond creating the new test files; fixtures can be copied from `test_mcp_project_git.py` (`_make_repo`, `_configure`, `_session`, `_git` with `core.autocrlf=false` — add a dedicated CRLF fixture).

## Security Domain

`security_enforcement` is not disabled in config (absent = enabled).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (no new auth; the script uses the existing API key) | existing Bearer API keys |
| V3 Session Management | no | — |
| V4 Access Control | yes | root confinement (`resolve_inside_root`), `--allow-write` flag, forbidden targets (.git, .env*, ignored) |
| V5 Input Validation | yes | `ToolInputError` on pattern/glob/symbol/path/size; reject control chars; reject glob magic `:`; bounded sizes |
| V6 Cryptography | no | — |
| V12 Files and Resources | yes | atomic writes inside root, size limit, binary refusal, no symlink escape (realpath) |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal / symlink escape on write | Tampering | `resolve_inside_root` (realpath + commonpath), tests exist for `..`, drives, UNC, link leaving root |
| Write into `.git/` (hooks => code execution on next git command) incl. NTFS aliases | Tampering / EoP | component rules (`.git`, trailing dot/space, `GIT~N`), case-insensitive |
| Secret exfiltration / overwrite (`.env`, ignored DBs) | Info disclosure / Tampering | refuse read (not visible) and write (`.env*`, ignored) |
| Prompt injection from repository file contents steering the model to write | Tampering | blast radius limited by root (a disposable clone, D-13), no delete/shell tools, diffs visible in cards, undo via git |
| Catastrophic regex from the model | DoS | `git grep` under `--timeout` with process-tree kill; pattern length cap |
| Option injection through `pattern`/`glob`/`path` | Tampering | `-e` for patterns, `--` before pathspecs, existing leading-`-` rejection, reject glob starting with `:` |
| XSS via file content shown in the diff card | Tampering | `createElement` + `textContent` only (UI-SPEC), source-guard test |
| Oversized write / disk fill | DoS | `MAX_WRITE_BYTES` |
| API key leakage into logs/artifacts | Info disclosure | `scrub()` before saving `run.json`; key via env/arg never written |

## Sources

### Primary (HIGH confidence)
- Repository code read in this session: `mcp_servers/project_git.py`, `agent/ws.py` (`_run_tool_rounds`, `_tool_call_frame`, `_load_mcp_toolset`, toolset/hint assembly), `agent/tool_guard.py`, `agent/http_chat.py`, `agent/mcp_tools.py` (naming, 20 000-char cap), `agent/schemas.py` (`ToolCallEvent`, `HttpChatRequest/Response`, `McpServerResponse`), `ui/static/app.js` (`buildToolCallCard`, `toolCallsByMessage`), `scripts/help_eval.py`, `scripts/e2e_mcp_git_playwright.py`, `scripts/mcp_list_tools.py`, `tests/test_mcp_project_git*.py`, `tests/test_docs_mcp_servers.py`, `docs/USER_GUIDE.md` (local-git section), `docs/API_SPEC.md` (API keys), `.planning/REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, `26-CONTEXT.md`, `26-UI-SPEC.md`
- Experiments run 2026-10-17 on git 2.39.1.windows.1 / Python 3.13.15: `ls-files --cached --others --exclude-standard`, `git grep` (-F/-E/-P, pathspec behaviour, bad-regex exit code), `check-ignore` (incl. `--literal-pathspecs` failure), `add -N` + `diff` + `apply --check` on a clone, `difflib` output shape, `FastMCP.add_tool` signature, LM Studio `/api/v0/models`
- Python `ast` documentation [CITED: https://docs.python.org/3/library/ast.html]

### Secondary (MEDIUM confidence)
- none beyond the above (no web search was needed; all questions were answerable from the repo and local tools)

### Tertiary (LOW confidence)
- NTFS `.git` alias and reserved-name behaviour (A1) — from general knowledge of git's Windows hardening, not re-verified in this session

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; every tool exercised locally
- Architecture: HIGH — chat path and card/frame caps read from source; server patterns tested
- Pitfalls: HIGH for items verified in code (five-tools tests, 4000-char frame cap, card persistence, literal-pathspecs); MEDIUM for model-behaviour items (rounds budget, instruction following)

**Research date:** 2026-10-17
**Valid until:** 2026-11-17 (stable stack; re-check LM Studio model and repo test pins if the branch base changes)
