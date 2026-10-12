---
phase: 19-api-keys-and-http-chat-day-30
verified: 2026-10-12T00:00:00Z
status: passed
score: 5/5 roadmap success criteria verified
has_blocking_gaps: false
overrides_applied: 0
---

# Phase 19: API keys and HTTP chat (Day 30) Verification Report

**Phase Goal:** A user can create API keys and use them to chat with the model over plain HTTP, with the conversation stored in the normal chat tree.
**Status:** passed. **Re-verification:** No.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Create a named key in Settings, full key shown once, list shows name/prefix/dates only | VERIFIED | `agent/api_keys_api.py` (POST/GET/DELETE), hash-only storage, UI section in `ui/static/app.js` and `index.html`. `tests/test_api_keys.py` passes. 19-05 e2e exit 0. |
| 2 | Revoke gives 401 immediately; invalid key 401 | VERIFIED | `agent/dependencies.py` Bearer branch (401 with `WWW-Authenticate: Bearer`, no cookie fallback). `tests/test_bearer_auth.py` passes. e2e revoke step. |
| 3 | Bearer calls REST routes as the key owner; WebSocket stays cookie-only | VERIFIED | `get_current_user` accepts a key; `get_session_user` is used for the cookie-only routes (logout, create user, api-keys). `test_bearer_auth.py` passes. See WR-A below for the security caveat. |
| 4 | POST text+model to a chat returns the whole reply as one JSON body with stats; exchange is stored in the tree and uses the same turn as WS | VERIFIED | `agent/http_chat.py` calls the shared `ws._run_chat_turn`. `tests/test_http_chat.py` and `test_http_chat_rag.py` pass (kb_id semantics). 19-05 e2e shows the browser live refresh through `chat_updated`. |
| 5 | CONTEXT_OVERFLOW (413), LM Studio down (503), chat busy (409) return clear statuses; API_SPEC documents a full curl scenario | VERIFIED | `http_chat.py` status mapping at lines 67-79 and 186. `docs/API_SPEC.md` has the endpoint section, status table and curl scenario (about lines 771-960). `tests/test_ws_turn_failures.py` passes. |

**Score:** 5/5

Targeted test run (my own): `pytest tests/test_api_keys.py tests/test_bearer_auth.py tests/test_http_chat.py tests/test_http_chat_rag.py tests/test_ws_turn_failures.py` gave 66 passed. The known supervisor test was not run and is unrelated.

## Requirements Coverage

| Req | Status | Evidence |
|-----|--------|----------|
| KEY-01 | SATISFIED | Create route and UI one-time modal |
| KEY-02 | SATISFIED | Hash-only storage, list shows prefix and dates |
| KEY-03 | SATISFIED | Revoke deletes the row, next request 401 |
| KEY-04 | SATISFIED (narrowed by D-02) | REQUIREMENTS.md wording already updated; the cookie-only 403 routes are implemented and tested |
| KEY-05 | SATISFIED | 401 on invalid or revoked key |
| HCHAT-01 | SATISFIED | `POST /api/v1/chats/{chat_id}/messages` |
| HCHAT-02 | SATISFIED | Shared `_run_chat_turn`, tree storage, `chat_updated` event |
| HCHAT-03 | SATISFIED | 413/503/409/502 mapping |
| HCHAT-04 | SATISFIED | `docs/API_SPEC.md` curl scenario |

All 9 IDs appear in the PLAN frontmatter. There are no orphaned requirements.

## Anti-Patterns

No TBD/FIXME/XXX in the key phase files (`agent/http_chat.py`, `agent/api_keys_api.py`, `shared/auth.py`).

## Warnings (non-blocking, from 19-REVIEW.md, confirmed against code)

- **WR-A (CR-01):** Default-allow Bearer auth lets a key reach about 100 routes, including MCP server creation, which spawns arbitrary commands. This follows D-02 and is advisory, but it is a real security exposure for leaked keys. Consider an allowlist in a later phase.
- **WR-01:** The HTTP chat path skips the per-chat rate limit. This is scheduled for Phase 20 (LIM-01).
- **WR-02:** A raised exception after the user message is persisted can leave an orphan user message, and `user_message_kept` is reported as None.
- **WR-03:** Deleting a chat during an HTTP turn can orphan the lock.
- **WR-04:** The 20-key cap check is not atomic (low impact).
- **Tracking:** REQUIREMENTS.md still shows KEY-*/HCHAT-* as `[ ]` and "Pending". Tick them and set Complete.
- **Stray file:** an untracked `scratch_api.md` is in the repo root. Delete it or ignore it.

## Human Verification

None required. The live browser refresh part was covered by the 19-05 e2e run (exit 0) on an isolated copy.

## Gaps Summary

No blocking gaps. The phase goal is achieved.

_Verifier: Claude (gsd-verifier)_
