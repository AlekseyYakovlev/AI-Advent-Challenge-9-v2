# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- ✅ **v2.0 Week 4: MCP Integration** — Phases 7-9, 12 (shipped 2026-10-02)
- ✅ **v3.0 Week 5: RAG** — Phases 10-11, 13-17 (shipped 2026-10-10)
- 🚧 **v4.0 Week 6: Local LLM as a Private Service** — Phases 18-20 (in progress)

## Phases

- [ ] **Phase 18: LAN exposure (Day 30)** - App can be bound to the home network and used from another machine's browser
- [ ] **Phase 19: API keys and HTTP chat (Day 30)** - Per-user API keys and a plain HTTP chat endpoint
- [ ] **Phase 20: Limits, verification script and report (Day 30)** - Rate/login/concurrency limits, network check script, live LAN report

### Phase 18: LAN exposure (Day 30)

**Goal**: Operator can expose the whole app on the home network with one setting, and users on other LAN machines can log in and chat in the browser, while foreign sites remain blocked
**Depends on**: Nothing (first phase of v4.0; builds on shipped v1.0-v3.0 code)
**Requirements**: NET-01, NET-02, NET-03, NET-04, NET-05, NET-06
**Success Criteria** (what must be TRUE):

  1. With `BIND_HOST=0.0.0.0` in `.env`, both UI (8000) and Agent (8001) listen on all interfaces; with `127.0.0.1` or unset they stay loopback-only
  2. Starting with any other `BIND_HOST` value exits immediately with a clear explanation
  3. From a browser on another LAN machine the user opens the UI, logs in and gets a streamed model reply over WebSocket, with no IP lists configured
  4. A request or WebSocket connection carrying a foreign site's Origin is still rejected
  5. In LAN mode `run.py` prints the reachable network addresses and an exposure warning (loopback output unchanged), and the docs explain LAN mode and opening ports 8000/8001 in Windows Firewall

**Plans:** 7 plans

Plans:
**Wave 1**

- [x] 18-01-PLAN.md — BIND_HOST setting, validation with exit code 2, supervisor host parameter (wave 1)
- [x] 18-02-PLAN.md — shared host-match Origin rule wired into REST CORS, mutation guard and WebSocket check (wave 1)
- [x] 18-03-PLAN.md — Settings "Сеть" section and HTTP-safe clipboard copy (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 18-04-PLAN.md — remove the duplicated origin list, repoint tests, fix isolated-copy e2e scripts (wave 2)
- [x] 18-05-PLAN.md — bind both servers to BIND_HOST, LAN banner, address list, GET /api/v1/network (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 18-06-PLAN.md — docs: LAN mode, Windows Firewall, Origin rule, endpoint, tests (wave 3)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 18-07-PLAN.md — isolated LAN-mode browser check and manual second-device UAT (wave 4, has checkpoint)

**UI hint**: yes

### Phase 19: API keys and HTTP chat (Day 30)

**Goal**: A user can create API keys and use them to chat with the model over plain HTTP from any client, with the conversation stored in the normal chat tree
**Depends on**: Phase 18
**Requirements**: KEY-01, KEY-02, KEY-03, KEY-04, KEY-05, HCHAT-01, HCHAT-02, HCHAT-03, HCHAT-04
**Success Criteria** (what must be TRUE):

  1. In Settings the user creates a named API key, sees the full key exactly once, and the list afterwards shows only name, prefix, creation date and last-used date
  2. The user revokes a key and the next request with it gets HTTP 401 immediately; an invalid key also gets 401
  3. `curl` with `Authorization: Bearer <key>` calls REST routes as the key owner (same ownership checks as the cookie), while WebSocket stays cookie-only
  4. `POST` of text and model to an existing chat returns the whole reply as one JSON body with token statistics, and the exchange appears in the UI with the same settings, memory, compression and RAG as a WebSocket turn
  5. Context overflow (`CONTEXT_OVERFLOW`), LM Studio not running and chat busy each return a clear HTTP status, and `docs/API_SPEC.md` documents the endpoint with a full curl scenario (create key, create chat, send message)

**Plans**: 6 plans

Plans:
**Wave 1**

- [x] 19-01-PLAN.md — ApiKey table, Bearer branch in get_current_user, cookie-only /api/v1/api-keys routes, tests (wave 1)
- [x] 19-02-PLAN.md — agent/ws.py: extract _run_chat_turn, leaf-restore bug fix on failed turns, LLM_ERROR cause (wave 1)
- [x] 19-03-PLAN.md — Settings "API ключи" section, one-time key modal with curl example, chat_updated handling (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 19-04-PLAN.md — POST /api/v1/chats/{chat_id}/messages: shared turn, kb_id selection, status mapping, event, tests (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 19-05-PLAN.md — isolated-copy browser + HTTP e2e script for keys and HTTP chat (wave 3)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 19-06-PLAN.md — API_SPEC with full curl scenario, TESTING_GUIDE, ARCHITECTURE, USER_GUIDE, KEY-04 rewording (wave 4)

**UI hint**: yes

### Phase 20: Limits, verification script and report (Day 30)

**Goal**: The service is protected against abuse and overload, and a live run from a second LAN machine proves it works as a private service
**Depends on**: Phase 19
**Requirements**: LIM-01, LIM-02, LIM-03, LIM-04, LIM-05, VER-01, VER-02, VER-03, VER-04, VER-05, VER-06
**Success Criteria** (what must be TRUE):

  1. The per-chat messages-per-minute limit is set in `.env` and applies to both WebSocket and HTTP chat; exceeding it over HTTP returns 429 with `Retry-After`
  2. After several consecutive wrong passwords login is temporarily blocked (threshold and duration configurable); `/debug/routes` requires an authenticated user while `/health` stays open
  3. With the concurrency cap set, excess parallel generations wait in a queue and all complete successfully
  4. Running the verification script with a server address and API key reports reachability/auth, results of N parallel real-model requests (successes, errors, timings), and demonstrates the rate limit and context overflow
  5. `Day30_report.md` records a live run from a second LAN machine plus a manual UI chat from another device; pytest covers bind host, Origin, API keys, HTTP chat and limits with matching `docs/TESTING_GUIDE.md` sections

**Plans**: TBD

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
| 18. LAN exposure (Day 30) | v4.0 | 6/7 | In Progress|  |
| 19. API keys and HTTP chat (Day 30) | v4.0 | 6/6 | Complete   | 2026-10-12 |
| 20. Limits, verification script and report (Day 30) | v4.0 | 0/0 | Not started | - |

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
*Last updated: 2026-10-12 — v4.0 roadmap: Phases 18-20 (Day 30); earlier: 2026-10-03 — v3.0 roadmap: Phases 13-17 (RAG, Days 21-25) added; earlier: 2026-10-02 — Phase 9 planned (4 plans, TITLE-01..06); backlog 999.4/999.5/999.6/999.11 promoted to Phases 9-12 (Day 21)*
