# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- ✅ **v2.0 Week 4: MCP Integration** — Phases 7-9, 12 (shipped 2026-10-02)
- ✅ **v3.0 Week 5: RAG** — Phases 10-11, 13-17 (shipped 2026-10-10)

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

<details>
<summary>✅ v3.0 Week 5: RAG (Phases 10-11, 13-17) — SHIPPED 2026-10-10</summary>

- [x] Phase 10: Modals close only via x button (Day 21) (1/1 plans) — completed 2026-10-02
- [x] Phase 11: Edit and delete long-term memory entries via UI (Day 21) (4/4 plans) — completed 2026-10-03
- [x] Phase 13: Knowledge base indexing (Day 21) (8/8 plans) — completed 2026-10-03
- [x] Phase 14: First RAG query (Day 22) (8/8 plans) — completed 2026-10-03
- [x] Phase 15: Reranking and filtering (Day 23) (13/13 plans) — completed 2026-10-03
- [x] Phase 16: Citations and anti-hallucination (Day 24) (8/8 plans) — completed 2026-10-04
- [x] Phase 17: Mini-chat with RAG and task memory (Day 25) (11/11 plans) — completed 2026-10-10

Full details: [milestones/v3.0-ROADMAP.md](milestones/v3.0-ROADMAP.md)

</details>

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
| 10. Modals close only via x button (Day 21) | v3.0 | 1/1 | Complete   | 2026-10-02 |
| 11. Edit and delete long-term memory entries via UI (Day 21) | v3.0 | 4/4 | Complete    | 2026-10-03 |
| 13. Knowledge base indexing (Day 21) | v3.0 | 8/8 | Complete   | 2026-10-03 |
| 14. First RAG query (Day 22) | v3.0 | 8/8 | Complete    | 2026-10-03 |
| 15. Reranking and filtering (Day 23) | v3.0 | 13/13 | Complete    | 2026-10-03 |
| 16. Citations and anti-hallucination (Day 24) | v3.0 | 8/8 | Complete    | 2026-10-04 |
| 17. Mini-chat with RAG and task memory (Day 25) | v3.0 | 11/11 | Complete    | 2026-10-10 |

## Backlog

> **Deferred to Day 20 (2026-09-26):** the remaining Day 16 leftovers: manual interactive Ctrl+C check with `filesystem.exe` connected, and browser/real-model rechecks for quick tasks 260924-1ic, 260924-2n8, 260925-oya, 260925-q0s, 260925-qj5, 260925-qvd. No Day 20 phase exists in the roadmap yet.

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

### Phase 999.17: Phase 16 review info items IN-01..IN-03 (BACKLOG)

**Goal:** (IN-01 fixed in quick 261006-l4o.) IN-02 bare empty «Цитаты:» heading stays in stored content when a «не знаю» reply ends with it (`parse_tail`, `agent/rag_cite.py:229-268`); IN-03 new `build_rag_payload` quote parameters are unused because `finalize_rag_turn` merges `payload_fields()` by hand (`agent/rag.py:268-299`, `agent/rag_turn.py:266-294`). Full report: `.planning/phases/16-citations-and-anti-hallucination-day-24/16-REVIEW.md`.
**Requirements:** TBD
**Plans:** 0 plans

Plans:

- [ ] TBD (promote with /bm:review-backlog when ready)

---
*Roadmap created: 2026-09-19*
*Last updated: 2026-10-03 — v3.0 roadmap: Phases 13-17 (RAG, Days 21-25) added; earlier: 2026-10-02 — Phase 9 planned (4 plans, TITLE-01..06); backlog 999.4/999.5/999.6/999.11 promoted to Phases 9-12 (Day 21)*
