---
phase: 24-automated-pr-code-review-day-32
plan: 02
subsystem: review
tags: [sqlmodel, ast, chunking, code-index]
requires: [24-01]
provides:
  - ReviewConfig and ReviewCodeIndex tables
  - agent/code_chunking.py (chunk_code, chunk_python, chunk_lines, parse_line_range)
key-files:
  created: [agent/code_chunking.py, tests/test_code_chunking.py]
  modified: [shared/models.py, tests/test_database.py]
requirements-completed: [REV-04, REV-06]
completed: 2026-10-13
---

# Phase 24 Plan 02: Review tables and code chunker Summary

Two per-user review tables plus a pure chunker that splits Python by `ast` (functions, classes, methods) and other files into 50-line windows, every fragment capped at 2000 characters with file path, name and line range.

## Tasks

1. ReviewConfig / ReviewCodeIndex tables - commit 68ee264. Appended after `HelpConfig`; FKs use `sa_column`; unique constraint `uq_reviewcodeindex_user_repo_ref`. Tests: defaults, SET NULL on docs KB delete, CASCADE on index KB delete, uniqueness `IntegrityError`, cascade on user delete, table list updated.
2. Code chunker - commit ff11506. Slices by lines only (no `col_offset`); falls back to line windows on `SyntaxError`/`ValueError`/`RecursionError`/NUL.

## Verification (run)

- `pytest tests/test_code_chunking.py tests/test_kb_chunking.py tests/test_database.py tests/test_help_api.py -q`: 81 passed.
- `pytest tests/test_database.py tests/test_help_api.py tests/test_kb_models.py -q`: 46 passed.
- `chunk_code('x.py', 'def f(:\n')` returns 1 fragment, no raise; `col_offset` count outside comments: 0.

## Deviations from Plan

None functionally. Note: a `<module>` gap fragment includes trailing blank lines up to the next definition (e.g. `<module> (L1-5)`), the test asserts that behaviour. Whitespace-only parts of a split span are dropped, so a run of blank lines alone could leave a line uncovered (cosmetic).

## Known Stubs

None.

## Self-Check: PASSED
