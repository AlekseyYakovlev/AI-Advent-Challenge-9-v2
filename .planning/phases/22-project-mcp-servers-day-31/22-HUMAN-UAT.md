---
status: complete
phase: 22-project-mcp-servers-day-31
source: [22-VERIFICATION.md]
started: 2026-10-13T11:01:45Z
updated: 2026-10-13T12:46:00Z
---

## Current Test

[testing complete]

## Tests

### 1. PGIT-04 browser run on a real local model
expected: With LM Studio running and a chat model loaded, `python scripts/e2e_mcp_git_playwright.py` exits 0: the chat answers which branch is checked out and the UI shows the local-git tool call card.
result: pass
evidence: Run by Claude on an isolated copy (ports 18000/18001), model qwen/qwen3.5-9b. `RESULT: PASS (attempt 1 of 2)`: answers named `alpha`, then `beta` after the switch, each with a `local-git · git_current_branch` card. The first run failed on a timing defect in the check script (model picker read before it was filled); the script was fixed and re-run. Output in 22-CHECKS.md, "PGIT-04 browser run", Runs 2 and 3.

### 2. Token shown masked in the Settings MCP form (optional)
expected: After saving the github server, GITHUB_PERSONAL_ACCESS_TOKEN is displayed masked, never as plain text.
result: pass
evidence: Run by Claude with Playwright on an isolated copy with the real binary and token. Server connected with 16 tools; the edit form showed `GITHUB_PERSONAL_ACCESS_TOKEN=•••`; the token value was absent from the DOM, the frontend state, all 26 API response bodies and the app log; re-saving the masked form kept the stored token. Output in 22-CHECKS.md, "GHUB-01 token mask in the Settings form (browser)".

## Summary

total: 2
passed: 2
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none]
