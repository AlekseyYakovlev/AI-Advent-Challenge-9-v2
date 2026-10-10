---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
verified: 2026-10-10T00:00:00Z
status: human_needed
score: 4/4 roadmap truths verified at deliverable level (SC4 result quality needs a human decision)
has_blocking_gaps: false
overrides_applied: 0
gaps: []
human_verification:
  - test: "Decide whether the Day 25 scenario results are acceptable for SC4 ('goal kept and sources on every turn')"
    expected: "Scenario A goal-kept is 0/12 by the code check and only 8/12 turns per scenario carry sources (the rest are gate refusals or model 'don't know'). Judge/proxy disagree on A (judge: 6 yes). Accept as honestly documented, or request a follow-up."
    why_human: "Result quality is a judgment call; the deliverables exist and the report is honest about the shortfalls."
  - test: "Browser UAT used ПДД.pdf instead of ФЗ-196 (PDF missing in C:\\Projects\\RAG); confirm acceptable"
    expected: "Sources, task-memory panel, per-message block, goal edit/delete/reset and branch restore behave the same on the ФЗ-196 corpus"
    why_human: "Runtime/UI check on a different corpus than planned"
---

# Phase 17: Mini-chat with RAG and task memory (Day 25) Verification Report

**Phase Goal:** the existing chat works as a RAG mini-chat that keeps the dialog goal, clarifications and constraints in task memory and answers every turn with sources
**Status:** human_needed. **Re-verification:** No.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | History kept, retrieval every question, answers show sources | VERIFIED | `agent/rag_turn.py` (`load_history_pairs`, `prepare_rag_turn`) wired in `agent/ws.py`; gate refusals still carry the retrieval details. Eval: 8/12 turns with sources per scenario, the rest are deliberate strict-gate refusals. |
| 2 | Task memory updates after each turn and is visible in UI | VERIFIED | `agent/task_memory.py` (469 lines, `update_task_memory` called at `ws.py:747` and `:1128`), `ChatTaskMemory` table, `task_memory_api` router included in `agent/main.py:457`, panel code in `ui/static/app.js`; 17-11 browser UAT. 0/24 failed extractions in the final main runs. |
| 3 | Follow-up retrieves via task memory + recent history in rewrite and system prompt | VERIFIED | `HistoryContext` / condense in `rag_pipeline.py`, `rag_llm.condense_query`; eval: "а за повторное?" (A02) condensed and found art. 12.9 (cosine 0.695) vs. gated without memory (0.555); 13 follow-ups: 8 answered with memory, 0 without. |
| 4 | Two scripted 10-15 message scenarios run end to end, documented in Day25_report.md | VERIFIED (deliverable) / quality UNCERTAIN | Frozen fixture `tests/fixtures/rag/dialog_scenarios.json` (status frozen, 12+12 turns); `Day25_report.md` (159 lines) has transcripts links, per-turn checks, judge section, limitations; `eval_out/day25` present. Goal-kept code check is 0/12 in A, 11/12 in B; sources 8/12 per scenario. Report states this honestly. |

Tests: `pytest tests/test_task_memory.py tests/test_rag_history.py tests/test_dialog_fixture.py` -> 88 passed. (Full suite started, no failures seen up to 68% before my time limit; not completed.)

## Requirements Coverage

| ID | Status | Evidence |
|----|--------|----------|
| RCHAT-01 | SATISFIED | history + per-question retrieval + sources (truth 1) |
| RCHAT-02 | SATISFIED | deterministic code-merged memory, UI panel (truth 2) |
| RCHAT-03 | SATISFIED | condense with history/memory, memory lines in system prompt (truth 3) |
| RCHAT-04 | SATISFIED as scripted run; result quality caveat | two scenarios ran 12/12 turns, 0 errors; goal-kept weak in A |
| RCHAT-05 | SATISFIED | `Day25_report.md` with per-turn checks (goal kept, sources, memory) |

All five IDs are in REQUIREMENTS.md; no orphaned requirements.

## Code Review Warnings vs must-haves

- WR-01 (rollback expires `chat`/`user_msg` after failed staging): failure-path robustness; does not break a must-have in the normal path (0 extraction failures in eval). Worth a fix.
- WR-02 (done frame overwrites memory panel after chat switch): UI race, not a must-have breach.
- WR-03: `update_task_memory` and persist run inside `chat_locks[chat_id]` (`ws.py:820-822`), so REST edits serialize with turns; no lost update. Only possible blocking up to 30 s.
- WR-04 (goal filter weaker than item filter): weakens the D-04 "user-stated only" claim for goals; documented in the report as a heuristic. Minor.

No debt markers checked beyond review; none blocking.

## Anti-patterns / Deviations

- 17-11 browser UAT used ПДД.pdf (not ФЗ-196) because the PDF is missing; scripts `rag_eval.py` and `e2e_kb_playwright.py` still reference the old file names and will report BLOCKED (info).
- `run_meta.json` hand-edited, disclosed in the report.

## Gaps Summary

No missing deliverable. Residual risk is result quality (scenario A goal retention, false gate refusals 8/22) which the report discloses; routed to human decision, optionally to backlog (WR-01..04 fixes, threshold for short follow-ups).

_Verifier: Claude (gsd-verifier)_
