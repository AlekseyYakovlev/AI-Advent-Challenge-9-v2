---
phase: 10-modals-close-only-via-x-button-day-21
fixed_at: 2026-10-03T00:00:00Z
review_path: .planning/phases/10-modals-close-only-via-x-button-day-21/10-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 10: Code Review Fix Report

**Fixed at:** 2026-10-03
**Source review:** .planning/phases/10-modals-close-only-via-x-button-day-21/10-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 2
- Fixed: 2
- Skipped: 0

## Fixed Issues

### WR-01: Escape and backdrop guards are trivially bypassable

**Files modified:** `tests/test_modal_close_policy.py`
**Commit:** 1cef57c
**Applied fix:** `_app_js()` now strips `/* */` block comments. Added checks for `getElementById('<modal>').addEventListener/on*`, `.id === '<modal>'`, `.code === 'Escape'`, any `\bEscape\b` outside comments, `keyCode == 27`, and `.onclick/.onmousedown/.onpointerdown =` assignments.

### WR-02: Test asserts exact quoting and formatting of the close-button binding

**Files modified:** `tests/test_modal_close_policy.py`
**Commit:** 1cef57c (same commit as WR-01, same file)
**Applied fix:** The close-button binding check is now a quote- and whitespace-tolerant regex. The optional check that the handler calls a `close...Modal` function was not added.

Tests: `pytest tests/test_modal_close_policy.py` gives 11 passed.

Info findings IN-01 and IN-02 were out of scope (fix_scope=critical_warning).

The fixer worked directly on branch Day21 in the main tree, not in a worktree.

---

_Fixed: 2026-10-03_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
