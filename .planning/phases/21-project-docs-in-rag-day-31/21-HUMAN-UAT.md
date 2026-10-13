---
status: partial
phase: 21-project-docs-in-rag-day-31
source: [21-VERIFICATION.md]
started: 2026-10-13T07:39:07Z
updated: 2026-10-13T07:39:07Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Live add of a file/folder to a ready knowledge base
expected: only the added files are indexed; earlier fragments stay searchable (run python scripts/e2e_kb_folder_playwright.py with LM Studio + nomic embedding model, expect exit 0)
result: [pending]

### 2. Live replace of an existing source name
expected: card shows "Добавлено: 1, заменено: 1, без изменений: 4"; old fragments gone, new marker searchable
result: [pending]

### 3. KBUP-03 interpretation
expected: user confirms the pending list (create form + add modal) counts as the "knowledge base file list"; no per-base document list is added
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
