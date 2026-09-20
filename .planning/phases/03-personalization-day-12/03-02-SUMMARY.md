---
phase: 03-personalization-day-12
plan: 02
subsystem: ui
tags: [vanilla-js, tailwind, personalization, profile, xss-safety]

# Dependency graph
requires:
  - phase: 03-personalization-day-12 (plan 01)
    provides: "GET/PUT /api/v1/profile REST endpoints, apiFetch/showToast helpers, renderMemoryPanel load-then-render precedent"
provides:
  - "#profile-panel sidebar block: three labeled textareas (style/format/constraints) plus a save button, stacked below #memory-panel"
  - "loadProfile / renderProfilePanel / saveProfile wired into init() and bindEvents()"
  - "Stacked-panel precedent for future sidebar surfaces (no tab-switcher pattern introduced)"
affects: [phase-04-tasks, phase-05-invariants]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Second stacked sidebar panel (not a tab), matching #memory-panel's load-then-render shape: loadX() populates state.lastX then calls renderXPanel()"
    - "Profile values assigned only via textarea .value, never innerHTML — freeform user text can never become live DOM"

key-files:
  created: []
  modified: [ui/static/index.html, ui/static/app.js]

key-decisions:
  - "Resolved the CONTEXT.md D-04 'sidebar tab' wording vs RESEARCH.md/UI-SPEC's 'no tab-switcher exists' finding by building #profile-panel as a second stacked <div>, identical in shape to #memory-panel, per the plan's explicit instruction to implement the research finding over the literal CONTEXT wording."
  - "loadProfile() is called exactly once at startup in init(), not from selectChat() or the WebSocket 'done' handler, because the profile is user-scoped (D-01) rather than chat-scoped."

patterns-established:
  - "Any future sidebar panel (e.g. a Phase 4/5 task or invariant panel) should follow this same stacked-<div>-with-load-then-render shape rather than introducing tab-switching JS, since no tab component exists anywhere in this codebase."

requirements-completed: [PERS-03]

# Metrics
duration: ~8min
completed: 2026-09-20
---

# Phase 3 Plan 2: Personalization UI Panel Summary

**Permanently-visible sidebar "Профиль" panel with three freeform textareas (style/format/constraints) wired to `GET`/`PUT /api/v1/profile`, using the same stacked-panel shape as the existing Memory panel.**

## Performance

- **Duration:** ~8 min (commit-to-commit)
- **Started:** 2026-09-20T11:43:23Z
- **Completed:** 2026-09-20T11:48:48Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- `#profile-panel` inserted between `#memory-panel` and `#agent-status` — a second stacked sidebar block, not a modal or tab (D-04, resolved per RESEARCH/UI-SPEC finding over CONTEXT's literal "tab" wording).
- Three labeled `<textarea>` fields (`profile-style`, `profile-format`, `profile-constraints`) with placeholders, byte-for-byte matching `settings-system-prompt`'s Tailwind classes plus a focus ring (D-03: freeform text, no enums/dropdowns).
- `loadProfile()` / `renderProfilePanel()` / `saveProfile()` added to `app.js`, following the exact `loadChatMemory`/`renderMemoryPanel` load-then-render pattern; profile loads once at startup (`init()`), not per chat switch.
- Save success/failure produce the exact UI-SPEC toast copy ("Профиль сохранён" / "Не удалось сохранить профиль. Проверьте соединение и попробуйте снова.").
- Profile values are assigned exclusively via `.value` on the textareas — `innerHTML` count in `app.js` is unchanged at 11, confirming no profile text can ever become live DOM.
- Full test suite still green (180 passed) — this plan touches no Python.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add the #profile-panel block to the sidebar** - `e0dbfc0` (feat)
2. **Task 2: Wire the panel to GET/PUT /api/v1/profile** - `cf3f055` (feat)

## Files Created/Modified
- `ui/static/index.html` - Adds `#profile-panel` (heading, three textareas, save button) between `#memory-panel` and `#agent-status`
- `ui/static/app.js` - Adds `lastProfile` to `state`, `loadProfile`/`renderProfilePanel`/`saveProfile`, wires `btn-save-profile` click and a one-time `await loadProfile()` in `init()`

## Decisions Made
- **Stacked panel over tab-switcher (D-04 resolution):** `03-CONTEXT.md`'s D-04 describes a "sidebar tab," but `03-RESEARCH.md` (Anti-Patterns, Assumption A3) and `03-UI-SPEC.md` both establish that no tab-switching component exists anywhere in this codebase. Per the plan's explicit instruction, `#profile-panel` was built as a second stacked `<div>` identical in container shape to `#memory-panel` — same placement intent (always reachable, not a modal), no new JS interaction pattern introduced. Later phases adding sidebar surfaces (task state, invariants) should follow this same stacked-panel precedent rather than inventing a tab component.
- Panel heading uses `font-medium` (500), not `#memory-panel`'s `font-semibold` (600), per UI-SPEC's two-weight cap for this phase's new UI.
- `loadProfile()` called exactly once in `init()` (after `loadModels()`, before the `loadChats()` try block) — never from `selectChat()` or the WS `done` handler — because the profile is user-scoped, not chat-scoped.
- Save does not re-render the panel afterward (assigns `state.lastProfile` from the response but leaves the textareas as the user left them), avoiding fighting an in-flight edit.

## Deviations from Plan

None — plan executed exactly as written. All markup and JS wiring match the plan's `<action>` text verbatim (element ids, classes, copy strings, function names, wiring order).

## Issues Encountered

**Verification-technique quirk in Task 1's acceptance criteria (non-blocking, same class of issue as noted in 03-01-SUMMARY.md):** The acceptance criterion `grep -c 'indigo' ui/static/index.html` increases by exactly `5` versus the HEAD count assumes one `grep`-countable line per new indigo utility class. In practice the save button's `class="... bg-indigo-600 hover:bg-indigo-500 ..."` places two indigo utilities (`bg-indigo-600` and `hover:bg-indigo-500`) on a single line, and `grep -c` counts *matching lines*, not occurrences — so the measured delta is `4` lines (15 vs. 11), not `5`. Confirmed with `grep -o 'indigo' | wc -l` instead (occurrence-count, not line-count): baseline `15` → current `20`, a delta of exactly `5`, matching the plan's intent (3 focus rings + `bg-indigo-600` + `hover:bg-indigo-500`). This is a limitation of the grep-line-based verification command as written, not a functional or visual defect — the markup exactly matches the plan's specified classes.

**Task 2's `fetch(` acceptance criterion wording:** The plan's acceptance bullet reads "`grep -c \"fetch(\"` ... is unchanged from ... plus `2`", which is internally inconsistent (unchanged and +2 cannot both hold). The explanatory clause immediately after it ("the two new calls both go through `apiFetch`, never raw `fetch`") clarifies the actual intent: the raw lowercase `fetch(` count (which does not match `apiFetch(` since the pattern is case-sensitive and `apiFetch` has a capital `F`) should stay unchanged, because the two new profile calls go through `apiFetch(...)`, not raw `fetch(...)`. Verified: `grep -c "fetch("` is `2` both before and after this task (unchanged), and `grep -c "apiFetch('/api/v1/profile'"` is `2` (one GET, one PUT) as separately required. Treated the explanatory clause as authoritative over the literal arithmetic in the bullet.

## User Setup Required

None — no external service configuration required. The panel is served as part of the existing static frontend; no build step, no new dependency.

## Next Phase Readiness

- PERS-03 is complete: users can view and edit their profile from a permanently-reachable sidebar panel, backed by the plan 03-01 REST API.
- Phase 3's personalization vertical (backend + UI) is now fully wired end-to-end: `PUT` a preference in the browser, reload, and it persists and re-injects into every system prompt.
- The stacked-panel precedent (no tab-switcher) is documented above for any later phase (Day 13-15: tasks, invariants) that adds another sidebar surface.

---
*Phase: 03-personalization-day-12*
*Completed: 2026-09-20*

## Self-Check: PASSED

Both modified files (`ui/static/index.html`, `ui/static/app.js`) verified present on disk; both task commits (`e0dbfc0`, `cf3f055`) and the plan-completion commit (`3de87c2`) verified present in `git log`.
