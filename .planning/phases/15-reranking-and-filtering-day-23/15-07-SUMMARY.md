---
phase: 15-reranking-and-filtering-day-23
plan: 07
subsystem: ui
tags: [rag, rerank, vanilla-js, popover, static-tests]
requires:
  - phase: 15-02
    provides: REST /chats/{id}/rag with search fields
  - phase: 15-05
    provides: payload v2 (done.rag, stored rag_sources)
provides:
  - "Поиск ⚙" popover with candidate-K, threshold and four independent stage switches
  - collapsed "Детали поиска" block with candidate table and status chips
  - grey below-threshold line
affects: [15-12]
tech-stack:
  added: []
  patterns: [DOM built with mcpEl/createElement and textContent only]
key-files:
  created: [tests/test_rag_search_static.py]
  modified: [ui/static/index.html, ui/static/app.js]
key-decisions:
  - "Details block rendered only via renderMessages; the done frame already reloads the tree, so live and history share one path"
requirements-completed: [RANK-01, RANK-02, RANK-03, RANK-04, RANK-05, RANK-06]
duration: 20min
completed: 2026-10-03
---

# Phase 15 Plan 07: Search UI Summary

Search settings popover (candidate-K, calibrated/override threshold, four independent switches) and the collapsed "Детали поиска" candidate table with status chips, all rendered with textContent only.

## Tasks

1. Popover and settings wiring: commit c84e048 (index.html, app.js).
2. Details block, grey threshold line, static tests: d31cfd2 (app.js, tests/test_rag_search_static.py).

## Verification (observed)

- `python -m pytest tests/test_rag_search_static.py tests/test_static_js_syntax.py tests/test_rag_static.py -q`: 87 passed.
- Precondition (14-06 anchors) passed. Browser behaviour was NOT run here; it is covered by plan 15-12 (Playwright on the isolated copy).

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree base reset.** Worktree HEAD was 87d51ec, not the expected c023f28; reset to c023f28 per the startup instruction before any work.

**2. Single call site for buildRagDetailsBlock.** The plan expects at least 3 occurrences of `buildRagDetailsBlock(` (history and done paths). The `done` frame calls loadChatTree, which re-renders through renderMessages, so one call site covers both; there are 2 occurrences (definition and call). Behaviour is the same.

**3. Details markup split into helpers.** buildRagDetailsBlock delegates to buildRagCandidatesTable, buildRagStatusCell, buildRagDetailRow and ragSkipReasonText. The static test guards the whole group (no innerHTML, showToast, yellow; status tokens present) instead of only the single function.

**4. saveChatRag** now whitelists patch keys into the body (mode, kb_id, top_k and the six search fields) and takes an optional error-toast text.

## Known Stubs

None.

## Self-Check: PASSED
