---
phase: 22-project-mcp-servers-day-31
reviewed: 2026-10-13T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - mcp_servers/__init__.py
  - mcp_servers/project_git.py
  - scripts/e2e_mcp_git_playwright.py
  - tests/test_docs_mcp_servers.py
  - tests/test_mcp_project_git.py
  - tests/test_mcp_project_git_core.py
  - tests/test_mcp_real_github.py
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 22: Code Review Report

**Reviewed:** 2026-10-13
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

The path and ref gates in `project_git.py` hold up. I found no traversal, argument-injection or option-looking-ref bypass.

- Paths are rejected if they are empty, start with `-`, contain control characters, or contain `:` or a drive-relative prefix. They are then realpath-resolved and checked with `commonpath`, so symlink escapes are caught. They reach git only after `--`, combined with `--literal-pathspecs`.
- Refs are rejected if they start with `-` or contain whitespace, control characters or `\`. They are resolved with `rev-parse --end-of-options` and `^{commit}`, and only a full hash reaches `diff` and `log`.
- The subprocess runs with an argv list, no shell, a scrubbed `GIT_*` environment, `--no-ext-diff`, `--no-textconv`, `core.fsmonitor=false`, and bounded time and output.

The defects are in behavior at the edges: the output-cap path, the root-as-subfolder scope, and what content the tools expose.

## Warnings

### WR-01: The hard output cap turns into a tool error instead of truncated output

**File:** `mcp_servers/project_git.py:497-498` (also 453, 548, 599, `run_command` 216-222)
**Issue:** `_read_stdout` kills the process tree when output exceeds `HARD_CAP_BYTES`, so `returncode` is non-zero (1 on Windows, -9 on POSIX). Each tool checks `result.returncode != 0` before looking at `result.capped`, in `git_status`, `git_diff`, `git_tracked_files` and `git_recent_commits`. A diff, status or listing larger than 4 MiB therefore raises `git diff failed: exit code 1`. It never reaches the `lower_bound=result.capped` truncation logic that was written for exactly this case. The core test (`test_mcp_project_git_core.py:98`) only checks the `capped` flag on `run_command`, not the tool-level behavior.
**Fix:** In `run_command`, report `returncode=0` when `capped` is true, or change every check to `if result.returncode != 0 and not result.capped:`. Add a tool-level test that forces a small `HARD_CAP_BYTES` and calls `git_diff`.

### WR-02: A `--root` that is a subfolder of a larger repository leaks the whole repository

**File:** `mcp_servers/project_git.py:450, 496, 506, 598`
**Issue:** `resolve_inside_root` confines `path` to `--root`. `git status` and `git diff` without a pathspec cover the whole work tree, not just the cwd. `git log` is likewise not limited to the cwd. `check_work_tree` accepts any directory inside a work tree. With `--root=<repo>/sub`, calling `git_diff()` or `git_status()` with no `path` returns changes and filenames from outside the root, so the confinement can be bypassed by omitting `path`.
**Fix:** In `main()`, require `git rev-parse --show-toplevel` to equal `root` and otherwise `fail(...)`. Alternatively, when `path` is omitted, default the pathspec to `-- .` (with `--relative` for diff) so every command stays scoped to the root.

### WR-03: No filtering of sensitive tracked or staged content

**File:** `mcp_servers/project_git.py:469-510`
**Issue:** `git_diff` returns full patch text for any tracked or staged file. This includes `.env`, key files or tokens that were added or edited, and it goes straight into the LLM context and the chat DB. The error paths also forward the last git stderr line (`_git_error`), which can contain absolute local paths. The path gate prevents escaping the repository but does not prevent reading secrets inside it.
**Fix:** Decide the policy explicitly. Either deny a small pathspec set (`.env*`, `*.pem`, `id_*`, `*.key`) via `:(exclude,literal)`, or document in the tool description and `docs/` that tracked secrets are visible to the model. Consider redacting absolute paths in `_git_error`.

### WR-04: The real GitHub test has a developer-specific absolute default path

**File:** `tests/test_mcp_real_github.py:21-23`
**Issue:** The default for the binary is `C:\Users\Aleksey\go\bin\github-mcp-server.exe`. Elsewhere this is a skip guard only, so it is harmless in CI. It embeds a personal username in the repo and silently runs a real-network test, with a real token, whenever that file exists on a machine. The token is read only from the environment and the masking test uses a fake value, which is good.
**Fix:** Default to `shutil.which("github-mcp-server")`, and require an explicit opt-in env var such as `RUN_REAL_GITHUB=1` before the real-token tests run.

## Info

### IN-01: `assert` used for runtime invariants

**File:** `mcp_servers/project_git.py:194`
**Issue:** `assert proc.stdout is not None and proc.stderr is not None` disappears under `python -O`. The streams are always PIPE here, so it is not a defect, only a style risk.
**Fix:** Replace it with an explicit `if ... is None: raise RuntimeError(...)`.

### IN-02: `create_subprocess_exec` failures are not mapped to a tool error

**File:** `mcp_servers/project_git.py:186`
**Issue:** `OSError` (git removed after startup) propagates as a raw exception instead of a `ToolInputError` with an actionable message. `check_work_tree` handles this only at startup.
**Fix:** Wrap the call in `try/except OSError` and raise `ToolInputError("git could not be started: ...")`.

### IN-03: `git_recent_commits` silently coerces `count`

**File:** `mcp_servers/project_git.py:586-587`
**Issue:** `count <= 0` is silently clamped to 1 with no note, unlike the explicit ">50" note. The behavior is harmless but inconsistent.
**Fix:** Add a "(count raised to 1)" note, or reject `count < 1` with a `ToolInputError`.

## Out of scope notes

- `scripts/e2e_mcp_git_playwright.py` is sound. It uses a scratch repo guarded by `ensure_scratch_repo`, scrubs `GIT_*`, generates random credentials, and never prints the password. No findings.
- `mcp_servers/__init__.py` and the docs test were not found to have defects.
- The CONVENTION-tier checker (`gsd-tools verify conventions`) was not run in this pass, so there are no CONVENTION findings.

---

_Reviewed: 2026-10-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
