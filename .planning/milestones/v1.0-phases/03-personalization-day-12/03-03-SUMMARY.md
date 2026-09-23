---
phase: 03-personalization-day-12
plan: 03
subsystem: testing
tags: [pytest, context-engine, lm-studio, acceptance-evidence]

requires:
  - phase: 03-personalization-day-12
    provides: Profile model, REST endpoints, and unconditional system-prompt injection (plan 03-01); sidebar panel wired to those endpoints (plan 03-02)
provides:
  - Automated proof (parametrized over all four ContextStrategy values, plus empty-chat/late-turn cases) that the profile reaches build_llm_context's outbound system message on every request
  - Live A/B acceptance evidence: two saved profiles, one shared prompt, two observably different real-model responses (PERS-04)
affects: [phase-04, phase-05, phase-06]

tech-stack:
  added: []
  patterns:
    - "Structural PERS-02 guards assert against build_llm_context (the outbound wire contract), not build_system_prompt directly"

key-files:
  created:
    - .planning/phases/03-personalization-day-12/03-03-SUMMARY.md
  modified:
    - tests/test_context_engine_profile.py

key-decisions:
  - "Task 2's live demo was run by the user directly against the running app (not simulated or self-approved by the executor), per the plan's explicit human-judgment requirement."
  - "Demo model pinned to qwen/qwen3.5-9b, the same tool-capable model confirmed working in 02-05-SUMMARY.md; DeepSeek is confirmed still unreachable by model-name selection (single LLMClient bound to LM_STUDIO_BASE_URL) — inherited limitation restated here so Phase 4 does not re-derive it."
  - "The already-running app process predated this phase's code (missing `profile` table in app.db) and was restarted via `python run.py` before the demo so the new Profile model/endpoints were actually live."

requirements-completed: [PERS-02, PERS-04]

duration: ~35min
completed: 2026-09-20
---

# Phase 03: Personalization (Day 12) — Plan 03-03 Summary

**Structural strategy/turn-count guards for PERS-02 plus a live A/B transcript proving PERS-04 against a real LM Studio model**

## Performance

- **Duration:** ~35 min (Task 1 executor + orchestrator-run Task 2 pre-flight and live demo capture)
- **Completed:** 2026-09-20
- **Tasks:** 2/2 complete
- **Files modified:** 1 (`tests/test_context_engine_profile.py`)

## Accomplishments

- Added `test_profile_present_under_every_strategy` (parametrized over all four `ContextStrategy` members) and `test_profile_present_on_first_and_late_turns` (empty chat + 30-message chat past the sliding window) to `tests/test_context_engine_profile.py`. Both assert against `build_llm_context(...)[0]["content"]` — the actual outbound wire contract — not the lower-level `build_system_prompt` helper.
- Ran the full Day 12 acceptance demo against the live app and a real LM Studio model (`qwen/qwen3.5-9b`): the same prompt, asked under two different saved profiles in two fresh chats, produced two visibly different responses matching each profile's requested style/format/constraints.
- Confirmed profile persistence: the `profile` table's stored row matches Profile B exactly after save, consistent with what the panel would show on reload.

## Task Commits

1. **Task 1: Lock "every request" with strategy and conversation-length coverage** - `d2d5e94` (test)
2. **Merge (worktree)** - `d5884fe`

**Task 2** made no source changes (live demo + evidence capture only, as specified by the plan).

## Files Created/Modified

- `tests/test_context_engine_profile.py` — added 2 tests (5 net, since `test_profile_present_under_every_strategy` is parametrized ×4); file now has 12 tests total, up from 7 after plan 03-01.

## Decisions Made

- Task 1 executor found `agent/context_engine.py::summarize_if_needed` is currently a no-op stub for every strategy (matching the existing pattern in `tests/test_strategies.py`), so no LLM mocking was needed to keep the new tests offline — both new tests exercise real compression logic paths without a network call.
- The orchestrator, not a headless subagent, ran Task 2's pre-flight and captured the live demo evidence, because Task 2 requires an actual human at a browser judging real-model output — a background executor has no path to the user and cannot self-approve this per the plan's explicit instruction.

## Deviations from Plan

**1. [Environment] Live app process was running stale code**

- **Found during:** Task 2 pre-flight (`profile` table check).
- **Issue:** The already-running UI/Agent processes (ports 8000/8001) predated the Day12 branch's merge of plans 03-01/03-02, so `app.db` had no `profile` table yet.
- **Fix:** Stopped the stale processes and restarted via `python run.py`. Confirmed `profile` table present immediately after.
- **Verification:** `sqlite3` table listing showed `profile` only after restart.
- **Impact:** None on scope — environmental, not a code defect.

**Total deviations:** 1 (environmental, non-blocking)
**Impact on plan:** None. All acceptance criteria still met against the corrected environment.

## Issues Encountered

None beyond the stale-process restart above.

## Live A/B Demo — PERS-04 Acceptance Evidence

**Pre-flight (run by the orchestrator before the demo):**
- `pytest tests/ -q` → **185 passed**, 0 failed.
- `GET http://localhost:8001/health` → `{"status": "ok"}`.
- `profile` table confirmed present in `app.db` (after the restart above).
- LM Studio model loaded and confirmed tool-capable: `qwen/qwen3.5-9b` — `state: "loaded"`, `capabilities: ["tool_use"]` (via `GET /api/v0/models`). This is the same model confirmed working for the graded demo in `02-05-SUMMARY.md`.
- **DeepSeek-routing limitation restated (inherited from 02-05-SUMMARY.md):** `agent/` constructs exactly one `LLMClient`, bound to `settings.LM_STUDIO_BASE_URL`. Changing the selected model name changes only the JSON `model` field, never the HTTP target. DeepSeek is not reachable by picking a model name; per-backend routing remains out of scope through at least Phase 4.

**Profile A** (chat 16, message id 197→198, saved at `2026-09-20 12:07:56` UTC):
- Стиль общения: `отвечай очень коротко, одним предложением, сухо и по делу`
- Формат ответа: `обычный текст, без списков и заголовков`
- Ограничения: `никаких примеров кода`

**Shared prompt:** `Как работает кэш в браузере?`

**Response A** (verbatim, from `message.id=198`):
> Кэш браузера хранит копии веб-страниц и их ресурсов на устройстве для ускорения загрузки при повторном посещении.

**Profile B** (chat 17, message id 199→200, saved at `2026-09-20 12:09:08` UTC):
- Стиль общения: `отвечай подробно и дружелюбно, с развёрнутыми объяснениями`
- Формат ответа: `маркированный список с заголовками`
- Ограничения: `обязательно добавь пример кода`

**Response B** (verbatim, from `message.id=200`, truncated here for readability — full text in `message.id=200` in `app.db`):
> # Как работает кэш в браузере: подробное руководство 🖥️
>
> Отличный вопрос! Кэширование — один из важнейших механизмов... [full response includes: "🔍 Что такое кэш браузера?", "📊 Основные типы кэширования" with 3 numbered subsections, "💾 Как работает кэш: пошаговый процесс" as a numbered list, an HTTP-headers table, a JavaScript code block with three functions (`checkCacheStatus`, `clearBrowserCache`, `setCacheWithExpiry`), browser-specific clearing instructions, a security section, and a practical-tips numbered list.]

**Judgment (PERS-04):** Response A is one short, dry, prose sentence with no lists, headers, or code — matching Profile A exactly. Response B is long, structured with markdown headers and bulleted/numbered lists, and includes a JavaScript code example — matching Profile B exactly. The two responses to the identical prompt are observably, unambiguously different in style, format, and content, caused only by the different saved profiles (both chats were fresh, with no prior conversation history). **User confirmed: approved.**

**Persistence check (PERS-03):** The `profile` table's stored row (`user_id=2`) matches Profile B's three field values exactly as of `updated_at=2026-09-20 12:09:07`, consistent with what `GET /api/v1/profile` and the reloaded panel would show.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- PERS-01 through PERS-04 are all now covered: structurally (unit + strategy/turn-count tests) and behaviorally (live A/B against a real model).
- Personalization backend, UI, and acceptance evidence are complete. Phase 3 is ready to be verified and merged: `Day12` → `main`.
- Phase 4 should reuse the tool-call dispatcher from Phase 2 unchanged (per STATE.md decision) and must not assume `payload.model` changes route to DeepSeek.

---
*Phase: 03-personalization-day-12*
*Completed: 2026-09-20*
