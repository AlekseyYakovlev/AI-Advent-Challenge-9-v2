---
phase: 21-project-docs-in-rag-day-31
verified: 2026-10-13T00:00:00Z
status: human_needed
score: 5/5 must-haves verified (automated); 3 behaviours need human run
has_blocking_gaps: false
human_verification:
  - test: "Add flow on a ready base (LM Studio running): add a new file and a folder, confirm only added files are indexed and old fragments stay searchable"
    expected: "Card shows progress then summary; old fragments still found in test search during and after the add"
    why_human: "Part B of scripts/e2e_kb_folder_playwright.py exited 3 (LM Studio unreachable); needs a real embedding model"
  - test: "Re-add a file with an existing source name (e.g. docs/API_SPEC.md) after editing it"
    expected: "Summary says replaced; no duplicate fragments; search shows new content"
    why_human: "Same blocked browser part B; pytest covers it with mocked embeddings only"
  - test: "Interpretation of KBUP-03 'knowledge base file list'"
    expected: "User confirms the pending list (before upload) satisfies it, since there is no per-base document list in the UI"
    why_human: "Requirement/roadmap wording ambiguity"
---

# Phase 21: Project docs in RAG (Day 31) Verification Report

**Phase Goal:** The user can load README and `docs/` into one knowledge base in a few clicks and keep it current by adding changed files
**Status:** human_needed
**Re-verification:** No

## Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | README.md at root describes purpose, structure, run, `.env`, docs | VERIFIED | README.md read (purpose, directory structure, requirements, install); `tests/test_readme.py` passes |
| 2 | Pick README then `docs/` folder, one pending list, skipped count, one base | VERIFIED (UI structure) | `webkitdirectory` inputs in `ui/static/index.html:552`; accumulating pending state in app.js; part A browser script 13/13 (reported); `tests/test_kb_folder_ui.py` passes |
| 3 | Relative-path sources; same base names in different folders stay separate | VERIFIED (caveat) | sha unique constraint dropped with chunk-preserving migration (`tests/test_kb_migration.py::test_migration_allows_same_sha_and_keeps_cascade` passes); server-side source names in `agent/kb_api.py`. Caveat: "file list" = pending list only, no per-base doc list (flagged) |
| 4 | Add to ready base indexes only added files, old fragments searchable | VERIFIED by pytest, UNCERTAIN live (`unverifiable_runtime`) | `POST /api/v1/kb/{id}/files`, `agent/kb_indexer.py` add job; `tests/test_kb_add_api.py`, `test_kb_add_job.py` pass; live LM Studio run blocked |
| 5 | Same source name replaces fragments; limit overflow rejected clearly | VERIFIED by pytest, UNCERTAIN live | `MAX_FILES = 200` in `agent/kb_limits.py`, `GET /kb/limits`, `MSG_TOO_MANY_FILES`; tests pass |

**Score:** 5/5 verified at code/test level; live add/replace unconfirmed.

## Automated checks run
Targeted run: `test_readme, test_kb_migration, test_kb_add_api, test_kb_add_job, test_kb_folder_ui, test_kb_add_ui, test_docs_kb_upload` -> 150 passed. Full suite reported 2759 passed (not re-run).

## Requirements Coverage

| Requirement | Status | Evidence |
|---|---|---|
| DOC-01 | SATISFIED | README.md + test_readme |
| KBUP-01 | SATISFIED | folder input, recursive take, skipped count (part A) |
| KBUP-02 | SATISFIED | accumulating pending list with removal (part A) |
| KBUP-03 | SATISFIED with interpretation flag | relative-path source names, same-name separation tested; "file list" = pending list |
| KBUP-04 | SATISFIED | MAX_FILES=200, /kb/limits, rejection messages, tests |
| KBUP-05 | SATISFIED (pytest); live run needs human | add job + tests |
| KBUP-06 | SATISFIED (pytest); live run needs human | replace tests |

No orphaned requirements: all 7 IDs mapped to Phase 21 in REQUIREMENTS.md appear in plans.

## Anti-Patterns / Code Review (21-REVIEW.md: 0 critical, 4 warnings, 3 info; advisory)
- WR-01: failure after DB commit (promote_index) unlinks files committed documents reference.
- WR-02: `_move_staged` thread can race with cancellation cleanup.
- WR-03: no per-base storage cap; concurrent adds each stage up to 100 MB before 409.
- WR-04: synchronous `rmtree` on the event loop in kb_api error branches.
- IN-01 duplicated chunk-row construction; IN-02 client/server name normalisation differ; IN-03 Starlette max_files 1000 vs 200 cap.
No TBD/FIXME debt markers were scanned for beyond review; none reported.

## Gaps Summary
No blocking gaps. Human verification needed for live add/replace with LM Studio and for the KBUP-03 interpretation. WR-01 is the most consequential advisory (data inconsistency on a rare post-commit failure).
