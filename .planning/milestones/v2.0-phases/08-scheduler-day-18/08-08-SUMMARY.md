---
phase: 08-scheduler-day-18
plan: 08
subsystem: docs-verification
tags: [docs, uat, playwright, scheduler]

requires:
  - phase: 08-scheduler-day-18
    provides: "Scheduler engine, REST API, LLM tools and UI (08-01..08-07)"
provides:
  - "docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/TESTING_GUIDE.md describe the shipped scheduler"
  - "Day 18 demo walkthrough executed end to end in a real browser (Playwright) against real LM Studio + real filesystem MCP"
affects: []

tech-stack:
  added: []
  patterns:
    - "UAT on an isolated app copy at ports 18000/18001 with a scratch DB (the app hard-codes 8000/8001)"

key-files:
  created: []
  modified:
    - docs/API_SPEC.md
    - docs/ARCHITECTURE.md
    - docs/TESTING_GUIDE.md
    - tests/test_scheduler_api.py

key-decisions:
  - "Accepted limitation: with the local qwen3.5-9b model a natural phrase such as 'через минуту прочитай файл ... и перескажи его' can make the model execute the request immediately (MCP read tool) instead of calling schedule_task; an explicit 'запланируй ...' / 'вызови schedule_task ...' works. The tool description already says 'use only when the user asks to do something later'; no prompt change was made (user decision, option 1). One sample, frequency not measured."

requirements-completed: [SCHED-01, SCHED-02, SCHED-03, SCHED-04, SCHED-05, SCHED-06, SCHED-07, SCHED-08, SCHED-09, SCHED-10, SCHED-11, SCHED-12, SCHED-13, SCHED-14]

duration: ~1h (docs + Playwright UAT)
completed: 2026-09-26
---

# Phase 8 Plan 08: Docs sync and Day 18 demo verification Summary

**Scheduler documented in API_SPEC / ARCHITECTURE / TESTING_GUIDE; the Day 18 demo was run by Claude through Playwright (real LM Studio model + real filesystem MCP) with 28/30 first-pass checks green, both remaining FAILs being script artifacts that pass on re-check; one model-behaviour limitation accepted by the user.**

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | API_SPEC, ARCHITECTURE, TESTING_GUIDE for the scheduler; full suite | 2b626e3 |
| 1+ | 401 test for every scheduler REST route (Rule 2 add-on) | be79003 |
| 2 | Day 18 demo walkthrough (checkpoint:human-verify) | executed via Playwright, no code commit |

Full suite after Task 1: **881 passed** (wave 4 was 871; +10 is the parametrized 401 test).

## Task 2: demo walkthrough (executed by Claude, not manually)

Environment: isolated copy of the project at UI :18000 / Agent :18001 with a scratch DB (the app hard-codes ports 8000/8001 in `app.js`, `login.html` and `agent/state.py`; the user's own app on 8000/8001 was not touched). Only those port constants were patched in the copy. LLM = real LM Studio `qwen/qwen3.5-9b` (loaded), MCP = real `filesystem.exe`. Headless Chromium via Playwright; two users in separate browser contexts.

| Step | Result |
| ---- | ------ |
| 1 empty panel "Заданий пока нет" | PASS |
| 2 chat -> `schedule_task` job appears live as "активно", "Однократно" | PASS only with an explicit instruction (see limitation) |
| 3-4 card goes "выполняется" -> "успешно" live, toast "Задание «…» выполнено" | PASS (first run flagged FAIL by a script bug: the no-reload marker was set before a `reload()`; re-check with the marker set after load: PASS) |
| 5 run modal shows the Markdown summary; "Показать ход выполнения" lists the MCP read call (`mcp__filesystem__read_text_file`) | PASS |
| 6 interval 20 s, max 2: two runs, "завершено", 2/2 | PASS |
| 7 "Запустить сейчас" (toast "Запуск начат"), "Пауза", "Продолжить", "Отменить" (confirm, history kept), "Удалить" (confirm, card gone) | PASS |
| 8 "покажи мои задания" -> `list_scheduled_tasks`; message without cancel intent does not cancel; "отмени задание <id>" cancels | PASS |
| 9 hard kill of the whole app, restart after fire time + late threshold: job runs exactly once, `is_late`, "с опозданием" chip in the UI | PASS |
| 10 second user sees none of the first user's jobs/events; foreign id -> 404; 0 leaked live frames | PASS |
| Browser console errors | Only `ERR_CONNECTION_REFUSED` while the app was intentionally stopped in step 9; a session with the app up shows none (re-check PASS) |

### Accepted limitation (user decision, option 1)

For the plan's natural phrase "через минуту прочитай файл <path> через MCP и перескажи его" the local model did not call `schedule_task` within 180 s: it read the file through the MCP tool and summarised it immediately (chat messages 1-2 in the scratch DB). Re-sent as "Вызови инструмент schedule_task: delay_seconds=60, prompt: ..." it created the job (messages 3-4). This is model tool-selection behaviour, not a scheduler defect; the mechanism (tool -> job -> run -> UI) works. Decision: accept and document; no system-prompt change in this phase. Sample size 1; not measured on other models (e.g. DeepSeek).

### Minor UI note (not fixed)

In the narrow sidebar panel a run row with the "с опозданием" chip wraps onto several lines. Cosmetic only.

## Deviations from Plan

1. **[Rule 2] 401 test for every scheduler route** added (`be79003`): TESTING_GUIDE lists "401 routes" but no test asserted it.
2. **Task 2 executed by Claude through Playwright** on an isolated app copy instead of the user following the manual steps on `python run.py` at 8000/8001 (the user asked for it; stopping the user's running app was not permitted and `run.py` would kill it).

## Not verified

Other models, a real Ctrl+C (the restart step uses a hard kill of the process tree), a second user watching live events while the first user's runs execute (leak check covered the whole session, 0 frames).

## Self-Check: PASSED
