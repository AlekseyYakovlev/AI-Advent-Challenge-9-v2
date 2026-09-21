---
phase: quick-260921-juu
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - ui/static/app.js
autonomous: true
requirements: [QUICK-260921-JUU]

must_haves:
  truths:
    - "A message typed before the model list resolves is not discarded — it is sent automatically once a model is selected."
    - "A message typed before the WebSocket is OPEN is not discarded — it is sent automatically once the socket opens."
    - "The toast shown in those two cases tells the user the message will be sent automatically, not that it failed."
    - "The user's own message bubble appears immediately when the message is sent, not after the assistant's reply finishes streaming."
    - "Existing no-send guards (no chat, empty input, already streaming) still return without queueing anything."
  artifacts:
    - path: "ui/static/app.js"
      provides: "pendingMessage state field, trySendPending() helper, appendUserBubble() renderer, reworked sendMessage() preconditions"
      contains: "trySendPending"
  key_links:
    - from: "populateModelSelect()"
      to: "trySendPending()"
      via: "call at end of function, after state.selectedModel assignment"
      pattern: "trySendPending\\(\\)"
    - from: "connectWs() ws.onopen"
      to: "trySendPending()"
      via: "call after state.reconnectAttempt = 0"
      pattern: "trySendPending\\(\\)"
    - from: "sendMessage()"
      to: "appendUserBubble(trimmed)"
      via: "call immediately before appendLoadingBubble()"
      pattern: "appendUserBubble\\(trimmed\\)"
---

<objective>
Fix two frontend bugs in `ui/static/app.js` found during a manual Playwright session on the Day15 branch:

1. **Silent message-drop race** — a message submitted before `loadModels()` or `connectWs()` finish resolving is toasted-and-dropped, and the toast auto-dismisses after 5s leaving no trace of the lost text.
2. **Delayed user bubble** — the user's own message is invisible until `loadChatTree()` runs on the WS `'done'` event, so the user stares at a typing indicator with no record of what they just sent.

Purpose: eliminate two ways the UI silently loses or hides the user's input at exactly the moment they are least sure the app is working (right after creating a new chat).
Output: modified `ui/static/app.js` — new `state.pendingMessage` field, `trySendPending()` helper, `appendUserBubble()` renderer, and reworked precondition branches in `sendMessage()`.
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@ui/static/app.js

<interfaces>
<!-- Existing shapes in ui/static/app.js the executor works against. Use directly — no exploration needed. -->

State object (lines 9-36) — append the new field alongside the existing flat fields:
  currentChatId, messages, models, selectedModel, isStreaming, ws,
  reconnectAttempt, reconnectTimer, shouldReconnect, lastFailedMessage, ...

Relevant existing functions (line numbers as of this plan):
  showToast(message, type = 'error')          — line 56;  types: 'error' | 'info' | 'success' | 'warning'
  renderMessages()                            — line 158; does `container.innerHTML = ''` then full rebuild
  appendLoadingBubble()                       — line 886; appends `[data-streaming="true"]` wrapper, scrolls to bottom
  removeLoadingBubble()                       — line 918
  setStreaming(active)                        — line 923
  connectWs(chatId)                           — line 1042; `ws.onopen` at line 1049 sets `state.reconnectAttempt = 0`
  sendMessage(content)                        — line 1215
  populateModelSelect()                       — line 1286; assigns `state.selectedModel` at lines 1303 / 1306

User-bubble markup produced by renderMessages() for `isUser` messages (lines 164-184) —
`appendUserBubble()` must match this, minus the token line and minus branch controls:
  wrapper  div.className = 'flex justify-end'
  bubble   div.className = 'max-w-[75%] rounded-xl px-4 py-2 text-sm bg-indigo-600 text-white'
  content  div.className = 'message-content prose prose-invert prose-sm max-w-none'
           content.innerHTML = DOMPurify.sanitize(msg.content)   // user text is NOT markdown-rendered
  container = $('messages')  // `<div id="messages" class="flex-1 overflow-y-auto p-4 space-y-4">` in index.html
  scroll:  container.scrollTop = container.scrollHeight
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Queue messages typed before model/WS are ready instead of dropping them</name>
  <files>ui/static/app.js</files>
  <action>
Add `pendingMessage: null` to the `state` object literal (near `lastFailedMessage`, around line 22).

Add a `trySendPending()` helper placed immediately above `sendMessage()`. It must no-op (plain `return`) unless ALL of these hold: `state.pendingMessage` is truthy, `state.currentChatId` is truthy, `!state.isStreaming`, `state.selectedModel` is truthy, and `state.ws && state.ws.readyState === WebSocket.OPEN`. When all hold, read the queued text into a local, set `state.pendingMessage = null` BEFORE dispatching (so a failure path cannot re-enter and double-send), then call `sendMessage(msg)`. Since `sendMessage` is async, attach `.catch((err) => showToast(err.message, 'error'))` to the returned promise — matching how `bindEvents()` handles the same call at line 1452 — rather than leaving an unhandled rejection.

In `sendMessage()`, rework only the two race branches:
- `if (!state.selectedModel)` branch: set `state.pendingMessage = content.trim()` and toast `'Модель ещё загружается — сообщение будет отправлено автоматически'` with type `'info'`, then return.
- `if (!state.ws || state.ws.readyState !== WebSocket.OPEN)` branch: set `state.pendingMessage = content.trim()`, toast `'Переподключение к серверу — сообщение будет отправлено автоматически'` with type `'info'`, keep the existing `connectWs(state.currentChatId)` call, then return.

Leave the first guard line (`if (!state.currentChatId || !content.trim() || state.isStreaming) return;`) EXACTLY as-is — those are legitimate no-send states, not races, and must not queue anything.

Wire the two resume points:
- End of `populateModelSelect()` (after the `if (loaded) / else if` block that assigns `state.selectedModel`): call `trySendPending()`.
- Inside `connectWs()`'s `ws.onopen`, after `state.reconnectAttempt = 0;` (and alongside the existing `loadChatStats` call): call `trySendPending()`.

Keep the diff minimal, `'use strict'`-safe, and consistent with surrounding style (4-space indent, plain function declarations, no arrow-function module scope, no new globals).
  </action>
  <verify>
    <automated>cd "$(git rev-parse --show-toplevel)" && { command -v node >/dev/null && node --check ui/static/app.js; } && grep -v '^\s*//' ui/static/app.js | grep -c 'pendingMessage' | awk '$1>=5{exit 0} {exit 1}' && grep -v '^\s*//' ui/static/app.js | grep -c 'trySendPending' | awk '$1>=4{exit 0} {exit 1}' && grep -q "if (!state.currentChatId || !content.trim() || state.isStreaming) return;" ui/static/app.js && echo OK</automated>
    <human-check>Open the app, click "New Chat", and immediately type a message + Enter before the model dropdown populates. An info toast appears saying the message will be sent automatically, and the message sends on its own once the model/WS are ready — nothing is lost.</human-check>
  </verify>
  <done>`state.pendingMessage` exists; `trySendPending()` gates on all five conditions and clears the field before dispatch; both race branches queue instead of dropping and toast an "will be sent automatically" message; `populateModelSelect()` and `ws.onopen` both call `trySendPending()`; the three original early-return guards are byte-identical to before.</done>
</task>

<task type="auto">
  <name>Task 2: Render the user's own message bubble optimistically on send</name>
  <files>ui/static/app.js</files>
  <action>
Add `appendUserBubble(content)` next to `appendLoadingBubble()` (around line 886). Build it with `document.createElement` in the same style as `renderMessages()`'s user branch:
- wrapper `div` with `className = 'flex justify-end'`
- bubble `div` with `className = 'max-w-[75%] rounded-xl px-4 py-2 text-sm bg-indigo-600 text-white'`
- content `div` with `className = 'message-content prose prose-invert prose-sm max-w-none'` and `innerHTML = DOMPurify.sanitize(content)` (sanitize is mandatory per CLAUDE.md — never raw innerHTML of user input)
- append content → bubble → wrapper → `$('messages')`, then `container.scrollTop = container.scrollHeight`

Do NOT add a token-count line (token_count is unknown client-side at send time) and do NOT call `branchControlsHtml()` (the message has no persisted id yet — branch buttons would carry a bogus `data-branch-from` and be clickable).

In `sendMessage()`, call `appendUserBubble(trimmed)` immediately before the existing `appendLoadingBubble()` call, so the user bubble sits above the typing indicator.

Add no cleanup or removal logic: `renderMessages()` clears the container with `container.innerHTML = ''` and rebuilds from server data when `loadChatTree()` runs on the WS `'done'` event, so the optimistic bubble is naturally replaced by the authoritative one carrying its token count and branch controls.
  </action>
  <verify>
    <automated>cd "$(git rev-parse --show-toplevel)" && { command -v node >/dev/null && node --check ui/static/app.js; } && grep -q 'function appendUserBubble(content)' ui/static/app.js && grep -q 'appendUserBubble(trimmed);' ui/static/app.js && grep -A12 'function appendUserBubble(content)' ui/static/app.js | grep -q 'DOMPurify.sanitize(content)' && ! grep -A16 'function appendUserBubble(content)' ui/static/app.js | grep -q 'branchControlsHtml\|token_count' && echo OK</automated>
    <human-check>Send a message in a chat: the indigo user bubble appears instantly on the right, the typing dots appear below it, and when the reply finishes the bubble is replaced by the persisted one (token count + "↩ отсюда" control) with no duplicate.</human-check>
  </verify>
  <done>`appendUserBubble(content)` exists, sanitizes via DOMPurify, renders the same indigo user markup as `renderMessages()` without token line or branch controls, scrolls into view, and is called from `sendMessage()` directly before `appendLoadingBubble()`; no removal logic was added.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| user input → DOM | Typed message text is injected into `#messages` before any server round-trip |
| browser → Agent WS | Queued message is replayed onto the socket from a deferred code path |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-QUICK-01 | Tampering | `appendUserBubble()` innerHTML | mitigate | `DOMPurify.sanitize(content)` before assignment — same call `renderMessages()` already uses for user messages; asserted by the Task 2 grep gate |
| T-QUICK-02 | Elevation of Privilege | `trySendPending()` replay after chat switch | mitigate | Guard requires `state.currentChatId` truthy and the socket OPEN at replay time; `selectChat()` calls `disconnectWs(false)` so a stale socket cannot satisfy the guard, and the pending text is cleared before dispatch so it fires at most once |
| T-QUICK-03 | Denial of Service | duplicate / runaway sends | accept | `state.pendingMessage` is a single slot cleared before `sendMessage()`, and `state.isStreaming` blocks re-entry; a queued message can be superseded but never fan out |
| T-QUICK-SC | Tampering | package installs | accept | No package-manager installs — vanilla JS, CDN-only frontend per CLAUDE.md hard constraint |
</threat_model>

<verification>
- `node --check ui/static/app.js` passes (skipped gracefully if node is absent on the box).
- No backend files, HTML, CSS, or test files changed: `git diff --name-only` lists only `ui/static/app.js`.
- No pytest tests added — this fix was found via ad-hoc Playwright exploration outside the repo suite. Existing suite must still pass untouched: `pytest tests/ -v`.
- Manual browser pass covering both human-check blocks above.
</verification>

<success_criteria>
- Submitting a message before the model list or WebSocket resolves queues it and auto-sends it once ready; nothing is silently discarded.
- Both race toasts use `'info'` type and say the message will be sent automatically.
- The three original early-return guards in `sendMessage()` are unchanged.
- The user's own bubble renders immediately on send and is replaced (not duplicated) by the server-rendered one on `'done'`.
- `ui/static/app.js` is the only file modified.
</success_criteria>

<output>
Create `.planning/quick/260921-juu-fix-silent-message-drop-race-model-ws-no/260921-juu-SUMMARY.md` when done.
</output>
