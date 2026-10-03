# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- ✅ **v2.0 Week 4: MCP Integration** — Phases 7-9, 12 (shipped 2026-10-02)
- 🚧 **v3.0 Week 5: RAG** — Phases 10-11 (carried over), 13-17 (in progress)

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

- [x] **Phase 10: Modals close only via x button (Day 21)** — carried over from v2.0; a modal closes only on its 'x'; a backdrop click no longer closes it
 (completed 2026-10-02)

- [ ] **Phase 11: Edit and delete long-term memory entries via UI (Day 21)** — carried over from v2.0; "Редактировать" / "Удалить" buttons per long-term memory entry
- [x] **Phase 13: Knowledge base indexing (Day 21)** — upload PDF/TXT/MD, chunk (fixed or structural), embed via LM Studio, persist FAISS + SQLite, background indexing with live progress (completed 2026-10-03)
- [ ] **Phase 14: First RAG query (Day 22)** — attach a KB to a chat, toggle RAG, retrieve top-K chunks into the LLM request, show sources, frozen 10-question eval and Day22 report
- [ ] **Phase 15: Reranking and filtering (Day 23)** — two-stage retrieval with threshold, lexical/LLM rerank, hybrid FTS5, query rewrite, "Детали поиска", Day23 report
- [ ] **Phase 16: Citations and anti-hallucination (Day 24)** — sources and verified quotes on every answer, code-enforced "не знаю" with a clarifying question
- [ ] **Phase 17: Mini-chat with RAG and task memory (Day 25)** — the existing chat as RAG mini-chat with per-chat task memory, two long scripted scenarios, Day25 report

## Phase Details

### Phase 10: Modals close only via x button (Day 21)

**Goal**: change modal behavior: a modal closes only when its 'x' is clicked; clicking outside the modal (on the backdrop) must not close it
**Branch**: `Day21`
**Depends on**: Phase 8
**Milestone**: v3.0 (carried over from v2.0)
**Promoted from**: backlog 999.5 (2026-10-02)
**Requirements**: MODAL-01
**Plans**: 1 plan
**UI hint**: yes

Plans:

- [x] 10-01-PLAN.md — Remove backdrop-click and Escape modal closers in app.js, add a source guard test, browser UAT on an isolated copy

### Phase 11: Edit and delete long-term memory entries via UI (Day 21)

**Goal**: the user must be able to edit long-term memory fields through the UI ("Редактировать" and "Удалить" buttons per entry)
**Branch**: `Day21`
**Depends on**: Phase 8
**Milestone**: v3.0 (carried over from v2.0)
**Promoted from**: backlog 999.6 (2026-10-02)
**Assumptions (2026-10-02, adopted from 11-RESEARCH.md; planned without CONTEXT.md, UI-SPEC.md or AI-SPEC.md)**: both `key` and `value` are editable; editing is inline in the sidebar memory panel (no modal); delete is confirmed with the native `confirm()`; no live cross-tab sync; the new PUT/DELETE routes check Origin and JSON content type.
**Requirements**: MEMUI-01, MEMUI-02, MEMUI-03, MEMUI-04, MEMUI-05, MEMUI-06
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

### Phase 13: Knowledge base indexing (Day 21)

**Goal**: users can build a knowledge base from PDF/TXT/MD files, choosing a chunking strategy and embedding model, and get a persisted, searchable FAISS + SQLite index with live indexing progress
**Branch**: `Day21`
**Depends on**: Phase 10 (the KB modal holds a file selection and must close only via ×)
**Requirements**: KB-01, KB-02, KB-03, KB-04, KB-05, KB-06, KB-07, KB-08, KB-09, KB-10, KB-11
**Success Criteria** (what must be TRUE):

  1. User opens "Добавить" in the sidebar "База знаний" block, fills the modal (name, files, chunking strategy, size/overlap, embedding model) and clicks "Индексировать"; the KB appears in the list with name, status and file/chunk counts
  2. Both PDFs from `C:\Projects\RAG` (ФЗ-196, КоАП РФ) index successfully with each chunking strategy; a scanned PDF without a text layer fails with a readable message and invalid size/overlap is rejected in Russian
  3. While indexing runs, the UI shows live status and progress (queued / x of y / ready / failed) and the Agent keeps passing health checks; a job interrupted by an Agent restart shows as failed
  4. A "тест поиска" query against a ready KB returns top chunks with scores and metadata (source, section, chunk_id)
  5. Deleting a KB removes its rows, on-disk index and uploaded files; another user's KB is never visible (404)

**Plans**: 8 plans
**UI hint**: yes
**Research flag**: needs deeper research (embedding round trip and per-model prefixes, КоАП header/footer patterns, VRAM co-loading with the chat model); start with a short spike

Plans:

**Wave 1**

- [x] 13-01-PLAN.md — Foundation: pinned RAG deps, KB config keys, KnowledgeBase/KbDocument/KbChunk tables, FAISS bytes storage helper, KB state, test isolation
- [x] 13-02-PLAN.md — KB limits module + pure chunkers (fixed with validation, structural cascade with breadcrumbs and 2000-char sub-split)
- [x] 13-03-PLAN.md — PyMuPDF/TXT/MD loaders with header/footer/annotation cleaning, scan detection, golden tests on real КоАП/ФЗ-196 pages
- [x] 13-07-PLAN.md — Sidebar «База знаний» block, create modal, test-search modal, live progress, chat picker hides embeddings

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 13-04-PLAN.md — Embeddings client with D-24 identity guard, explicit load, batching, prefixes; additive `type` on provider models

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 13-05-PLAN.md — Background indexing job (all-or-nothing), delete with cancel, orphan recovery, kb_progress events, lifespan wiring

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 13-06-PLAN.md — KB REST API (multipart 202 create with caps/dedupe, list/get/delete, search, embedding models/check) + search service

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 13-08-PLAN.md — Real-PDF golden tests, Playwright E2E on isolated copy (18000/18001) with both PDFs and strategies, docs sync

### Phase 14: First RAG query (Day 22)

**Goal**: users can attach a knowledge base to a chat and get answers grounded in retrieved chunks, with the sources visible, and compare answers with and without RAG on a frozen control set
**Branch**: `Day22`
**Depends on**: Phase 13
**Requirements**: RAG-01, RAG-02, RAG-03, RAG-04, RAG-05, RAG-06, RAG-07, RAG-08
**Success Criteria** (what must be TRUE):

  1. User attaches a KB to a chat and switches between "без RAG" and "с RAG"; the current mode and KB are visibly indicated in the chat
  2. With RAG on, an answer is based on the top-K retrieved chunks and shows its sources (file, section, chunk_id, score) under the message; the stored user message remains the raw question
  3. If the embedding model is unavailable or the KB was deleted, the turn still answers without RAG and shows a visible warning; a large RAG block never deletes the user message
  4. `scripts/rag_eval.py` runs the frozen 10-question control set (including out-of-corpus questions) and `Day22_report.md` compares no-RAG vs RAG answers and nomic vs bge-m3 embeddings on hit@k (giga is not available as an embedder — see Phase 13 spike / D-15)

**Plans**: 8 plans
**UI hint**: yes
**Research flag**: standard patterns; decide the retrieval result shape, `rag_sources` storage and eval fixture here

Plans:

**Wave 1**

- [ ] 14-01-PLAN.md — ChatRagConfig + Message.rag_sources migration, dim-mismatch error, agent/rag.py (retrieve, failure mapping, budget, block, merge, payload)
- [ ] 14-02-PLAN.md — Draft, user-approve and freeze the 10-question control set fixture (checkpoint)

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 14-03-PLAN.md — REST: GET/PUT /chats/{id}/rag, chunk snippet route, rag_sources in the chat tree
- [ ] 14-04-PLAN.md — WS turn: fail-soft prepare_rag_turn, outbound-only merge, rag_sources persistence, done.rag
- [ ] 14-05-PLAN.md — scripts/rag_eval.py (build-kbs, run, hit@k, tables) with fake-backed tests

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 14-06-PLAN.md — UI: header toggle/KB select/K/badge, per-answer mode label, warning line, Источники block
- [ ] 14-07-PLAN.md — Live eval (nomic vs bge-m3, no-RAG vs RAG), verdicts, Day22_report.md (checkpoint)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 14-08-PLAN.md — Playwright E2E on the isolated copy (18000/18001) and docs sync

### Phase 15: Reranking and filtering (Day 23)

**Goal**: retrieval quality improves through two-stage candidate selection, a calibrated relevance cut-off, optional rerankers and query rewrite, with every step inspectable
**Branch**: `Day23`
**Depends on**: Phase 14
**Requirements**: RANK-01, RANK-02, RANK-03, RANK-04, RANK-05, RANK-06, RANK-07, RANK-08, RANK-09
**Success Criteria** (what must be TRUE):

  1. User can configure candidate top-K, final top-K and the similarity threshold per chat, and low-scoring chunks are cut before reaching the LLM
  2. User can enable the lexical reranker, the LLM reranker, hybrid FTS5 retrieval and query rewrite independently; rewrite falls back to the original question on bad output
  3. A collapsible "Детали поиска" block under each RAG answer shows the (rewritten) query, candidates with scores, what was cut and why, and the final chunks
  4. The threshold is calibrated per embedding model on the control set, and `Day23_report.md` compares no filter vs filter, each reranker and rewrite (optional LLM-judge column, manual verdict primary)

**Plans**: TBD
**UI hint**: yes
**Research flag**: needs deeper research (empirical threshold calibration, rewrite drift on a 9B local model)

### Phase 16: Citations and anti-hallucination (Day 24)

**Goal**: every RAG answer carries verifiable sources and quotes, and the assistant says "не знаю" instead of guessing when retrieval is not relevant enough
**Branch**: `Day24`
**Depends on**: Phase 15
**Requirements**: CITE-01, CITE-02, CITE-03, CITE-04
**Success Criteria** (what must be TRUE):

  1. Each RAG answer shows the answer text, a list of sources (source + section / chunk_id) and quotes taken from the retrieved chunks
  2. Each quote is marked verified or unverified by a server-side substring check against the cited chunk, and sources are rendered from chunk metadata, not from model text
  3. An out-of-corpus question gets "не знаю" plus a clarifying question, enforced in code when best relevance is below the threshold
  4. The Day 24 report section records, per control question, sources present, quotes present, meaning matches quotes, and correct "не знаю" on out-of-corpus questions

**Plans**: TBD
**UI hint**: yes
**Research flag**: needs deeper research (local-model compliance with `[n]` citations and verbatim quotes)

### Phase 17: Mini-chat with RAG and task memory (Day 25)

**Goal**: the existing chat works as a RAG mini-chat that keeps the dialog goal, clarifications and constraints in task memory and answers every turn with sources
**Branch**: `Day25`
**Depends on**: Phase 16
**Requirements**: RCHAT-01, RCHAT-02, RCHAT-03, RCHAT-04, RCHAT-05
**Success Criteria** (what must be TRUE):

  1. In a RAG chat, history is kept, retrieval runs on every new question and every answer shows its sources
  2. Task memory (goal, clarified points, constraints/terms) updates after each turn and is visible in the UI
  3. A follow-up question that depends on earlier turns retrieves correctly because task memory and recent history feed the query rewrite and the system prompt
  4. Two scripted 10-15 message scenarios run end to end with the goal kept and sources on every turn, documented in `Day25_report.md`

**Plans**: TBD
**UI hint**: yes
**Research flag**: standard patterns; confirm `dialog_state` rendering in the memory panel during planning

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
| 11. Edit and delete long-term memory entries via UI (Day 21) | v3.0 | 0/4 | Planned | - |
| 13. Knowledge base indexing (Day 21) | v3.0 | 8/8 | Complete   | 2026-10-03 |
| 14. First RAG query (Day 22) | v3.0 | 0/TBD | Not started | - |
| 15. Reranking and filtering (Day 23) | v3.0 | 0/TBD | Not started | - |
| 16. Citations and anti-hallucination (Day 24) | v3.0 | 0/TBD | Not started | - |
| 17. Mini-chat with RAG and task memory (Day 25) | v3.0 | 0/TBD | Not started | - |

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
*Last updated: 2026-10-03 — v3.0 roadmap: Phases 13-17 (RAG, Days 21-25) added; earlier: 2026-10-02 — Phase 9 planned (4 plans, TITLE-01..06); backlog 999.4/999.5/999.6/999.11 promoted to Phases 9-12 (Day 21)*
