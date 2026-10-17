---
phase: 25-support-assistant-day-33
plan: 03
subsystem: docs
tags: [faq, docs, support]
requires: []
provides:
  - "docs/FAQ.md Russian support FAQ, nine symptom-first sections"
  - "tests/test_docs_faq.py guard pinning FAQ facts to code"
affects: [25-05, 25-09]
tech-stack:
  added: []
  patterns: ["docs test reads defaults via Settings.model_fields so .env does not affect it"]
key-files:
  created:
    - docs/FAQ.md
    - tests/test_docs_faq.py
  modified: []
key-decisions:
  - "One ## section per problem, heading carries the literal error text, each section under 1500 chars"
requirements-completed: [SUP-07]
duration: 10min
completed: 2026-10-17
---

# Phase 25 Plan 03: Support FAQ Summary

Russian `docs/FAQ.md` with nine code-verified problem sections (login 401/429, Not authenticated, Origin 403, API key 401, LM Studio 503, MCP, rate limit, chat busy 409), guarded by a docs test that reads values from code.

## Tasks
1. FAQ written after checking facts in shared/auth.py, agent/dependencies.py, agent/limits.py, shared/origins.py, shared/config.py, agent/http_chat.py: commit 204b60b
2. tests/test_docs_faq.py (4 tests): commit c973819

## Verification (observed)
- `pytest tests/test_docs_faq.py tests/test_docs_help.py tests/test_readme.py -q`: 66 passed.

## Deviations from Plan
None - plan executed as written. Not verified: the MCP "needs the `mcp` package" and "key revoked in UI" statements come from the plan text and were not re-checked against code.

## Self-Check: PASSED
docs/FAQ.md, tests/test_docs_faq.py exist; commits 204b60b and c973819 exist.
