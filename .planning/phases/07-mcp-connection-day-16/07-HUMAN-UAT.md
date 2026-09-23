---
status: partial
phase: 07-mcp-connection-day-16
source: [07-VERIFICATION.md]
started: 2026-09-23T21:31:09Z
updated: 2026-09-23T21:31:09Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Real Ctrl+C shutdown leaves no filesystem.exe
expected: With filesystem.exe connected via Settings, stop `python run.py` with Ctrl+C in an interactive console; no new filesystem.exe remains (PID 26256 is a pre-existing unrelated instance). Note: graceful Ctrl+Break and Agent terminate() were already verified by Claude with no orphans.
result: [pending]

### 2. Visual review of Settings > "MCP серверы"
expected: Layout, status badges, collapsible tool list, and error block look right and match the rest of the modal (07-UI-SPEC.md). Claude reviewed screenshots only.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
