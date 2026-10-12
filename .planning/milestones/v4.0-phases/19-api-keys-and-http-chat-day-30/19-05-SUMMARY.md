---
phase: 19-api-keys-and-http-chat-day-30
plan: 05
subsystem: e2e
tags: [e2e, playwright, api-keys, http-chat]
requires: [19-01, 19-03, 19-04]
provides:
  - "scripts/e2e_api_keys_playwright.py: isolated-copy browser and HTTP end-to-end check"
key-files:
  created: [scripts/e2e_api_keys_playwright.py]
requirements-completed: [KEY-01, KEY-03, HCHAT-01, HCHAT-02]
e2e_status: passed
completed: 2026-10-12
---

# Phase 19 Plan 05: API keys and HTTP chat e2e Summary

`scripts/e2e_api_keys_playwright.py` starts an isolated copy (ports 18000/18001, scratch DB, stub provider), creates an API key through the real Settings UI, holds a cookie-less HTTP conversation with it, watches the exchange appear live in the open browser, revokes the key and confirms 401. It reuses the `prov` helpers from `e2e_llm_providers_playwright.py`.

## Commits

| Task | Commit |
|------|--------|
| 1 script (K1-K6, H1-H5, plus L/K7/R/Z scenarios) | 91655e5 |
| 2 K7 race fix after the first run | e03408e |

## E2E run

Command: `python scripts/e2e_api_keys_playwright.py`

Status: **PASSED (exit 0)**

Run history (both observed): the first run exited 1 with a single failure, K7; after the script fix (below) the second run exited 0.

| Label | Result |
|-------|--------|
| K1 section heading, `Authorization: Bearer` explainer, empty state | PASS |
| K2 empty name error, no request | PASS |
| K3 modal: `aia_` key of 47 chars, curl example with Agent address, warning | PASS |
| K4 copy toasts | PASS |
| K5 Escape/backdrop ignored, x closes, no trace of key in page | PASS |
| K6 row: name, prefix, `использован: никогда` | PASS |
| K7 reopened Settings shows a last-used date | PASS |
| H1 key-only `auth/me`, `kb`, `mcp/servers` | PASS |
| H2 `POST /api/v1/api-keys` with a key -> 403 | PASS |
| H3 key creates chat, one JSON reply with stats | PASS |
| H4 tree returns two messages | PASS |
| H5 wrong key -> 401 | PASS |
| L1 HTTP-created chat appears in the open browser without reload | PASS |
| L2 second HTTP turn appears live as four messages, same classes as typed ones, no API badge | PASS |
| L3 input and send enabled after the HTTP turn | PASS |
| R1 dismissed confirm keeps the row, accepted confirm revokes | PASS |
| R2 revoked key -> 401 on `GET chats` and `POST messages` | PASS |
| Z no uncaught page errors | PASS |

The script's own teardown ran; no listener remained on 18000/18001/18766/18767 afterwards (only TIME_WAIT sockets). `git status` shows no change to `.env` or `app.db` (neither exists in the worktree; the copy uses a scratch DB and its own `.env`). Scratch artifacts (results.json, app.log, screenshots) are under the temp directory printed by the run.

## Open manual UAT

None - e2e passed

## Verification (observed)

- `python -m pytest tests/test_port_literals.py tests/test_api_keys_ui.py tests/test_http_chat.py -q` -> 35 passed.
- `python -m py_compile scripts/e2e_api_keys_playwright.py` exit 0; the 8000/8001-literal regex check printed `ok`.

## Deviations from Plan

**1. [Rule 1 - Script bug] K7 race.** First run failed K7: reopening Settings briefly showed the row rendered before the HTTP turns (`никогда`) until the reload finished. The script now waits for the refreshed text instead of for the row's existence. No product defect; the second run passed K7 with a real date.

**2. [Minor] Stub provider registered over HTTP, not via the UI.** The script logs in with a cookie client and posts the stub provider to `/api/v1/llm-providers` before the browser logs in (same endpoint the UI uses), so the model picker already has `Stub · stub-model`.

**3. [Minor] H3 HTTP timeout is 60 s** (plan: step timeout) to tolerate auxiliary LLM calls; the stub answered immediately.

Worktree base was reset to 178f49a at start as instructed. No product files were changed.

## Known Stubs

None.

## Self-Check: PASSED

scripts/e2e_api_keys_playwright.py exists; commits 91655e5 and e03408e exist; STATE.md and ROADMAP.md untouched.
