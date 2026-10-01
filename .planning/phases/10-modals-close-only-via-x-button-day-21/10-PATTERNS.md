# Phase 10: Modals close only via x button (Day 21) - Pattern Map

**Mapped:** 2026-10-02
**Files analyzed:** 3 (1 modified, 1 optional a11y tweak, 1 verification artifact)
**Analogs found:** 3 / 3

This is a deletion-only behavior change. "Patterns" here mean: which exact code to delete, which neighbouring code to leave untouched (the x/Cancel bindings are the pattern to preserve), and how earlier phases structured browser verification.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `ui/static/app.js` (modified, delete 4 backdrop listeners + Escape handler) | component (vanilla-JS event bindings) | event-driven | itself: `bindSchedulerModals()` and `bindEvents()` x/Cancel bindings | exact |
| `ui/static/index.html` (optional: Settings x `aria-label` / `type="button"`) | component (markup) | n/a | the other three modals' x buttons (index.html 370, 404, 496) | exact |
| Browser verification script (temp dir, NOT committed; or scenario list in SUMMARY/UAT) | test (E2E) | request-response | `scripts/e2e_mcp_chat_playwright.py` (report/PASS-FAIL style) and Phase 9 plan 09-04 Task 2 (isolated copy recipe) | role-match |

No pytest is added: grep of `tests/` for modal/backdrop/Escape found nothing, and no JS test runner is allowed (no Node/npm).

## Pattern Assignments

### `ui/static/app.js` (component, event-driven)

**Analog:** the same file. The surviving x/Cancel bindings define the "keep" shape; the `e.target === overlay` listeners are the "delete" shape.

**DELETE 1 and 2: scheduler backdrop listeners** (`bindSchedulerModals()`, lines 2494-2502). Keep 2494-2496, delete 2497-2502:
```javascript
    $('btn-close-scheduler-create').addEventListener('click', closeSchedulerCreateModal);   // KEEP
    $('btn-cancel-scheduler-create').addEventListener('click', closeSchedulerCreateModal);  // KEEP
    $('btn-close-scheduler-run').addEventListener('click', closeSchedulerRunModal);         // KEEP
    $('scheduler-create-modal').addEventListener('click', (e) => {                          // DELETE
        if (e.target === $('scheduler-create-modal')) closeSchedulerCreateModal();          // DELETE
    });                                                                                     // DELETE
    $('scheduler-run-modal').addEventListener('click', (e) => {                             // DELETE
        if (e.target === $('scheduler-run-modal')) closeSchedulerRunModal();                // DELETE
    });                                                                                     // DELETE
}
```
After deletion the function ends right after line 2496 with `}`.

**DELETE 3: add-user backdrop listener** (`bindEvents()`, lines 2794-2802). Keep 2794-2799, delete 2800-2802:
```javascript
    $('btn-users').addEventListener('click', openAddUserModal);
    $('btn-close-add-user').addEventListener('click', closeAddUserModal);     // KEEP
    $('btn-cancel-add-user').addEventListener('click', closeAddUserModal);    // KEEP
    $('add-user-form').addEventListener('submit', (e) => {
        createUser(e).catch((err) => showToast(err.message, 'error'));
    });
    $('add-user-modal').addEventListener('click', (e) => {                    // DELETE
        if (e.target === $('add-user-modal')) closeAddUserModal();            // DELETE
    });                                                                       // DELETE
```

**DELETE 4 and 5: settings backdrop listener + global Escape handler** (`bindEvents()`, lines 2834-2844; Escape removal is the RESEARCH recommendation, flag as a reversible decision in the plan). Delete the whole contiguous block, the lines before (2818-2833 `#messages` click) and after (2845 `#chat-list` contextmenu) stay:
```javascript
    $('settings-modal').addEventListener('click', (e) => {
        if (e.target === $('settings-modal')) closeSettingsModal();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeSettingsModal();
            closeAddUserModal();
            closeSchedulerCreateModal();
            closeSchedulerRunModal();
        }
    });
```
The keydown block contains only the four close calls, so the whole `document.addEventListener('keydown', ...)` goes. The separate `$('message-input').addEventListener('keydown', ...)` at line 2780 is unrelated and must stay.

**Settings x/Cancel bindings to leave** (lines 2789-2790):
```javascript
    $('btn-close-settings').addEventListener('click', closeSettingsModal);
    $('btn-cancel-settings').addEventListener('click', closeSettingsModal);
```

**Leave untouched (close paths that are not user dismissal gestures):**
- Close/open functions: `closeSettingsModal` 2709, `closeAddUserModal` 2721, `hideSchedulerModal`/`closeSchedulerModal` 2227-2248 (focus restore to `state.schedulerModalOpener`).
- Post-success auto-close: `closeAddUserModal()` 2735, `closeSettingsModal()` 2759, `closeSchedulerCreateModal()` 2361.
- Programmatic swap: `hideSchedulerModal('scheduler-run-modal')` 2288, `hideSchedulerModal('scheduler-create-modal')` 2466.
- `contextmenu` handler on `#chat-list` (2845+) and native `confirm()` calls.

**Error handling / conventions:** none new. No `innerHTML`, no new handlers, no logging. Vanilla JS, 4-space indent, `$()` id helper, single quotes.

---

### `ui/static/index.html` (optional a11y nit)

**Analog:** the other three x buttons (index.html 370, 404, 496), which carry `aria-label="Закрыть"` and `type="button"`. The Settings x is `#btn-close-settings` at index.html 251 (no aria-label). Read lines 249-253 and 370 before editing and copy the attribute set exactly. Non-visual; the planner may include or skip it (not required by the goal).

---

### Verification artifact (E2E, not committed)

**Analog A:** `scripts/e2e_mcp_chat_playwright.py`: the repo's only committed Playwright script. Reusable conventions (lines 1-66): module docstring with Usage and exit-code legend, `checks: list[tuple[str, bool, str]]`, and
```python
def report(label: str, passed: bool, detail: str = "") -> bool:
    checks.append((label, passed, detail))
    suffix = f" - {detail}" if detail else ""
    print(f"{'PASS' if passed else 'FAIL'}: {label}{suffix}")
    return passed
```
Do NOT copy its ports: it uses UI 8000 / AGENT 8001 (line 31-32), which violates the project memory rule for this phase.

**Analog B:** `.planning/phases/09-auto-rename-chats-with-llm-day-21/09-04-PLAN.md` Task 2 (lines 107-128), which was derived from `08-08-SUMMARY.md`. Recipe to follow:
1. Preconditions: `python -c "import playwright.async_api"` succeeds; otherwise write "Browser UAT NOT RUN: <reason>" as an open item and do not claim browser truths. (Phase 10 needs no LM Studio since no chat turn is required.)
2. Copy the repo to a temp dir outside the repo (excluding `.git`, `.claude`, `.planning`, `.env`, `*.db*`, `tests`, `docs`, `__pycache__`); in the COPY only, change `8001` to `18001` in `ui/static/app.js` and `ui/static/login.html`, and the two origins in `agent/state.py::CORS_ORIGINS` to `http://localhost:18000` / `http://127.0.0.1:18000`. Grep the copy's `run.py`, `ui/`, `agent/state.py`, `shared/config.py` for leftover 8000/8001.
3. Start the copy with env `UI_PORT=18000 AGENT_PORT=18001 DB_PATH=<temp>/uat10.db PYTHONIOENCODING=utf-8`; wait for `GET http://localhost:18001/health` = 200. Seed a user via `shared.database` / `shared.auth.hash_password` against the scratch DB.
4. Playwright (Python `playwright.async_api`, headless Chromium, base `http://localhost:18000`): log in, hard-reload (cached `app.js` pitfall), then per modal:
   - Open via `#btn-settings`, `#btn-users`, `#btn-scheduler-new`; scheduler run modal by clicking a run row (needs a seeded run, otherwise mark as covered by static grep + note).
   - Click the overlay at its corner, e.g. `page.mouse.click(5, 5)` (outside the card); assert the overlay still lacks the `hidden` class.
   - Press Escape; assert still open (if Escape removal is accepted).
   - Click the x button (`#btn-close-settings`, `#btn-close-add-user`, `#btn-close-scheduler-create`, `#btn-close-scheduler-run`); assert `hidden` present.
   - Re-open and click Cancel (modals 1-3); assert closed.
5. Stop only own PIDs, confirm 18000/18001 free, delete scratch DB, confirm `git status --porcelain` shows only the intended `ui/static` changes.

**Static check (cheap, no browser)**:
```bash
grep -n "e.target === \$(" ui/static/app.js        # expect no modal hits
grep -n "btn-close-" ui/static/app.js              # expect 4 bindings (settings, add-user, scheduler-create, scheduler-run)
grep -n "'Escape'" ui/static/app.js                # expect none (if Escape removed)
pytest tests/ -v                                   # backend regression
```

## Shared Patterns

### Per-modal open/close contract (preserve)
**Source:** `ui/static/app.js` 2227-2248 (scheduler), 2684-2722 (settings, add-user). Overlay visibility is toggled only by Tailwind `hidden` class; close functions are idempotent (no-op when already hidden). Verification asserts on `classList.contains('hidden')`.

### Isolated E2E environment
**Source:** Phase 9 plan 09-04 Task 2 + project memory (`feedback_e2e_playwright_isolated_copy`). Apply to the verification task: ports 18000/18001 only, never touch 8000/8001.

### Forward rule (from RESEARCH)
Any modal added by Phases 11/12 must follow the x-only rule (no backdrop listener, no Escape closer).

## Conventions

Derivation via `gsd-tools verify conventions` was not run (the phase edits one vanilla-JS file by deletion only). Observed local style in `ui/static/app.js`: camelCase functions and identifiers, `$('id')` DOM helper, 4-space indent, single quotes, arrow-function listeners, ESM-free classic script (no imports). Contested hotspot (author's choice): the CJS<->SDK dual resolver (`bin/lib/**` CJS vs `sdk/src/**` ESM) is irrelevant to this phase; match the local directory style (`ui/static` is classic browser JS).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | | | Phase 10 has no JS test runner and no existing modal test; verification reuses the Phase 8/9 Playwright recipe |

## Metadata

**Analog search scope:** `ui/static/app.js`, `ui/static/index.html`, `scripts/`, `tests/` (via RESEARCH grep), `.planning/phases/07-09`
**Files scanned:** ~6 read/grepped directly
**Pattern extraction date:** 2026-10-02
