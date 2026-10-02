---
status: partial
phase: 09-auto-rename-chats-with-llm-day-21
source: [09-VERIFICATION.md]
started: 2026-10-02T00:45:00Z
updated: 2026-10-02T00:45:00Z
---

## Current Test

[awaiting human testing — item 1]

## Tests

### 1. DeepSeek backend: send a first message in a new chat with a DeepSeek model
expected: Sidebar item and header change from 'New Chat' to a 3-8 word title in the user's language
result: [pending]
note: Not run by Claude — needs the DeepSeek API key from `.env` and makes paid cloud calls; the isolated-copy UAT recipe excludes `.env`. Browser UAT so far covered LM Studio (`qwen/qwen3.5-9b`) only.

### 2. Force the title call to fail, send a first message
expected: Title becomes the truncated first user message (fallback), sidebar updates
result: passed
note: Run by Claude (Playwright, headless Chromium) on the isolated copy at UI :18000 / Agent :18001 with a local proxy on :18234 in front of LM Studio that forwards streaming completions and returns HTTP 500 for non-streaming ones (the title call is `stream: false`). Sidebar item and `#chat-title` changed live, without reload, from 'New Chat' to 'Расскажи коротко, что такое асинхронное…' (40 chars), identical to `fallback_title(message)`. One `chat_title_updated` frame seen on `/ws/events`. Title persisted after reload and was unchanged after a second turn. Agent log: `chat_title_llm_failed` (HTTPStatusError 500) then `chat_title_set source=fallback length=40`. No dialogs. Screenshots: `%TEMP%/aiadvent-uat-09/shots/fb_live.png`, `fb_turn2.png`. Not covered: the case where the turn's own model call also fails.

## Summary

total: 2
passed: 1
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps
