---
phase: 21-project-docs-in-rag-day-31
plan: 02
subsystem: documentation
tags: [readme, docs, guard-test]
requires: []
provides:
  - "README.md at the repository root (English)"
  - "tests/test_readme.py guard for README claims"
affects: [21-07]
key-files:
  created: [README.md, tests/test_readme.py]
  modified: []
decisions:
  - "README sections are self-contained so a heading-based chunker can retrieve them independently"
metrics:
  tasks: 2
  completed: 2026-10-13
---

# Phase 21 Plan 02: Repository README Summary

An English README.md covering purpose, features, directories, install, run, main .env settings and links to the four docs, with a pytest guard that checks its directories, links, setting names, ports and commands against the repository.

## Commits
- d417e80: README.md
- 738efbd: tests/test_readme.py

## Verification (observed)
- Task 1 check: 98 lines, no Cyrillic characters.
- `pytest tests/test_readme.py tests/test_docs_day30.py -q`: 57 passed.

## Deviations from Plan
None - plan executed exactly as written. Folder upload for knowledge bases is intentionally not described (Plan 21-07 adds it).

## Known Stubs
None.

## Self-Check: PASSED
