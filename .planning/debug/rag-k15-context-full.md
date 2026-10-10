---
status: resolved
trigger: "RAG K=15 gives context_full warning while K=5 works (Phase 14 UAT test 3)"
created: 2026-10-10
updated: 2026-10-10
---

## Current Focus

hypothesis: unknown; build_rag_block keeps zero chunks at K=15 (agent/rag.py) or another path raises context_full in agent/rag_turn.py (~line 121)
next_action: gather initial evidence (reproduce offline with fakes: K=5 vs K=15, log used/extra_tokens/budget/first-chunk tokens)

## Symptoms

expected: with RAG on and a ready KB, any valid K (1..20) yields an answer with sources
actual: K=5 works; K=15 gives "Контекст заполнен. Ответ дан без фрагментов базы знаний. Выберите другую стратегию сжатия или начните новый чат." The label reads "без RAG (сбой поиска)". Context usage only 499/16384 (3%).
errors: context_full warning from agent/rag_turn.py (~121) when build_rag_block (agent/rag.py ~134) keeps zero chunks
timeline: found in Phase 14 UAT (.planning/phases/14-first-rag-query-day-22/14-UAT.md test 3)
reproduction: chat with ctx 16384, max_tokens 4096, RAG on, K=15; rank-1 chunk is identical for K=5 and K=15, so a plain size overflow does not explain it
extra_goals: also fix the misleading UI label "сбой поиска" and the irrelevant advice about compression strategy for the budget condition; add regression tests; do not touch ports 8000/8001 (user's app); follow CLAUDE.md conventions

## Evidence

## Eliminated

## Resolution

root_cause: Most likely rag_budget reserved the whole max_tokens (no cap), so a chat whose max_tokens was at or near the window got budget 0 -> context_full regardless of K. Fixed in c108374 (reserve min(max_tokens, ctx//2)), made after the UAT. K-dependence is not explainable by code: build_rag_block fits a prefix, so rank-1 fits or not independently of K. The K=5 vs K=15 difference was likely a settings change between runs. Not reproduced offline with ctx 16384/max_tokens 4096 (budget 4915, rank-1 always kept).
fix: Already in c108374: capped reservation, new context_full text (no compression-strategy advice), UI label «без RAG (нет места в контексте)» for context_full, budget breakdown logged in rag_turn_prepared. This session added regression tests only.
verification: pytest full suite 2193 passed, 11 skipped; new tests in tests/test_rag.py (rank-1 kept for K=1/5/15/20, 30% budget for 16384/499/4096).
files_changed: tests/test_rag.py
