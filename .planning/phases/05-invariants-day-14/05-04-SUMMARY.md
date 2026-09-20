---
phase: 05-invariants-day-14
plan: 04
subsystem: testing
tags: [pytest, context-engine, invariants, regression-guard]
status: paused

# Dependency graph
requires:
  - phase: 05-invariants-day-14 (plan 01)
    provides: GlobalInvariant table, agent/invariants.py CRUD module
  - phase: 05-invariants-day-14 (plan 02)
    provides: ChatInvariant table, resolve_active_invariants, build_system_prompt injection
  - phase: 05-invariants-day-14 (plan 03)
    provides: self-critique conflict check, InvariantConflict persistence, conflict surfacing
provides:
  - tests/test_invariants_coverage.py — strategy-matrix and long-history injection guards for INV-03
  - A drift guard pinning resolve_active_invariants as the sole invariant read path in context assembly
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Text-based drift guard: read a module's source as text (comment lines stripped) and assert a single call-site pattern, to stop a second, divergent read path from being added later without a corresponding test failure"

key-files:
  created:
    - tests/test_invariants_coverage.py
  modified: []

key-decisions:
  - "Task 1 (automated test coverage) executed; Task 2 (checkpoint:human-verify acceptance demo against a real LLM backend) is explicitly out of scope for this sequential, non-interactive execution pass and is left paused for the live developer"

patterns-established:
  - "Strategy-matrix parametrized test (all four ContextStrategy values, plus a long-history and a near-overflow case) as the standard shape for pinning 'survives every compression strategy' guarantees"

requirements-completed: []

# Metrics
duration: 12min
completed: 2026-09-21
---

# Phase 5 Plan 4 (Task 1 only): Invariant Injection Coverage Guard Summary — PAUSED at Task 2 checkpoint

**Strategy-matrix and long-history pytest coverage proving invariant injection survives all four ContextStrategy values in `build_llm_context`, plus a drift guard pinning `resolve_active_invariants` as the sole invariant read path — Task 2's live acceptance demo against a real LLM backend is paused pending the developer**

## Performance

- **Duration:** 12 min (Task 1 only)
- **Started:** 2026-09-21T00:00:00Z
- **Completed:** 2026-09-21T00:12:00Z
- **Tasks:** 1 of 2 (Task 1 complete; Task 2 paused — see below)
- **Files modified:** 1 (created)

## Scope of this execution pass

This plan has two tasks:
1. **Task 1** (`type="auto"`, TDD) — write `tests/test_invariants_coverage.py`. Pure automated work against already-shipped code from 05-01/05-02/05-03.
2. **Task 2** (`type="checkpoint:human-verify" gate="blocking"`) — an eleven-step live acceptance demo run by the developer against a real LLM backend (DeepSeek/LM Studio) through the actual browser UI, followed by writing the model/backend used, the Assumption A1 (critique-JSON reliability) verdict, and the two STATE.md Phase 5 open-question resolutions into this summary.

This execution ran sequentially on the primary working tree (branch `Day14`, no worktree isolation) with no ability to interact with a live human across turns or drive a real browser/LLM session. Per explicit orchestrator instruction, **only Task 1 was executed**. Task 2 was not attempted, not simulated, and no developer confirmation was fabricated. The app was not started (`python run.py` was not run) as part of this pass, since that step exists in the plan specifically to prepare for Task 2's human checkpoint.

## Accomplishments (Task 1)

- `tests/test_invariants_coverage.py` pins INV-03's "on every relevant request" wording with a `pytest.mark.parametrize` strategy matrix covering all four `ContextStrategy` values (`sliding`, `sticky`, `truncate_middle`, `no_compression`); each case seeds one global invariant overridden by one chat invariant plus 30 messages, then asserts the assembled `build_llm_context(...)` output's first (`system`) message still contains the invariants header and both D-06 override labels verbatim
- A long-history case (40 messages, well past `RECENT_MESSAGE_COUNT=10`) confirms the guarantee does not silently decay as history grows under `SLIDING_WINDOW`
- A near-overflow `NO_COMPRESSION` case confirms invariants are not dropped as a size-saving measure when the context window has headroom
- A baseline case confirms zero invariants configured leaves the system prompt free of the "Active invariants" header while the chat's own `system_prompt` (`Settings.system_prompt`) still comes through untouched
- A drift guard reads `agent/context_engine.py` as text (comment lines stripped) and asserts `resolve_active_invariants` appears exactly once and neither `list_global(` nor `list_chat_invariants(` appear — stopping a future second, divergent invariant read path from entering context assembly without a test failure

## Task Commits

1. **Task 1: Guard invariant injection across every compression strategy and a long history** - `d7cd4e6` (test)

Task 2 has no commit — it was not attempted this pass (see Scope above).

## Files Created/Modified

- `tests/test_invariants_coverage.py` (new) — 8 test cases total: 4 parametrized strategy-matrix cases (`test_invariants_injected_under_every_strategy`), 1 long-history case, 1 near-overflow `NO_COMPRESSION` case, 1 no-invariants-configured baseline, 1 text-based drift guard

## Decisions Made

- No implementation code changed — this plan's Task 1 is pure test-writing against the invariant injection and resolution logic already shipped in 05-01/05-02/05-03 (`agent/invariants.py::resolve_active_invariants`, `agent/context_engine.py::build_system_prompt`). All 8 new tests passed on first write, with zero fixes required, confirming the existing implementation already satisfies INV-03's guarantee under every compression strategy.
- Followed the plan's `<behavior>` spec exactly: reused `authenticated_client` for `seeded_user_id`, mirrored `tests/test_strategies.py`'s `_append_messages` seeding shape and `tests/test_context_engine_invariants.py`'s `_create_chat` shape locally (no cross-test-file import, to keep the new module self-contained), and did not mock the LLM or `build_system_prompt` anywhere.

## Verification

- `python -m pytest tests/test_invariants_coverage.py -v` — **8 passed**, 0 failed (all 4 parametrized strategy cases reported individually: `sliding`, `sticky`, `truncate_middle`, `no_compression`)
- Acceptance-criteria greps, run and confirmed against the actual file:
  - `grep -c "ContextStrategy.SLIDING_WINDOW\|ContextStrategy.STICKY_FACTS\|ContextStrategy.TRUNCATE_MIDDLE\|ContextStrategy.NO_COMPRESSION" tests/test_invariants_coverage.py` → `7` (≥ 4 required)
  - `grep -c "build_llm_context" tests/test_invariants_coverage.py` → `7` (≥ 3 required)
  - `grep -c "overridden for this chat — see below" tests/test_invariants_coverage.py` → `1` (≥ 1 required)
  - `grep -cE "respx|httpx.Response" tests/test_invariants_coverage.py` → `0` (must be 0 — confirmed no LLM mocking used or needed)
- `python -m pytest tests/ -q` — **286 passed, 0 failed** (full suite, run from `C:\Projects\AiAdventAgentV2`, 47.13s). Only pre-existing, unrelated `DeprecationWarning`s appeared (Starlette/`httpx` TestClient deprecation, SQLAlchemy `session.execute()` vs `session.exec()` in `test_strategies.py`/`test_tasks.py`/`migrate_strategies.py`) — none touch this plan's files.

## Deviations from Plan

None — Task 1 executed exactly as written; all 8 tests passed on the first run with no auto-fixes needed.

## Issues Encountered

None for Task 1.

## What is NOT done — Task 2 (paused, awaiting the live developer)

Task 2 is a `type="checkpoint:human-verify" gate="blocking"` task requiring the actual developer to:
1. Start `python run.py` and log in through the real browser UI
2. Walk through eleven numbered verification steps (fold/unfold retrofit, global invariant CRUD, cross-account sharing, per-chat override, injection + precedence against a real model, the self-critique conflict-check + justify/retract round-trip, dual conflict surfacing, reload persistence, fail-open behavior, cascade delete)
3. Report back which model/backend was used, the Assumption A1 (critique-JSON reliability) verdict, confirmation that STATE.md's two Phase 5 open questions are closed, and a latency/cost observation on the always-on critique call

None of this was performed, simulated, or fabricated in this pass. Per the phase's own protocol, this requires a live human clicking through a running app against a real DeepSeek/LM Studio backend — it cannot be completed by a sequential, non-interactive executor. `python run.py` was not started.

**STATE.md's two Phase 5 open questions and 05-RESEARCH.md's Assumption A1 remain formally unresolved in writing** (though 05-CONTEXT.md's D-05/D-06/D-08 already settled the underlying design decisions at planning time — Task 2 is where the *demo confirmation* of those decisions, and the live reliability spot-check of A1, was meant to happen and be recorded).

## User Setup Required

**A live human must run Task 2 of this plan** (`.planning/phases/05-invariants-day-14/05-04-PLAN.md`, lines 139-227) — start the app, complete the eleven verification steps against the actual configured LLM backend, and report back so this summary can be updated with the model/backend used, the Assumption A1 verdict, and the two open-question resolutions. Until that happens, this plan is not complete and `Day14` should not be pushed/merged to `main` per the plan's own `<objective>` ("After the checkpoint passes, push branch `Day14`...").

## Next Phase Readiness

- Task 1's coverage guard is a pure addition with no blocking effect on Task 2 — the live demo can proceed independently whenever a developer is available.
- No blockers for re-running this plan's Task 2 in a fresh session with a live human present.
- `ROADMAP.md` has deliberately NOT been updated by this pass (per explicit instruction) — the orchestrator updates it once the full plan, including Task 2's checkpoint, is done.

---
*Phase: 05-invariants-day-14*
*Completed: 2026-09-21 (Task 1 only; Task 2 paused)*

## Self-Check: PASSED

- FOUND: tests/test_invariants_coverage.py
- FOUND commit: d7cd4e6 (test)
