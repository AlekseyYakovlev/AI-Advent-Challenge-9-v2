---
phase: 09-auto-rename-chats-with-llm-day-21
plan: 06
subsystem: testing
tags: [uat, playwright, lm-studio, gap-closure]
requires: [09-05]
provides: [observed chat_title_set source=llm evidence on the local reasoning model]
affects: [09-HUMAN-UAT.md]
key-files:
  modified: [.planning/phases/09-auto-rename-chats-with-llm-day-21/09-HUMAN-UAT.md]
requirements-completed: []
metrics:
  tasks: 2 of 2 completed
---

# Phase 9 Plan 06: Live post-fix UAT Summary

On the local reasoning model `qwen/qwen3.5-9b`, scenarios A, D and E each produced a `chat_title_set` log line with `source=llm`; the 09-05 fix works end to end. The DeepSeek check stays a pending human item with a runnable procedure.

## Tasks

1. Live Playwright re-run of A, D, E on the isolated copy: no repository commit (all artifacts outside the repo).
2. 09-HUMAN-UAT.md updated (test 3 added with observed result, test 1 rewritten as an API-level procedure): e34a67a

## Live UAT (post-fix, local model)

Model: `qwen/qwen3.5-9b` (already loaded; not loaded/unloaded by the run). Working tree contained `TITLE_REASONING_EFFORT` (09-05 fix). Copy at UI :18000 / Agent :18001, scratch DB, no `.env`, headless Chromium.

| Scenario | chat_id | First message | Title shown | `source` (from agent.log) | Equals fallback | Result |
| -------- | ------- | ------------- | ----------- | ------------------------- | --------------- | ------ |
| A live rename | 1 | Как настроить WebSocket в FastAPI? | Настройка WebSocket в FastAPI (29) | llm | no (fallback = the message itself) | PASS |
| D cross-chat delivery | 2 | Посоветуй рецепт борща | Классический рецепт домашнего борща с говядиной (47) | llm | no (fallback = "Посоветуй рецепт борща") | PASS (sidebar button updated while header kept A's title) |
| E markup probe | 3 | `<img src=x onerror=alert(1)> что такое HTML?` | HTML это язык разметки для веб страниц (38) | llm | no (fallback = "что такое HTML?") | PASS (0 dialogs, 0 img in `#chat-list`/`#chat-title`, no `<` `>`) |

Log lines (copied from the copy's `logs/agent.log`):
```
{"chat_id": 1, "source": "llm", "length": 29, "event": "chat_title_set", ...}
{"chat_id": 2, "source": "llm", "length": 47, "event": "chat_title_set", ...}
{"chat_id": 3, "source": "llm", "length": 38, "event": "chat_title_set", ...}
```
`grep '"event": "chat_title_set"' agent.log | grep -c '"source": "llm"'` printed `3`.

Counts: `chat_title_llm_unusable` = 0, `chat_title_llm_failed` = 0, `chat_title_reasoning_control_rejected` = 0.

Artifacts (outside the repo, scratchpad `C:\Users\Aleksey\AppData\Local\Temp\claude\C--Projects-AiAdventAgentV2\221322ad-da88-4636-9677-aabf9744be97\scratchpad\uat-09-gap\`): `shots\A.png`, `shots\D.png`, `shots\E.png`, `agent.log.copy`, `uat.py`, `results.json`, `app\` (the copy).

Ports 8000/8001 were never started, stopped, killed or reconfigured. DeepSeek was not exercised and no cloud call was made; `.env` was not read. The copy's process tree (PIDs 17728/19660, started by me) was killed; `/health` on 18001 returns no response and nothing listens on 18000/18001. Scratch DB deleted.

Verified: A, D, E as above. Not verified: DeepSeek backend; scenarios B, C, F were not re-run (unchanged by the fix).

## Verification

`pytest tests/test_titles.py tests/test_titles_ws.py tests/test_llm_complete_chat.py tests/test_static_js_syntax.py -q`: `86 passed, 2 warnings`. `git status --porcelain` was clean after the UAT.

## Deviations from Plan

- Worktree HEAD was at an older ancestor (64d13b5); reset to the expected base d0fbac2 per the branch check (agent/titles.py then contained `TITLE_REASONING_EFFORT`).
- The copy's `agent/ws.py` has additional hard-coded 8000/8001 origins in `_validate_origin`; they are only an extra allow-list and `CORS_ORIGINS` (patched to 18000) covers the copy, so no change was needed.
- Acceptance greps for `reasoning_effort` count lines; the DeepSeek note is a single line, so the count is 1 rather than >= 2 (the term appears multiple times in that line).
- No code fixes were needed.

## Open items for the user

Run the DeepSeek procedure in test 1 of 09-HUMAN-UAT.md with your own key (pending).

## Known Stubs

None.

## Self-Check: PASSED

09-HUMAN-UAT.md present and committed (e34a67a); evidence files present in the scratchpad.
