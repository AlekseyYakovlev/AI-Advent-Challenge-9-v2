# Phase 7: MCP Connection (Day 16) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-23
**Phase:** 07-MCP Connection (Day 16)
**Areas discussed:** UI placement & tool view, Connection lifecycle, Config input shape, Error & timeout reporting

---

## UI placement & tool view

| Question | Options | Selected |
|----------|---------|----------|
| Where MCP management lives | Section in Settings modal / Tab inside Settings modal / New sidebar panel | Section in Settings modal |
| Tool list rendering | Collapsible per-tool rows / Everything expanded / Compact table | Collapsible per-tool rows |
| Raw inputSchema JSON | Yes, behind a toggle / No, parsed params only | Yes, behind a toggle |
| Servers per user | Many, per-server Connect / Exactly one | Many, per-server Connect |

## Connection lifecycle

| Question | Options | Selected |
|----------|---------|----------|
| What Connect does | Persistent session until Disconnect / One-shot probe | Persistent session |
| Status after restart | Reset to not connected / Auto-reconnect / Show cached last tool list | Reset to not connected |
| Detecting a dead server | Checked on next status fetch / Periodic polling / You decide | Checked on next status fetch |
| Editing a connected server | Auto-disconnect on save / Block edits while connected | Auto-disconnect on save |

## Config input shape

| Question | Options | Selected |
|----------|---------|----------|
| Args input | One per line / Single shell-style line | One per line |
| Extra fields (multi) | Env vars / Working directory / Enabled toggle | All three |
| Validate path on save | No, only on Connect / Warn on save | No, only on Connect |
| Env secret handling | Plaintext, mask in UI / Plaintext shown as-is | Plaintext, mask in UI |
| enabled=off behavior | Connect disabled / Stored flag only | Connect disabled |

## Error & timeout reporting

| Question | Options | Selected |
|----------|---------|----------|
| Handshake timeout | 10s from config / 30s / Per-server field | 10s from config |
| Show stderr | Last ~20 lines under error / Classified message only | Last ~20 lines |
| Error categorization | Fixed codes + detail / Free-form exception text | Fixed codes + detail |
| CLI output | Human-readable + exit codes / Add --json flag | Human-readable + exit codes |

**Notes:** The user took the recommended option on every single-choice question and chose all three optional fields on the multi-select.

## Claude's Discretion

- REST endpoint shapes, module/table names, SDK session-holding pattern, stderr capture mechanism, `mcp` version pin, concurrent-connect handling, UI markup/wording.

## Deferred Ideas

- LLM tool calls via MCP (MCP-F1), non-stdio transports (MCP-F3), auto-reconnect / cached tool list, env secret encryption.
