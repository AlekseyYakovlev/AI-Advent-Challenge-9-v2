---
phase: 10-modals-close-only-via-x-button-day-21
reviewed: 2026-10-03T00:00:00Z
depth: standard
files_reviewed: 3
files_reviewed_list:
  - tests/test_modal_close_policy.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 2
  info: 2
  total: 4
status: issues_found
---

# Phase 10: Code Review Report

**Reviewed:** 2026-10-03
**Depth:** standard
**Files Reviewed:** 3
**Status:** issues_found

## Summary

The change removes the backdrop-click and Escape closers from the four modals, adds `type="button"` and `aria-label` to the settings close button, and adds a source-guard test. The production edits are correct and minimal. No bugs or security problems were found in `app.js` or `index.html`. The weaknesses are in the guard test, which is regex-based and easy to bypass, and in an inconsistent HTML edit.

## Warnings

### WR-01: Escape and backdrop guards are trivially bypassable (false sense of safety)

**File:** `tests/test_modal_close_policy.py:42-47, 59-63`
**Issue:** The guard only matches the exact spellings `$('id').addEventListener('click'`, `target === $('id')`, `.key === 'Escape'` and `keyCode === 27`. It misses these cases:
- `e.code === 'Escape'`
- `switch (e.key) { case 'Escape': ... }`
- `['Escape'].includes(e.key)`
- `document.getElementById('x-modal').onclick = ...`
- `e.target.id === 'settings-modal'`
- `e.target.classList.contains(...)`
- `$('id').addEventListener("click"` followed by a line break

`_app_js()` also strips only full-line `//` comments. A `/* ... */` block containing a forbidden pattern causes a false failure, and trailing comments are not handled. The test therefore gives weaker protection than its docstring claims.
**Fix:** Add checks for `\.code\s*===\s*['"]Escape`, `case\s+['"]Escape['"]`, `\bEscape\b` anywhere outside comments, `getElementById\(\s*['"]<id>['"]\s*\)\s*\.(addEventListener|on\w+)`, and `\.on(click|mousedown|pointerdown)\s*=`. Strip block comments with `re.sub(r'/\*.*?\*/', '', src, flags=re.S)`.

### WR-02: Test asserts exact quoting and formatting of the close-button binding

**File:** `tests/test_modal_close_policy.py:55-56`
**Issue:** `f"$('btn-close-{stem}').addEventListener('click'" in _app_js()` is a literal substring match. Switching to double quotes, adding a line break, or using a helper such as `bind('btn-close-x', ...)` fails the test even though behavior is unchanged. The test is brittle in the opposite direction from WR-01. It also does not verify that the handler calls the right close function. A button bound to a no-op would pass.
**Fix:** Use a whitespace- and quote-tolerant regex, for example `\$\(\s*['"]btn-close-<stem>['"]\s*\)\s*\.addEventListener\(\s*['"]click['"]`, and optionally assert that the handler references `close...Modal`.

## Info

### IN-01: `type="button"` / `aria-label` added to only one of the four close buttons

**File:** `ui/static/index.html:251`
**Issue:** `btn-close-settings` now has `type="button"` and `aria-label="Закрыть"`. The other modals' x buttons (`btn-close-add-user`, `btn-close-scheduler-create`, `btn-close-scheduler-run`) were not touched. Any of them inside a `<form>` would act as a submit button. With Escape and backdrop closers removed, the x button is now the only dismiss path, so accessibility labels matter more.
**Fix:** Apply `type="button" aria-label="Закрыть"` to all modal x/Cancel buttons. Consider adding `role="dialog" aria-modal="true"` to the overlays.

### IN-02: Keyboard users lose their only quick dismiss path

**File:** `ui/static/app.js:3126-3127`
**Issue:** Removing Escape is an intentional product decision (assumption A-01). It does reduce keyboard accessibility, since WCAG expects a keyboard-accessible way to dismiss a dialog. The x button stays reachable via Tab, so this is advisory only.
**Fix:** None required. Make sure the x buttons are focusable and labeled (see IN-01).

---

_Reviewed: 2026-10-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
