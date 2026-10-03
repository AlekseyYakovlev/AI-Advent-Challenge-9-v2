---
status: investigating
trigger: "Phase 14 UAT gap: RAG K=15 raises context_full at 3% context usage; see 14-UAT.md"
created: 2026-10-03
updated: 2026-10-03
---

## Current Focus

hypothesis: unknown — rank-1 chunk identical for K=5 and K=15, so a plain size overflow does not explain K-dependence
test: gather initial evidence (agent/rag_turn.py ~L121, agent/rag.py build_rag_block ~L134, rag_budget, CYRILLIC_SAFETY)
expecting: find which input to rag_budget/build_rag_block depends on K
next_action: gather initial evidence

## Symptoms

<!-- DATA_START -->
expected: With RAG on and a ready KB, answer is generated, labeled with RAG mode, has collapsed "Источники (N)" block, for any valid K (1..20).
actual: K=5 works. K=15 gives "Контекст заполнен. Ответ дан без фрагментов базы знаний. Выберите другую стратегию сжатия или начните новый чат." Label shows "без RAG (сбой поиска)". Context usage only 499/16384 (3%).
errors: context_full warning (raised at agent/rag_turn.py:121 when build_rag_block keeps zero chunks)
timeline: found during Phase 14 UAT (test 3), never verified working for K=15
reproduction: chat with ctx 16384, max_tokens 4096; RAG on, ready KB, K=15, ask "Какой штраф за превышение скорости на 40-60 км/ч?"
context: full UAT at .planning/phases/14-first-rag-query-day-22/14-UAT.md (Gaps section). Also noted: UI label 'сбой поиска' misleading for budget condition; message advising to change compression strategy irrelevant.
<!-- DATA_END -->

## Eliminated

## Evidence

## Resolution

root_cause: Not K-dependent. User 2's MCP toolset (GitLab 20 + filesystem 17 + 6 builtin tools) adds 10321 schema tokens to `used` in rag_turn; chat 55 has ctx 16384/max 4096, so budget = 16384-486-10321-4096 = 1481 tokens. Rank-1 chunk costs 1274 (with safety), 2nd would exceed. A slightly longer history drops it to zero kept -> context_full. Log shows context_full also at K=5 in chat 55; chat 56 (global ctx 32768) fine. UI '3%' ignores schema tokens.
fix: Accurate MSG_CONTEXT_FULL text (agent/rag.py) and UI label for context_full (ui/static/app.js). Budget logic is correct and unchanged.
verification: tests test_rag_turn/test_rag_static/test_rag_ws 43 passed; reproduced budget numbers offline.
files_changed: [agent/rag.py, ui/static/app.js]
