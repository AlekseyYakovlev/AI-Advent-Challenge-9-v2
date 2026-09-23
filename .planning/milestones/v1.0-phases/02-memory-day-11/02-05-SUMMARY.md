---
phase: 02-memory-day-11
plan: 05
subsystem: testing
tags: [tool-calling, lm-studio, memory, e2e-verification, qwen3.5]

# Dependency graph
requires:
  - phase: 02-memory-day-11 (plan 02)
    provides: LM Studio v1 control-endpoint load/unload fix (instance_id contract)
  - phase: 02-memory-day-11 (plan 04)
    provides: full tool-call round-trip in agent/ws.py, memory_writes on the done frame, read-only memory injection into the system prompt
provides:
  - Human/end-to-end acceptance evidence for MEM-01, MEM-03, MEM-04, MEM-05 against a real tool-capable local model
  - Confirmed working model for the graded demo — qwen/qwen3.5-9b (tool_use capable); qwen/qwen2.5-coder-14b-instruct confirmed NOT usable (no tool_use, hallucinates tool-call-shaped text instead of calling)
  - Recorded, measured fact that DeepSeek backend routing is not wired (single LLMClient construction bound to LM_STUDIO_BASE_URL) — Phase 3 must not assume model-name switching changes the HTTP target
affects: [03-personalization, 04-tasks, 05-invariants — all reuse this same LM Studio/tool-calling demo path and DeepSeek-routing limitation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "REST login (httpx, cookie jar) + raw websockets client used to drive the WS chat endpoint headlessly for acceptance testing when browser interaction isn't available to the verifier"

key-files:
  created:
    - .planning/phases/02-memory-day-11/02-05-SUMMARY.md
  modified: []

key-decisions:
  - "Demo model is qwen/qwen3.5-9b (LM Studio, capabilities: [tool_use]). qwen/qwen2.5-coder-14b-instruct must never be used for this demo — it has no tool_use capability and silently degrades to printing tool-call-shaped JSON as assistant prose, which produces zero real tool_call_dispatched events and a permanently-empty memory panel. This is the exact failure mode documented as 02-RESEARCH.md Pitfall 6/T-02-21."
  - "DeepSeek fallback routing is confirmed NOT wired: `grep -rn \"LLMClient(\" agent/` shows exactly one construction (agent/llm_client.py:337) bound to LM_STUDIO_BASE_URL. Changing `payload.model` only changes the JSON body's model field, never the HTTP target. Recorded as a known limitation, not carried forward as an open question."

patterns-established: []

requirements-completed: [MEM-01, MEM-03, MEM-04, MEM-05]

# Metrics
duration: 45min
completed: 2026-09-20
---

# Phase 02 Plan 05: Day 11 Acceptance Demo Summary

**Verified end-to-end against a real tool-capable local model (qwen/qwen3.5-9b) that memory writes only ever happen via explicit, dispatched tool calls into the correct layer, and that both layers are correctly scoped (per-chat vs. per-user) and visible in the inspection panel.**

## Performance

- **Duration:** ~45 min (including one failed attempt caused by an LM Studio restart mid-session that silently reloaded the wrong model)
- **Started:** 2026-09-20T09:29:00Z
- **Completed:** 2026-09-20T09:52:00Z
- **Tasks:** 2 (1 automated pre-flight, 1 human-verify checkpoint)
- **Files modified:** 0 (verification-only plan)

## Accomplishments
- Full automated suite green: `pytest tests/ -q` → 161 passed, 0 failed.
- Confirmed `qwen/qwen3.5-9b` as the only downloaded model whose LM Studio metadata declares `capabilities: ["tool_use"]` alongside `prism-ml/bonsai-27b`; `qwen/qwen2.5-coder-14b-instruct` and `qwen/qwen2.5-coder-14b` do not.
- Ran the complete 8-step Day 11 demo script (REST login as a real second user `alex` + a raw WebSocket client driving `/ws/chat/{chat_id}` with `model: "qwen/qwen3.5-9b"`) and verified every acceptance criterion against live log/DB state rather than prose claims.
- Diagnosed and documented a real environment failure mode (wrong model auto-selected after an LM Studio restart) as measured evidence for Pitfall 6/T-02-21, not just a theoretical risk.

## Task Commits

No source changes — this plan is verification-only (`files_modified: []` in frontmatter). No task commits; this SUMMARY.md is the only artifact.

## Files Created/Modified
- `.planning/phases/02-memory-day-11/02-05-SUMMARY.md` - this acceptance record

## Decisions Made
- **Demo model pinned to `qwen/qwen3.5-9b`.** See `key-decisions` above — measured, not assumed.
- **DeepSeek routing limitation recorded as fact.** `agent/llm_client.py:337` is the sole `LLMClient(...)` construction; `grep -rc "LLMClient(" agent/llm_client.py` → `1`, `grep -rl "LLMClient(" agent/` → only that file. Phase 3+ must add per-backend base-URL routing before DeepSeek can be selected from the UI.

## Deviations from Plan

### Auto-fixed Issues

**1. [Environment — not a code defect] Wrong model auto-loaded after LM Studio restart**
- **Found during:** Task 2 (human demo, first attempt)
- **Issue:** LM Studio restarted mid-session (see `C:\Users\Aleksey\.lmstudio\apps\bionic\server-logs\2026-09\2026-09-20.1.log`, `12:21:32` restart) and came back with `qwen/qwen2.5-coder-14b-instruct` loaded/selected instead of the tool-capable `qwen/qwen3.5-9b`. All three browser-driven demo messages that followed (`12:35`–`12:37`) went to the non-tool-use model, which printed tool-call-shaped JSON as plain assistant text (`"tool_calls": []` in every LM Studio response) instead of issuing real tool calls — exactly Pitfall 6's predicted failure. The `Память` panel correctly stayed at 0/0 because zero real writes occurred; this was working-as-designed detection of a bad model, not a bug in the memory system.
- **Fix:** No code change. Loaded `qwen/qwen3.5-9b` explicitly via `POST /api/v1/lm-studio/load-model`, confirmed `state: "loaded"` via `GET /api/v0/models`, and re-ran the full 8-step script end-to-end.
- **Files modified:** None.
- **Verification:** `logs/agent.log` shows exactly 2 `tool_call_dispatched` lines for the corrected run (chat_id 13: `save_long_term_memory` / `save_working_memory`, distinct `tool_call_id`s), 0 lines for the "Спасибо!" turn (chat_id 14), and direct SQLite inspection (`sqlite3 app.db`) confirms the working-memory row for the deleted chat is gone while the long-term `user_name → Alex` row survives.
- **Committed in:** N/A — no source changes.

---

**Total deviations:** 1 auto-fixed (environment mis-selection, not a source defect)
**Impact on plan:** None on scope. Confirms Pitfall 6/T-02-21 mitigation (Task 1's model gate) is necessary in practice, not just in theory — it is exactly what caught this failure mode before it reached the graded demo as a false negative.

## Issues Encountered
- No browser automation tool was available to the verifying agent, and app endpoints require an authenticated session cookie (Week 3 Auth foundation). The user created a dedicated test account (`alex`) so the demo could be driven headlessly (httpx + websockets) with real, inspectable evidence (agent.log, SQLite rows, WS `done` frame `memory_writes`) rather than relying on browser screenshots alone.
- `PYTHONIOENCODING` must be set to `utf-8` when running the demo script from this shell — Windows' default `cp1252` console encoding raises `UnicodeEncodeError` on the Cyrillic demo strings otherwise. Verification-only, no source impact.

## User Setup Required
None - no external service configuration required. (LM Studio itself must be running with `qwen/qwen3.5-9b` — or another model whose `/api/v0/models` entry lists `tool_use` — loaded before any future re-run of this demo.)

## Next Phase Readiness
- Phase 02 (Day 11 — Memory) is fully verified: MEM-01, MEM-03, MEM-04, MEM-05 all pass against a real tool-capable model, in addition to the mocked test coverage from plans 01-04.
- Phase 3 (Personalization) can reuse the same tool-call dispatch and `memory_writes`-reporting pattern unchanged, per the 02-04 SUMMARY decision, but must NOT assume a `payload.model` change routes to DeepSeek — that requires new per-backend routing work first.
- Operational note for future demos: always verify the LM Studio-loaded model's `capabilities` via `GET /api/v0/models` immediately before a graded run — a background LM Studio restart can silently swap the active model.

---
*Phase: 02-memory-day-11*
*Completed: 2026-09-20*
