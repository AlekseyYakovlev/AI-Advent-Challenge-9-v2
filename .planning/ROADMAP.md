# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- ✅ **v2.0 Week 4: MCP Integration** — Phases 7-9, 12 (shipped 2026-10-02)
- ✅ **v3.0 Week 5: RAG** — Phases 10-11, 13-17 (shipped 2026-10-10)
- ✅ **v4.0 Week 6: Local LLM as a Private Service** — Phases 18-20 (shipped 2026-10-12)
- 🚧 **v5.0 Week 7: Pipeline** — Phases 21-23 (in progress)

## Phases

- [ ] **Phase 21: Project docs in RAG (Day 31)** - README plus folder upload, accumulated selections and addition to an existing knowledge base
- [ ] **Phase 22: Project MCP servers (Day 31)** - Own read-only git MCP server and the official GitHub server connected read-only
- [ ] **Phase 23: /help command, verification and report (Day 31)** - `/help` in the chat and over HTTP, tests, end-to-end script and `Day31_report.md`
- [ ] **Phase 24: Automated PR code review (Day 32)** - Pipeline on every Pull Request: diff plus changed files, RAG over docs and code, review text

### Phase 21: Project docs in RAG (Day 31)

**Goal**: The user can load the project's documentation (README and the `docs/` folder) into one knowledge base in a few clicks and keep it current by adding changed files
**Depends on**: Nothing (first phase of v5.0; builds on the shipped v3.0 knowledge base code)
**Requirements**: DOC-01, KBUP-01, KBUP-02, KBUP-03, KBUP-04, KBUP-05, KBUP-06
**Success Criteria** (what must be TRUE):

  1. `README.md` exists at the repository root and describes the purpose, the directory structure, how to run the app, the main `.env` settings and the documents in `docs/`
  2. In the knowledge base form the user picks `README.md`, then the `docs/` folder, sees both in one pending list with the count of skipped unsupported files, and creates one base from the whole list
  3. Sources in the file list, in test search and under answers show relative paths such as `docs/API_SPEC.md`, and two files with the same base name in different folders stay separate
  4. The user adds a new file or folder to a ready base and only the added files are indexed while earlier fragments stay searchable
  5. Adding a file whose source name already exists replaces its fragments, and exceeding the file-count or size limit is rejected with a clear message

**Plans**: 7 plans

Plans:
**Wave 1**

- [x] 21-01-PLAN.md — Source-name identity: drop the sha unique constraint with a chunk-preserving migration, server-side relative-path source names, MAX_FILES = 200, GET /api/v1/kb/limits
- [x] 21-02-PLAN.md — README.md at the repository root with a guard test

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 21-03-PLAN.md — Background addition job: non-destructive add/replace on a ready base, patched FAISS index, summary frame, restart recovery
- [x] 21-05-PLAN.md — Create form: folder choice, accumulating pending list, taken/skipped report, limits from the server, upload under source names

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 21-04-PLAN.md — POST /api/v1/kb/{kb_id}/files: staging, added/replaced/unchanged classification, atomic claim

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 21-06-PLAN.md — Add-files modal and card states (progress, summary, failure) for a ready base

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 21-07-PLAN.md — Documents and README updated as built, old browser script repaired, isolated-copy browser check of folder upload and addition

**UI hint**: yes

### Phase 22: Project MCP servers (Day 31)

**Goal**: The assistant can read the live state of the project's working copy through MCP, and the GitHub server is connected as the base for Pull Request work
**Depends on**: Nothing (independent of Phase 21; builds on the shipped v2.0 MCP client)
**Requirements**: PGIT-01, PGIT-02, PGIT-03, PGIT-04, GHUB-01, GHUB-02
**Success Criteria** (what must be TRUE):

  1. `python scripts/mcp_list_tools.py` against the new server lists the read-only tools: current branch, status, diff, tracked files, recent commits
  2. A path outside the repository root or an argument that starts with `-` is rejected with a clear error, and a long diff comes back truncated with an explicit marker
  3. The user adds the server in Settings with the values from the documentation, asks in a chat which branch the project is on, and gets the branch that is checked out at that moment, with the tool call visible
  4. With the documented configuration the app connects to `github-mcp-server` in read-only mode and lists its tools; the token is masked after saving

**Plans**: 6 plans

Plans:
**Wave 1**

- [x] 22-01-PLAN.md — Git server core: launch arguments, fail-fast startup, process runner, path gate, ref gate, truncation helpers
- [x] 22-02-PLAN.md — Real-binary pytest check for `github-mcp-server` (read-only) and always-on token masking test

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 22-03-PLAN.md — The five read-only git tools with behaviour, stdio and app-path tests

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 22-04-PLAN.md — Documentation of both servers (form values, install, token) and a docs guard test
- [x] 22-05-PLAN.md — PGIT-04 browser script on the isolated copy and the recorded local-git checks

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 22-06-PLAN.md — User installs the GitHub server and token; GHUB-02 check run and recorded; final full-suite run recorded

### Phase 23: /help command, verification and report (Day 31)

**Goal**: A developer asks a question about the project with `/help` and gets an answer built from the project documentation and the live git state, and a recorded run on a real model proves it
**Depends on**: Phase 21, Phase 22
**Requirements**: HELP-01, HELP-02, HELP-03, HELP-04, HELP-05, HELP-06, HELP-07, VER-07, VER-08, VER-09, VER-10
**Success Criteria** (what must be TRUE):

  1. The user marks a knowledge base as the project documentation base, and `/help <question>` in any chat answers from it with sources without changing that chat's RAG settings
  2. `/help` with no question shows a usage text, and `/help <question>` with no marked or ready base explains what to do; neither calls the model
  3. During `/help` the model can call the project git tools, and the call is visible in the chat; the exchange survives a page reload
  4. The same `/help` message sent to the HTTP chat endpoint with an API key returns the answer, the sources and the tool calls in one JSON body
  5. `Day31_report.md` records 8-10 control questions about the project run through `/help` on a real model, and pytest, the isolated-copy end-to-end script and the three documents cover everything built in this milestone

**Plans**: 8 plans

Plans:

**Wave 1**

- [x] 23-01-PLAN.md — HelpConfig table, /help building blocks (recognition, notices, tool selection) and the config REST routes

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 23-02-PLAN.md — /help retrieval without the gate, tool-aware quote verification, shared turn-middle helper
- [x] 23-03-PLAN.md — Settings section, knowledge base badge, answer label and temporary notice in the browser

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 23-04-PLAN.md — /help wired into the chat turn (WebSocket) and the HTTP chat endpoint, with tests

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 23-05-PLAN.md — Control-run driver, threshold re-measurement, prompt spike on both models, frozen control set

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 23-06-PLAN.md — /help in API_SPEC, ARCHITECTURE, USER_GUIDE, TESTING_GUIDE and the documentation guard test
- [ ] 23-07-PLAN.md — Isolated-copy end-to-end script (folder upload, git server, /help in browser and over HTTP) and its recorded run

**Wave 6** *(blocked on Wave 5 completion)*

- [ ] 23-08-PLAN.md — Control run on two models, DeepSeek judge, full test-suite record and Day31_report.md

**UI hint**: yes

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

<details>
<summary>✅ v4.0 Week 6: Local LLM as a Private Service (Phases 18-20) — SHIPPED 2026-10-12</summary>

- [x] Phase 18: LAN exposure (Day 30) (7/7 plans) — completed 2026-10-12
- [x] Phase 19: API keys and HTTP chat (Day 30) (6/6 plans) — completed 2026-10-12
- [x] Phase 20: Limits, verification script and report (Day 30) (8/8 plans) — completed 2026-10-12

Full details: [milestones/v4.0-ROADMAP.md](milestones/v4.0-ROADMAP.md)

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
| 18. LAN exposure (Day 30) | v4.0 | 7/7 | Complete    | 2026-10-12 |
| 19. API keys and HTTP chat (Day 30) | v4.0 | 6/6 | Complete    | 2026-10-12 |
| 20. Limits, verification script and report (Day 30) | v4.0 | 8/8 | Complete    | 2026-10-12 |
| 21. Project docs in RAG (Day 31) | v5.0 | 7/7 | Complete   | 2026-10-13 |
| 22. Project MCP servers (Day 31) | v5.0 | 6/6 | Complete   | 2026-10-13 |
| 23. /help command, verification and report (Day 31) | v5.0 | 6/8 | In Progress|  |

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

### Phase 24: Automated PR code review (Day 32)

**Goal:** A pipeline (for example a GitHub Action) runs on every Pull Request: the assistant receives the PR diff and the changed files, uses RAG over the project documentation and code, and returns a review text with potential bugs, architectural problems and recommendations
**Requirements**: TBD
**Depends on:** Phase 21, Phase 22 (project docs knowledge base and the GitHub MCP server); independent of Phase 23
**Plans:** 14 plans

Plans:
**Wave 1**

- [ ] 24-01-PLAN.md — Day32 branch cut-point decision; register REV-01..REV-08

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 24-02-PLAN.md — ReviewConfig / ReviewCodeIndex tables and the pure code chunker
- [ ] 24-03-PLAN.md — Live GitHub MCP check and the in-code PR reader (no 20 000-char cut)
- [ ] 24-04-PLAN.md — GitHub Actions workflow and the thin caller script
- [ ] 24-11-PLAN.md — DeepSeek judge and report table renderer

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 24-05-PLAN.md — Git-based incremental code indexer over the base branch
- [ ] 24-06-PLAN.md — Review core: budget, split, per-base retrieval, model calls, merge

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 24-07-PLAN.md — Review route, settings routes and router wiring

**Wave 5** *(blocked on Wave 4 completion)*

- [ ] 24-08-PLAN.md — «Ревью Pull Request» Settings section (UI-SPEC S1)
- [ ] 24-10-PLAN.md — Frozen control set of test PRs and the control-run script

**Wave 6** *(blocked on Wave 5 completion)*

- [ ] 24-09-PLAN.md — Docs: API spec, architecture, user guide with safe runner config, testing guide

**Wave 7** *(blocked on Wave 6 completion)*

- [ ] 24-12-PLAN.md — Runner security decision, manual setup, push Day32, real smoke PR

**Wave 8** *(blocked on Wave 7 completion)*

- [ ] 24-13-PLAN.md — Control run on the local model and on DeepSeek

**Wave 9** *(blocked on Wave 8 completion)*

- [ ] 24-14-PLAN.md — Judge both runs, Day32_report.md, close test PRs

---
*Roadmap created: 2026-09-19*
*Last updated: 2026-10-13 — v5.0 roadmap: Phases 21-23 (Day 31); earlier: 2026-10-12 — v4.0 shipped and archived; earlier: 2026-10-12 — v4.0 roadmap: Phases 18-20 (Day 30); earlier: 2026-10-03 — v3.0 roadmap: Phases 13-17 (RAG, Days 21-25) added; earlier: 2026-10-02 — Phase 9 planned (4 plans, TITLE-01..06); backlog 999.4/999.5/999.6/999.11 promoted to Phases 9-12 (Day 21)*
