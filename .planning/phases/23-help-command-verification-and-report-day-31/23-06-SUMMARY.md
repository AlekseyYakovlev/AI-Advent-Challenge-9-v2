---
phase: 23-help-command-verification-and-report-day-31
plan: 06
subsystem: docs
tags: [docs, help-command, testing-guide, source-guard]
requires:
  - phase: 23-04
    provides: /help over WebSocket and HTTP as built
  - phase: 23-05
    provides: default threshold measurement and control-run script
provides:
  - "/help sections in API_SPEC.md, ARCHITECTURE.md, USER_GUIDE.md (Russian) and TESTING_GUIDE.md"
  - "tests/test_docs_help.py documentation guard"
key-files:
  created: [tests/test_docs_help.py]
  modified: [docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/USER_GUIDE.md, docs/TESTING_GUIDE.md]
requirements-completed: [VER-09, VER-07]
completed: 2026-10-13
---

# Phase 23 Plan 06: /help documentation and guard test

The three product documents and the testing guide now describe `/help` as built, written from the code (`agent/help_command.py`, `agent/help_api.py`, `agent/ws.py::_run_help_turn`) and the 23-01..23-05 summaries. A source test keeps the sections and the Phase 21 and 22 descriptions in place.

## Commits
- 20f43dc: API_SPEC, ARCHITECTURE, USER_GUIDE sections
- f226780: user guide wording so the title rule contains the required phrase
- c5faca8: TESTING_GUIDE section and `tests/test_docs_help.py`

## What the documents say
- API_SPEC `## /help command (Day 31)`: config routes with fields, ranges and status codes, recognition rule, `help_notice` frame, `rag.help`, HTTP success and not-stored bodies, `kb_id` ignored for `/help` (also cross-referenced in the HTTP chat section), curl example with `<API_KEY>`. Default threshold documented as 0.45 (equal to `DEFAULT_HELP_THRESHOLD`) with the reason it differs from the calibrated 0.67 and a pointer to `eval_out/day31/CALIBRATION.md`.
- ARCHITECTURE: recognition, notices before the rate and queue slots, `HelpConfig` and its `SET NULL` key, the turn flow, whole-server tool selection with the `local-git` default and an explicit `allowed_tools` set, failure handling, skipped side jobs, storage in `Message.rag_sources`, tool cards live-only.
- USER_GUIDE `## Команда /help` (Russian): setup, controls, warning that built-in groups and write-capable servers can change data, label, badge, notices, the default-title sentence ("New Chat", phrase «название чата не создаётся автоматически»), tool cards not restored after reload, HTTP use.
- TESTING_GUIDE: coverage map for VER-07, one subsection per new test file and the two scripts.

## Verification (actually run)
- Task 1 verify, assertions part: the first run of the inline `python -c` failed only on the title phrase (the guide had a capital letter, «Название чата...»); fixed in f226780 and the same assertions then confirmed via a script (all present). The inline command itself was not re-run after the fix because shell quoting mangled the Cyrillic literals.
- `pytest tests/test_docs_help.py tests/test_docs_day30.py tests/test_docs_mcp_servers.py tests/test_docs_kb_upload.py -q`: 174 passed.
- Full `pytest tests -q`: 3143 passed, 13 skipped, 0 failed.

## Deviations from Plan
- [Rule 1] The first user-guide wording did not contain the required lowercase phrase; reworded in f226780.
- `scripts/e2e_help_playwright.py` is listed in TESTING_GUIDE and checked by the guard as a string, but the file is created by Plan 23-07 (same wave), so the guard does not require it to exist.
- The real GitHub server and token text from earlier phases was left untouched.

## Corpus note for the control run
The 23-05 threshold was measured on corpus commit 822acac (docs before these edits). These edits add the `/help` sections and change the indexed corpus, so the control run in Plan 23-08 indexes the final documents and the report must name both states.

## Known Stubs
None.

## Self-Check: PASSED
Files present: the four documents and `tests/test_docs_help.py`; commits 20f43dc, f226780, c5faca8 exist.
