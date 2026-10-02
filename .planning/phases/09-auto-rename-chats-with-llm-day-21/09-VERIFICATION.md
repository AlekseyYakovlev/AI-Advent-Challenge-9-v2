---
phase: 09-auto-rename-chats-with-llm-day-21
verified: 2026-10-02T12:00:00Z
status: gaps_found
score: 4/6 must-haves verified
has_blocking_gaps: true
re_verification:
  previous_status: human_needed
  previous_score: 6/6
  gaps_closed:
    - "Human item 2 (LLM-failure fallback in a real browser): run and passed (proxy-injected HTTP 500 -> fallback title shown live, persisted, unchanged on 2nd turn)"
  gaps_remaining:
    - "CR-01 quadratic regex (minor)"
  regressions:
    - "TITLE-01 / TITLE-02 downgraded from VERIFIED to FAILED/PARTIAL: new runtime evidence shows the LLM title path never yields a title on the only model it was run against"
gaps:
  - truth: "TITLE-01: the LLM generates a short title per chat after the first Q&A turn (the phase goal)"
    status: failed
    severity: blocking
    requirements: [TITLE-01, TITLE-02]
    reason: "With the user's loaded local model (qwen/qwen3.5-9b, a reasoning model) the title request (max_tokens=30, temperature 0) is consumed entirely by reasoning: finish_reason=length, content='', reasoning_content='Thinking Process...'. clean_title('') returns None, so every chat falls back to the truncated user message. Re-probed by the verifier against LM Studio: identical result (completion_tokens=30, reasoning_tokens=30, content ''). All 8 chat_title_set events in the UAT agent.log have source=fallback; zero have source=llm. The LLM path has never been observed producing a title in any live run; DeepSeek is untested."
    artifacts:
      - path: "agent/titles.py"
        issue: "TITLE_MAX_TOKENS=30 with no reasoning control; request_title treats empty content as plain 'unusable' with no distinct log, so the failure is silent (no chat_title_llm_failed, source=fallback)"
      - path: "agent/llm_client.py"
        issue: "complete_chat returns only message.content; ignores finish_reason and reasoning_content and cannot pass extra body params (e.g. reasoning_effort)"
    missing:
      - "Make the title call yield usable content on reasoning models. Options (planner decides): (a) send reasoning_effort='none' with max_tokens 30 (orchestrator probe: stop, 7 tokens, good title; must confirm DeepSeek/other OpenAI-compatible backends tolerate the field); (b) prefill assistant '<think>\\n\\n</think>\\n\\n' (probe: good result, but model/template specific); (c) raise max_tokens to ~2000+ (probe: 501 tokens, works but ~17x cost/latency and conflicts with the TITLE-02 '~30' wording; 400 still failed); (d) fall back to reasoning_content parsing (unreliable). /no_think suffix and chat_template_kwargs.enable_thinking=false did NOT work per probe."
      - "Log a distinct event (e.g. chat_title_llm_unusable with finish_reason / empty-content flag) so a silent 100% fallback is observable"
      - "A regression test using a reasoning-style response (content '', finish_reason length, reasoning_content set) asserting the request payload carries the reasoning control and that the empty-content case is logged"
      - "Live evidence of at least one chat_title_set source=llm on the local model (and ideally DeepSeek) before the phase is called done"
  - truth: "09-04-SUMMARY.md browser UAT table reports LLM-generated titles (scenarios A/D/E)"
    status: failed
    severity: minor
    requirements: [TITLE-01]
    reason: "The titles recorded as 'generated' ('Как настроить WebSocket в FastAPI?' len 34, 'Посоветуй рецепт борща'/'...плова' len 22, 'что такое HTML?' len 15) are the user's own first message produced by fallback_title; agent.log shows source=fallback for all of them. The UAT validated the live sidebar/header frame (TITLE-05) but not LLM generation, and the SUMMARY mislabels it. This is a documentation-accuracy gap that also masked the blocking gap above."
    artifacts:
      - path: ".planning/phases/09-auto-rename-chats-with-llm-day-21/09-04-SUMMARY.md"
        issue: "UAT table presents fallback titles as LLM-generated; should state source=fallback and that LLM generation was not observed"
    missing:
      - "Correct the SUMMARY UAT rows to say these were fallback titles; record the source field from agent.log for each scenario in the re-run UAT"
      - "Re-run browser scenarios A/D/E after the fix and assert source=llm in agent.log (title differs from the raw user message)"
  - truth: "Title helpers stay cheap on arbitrarily large input (review CR-01)"
    status: partial
    severity: minor
    requirements: [TITLE-02]
    reason: "fallback_title/_strip_markup run regex <[^>]*> on the untruncated user message; '<' * 100000 takes ~3.6 s on the event loop (reproduced). Reachable only on the fallback path (which is now the 100% path on a reasoning model), runs after the done frame so the user's own turn is not delayed, requires an authenticated user. Single-user local app."
    artifacts:
      - path: "agent/titles.py"
        issue: "fallback_title/_strip_markup operate on full text before truncation"
    missing:
      - "Truncate user_text (e.g. [:~500]) before _strip_markup in fallback_title"
human_verification:
  - test: "DeepSeek backend: send a first message in a new chat with a DeepSeek model"
    expected: "Sidebar item and header change from 'New Chat' to a 3-8 word title that is NOT just the user's message; agent.log shows chat_title_set source=llm"
    why_human: "Real cloud call needing the user's API key; never exercised"
  - test: "After the reasoning-model fix, re-run the browser UAT on the local model"
    expected: "Title is a 3-8 word LLM-written title in the user's language, agent.log source=llm"
    why_human: "Needs the real loaded model and a browser"
---

# Phase 9: Auto-rename chats with LLM (Day 21) Verification Report

**Phase Goal:** Every chat is titled 'New Chat'; the LLM should generate a short title per chat after the first Q&A turn (3-8 words / ~50 chars, in the user's language, by the chat's current model), with fallback = truncated first user message only if the LLM call fails.
**Status:** gaps_found (one blocking gap)
**Re-verification:** Yes - after new runtime evidence from the orchestrator's browser UAT logs and direct model probes. The previous report (human_needed, 6/6) relied on code reading plus tests and on SUMMARY/UAT claims that turned out to be fallback titles.

## Verdict on the goal

The goal is not achieved. The central deliverable is "the LLM generates the title". The only model the phase has been run against never produces a usable LLM title: the whole `max_tokens=30` budget is spent in `reasoning_content`, `content` is empty, `clean_title('')` is None, and the app silently substitutes the truncated user message on every chat. What ships today is the fallback path presented as the primary path. The fallback is meant for LLM failure ("only if the LLM call fails"); here it is the steady state and nothing in the logs or UI flags it. Passing tests (990) and a green browser UAT do not change this because the tests mock the LLM response (a clean content string) and the UAT never checked `source`.

Verifier re-probe (read-only, LM Studio `qwen/qwen3.5-9b`, system+user prompt like the real one, temperature 0, max_tokens 30, stream false): `finish_reason=length`, `content=""`, `reasoning_content="Thinking Process: ..."`, `completion_tokens=30`, `reasoning_tokens=30`. Confirms orchestrator finding. `grep` of the UAT agent.log: 8 `chat_title_set` events, 0 with `source=llm`.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | TITLE-01: LLM generates a 3-8 word / 50 char title once after first Q&A turn, in the user's language, with the chat's current model | FAILED (blocking) | Scheduling, gating, prompt, 50-char cap and once-per-chat dedupe are correct in code, but on the actual model no LLM title is ever produced (reasoning exhausts max_tokens). 0/8 live title events have source=llm. DeepSeek untested. |
| 2 | TITLE-02: Non-blocking extra call, temperature 0, max_tokens ~30, plain text, timeout | PARTIAL (blocking via link to #1) | Literally implemented (create_task before done frame, temp 0.0, max_tokens 30, 20 s wait_for, non-streaming plain content, never raises). But the mandated ~30 budget is incompatible with a reasoning model without a reasoning control, so the call is wasted. The requirement text itself may need amendment (e.g. allow a reasoning-off parameter). |
| 3 | TITLE-03: Fallback = truncated first user message on failure/timeout/unusable | VERIFIED | Pytest, plus browser UAT (proxy-injected HTTP 500 -> 'Расскажи коротко, что такое асинхронное…' shown live, persisted, unchanged on 2nd turn). Note: it also catches the silent "unusable" case, which is why the gap above stayed hidden. |
| 4 | TITLE-04: Non-default title never overwritten, race-safe | VERIFIED | `apply_title` conditional `UPDATE ... WHERE id=? AND title='New Chat'`, rowcount==1; UAT confirmed title unchanged on 2nd turn. |
| 5 | TITLE-05: Persisted and pushed as chat_title_updated to owner's /ws/events only; sidebar and header update live | VERIFIED | Hub publish scoped by user_id; app.js handler; browser UAT observed live sidebar/header update (with fallback-sourced titles, which exercise the identical frame path). |
| 6 | TITLE-06: Injection hardening (tags, breakout stripping, sanitized output, textContent) | VERIFIED (with caveat) | Code and tests hold. Output sanitization of real LLM output has not been observed live because no LLM output ever reached `clean_title` non-empty. |

**Score:** 4/6 (TITLE-03, 04, 05, 06 verified; TITLE-01 failed; TITLE-02 partial)

## Requirements Coverage

| Requirement | Status | Evidence |
|-------------|--------|----------|
| TITLE-01 | NOT SATISFIED (blocking) | LLM title never produced on the local model; no source=llm event in any run |
| TITLE-02 | PARTIALLY SATISFIED (blocking, same root cause) | Mechanics correct; max_tokens 30 yields empty content on reasoning model |
| TITLE-03 | SATISFIED | Pytest + browser UAT of injected failure |
| TITLE-04 | SATISFIED | Conditional UPDATE + UAT |
| TITLE-05 | SATISFIED | Live frame observed |
| TITLE-06 | SATISFIED (LLM-output sanitization unobserved live) | Code + tests |

REQUIREMENTS.md traceability still shows "Pending" for all six; do not flip TITLE-01/02 until the gap is closed.

## Key Links

| From | To | Status |
|------|----|--------|
| ws.py `_handle_chat_message` | titles.schedule_title_generation | WIRED |
| titles.py | llm_client.complete_chat | WIRED, but complete_chat drops reasoning_content/finish_reason and cannot pass reasoning controls (HOLLOW for reasoning models) |
| titles.py | events.hub.publish | WIRED |
| state.cleanup_chat_caches | title_tasks cancel | WIRED |
| app.js onmessage | handleEventFrame -> applyChatTitleUpdate | WIRED |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Title request on live local model | curl POST /v1/chat/completions, max_tokens 30, temp 0 | content '', finish_reason length, 30 reasoning tokens | FAIL |
| Any LLM-sourced title in UAT log | grep `source.*llm` agent.log | 0 matches (8 chat_title_set, all fallback) | FAIL |
| Test suites (orchestrator) | pytest | 990 passed (mocked LLM output, does not cover reasoning responses) | PASS (not probative) |

## Anti-Patterns

No TBD/FIXME/XXX markers in phase files. Review findings WR-01/02/03 remain quality issues. CR-01 retained as minor gap. New: silent degradation - an empty/unusable LLM reply is indistinguishable from a deliberate fallback in logs (only `source=fallback` on the success line), which let this ship unnoticed.

## Gaps Summary

1. BLOCKING (TITLE-01, TITLE-02): LLM title path yields nothing on the reasoning model; app is in permanent fallback. Needs a reasoning control (or equivalent) on the title call, a distinct log for unusable output, a regression test with a reasoning-style response, and live proof of a `source=llm` title. Candidate fixes recorded in the gap; the planner chooses. Option (a) `reasoning_effort: "none"` is the smallest and was shown to work at max_tokens 30; it needs a check against DeepSeek and a decision on whether TITLE-02's wording needs adjusting.
2. MINOR (TITLE-01 evidence): 09-04-SUMMARY.md mislabels fallback titles as generated; correct it and re-run UAT with `source` asserted.
3. MINOR (TITLE-02): CR-01 quadratic regex on fallback path; truncate before `_strip_markup`. Its practical exposure grows now that fallback is the 100% path, but it remains a self-inflicted, post-`done` stall.

## Human Verification Required

1. DeepSeek title generation end-to-end (needs user's API key); expect `source=llm`.
2. Post-fix browser re-run on the local reasoning model; expect `source=llm`.

(Previous item 2, fallback in a browser, is closed: passed.)

---
_Re-verified: 2026-10-02_
_Verifier: Claude (gsd-verifier)_
