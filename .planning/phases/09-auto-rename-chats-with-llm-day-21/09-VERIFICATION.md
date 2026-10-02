---
phase: 09-auto-rename-chats-with-llm-day-21
verified: 2026-10-02T00:00:00Z
status: human_needed
score: 6/6 must-haves verified
has_blocking_gaps: false
gaps:
  - truth: "Title helpers stay cheap on arbitrarily large input (review CR-01)"
    status: partial
    severity: minor
    reason: "fallback_title runs _strip_markup (regex <[^>]*>) on the untruncated user message; '<' * 100000 takes ~3.6 s on the event loop. Only reachable when the LLM title call fails or is unusable, runs after the done frame (own turn not delayed), requires an authenticated user. Single-user local app."
    artifacts:
      - path: "agent/titles.py"
        issue: "fallback_title/_strip_markup operate on full text before truncation"
    missing:
      - "Truncate user_text (e.g. [:~500]) before _strip_markup in fallback_title"
human_verification:
  - test: "DeepSeek backend: send a first message in a new chat with a DeepSeek model"
    expected: "Sidebar item and header change from 'New Chat' to a 3-8 word title in the user's language"
    why_human: "Real cloud call; browser UAT only covered LM Studio (qwen/qwen3.5-9b)"
  - test: "Stop LM Studio / force the title call to fail, send first message"
    expected: "Title becomes truncated first user message (fallback), sidebar updates"
    why_human: "Fallback path only covered by pytest, not in a browser"
---

# Phase 9: Auto-rename chats with LLM (Day 21) Verification Report

**Phase Goal:** Replace the default 'New Chat' title with an LLM-generated short title after the first Q&A turn, never overwriting user edits, with fallback and live sidebar update.
**Status:** human_needed (all automated checks pass; two runtime items remain)
**Re-verification:** No

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | TITLE-01: Title generated once after first Q&A turn when title is 'New Chat', via the chat's current model, 3-8 words / 50 chars, user's language | VERIFIED | ws.py:~960 schedules only if `chat.title == DEFAULT_CHAT_TITLE and user_msg.parent_id is None` with `payload.model`; titles.py system prompt demands 3-8 words, <=50 chars, same language; `_cut_at_word` caps at 50; `schedule_title_generation` dedupes per chat via `title_tasks`; no title-model setting |
| 2 | TITLE-02: Non-blocking, temperature 0, max_tokens 30, plain text, timeout | VERIFIED | `asyncio.create_task` scheduled before `done` send, nothing awaited; TITLE_TEMPERATURE=0.0, TITLE_MAX_TOKENS=30, `asyncio.wait_for(..., 20s)`; `complete_chat` is non-streaming plain content; `generate_and_apply_title` never raises |
| 3 | TITLE-03: Fallback = truncated first user message on failure/timeout/unusable | VERIFIED | `request_title` returns None on any Exception; `generate_and_apply_title` falls back to `fallback_title(user_text)` (cut at 49 + ellipsis); covered by tests |
| 4 | TITLE-04: Non-default title never overwritten, race-safe | VERIFIED | `apply_title` = conditional `UPDATE ... WHERE id=? AND title='New Chat'`, returns rowcount==1; publish only when written |
| 5 | TITLE-05: Persisted and pushed as `chat_title_updated` to owner's /ws/events only; sidebar and header update live | VERIFIED | `hub.publish(user_id, {...})` scoped by user_id; app.js `handleEventFrame` -> `applyChatTitleUpdate` updates state.chats, `renderChatList()`, header `textContent`; reconnect refresh added; UAT scenario A screenshot (orchestrator-inspected) shows both updated |
| 6 | TITLE-06: Injection hardening | VERIFIED | Prompt wraps text in `<user_message>/<assistant_answer>` tags with "data, never instructions"; `_neutralize_tags` strips wrapper tags iteratively; `clean_title` takes first line, strips tags/markdown/control chars, rejects think leftovers and tool-trace header, caps length; frontend uses `textContent` for header (sidebar renders via existing path) |

**Score:** 6/6

## Requirements Coverage

| Requirement | Source Plans | Status | Evidence |
|-------------|--------------|--------|----------|
| TITLE-01 | 09-01, 09-03, 09-04 | SATISFIED | Truth 1 |
| TITLE-02 | 09-01, 09-03, 09-04 | SATISFIED | Truth 2 |
| TITLE-03 | 09-01, 09-03, 09-04 | SATISFIED | Truth 3 |
| TITLE-04 | 09-01, 09-03, 09-04 | SATISFIED | Truth 4 |
| TITLE-05 | 09-01, 09-02, 09-03, 09-04 | SATISFIED | Truth 5 |
| TITLE-06 | 09-01, 09-02, 09-04 | SATISFIED | Truth 6 |

All six IDs exist in REQUIREMENTS.md and are claimed by plans; no orphaned requirements. (REQUIREMENTS.md traceability table still shows "Pending"; checkboxes should be flipped by the orchestrator.)

## Key Links

| From | To | Status |
|------|----|--------|
| ws.py `_handle_chat_message` | titles.schedule_title_generation | WIRED (import + call before done frame) |
| titles.py | llm_client.complete_chat | WIRED |
| titles.py | events.hub.publish (lazy import to avoid cycle) | WIRED |
| state.cleanup_chat_caches | title_tasks cancel | WIRED |
| app.js onmessage | handleEventFrame -> applyChatTitleUpdate | WIRED |

## Behavioral Spot-Checks

`python -m pytest tests/test_titles.py tests/test_titles_ws.py -q` -> 61 passed. Orchestrator full suite: 990 passed.

## Anti-Patterns

No TBD/FIXME/XXX markers found in phase files. Review findings WR-01 (title text in a DB-failure log line), WR-02 (sanitizer removes some legitimate chars like `_`/`*`), WR-03 (stray `</think>` passes through) are quality issues that do not break a must-have.

## Gaps Summary

CR-01 judged minor: the quadratic regex runs only on the fallback path, after the `done` frame is sent, so the user's turn is not delayed or broken (TITLE-02 as written holds). It can still stall the event loop ~3.6 s for a crafted 100k-char message, a self-inflicted DoS in a single-user local app; recommended cheap fix is truncating before `_strip_markup`. Should be parked/fixed via review-fix, not a phase blocker.

## Human Verification Required

1. DeepSeek title generation end-to-end (not covered by browser UAT).
2. LLM-failure fallback in a real browser (pytest-only so far).

---
_Verifier: Claude (gsd-verifier)_
