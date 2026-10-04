---
phase: 16-citations-and-anti-hallucination-day-24
plan: 02
subsystem: rag
tags: [rag, settings, migration, sqlite, fastapi]
requires: []
provides:
  - "ChatRagConfig.strict column (default on) with idempotent migration"
  - "strict field in GET/PUT /api/v1/chats/{id}/rag"
affects: [16-03, 16-06]
key-files:
  modified:
    - shared/models.py
    - shared/database.py
    - agent/rag_api.py
    - tests/test_database.py
    - tests/test_rag_api.py
key-decisions:
  - "strict reuses the existing PRAGMA-checked ALTER loop with BOOLEAN DEFAULT 1, so pre-existing rows become strict-on (D-15)"
requirements-completed: [CITE-03]
duration: 10min
completed: 2026-10-04
---

# Phase 16 Plan 02: Strict-mode flag Summary

Per-chat `strict` boolean (quotes plus the "не знаю" gate switch, on by default) added to the ChatRagConfig model, the idempotent migration, and the RAG settings REST API with partial-update semantics.

## Tasks

1. Task 1 (commit f3a0936): `strict: bool = Field(default=True)` on ChatRagConfig, `("strict", "BOOLEAN DEFAULT 1")` in `_CHATRAGCONFIG_RANK_COLUMNS`, tests for migration (once, existing row reads 1) and model default.
2. Task 2 (commit 59135f4): `strict` in RagConfigIn (`StrictBool | None`), RagConfigOut, `_config_out` (True for no row / NULL), `_apply_search_settings` flag loop, log field; tests for default, roundtrip/partial update, 422 for `"yes"` and `1`.

## Verification (actually run)

- `python -m pytest tests/test_database.py tests/test_rag_api.py -q`: 47 passed.
- The existing `test_config_foreign_chat_404` still passes (ownership guard untouched).

## Deviations from Plan

- Worktree base was off; reset to 860d7e4 per the base-check instructions.
- Working-tree guard: files were clean, no unrelated diffs.
- Otherwise: none.

## Known Stubs

None.

## Self-Check: PASSED
