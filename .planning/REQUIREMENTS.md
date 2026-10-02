# Requirements: AiAdventAgentV2 — Week 4: MCP Integration

**Defined:** 2026-09-23
**Core Value:** The agent must demonstrably separate and manage distinct kinds of state, making explicit, inspectable decisions — extended this milestone to external tools reached over the Model Context Protocol.

## v2.0 Requirements

### MCP Connection (Day 16)

- [x] **MCP-01**: User can add, edit and delete MCP server configs (name, command, args) in the Settings UI; configs are stored in SQLite scoped by `user_id`
- [x] **MCP-02**: User can press "Connect" for a server in Settings; the Agent opens a stdio MCP session (initialize handshake) and the UI shows connection status plus serverInfo (name, version, protocol version)
- [x] **MCP-03**: After a successful connection, the UI lists the server's tools (name, description, parameters derived from inputSchema)
- [x] **MCP-04**: Connection failures (nonexistent command path, server exits/crashes, handshake timeout) are reported to the user as clear error messages; the Agent process keeps running
- [x] **MCP-05**: The `mcp` SDK is pinned in `requirements.txt`, and pytest covers connect + list_tools (success and failure paths)
- [x] **MCP-06**: A standalone CLI script `scripts/mcp_list_tools.py <command> [args…]` connects to an MCP server and prints serverInfo and the tool list to the console
- [x] **MCP-F1**: LLM can call MCP tools during a chat turn via the existing tool-call dispatcher (quick task 260924-1ic)

### Scheduler (Day 18)

- [x] **SCHED-01**: User can create a scheduled job (title, prompt, model, schedule type once/interval/cron, optional `max_runs`) through the UI form / REST; jobs are stored in SQLite (`ScheduledTask`) scoped by `user_id`, with server-side validation (Russian error messages)
- [x] **SCHED-02**: The LLM can create, list and cancel jobs through chat tools `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`; a job created from chat keeps `origin_chat_id` (`ON DELETE SET NULL`; deleting the chat keeps the job)
- [x] **SCHED-03**: The Agent runs due jobs in the background (asyncio poll loop started in the agent lifespan, atomic optimistic claim `UPDATE ... WHERE next_run_at = old`); jobs survive Agent restarts
- [x] **SCHED-04**: After downtime a missed job gets exactly one compensating run flagged `is_late`, then `next_run_at` advances to the next future slot; runs left `running` by a dead process are marked `failed` at startup
- [x] **SCHED-05**: A run executes a headless LLM turn (no chat, no WebSocket, no chat lock) with the user's MCP tools + `save_long_term_memory` only, sequential tool calls, `MAX_TOOL_ROUNDS` cap and an overall timeout (`SCHEDULER_RUN_TIMEOUT`, default 120 s); an unavailable model ends the run `failed` with a clear error
- [x] **SCHED-06**: Every run's status, trigger, timings, late flag, final answer, error and tool trace are stored on a `TaskRun` row; nothing is written to any chat
- [x] **SCHED-07**: Overlap policy: a slot arriving while the previous run is still running is recorded as a `skipped` run and never runs in parallel (DB-enforced by a partial unique index); no retries — the next slot fires normally after a failure
- [x] **SCHED-08**: Periodic jobs with `max_runs` auto-complete after N runs; one-shot jobs complete after their run
- [x] **SCHED-09**: Cron expressions (exactly 5 fields) are interpreted in the Agent machine's local timezone; all stored timestamps are UTC and all API/WS timestamps are UTC-aware ISO strings
- [x] **SCHED-10**: REST `/api/v1/scheduler/*` lets the owner list/create/get jobs, pause, resume, run now, cancel (soft, history kept), delete (job + runs), and read run history and a run's full result; all routes are session-authenticated and user-scoped (foreign ids -> 404)
- [x] **SCHED-11**: `cancel_scheduled_task` requires an explicit `task_id` and may cancel only when the user's latest chat message asked for it (tool description + required confirmation flag + code-side intent guard)
- [x] **SCHED-12**: A user-level WebSocket `/ws/events` (origin + session-cookie checks like `/ws/chat`) pushes `run_started` / `run_finished` / `task_updated` / `task_deleted` events only to the owning user; the UI re-syncs over REST on connect and polls while disconnected
- [x] **SCHED-13**: The sidebar `#scheduler-panel` lists jobs with status badges, expandable run history, the full result in a Markdown modal (DOMPurify), a create form, and pause / resume / run now / cancel / delete actions with live updates (per 08-UI-SPEC.md)
- [x] **SCHED-14**: pytest covers schedule math, claim atomicity, catch-up, overlap skip, max_runs, orphan recovery, the headless runner (success / tool round / timeout / model down / disallowed tool), REST scoping, event isolation, the cancel gate and chat-delete SET NULL; the full suite passes

### Chat auto-titling (Day 21)

- [ ] **TITLE-01**: After the first completed Q&A turn of a chat whose title is still the default `New Chat`, the Agent generates a short title (3-8 words, at most ~50 characters, in the user's language) once per chat, using the chat's current model (the model that answered the turn; no separate title-model setting)
- [ ] **TITLE-02**: The title call is a non-blocking extra call that never delays or breaks the turn; it uses temperature 0 and max_tokens ~30, returns plain text (not JSON) and is bounded by a timeout
- [ ] **TITLE-03**: If the title call fails, times out or returns unusable text, the chat gets the fallback title: the truncated first user message
- [ ] **TITLE-04**: A non-default title is never overwritten, including under races (conditional UPDATE on the default title)
- [ ] **TITLE-05**: The title is persisted and pushed as a `chat_title_updated` frame to the owner's `/ws/events` sockets only; the sidebar item and the chat header update live without a reload
- [ ] **TITLE-06**: Prompt-injection hardening: user/assistant text is wrapped in tags and treated as data, tag-breakout attempts are stripped, the model output is sanitized (one line, length cap, no markup), and the title is rendered via `textContent`

### LLM providers (Day 21)

- [x] **PROV-01**: User can add, edit, delete and enable/disable OpenAI-compatible LLM providers (name, base URL, name of the `.env` variable holding the API key) in the Settings section "Провайдеры LLM"; rows are stored in SQLite scoped by `user_id`, foreign ids return 404, names are unique per user (409), the key value is never stored, logged or returned
- [x] **PROV-02**: An "LM Studio" provider (base URL `LM_STUDIO_BASE_URL`, no key) and, when the `DEEPSEEK_API_KEY` variable resolves, a "DeepSeek" provider (`https://api.deepseek.com`, key variable `DEEPSEEK_API_KEY`) are seeded per user, idempotently, and a deleted seeded provider is never re-created
- [x] **PROV-03**: After a provider is saved, and on the per-provider "Проверить" button, the Agent runs `GET {base_url}/v1/models` with the resolved key and the row shows an ok badge with the model count or an error badge with a classified Russian message (missing env variable, bad key 401/403, unreachable, timeout, other HTTP status, malformed response)
- [x] **PROV-04**: The models of every enabled, reachable provider are fetched live, cached in memory per process, and listed in the header model picker (and the scheduler model select) as `<optgroup>` per provider with entries "Provider · model"; disabled or failing providers add no entries and a failing provider only raises a non-blocking toast; LM Studio entries keep the `✓` loaded mark and the confirm-to-load flow
- [x] **PROV-05**: Every LLM call routes through the selected provider via a `provider_id` next to the model id: chat streaming, invariant self-critique, fact extraction, auto-title and headless scheduled runs (`ScheduledTask.provider_id`); a missing `provider_id` (old clients, existing jobs) resolves to the user's seeded LM Studio provider; token counting stays provider-independent
- [x] **PROV-06**: A deleted or disabled provider makes the chat turn fail with a `PROVIDER_UNAVAILABLE` WebSocket error (no user message persisted) and a scheduled run end `failed` with "Провайдер недоступен (удалён или отключён)" (no fallback); the UI falls back to the first available picker entry
- [x] **PROV-07**: The DeepSeek title request is verified through the new routing (live check against `https://api.deepseek.com`), closing backlog 999.11 / Phase 09 UAT test 1

## Future Requirements

Later Week 4 days (not yet announced) — likely candidates:

- **MCP-F2**: Persistent (long-lived) MCP sessions reused across chat turns
- **MCP-F3**: Non-stdio transports (Streamable HTTP / SSE)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Node/npx-based MCP servers (e.g. `@modelcontextprotocol/server-filesystem`) | Project hard constraint: no Node.js/npm; the Go filesystem server is used instead |
| MCP resources/prompts listing | Assignment asks for tools only |
| MCP server auth/OAuth | Local stdio servers need no auth |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| MCP-01 | Phase 7 | Complete |
| MCP-02 | Phase 7 | Complete |
| MCP-03 | Phase 7 | Complete |
| MCP-04 | Phase 7 | Complete |
| MCP-05 | Phase 7 | Complete |
| MCP-06 | Phase 7 | Complete |
| MCP-F1 | Phase 7 (quick 260924-1ic) | Complete |
| SCHED-01 | Phase 8 | Complete |
| SCHED-02 | Phase 8 | Complete |
| SCHED-03 | Phase 8 | Complete |
| SCHED-04 | Phase 8 | Complete |
| SCHED-05 | Phase 8 | Complete |
| SCHED-06 | Phase 8 | Complete |
| SCHED-07 | Phase 8 | Complete |
| SCHED-08 | Phase 8 | Complete |
| SCHED-09 | Phase 8 | Complete |
| SCHED-10 | Phase 8 | Complete |
| SCHED-11 | Phase 8 | Complete |
| SCHED-12 | Phase 8 | Complete |
| SCHED-13 | Phase 8 | Complete |
| SCHED-14 | Phase 8 | Complete |
| TITLE-01 | Phase 9 | Pending |
| TITLE-02 | Phase 9 | Pending |
| TITLE-03 | Phase 9 | Pending |
| TITLE-04 | Phase 9 | Pending |
| TITLE-05 | Phase 9 | Pending |
| TITLE-06 | Phase 9 | Pending |
| PROV-01 | Phase 12 | Complete |
| PROV-02 | Phase 12 | Complete |
| PROV-03 | Phase 12 | Complete |
| PROV-04 | Phase 12 | Complete |
| PROV-05 | Phase 12 | Complete |
| PROV-06 | Phase 12 | Complete |
| PROV-07 | Phase 12 | Complete |

**Coverage:**
- v2.0 requirements: 34 total
- Mapped to phases: 34
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-23*
*Last updated: 2026-10-02 — PROV-01..07 added for Phase 12 (LLM providers, Day 21)*
