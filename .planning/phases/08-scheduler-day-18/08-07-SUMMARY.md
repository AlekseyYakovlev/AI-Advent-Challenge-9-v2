---
phase: 08-scheduler-day-18
plan: 07
subsystem: ui
tags: [vanilla-js, tailwind, websocket, scheduler, markdown, dompurify]

requires:
  - phase: 08-scheduler-day-18
    provides: "REST /api/v1/scheduler/* (08-05) and WS /ws/events frames (08-02, 08-04)"
provides:
  - "Sidebar 'Расписание' panel with job cards, run history and actions"
  - "Create-job modal and run-result modal (Markdown result via renderMarkdown)"
  - "/ws/events live client with REST re-sync, backoff reconnect and single-timer polling fallback"
  - "Node-free JS structural check (tests/test_static_js_syntax.py)"
affects: [08-08 manual verification]

tech-stack:
  added: []
  patterns:
    - "State attached to the state object after the big literal (like the MCP block)"
    - "All server text via textContent (mcpEl); only result body through renderMarkdown"
    - "Python single-pass scanner as a Node-free JS syntax guard"

key-files:
  created:
    - tests/test_static_js_syntax.py
  modified:
    - ui/static/index.html
    - ui/static/app.js

key-decisions:
  - "Polling and post-connect re-sync call loadSchedulerTasks(true) (silent) so a down agent does not toast every 10 s"
  - "Client-side sort mirrors the server order (live first, next_run_at asc with nulls last, newest created first) so WS upserts keep list order"
  - "Create form uses noValidate so the Russian client-side messages are shown instead of browser-native tooltips"

patterns-established:
  - "Scheduler modals: hideSchedulerModal (no focus change) vs closeSchedulerModal (restores focus to stored opener); only one open at a time"

requirements-completed: [SCHED-12, SCHED-13]

duration: ~35min
completed: 2026-09-26
---

# Phase 8 Plan 07: Scheduler UI Summary

**Foldable sidebar scheduler panel with create/result modals and a live /ws/events client (REST re-sync, backoff reconnect, single guarded poll timer), plus a Node-free Python JS balance check.**

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1a | Panel and modal markup in index.html | cb93281 |
| 1b | Node-free JS balance test | 90fdd2d |
| 1c | Panel rendering, run history, actions in app.js | 3e2d556 |
| 2 | Create-job modal and run-result modal | d80b4a4 |
| 3 | /ws/events live client, re-sync and polling fallback | 1159f5e |

## What was built

- `index.html`: `#scheduler-panel` (collapsed by default, `data-fold-toggle="scheduler-panel-body"`), `#scheduler-create-modal` and `#scheduler-run-modal` (both `role="dialog" aria-modal="true"`). No new script tags, no half-step spacing classes, no `font-medium`.
- `app.js` (block between "MCP servers" and `openSettingsModal`): job/run status label and class maps, timestamp/duration/schedule formatters, `loadSchedulerTasks`/`loadSchedulerRuns`, `renderSchedulerPanel` with cards, expandable run history (max 20), per-status action rows, in-flight guard (`state.schedulerBusy`), native `confirm()` for Cancel/Delete, exact Russian toasts.
- Create modal: type-dependent field groups, once delay vs local datetime, interval unit conversion to seconds, optional `max_runs` (not for once), model select from `state.models` preselecting `state.selectedModel`, server Russian 422 detail shown inline, submit disabled during the request.
- Run modal: status/start/duration/late meta, Markdown result via `renderMarkdown` (the only server-data `innerHTML`), error box for failed runs, skip line for skipped runs, pulsing "Задание выполняется…" for running, collapsible tool trace (name, arguments, result in `<pre>` via textContent), `state.schedulerOpenRunId` tracked so `run_finished` refreshes an open modal in place.
- Live client: `connectEventsWs` (re-sync on every connect, 25 s "ping", backoff capped at `MAX_RECONNECT_DELAY`, no reconnect/polling/toast after `STOP_RECONNECT_CODES` such as 1008), `applySchedulerEvent` (upserts the `frame.task` snapshot for run_started/run_finished/task_updated, removes on task_deleted, toasts on finished success/failed only), `startSchedulerPolling` (idempotent, the single `schedulerPollTimer = setInterval`, ticks only while the panel is expanded) and `stopSchedulerPolling`.
- `tests/test_static_js_syntax.py`: `check_js_balance` scanner (brackets, strings, nested template literals with `${}`, comments, regex literals) with a parametrized check over `ui/static/*.js`, negative samples, and a tricky-valid sample.

## Verification (actually run)

- `pytest tests/test_static_js_syntax.py -q`: 6 passed after each task (real `app.js` reports no errors; all four negative samples detected; tricky valid sample accepted).
- Task 1 automated check: all required ids present in index.html, `role="dialog"` count is 2, `<script` count 4 (unchanged from base), all required Russian strings present in app.js.
- Task 2/3 automated checks: required identifiers present; exactly one `schedulerPollTimer = setInterval`; guard `if (state.schedulerPollTimer)` present; `connectEventsWs` body contains no `showToast(`; the 1008 branch stops polling and returns before the only `startSchedulerPolling()` call.
- `innerHTML` audit: the only new occurrence in app.js is `content.innerHTML = renderMarkdown(...)`.
- Added markup contains 0 occurrences of `py-1.5`, `px-1.5`, `gap-1.5`, `font-medium`.

NOT verified: no browser run (no Node/GUI available here). Runtime behaviour, layout, focus return and live updates against a real agent are left to the 08-08 manual checkpoint; the balance test only proves structural JS validity, not logic.

## Deviations from Plan

### Auto-fixed / additions

**1. [Rule 2 - Missing critical] Client check for empty model**
- The create form shows "Выберите модель" when no model is available (empty select) instead of sending an empty `model` to the server. Files: `ui/static/app.js`.

**2. [Rule 2 - Missing critical] Silent polling/resync loads**
- `loadSchedulerTasks`/`loadSchedulerRuns` accept a `silent` flag; polling, WS-connect re-sync and event fallbacks use it so a stopped agent does not produce a toast every 10 s (UI-SPEC forbids an offline banner for this socket).

**3. [Rule 3 - Blocking] Worktree base reset**
- The worktree HEAD (82a17ff) was an ancestor of the required base 53e62f2; reset to the base per the startup step before any work.

### Other notes

- `form.noValidate = true` on the create form so the Russian inline messages replace native browser tooltips (fields keep `required` for accessibility).
- Minor extra copy not in the UI-SPEC: "Аргументы"/"Результат" labels inside tool-trace entries, "ошибка" marker for failed tool calls, "неизвестная ошибка" fallback when a failed run has no error text.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-08-32..T-08-35 applied as planned (renderMarkdown-only HTML, textContent elsewhere, same-origin credentialed apiFetch, capped backoff, 1008 stops reconnect and polling, one guarded poll timer).

## Self-Check: PASSED

- Files: ui/static/index.html, ui/static/app.js, tests/test_static_js_syntax.py exist and are committed.
- Commits cb93281, 90fdd2d, 3e2d556, d80b4a4, 1159f5e present in git log.
