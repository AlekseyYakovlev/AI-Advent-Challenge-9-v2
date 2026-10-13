---
phase: 22-project-mcp-servers-day-31
plan: 04
subsystem: docs
tags: [docs, mcp, git, github, guard-test]
requires: ["22-02", "22-03"]
provides:
  - "docs/USER_GUIDE.md section «MCP серверы» with exact form values for local-git and github"
  - "Bundled MCP servers subsection in ARCHITECTURE, example bodies in API_SPEC, test sections in TESTING_GUIDE"
  - "tests/test_docs_mcp_servers.py guard (documentation vs server vs secrets)"
affects: [23]
tech-stack:
  added: []
  patterns:
    - "guard test reads tool names from server.list_tools() and limits from module constants and Settings"
key-files:
  created:
    - tests/test_docs_mcp_servers.py
  modified:
    - docs/USER_GUIDE.md
    - docs/ARCHITECTURE.md
    - docs/API_SPEC.md
    - docs/TESTING_GUIDE.md
key-decisions:
  - "API examples sit in a new `## Project MCP servers (Day 31)` section before `## Environment Settings` because API_SPEC has no MCP route section to extend"
requirements-completed: [PGIT-03, GHUB-01]
duration: 20min
completed: 2026-10-13
---

# Phase 22 Plan 04: MCP servers documentation Summary

User guide section with copy-ready form values for `local-git` and `github` (v2.0.2, SHA-256, fine-grained token, `--read-only` caveat, masking, troubleshooting), plus architecture, API and testing documents, all held in step with the code by a 16-test guard.

## What was built

- Task 1 (a18a65c): `## MCP серверы` before `## Tips` in `docs/USER_GUIDE.md` (form, local-git, github, connection problems) and three English Troubleshooting entries.
- Task 2 (ba40d5b): `### Bundled MCP servers` in `docs/ARCHITECTURE.md`; `## Project MCP servers (Day 31)` in `docs/API_SPEC.md`; `## Project MCP servers (Day 31)` in `docs/TESTING_GUIDE.md`; `tests/test_docs_mcp_servers.py`.

## Verification (observed)

- `pytest tests/test_docs_mcp_servers.py tests/test_docs_day30.py tests/test_readme.py -q` (scratch `DB_PATH`, deleted afterwards): 116 passed.
- README.md untouched. No token-shaped string in scanned files (guard passes).
- Not verified: the GitHub install steps and checksum were taken from the plan's research data, not re-downloaded here.

## Deviations from Plan

**1. [Rule 3 - Placement] API example section.** `docs/API_SPEC.md` has no MCP route section, so the examples went into a new section before `## Environment Settings`.

**2. [Ordering] Guard test committed with Task 2.** It also checks Task 2 documents, so the whole file was committed in the second commit.

**3. Note.** `docs/TESTING_GUIDE.md` describes `scripts/e2e_mcp_git_playwright.py`, which a later plan creates; the guard checks only that the name is in the guide.

## Known Stubs

None.

## Self-Check: PASSED

All five files exist; commits a18a65c and ba40d5b exist.
