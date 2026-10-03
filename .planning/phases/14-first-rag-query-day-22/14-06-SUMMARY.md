---
phase: 14-first-rag-query-day-22
plan: 06
subsystem: frontend
tags: [rag, ui, vanilla-js]
requires: [14-03, 14-04]
provides: [rag-header-controls, rag-answer-meta, rag-sources-block]
key-files:
  modified: [ui/static/index.html, ui/static/app.js]
  created: [tests/test_rag_static.py]
metrics:
  completed: 2026-10-03
---

# Phase 14 Plan 06: RAG UI Summary

Header RAG controls (badge, switch, KB select, K input) bound to GET/PUT /chats/{id}/rag, plus per-answer mode label, persistent warning line, collapsed lazy-loaded "Источники (N)" block and a done-frame warning toast, all rendered with textContent/mcpEl only.

## Tasks

1. Header controls (loadChatRag, saveChatRag, renderRagControls, listeners, kb_progress/kb_deleted refresh).
2. buildRagMeta, buildRagSourcesBlock, buildRagSourceRow, loadRagSnippets, renderMessages integration, done toast, tests/test_rag_static.py.

Commits: feat commit (tasks 1+2 code) and test commit (see git log).

## Verification (observed)

- `pytest tests/test_rag_static.py -q`: 25 passed.
- `pytest tests/ -q`: 1463 passed, 1 skipped.
- Browser behavior not run here (planned for 14-08 Playwright).

## Deviations from Plan

- [Process] Tasks 1 and 2 touch the same app.js regions (selectChat/renderMessages/done), so their code was committed as one feat commit rather than two; the test file is a separate commit.
- Worktree base was corrected with `git reset --hard 542cc1b` per the startup check.

## Known Stubs

None.

## Self-Check: PASSED
