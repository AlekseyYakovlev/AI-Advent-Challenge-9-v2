---
status: partial
phase: 12-llm-providers-section-in-settings-day-21
source: [12-VERIFICATION.md]
started: 2026-10-02T15:02:08Z
updated: 2026-10-02T15:02:08Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Live DeepSeek title check (PROV-07, backlog 999.11)
expected: With a real DEEPSEEK_API_KEY in .env, `RUN_LIVE_DEEPSEEK=1 pytest tests/test_live_deepseek_title.py -q -rs` passes: HTTP 200 from api.deepseek.com, non-empty short title via the provider routing.
result: [pending]

## Summary

total: 1
passed: 0
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps
