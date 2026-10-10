---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 02
subsystem: testing
tags: [rag, dialog-scenarios, fixture, frozen]
requires: []
provides:
  - frozen two-scenario dialog fixture (A: driver case, B: ФЗ-196 study) with per-turn expectations
affects: [17-05, 17-08, 17-09, 17-10]
tech-stack:
  added: []
  patterns: [frozen fixture with status/frozen_at, shape tests]
key-files:
  created:
    - tests/fixtures/rag/dialog_scenarios.json
    - tests/test_dialog_fixture.py
  modified: []
key-decisions:
  - "Fixture frozen on 2026-10-10 after user approval; later plans must not edit it"
requirements-completed: [RCHAT-04]
duration: n/a
completed: 2026-10-10
---

# Phase 17 Plan 02: Dialog scenarios fixture Summary

Two 12-turn dialog scenarios on the ФЗ-196 + КоАП corpus (A: speeding fine caught by camera with a КоАП-only constraint; B: ФЗ-196 exam summary with a fixed term), approved by the user and frozen with per-turn memory, article and keyword expectations.

## Commits
- 8ca9110: draft fixture and shape tests
- freeze commit (see git log, "test(17-02): freeze approved dialog scenarios")

## User approval
Checkpoint answer (AskUserQuestion): "Approve as is". No changes were applied.

## Corpus check
Query (read-only, sqlite3 URI `mode=ro`, `eval_out/day23/eval.db`, gitignored so read from the main checkout):
`select distinct source, section from kbchunk where kb_id=2 order by source, section` (1180 rows).

Section string for each non-null `expect_article`:
- 2: "Глава I > Статья 2. Основные термины" (ФЗ-196)
- 12.9: "Раздел II > Глава 12 > Статья 12.9. Превышение установленной скорости движения"
- 12.12: "Раздел II > Глава 12 > Статья 12.12. Проезд на запрещающий сигнал светофора..."
- 20.25: section "... > Статья 20.25" present
- 23: "Статья 23" (ФЗ-196, medical support)
- 24: "Глава IV > Статья 24. Права и обязанности участников дорожного движения"
- 25: "Глава IV > Статья 25. Основные положения, касающиеся допуска к управлению..."
- 26: "Статья 26" (ФЗ-196, age)
- 28: "Глава IV > Статья 28. Основания прекращения, приостановления действия права..."
- 32.2: "Раздел V > Глава 32 > Статья 32.2. Исполнение постановления о наложении административного штрафа"

## Frozen file hashes (frozen_at 2026-10-10)
- sha256 of working-copy bytes: `ebb156e270eaec50baa18fac2dcb5519d112fdee92a49c81998195651eb3dcc7`
- sha256 with LF endings: `ebb156e270eaec50baa18fac2dcb5519d112fdee92a49c81998195651eb3dcc7`
(the worktree copy already has LF endings, so both values are identical; a CRLF checkout on Windows would give a different first value)

## Verification (actually run)
- `python -m pytest tests/test_dialog_fixture.py -q`: 17 passed, includes `test_fixture_is_frozen`.
- `grep -c '"status": "frozen"'` prints 1.
- `git status --porcelain eval_out/day23` printed nothing (source DB untouched).

## Caveats recorded for later plans
- A02 «а за повторное?» expects article 12.9, but 12.9 defines repeat offences only for ч.3-5, not ч.2 (the 35 км/ч case); a correct answer may say there is no special repeat rule. Per D-17 this is noted, not edited.
- A05 keyword "20" would miss an answer that writes «двадцати».
- Per Day 24, age-type questions (B06) may be gated at threshold 0.67 without the condensed query.

## Deviations from Plan
None in content. Process: worktree HEAD was behind the required base and was reset to a96b192 at start per the startup check. The checkpoint was resolved by the coordinator relaying the user's answer.

## Known Stubs
None.

## Self-Check: PASSED
Files exist (fixture, tests); pytest passes; commits present.
