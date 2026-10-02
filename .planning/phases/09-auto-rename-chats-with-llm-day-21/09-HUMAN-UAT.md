---
status: partial
phase: 09-auto-rename-chats-with-llm-day-21
source: [09-VERIFICATION.md]
started: 2026-10-02T00:45:00Z
updated: 2026-10-02T10:20:00Z
---

## Current Test

[awaiting human testing — item 1 (DeepSeek, API-level)]

## Tests

### 1. DeepSeek backend: title request returns usable content
expected: HTTP 200, `finish_reason` `stop`, non-empty `message.content` that is a short title (not the user's message), i.e. the request shape the Agent sends is accepted by DeepSeek.
result: [pending]
note: (a) Not run by Claude: it needs the user's DeepSeek API key and makes a paid cloud call; the executor does not read `.env`. (b) The app has a single LLM endpoint (`LM_STUDIO_BASE_URL`) and no DeepSeek model picker until Phase 12, so this check is API-level, not browser-level. (c) Procedure: one POST to `https://api.deepseek.com/v1/chat/completions` with header `Authorization: Bearer <key>` (key read from the environment variable `DEEPSEEK_API_KEY`, never pasted into a file), JSON body `model` `deepseek-chat`, `messages` = `agent.titles.build_title_messages("Как настроить WebSocket в FastAPI?", "Используйте декоратор app.websocket")`, `temperature` 0, `max_tokens` 30, `stream` false, `reasoning_effort` `none`. Runnable from the repo root (PowerShell or bash, with `DEEPSEEK_API_KEY` set in the shell): `python -c "import os,json,httpx; from agent.titles import build_title_messages as b; body={'model':'deepseek-chat','messages':b('Как настроить WebSocket в FastAPI?','Используйте декоратор app.websocket'),'temperature':0,'max_tokens':30,'stream':False,'reasoning_effort':'none'}; r=httpx.post('https://api.deepseek.com/v1/chat/completions',headers={'Authorization':'Bearer '+os.environ['DEEPSEEK_API_KEY']},json=body,timeout=30); print(r.status_code); print(json.dumps(r.json(),ensure_ascii=False)[:600])"`. (d) Reading the outcome: HTTP 200 with non-empty content = pass. HTTP 400 or 422 = the Agent repeats the call once without `reasoning_effort` (covered by `tests/test_titles.py`), so repeat the command without that field (remove `'reasoning_effort':'none'`) and record both results. Empty content with `finish_reason` `length` = the backend ignores the field and chats on that model get the fallback title (logged as `chat_title_llm_unusable`); report that as an issue.

### 2. Force the title call to fail, send a first message
expected: Title becomes the truncated first user message (fallback), sidebar updates
result: passed
note: Run by Claude (Playwright, headless Chromium) on the isolated copy at UI :18000 / Agent :18001 with a local proxy on :18234 in front of LM Studio that forwards streaming completions and returns HTTP 500 for non-streaming ones (the title call is `stream: false`). Sidebar item and `#chat-title` changed live, without reload, from 'New Chat' to 'Расскажи коротко, что такое асинхронное…' (40 chars), identical to `fallback_title(message)`. One `chat_title_updated` frame seen on `/ws/events`. Title persisted after reload and was unchanged after a second turn. Agent log: `chat_title_llm_failed` (HTTPStatusError 500) then `chat_title_set source=fallback length=40`. No dialogs. Screenshots: `%TEMP%/aiadvent-uat-09/shots/fb_live.png`, `fb_turn2.png`. Not covered: the case where the turn's own model call also fails.

### 3. After the reasoning-model fix, re-run the browser UAT on the local model
expected: Title is a 3-8 word LLM-written title in the user's language, agent.log shows chat_title_set source=llm
result: passed
note: Run by Claude with Playwright (headless Chromium) on the isolated copy at UI :18000 / Agent :18001 (ports 8000/8001 untouched), model `qwen/qwen3.5-9b` (already loaded in LM Studio, not touched), working tree containing the plan 09-05 fix. Scenario A: chat_id=1, title shown in sidebar and header "Настройка WebSocket в FastAPI" (29 chars), `source=llm` (agent.log `chat_title_set`, length 29), not equal to `fallback_title(first message)` = "Как настроить WebSocket в FastAPI?". Scenario D: chat_id=2, sidebar button "Классический рецепт домашнего борща с говядиной" (47 chars), `source=llm` (length 47), not equal to fallback "Посоветуй рецепт борща"; while it arrived the header still showed the title from A. Scenario E (`<img src=x onerror=alert(1)> что такое HTML?`): chat_id=3, title "HTML это язык разметки для веб страниц" (38 chars), `source=llm` (length 38), differs from fallback "что такое HTML?", no `<`/`>`, 0 dialogs, 0 `img` in `#chat-list` / `#chat-title`. `chat_title_llm_unusable`: 0, `chat_title_llm_failed`: 0, `chat_title_reasoning_control_rejected`: 0. Artifacts (outside the repo, session scratchpad): `.../scratchpad/uat-09-gap/shots/A.png`, `D.png`, `E.png`, `.../scratchpad/uat-09-gap/agent.log.copy`, `uat.py`, `results.json`. DeepSeek not exercised.

## Summary

total: 3
passed: 2
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps
