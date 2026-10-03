---
phase: 14-first-rag-query-day-22
plan: 02
status: paused
subsystem: rag-eval
tags: [rag, fixture, control-set]
requirements: [RAG-06]
key-files:
  created:
    - tests/fixtures/rag/control_set.json
    - tests/test_rag_fixture.py
---

# Phase 14 Plan 02: RAG control set (PAUSED at Task 2 checkpoint)

Draft 10-question control set (status draft) and schema test are committed; awaiting user approval (D-13) before Task 3 freezes it.

## Progress

| Task | Name | Status | Commit |
| ---- | ---- | ------ | ------ |
| 1 | Draft control set + schema test | done | 8b84824 |
| 2 | User approval (checkpoint) | awaiting | - |
| 3 | Freeze fixture, record sha256 | not started | - |

Verified: `pytest tests/test_rag_fixture.py -q` -> 9 passed, 1 xfailed.

## Draft table

| id | cat | question | expected | sources |
| -- | --- | -------- | -------- | ------- |
| Q01 | direct | Min age for category B / A | 18 (M/A1: 16; exams from 17) | FZ_N_196_FZ ст. 26 |
| Q02 | direct | Are vehicles in operation subject to technical inspection | yes | FZ_N_196_FZ ст. 17 |
| Q03 | direct | Medical support content, periodic medical exam frequency | mandatory exams, periodic at least once per 2 years | FZ_N_196_FZ ст. 23 |
| Q04 | direct | Fine for speeding 40-60 km/h | 1500-2250 rub | N_195-FZ ст. 12.9 |
| Q05 | direct | Penalty for drunk driving | 45000 rub + 1.5-2 years deprivation | N_195-FZ ст. 12.8 |
| Q06 | direct | Fine for no seatbelt | 1500 rub | N_195-FZ ст. 12.6 |
| Q07 | synthesis | Driving without OSAGO and the fine | forbidden (ст. 19 п.2); 800 rub (12.37 ч.2) | FZ ст. 19 + KoAP 12.37 |
| Q08 | synthesis | Age for licence and fine for driving without right | 18; 5000-15000 rub (12.7 ч.1) | FZ ст. 26 + KoAP 12.7 |
| Q09 | out_of_corpus | Transport tax rate for 150 hp car | no answer in base | - |
| Q10 | out_of_corpus | OSAGO base tariff and bonus-malus | no answer in base | - |

## Deviations / Assumption Drift (advisory)

- Plan/CONTEXT cited "ФЗ-196 Статья 19 age requirement". In the actual text, age requirements are in Статья 26; Статья 19 covers grounds for banning vehicle operation. Fixture uses Статья 26 for age questions and Статья 19 (п. 2, OSAGO) in Q07.
- Raw PDF extraction splits article headings across lines (Статья / 26. / ...); the loader's cleaning (`load_document`) rejoins them, and article numbers were checked against ARTICLE_RE on the cleaned text (FZ-196: 34 articles found).
- Out-of-corpus claims verified by text search: no matches for "транспортный налог", "базовый тариф", "КБМ" in either cleaned text.

## Pending

Task 3: apply approved edits, set status frozen + frozen_at, remove xfail marker, record sha256 here, commit `test(14-02): freeze RAG control set`.
