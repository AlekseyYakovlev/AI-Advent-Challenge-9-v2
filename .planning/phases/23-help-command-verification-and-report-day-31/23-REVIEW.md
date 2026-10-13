---
phase: 23-help-command-verification-and-report-day-31
reviewed: 2026-10-13T00:00:00Z
depth: standard
files_reviewed: 21
files_reviewed_list:
  - agent/help_api.py
  - agent/help_command.py
  - agent/http_chat.py
  - agent/main.py
  - agent/mcp_tools.py
  - agent/rag.py
  - agent/schemas.py
  - agent/ws.py
  - scripts/e2e_help_playwright.py
  - scripts/help_eval.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
  - tests/test_help_command.py
  - tests/test_help_api.py
  - tests/test_help_turn.py
  - tests/test_help_ws.py
  - tests/test_help_http.py
  - tests/test_help_ui.py
  - tests/test_help_eval.py
  - tests/test_docs_help.py
findings:
  critical: 0
  warning: 3
  info: 2
  total: 5
status: issues_found
---

# Phase 23: Code Review Report

**Reviewed:** 2026-10-13
**Depth:** standard
**Files Reviewed:** 21
**Status:** issues_found

## Summary

The /help feature is well structured. Built-in tools are enforced server-side through `allowed_tools` (`agent/tools.py:139`). MCP exposure is limited to the chosen servers. The frontend renders notices with `textContent`, and the scripts call git through list argv with no shell. I found no security vulnerabilities. The findings below are robustness issues. The test files (tests/test_help_*.py, tests/test_docs_help.py) were not reviewed in depth, so the review does not vouch for their reliability.

## Warnings

### WR-01: /help notices bypass the per-chat rate limit and run DB queries first (WS and HTTP)

**File:** `agent/ws.py:1463-1480` (ws_chat help branch), `agent/http_chat.py:203-208`
**Issue:** `resolve_help_notice` runs one to three DB reads (config, kb, kb again) before `check_message_rate`. This happens in the WS loop and in the HTTP handler, which also resolves the notice before the busy-lock and rate checks. An authenticated client (or API-key holder) can spam `/help` with an empty question or with no base marked. Each message costs a DB round trip and is never throttled. For a non-empty question it also bypasses the 409 busy check, which is harmless. The rate-limit slot is taken only after the notice resolves.
**Fix:** Call `_reject_rate_limited` (WS) and `limits.check_message_rate` (HTTP) before `resolve_help_notice`. If notices must not consume the turn budget, add a separate cheap limiter for them.

### WR-02: A user message is orphaned when /help setup fails after persisting it

**File:** `agent/ws.py` (`_run_help_turn`, lines about 406-426 of the diff)
**Issue:** `_persist_user_message` runs before `get_effective_settings`, `build_help_tools` and `prepare_help_turn`. `build_help_tools` calls `mcp_config.list_servers`, which is a DB call and is not wrapped in try/except. If any step between the persist and `_run_turn_middle` raises, the user message stays in the tree as a leaf with no reply. `_discard_user_message` is called only on LLM failure inside the middle step. `prepare_help_turn` is the only step that guards itself.
**Fix:** Wrap the setup in `try/except Exception`. On failure call `await _discard_user_message(session, chat_id, user_msg_id, user_parent_id)`, send an error frame and return. Alternatively, build the tools and settings before persisting the user message.

### WR-03: A concurrent first PUT of /help/config can return 500

**File:** `agent/help_api.py:174-190`
**Issue:** The `session.get` then `session.add(HelpConfig(...))` sequence is a check-then-insert on the primary key `user_id`. Two simultaneous first-time saves (for example a double-click or two tabs) both see no row. The second commit raises `IntegrityError`, which is a `SQLAlchemyError`. The handler rolls back and re-raises, so the client gets an unhandled 500 instead of a retry or a merged result.
**Fix:** On `IntegrityError`, roll back, reload the row, reapply the fields and commit again. You could also use `session.merge`, or catch the error and return 409.

## Info

### IN-01: `stored_servers` and `help_api` accept `bool` as a server id

**File:** `agent/help_command.py:158`
**Issue:** `isinstance(item.get("id"), int)` is also true for `True` and `False`. The JSON column is written only by this code, so the impact is negligible. Hand-edited data of `{"id": true}` would be treated as id 1.
**Fix:** Use `type(x) is int`, or add `and not isinstance(x, bool)`.

### IN-02: The no-base and not-ready notice logic is duplicated

**File:** `agent/ws.py` (`_run_help_turn` no-base branch), `agent/help_command.py:122-139`
**Issue:** `_run_help_turn` re-checks the base and sends a generic `HELP_NO_BASE_TEXT`. `resolve_help_notice` already produced the specific not-ready text for the same condition. If the base changes between the two checks, the user gets a less specific message. The duplicate check is also an extra DB read.
**Fix:** Pass the already-loaded config and kb from the first check into `_run_help_turn`. Alternatively, have `_run_help_turn` call `resolve_help_notice` and reuse its frame.

---

_Reviewed: 2026-10-13_
_Reviewer: Claude (bm-code-reviewer)_
_Depth: standard_
