---
phase: 23-help-command-verification-and-report-day-31
verified: 2026-10-13T00:00:00Z
status: passed
score: 5/5 must-haves verified
has_blocking_gaps: false
overrides_applied: 0
---

# Phase 23: /help command, verification and report (Day 31) Verification Report

**Phase Goal:** A developer asks a question about the project with `/help` and gets an answer built from the project documentation and the live git state, and a recorded run on a real model proves it.
**Status:** passed
**Re-verification:** No (initial)

## Observable Truths

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---|---|---|
| 1 | User marks a KB as project docs base; `/help <q>` answers from it with sources without touching chat RAG settings | VERIFIED | `agent/help_api.py` (GET/PUT `/api/v1/help/config`, router included in `agent/main.py:88,478`); `agent/help_command.py` `prepare_help_turn`; test `test_prepare_ignores_chat_rag_config`; e2e Run 2 PASS (base marked, badge, label, sources). |
| 2 | `/help` alone shows usage; `/help <q>` with no or unready base explains; neither calls the model | VERIFIED | `resolve_help_notice` (help_command.py:122-139) returns a notice before any LLM path; called in WS (`ws.py:1466-1474`) and HTTP (`http_chat.py:203-208`); notices not stored (`stored=False` in e2e). Tests in test_help_ws / test_help_http pass. |
| 3 | During `/help` the model can call git tools, visible in chat; exchange survives reload | VERIFIED | `build_help_tools` + `_run_help_turn` (ws.py:1009-1076); e2e card `local-git . git_current_branch` shown; after reload two exchanges, labels and sources restored. Note: tool cards are not restored after reload (live-only for all chats, documented in report and CHECKS). Exchange itself persists, so SC met. |
| 4 | Same `/help` message via HTTP endpoint with API key returns answer, sources, tool calls in one JSON body | VERIFIED | `agent/http_chat.py` (help block, `rag.sources`, `tool_calls`); e2e `http: git answer tool_calls=['mcp__local-git__git_current_branch']`; control run used HTTP for all 20 answers. |
| 5 | `Day31_report.md` has 8-10 control questions via `/help` on a real model; pytest, e2e script and the three docs cover the milestone | VERIFIED | Report has 10 frozen questions, 2 real models (qwen3.5-9b, deepseek-flash), answers, sources, tool calls, verdicts, failures kept (5/10 and 6/10 correct). `eval_out/day31/` and `tests/fixtures/help/control_set.json` exist. 3150 passed / 13 skipped recorded in 23-CHECKS.md; e2e Run 2 PASS (Run 1 failure honestly recorded). Docs sections present in API_SPEC (L1162), ARCHITECTURE (L763), USER_GUIDE (L461), TESTING_GUIDE (L649). |

**Score:** 5/5

## Spot-checks run by verifier

| Check | Result |
|---|---|
| `pytest tests/test_help_*.py tests/test_docs_help.py` (8 files) | 187 passed |
| Debt markers (TBD/FIXME/XXX) in phase code and scripts | none |
| Files exist: help_api.py, help_command.py, e2e_help_playwright.py, help_eval.py, 8 test files, Day31_report.md | all present, substantive (395 / 194 lines for core modules) |

## Requirements Coverage

| Requirement | Status | Evidence |
|---|---|---|
| HELP-01 mark/clear docs base | SATISFIED | help_api config routes, Settings UI, e2e "base marked", badge |
| HELP-02 `/help q` answers from base with sources, chat RAG untouched | SATISFIED | prepare_help_turn, test_prepare_ignores_chat_rag_config, e2e |
| HELP-03 git tools in /help turn, visible | SATISFIED | build_help_tools, e2e card, control run tool calls |
| HELP-04 usage text without model | SATISFIED | resolve_help_notice, e2e usage notice, HTTP usage body |
| HELP-05 no/unready base instruction, no model call | SATISFIED | resolve_help_notice NOT_READY / NO_BASE texts, tests |
| HELP-06 HTTP parity | SATISFIED | http_chat.py, e2e http checks |
| HELP-07 stored in tree, survives reload with sources | SATISFIED | e2e reload checks (exchanges, labels, sources) |
| VER-07 pytest + TESTING_GUIDE | SATISFIED | 8 test files, TESTING_GUIDE section; full suite 3150 passed (recorded) |
| VER-08 isolated-copy e2e | SATISFIED | scripts/e2e_help_playwright.py, Run 2 PASS on 18000/18001 |
| VER-09 three docs updated | SATISFIED | sections in API_SPEC, ARCHITECTURE, USER_GUIDE (upload, git server, github-mcp-server, /help) |
| VER-10 report with 8-10 questions incl. failures | SATISFIED | Day31_report.md, 10 questions, failures listed |

All 11 IDs are marked Complete in REQUIREMENTS.md and map to Phase 23; no orphaned requirements.

## Anti-Patterns / Code Review (advisory, not gaps)

- WR-01: `/help` notices (usage / no base) resolve DB reads before the per-chat rate limit (`ws.py:1463-1480`, `http_chat.py:203-208`); they can be spammed unthrottled. Does not break the goal.
- WR-02: In `_run_help_turn` the user message is persisted before settings/tool setup; a setup exception (e.g. `mcp_config.list_servers`) leaves an orphan leaf with no reply.
- WR-03: Concurrent first PUT `/help/config` can raise IntegrityError and return 500.
- Info: IN-01 bool accepted as server id; IN-02 duplicated no-base logic.

## Human Verification

None required: the real-model run and browser e2e are recorded with output. Residual caveats (stated honestly in the report, not blockers): tool cards not restored after reload; Phase 21 live add/replace UAT still pending (belongs to Phase 21); judge and DeepSeek answerer share a provider; single run per model.

## Gaps Summary

No gaps. The goal is achieved: `/help` is implemented on WS and HTTP, tested, exercised end to end in a browser, and documented, and a recorded 10-question run on two real models is in `Day31_report.md` with failures included.

_Verified: 2026-10-13_
_Verifier: Claude (bm-verifier)_
