# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- ✅ **v2.0 Week 4: MCP Integration** — Phases 7-9, 12 (shipped 2026-10-02)
- 🚧 **v3.0 Week 5: RAG** — Phases 10-11 (carried over), 13+ (in progress)

## Phases

<details>
<summary>✅ v1.0 Week 3: Agent Memory & Task State (Phases 1-6) — SHIPPED 2026-09-23</summary>

- [x] Phase 1: Auth Foundation (5/5 plans) — completed 2026-09-20
- [x] Phase 2: Memory (Day 11) (5/5 plans) — completed 2026-09-20
- [x] Phase 3: Personalization (Day 12) (3/3 plans) — completed 2026-09-20
- [x] Phase 4: Task State Machine (Day 13) (4/4 plans) — completed 2026-09-20
- [x] Phase 5: Invariants (Day 14) (4/4 plans) — completed 2026-09-20
- [x] Phase 6: Controlled Transitions (Day 15) (4/4 plans) — completed 2026-09-21

Full details: [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

<details>
<summary>✅ v2.0 Week 4: MCP Integration (Phases 7-9, 12) — SHIPPED 2026-10-02</summary>

- [x] Phase 7: MCP Connection (Day 16) (6/6 plans) — completed 2026-09-23
- [x] Phase 8: Scheduler (Day 18) (9/9 plans) — completed 2026-09-26
- [x] Phase 9: Auto-rename chats with LLM (Day 21) (6/6 plans) — completed 2026-10-02
- [x] Phase 12: LLM providers section in Settings (Day 21) (6/6 plans) — completed 2026-10-02

Full details: [milestones/v2.0-ROADMAP.md](milestones/v2.0-ROADMAP.md)

</details>

### 🚧 v3.0 Week 5: RAG (In Progress)

- [ ] **Phase 10: Modals close only via x button (Day 21)** — carried over from v2.0; a modal closes only on its 'x'; a backdrop click no longer closes it
- [ ] **Phase 11: Edit and delete long-term memory entries via UI (Day 21)** — carried over from v2.0; "Редактировать" / "Удалить" buttons per long-term memory entry

## Phase Details

### Phase 10: Modals close only via x button (Day 21)

**Goal**: change modal behavior: a modal closes only when its 'x' is clicked; clicking outside the modal (on the backdrop) must not close it
**Branch**: `Day21`
**Depends on**: Phase 8
**Milestone**: v3.0 (carried over from v2.0)
**Promoted from**: backlog 999.5 (2026-10-02)
**Requirements**: TBD
**Plans**: 1 plan
**UI hint**: yes

Plans:

- [ ] 10-01-PLAN.md — Remove backdrop-click and Escape modal closers in app.js, add a source guard test, browser UAT on an isolated copy

### Phase 11: Edit and delete long-term memory entries via UI (Day 21)

**Goal**: the user must be able to edit long-term memory fields through the UI ("Редактировать" and "Удалить" buttons per entry)
**Branch**: `Day21`
**Depends on**: Phase 8
**Milestone**: v3.0 (carried over from v2.0)
**Promoted from**: backlog 999.6 (2026-10-02)
**Requirements**: TBD
**Plans**: 4 plans
**UI hint**: yes

Plans:
**Wave 1**

- [ ] 11-01-PLAN.md — MEMUI requirement IDs, user-scoped update/delete helpers in `agent/memory.py`, `LongTermMemoryUpdate` schema, `PUT`/`DELETE /api/v1/memory/long-term/{entry_id}` (404/409/422, Origin + JSON checks) + pytest (wave 1)
- [ ] 11-02-PLAN.md — Frontend: "Редактировать" / "Удалить" buttons per long-term entry, inline edit form with draft state, confirmed delete, source guard test (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 11-03-PLAN.md — Docs sync (API_SPEC, ARCHITECTURE, TESTING_GUIDE, USER_GUIDE) + full-suite regression gate (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 11-04-PLAN.md — Playwright browser UAT (S1-S11) on the isolated copy at 18000/18001 with an exit-code / result-file gate and a capped fix-and-rerun loop (wave 3)

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Auth Foundation | v1.0 | 5/5 | Complete | 2026-09-20 |
| 2. Memory (Day 11) | v1.0 | 5/5 | Complete | 2026-09-20 |
| 3. Personalization (Day 12) | v1.0 | 3/3 | Complete | 2026-09-20 |
| 4. Task State Machine (Day 13) | v1.0 | 4/4 | Complete | 2026-09-20 |
| 5. Invariants (Day 14) | v1.0 | 4/4 | Complete | 2026-09-20 |
| 6. Controlled Transitions (Day 15) | v1.0 | 4/4 | Complete | 2026-09-21 |
| 7. MCP Connection (Day 16) | v2.0 | 6/6 | Complete   | 2026-09-23 |
| 8. Scheduler (Day 18) | v2.0 | 9/9 | Complete   | 2026-09-26 |
| 9. Auto-rename chats with LLM (Day 21) | v2.0 | 6/6 | Complete   | 2026-10-02 |
| 12. LLM providers section in Settings (Day 21) | v2.0 | 6/6 | Complete   | 2026-10-02 |
| 10. Modals close only via x button (Day 21) | v3.0 | 0/1 | Planned | - |
| 11. Edit and delete long-term memory entries via UI (Day 21) | v3.0 | 0/4 | Planned | - |

## Backlog

> **Deferred to Day 20 (2026-09-26):** items 999.1–999.3 below, plus the remaining Day 16 leftovers: manual interactive Ctrl+C check with `filesystem.exe` connected, and browser/real-model rechecks for quick tasks 260924-1ic, 260924-2n8, 260925-oya, 260925-q0s, 260925-qj5, 260925-qvd. No Day 20 phase exists in the roadmap yet.

### Phase 999.1: Guard merge_merge_request: run only when the user explicitly asks to merge (BACKLOG)

**Goal:** block or require explicit user request for merge_merge_request; on 'сделай MR' the model created MR !1 and merged it unprompted (quick 260926-38j real GitLab run)
**Deferred to:** Day 20
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.2: Descriptive LLM timeout error instead of empty 'LLM error:' (BACKLOG)

**Goal:** llm_stream_timeout yields empty detail and the whole turn (user message) is rolled back even though tools already ran; use type name/'timeout' and keep executed tool results
**Deferred to:** Day 20
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.3: Trim wasted tool rounds and stray second answer after MCP error nudge (BACKLOG)

**Goal:** model burns rounds on list_allowed_directories/get_file_info and commit_files 'update' on missing files; TOOL_ERROR_REMINDER can append a second answer after an already good final answer
**Deferred to:** Day 20
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.7: Harden scheduler LLM tools: gate schedule_task and bind cancel to the task (BACKLOG)

**Goal:** WR-05/WR-06 from the phase 08 code review. `schedule_task` has no intent gate: a prompt injected through MCP/file content can persist a delayed, unattended prompt that later runs with all of the user's MCP tools. The cancel gate (`user_asked_to_cancel`) is a keyword heuristic that passes questions/hypotheticals and does not tie the cancel to the task id. Ideas: require explicit user intent (like the cancel gate) or a confirm step for schedule_task, bind `cancel_scheduled_task` to the id/title the user actually named
**Refs:** 08-REVIEW.md WR-05, WR-06
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.8: Fix DST fall-back fold in cron next-run math (BACKLOG)

**Goal:** WR-04 from the phase 08 code review. `_to_local_naive` in agent/schedule.py drops the datetime fold, so on the fall-back night `next_cron_run` can return a slot in the past and the job refires (or writes a SKIPPED row) every tick for up to an hour. Add a test with a fixed DST zone; cron is computed in machine-local time and stored as UTC
**Refs:** 08-REVIEW.md WR-04
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.9: Re-validate the session on the /ws/events socket (BACKLOG)

**Goal:** WR-07 + IN-05 from the phase 08 code review. `/ws/events` checks the session cookie only at the handshake, so the socket keeps streaming per-user events after logout or session expiry; a binary frame also raises KeyError from `receive_text()`. Re-check the session periodically (e.g. on each ping) and close with 1008 when it is gone; ignore/close on non-text frames
**Refs:** 08-REVIEW.md WR-07, IN-05
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.10: Scheduler code review INFO cleanup (BACKLOG)

**Goal:** IN-01..IN-04, IN-06, IN-07 from the phase 08 code review: naive `created_at.timestamp()` sort in list_scheduled_tasks; duplicated constants/running-run query; unused `RecordingSink.frames`; events UI can show stale state (stale fetch overwrites newer event) and drops keyboard focus on every panel render; headless.py coupled to private agent.ws helpers and lists MCP servers twice; any built-in TimeoutError is reported as the run deadline. Also cosmetic: the run row with the 'с опозданием' chip wraps onto several lines in the narrow sidebar
**Refs:** 08-REVIEW.md IN-01..IN-07; Playwright UAT note in 08-08-SUMMARY.md
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

### Phase 999.11: DeepSeek backend check for auto-title requests (Phase 09 UAT #1) (BACKLOG)

**Goal:** one POST to `https://api.deepseek.com/v1/chat/completions` (model `deepseek-chat`, messages from `agent.titles.build_title_messages`, temperature 0, max_tokens 30, stream false, `reasoning_effort` none; on 400/422 repeat without `reasoning_effort`). Expect HTTP 200, finish_reason `stop`, non-empty short title. Needs `DEEPSEEK_API_KEY` from the environment (paid call). Procedure and command: `.planning/phases/09-auto-rename-chats-with-llm-day-21/09-HUMAN-UAT.md` (test 1). Can be closed together with Phase 12 (DeepSeek model picker).
**Status:** code path delivered in Phase 12; live check blocked: no real DEEPSEEK_API_KEY in `.env` (placeholder only); run `RUN_LIVE_DEEPSEEK=1 pytest tests/test_live_deepseek_title.py -q -rs` once a key is set
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

---
*Roadmap created: 2026-09-19*
*Last updated: 2026-10-02 — Phase 9 planned (4 plans, TITLE-01..06); backlog 999.4/999.5/999.6/999.11 promoted to Phases 9-12 (Day 21)*
