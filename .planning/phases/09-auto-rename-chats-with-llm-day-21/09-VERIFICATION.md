---
phase: 09-auto-rename-chats-with-llm-day-21
verified: 2026-10-02T13:00:00Z
status: human_needed
score: 6/6 must-haves verified
has_blocking_gaps: false
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 4/6
  gaps_closed:
    - "TITLE-01 / TITLE-02 blocking gap: LLM title path yielded nothing on the reasoning model. Closed by 09-05 (reasoning_effort 'none' via complete_chat_detailed extra_body, one retry without the field on HTTP 400/422, distinct chat_title_llm_unusable log) and proven live by 09-06 (3/3 chat_title_set source=llm)."
    - "Minor: 09-04-SUMMARY mislabelled fallback titles as LLM titles. Corrected (09-04-SUMMARY now states source=fallback and carries a correction note)."
    - "Minor CR-01 quadratic regex on fallback_title / _snippet: fixed (input cut before regex). Review measured 0.0001 s on the former 3.8 s input."
  gaps_remaining:
    - "WR-04 (minor): clean_title still runs the quadratic <[^>]*> regex on unbounded model output (relies on backend honouring max_tokens)"
    - "WR-01 (minor): chat_title_failed logs str(exc), which can contain bound SQL parameters (title text) on a DB error"
    - "WR-02 (minor): sanitizer strips legitimate characters (C#, user_id, 'a < b')"
    - "WR-03 (minor): '</think>' without an opening tag passes reasoning text as the title"
  regressions: []
gaps:
  - truth: "Title log events never contain message text (review WR-01)"
    status: partial
    severity: minor
    reason: "agent/titles.py:226-227 logs error=str(exc) on a DB failure; a SQLAlchemy error string embeds the UPDATE parameters, which on the fallback path is the first 50 chars of the user's message. Only reachable on a DB error (e.g. database is locked). Breaks the project no-user-data-in-logs rule and the docs claim at TESTING_GUIDE.md:174; does not break any TITLE-0x requirement."
    artifacts:
      - path: "agent/titles.py"
        issue: "chat_title_failed logs str(exc)"
    missing:
      - "Log error_type and str(exc.orig) instead; add a test asserting no message text in log values on an OperationalError"
  - truth: "Sanitizer preserves legitimate title characters (review WR-02)"
    status: partial
    severity: minor
    reason: "_strip_markup removes every * _ # ` ~ and everything between any < and >: 'Основы C# и F#' -> 'Основы C и F', 'user_id' -> 'userid', 'a < b и c > d' -> 'a d'. Titles are wrong for programming chats but safe; TITLE-06 (no markup, textContent) still holds."
    artifacts:
      - path: "agent/titles.py"
        issue: "over-aggressive _MARKDOWN_RE / _TAG_LIKE_RE"
    missing:
      - "Strip only real tags and paired markdown markers"
  - truth: "Model reasoning is never stored as a title (review WR-03)"
    status: partial
    severity: minor
    reason: "clean_title('reasoning</think>Real title') -> 'reasoning Real title'. Reachable only if a backend emits an unopened </think> in content (e.g. after the retry without reasoning control). Not observed live (0 unusable events)."
    artifacts:
      - path: "agent/titles.py"
        issue: "no handling of a lone closing </think>"
    missing:
      - "Keep only the text after the last </think>"
  - truth: "Title helpers stay cheap on arbitrarily large model output (review WR-04)"
    status: partial
    severity: minor
    reason: "clean_title applies _TAG_LIKE_RE to the whole first line with no length cut; '<'*100000 took ~3.6 s in the review. The only bound is the backend honouring max_tokens=30; a compliant backend yields a few hundred chars. Runs after the done frame. The user-reachable vector (CR-01, user message) is fixed."
    artifacts:
      - path: "agent/titles.py"
        issue: "clean_title input not truncated before regex"
    missing:
      - "raw = raw[:1000] before regexes; make the tag regex linear (<[^<>]*>)"
human_verification:
  - test: "DeepSeek backend: POST the title request shape (deepseek-chat, temperature 0, max_tokens 30, stream false, reasoning_effort none) using the command in 09-HUMAN-UAT.md item 1, with DEEPSEEK_API_KEY set in the shell"
    expected: "HTTP 200, finish_reason stop, non-empty message.content that is a short title. If HTTP 400/422, repeat without reasoning_effort and record both (the Agent does the same retry). Empty content with finish_reason length means that backend ignores the field and would get fallback titles (logged chat_title_llm_unusable)."
    why_human: "Needs the user's DeepSeek API key and a paid cloud call. Nobody has exercised DeepSeek; this is pending, not passed."
---

# Phase 9: Auto-rename chats with LLM (Day 21) Verification Report

**Phase Goal:** Every chat is titled 'New Chat'; the LLM generates a short title per chat after the first Q&A turn (3-8 words / ~50 chars, user's language, chat's current model), fallback = truncated first user message only if the LLM call fails; non-blocking, pushed over WebSocket so the sidebar updates.
**Verified:** 2026-10-02
**Status:** human_needed (all must-haves verified; one pending human item, DeepSeek; no blocking gaps)
**Re-verification:** Yes, after gap closure (plans 09-05, 09-06)

## Goal Achievement

The previous blocking gap is closed with independent evidence, not summary claims.

Evidence observed by the verifier:
- `agent/titles.py:131-154`: `_complete_title` calls `llm_client.complete_chat_detailed(... temperature=0.0, max_tokens=30, extra_body={"reasoning_effort": "none"})`; on HTTPStatusError 400/422 it repeats once with `extra_body=None`; other errors propagate to `request_title`, which logs `chat_title_llm_failed` and returns None. Both attempts sit under one `asyncio.wait_for(20 s)`.
- `agent/llm_client.py:76-111`: `complete_chat_detailed` merges `extra_body` first and applies the core keys last (cannot be overridden), returns `ChatCompletionResult(content, finish_reason, has_reasoning, completion_tokens)`. `complete_chat` is unchanged (five-key payload, returns content).
- `agent/titles.py:168-178`: empty/unusable content now logs `chat_title_llm_unusable` (model, finish_reason, content_empty, has_reasoning, completion_tokens; no text). The silent-100%-fallback failure mode is now observable.
- Live UAT log (`uat-09-gap/agent.log.copy`), read directly: exactly 3 `chat_title_set` lines, all `source: "llm"` (chat 1 length 29, chat 2 length 47, chat 3 length 38). `grep -cE "unusable|llm_failed|rejected"` = 0.
- `results.json`: titles are 'Настройка WebSocket в FastAPI', 'Классический рецепт домашнего борща с говядиной', 'HTML это язык разметки для веб страниц'. Each differs from its first user message ('Как настроить WebSocket в FastAPI?', 'Посоветуй рецепт борща', '<img src=x onerror=alert(1)> что такое HTML?'), so they are not fallback output; all in Russian (user's language); sidebar/header updated via `chat_title_updated` frame; `imgs: 0`, `dialogs: []`.
- Independent read-only probe of LM Studio (`qwen/qwen3.5-9b`, temperature 0, max_tokens 30, stream false, reasoning_effort none): content 'FastAPI WebSocket Implementation Guide', finish_reason stop, completion_tokens 6, reasoning_tokens 0. Confirms the model behaviour that previously failed is now fixed by the request shape. (The probe prompt was a simplified English system prompt, so it confirms the mechanism, not the exact production prompt; the production prompt is covered by the UAT log above.)
- Tests: `tests/test_titles.py tests/test_titles_ws.py tests/test_llm_complete_chat.py` re-run on a scratch DB_PATH: 80 passed. Orchestrator's full suite: 1009 passed.
- Hook wiring: `agent/ws.py:960-965` schedules `schedule_title_generation` before the `done` frame, gated on `chat.title == DEFAULT_CHAT_TITLE and user_msg.parent_id is None`; `ui/static/app.js:2582` handles `chat_title_updated`.

Limit of what was observed: the local run covers LM Studio only. UAT was a single run of three chats, not a statistical sample; the model is deterministic at temperature 0.

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | TITLE-01: LLM generates 3-8 word / <=50 char title once after the first Q&A turn, in the user's language, with the chat's current model | VERIFIED (local model); DeepSeek not exercised | 3/3 live chats source=llm, lengths 29/47/38, Russian, differ from user text; model passed through from payload.model |
| 2 | TITLE-02: non-blocking extra call, temperature 0, max_tokens ~30, plain text, timeout | VERIFIED | create_task before done frame; temp 0.0, max_tokens 30, non-streaming plain content, 20 s wait_for spanning retry; test for done not delayed by blocked title call passes |
| 3 | TITLE-03: fallback = truncated first user message on failure / timeout / unusable | VERIFIED | Pytest; earlier browser UAT with injected HTTP 500 (passed, HUMAN-UAT item 2); unusable case now logged distinctly |
| 4 | TITLE-04: non-default title never overwritten, race-safe | VERIFIED | `apply_title` conditional UPDATE `WHERE title='New Chat'`, rowcount==1 |
| 5 | TITLE-05: persisted and pushed as chat_title_updated to owner only; sidebar and header update live | VERIFIED | Live frames observed in UAT for chats 1-3; hub.publish scoped by user_id; app.js handler |
| 6 | TITLE-06: injection hardening | VERIFIED | Tag wrapping + breakout stripping + sanitizer + textContent; scenario E (`<img onerror>`) produced an LLM title with 0 img elements and 0 dialogs. Sanitizing of real LLM output now observed non-empty |

**Score:** 6/6

### Requirements Coverage

| Requirement | Status | Evidence |
|-------------|--------|----------|
| TITLE-01 | SATISFIED (local backend); DeepSeek pending human | 3 source=llm events, titles differ from user messages |
| TITLE-02 | SATISFIED | Mechanics in code and tests; the ~30 token budget now works with reasoning_effort none (completion_tokens 6 in probe) |
| TITLE-03 | SATISFIED | Tests + browser UAT with injected failure |
| TITLE-04 | SATISFIED | Conditional UPDATE |
| TITLE-05 | SATISFIED | Live frame and sidebar/header update observed |
| TITLE-06 | SATISFIED | Code, tests, XSS scenario E live |

All six IDs from the plan frontmatter appear in REQUIREMENTS.md (lines 37-42) and the traceability table (84-89). The traceability table still shows "Pending" for all six; the orchestrator should flip it when the phase is closed. No orphaned requirements (no other IDs map to Phase 9).

### Key Links

| From | To | Status |
|------|----|--------|
| ws.py `_handle_chat_message` | titles.schedule_title_generation | WIRED |
| titles._complete_title | llm_client.complete_chat_detailed (extra_body reasoning_effort) | WIRED |
| titles.request_title | clean_title / chat_title_llm_unusable | WIRED |
| titles.py | events.hub.publish | WIRED (function-local import due to cycle, IN-06) |
| state.cleanup_chat_caches | title_tasks cancel | WIRED |
| app.js onmessage | handleEventFrame -> applyChatTitleUpdate | WIRED |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Local model yields title at max_tokens 30 with reasoning off | curl POST /v1/chat/completions | content 'FastAPI WebSocket Implementation Guide', stop, 6 tokens | PASS |
| LLM-sourced titles in UAT log | read agent.log.copy | 3/3 source=llm; 0 unusable/failed/rejected | PASS |
| Title test files | pytest (3 files, scratch DB) | 80 passed | PASS |
| DeepSeek | not run | n/a | SKIP (human) |

### Review Warnings Triage (09-REVIEW.md: 0 critical, 4 warnings, 15 info)

None of the four warnings breaks a TITLE requirement, so none is blocking; each is recorded as a minor gap above (WR-01 log leak on DB error, WR-02 over-aggressive sanitizer, WR-03 lone `</think>`, WR-04 unbounded `clean_title` regex). The 15 info/convention items (including IN-02 max_tokens headroom, IN-09 400/422 labelling, IN-12 pre-existing DeepSeek key sent to LM_STUDIO_BASE_URL) are advisory and out of phase scope; IN-12 is pre-existing code. Recommend routing the minor gaps to backlog, with WR-01 first because it conflicts with a documented guarantee.

### Anti-Patterns

No TBD/FIXME/XXX in phase files. No stubs found. Documentation accuracy gap (09-04-SUMMARY) corrected.

### Human Verification Required

1. **DeepSeek title call (API level).** Run the command in 09-HUMAN-UAT.md item 1 with `DEEPSEEK_API_KEY` set in the shell. Expected: HTTP 200, non-empty short title, finish_reason stop; on 400/422 repeat without `reasoning_effort`. Why human: requires the user's API key and a paid cloud call. DeepSeek has not been exercised by anyone; this is pending, not a pass.

### Gaps Summary

No blocking gaps. The central deliverable (the LLM writes the title) is now observed working end to end on the local model. Four minor review-derived gaps remain (WR-01..04), plus one unexercised backend (DeepSeek).

---
_Re-verified: 2026-10-02_
_Verifier: Claude (gsd-verifier)_
