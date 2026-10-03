---
phase: 11-edit-and-delete-long-term-memory-entries-via-ui-day-21
plan: 04
subsystem: testing
tags: [playwright, browser-uat, memory-panel, e2e]
requires:
  - phase: 11-01
    provides: PUT/DELETE /api/v1/memory/long-term/{id}
  - phase: 11-02
    provides: inline edit / confirm-delete UI in the memory panel
  - phase: 11-03
    provides: docs sync, green full suite
provides:
  - Browser-verified result for scenarios S1-S11 on an isolated copy (UI 18000 / Agent 18001)
affects: []
key-files:
  created: []
  modified: []
requirements-completed: [MEMUI-01, MEMUI-02, MEMUI-03, MEMUI-04, MEMUI-05, MEMUI-06]
duration: 20min
completed: 2026-10-03
---

# Phase 11 Plan 04: Browser UAT of long-term memory edit/delete Summary

All eleven browser scenarios (edit with full 406-char round-trip, cancel, duplicate-key error, draft survival, confirm-guarded delete, plain-text XSS probe, persistence, cross-user 404) pass in headless Chromium on an isolated copy at ports 18000/18001; no repository file was changed.

## Browser UAT

Result file: `%TEMP%\aiadvent-uat-11\uat11-result.json` (status PASS, run 2, `s6_fallback_used` false, 2 dialogs recorded - both from S7, 0 page errors). Screenshots: `%TEMP%\aiadvent-uat-11\shots\S1.png` ... `S11.png`.

| Scenario | Result | Observed |
|----------|--------|----------|
| S1 Buttons | PASS | 5 long-term rows each with exactly Редактировать + Удалить; working list 0 buttons; counter 5 |
| S2 Full-value edit | PASS | textarea 400 chars with marker, no modal opened; after save API read-back 406 chars, newline intact, updated_at changed |
| S3 Cancel | PASS | row restored, API unchanged |
| S4 Duplicate key | PASS | 409 toast, form still open with `user_name`, both entries intact; rename to `home_city` saved, `city` gone |
| S5 Empty input | PASS | toast "Заполните ключ и значение", form open, API unchanged |
| S6 Draft survives re-render | PASS | draft kept after switching chats (real click, fallback not used); saved |
| S7 Delete | PASS | dismissed: 1 dialog mentioning `to_delete`, entry kept; accepted: toast, row gone, counter 5 -> 4, API no longer lists it |
| S8 Plain-text rendering | PASS | no img/b elements in list or form, `window.__xss` undefined, literal `<b>bold</b>` in key, no extra dialogs |
| S9 Cross-chat / persistence | PASS | after reload both chats show `home_city`, `Алексей Я.`, 406-char `long_note` |
| S10 Isolation | PASS | second user sees only `secret_b`; PUT and DELETE on first user's entry -> 404; entry unchanged |
| S11 No JS errors | PASS | 0 pageerror events |

Script runs: 2.
- Run 1: 9/11 (S4, S6 failed).
- Run 2: 11/11 PASS, script exit code 0.

Run 1 cause was a harness defect, not an app defect. The scenario waited for the "Запись памяти обновлена" toast, and the lingering toast from S2 satisfied it before the PUT finished, so the API read-back raced the save. Fix: the script clears `#toast-container` before each save/delete click. Only `uat11_playwright.py` changed. The environment was then reset (fresh scratch DB, restart, reseed) and the whole script re-run. No phase code fix was needed and no repository commit was made.

Open defects: none.

Environment: Playwright was already importable and headless Chromium launched; no package or browser was installed. The copy lived under the temp directory with `8001` patched to `18001` in `ui/static/app.js` and `login.html` and the CORS origins patched to `18000` in `agent/state.py` (copy only). The scratch DB was removed at teardown; the copy was stopped (only the PIDs started here), and ports 18000/18001 have no listener afterwards.

Nothing listening on ports 8000/8001 was started, stopped or killed (no process was even listening there during the run).

## Verification (actually run)
- `pytest tests/test_memory.py tests/test_memory_api.py tests/test_context_engine_memory.py tests/test_memory_panel_ui.py tests/test_static_js_syntax.py -q`: 64 passed.
- Result-file gate: exit 0, "Browser UAT: S1-S11 PASS".
- `git status --short`: only the pre-existing untracked `.planning/HANDOFF.json`; no UAT artifact in the repository.

## Assumptions adopted (from research, not user decisions; see the assumptions tables in 11-01-PLAN.md and 11-02-PLAN.md)
- A1: key and value are both editable.
- A2: inline edit in the sidebar, no modal.
- A3: native `confirm()` before delete.
- A4: no live cross-tab sync.
- A5: Origin + JSON content-type checks on the new PUT/DELETE routes.

## Deviations from Plan
None in the product. The UAT script was corrected once in-loop (stale-toast harness defect, described above).

## Known Stubs
None.

## Self-Check: PASSED
