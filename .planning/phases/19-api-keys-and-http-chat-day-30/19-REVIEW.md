---
phase: 19-api-keys-and-http-chat-day-30
reviewed: 2026-10-12T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - agent/api_keys_api.py
  - agent/dependencies.py
  - agent/http_chat.py
  - agent/main.py
  - agent/rag_api.py
  - agent/schemas.py
  - agent/ws.py
  - scripts/e2e_api_keys_playwright.py
  - shared/auth.py
  - shared/database.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
  - tests/test_api_keys.py
  - tests/test_bearer_auth.py
  - tests/test_http_chat.py
  - tests/test_http_chat_rag.py
  - tests/test_ws_turn_failures.py
findings:
  critical: 1
  warning: 4
  info: 2
  total: 7
status: issues_found
---

# Phase 19: Code Review Report

**Reviewed:** 2026-10-12
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

Key hashing (sha256 over 256-bit random secret, one-time display, `Cache-Control: no-store`, hash-only storage, per-user 404 on foreign keys) is sound. HTTP chat lock handling is correct: `locked()` check and `acquire()` have no await between them, the turn runs in a shielded task that releases the lock in `finally`. The ws.py refactor looks behavior-preserving. The main problem is the Bearer/cookie seam: it is a deny-list, so an API key reaches almost the entire API.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: API key grants full account access, including host command execution via MCP servers

**File:** `agent/dependencies.py:116-120,148-155`; `agent/main.py:1149-1170` (and 1178, 1235, 1281)
**Issue:** `get_current_user` accepts a Bearer key, and only `logout`, `create_user` and the api-keys routes use `get_session_user`. All other routes (about 100 `Depends(get_current_user)` across main.py, providers_api, scheduler_api, kb_api, task_memory_api, rag_api) are reachable with a key whose stated purpose is HTTP chat. `POST /api/v1/mcp/servers` accepts an arbitrary `command`/`args`/`env`/`cwd` that the agent later spawns, so a leaked key (curl scripts, CI logs) gives remote code execution as the agent user. The same key can also repoint or delete providers (including their key env names), delete chats, and schedule jobs. Default-allow means every new endpoint is silently exposed.
**Fix:** Invert the seam: make `get_current_user` session-only and add an explicit `get_api_or_session_user` used only on the endpoints a key needs (`POST /chats/{id}/messages`, `GET /chats`, `POST /chats`, `GET /chats/{id}/tree`, RAG GET, etc.). At minimum, switch MCP server, provider, scheduler, settings, profile and delete routes to `get_session_user` now.

## Warnings

### WR-01: HTTP chat endpoint bypasses the per-chat rate limit

**File:** `agent/http_chat.py:165-196` (vs `agent/ws.py:163-173`)
**Issue:** The WebSocket path enforces `_check_rate_limit` (10/min/chat); the HTTP path never calls it. A key holder can run unbounded paid LLM turns, limited only by the one-at-a-time chat lock, and could create many chats to parallelise.
**Fix:** Call `ws._check_rate_limit(chat_id)` before taking the lock and return 429 with the uniform error body when it fails.

### WR-02: Failed turn after the user message is persisted leaves an orphan message and a misleading flag

**File:** `agent/http_chat.py:135-153`
**Issue:** If `_run_chat_turn` raises (e.g. the assistant persist or stats fails) or `_build_success` returns None, the user message and advanced `current_leaf_message_id` stay in the DB (only the explicit error paths call `_discard_user_message`). The response reports `user_message_kept: None`, so a client that retries creates duplicate user messages and an assistant-less branch.
**Fix:** On the exception path, check whether the chat leaf is a user message created by this turn and discard it (or report `user_message_kept: True` accurately by inspecting the DB).

### WR-03: Deleting a chat during an HTTP turn breaks the per-chat lock invariant

**File:** `agent/http_chat.py:184-187`; `agent/state.py:26-30`
**Issue:** `cleanup_chat_caches` pops `chat_locks[chat_id]` while a turn holds it. The turn then continues on an orphaned lock object and the in-flight turn writes to a deleted chat (FK errors, 500). The code also passes the lock object into the task, so release is safe, but a concurrent request can create a fresh lock and run in parallel.
**Fix:** Reject chat deletion while `chat_locks[chat_id].locked()` (409), or have the turn tolerate chat deletion explicitly.

### WR-04: API key limit check is not atomic

**File:** `agent/api_keys_api.py:37-41`
**Issue:** Count-then-insert has a TOCTOU window; concurrent POSTs can exceed `API_KEY_MAX_PER_USER`. Low impact (single user, SQLite), but the limit is advertised as enforced.
**Fix:** Do the count and insert in one write transaction (`BEGIN IMMEDIATE`) or re-count after insert and roll back when over the limit.

## Info

### IN-01: http_chat reaches into private helpers of other modules

**File:** `agent/http_chat.py:10,15` (`ws._run_chat_turn`, `rag_api._get_owned_chat`)
**Issue:** Underscore-prefixed functions used across modules; headless.py does the same, so it is a known pattern, but it couples the HTTP API to ws internals.
**Fix:** Rename to public names (`run_chat_turn`, `get_owned_chat`) or move to a shared module. Convention-level only.

### IN-02: Created key timestamps serialize as naive datetimes

**File:** `agent/api_keys_api.py:60-67`, `agent/schemas.py` (`ApiKeyOut`)
**Issue:** After `refresh`, SQLite returns naive `created_at`/`last_used_at`, so the JSON has no timezone offset and the UI parses it as local time.
**Fix:** Normalize with `_as_aware_utc` (or a serializer that appends UTC) before returning.

---

_Reviewed: 2026-10-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
