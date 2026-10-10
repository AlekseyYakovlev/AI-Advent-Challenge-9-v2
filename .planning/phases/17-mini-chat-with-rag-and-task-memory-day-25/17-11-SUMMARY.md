---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 11
subsystem: verification-docs
tags: [playwright, e2e, docs, task-memory, rag, day25]
requires: ["17-07", "17-09", "17-10"]
provides:
  - "scripts/e2e_rag_dialog_playwright.py: ten-scenario browser UAT of task memory and history-aware retrieval"
  - "eval_out/day25/screens/: seven screenshots of the new blocks"
  - "Day 25 sections in ARCHITECTURE, API_SPEC, USER_GUIDE, TESTING_GUIDE"
affects: []
key-files:
  created: [scripts/e2e_rag_dialog_playwright.py, eval_out/day25/screens/]
  modified: [docs/ARCHITECTURE.md, docs/API_SPEC.md, docs/USER_GUIDE.md, docs/TESTING_GUIDE.md]
requirements-completed: [RCHAT-01, RCHAT-02, RCHAT-03]
completed: 2026-10-10
---

# Phase 17 Plan 11: Browser UAT, docs sync and final gate Summary

The task-memory UI and history-aware retrieval pass a ten-scenario Playwright run on the isolated copy (14 PASS, 0 FAIL, exit 0), the four docs describe the shipped Day 25 behaviour, and the full suite passes (2188 passed, 11 skipped).

## Commits
- f7ed8b7 test(17-11): browser UAT of task memory and history-aware retrieval (script, screenshots)
- 5bb3b4b docs(17-11): document task memory, history-aware retrieval and Day 25 commands

## Task 1: Playwright run (observed)
`python scripts/e2e_rag_dialog_playwright.py` on the isolated copy (UI 18000, Agent 18001), real LM Studio (`qwen/qwen3.5-9b`, nomic embedder), exit code 0, `summary: 14 passed, 0 failed`:
- S1 PASS: block hidden before RAG; after RAG on and reload: labels «Цель», «Уточнено», «Ограничения и термины», goal «—», «Пока пусто».
- S2 PASS: sources follow the answer; sidebar goal appears without a reload and equals the API goal; stored payload `v == 4` with `task_memory`; per-message block collapsed, summary «Память задачи (4) · новое: 4», «новое» after opening; `failed` false.
- S3 PASS: `search.condensed` true, `history_pairs` 1, details show «Уточнён:» and «история»; earlier messages still on the page. The model answered this follow-up with «Не знаю» (recorded as it happened).
- S4 PASS: Escape restores the old goal; «Сохранить» stores the new goal (API confirms).
- S5 PASS: «×» removed item id 1 without any dialog; API dropped it.
- S6 PASS: through the page's own «↩ отсюда» control; sidebar and API goal equal the first answer's snapshot goal (the manual edit of S4 is gone, per D-11).
- S7 PASS: confirm text starts «Сбросить память задачи этого чата?»; accept clears and shows the empty state; after a new turn, dismiss leaves memory unchanged.
- S8 PASS: default 3; 0 accepted and kept; 25 clamped to 10; back to 3.
- S9 PASS: chat without RAG: block hidden, no `task_memory` in the payload, no per-message block, API `task_state` null.
- S10 PASS: after reload the per-message blocks render, no page errors, no script/img inside the blocks.
- Screenshots (7): `S1-empty-task-memory.png`, `S2-first-turn-task-memory.png`, `S3-followup-search-details.png`, `S4-goal-edited.png`, `S6-branch-restored.png`, `S8-history-turns-popover.png`, `S10-after-reload.png` in `eval_out/day25/screens/`.
- Ports: nothing on 8000/8001 was stopped or touched (the user's instance was not running during the checks; port 8000/8001 listeners were not present). Only processes started by the script were stopped.

## Task 2: docs and gate (observed)
- Docs: ARCHITECTURE «Task memory and history-aware retrieval (Day 25)» (also lists task memory as its own layer for RAG chats); API_SPEC (`task_state`, three `task-memory` routes with 403/404/409/415/422, `history_turns`, branch restore, payload v4, env vars `TASK_MEMORY_ENABLED` / `TASK_MEMORY_TIMEOUT`); USER_GUIDE («Память задачи», «Ходов истории», «Уточнён:», «память не обновлена», link to Day25_report.md); TESTING_GUIDE (Day 25 tests, eval commands, Playwright script). Acceptance greps all met (counts: task_memory 5, ChatTaskMemory 1, TASK_MEMORY_ENABLED 2, task-memory 4, task_state 4, history_turns 1, «Память задачи» 3, «Ходов истории» 2, Day25_report.md 2, test_task_memory_ws.py 1, `rag_eval.py dialog` 4, e2e_rag_dialog_playwright.py 2, goal_adherence 1).
- Full suite: `python -m pytest tests/ -q`: 2188 passed, 11 skipped, 0 failed.

## Deviations from Plan
1. [Rule 3 - Blocking] The ФЗ-196 PDF named in the plan (`19951210_20260626_FZ_N_196_FZ.pdf`) and the old КоАП file name are absent from `C:\Projects\RAG`; the folder now holds `ПДД.pdf` and `КОАП РФ.pdf`. The first run exited 2 (BLOCKED, PDFs missing). The script now uses the ФЗ-196 file when it exists and otherwise `ПДД.pdf` (and points the shared preflight at `КОАП РФ.pdf`); the questions were rephrased to be document-neutral ("по правилам дорожного движения", "по загруженному документу"). The run therefore used the traffic rules PDF (91 chunks), not ФЗ-196. The ДТП definition and its participants exist in both documents, so the scenarios test the same behaviour.
2. [Rule 1 - Bug, script] First full run hung in S9: a native `confirm` («Модель ... не загружена. Загрузить?») appeared in the new non-RAG chat and my S5 dialog listener did not answer it. I killed my own script tree (PID of the script and the app it had started on 18000/18001) and replaced the per-scenario listeners with one policy handler that records every dialog and dismisses it unless a scenario asks to accept. One rerun, then all green.
3. The first full pytest run (executed in parallel with the browser run) stopped on `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` (timeout under load). It passes alone (1 passed) and the complete rerun with nothing else running passed fully. No test was changed or skipped.

## Assumption Drift (advisory)
- Found during: Task 1 preflight. Planned: `C:\Projects\RAG` holds the ФЗ-196 PDF. Actual: it holds `ПДД.pdf` and `КОАП РФ.pdf` only. Why: the files were renamed or replaced after Phase 16. Other scripts that name the old files (`scripts/rag_eval.py` default list, `e2e_kb_playwright.py` constants) were not changed and will still report BLOCKED if run.

## Open items
- `rag_eval.py` and the Phase 16 E2E scripts still reference the old PDF file names (not touched, out of scope).
- Screenshots were taken with a traffic-rules document, so «Источники» show ПДД, not ФЗ-196.
- `bm-sdk state.advance-plan / update-progress / record-metric` were not attempted (they failed on this STATE.md in earlier plans); only `roadmap.update-plan-progress` was used (see the final report).

## Known Stubs
None.

## Self-Check: PASSED
- scripts/e2e_rag_dialog_playwright.py, the seven screenshots and the four docs exist; commits f7ed8b7 and 5bb3b4b exist; neither commit message has a Co-Authored-By line; `.planning/HANDOFF.json` and `.planning/debug/rag-speeding-fine-below-threshold.md` were never staged.
