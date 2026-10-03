---
status: complete
phase: 15-reranking-and-filtering-day-23
source: 15-01-SUMMARY.md .. 15-13-SUMMARY.md
started: 2026-10-03T21:30:00Z
updated: 2026-10-03T22:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Stop the app, run `python run.py`. Both servers boot with no errors (FTS5 index migration/backfill included) and the chat list loads in the browser.
result: pass

### 2. Search settings popover
expected: In a chat with a knowledge base, open the search settings popover. It shows candidate-K, threshold (calibrated default vs override) and four independent switches (lexical rerank, LLM rerank, hybrid FTS5, query rewrite). Changing a value persists after reopening the popover / reloading the chat.
result: pass

### 3. Calibrated threshold default and reset
expected: With the bge-m3 embedder the threshold shows the calibrated value 0.67. Overriding it shows the override; resetting returns to the calibrated default.
result: pass

### 4. Out-of-corpus question gets cut
expected: Ask a question the KB does not cover. Low-scoring chunks are cut before the LLM, the answer notes no relevant fragments were found, and a threshold line explains the cut.
result: pass

### 5. "Детали поиска" block
expected: Under a RAG answer there is a collapsed "Детали поиска" block. Expanding it shows the query (and rewritten query if rewrite is on), stages and any skipped stages, a candidates table (cos, lex, fts_rank, llm, status chips) and the final chunks.
result: pass

### 6. Independent stage toggles
expected: Turn on each stage alone (lexical, LLM rerank, hybrid, rewrite): the answer still arrives, and the details block reflects only the enabled stage. A failing optional stage shows a skip reason instead of breaking the answer.
result: pass

### 7. Day23_report.md
expected: Day23_report.md reads clearly: calibration, 7-config comparison, threshold finding (0.67 lowers hit@5 on the control set), rerankers, hybrid, rewrite, judge column, limitations.
result: pass

## Summary

total: 7
passed: 7
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]
