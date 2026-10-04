---
phase: 16-citations-and-anti-hallucination-day-24
plan: 06
subsystem: ui
tags: [rag, citations, frontend, vanilla-js]
requires:
  - phase: 16-02
    provides: "strict flag on GET/PUT /api/v1/chats/{id}/rag"
provides:
  - "Strict-mode switch in the Poisk popover"
  - "Quotes block with verification chips, amber/grey answer lines, cited-source marks"
affects: [16-08]
tech-stack:
  added: []
  patterns: ["all payload strings through mcpEl/textContent", "static guard tests for ids, copy, no innerHTML"]
key-files:
  created: [tests/test_rag_cite_static.py]
  modified: [ui/static/index.html, ui/static/app.js]
key-decisions:
  - "Switch does not light the Poisk button (anyOn uses the four stage flags only)"
requirements-completed: [CITE-01, CITE-02, CITE-03]
duration: 20min
completed: 2026-10-04
---

# Phase 16 Plan 06: Citations UI Summary

Chat UI for the Phase 16 evidence: a first-in-list "Строгий режим" switch (saved through PUT /rag, reverts on failure), an expanded "Цитаты (N)" block with exact/fuzzy/unverified chips built only from the stored payload, amber/grey answer lines, and cited sources listed first with a "цитируется" chip.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Strict switch | bc61d6e | `#rag-strict` markup, popover render, change handler, `'strict'` in `saveChatRag` whitelist |
| 2 Quotes block | e4115ff | `buildRagQuoteChip`, `buildRagQuoteRow`, `buildRagQuotesBlock`; card placed before Istochniki in `renderMessages` |
| 3 Answer lines and marks | 1cf5519 | amber / model_idk / invalid_refs / answer_empty lines, zero-candidate threshold guard, `isCited` ordering and chip |

## Verification (observed)

`python -m pytest tests/test_rag_cite_static.py tests/test_rag_static.py tests/test_rag_search_static.py tests/test_static_js_syntax.py -q` -> 119 passed (final run after Task 3). Earlier runs: 68 passed after Task 1, 108 after Task 2. Browser behaviour was NOT verified here (planned for 16-08 with Playwright).

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree base reset.** The worktree base differed from the expected commit; reset to 317a493 as the startup check prescribes, before any edit.

**2. Acceptance count drift.** The criterion `grep -o 'role="switch"' index.html | wc -l` expects 5, but the page already has 5 before this plan (header `#rag-toggle` plus four stage switches), so it is 6 now. The check was written without counting `#rag-toggle`; the new switch itself is covered by the static test.

**3. Task 1 test file was committed with only Task 1 tests**, with later tests added in Tasks 2 and 3, so every commit passes its own tests.

`app.js`/`index.html` had no foreign uncommitted changes (`git diff` clean at start); staged diffs contain no `context_length`/`max_tokens` lines.

## Known Stubs

None.

## Threat Flags

None. All dynamic strings render via `textContent`; static tests forbid innerHTML/insertAdjacentHTML/outerHTML in the quote, meta and source functions.

## Self-Check: PASSED
