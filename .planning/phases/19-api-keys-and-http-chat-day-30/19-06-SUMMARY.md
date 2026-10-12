---
phase: 19-api-keys-and-http-chat-day-30
plan: 06
subsystem: docs
tags: [docs, api-spec, curl, testing-guide, key-04]
requires: [19-01, 19-02, 19-03, 19-04, 19-05]
provides:
  - "API_SPEC sections 'API keys (Day 30)' and 'HTTP chat (Day 30)' with a full curl scenario"
  - "TESTING_GUIDE, ARCHITECTURE and USER_GUIDE sections for Day 30"
  - "KEY-04 reworded with the cookie-only exceptions"
  - "tests/test_docs_day30.py documentation source guard"
key-files:
  created: [tests/test_docs_day30.py]
  modified: [docs/API_SPEC.md, docs/TESTING_GUIDE.md, docs/ARCHITECTURE.md, docs/USER_GUIDE.md, .planning/REQUIREMENTS.md]
requirements-completed: [HCHAT-04, KEY-04]
completed: 2026-10-12
---

# Phase 19 Plan 06: Day 30 documentation Summary

The key routes, the Bearer rules, the plain-HTTP chat endpoint, its status table and an eleven-step curl scenario are documented from the source as built, with guide sections for testing, architecture and users and a pytest guard that keeps the documented routes real.

## Commits

| Task | Commit |
|------|--------|
| 1 API_SPEC sections, WebSocket amendments, tests/test_docs_day30.py | b416fea |
| 2 TESTING_GUIDE, ARCHITECTURE, USER_GUIDE, KEY-04, guard tests | 17e285a |

## What was documented

- API_SPEC: Bearer rules (header decides, no cookie fallback, 401 body with `WWW-Authenticate`), the five cookie-only routes (checked against the `get_session_user` call sites in `agent/main.py` and `agent/api_keys_api.py`), key routes with bodies, storage and `last_used_at` semantics, the endpoint request/response, behaviour rules, the status table (200, 401, 403, 404, 409, 413, 415, 422, 500, 502, 503), the `chat_updated` event, the curl scenario. Existing sections amended: route overview, `/ws/events` frame list, `LLM_ERROR` `cause` / `provider_kind`, leaf restore after a failed turn.
- Scenario field names match the source models: `LoginRequest` (`username`, `password`), `ModelLoadRequest` (`model_id`, `gpu_offload`), `McpServerUpdate` (`enabled`), `ChatCreate` (`title`).
- TESTING_GUIDE: a section per Day 30 test file and the e2e script (labels K1-K7, H1-H5, L1-L3, R1-R2, Z; exit 2 / 4 means BLOCKED). 19-05 reports `e2e_status: passed`, so no manual UAT item is opened.
- ARCHITECTURE and USER_GUIDE sections; UI strings «API ключи», «+ Создать ключ», «Ключ создан», «Отозвать» match `ui/static/index.html` / `app.js`.
- REQUIREMENTS.md: only the KEY-04 line changed (checkbox state kept).

## Verification (observed)

- `python -m pytest tests/test_docs_day30.py -q` -> 25 passed (22 after Task 1, 25 after Task 2).
- KEY-04 shell-neutral check printed `ok`; `git diff` of REQUIREMENTS.md is one line.
- Full suite `python -m pytest tests/ -q -x --deselect tests/test_supervisor.py::test_agent_restarts_within_5_seconds` -> 2406 passed, 11 skipped, 1 deselected (418 s).
- Not run: the curl scenario against a live app and a second machine (the live LAN check belongs to Phase 20, VER-05). The scenario is verified only by route existence and model field names.

## Deviations from Plan

**1. [Rule 3 - Blocking] Route check uses OpenAPI paths too.** Per 19-01/19-04, `app.routes` does not expose included routers in this FastAPI version, so `test_api_spec_scenario_routes_exist` unions `route.path` with `app.openapi()["paths"]`.

**2. [Rule 1 - Bug] Guide bullet tripped the placeholder test.** My first wording of the testing guide quoted the forbidden placeholder string; it was reworded.

**3. [Process] Full suite ran with `test_supervisor.py::test_agent_restarts_within_5_seconds` deselected.** It is the known environment-dependent failure recorded in 19-01 (real agent subprocess startup over 5 s); it touches no docs code.

**4. [Process]** The worktree base differed from the expected one; `git reset --hard 0d2427a` was done at start. Commit b416fea was amended once to drop an unwanted Co-Authored-By line.

## Known Stubs

None.

## Self-Check: PASSED

All six files exist and both commits are in `git log`; STATE.md and ROADMAP.md untouched.
