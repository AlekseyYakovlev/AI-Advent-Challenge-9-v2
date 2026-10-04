---
phase: 16-citations-and-anti-hallucination-day-24
verified: 2026-10-04T00:00:00Z
status: human_needed
score: 4/4 roadmap truths verified (plan-level truths all verified in code/tests)
has_blocking_gaps: false
overrides_applied: 0
human_verification:
  - test: "Run tests/test_supervisor.py::test_agent_restarts_within_5_seconds on a machine where ports 8000/8001 are free"
    expected: "Agent restarts within 5 seconds"
    why_human: "Deselected in every gate, never run (timed out once; can clash with the user's app on 8000/8001). Not Phase 16 code, so it does not block the goal, but it is unverified."
  - test: "Decide on review warnings WR-01 (trivial quotes become 'exact' and hide the unsupported warning) and WR-02 (auto quotes stored as 'exact' and shown with the green chip)"
    expected: "Accept as known limitations or schedule a fix"
    why_human: "They weaken the strength of the 'verified' signal. They do not break the literal must-haves, so this is a product call."
---

# Phase 16: Citations and anti-hallucination (Day 24) - Verification Report

**Phase Goal:** every RAG answer carries verifiable sources and quotes, and the assistant says "не знаю" instead of guessing when retrieval is not relevant enough
**Status:** human_needed (all automated checks pass; two items need a human decision)
**Re-verification:** No, initial verification

## Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Each RAG answer shows answer text, sources (source + section / chunk_id) and quotes from retrieved chunks | VERIFIED | `agent/rag_cite.py` (parse_tail, process_answer, auto quotes). `agent/rag_turn.py::finalize_rag_turn` is called from `agent/ws.py:1047` before persist. The quotes go into `done.rag` and `Message.rag_sources`. The UI builds the quotes block in `ui/static/app.js` (`buildRagQuotesBlock`). Playwright S3/S4 on the isolated copy: 3 quotes with chips, sources collapsed, no «Цитаты:» tail. Live Day 24 run: Q02-Q05 have sources and quotes. |
| 2 | Each quote is marked verified/unverified by a server-side check against the cited chunk; sources rendered from chunk metadata, not model text | VERIFIED (caveat WR-01) | `rag_cite.py`: normalized exact match, then difflib fuzzy at 0.9 (`FUZZY_MIN_CHARS=25`, `MIN_PART_CHARS=8`). The states are exact / fuzzy / unverified, with rebound and invalid-ref handling. Sources are built from chunk metadata, and chunk text is never put in the payload (`kept_chunks` lives outside the payload). The tests in `tests/test_rag_cite.py` pass. Caveat: WR-01 means very short or common-phrase quotes can reach `exact`. |
| 3 | An out-of-corpus question gets "не знаю" plus a clarifying question, enforced in code when best relevance is below the threshold | VERIFIED | `agent/rag_turn.py:147` gates on `verdict == below_threshold or not chunks` and sets `reply_text=build_idk_reply(trace)`. `agent/ws.py:700-748` (`_complete_gated_turn`) sends and stores that reply with no LLM call. `tests/test_rag_ws.py` asserts zero `stream: true` requests. Live run: Q09 and Q10 were both gated by code (2/2). Playwright S6: «Не знаю:» + «Уточните», `gated: true`. |
| 4 | The Day 24 report records, per control question, sources present, quotes present, meaning matches quotes, correct "не знаю" | VERIFIED | `Day24_report.md` has a per-question table with all four columns for Q01-Q10. `eval_out/day24/` contains cite_summary, answers.csv/md, raw records, run_meta and judge_meta. `scripts/rag_eval.py cite` exists, and `tests/test_rag_report_day24.py` pins the structure. |

**Score:** 4/4

Plan-level truths (16-01..16-08, D-01..D-18) were spot-checked against code and tests. This includes strict flag default-on with an idempotent migration, payload v3, strict-off preserving Phase 15 behaviour, the strict switch, the cited-first source ordering, and the docs sync (strict mode appears in ARCHITECTURE, API_SPEC, USER_GUIDE and TESTING_GUIDE). No contradictions found.

## Behavioral Spot-Checks and Test Run

| Check | Command | Result | Status |
|-------|---------|--------|--------|
| Full suite, excluding the supervisor restart test | `pytest tests/ -q --deselect tests/test_supervisor.py::test_agent_restarts_within_5_seconds` | 1880 passed, 1 skipped, 1 deselected | PASS |
| `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` | not run, by instruction | n/a | UNVERIFIED (not Phase 16 code) |
| Playwright E2E (isolated ports 18000/18001) | per 16-08-SUMMARY; not re-run by the verifier | 13 passed, 0 failed, plus the Phase 14/15 scripts pinned to strict off | Claimed by SUMMARY, not independently re-run |

Ports 8000/8001 were not touched.

## Requirements Coverage

| Requirement | Source Plans | Status | Evidence |
|-------------|-------------|--------|----------|
| CITE-01 | 16-03, 16-06, 16-08 | SATISFIED | Truth 1 |
| CITE-02 | 16-01, 16-03, 16-06, 16-08 | SATISFIED (caveat WR-01) | Truth 2 |
| CITE-03 | 16-02, 16-03, 16-06, 16-08 | SATISFIED | Truth 3 |
| CITE-04 | 16-05, 16-07 | SATISFIED | Truth 4 |

All 4 IDs are marked complete in REQUIREMENTS.md and mapped to Phase 16. No orphaned requirements.

## Review Warnings (advisory) vs must-haves

| ID | Breaks a must-have? | Assessment |
|----|---------------------|------------|
| WR-01 trivial quotes get "verified" | No (weakens CITE-02) | The check still performs the substring test as specified. A trivial quote can mask the "not supported" warning. Recommend a minimum total length for `exact`. |
| WR-02 auto quotes stored as `exact` | No | The `auto` flag is kept and the UI shows "подобрана автоматически", but the green chip is misleading. |
| WR-03 more than 10 quote lines leak into the answer | No | Edge case that needs a model to emit more than 10 quote lines. It can inflate `supported`. |
| WR-04 old chats become strict; gated path has no rollback/error handling | No | The default-on is the documented D-15 decision, and the docs state it. The missing rollback in `_complete_gated_turn` is a robustness gap, not a must-have failure. |

## Anti-Patterns

No TBD/FIXME/XXX markers in the phase's Python files. No blocker stubs found. IN-01 (`assert` for narrowing) and IN-03 (duplicated payload-shape path) are info only.

## Notable observation (not a gap)

In the live strict run, the gate falsely refused 3 of 8 answerable questions (Q01, Q07, Q08). Their best cosine was 0.63-0.65 against the Phase 15 threshold of 0.67, which this phase deliberately did not change. The report documents it and the user approved it. The goal says "не знаю" when retrieval is not relevant enough, and that behaviour is working as specified. The cost is recall at the current threshold.

## Gaps Summary

No blocking gaps. The phase goal is achieved in code, tests, the live evaluation and the browser run. Status is `human_needed` only because of two items. First, the supervisor restart test was never run. Second, the product decision on WR-01/WR-02 is still open.

---

_Verified: 2026-10-04_
_Verifier: Claude (gsd-verifier)_
