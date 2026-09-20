---
phase: 01-auth-foundation
plan: 03
subsystem: auth
tags: [auth, authorization, idor, rest-api, settings-scoping, test-repair]
dependency-graph:
  requires:
    - agent/dependencies.py get_current_user (Plan 01-01)
    - shared/models.py Chat.user_id / Settings.user_id nullable columns (Plan 01-02)
  provides:
    - Depends(get_current_user) + user_id filtering on every pre-existing /api/v1 route
    - agent/main.py::_get_chat_or_404(session, chat_id, user_id) — 404-not-403 IDOR guard
    - agent/main.py::_ensure_global_settings(session, user_id) / _resolve_settings(session, chat_id, user_id)
    - agent/context_engine.py::get_effective_settings owner-aware global fallback
    - tests/conftest.py login_test_client / second_authenticated_client
    - tests/test_scoping.py access-control regression suite
  affects:
    - agent/main.py lifespan (no longer pre-creates an ownerless global Settings row)
    - every pre-existing REST/WS test file that called the API unauthenticated
tech-stack:
  added: []
  patterns:
    - "404-not-403 IDOR guard: ownership mismatch and missing-row return the identical response body"
    - "Per-user global settings row (chat_id IS NULL, user_id = caller) instead of one installation-wide row"
    - "TestClient.portal.call() to seed/login a user inside the app's own event loop for sync WebSocket tests"
key-files:
  created:
    - tests/test_scoping.py
  modified:
    - agent/main.py
    - agent/context_engine.py
    - tests/conftest.py
    - tests/test_cascade_delete.py
    - tests/test_settings_fallback.py
    - tests/test_stats.py
    - tests/test_strategies.py
    - tests/test_ws_origin_validation.py
    - tests/test_ws_security.py
    - tests/test_concurrent_ws.py
    - tests/test_websocket_cors.py
decisions: []
metrics:
  duration: "~45 minutes"
  completed: 2026-09-20
---

# Phase 01 Plan 03: Access Control & Ownership Enforcement Summary

Every pre-existing `agent/main.py` route (except `POST /api/v1/auth/login` and the supervisor's `/health`/`/debug/routes`) now requires a valid session cookie and filters/checks ownership by `user_id`, closing the IDOR gap left by Plans 01-02; the entire pre-existing test suite is repaired to authenticate through the new gate.

## What Was Built

- `agent/main.py` — `Depends(get_current_user)` added as a sibling parameter to `list_chats`, `create_chat`, `delete_chat`, `get_chat_tree`, `get_chat_stats`, `branch_chat`, `get_settings`, `update_settings`, `list_lm_studio_models`, `load_lm_studio_model`, `unload_lm_studio_model` (13 total occurrences counting the pre-existing `logout`/`get_me`); `health` and `debug_routes` deliberately left ungated for `ui/supervisor.py`'s unauthenticated polling.
  - `_get_chat_or_404(session, chat_id, user_id)` now 404s identically whether the chat is missing or owned by someone else — never a distinguishable 403.
  - `_resolve_settings(session, chat_id, user_id)` calls `_get_chat_or_404` first for a per-chat lookup (so a foreign `chat_id` 404s before any settings read), then falls back to `_ensure_global_settings(session, user_id)`.
  - `_ensure_global_settings(session, user_id)` and the `update_settings`/`create_chat` handlers write `user_id` on every new `Chat`/`Settings` row.
  - `lifespan()` no longer calls `_ensure_global_settings()` at startup (that call had no user context and would have created a duplicate ownerless global row on every boot after Plan 02's backfill).
- `agent/context_engine.py` — `get_effective_settings()` keeps its existing signature (still called by `agent/ws.py` and multiple tests) but its global-row fallback is now owner-aware: it loads the `Chat`, resolves `owner_id = chat.user_id`, and selects/creates the global `Settings` row scoped to that owner. `_facts_target_row()` copies `user_id` from the owning chat onto any settings row it creates.
- `tests/conftest.py` — `_create_user()` (shared insert helper, `seed_user` now delegates to it), `login_test_client(client, username, password)` (seeds a user via `client.portal.call()` inside the sync `TestClient`'s own event loop, then logs in over REST so the cookie jar carries the session onto later WS handshakes), and `second_authenticated_client` (a second logged-in identity for cross-user IDOR tests).
- `tests/test_scoping.py` (new) — parametrized 401 sweep over all nine previously-open routes, an open-route check for `/health`/`/debug/routes`, chat-ownership-on-create, per-user chat-list isolation, IDOR 404s across tree/stats/branch/delete plus settings get/put, per-user global-settings isolation, and the single-user global→per-chat fallback invariant.
- Eight pre-existing test files repaired to authenticate: `test_cascade_delete.py`, `test_settings_fallback.py`, `test_stats.py`, `test_strategies.py` swapped their `client` fixture parameter for `authenticated_client` (including the two `test_strategies.py` tests that previously built an inline unauthenticated `AsyncClient`, now consuming the fixture instead); `test_ws_origin_validation.py`, `test_ws_security.py`, `test_concurrent_ws.py`, `test_websocket_cors.py` call `login_test_client(client)` immediately after `with TestClient(app) as client:`, before the first REST call.

## Deviations from Plan

None — plan executed exactly as written. The one file-list correction (`agent/main.py`'s unused `async_session_factory` import removed after the `lifespan()` simplification) is ordinary cleanup within the same file already in scope, not a deviation.

### Deferred Issues (not in scope, not fixed)

- `tests/test_context_engine.py::test_extract_and_update_facts_debounce_and_merge` is the same pre-existing timing-dependent flake already documented in Plans 01-01 and 01-02's summaries (fire-and-forget debounced fact extraction racing a fixed `asyncio.sleep`, reading back through a second, separately-opened session). Failed once during iterative full-suite runs in this session, passed cleanly on the next two consecutive full-suite runs (`108 passed`, zero failures). `agent/context_engine.py`'s debounce/session-isolation logic is untouched by this plan (only `get_effective_settings`'s global-fallback branch and `_facts_target_row`'s row-creation branch were touched, neither of which this test's fast path exercises); out of scope per the plan's file list and the SCOPE BOUNDARY rule.

## Verification Evidence

- `python -m pytest tests/test_scoping.py -x` (before Task 2) — RED as designed: `assert 200 == 401` on the first parametrized 401 case, no import/fixture errors.
- `python -m pytest tests/ -q --collect-only` (after Task 1) — 108 tests collected, no collection errors.
- `python -m pytest tests/test_bootstrap_admin.py -q` (after Task 1) — 9 passed (no user seeded by the autouse fixture, Plan 02's assumption intact).
- `python -m pytest tests/test_scoping.py tests/test_context_engine.py -v` (Task 2) — 26 passed.
- `grep -c "Depends(get_current_user)" agent/main.py` — 13 (>= 11 required).
- `grep -n "async def health" -A 3 agent/main.py` / `debug_routes` — neither takes `current_user`.
- `grep -n "status.HTTP_403" agent/main.py` — no matches.
- `grep -n "Chat.user_id == current_user.id" agent/main.py` — present in `list_chats`.
- `grep -n "== None" agent/main.py agent/context_engine.py tests/test_scoping.py tests/conftest.py` — no matches.
- `python -c "import inspect,agent.main as m; assert len(inspect.signature(m._get_chat_or_404).parameters)==3; assert len(inspect.signature(m._resolve_settings).parameters)==3; print('ok')"` — printed `ok`.
- `sed -n '/async def lifespan/,/^app = FastAPI/p' agent/main.py | grep -c "_ensure_global_settings"` — 0.
- `python -m pytest tests/test_auth.py tests/test_bootstrap_admin.py -q` (Task 2) — 18 passed (Plans 01/02 untouched).
- `python -m pytest tests/ -q` (Task 3, two consecutive runs after the repairs) — 108 passed, zero failures, zero errors, both times.
- `python -m pytest tests/test_concurrent_ws.py -q` — 1 passed (5-parallel-WS guarantee survives the auth retrofit).
- `python -m pytest tests/test_cors.py -q` — 3 passed (confirmed no change needed, only touches `/health`).
- `grep -rln "login_test_client" tests/test_ws_origin_validation.py tests/test_websocket_cors.py tests/test_ws_security.py tests/test_concurrent_ws.py` — matches in all four files.
- `grep -oE "\bclient: AsyncClient" tests/test_settings_fallback.py | wc -l` — 0 (all swapped to `authenticated_client`; the plan's literal `grep -c "client: AsyncClient"` returns 6 only because it substring-matches inside `authenticated_client: AsyncClient` itself — confirmed with a word-boundary grep that no bare `client` parameter remains).
- `grep -n "user_id" tests/test_cascade_delete.py` — present on the directly-inserted `Chat` row.
- Full plan-level `<verification>` block re-run at the end: all five checks pass (`pytest tests/ -q` zero failures; `pytest tests/test_scoping.py -v` 19 passed; `Depends(get_current_user)` count 13; no `HTTP_403`; lifespan `_ensure_global_settings` count 0).

## Known Stubs

None — every artifact (route gating, ownership checks, settings scoping, test repairs) is wired to real code paths and exercised by the test suite.

## Assumption Drift (advisory)

None — implementation matched the plan's `<action>` text throughout; no material drift from CONTEXT.md decisions or the plan's stated assumptions.

## Self-Check: PASSED

- FOUND: agent/main.py (Depends(get_current_user) on 13 routes, _get_chat_or_404/_resolve_settings/_ensure_global_settings with user_id)
- FOUND: agent/context_engine.py (owner-aware get_effective_settings, _facts_target_row user_id)
- FOUND: tests/conftest.py (login_test_client, second_authenticated_client, _create_user)
- FOUND: tests/test_scoping.py
- FOUND commit 3a660c4 (test: RED access-control suite + login_test_client helper)
- FOUND commit 719d035 (feat: gate every REST route + user-scope settings fallback)
- FOUND commit 32a8a72 (fix: repair pre-existing test suite against the now-gated API)
