---
phase: 13-knowledge-base-indexing-day-21
plan: 03
subsystem: knowledge-base
tags: [pymupdf, pdf-cleaning, loaders, golden-tests]
requires: []
provides:
  - agent/kb_loaders.py (LoadedDocument, KbLoadError, load_document, extract_pdf_pages, clean_pdf_pages, is_scan, read_text_file, normalize_plain_text, MSG_* constants)
affects: [13-05 indexer]
tech-stack:
  added: []
  patterns: [pure sync loaders for asyncio.to_thread, golden page-text fixtures]
key-files:
  created:
    - agent/kb_loaders.py
    - tests/test_kb_loaders.py
    - tests/fixtures/kb/koap_excerpt_pages.txt
    - tests/fixtures/kb/fz196_excerpt_pages.txt
  modified: []
decisions:
  - "Repeal stubs are kept by a case-insensitive 'утратил' guard on every annotation removal"
metrics:
  tasks: 2
  files: 4
  completed: 2026-10-03
---

# Phase 13 Plan 03: KB loaders and PDF cleaning Summary

PyMuPDF-based document loader with header/footer frequency filtering, annotation stripping, line joining and safe dehyphenation, proven by golden tests on real КоАП and ФЗ-196 page text; scan, broken-PDF and encoding failures raise Russian `KbLoadError` messages.

## Fixtures (0-based PyMuPDF page indexes)
- КоАП (Техэксперт export): pages 79-86 (Раздел II, Глава 5, Статья 5.1 onward, with `См. предыдущую редакцию` annotations, footers), 101-102 (page 102 has a `Утратила силу` stub), 358 (suffix-numbered articles). 11 pages, 77 KB.
- ФЗ-196: pages 9, 10, 11, 12, 16 (`(в ред. ...)` lines, a `КонсультантПлюс: примечание.` block, Статья 7-15 headings, word-per-line text). 5 pages, 28 KB.
- No PDFs committed.

## Verification
`pytest tests/test_kb_loaders.py -q` -> 18 passed (observed). Includes both golden tests, generated scan PDF, generated 3-page text PDF, broken PDF, BOM/cp1251/invalid/empty encodings.

## Deviations from Plan

**1. [Rule 1 - Bug] Annotation regex tuning (A6)**
- Real КоАП text has lowercase variants `(абзац в редакции, ... - см. предыдущую редакцию)`. Regex made case-insensitive; the removal guard checks `утратил` case-insensitively so stubs like `(статья утратила силу ... См. предыдущую редакцию)` survive (D-14). `N \d+-ФЗ` widened to `N \d+-\s*ФЗ` for annotations wrapped as `232-\nФЗ)`. Added whitespace-before-punctuation tidy after removals.
- Marker for new paragraphs refined to `\d+(?:\.\d+)*\.(?=\s|$)` so date fragments like `05.04.2016` at line start do not split paragraphs; a lone `Статья`/`Глава`/`Раздел` line followed by `9.` is joined (ФЗ-196 prints headings word per line). `КонсультантПлюс: примечание` added as a paragraph marker.
- Commit: 5952ca7

**2. [Rule 1 - Test fixes]** Three of my tests were mis-specified and corrected: footer test needed realistic page length (short pages are entirely "edge" lines); scan threshold example needed >50% sparse pages (spec is strict >); noise check excludes repeal stubs which legitimately end with the edition note.

**3. Fixture finding:** КоАП suffix articles appear as `Статья 14.1_1-1.` (underscore), so the plan's `\d+(?:\.\d+)*(?:-\d+)?` article regex does not match them; golden test compares raw vs cleaned sets with that regex as specified. Plan 04's structural chunker may need `[\d._]` handling.

## Known behaviour notes
- When a КоАП article heading wraps and body text has no marker, the heading line and body merge into one paragraph (`Статья 5.1. <title> <body>`); chunker should take heading by regex rather than assume a separate paragraph.
- extract_pdf_pages takes an optional `filename` argument (defaults to path.name) so errors name the original file.

## Known Stubs
None.

## Threat surface
No new surface; parse errors map to `KbLoadError`, only exception type names logged, annotation regex bounded.

## Commits
- 78a446f test(13-03): fixtures
- 4b6bf58 test(13-03): failing tests (RED)
- 5952ca7 feat(13-03): loaders (GREEN)

## Self-Check: PASSED
