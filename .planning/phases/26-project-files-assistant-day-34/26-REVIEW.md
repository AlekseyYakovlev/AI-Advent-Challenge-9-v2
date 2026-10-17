---
phase: 26-project-files-assistant-day-34
reviewed: 2026-10-17T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - mcp_servers/project_git.py
  - scripts/e2e_kb_playwright.py
  - scripts/e2e_project_files_playwright.py
  - scripts/files_eval.py
  - scripts/files_task.py
  - tests/test_diff_card_ui.py
  - tests/test_files_eval.py
  - tests/test_files_task.py
  - tests/test_mcp_project_files.py
  - ui/static/app.js
findings:
  critical: 1
  warning: 6
  info: 5
  total: 12
status: issues_found
---

# Phase 26: Code Review Report

**Reviewed:** 2026-10-17
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

The write gate in `project_git.py` is solid for the main paths: `resolve_inside_root` uses `realpath`, so a symlink that points outside the root, into `.git`, or at `.env` is refused by `read_file`, `write_file` and `edit_file`. `.git`, `.env*` and ignored paths are blocked on write. The diff-card code in `app.js` fills rows through `textContent`, so there is no XSS. Two weaknesses stand out. `find_usages` skips the symlink containment check. The read tools rely only on `.gitignore` to protect secrets. The scripts also have evidence-handling and exit-code defects.

The convention rule-pack command (`gsd-tools verify conventions`) produced no output (exit 1), so no CONVENTION findings were emitted.

## Critical Issues

### CR-01: find_usages follows symlinks out of the root (containment bypass)

**File:** `mcp_servers/project_git.py:929-946`
**Issue:** `find_usages` takes file names from `visible_paths()`, which lists tracked and untracked non-ignored entries, including symlinks. It then does `os.path.join(cfg.root, item)` followed by `getsize`/`open`/`ast.parse` without `resolve_inside_root`. `read_file` closes this hole by resolving through `realpath`. `find_usages` does not. A tracked or untracked `x.py` symlink to `../../secret.py`, or to `.env`, is opened and parsed. Any line that contains the searched name is returned as a snippet. `KEY="sk-..."` is valid Python, so `find_usages("KEY")` leaks an ignored `.env` line. Content outside the root is leaked the same way. This defeats the stated invariants "ignored files cannot be read" and "path must stay inside the repository root". The test suite covers a symlink only for the write path (`test_write_through_symlink_into_git_refused`).
**Fix:**
```python
for item in py_files:
    ...
    absolute = os.path.join(cfg.root, item)
    try:
        real = resolve_inside_root(cfg.root, item)   # realpath + containment
    except ToolInputError:
        skipped += 1
        continue
    if real != item and real not in visible_set:      # symlink target must itself be visible
        skipped += 1
        continue
```
(build `visible_set = set(await visible_paths())` once). Add a test with a symlink to `.env` and to an outside file.

## Warnings

### WR-01: Read tools rely only on .gitignore; no explicit .env/.git deny

**File:** `mcp_servers/project_git.py:683-693` (`require_visible_file`), `:794-797` (`search_text`)
**Issue:** The write path hard-blocks `.env*` and `.git`. The read path trusts `.gitignore` alone. A repo whose `.gitignore` lacks `.env` (or a tracked `.env.local`/`.env.production`) lets `read_file` and `search_text` expose secrets to the model and to any remote provider. This is inconsistent with the documented promise ("`.env`, databases cannot be read").
**Fix:** In `require_visible_file` and as a post-filter on `search_text` hits, refuse basenames matching `casefold().startswith(".env")` and any `.git` path component. Reuse the check from `check_write_target`.

### WR-02: _write_atomic drops file mode and permissions

**File:** `mcp_servers/project_git.py:1069-1073`
**Issue:** `NamedTemporaryFile` creates the file with mode 0600, and `os.replace` installs it over the target. An edited executable script loses its `+x` bit, and shared-read permissions are narrowed. On Windows this is harmless, but the server is presented as portable.
**Fix:** Before the replace, call `shutil.copymode(abs_path, tmp_name)` when the target exists. Otherwise apply `0o666 & ~umask`.

### WR-03: files_eval blocked run overwrites previously recorded evidence

**File:** `scripts/files_eval.py:555-561`, `:450-454`
**Issue:** When preflight fails (LM Studio down, ports busy), `save_all_not_run` writes `not run` records over `eval_out/day34/<slug>/run.json|run.patch|answer.md`, then regenerates `summary.md`. A single `check`-like failure destroys real scenario results from an earlier successful run.
**Fix:** Skip writing when a record with status other than `not run` already exists, or write not-run records to a separate file/dir.

### WR-04: e2e_project_files returns NOT RUN and hides a failed render or unchanged check

**File:** `scripts/e2e_project_files_playwright.py:445-453`
**Issue:** `if not model_state["available"]` is evaluated first. It sets `EXIT_BLOCKED` and `RESULT: NOT RUN` even when `render_ok` is False (the model-independent checks failed) or `unchanged` is False (the user's working copy was modified). The failure is reported only in the check lines, and the exit code (2) is read as "environment problem".
**Fix:**
```python
if not (render_ok and unchanged):
    result, code = f"RESULT: FAIL (model {model})", EXIT_FAIL
elif not model_state["available"]:
    result, code = f"RESULT: NOT RUN (model part: ...)", EXIT_BLOCKED
```

### WR-05: e2e_kb teardown has a dead loop; respawned Agent can be orphaned

**File:** `scripts/e2e_kb_playwright.py:681-684`
**Issue:** The loop `for proc in psutil.Process().children(recursive=True): if proc.pid == app_proc.pid: continue` does nothing. The comment says the supervisor may have respawned the Agent after the snapshot, but no kill is performed. Scenario 10 deliberately kills the Agent, so a respawned process can keep port 18001 busy (the later "ports are free" check then fails) and leaks into later runs, which `files_eval` reuses.
**Fix:** Re-snapshot `psutil.Process(app_proc.pid).children(recursive=True)` after `terminate()` and kill the survivors. Alternatively kill any listener on 18000/18001 that descends from the app. Remove the dead loop.

### WR-06: patch_stats / _added_text miscount content lines that look like file headers

**File:** `scripts/files_task.py:317-320`, `scripts/files_eval.py:173`
**Issue:** There is no hunk-state tracking. A removed line `-- comment` appears in the diff as `--- comment`, and an added line `++ x` as `+++ x`. Both are skipped, so `total_removed`/`added` in `run.json` and the changelog/readme checks (`_added_text`) undercount. The e2e script and `app.js` already handle this case, so the three implementations disagree.
**Fix:** Track `in_hunk` (set on `@@`, reset on `diff --git`) and exclude `---`/`+++` only when `not in_hunk`.

## Info

### IN-01: find_usages summary mislabels skips

**File:** `mcp_servers/project_git.py:947-949, 971-972`
**Issue:** Oversized files, deleted-but-tracked files (`OSError`) and `ValueError` all count as "skipped (syntax errors)".
**Fix:** Use "files skipped (unreadable, too large or syntax errors)".

### IN-02: Reserved-device check misses names with trailing space before the extension

**File:** `mcp_servers/project_git.py:1030`
**Issue:** `part.split(".")[0].casefold()` for `"CON .txt"` is `"con "`, which is not in `RESERVED_NAMES`. Windows normalises trailing spaces in some APIs. Strip trailing spaces and dots from the stem first (as is done for `.git` via `rstrip(". ")`).
**Fix:** `part.split(".")[0].rstrip(" ").casefold()`.

### IN-03: search_text can emit two truncation markers; read_file may cut its own marker

**File:** `mcp_servers/project_git.py:843-847, 765`
**Issue:** The 200-hit cap appends a marker, then `fit_text` appends a second one if the body is still over `max_chars`. `read_file` ends with `text[:max_chars]`, which can slice through the marker.
**Fix:** Let `fit_text` own truncation, or reserve marker space before the final slice.

### IN-04: app.js and e2e scripts: minor quality items

**File:** `ui/static/app.js:1569, 1596`; `scripts/e2e_project_files_playwright.py:153-154`; `scripts/files_eval.py:134-137`
**Issue:**
- `isUnifiedDiff(evt.result)` is computed twice per card (once into `isDiff`, once in `body`). Use `isDiff`.
- `ensure_scratch_clone` guards with `assert`, which is stripped under `python -O`.
- `reset_clone` only checks `name == "files-root"` before `git reset --hard` + `clean -fd`. Any other directory with that name outside the repo is wiped. Also assert it lies under the scratch dir.
**Fix:** Reuse `isDiff`; raise explicit exceptions instead of `assert`; pass `scratch` to `reset_clone` and verify containment.

### IN-05: Tests: diff-card tests are source greps, not behaviour

**File:** `tests/test_diff_card_ui.py:54-107`
**Issue:** The tests assert substrings of `app.js` (Tailwind class names, `tabindex`, regex text). They pass on any refactor-free text and break on harmless restyling, while behaviour (the `--- ` removed line inside a hunk, truncated counts) is checked only by the Playwright script, which is not part of `pytest`. The suite also has no test for `find_usages`/`read_file` through a symlink (see CR-01).
**Fix:** Add a node-free behavioural check where possible (e.g. extract the pure functions and run through a small JS runtime if available), and add the symlink tests.

---

_Reviewed: 2026-10-17_
_Reviewer: Claude (bm-code-reviewer)_
_Depth: standard_
