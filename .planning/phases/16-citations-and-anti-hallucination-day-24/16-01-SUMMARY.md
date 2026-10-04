---
phase: 16-citations-and-anti-hallucination-day-24
plan: 01
subsystem: rag
tags: [citations, quote-verification, difflib, anti-hallucination]
requires: []
provides:
  - "agent/rag_cite.py: STRICT_INSTRUCTION, normalize, match_quote, parse_tail, body_refs, is_idk, process_answer, build_idk_reply, Quote, CitationResult"
affects: [16-03, 16-04, 16-05]
tech-stack:
  added: []
  patterns: ["pure I/O-free rule module shared by ws and eval script"]
key-files:
  created: [agent/rag_cite.py, tests/test_rag_cite.py]
  modified: []
key-decisions:
  - "Cited fragment checked first (exact+fuzzy), then other fragments exact-before-fuzzy, per D-06"
  - "Fuzzy threshold fixed at 0.9 with sliding-window difflib (autojunk=False)"
duration: ~15min
completed: 2026-10-04
---

# Phase 16 Plan 01: Citation module Summary

Pure `agent/rag_cite.py` with quote parsing, two-step (exact, fuzzy 0.9) verification, rebound and bad-reference handling, auto quotes, «Не знаю» detection and a code-built «Не знаю» reply, covered by 69 unit tests.

## Verification (actually run)
- `python -m pytest tests/test_rag_cite.py tests/test_rag_rank.py -q`: 125 passed.
- Acceptance greps: `autojunk=False` x1; no agent.rag / shared.database / get_logger imports; no bare `except:`; `process_answer('q','',...)` prints `True False []`.

## Deviations from Plan
- All three tasks were implemented and committed in one commit (cac8f56) rather than three, because they build one module/test file together; each task's behavior list has its own tests. No TDD RED-only commit was made (tests were written together with the code and passed on first run), so no separate `test(...)` commit exists.
- The per-quote `except Exception` in `process_answer` is intentionally broad (fail-soft per plan D-06).
- `.planning/HANDOFF.json` appeared modified in the worktree; not touched by this plan and not committed.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
- agent/rag_cite.py and tests/test_rag_cite.py exist; commit cac8f56 exists.
