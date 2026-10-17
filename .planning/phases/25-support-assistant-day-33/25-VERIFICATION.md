---
phase: 25-support-assistant-day-33
verified: 2026-10-17T00:00:00Z
status: human_needed
score: 14/14 must-haves verified (code level); live-model behavior pending human run
has_blocking_gaps: false
overrides_applied: 0
human_verification:
  - test: "Load a chat model in LM Studio (e.g. qwen/qwen3.5-9b) and re-run `python scripts/e2e_support_playwright.py`"
    expected: "The 7 lines recorded NOT RUN (T-1001 card+label, T-1002 card+label, reload label, follow-up, status change, http T-1001, http T-1002) become PASS"
    why_human: "Requires a loaded chat model and documentation base creation; not available when the e2e was recorded"
  - test: "In the browser run `/support #T-1001 Почему не работает авторизация?` and the same for #T-1002"
    expected: "mcp__support__get_ticket card appears, label «Поддержка · T-100x», answers differ (lockout cause vs Origin/LAN cause), label survives reload"
    why_human: "Model answer quality and visual rendering cannot be checked by grep (SUP-07/08/11 end-to-end)"
---

# Phase 25: Support Assistant (Day 33) Verification Report

**Phase Goal:** A mini support service: the assistant answers product questions from a FAQ/product-docs knowledge base (existing RAG) and takes user/ticket context from an own read-only stdio MCP server over a JSON file, so "Почему не работает авторизация?" is answered with that ticket's data.
**Status:** human_needed
**Re-verification:** No, initial verification

## Goal Achievement

All artifacts exist, are substantive and wired. The deterministic parts are covered by passing tests; the live-model part is honestly recorded as NOT RUN in 25-E2E.md and is routed to human verification.

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| SUP-01 | /support parsing WS + HTTP | VERIFIED | `agent/support_command.py`, `agent/ws.py::_run_support_turn` (l.1097), `agent/http_chat.py` hooks; tests `test_support_command/ws/http` |
| SUP-02 | Shared command-turn mechanism | VERIFIED | `agent/command_turn.py` exists; ws.py imports `command_turn`, `help_command`; `test_command_turn.py` + `test_help_command.py` pass |
| SUP-03 | SupportConfig + GET/PUT routes + Settings section | VERIFIED | `agent/support_api.py` router included at `agent/main.py:481`; `test_support_api.py`, `test_support_ui.py` pass |
| SUP-04 | KB marked for /help, notice when none | VERIFIED | ws.py l.1109-1115 NOTICE_NO_BASE path, no model call; tests pass |
| SUP-05 | Read-only stdio MCP with 4 tools, isError, 20000 cap | VERIFIED | `mcp_servers/support_desk.py` (`--data`, `APP_RESULT_CAP = 20000`); `test_mcp_support_desk.py` passes |
| SUP-06 | Re-read on change, invalid file handling | VERIFIED | mtime/size cache key (l.173-182); tests pass |
| SUP-07 | Seed JSON (4 users, 6 tickets) + Russian docs/FAQ.md | VERIFIED (code) | seed counts confirmed; `test_docs_support.py` passes; different-answer quality is a human item |
| SUP-08 | Direct in-code lookup, tool-call card, fenced block | VERIFIED (code) | `agent/support_lookup.py::direct_lookup`, `fence_support_data`; card rendering in real browser is human item |
| SUP-09 | Warning paths never refuse | VERIFIED | lookup warnings, `test_support_lookup.py` pass |
| SUP-10 | /support history rule | VERIFIED | `load_support_history`; tests pass |
| SUP-11 | `support` payload key persisted, HTTP body | VERIFIED (code) | `support_payload_extra`; reload survival in browser is human item |
| SUP-12 | pytest coverage | VERIFIED | 265 passed in the 12 phase-related test files (run by verifier); SUMMARY reports 3715 passed full suite |
| SUP-13 | Isolated e2e script and recorded run | VERIFIED (partial) | script exists with `test_e2e_support_script.py`; 25-E2E.md records 5 PASS and 7 NOT RUN honestly (exit 0) |
| SUP-14 | Docs as built with docs guard | VERIFIED | `docs/API_SPEC.md` mentions support; `test_docs_support.py` passes |

**Score:** 14/14 requirements satisfied at code level. Requirement IDs in PLAN frontmatter (SUP-01..14) match REQUIREMENTS.md exactly; no orphaned IDs.

### Anti-Patterns
No TBD/FIXME/XXX markers in the new modules. Code review (25-REVIEW.md): 0 critical, 4 advisory warnings, none blocking:
- WR-01: model-invoked support tool results are not fenced (injection surface weaker than the direct path).
- WR-02: fence neutralisation matches only ASCII `<support_data` prefix (whitespace variants bypass).
- WR-03: retrieval hint truncation at 300 chars can cut the question.
- WR-04: first-write race on SupportConfig PUT can return 500.

These are minor hardening items and do not break the phase goal; recommend backlog.

### Human Verification Required

1. **Live-model e2e re-run.** Load a chat model in LM Studio, run `python scripts/e2e_support_playwright.py`. Expected: the 7 NOT RUN lines become PASS. Why human: needs a loaded chat model.
2. **Manual `/support #T-1001` vs `#T-1002`.** Expected: tool card, label «Поддержка · T-100x», correct differing causes, label persists after reload. Why human: model output and visual rendering.

### Gaps Summary

No gaps. Deterministic behavior is verified by code inspection and passing tests; remaining uncertainty is solely the live-model end-to-end behavior (`unverifiable_runtime`).

_Verifier: Claude (bm-verifier)_
