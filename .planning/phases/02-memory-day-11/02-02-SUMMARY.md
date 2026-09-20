---
phase: 02-memory-day-11
plan: 02
subsystem: api
tags: [lm-studio, httpx, respx, llm-client]

# Dependency graph
requires: []
provides:
  - LMStudioClient load/unload targeting the actual LM Studio v1 control endpoints
  - instance_id tracking keyed by model_id, threaded from load through unload/emergency-unload
  - Regression-guard tests proving the v0 endpoints are no longer called
affects: [02-03 (extends agent/llm_client.py further for Phase 2's tool-call dispatcher)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "LM Studio control-plane calls use /api/v1/models/{load,unload} with instance_id-based unload, not model-id-based"

key-files:
  created: []
  modified:
    - agent/llm_client.py
    - tests/test_lm_studio_client.py
    - tests/test_model_switch_lock.py

key-decisions:
  - "Did not forward gpu_offload/context_length to the v1 load endpoint since the verified contract does not document them; logs lm_studio_load_params_ignored when either is non-default instead of silently dropping them"
  - "Live LM Studio probe was attempted but the instance was unreachable in this environment (curl timed out), so the minimal verified v1 contract from 02-RESEARCH.md was implemented unchanged rather than guessing an extended shape"

patterns-established:
  - "Model instance identifiers returned by an external control API are cached client-side (dict keyed by model_id) and consumed on the corresponding teardown call, with a fallback to the original id if no mapping exists"

requirements-completed: [MEM-03]

# Metrics
duration: 20min
completed: 2026-09-20
---

# Phase 02 Plan 02: LM Studio v1 Control-Plane Migration Summary

**Migrated `LMStudioClient` load/unload from the dead `/api/v0/models/*` control endpoints to the verified `/api/v1/models/{load,unload}` contract, with `instance_id` capture/threading and two new regression-guard tests.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-09-20T06:13:00Z (approx.)
- **Completed:** 2026-09-20T06:33:23Z
- **Tasks:** 2 completed
- **Files modified:** 3

## Accomplishments
- `_load_model_locked` now posts to `/api/v1/models/load` with body `{"model": model_id}` only, and captures `instance_id` from the response into `self._instance_ids`.
- `_unload_model_http` now posts to `/api/v1/models/unload` with body `{"instance_id": ...}`, resolved from the cached mapping with a fallback to the model id.
- `_unload_model_locked` and `_emergency_unload` clean up the `instance_id` mapping alongside the existing `_current_loaded_model` reset.
- All 7 respx route registrations across `tests/test_lm_studio_client.py` and `tests/test_model_switch_lock.py` repointed from v0 to v1; two new tests added (`test_unload_sends_instance_id_from_load_response`, `test_load_targets_v1_endpoint`) that pin the new contract and make a regression back to v0 detectable.
- Full suite green: `pytest tests/ -q` → 125 passed.

## Live Probe Result

Per the plan's Task 1 instruction, the live LM Studio instance was probed before editing:

```
curl -s -m 3 -X POST http://localhost:1234/api/v1/models/load -H "Content-Type: application/json" -d '{"model":"qwen/qwen3.5-9b"}'
```

This timed out (curl exit code 28) — LM Studio is not running in this execution environment. Per the plan's fallback instruction ("If LM Studio is not running, skip the probe and implement the minimal verified contract below unchanged"), the implementation forwards only `{"model": model_id}` on load and does **not** forward `gpu_offload`/`context_length`, logging `lm_studio_load_params_ignored` when either is supplied non-default. This was not re-verified against a live instance — a future session with LM Studio running should confirm whether the v1 load endpoint actually accepts `gpu_offload`/`context_length`, per the plan's note.

## Task Commits

1. **Task 1: Migrate LMStudioClient model load/unload to the v1 control endpoints** - `5132fe5` (feat)
2. **Task 2: Repoint the LM Studio mocks to the v1 contract and restore the suite to green** - `1420264` (test)

## Files Created/Modified
- `agent/llm_client.py` - `LMStudioClient` now targets `/api/v1/models/load` and `/api/v1/models/unload`, tracks `instance_id` per `model_id`, and logs when unsupported load params are supplied.
- `tests/test_lm_studio_client.py` - All respx routes repointed to v1; load mocks return the verified v1 response shape; two new tests guard the `instance_id` unload contract and the v0→v1 regression.
- `tests/test_model_switch_lock.py` - Both respx routes (load/unload) repointed to v1 so the switch-lock serialization test still exercises the real call path.

## Decisions Made
- Did not forward `gpu_offload`/`context_length` on the v1 load call — the verified v1 contract (02-RESEARCH.md Pitfall 5) does not document these fields, so guessing an unverified shape risked silently breaking the load call against the real server. Instead, non-default values are logged via `lm_studio_load_params_ignored` so the limitation is visible in logs.
- Added `json.JSONDecodeError` to the existing `except httpx.HTTPError` clause in `_load_model_locked` (see Deviations below) since the new code path calls `response.json()` after `raise_for_status()`, and a malformed JSON body would otherwise propagate as an unhandled exception instead of a typed `ModelLoadResult`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Malformed JSON response would have crashed `_load_model_locked` instead of returning a typed error**
- **Found during:** Task 1 (adding `response.json()` parsing to capture `instance_id`)
- **Issue:** The plan's action text says to "parse the response JSON and record `self._instance_ids[model_id] = body["instance_id"]`" but the existing except clauses (`httpx.TimeoutException`, `httpx.ConnectError`, `httpx.HTTPError`) don't cover `json.JSONDecodeError`, which `response.json()` can raise on a malformed body. Without a fix, that would propagate as an unhandled exception through `load_model`, breaking the documented behavior that load errors always return a `ModelLoadResult`.
- **Fix:** Extended the existing `except httpx.HTTPError as exc:` clause to `except (httpx.HTTPError, json.JSONDecodeError) as exc:`, reusing the same `ModelLoadStatus.ERROR` handling path. No new behavior branch was added — this only closes a gap in the existing error handling.
- **Files modified:** `agent/llm_client.py`
- **Verification:** `pytest tests/test_lm_studio_client.py tests/test_model_switch_lock.py -v` (8/8 pass) and `pytest tests/ -q` (125/125 pass).
- **Committed in:** `5132fe5` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Necessary for correctness — closes an unhandled-exception path introduced by the plan's own JSON-parsing requirement. No scope creep; no test or production behavior outside `agent/llm_client.py`'s load path was touched.

## Issues Encountered
- The plan's Task 1 acceptance criteria includes `grep -c 'async def load_model(self, model_id: str, gpu_offload: int, context_length: int | None)' agent/llm_client.py` expecting output `1`. The actual result is `0` because the pre-existing (unmodified by this plan) signature is written across multiple lines:
  ```python
  async def load_model(
      self,
      model_id: str,
      gpu_offload: int,
      context_length: int | None,
  ) -> ModelLoadResult:
  ```
  This formatting predates this plan (confirmed via `git diff agent/llm_client.py`, which shows no changes to the `load_model` signature). The plan's actual `<verify>` gate — the `python -c` import/source assertion — passed and printed `ok`; the multi-line-signature grep is a single acceptance-criteria bullet that doesn't account for existing multi-line formatting and is not treated as a defect since the interface contract (parameter names, types, order) is unchanged, matching the plan's "Interfaces that must keep working unchanged" section.

## Next Phase Readiness
- `agent/llm_client.py`'s `LMStudioClient` now targets the LM Studio version actually installed on this machine; the MEM-03 acceptance demo (loading `qwen/qwen3.5-9b` via the app's model selector) is unblocked pending a live LM Studio instance to confirm end-to-end (not verified in this environment — LM Studio was not running).
- No blockers for Plan 03, which extends the same file with the tool-call dispatcher; the `_instance_ids` map and v1 endpoints are stable, tested interfaces for it to build on.

---
*Phase: 02-memory-day-11*
*Completed: 2026-09-20*

## Self-Check: PASSED

- FOUND: agent/llm_client.py
- FOUND: tests/test_lm_studio_client.py
- FOUND: tests/test_model_switch_lock.py
- FOUND: .planning/phases/02-memory-day-11/02-02-SUMMARY.md
- FOUND commit: 5132fe5 (Task 1)
- FOUND commit: 1420264 (Task 2)
- FOUND commit: c699d92 (SUMMARY.md)
