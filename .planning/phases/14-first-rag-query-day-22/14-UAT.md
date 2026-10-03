---
status: complete
phase: 14-first-rag-query-day-22
source: [14-01-SUMMARY.md, 14-02-SUMMARY.md, 14-03-SUMMARY.md, 14-04-SUMMARY.md, 14-05-SUMMARY.md, 14-06-SUMMARY.md, 14-07-SUMMARY.md, 14-08-SUMMARY.md]
started: 2026-10-03T12:00:00Z
updated: 2026-10-03T12:00:00Z
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Stop the app, start `python run.py` from scratch. Servers boot without errors, migration completes on existing app.db, UI loads with existing chats.
result: pass

### 2. Attach a KB to a chat and see RAG controls
expected: In a chat header you see a RAG badge, an on/off switch, a KB select (only ready KBs) and a K input (1..20, default 5). Choosing a KB and switching RAG on updates the badge; reloading the page keeps the setting.
result: pass

### 3. RAG answer shows sources
expected: With RAG on and a ready KB, ask e.g. "Какой штраф за превышение скорости на 40-60 км/ч?". The answer is generated, labeled with the RAG mode, and has a collapsed "Источники (N)" block under it. Expanding it lists file, section, chunk_id, score and loads the fragment snippet text lazily.
result: issue
reported: "При установке K=5 всё ок. При установке K=15 получаю \"Контекст заполнен. Ответ дан без фрагментов базы знаний. Выберите другую стратегию сжатия или начните новый чат.\""
severity: major

### 4. Without RAG the answer has no sources
expected: Switch RAG off (KB stays attached) and ask the same question. The answer is plain, without a sources block; the user message in the history is shown unchanged (no injected fragments).
result: pass

### 5. Fail-soft: embedder unavailable or KB deleted
expected: With RAG on, stop/unload the embedding model in LM Studio (or delete the attached KB) and send a message. You still get a plain answer, with a visible warning line and a warning toast; the user message is not deleted.
result: pass

### 6. Sources persist after reload
expected: Reload the page and reopen the chat from test 3. The "Источники (N)" block is still present under that answer.
result: pass

### 7. Day22 report and eval artifacts
expected: `Day22_report.md` exists and shows the 10-question control set, a no-RAG vs RAG verdict table and the hit@k comparison nomic (0.12/0.25/0.25) vs bge-m3 (0.75/0.88/0.88); `eval_out/day22/` has raw/, retrieval.md, answers.csv, run_meta.json.
result: pass

## Summary

total: 7
passed: 6
issues: 1
pending: 0
skipped: 0
blocked: 0

## Gaps

- truth: "With RAG on and a ready KB, the answer is labeled with RAG mode and has a collapsed sources block (for any valid K 1..20)"
  status: failed
  reason: "User reported: K=5 works; K=15 gives 'Контекст заполнен. Ответ дан без фрагментов базы знаний. Выберите другую стратегию сжатия или начните новый чат.' (context_full warning, answer without fragments). Screenshot evidence: context usage only 499 / 16384 (3%) when the warning appeared, label 'без RAG (сбой поиска)'."
  severity: major
  test: 3
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""
