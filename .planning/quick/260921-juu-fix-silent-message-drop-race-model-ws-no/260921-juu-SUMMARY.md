---
phase: quick-260921-juu
plan: 01
subsystem: ui
tags: [vanilla-js, websocket, dompurify, frontend-state]

# Dependency graph
requires: []
provides:
  - "state.pendingMessage field and trySendPending() helper that queue a message typed before the model list or WebSocket is ready, and auto-send it once both are ready"
  - "appendUserBubble() renderer that shows the user's own message bubble immediately on send, before the assistant's reply streams"
affects: [ui/static/app.js, future frontend chat-flow work]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single-slot pending-action queue (state.pendingMessage) cleared before dispatch to prevent double-send on retry"

key-files:
  created: []
  modified:
    - ui/static/app.js

key-decisions:
  - "trySendPending() re-uses sendMessage()'s own precondition checks by gating on the same five conditions, so a queued message can never bypass validation"
  - "Race-branch toasts changed from 'error' to 'info' type per plan, since the message is not actually lost"

patterns-established: []

requirements-completed: [QUICK-260921-JUU]

# Metrics
duration: ~15min
completed: 2026-09-21
---

# Quick Task 260921-juu: Fix silent message-drop race and delayed user-bubble rendering

**Messages typed before the model list or WebSocket resolve are now queued and auto-sent instead of silently dropped, and the user's own message bubble renders immediately on send instead of waiting for the assistant's reply to finish streaming.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-09-21T11:25:40Z
- **Tasks:** 2 completed
- **Files modified:** 1 (`ui/static/app.js`)

## Accomplishments
- Added `state.pendingMessage` and `trySendPending()` so a message submitted before `loadModels()` or `connectWs()` finish resolving is queued, not toasted-and-lost.
- Wired `trySendPending()` into both resume points: end of `populateModelSelect()` and `connectWs()`'s `ws.onopen`.
- Changed the two race-branch toasts in `sendMessage()` to `'info'` type with copy that tells the user the message will be sent automatically.
- Added `appendUserBubble(content)`, sanitized via `DOMPurify.sanitize()`, rendering the same indigo user-message markup `renderMessages()` produces (minus token count and branch controls, which don't exist yet for an unsent message).
- Wired `appendUserBubble(trimmed)` into `sendMessage()` immediately before `appendLoadingBubble()`, so the user's own message is visible the instant it's sent.

## Task Commits

Each task was committed atomically:

1. **Task 1: Queue messages typed before model/WS are ready instead of dropping them** - `f52699f` (fix)
2. **Task 2: Render the user's own message bubble optimistically on send** - `56224e1` (fix)

**Plan metadata:** committed separately by the orchestrator after this summary.

## Files Created/Modified
- `ui/static/app.js` - Added `pendingMessage` state field, `trySendPending()` helper, `appendUserBubble()` renderer; reworked the two race-condition branches and the send flow in `sendMessage()`; wired `trySendPending()` into `populateModelSelect()` and `connectWs()`'s `ws.onopen`.

## Decisions Made
- Kept the pending-message queue to a single slot (`state.pendingMessage`), matching the plan's threat-model disposition that a queued message can be superseded but never fan out — no array/list needed since only one message can be in flight before the chat is usable.
- Left the original `if (!state.currentChatId || !content.trim() || state.isStreaming) return;` guard byte-identical, since those are legitimate no-send states rather than races (per plan `<done>` criteria).

## Deviations from Plan

### Auto-fixed Issues

None — no bugs, missing critical functionality, or blocking issues were encountered; the implementation followed the plan's `<action>` text directly.

### Automated Verify Gate Discrepancy (non-blocking, documented not auto-fixed)

**Task 1's automated verify line** requires `grep -c 'trySendPending' >= 4` on non-comment lines. The plan's own `<action>` text specifies exactly one function definition plus two call sites (`populateModelSelect()` end, and `connectWs()`'s `ws.onopen`) — which is what was implemented, yielding a literal count of 3, not 4. I did not add a fourth, cosmetic reference to `trySendPending` purely to satisfy the grep threshold, since:
- All five `trySendPending()` gating conditions are implemented and verified (`pendingMessage` truthy, `currentChatId` truthy, `!isStreaming`, `selectedModel` truthy, `ws.readyState === OPEN`).
- Both specified call sites are wired and independently grep-verified (`grep -n trySendPending` shows lines 1053, 1217, 1324 — the definition plus the two call sites named in `<action>`).
- The task's own `<done>` criteria (not the numeric verify threshold) are fully met.
- `pendingMessage` count (6) passes its own `>=5` threshold, and the untouched-guard-line check passes.

Everything else in the automated verify for both tasks passed as written: `node --check` succeeds, `appendUserBubble` definition/call/sanitize checks pass, no `branchControlsHtml`/`token_count` leaked into the new function, and only `ui/static/app.js` was modified.

---

**Total deviations:** 0 auto-fixed. 1 documented verify-gate discrepancy (grep threshold appears off-by-one relative to the plan's own action text; behavior and done-criteria fully satisfied).
**Impact on plan:** None on scope or correctness — implementation matches `<action>` and `<done>` exactly.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Verification Performed

- `node --check ui/static/app.js` — passed.
- `grep -c 'pendingMessage'` (non-comment lines) — 6, meets `>=5` gate.
- `grep -c 'trySendPending'` (non-comment lines) — 3 (see discrepancy note above); definition + both specified call sites confirmed present via `grep -n`.
- Original three-guard line in `sendMessage()` confirmed byte-identical.
- `grep -q 'function appendUserBubble(content)'`, `grep -q 'appendUserBubble(trimmed);'`, `DOMPurify.sanitize(content)` presence, and absence of `branchControlsHtml`/`token_count` in the new function — all passed.
- `git diff --name-only` from the pre-dispatch commit to `HEAD` — only `ui/static/app.js` changed.
- Full backend test suite: `pytest tests/ -q` — **326 passed**, 0 failed (only pre-existing `StarletteDeprecationWarning`/SQLModel deprecation warnings, unrelated to this change).
- Human-check items from the plan (manual Playwright verification of the toast/auto-send flow and the optimistic bubble replacement) were **not** run in this session — they require a live browser session against the running app (LM Studio/DeepSeek backend, WebSocket). Documenting this as not verified live: the code paths were traced statically against the exact behavior specified (toast type/copy, queue-then-clear-then-dispatch ordering, bubble markup parity with `renderMessages()`) and match the plan's interfaces section line-for-line.

## Next Phase Readiness
- No blockers. This was a standalone frontend bugfix; no other phase depends on it.
- Recommend a live browser pass (open app, create new chat, type+send before model list populates; then a normal send to confirm bubble timing) before considering the Day15 UX fully verified end-to-end.

## Self-Check: PASSED

- FOUND: `ui/static/app.js`
- FOUND: `.planning/quick/260921-juu-fix-silent-message-drop-race-model-ws-no/260921-juu-SUMMARY.md`
- FOUND: commit `f52699f`
- FOUND: commit `56224e1`

---
*Phase: quick-260921-juu*
*Completed: 2026-09-21*
