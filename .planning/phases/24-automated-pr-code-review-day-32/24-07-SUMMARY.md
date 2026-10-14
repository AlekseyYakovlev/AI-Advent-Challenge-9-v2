---
phase: 24-automated-pr-code-review-day-32
plan: 07
subsystem: agent
tags: [review, fastapi, settings, code-index]
requires:
  - phase: 24-02
    provides: ReviewConfig and ReviewCodeIndex tables
  - phase: 24-03
    provides: fetch_pull_request, fetch_head_sha
  - phase: 24-05
    provides: refresh_code_index, spawn_refresh_job, latest_index
  - phase: 24-06
    provides: run_review
provides:
  - agent/review_service.py (produce_review, resolve_review_settings, resolve_docs_kb, resolve_provider_and_model, ReviewServiceError)
  - agent/review_api.py (POST /api/v1/reviews, GET/PUT /api/v1/reviews/config, GET /api/v1/reviews/code-index, POST /api/v1/reviews/code-index/refresh)
  - providers.build_client(row, timeout=None)
affects: [24-04, 24-08, 24-09, 24-10]
key-files:
  created:
    - agent/review_service.py
    - agent/review_api.py
    - tests/test_review_service.py
    - tests/test_review_api.py
  modified:
    - agent/providers.py
    - agent/main.py
key-decisions:
  - "Settings GET is readable with an API key but omits repo_path for it; PUT and refresh are session-only"
  - "Per-PR lock keyed (user, lower-cased repo, PR number), released and removed in the shielded task's finally"
requirements-completed: [REV-02, REV-06]
completed: 2026-10-14
---

# Phase 24 Plan 07: Review route and settings routes Summary

One authenticated route runs the whole chain (settings, GitHub PR, code index refresh, review, final head check) and returns the structured reply or a named reason; per-user settings, index state and a session-only refresh action sit beside it.

## Tasks

| Task | Commit | Result |
| --- | --- | --- |
| 1. review_service.py + build_client timeout | 902360f | `pytest tests/test_review_service.py tests/test_llm_providers_service.py tests/test_llm_providers_routing.py -q`: 77 passed |
| 2. review_api.py, router wiring | 0dacced | `pytest tests/test_review_api.py tests/test_review_service.py tests/test_http_chat.py tests/test_help_api.py tests/test_bearer_auth.py -q`: 128 passed; `grep include_router(review_router) agent/main.py` matched |

Full suite: `pytest tests/ -q -x --ignore=tests/test_kb_real_pdfs.py` gave 3443 passed, 4 skipped, no failure. The `python -c "from agent.main import app ..."` route listing in the plan's verification block does not work as written (this Starlette version wraps routers in `_IncludedRouter` objects without `.path`); the import itself succeeds and every route is exercised by the API tests.

## Behaviour notes

- Provider resolution: `provider_id` NULL selects the user's enabled LM Studio provider; a disabled or deleted one raises `REVIEW_NOT_CONFIGURED`. With no stored model LM Studio's `/api/v0/models` is read for the first loaded `llm`/`vlm` model (falls back to `/v1/models` skipping ids containing "embed" when the control API answers an HTTP error). The settings GET resolves non-strictly with a 3 s timeout.
- Documentation base: review setting, else the Phase 23 `/help` mark, else none; foreign or not-ready bases count as absent.
- `head_sha` of the request is accepted and validated but the reply carries the head read from GitHub at the start; the final `fetch_head_sha` sets `head_changed` and a note, and its failure is only logged.
- Refresh route: body is optional; with a body it needs a JSON content type. Base ref order: body, latest index row, current local branch, else 422.
- All error bodies are `{"detail", "code"}`; the 500 body is fixed and never contains the exception text.

## Deviations from Plan

**1. [Rule 3 - mechanics] Worktree base.** HEAD was behind the required base (it was an ancestor); reset to c27ca0e as the startup check prescribes.

**2. [Interpretation] Refresh POST body.** Plan says the body is optional but lists the JSON content-type check for the route family; implemented as "content type required only when a body is sent", so an empty POST works.

**3. [Interpretation] Code-index GET.** `latest_index` is called without a repo key, so it returns the user's most recently updated base (the plan's single-repository assumption), avoiding a git subprocess on every settings read.

## Not verified

- Live LM Studio model discovery (`/api/v0/models` shape) is covered only by respx fakes, not a running LM Studio.
- No live GitHub server or real model; `fetch_pull_request`, `refresh_code_index`, `run_review` are faked in the service tests.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (T-24-33..T-24-39 mitigations are implemented and tested: 401/403/415 tests, session-only PUT/refresh, ownership 404s, path hidden from API-key callers, fixed 500 text, per-PR busy guard).

## Self-Check: PASSED
