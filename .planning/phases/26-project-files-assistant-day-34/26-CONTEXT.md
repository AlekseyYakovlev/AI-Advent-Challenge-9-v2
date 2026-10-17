# Phase 26: Project files assistant (Day 34) - Context

**Gathered:** 2026-10-17
**Status:** Ready for planning

<domain>
## Phase Boundary

An assistant that works with the project's files on its own: the user states a goal in an ordinary chat (not "open file X"), and the model reads, searches across several files, analyses them and creates or changes files through MCP tools. Every change is saved to the working copy and shown as a diff, and a run can be repeated from a script with a recorded log and patch. Scenarios: find every usage of a component or API, and generate a new file (README / ADR / changelog). Branch `Day34`; the course deliverable is a video plus code, and the user records the video.

In scope: read, search and write tools added to the own git MCP server `mcp_servers/project_git.py` (writes only behind a launch flag); a coloured diff view in the chat's tool-call cards; a run script over the existing plain HTTP chat endpoint that records a log and a `.patch`; pytest; an isolated-copy end-to-end script; `Day34_report.md` with the four scenarios on the local model; documentation updates.

The roadmap has no requirement IDs for this phase yet; the planner defines them (suggested prefix `PFILE-`) from the decisions below.

Out of scope here: a new slash command (`/files`) or a new Settings section for it; git write tools such as stage, commit, branch, push (PGIT-05 stays deferred); file deletion or rename; an "Apply" button or any confirm-before-write flow; semantic (RAG) search over code for this assistant; a judge model or a two-model comparison; any change to `/help`, `/support` or the PR review behaviour.

</domain>

<decisions>
## Implementation Decisions

### Entry point
- **D-01:** There is no new command. The user gives the goal as an **ordinary chat message** in a chat where the file tools are available; the existing tool loop (`agent/ws.py::_run_tool_rounds`, `MAX_TOOL_ROUNDS`) does the multi-step work, and every call is visible as an ordinary tool-call card.
- **D-02:** The same goal can be run **from the command line**: a new script (expected `scripts/files_task.py`) sends the goal to the existing plain HTTP chat endpoint (`agent/http_chat.py`) with an API key, so the chat path and the script path run the same code.
- **D-03:** The project root is the server's `--root` launch argument in the «MCP серверы» form, as for `local-git` (Phase 22 D-02). No root setting is added elsewhere. (User left this to Claude; chosen for consistency with Phase 22.)

### File tools (extension of the local-git server)
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

### Writes and diff
- **D-09:** A write call **saves to disk immediately**, and its tool result is the **unified diff of that change** (a new file appears as an all-added diff). Undo is through git. There is no dry-run mode and no confirmation step.
- **D-10:** Writes are rejected (with `isError` and a reason) for: paths outside the root (existing path gate), anything under `.git/`, `.env*` files, paths git ignores, binary files, and content above a size limit.
- **D-11:** In the chat, a tool-call card whose result is a unified diff renders it **as a coloured diff** (added lines green, removed lines red, hunk headers marked) — a change in `ui/static/app.js` card rendering, sanitised as all inserted HTML (DOMPurify). Other cards stay as they are. The model ends its answer by listing the files it created or changed.

### Reproducibility and verification
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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope
- `.planning/ROADMAP.md` — Phase 26 goal and dependency on Phase 22; Phase 27 depends on this phase's file generation
- `.planning/REQUIREMENTS.md` — PGIT-05 (git write tools) stays deferred; v5.0 out-of-scope rows

### The server being extended
- `.planning/phases/22-project-mcp-servers-day-31/22-CONTEXT.md` — local-git design decisions (D-01..D-15: root, path gate, truncation marker, timeouts, error style) that the new tools must follow
- `mcp_servers/project_git.py` — the server: path gate, process runner, truncation helpers, tool registration
- `tests/test_mcp_project_git.py`, `tests/test_mcp_project_git_core.py` — existing tests that must stay green and the pattern for new ones
- `docs/USER_GUIDE.md` §«MCP серверы» / «Сервер git проекта (local-git)» — documented form values; the `project-files` entry goes next to it

### Chat path, HTTP path and cards
- `agent/ws.py` (`_run_tool_rounds`, ordinary-turn tool assembly around `_load_mcp_toolset`) — the multi-step tool loop the assistant runs on
- `agent/tool_guard.py` — `MAX_TOOL_ROUNDS`, `MULTI_STEP_TOOL_HINT`
- `agent/mcp_tools.py` — tool naming `mcp__<server>__<tool>` and toolset building
- `agent/http_chat.py` — plain HTTP chat endpoint the run script calls
- `ui/static/app.js` (`buildToolCallCard`, `appendToolCallCard`) — card rendering to extend with the diff view
- `.planning/phases/23-help-command-verification-and-report-day-31/23-CONTEXT.md` — `/help` uses `local-git` by default; it must stay read-only

### Verification patterns
- `scripts/e2e_mcp_git_playwright.py` — isolated-copy browser script with the git server, the model for the new end-to-end script
- `Day32_report.md` — report format and honest-recording style

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mcp_servers/project_git.py`: path gate (rejects paths outside the root and arguments starting with `-`), fixed-command git runner with timeout, truncation with an actionable marker, `ToolInputError` → `isError` — the new tools reuse all of these.
- The tracked-files tool already lists files (with an optional `path`); `git ls-files` gives the read/search scope of D-07.
- `agent/code_chunking.py`: existing `ast`-based Python splitting from Phase 24 — a reference for `find_usages` (the server itself cannot import it; it stays self-contained).
- `scripts/mcp_list_tools.py`: lists a server's tools; useful to show the tool set with and without `--allow-write`.
- `scripts/pr_review_ci.py` / `scripts/help_eval.py`: existing scripts that call the app over HTTP with an API key — a pattern for `scripts/files_task.py`.

### Established Patterns
- MCP servers are separate stdio processes configured in the «MCP серверы» form; the same script can be added twice with different arguments and names.
- Tool results are capped at `MCP_TOOL_RESULT_MAX_CHARS` (20 000) by the app; the server's own limit sits below it (Phase 22 D-11).
- Tool calls in one turn run strictly sequentially (STATE.md decision), so a read-then-edit sequence is safe.
- Isolated-copy checks on ports 18000/18001 with a scratch database; the user's app on 8000/8001 is never touched.

### Integration Points
- `mcp_servers/project_git.py` — new tools and the `--allow-write` flag.
- `ui/static/app.js` — diff rendering inside tool-call cards.
- `scripts/files_task.py` (new) — run script over `agent/http_chat.py`.
- `docs/` + `README.md` + the docs guard test (`tests/test_docs_mcp_servers.py`).

</code_context>

<specifics>
## Specific Ideas

- The goal is phrased at the level of intent («найди все использования…», «напиши ADR…»), and the assistant itself decides which files to list, search and read.
- The CHANGELOG scenario is deliberately chosen so Phase 27 (AI release pipeline) can build on it.
- The video shows the chat with tool-call cards and coloured diffs; the report shows the same scenarios run from the script with logs and patches.

</specifics>

<deferred>
## Deferred Ideas

- A `/files` slash command with its own prompt, label and Settings section (offered, not chosen — D-01).
- Confirm-before-write: proposed edits applied with an «Применить» button, or a `--dry-run` mode that only shows the diff (offered, not chosen — D-09).
- A unified «Изменения» block under the answer collected by the app (offered, not chosen — D-11).
- Semantic search over the code base from Phase 24 as an extra tool for this assistant.
- File delete/rename tools; git write tools (PGIT-05).
- A two-model run with a judge for Day 34.

</deferred>

---

*Phase: 26-project-files-assistant-day-34*
*Context gathered: 2026-10-17*
