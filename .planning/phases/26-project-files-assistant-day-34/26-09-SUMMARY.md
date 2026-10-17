---
phase: 26-project-files-assistant-day-34
plan: 09
subsystem: mcp-project-files
tags: [security, symlink, gap-closure, project-files]
requires:
  - phase: 26-01
    provides: read tools and git visibility
provides:
  - symlink containment for find_usages and search_text
  - read-side .env*/.git deny shared with the write gate
key-files:
  modified: [mcp_servers/project_git.py, tests/test_mcp_project_files.py, docs/USER_GUIDE.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md, .planning/REQUIREMENTS.md]
key-decisions:
  - "Shared helpers _has_git_part/_is_env_name/_is_protected_path used by both read and write paths"
  - "Symlinks are simulated in tests by monkeypatching os.path.realpath so they run without symlink privilege"
requirements-completed: [PFILE-01, PFILE-08]
duration: 40min
completed: 2026-10-17
---

# Phase 26 Plan 09: Read-side containment gap closure Summary

**find_usages, search_text and read_file now resolve listed files through resolve_inside_root and refuse `.env*` and `.git` paths, closing CR-01 and WR-01.**

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 + 2. Helpers, find_usages, read_file, search_text | 3277207 | RED confirmed (8 failed) before the fix; GREEN 301 passed, 3 skipped across the four related test files |
| 3. Docs and REQUIREMENTS | d409a27 | `tests/test_docs_mcp_servers.py` 88 passed; full `pytest tests/ -q` 3932 passed, 17 skipped |

## Verification observed

- Simulated-symlink tests (realpath monkeypatch) pass on this Windows account; real-symlink tests skip when `os.symlink` is not permitted.
- Write-path refusal messages are unchanged; `test_write_forbidden_targets` and `test_edit_forbidden_target` stay green.

## Deviations from Plan

**1. [Rule 3 - Blocking] Tasks 1 and 2 committed together.** The new tests for both tasks live in one test file, so splitting would have left failing tests in the first commit.

**2. [Rule 1 - Bug] Protected-path check placed after the visibility check in `require_visible_file`.** The existing `test_read_file_ignored_refused` expects the "git ignores it" message for the ignored `.env`; checking protection first changed that message. A visible `.env.local` still gets the new refusal.

**3. [Rule 1 - Bug] `test_resolve_listed_ignores_case_difference` calls `find_usages("needle")` without `path`.** With `path="a.py"` the fake realpath rewrote the pathspec to `A.PY`, which case-sensitive `git ls-files` does not match; that is a test artifact, not a `_resolve_listed` defect.

## Known Stubs

None.

## Self-Check: PASSED
