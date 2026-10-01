# Phase 10: Modals close only via x button (Day 21) - Research

**Researched:** 2026-10-02
**Domain:** Vanilla-JS frontend behavior change (`ui/static/app.js`, `ui/static/index.html`)
**Confidence:** HIGH (all findings from direct codebase reads, [VERIFIED: codebase grep])

## Summary

There are exactly 4 modals, all in `ui/static/index.html`, all driven from `ui/static/app.js`. `login.html` has no modal. Each modal has a hand-written per-modal backdrop click listener (`e.target === modal` check), no shared helper. Each modal already has an x (&times;) button, so no x button needs to be added. There is also one global `Escape` keydown handler that closes all four modals.

The change is small: delete 4 backdrop click listeners (and decide on Escape). No new visuals, no backend change, no new dependencies.

**Primary recommendation:** Remove the four backdrop `click` listeners (app.js 2497-2502, 2800-2802, 2834-2836). Also remove the Escape handler (app.js 2837-2844), because the goal says "closes only when its x is clicked". Keep Cancel buttons and post-success auto-close. See Ambiguities.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| (none, TBD) | Derived: backdrop click must not close any modal; x button must still close every modal | Inventory below |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Rationale |
|------------|-------------|-----------|
| Modal open/close | Browser (app.js + index.html) | Pure client DOM (`hidden` class toggle); no backend involvement |

## 1. Modal inventory

Pattern for all: overlay `div` (`hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60`) containing a card `div`. Open/close = toggling Tailwind `hidden` class.

| # | Modal | DOM id | index.html | Opened by | x button | Cancel button |
|---|-------|--------|-----------|-----------|----------|---------------|
| 1 | Settings | `settings-modal` | 246-247 | `#btn-settings` click -> `openSettingsModal()` (app.js 2684-2707, 2786-2788); also inline `onclick="openSettingsModal()"` at app.js 1189 | `#btn-close-settings` (index.html 251; no aria-label) | `#btn-cancel-settings` (307) |
| 2 | Add user | `add-user-modal` | 365-366 | `#btn-users` -> `openAddUserModal()` (app.js 2713, 2794) | `#btn-close-add-user` (370) | `#btn-cancel-add-user` (385) |
| 3 | Scheduler create | `scheduler-create-modal` | 398-400 (role=dialog aria-modal) | `#btn-scheduler-new` -> `openSchedulerCreateModal(opener)` (app.js 2287, 2484) | `#btn-close-scheduler-create` (404) | `#btn-cancel-scheduler-create` (477) |
| 4 | Scheduler run result | `scheduler-run-modal` | 490-492 (role=dialog aria-modal) | click on a run row -> `openSchedulerRunModal(runId, row)` (app.js 2034, 2457) | `#btn-close-scheduler-run` (496) | none |

Other overlay-like things: `confirm()` native dialogs (app.js 520, 809, 856, 1435, 1810, 2088, 2099, 2850) - browser-native, not DOM modals, out of scope. Toasts (`showToast`) are not modals. The MCP add/edit form (`openMcpForm`/`closeMcpForm`, `#btn-mcp-cancel`) is an inline panel inside the Settings modal, not an overlay. `login.html` contains no modal.

## 2. Every current close path (per modal)

| Modal | x | Backdrop click | Escape | Cancel | Auto-close after action |
|-------|---|----------------|--------|--------|-------------------------|
| Settings | app.js 2789 | **app.js 2834-2836** | 2839 | 2790 | `saveSettings` -> `closeSettingsModal()` at 2759 (after successful PUT) |
| Add user | 2795 | **app.js 2800-2802** | 2840 | 2796 | `createUser` -> `closeAddUserModal()` at 2735 (success only; error stays open and shows inline error) |
| Scheduler create | 2494 | **app.js 2497-2499** | 2841 | 2495 | `submitSchedulerCreate` -> `closeSchedulerCreateModal()` at 2361 |
| Scheduler run | 2496 | **app.js 2500-2502** | 2842 | none | none; but `openSchedulerCreateModal`/`openSchedulerRunModal` programmatically `hideSchedulerModal(other)` (2288, 2466) |

Close functions: `closeSettingsModal` (2709), `closeAddUserModal` (2721), `closeSchedulerCreateModal`/`closeSchedulerRunModal` -> `closeSchedulerModal` (2227-2248; restores focus to `state.schedulerModalOpener`).

## 3. Where backdrop-click logic lives

No shared helper. Four separate listeners, each `if (e.target === $('<modal-id>')) close...()`:
- `bindSchedulerModals()` app.js 2483-2503: lines 2497-2499 (create), 2500-2502 (run)
- `bindEvents()` app.js 2800-2802 (add-user), 2834-2836 (settings)

Those are the exact code blocks to delete. `e.target === overlay` already means clicks inside the card never closed it, so deleting them is sufficient; no CSS/HTML change needed. No `mousedown`/`pointerdown` variants, no `<dialog>` elements, no `data-` attributes (verified by grep).

## 4. x button coverage

All 4 modals have an x button. No x needs adding. Minor a11y nit (optional, not required): Settings x (index.html 251) lacks `aria-label="Закрыть"` and `type="button"` that the other three have. Adding `aria-label` is non-visual; planner may include it but it is not required by the goal.

## Ambiguities and recommendations

| Question | Current behavior | Recommendation | Rationale |
|----------|------------------|----------------|-----------|
| Escape key | Global document keydown (app.js 2837-2844) closes all four modals (calls each close fn unconditionally; close fns no-op if already hidden) | **Remove it** (Claude's-discretion recommendation; flag in plan as a decision the user can reverse) | Goal wording is "closes only when its x is clicked". Literal reading excludes Escape. Risk: keyboard a11y regression; but with the x as the sole path this matches the stated spec. Alternative (keep Escape) is defensible since the goal only names backdrop click explicitly - if planner wants zero ambiguity, ask the user. Default: remove, since "only" is explicit. |
| Cancel buttons | Close modals 1-3 | **Keep** | They are explicit buttons inside the modal, a form-cancel affordance; goal targets "clicking outside". Removing visible buttons would be a visual change, and user said no visual redesign. |
| Auto-close after success (Save settings, create user, create scheduler task) | Closes programmatically | **Keep** | Workflow completion, not a user dismissal gesture; removing would force an extra x click and degrade UX. |
| Programmatic swap (open create hides run modal and vice versa) | Hides the other modal | Keep | Internal state hygiene. |

Note: if Escape is removed, the Scheduler focus-restore (`closeSchedulerModal` opener focus) still works via x/Cancel/auto-close.

## 5. Non-modal outside-click closers (leave alone)

None found. Grep of `app.js` for document-level `click`, `mousedown`, popover/dropdown/context-menu handlers found: only the global `keydown` Escape (modals) at 2837 and `contextmenu` on `#chat-list` (2845-2853), which triggers a native `confirm()` for chat deletion - not an outside-click closer. `<select>` elements are native. Foldable panels (`setupFoldablePanels`, `data-fold-toggle`) toggle on their own header click, not outside click. Plan should state: no other handler is touched; the `contextmenu` handler and `confirm()` flows remain unchanged.

## Don't Hand-Roll / Standard Stack

No libraries. Do NOT introduce a shared "modal manager" or `<dialog>` migration: out of scope for a behavior-only change; just delete listeners. (Optionally, if the Escape removal makes the keydown block empty, delete the whole `document.addEventListener('keydown', ...)` block 2837-2844 - verified it contains only the four close calls.)

## Common Pitfalls

1. **Leaving the Escape handler while claiming "only x"**: Escape would still close; decide explicitly and document.
2. **Deleting the wrong listener**: `$('settings-modal').addEventListener` at 2834 is far from the other settings bindings (2786-2793); the add-user one is at 2800. Four total; grep `e.target === \$\('` after editing should return zero modal hits.
3. **Removing the whole `bindSchedulerModals` tail**: only remove lines 2497-2502; keep x/Cancel bindings 2494-2496.
4. **Cached static JS**: browser may cache `app.js`; E2E should hard-reload.
5. **Scrollable settings card**: overlay is not scroll container (card has `overflow-y-auto`), so no side effect from removing click listener.

## Validation Architecture

### Test Framework
No JS test runner exists (no npm/Node allowed). `pytest` suite (`tests/`, 40+ files) covers backend only; grep of `tests/` and `docs/` for `modal|backdrop|Escape` returned no hits - **no existing test touches modal behavior**, so nothing to update or break.

| Behavior | Verification | Command |
|----------|--------------|---------|
| No `e.target ===` backdrop close listeners remain; x/Cancel bindings remain | Static grep check | `grep -n "e.target === \$(" ui/static/app.js` (expect no modal hits); `grep -n "btn-close-" ui/static/app.js` (expect 4 bindings) |
| Backdrop click leaves each of 4 modals open; x closes each; (Escape per decision) | Manual/Playwright E2E on isolated copy at ports 18000/18001 (never kill/use user's app on 8000/8001; per project memory) | Playwright: open modal, click overlay at corner coordinates (outside card), assert `hidden` class absent; click x, assert present |
| Cancel and post-success auto-close still work | Same E2E | - |
| Regression of backend suite | `pytest tests/ -v` | - |

Optional (not recommended unless user wants): a pytest that reads `ui/static/app.js` as text and asserts no `e.target === $('...-modal')` pattern. It is a source-text check; acceptable but brittle. Wave 0 gaps: none required.

## Security Domain

No new input handling, no HTML injection; ASVS categories not affected. Constraint reminder: no `innerHTML` without `DOMPurify.sanitize()` (none needed here).

## Project Constraints (from CLAUDE.md)
- Vanilla JS + CDN only; no bundler/npm/Node.
- Never insert HTML without `DOMPurify.sanitize()`.
- Branch is `Day21` (shared with Phases 9-12); do not create/switch branches.
- Browser E2E via Playwright on isolated copy at ports 18000/18001 (memory note); never kill the user's app at 8000/8001.
- No visual redesign (user skipped UI-SPEC).

## Assumptions Log

| # | Claim | Risk if Wrong |
|---|-------|---------------|
| A1 | User wants Escape removed (literal "only x") | Low-medium: user may want Escape kept; trivial to restore. Planner should surface as an explicit decision in the plan |
| A2 | Cancel buttons and auto-close after success are kept | Low |

## Open Questions

1. Escape key: remove (recommended) or keep? Resolved by default recommendation above unless user says otherwise.

## Environment Availability

Playwright for E2E is run by the executor/verifier per project memory; not probed here. No other external dependencies.

## Sources

Primary (HIGH): direct reads of `ui/static/app.js` (2215-2310, 2480-2503, 2680-2876), `ui/static/index.html` (greps at lines 246-509), `ui/static/login.html` (no modal), `.planning/ROADMAP.md` Phase 10, repo `CLAUDE.md`.

**Research date:** 2026-10-02 | **Valid until:** until Phase 9/11/12 add new modals (Phases 11 and 12 may add edit dialogs or a providers section; the plan should note any modal added by those phases must follow the same x-only rule)
