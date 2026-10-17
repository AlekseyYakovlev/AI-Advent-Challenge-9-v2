# Phase 26: Project files assistant (Day 34) - Pattern Map

**Mapped:** 2026-10-17
**Files analyzed:** 14 new/modified
**Analogs found:** 14 / 14 (two are role-match only)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `mcp_servers/project_git.py` (modify: `read_file`, `search_text`, `find_usages`) | MCP tool (read) | request-response, file-I/O | `git_tracked_files` / `git_recent_commits` in same file (L536-609) | exact |
| `mcp_servers/project_git.py` (modify: `write_file`, `edit_file`, `--allow-write`, `ServerConfig.allow_write`) | MCP tool (write) + config gate | request-response, file-I/O | `configure`/`parse_args`/`main` (L52-105, L639-656) + `resolve_inside_root` (L237) | role-match (no writer exists) |
| `ui/static/app.js` (modify `buildToolCallCard`, add `isUnifiedDiff`/`classifyDiffLine`/`buildDiffBlock`) | component | transform (WS frame to DOM) | `buildToolCallCard` (L1477-1505) | exact |
| `scripts/files_task.py` (new) | CLI script | request-response (HTTP), batch | `scripts/help_eval.py` (`create_chat` L801, `send_help` L822, `scrub` L170, `_git` L178, `report` L101) | role-match |
| `scripts/e2e_project_files_playwright.py` (new) | e2e script | event-driven (browser) | `scripts/e2e_mcp_git_playwright.py` | exact |
| `tests/test_mcp_project_files.py` (new) | test | request-response | `tests/test_mcp_project_git.py` (fixtures L50-125, `_session` L459) | exact |
| `tests/test_files_task.py` (new) | test | request-response (respx) | `tests/test_help_eval.py` (importlib load L12-20) | role-match |
| `tests/test_diff_card_ui.py` (new) | test (source guard) | transform | `tests/test_api_keys_ui.py` (`_function_source` L40-47) | exact |
| `tests/test_mcp_project_git.py` (edit 3 "five tools" asserts) | test | - | itself, L143, L482 | exact |
| `tests/test_docs_mcp_servers.py` (edit L71 `len(names) == 5`, add `project-files` pins) | test | - | itself | exact |
| `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md` (L626 "exactly five tools"), `README.md` | docs | - | existing `local-git` section | exact |
| `Day34_report.md` | docs/report | - | `Day32_report.md` | exact |
| `eval_out/day34/<scenario>/run.json, run.patch, answer.md` | artifacts | file-I/O | `eval_out/day32/` | exact |
| `.planning/REQUIREMENTS.md` PFILE-01..10 | planning | - | existing requirement rows | exact |

## Pattern Assignments

### `mcp_servers/project_git.py` - read tools (tool, request-response)

**Analog:** `git_tracked_files` (L536-571) and `git_recent_commits` (L574-609) in the same file.

**Imports/constants pattern** (L12-35): stdlib, then `psutil`, `mcp.server.fastmcp.FastMCP`; module constants in UPPER_CASE with type hints. Add `ast`, `difflib`, `fnmatch`, `time`, `tempfile` as stdlib. No `print(` and no `import shared|agent` (pinned by `tests/test_mcp_project_git_core.py::test_module_source_is_clean`, L171).

**Tool registration pattern** (L536-545):
```python
@server.tool(structured_output=False)
async def git_tracked_files(path: str | None = None) -> str:
    """LOCAL working copy (not the GitHub remote): list files tracked by git.

    Parameter: `path` (optional) ...
    """
    max_chars = get_config().max_chars
    rel = _normalise_path(path)
    result = await run_git(["ls-files", "--", rel] if rel is not None else ["ls-files"])
    if result.returncode != 0:
        raise _git_error(result, "ls-files")
```

**Truncation/summary pattern** (L553-571, L606-609): first line is a summary (`_plural(...)`), body, then `fit_text(summary, body, max_chars, lower_bound=result.capped)` or `truncation_marker(shown, total, unit="files", advice=...)`. Never return an empty string (L551-552, L605 return explicit "No ..." text).

**Error pattern:** raise `ToolInputError("<English reason + fix>")`; git failures via `_git_error(result, "<cmd>")` (L394-398).

**New-helper guidance (from RESEARCH, copy structure from `run_git` L225-234):**
```python
async def is_git_ignored(rel: str) -> bool:
    cfg = get_config()
    argv = [a for a in base_argv(cfg.git) if a != "--literal-pathspecs"] + ["check-ignore", "-q", "--", rel]
    res = await run_command(argv, cwd=cfg.root, env=git_env(), timeout=cfg.timeout, label="git check-ignore")
```
`run_command(argv, cwd=, env=, timeout=, label=)` is the only subprocess entry (L182) - reuse for `git ls-files --cached --others --exclude-standard -z` (visibility) and `git grep -n -I --untracked --exclude-standard -F|-E -e <pattern> -- <glob>` (search; omit `--literal-pathspecs` from `base_argv`, rc 1 = no hits). Path gate: `_normalise_path` / `resolve_inside_root(root, raw)` (L237-264). `find_usages`: `ast.NodeVisitor` recipe in RESEARCH Pattern 4; stdlib only (server cannot import `agent/code_chunking.py`).

### `mcp_servers/project_git.py` - write tools and `--allow-write` gate

**Analog:** `ServerConfig`/`configure`/`parse_args`/`main` for the gate; `resolve_inside_root` for the path gate; `fit_text` for result framing.

**Config pattern** (L52-70): add `allow_write: bool = False` to the frozen `ServerConfig` and a defaulted `allow_write` param to `configure(...)` so every existing 4-arg call site (`_configure` in `tests/test_mcp_project_git.py` L100) keeps working:
```python
@dataclass(frozen=True)
class ServerConfig:
    root: str; git: str; max_chars: int; timeout: float
def configure(root: str, git: str, max_chars: int, timeout: float) -> None:
    global _config
    _config = ServerConfig(root=root, git=git, max_chars=max_chars, timeout=timeout)
```

**CLI flag pattern** (L80-105): `parser.add_argument("--allow-write", action="store_true", help=...)`; `allow_abbrev=False` is already set (L85).

**Conditional registration** (main, L655-656): write tools are plain `async def` (no `@server.tool`) and registered only when the flag is set:
```python
configure(root, git, args.max_chars, args.timeout, allow_write=args.allow_write)
if args.allow_write:
    server.add_tool(write_file, structured_output=False)
    server.add_tool(edit_file, structured_output=False)
server.run(transport="stdio")
```
Each writer also calls a `require_write()` guard that raises `ToolInputError` (defence in depth, makes direct-call tests meaningful). Update the module docstring (L1) and `parser description` ("Read-only") accordingly.

**Result pattern:** summary line (`Created path (+A -R)`) + `difflib.unified_diff` text via `fit_text(summary, diff, max_chars, advice="The file was saved in full; call read_file to review it.")`. Identical content returns "No changes: ..." (never empty). CRLF: match/diff on LF-normalised text, write back in the file's style; atomic write via temp file + `os.replace`. Forbidden-target rules (`.git` component incl. NTFS aliases, `.env*`, `is_git_ignored`, binary, size cap, reserved names) are applied to the `resolve_inside_root` result.

### `ui/static/app.js` - diff card (component, transform)

**Analog:** `buildToolCallCard` (L1477-1505) and `appendToolCallCard` (L1514-1524).

**Core pattern to extend** (L1496-1503) - only the result `<pre>` is swapped; summary, args, labels stay:
```javascript
const resultPre = document.createElement('pre');
resultPre.className = 'whitespace-pre-wrap break-all';
resultPre.textContent = evt.result || '';
card.append(argsLabel, argsPre, resultLabel, resultPre);
```
Change: `const body = (evt.ok && isUnifiedDiff(evt.result || '')) ? buildDiffBlock(evt.result) : resultPre;` and optional ` · +A −R` suffix via `summary.textContent` (parse the server's first-line `(+A -R)` when `evt.truncated`). Build the block with `createElement` + `textContent` only (no `innerHTML` for diff text); classes per 26-UI-SPEC "Diff Block Specification". Classifier: hunk-aware state machine (RESEARCH Pattern 8) so a removed `-- x` line is not coloured as a header. Error cards (`evt.ok` false) never render as diff.

**Security pattern in repo:** innerHTML only through `renderMarkdown` (L1473); new code must not add another path.

### `scripts/files_task.py` (CLI script, HTTP request-response + git batch)

**Analog:** `scripts/help_eval.py` (role-match; reuse functions by copying, not importing the 1240-line module).

**Terminal output** (L101-103): scripts may print via a `report()` helper:
```python
def report(message: str) -> None:
    print(message, flush=True)
```
**Secret scrubbing** (L170-175): `scrub(text, secrets_list)` before saving `run.json`.

**git helper** (L178-186): list-argv `subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=20)`, no shell.

**Chat creation + settings (temperature 0)** (L801-819): `POST /api/v1/chats` then `PUT /api/v1/settings` with `{chat_id, temperature, max_tokens, context_length}`; use the file-task values (temperature 0, larger context).

**Send message with API key** (L822-848):
```python
response = await http.post(
    f"{kb.AGENT_URL}/api/v1/chats/{chat_id}/messages",
    json={"content": goal, "model": target["model"], "provider_id": target["provider_id"]},
    headers={"Authorization": f"Bearer {api_key}"},
)
```
Returns `(body, elapsed, error_text, status)`; map 409/429/503 to a "not run/blocked" exit code, never "passed". Provider/model discovery: `provider_models` (L784). Then `git add -N .` and `git diff --binary > run.patch` on the root; `run.json` with goal, commit, provider/model, settings, MCP server list, tool_calls (frames are capped at 4000/2000 chars), answer. Use pure functions (`build_run_record`, `check_clean`, `render_patch_stats`) so tests need no network. Refuse a dirty root; refuse the script's own repo without `--allow-own-repo`.

### `scripts/e2e_project_files_playwright.py` (e2e, event-driven)

**Analog:** `scripts/e2e_mcp_git_playwright.py` (365 lines) - copy structure: module docstring with usage/exit codes (L1-15), `sys.path.insert` + `import e2e_kb_playwright as kb` (L33-37), constants for ports 18000/18001 (L40-42), exit codes `EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_NO_PLAYWRIGHT = 0, 1, 2, 4` (L55), `pick_chat_model`/`preflight`/`blocked_exit` (L59-92), `make_temp_repo` (L116) -> replace with `git clone --local --no-hardlinks` of the repo as the write root, `add_server` (L140) -> pass `--allow-write` and name `project-files`, `ask`/`card_names_the_tool` (L201, L226), `scenario`/`drive_browser`/`run_e2e` (L231-347). Assert a diff card rendered and the clone changed; verify card persistence via chat switch, not F5.

### `tests/test_mcp_project_files.py` (test, request-response)

**Analog:** `tests/test_mcp_project_git.py`.

**Fixtures** (L50-125): `_clean_env`, `_git(repo, *args)` with `core.autocrlf=false`, `_make_repo`, `_configure(repo, max_chars)`, `@pytest.fixture git_repo/clean_repo/empty_repo`; `pytestmark = pytest.mark.skipif(shutil.which("git") is None, ...)` (L27); `_ok(call)` helper asserting non-empty and `<= max_chars` (L127-132). Add a dedicated CRLF fixture and a fixture with `.gitignore`d `.env`/`app.db`.

**Rejection test pattern** (L411-422):
```python
@pytest.mark.parametrize("call", [lambda: project_git.git_diff(path="../x"), ...])
async def test_bad_arguments_raise(git_repo, call):
    with pytest.raises(project_git.ToolInputError):
        await call()
```
**Stdio gate test** (L455-474): `_session(args)` using `open_session(sys.executable, [SERVER, *args], env, cwd)`; assert tool list lacks `write_file/edit_file` without `--allow-write` and has exactly those two extra with it. `_text(result)` joins content parts. For direct-call gate test: `configure(...)` without `allow_write` then `pytest.raises(ToolInputError)`.

### `tests/test_files_task.py` (test)

**Analog:** `tests/test_help_eval.py` L12-20 (load a script by `importlib.util.spec_from_file_location`, register in `sys.modules`, `exec_module`). Use `respx` to mock `/api/v1/chats`, `/settings`, `/messages`; real temp git repo for patch assertions (new file appears; `git apply --check` on a fresh clone).

### `tests/test_diff_card_ui.py` (test, source guard)

**Analog:** `tests/test_api_keys_ui.py`: `_raw_app_js()` (L14-16), `_app_js_source()` stripping `//` comment lines (L19-23), `_function_source(src, name)` regex (L40-47). Assert `buildDiffBlock` body has no `innerHTML`, uses `textContent`, `isUnifiedDiff` exists, and the `evt.ok` condition.

### Edits to existing tests (deliberate exception to D-16 "unchanged")

- `tests/test_mcp_project_git.py` L143-147 `test_exactly_five_tools_with_local_description`: assert `FIVE_TOOLS` subset, `LOCAL working copy` prefix only for `git_*` tools, read tools set separately.
- `tests/test_mcp_project_git.py` L482-490 `test_cli_lists_five_tools`: relax `"Tools (5):"` (8 without flag, 10 with).
- `tests/test_docs_mcp_servers.py` L71 `assert len(names) == 5`: iterate all registered names; add `project-files` form-value pins; `docs/TESTING_GUIDE.md:626` text.

## Shared Patterns

### Path confinement and argument validation
**Source:** `mcp_servers/project_git.py::resolve_inside_root` (L237-264), `_normalise_path` (L406-410)
**Apply to:** every new tool taking a path/glob/symbol. Reject leading `-`, control chars, drives/`:`, escapes; add glob rules (no leading `:`, no `..`).

### Truncation and non-empty results
**Source:** `truncation_marker` (L309), `fit_text` (L333), `_cut_at_line` (L322)
**Apply to:** `read_file`, `search_text`, `find_usages`, write results. Pass `lower_bound=result.capped` for capped subprocess output; custom `advice` (e.g. "call again with start_line=N").

### Error style
**Source:** `ToolInputError` (L38), `_git_error` (L394)
**Apply to:** all tools; English reason plus fix; error texts must say to change the arguments (loop detector `tool_loop_detected`).

### Subprocess execution
**Source:** `run_command` (L182), `run_git` (L225), `git_env` (L115), `base_argv` (L127)
**Apply to:** `git grep`, `ls-files`, `check-ignore`; never `os.fork`/multiprocessing; no new subprocess code.

### Stderr-only diagnostics
**Source:** `fail` (L108-112), `main` warning (L649-654) using `sys.stderr.write`
**Apply to:** the server file (no `print`, no `shared` imports).

### Scripts: terminal output, secrets, git
**Source:** `scripts/help_eval.py` `report` L101, `scrub` L170, `_git` L178
**Apply to:** `files_task.py`, the e2e script.

### Isolated copy
**Source:** `scripts/e2e_mcp_git_playwright.py` (ports 18000/18001, scratch DB, never touch 8000/8001)
**Apply to:** e2e script and the report runs; write root is a separate clone, never `C:\Projects\AiAdventAgentV2`.

## Conventions

Scope derivation could not run (`gsd-tools.cjs` not resolved: `CLAUDE_PLUGIN_ROOT` unset and plugin cache absent), so convention derivation skipped (module path not found). Conventions taken from CLAUDE.md and the read files:

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case (`project_git.py`, `test_<module>.py`) | ~100% (observed) | low | named contract |
| Identifier casing | snake_case funcs, UPPER_CASE constants, PascalCase classes (JS: camelCase) | ~100% per language | low | named contract |
| Export style | Python module-level defs, direct imports; JS top-level `function` | n/a | low | named contract |
| Import style | stdlib, third-party, local; absolute from project root | high | low | named contract |

Contested hotspots (author's choice): the CJS<->SDK dual resolver (`bin/lib/**` CJS `module.exports`/`require`; `sdk/src/**` ESM `export`/`import`) is the prototype intentional split, not present in this repo. Here the analogous per-directory split is Python server code (self-contained, stderr only, no `shared/` imports, `mcp_servers/`) versus app code (structlog, `shared/` imports) and scripts (may `print` via `report()`); match the local style of the directory being edited.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| write gate in `project_git.py` (`edit_file`/`write_file`) | tool | file-I/O write | Server has no writer yet; use config/`resolve_inside_root`/`fit_text` structure plus RESEARCH Patterns 5-7 |
| `find_usages` AST visitor | tool | transform | No `ast` use in the server; use RESEARCH Pattern 4 (reference only: `agent/code_chunking.py`, not importable) |
| Diff classifier in `app.js` | util | transform | No diff rendering exists; use RESEARCH Pattern 8 and 26-UI-SPEC |

## Metadata

**Analog search scope:** `mcp_servers/`, `scripts/`, `tests/`, `ui/static/app.js`
**Files scanned:** about 10 read in full or in ranges (project_git.py, app.js card region, help_eval.py helpers, e2e script structure, test fixtures)
**Pattern extraction date:** 2026-10-17
