---
phase: 25-support-assistant-day-33
reviewed: 2026-10-17T00:00:00Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - agent/command_turn.py
  - agent/help_command.py
  - agent/http_chat.py
  - agent/main.py
  - agent/schemas.py
  - agent/support_api.py
  - agent/support_command.py
  - agent/support_lookup.py
  - agent/tools.py
  - agent/ws.py
  - mcp_servers/support_data.json
  - mcp_servers/support_desk.py
  - scripts/e2e_support_playwright.py
  - scripts/support_probe.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 25: Code Review Report

**Reviewed:** 2026-10-17
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

No blockers. user_id scoping is sound: SupportConfig is keyed by user_id, MCP server ids in PUT are validated against the caller's own servers, the /help KB ownership is re-checked, and the direct lookup uses `chat.user_id`. The frontend renders all new support data through `textContent` (`mcpEl`), with no new `innerHTML` sinks. The prompt-injection fence is reasonable but covers only the direct-lookup path.

## Structural Findings (fallow)

None provided.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Fence applies only to the direct lookup; model-invoked support tool results are unfenced

**File:** `agent/support_lookup.py:57-62` (and `agent/command_turn.py:229-255`)
**Issue:** `fence_support_data` wraps only the in-code `get_ticket`/`get_user` lookup. When the model calls `search_tickets`, `list_user_tickets` or `get_ticket` itself through the MCP toolset, the raw ticket text (customer-authored messages, subjects, error text) goes into tool messages with no `<support_data>` fence or preamble. SUPPORT_SYSTEM_PROMPT says that only `<support_data>` content is data, so the tool path is the weaker injection surface.
**Fix:** Fence MCP tool results from the support server before they are appended to the tool message, or state in the system prompt that all tool results are untrusted data.

### WR-02: Fence neutralization is limited to an ASCII, case-insensitive `<support_data` match

**File:** `agent/support_lookup.py:43,59`
**Issue:** Only `<support_data` and `</support_data` are rewritten. Variants such as `< /support_data>` (whitespace) or full-width/homoglyph brackets are not covered. Rewriting only the tag prefix is also easy to bypass with whitespace between `<` and `/`. Customer text is attacker-controlled by design, so the fence can be broken early.
**Fix:** Use `re.compile(r"<\s*(/?)\s*support_data", re.I)`, and escape every `<` in the data (for example `&lt;`) instead of matching the tag name.

### WR-03: Retrieval hint can cut off the user's question; ticket text steers retrieval

**File:** `agent/support_lookup.py:65-76`
**Issue:** `" ".join(parts)[:300]` truncates the question itself when it is longer than 300 chars and drops the ticket hints entirely. Subject/Error lines are customer-controlled and are appended to the retrieval query without sanitization. This is low impact (retrieval only), but it is undocumented behavior.
**Fix:** Cap each hint separately and keep the question whole, for example `question + " " + hints[:RETRIEVAL_HINT_MAX_CHARS]`.

### WR-04: First-write race on SupportConfig returns 500

**File:** `agent/support_api.py:171-187`
**Issue:** The frontend fires a PUT per control change. Two concurrent first PUTs for a user both see `row is None` and both insert, so one raises `IntegrityError` (a `SQLAlchemyError`). It is re-raised and surfaces as a 500, and the UI then rolls back state and shows an error toast.
**Fix:** Catch `IntegrityError`, roll back, reload the row and re-apply the fields. Alternatively, serialize the UI saves.

## Info

### IN-01: Duplicate "support server missing" warnings

**File:** `agent/command_turn.py:172-174`, `agent/ws.py` (`warnings = tools.warnings + lookup.warning`)
**Issue:** When no support server exists, the user gets both a `select_tools` warning ("Сервер support недоступен...") and the lookup's `REASON_NOT_ADDED` warning for the same cause.
**Fix:** Dedupe by `server` key when merging warnings.

### IN-02: Over-long import line and long docstring

**File:** `agent/ws.py` (import `from agent import command_turn, help_command, invariants, limits, support_command, support_lookup, tasks`), `_run_support_turn` docstring
**Issue:** Single-line docstring about 150 chars, and import line over 100 chars, against the CLAUDE.md "under 100 chars" guidance. `_run_support_turn` is also about 100 lines, well over the 50-line guideline.
**Fix:** Wrap the import in parentheses and shorten the docstring. Split the turn into helpers (setup, lookup, persist).

### IN-03: Minor grammar and robustness nits in the MCP server

**File:** `mcp_servers/support_desk.py:400`
**Issue:** `"Found 1 tickets"` is ungrammatical (`_plural` exists but is not used here). `_user_by_id` uses `next(...)` without a default, and is safe only because `validate_data` ran at load time.
**Fix:** Use `_plural(len(hits), 'ticket')`. Optionally give `next` a clear error default.

---

_Reviewed: 2026-10-17_
_Reviewer: Claude (bm-code-reviewer)_
_Depth: standard_
