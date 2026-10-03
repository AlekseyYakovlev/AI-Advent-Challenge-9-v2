# Phase 13: Knowledge base indexing (Day 21) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-03
**Phase:** 13-knowledge-base-indexing-day-21
**Areas discussed:** Failed files & job lifecycle, Upload limits & duplicates, Embedding model picker & defaults, Chunking details, KB list & test-search UI, Spike scope & acceptance proof

---

## Failed files & job lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| All-or-nothing | Whole KB failed with named file | ✓ |
| Skip bad file, keep rest | Ready with per-file errors | |
| Pre-validate, then all-or-nothing | Parse first, reject before embedding | |

**User's choice:** All-or-nothing; delete mid-job cancels the job; retry = delete and re-create only.

---

## Upload limits & duplicates

| Option | Description | Selected |
|--------|-------------|----------|
| 50 MB/file, 10 files, 100 MB total | | ✓ |
| 25 MB/file, 5 files | | |
| You decide | | |

**User's choice:** 50/10/100 caps; duplicate SHA-256 rejected with message; errors inline in modal.

---

## Embedding model picker & defaults

| Option | Description | Selected |
|--------|-------------|----------|
| Embeddings + name match, show all | | ✓ |
| Only type==embeddings | | |
| All models, no filter | | |

**User's choice:** Embeddings + name match with "show all". Missing default model: free-text answer "try to load, then 1" — interpreted as: try an explicit load first, then fail the KB with a clear message (no fallback). giga prefix none; "Проверить эмбеддинг" button included.
**Notes:** Interpretation of the free-text answer should be re-confirmed if wrong.

---

## Chunking details

| Option | Description | Selected |
|--------|-------------|----------|
| 1000 / 150 | | ✓ |
| 800 / 100 | | |
| 1500 / 200 | | |

**User's choice:** 1000/150; cascade structural strategy (legal → MD headings → paragraphs); paragraph/sentence sub-split with breadcrumb prefix; strip annotations, keep "Утратила силу" stubs.

---

## KB list & test-search UI

**User's choice:** Row shows name, status chip, counts, model, delete. Test search in a separate modal (not the recommended inline expand). Top 5 cards with snippet.

---

## Spike scope & acceptance proof

**User's choice:** Spike covers all four: embedding round trip, КоАП golden file, VRAM co-loading, retrieval smoke A/B. KB-11 proven by Playwright E2E on 18000/18001 + pytest with mocks.

---

## Claude's Discretion

Progress throttling, batch size, styling, retry/backoff, module split, Windows FAISS path handling.

## Deferred Ideas

Re-index action, partial success state, editable prefix, incremental add/OCR/DOCX.
