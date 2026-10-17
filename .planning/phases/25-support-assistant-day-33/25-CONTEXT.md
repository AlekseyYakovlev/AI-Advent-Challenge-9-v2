# Phase 25: Support assistant (Day 33) - Context

**Gathered:** 2026-10-17
**Status:** Ready for planning

<domain>
## Phase Boundary

A mini support service inside the app: a support agent (the logged-in app user) asks a product question with a `/support` command, and the assistant answers from the product documentation knowledge base (existing RAG) together with the data of a specific user and ticket, taken from an own read-only stdio MCP server over a JSON file of users and tickets. The headline example: «Почему не работает авторизация?» about a given ticket is answered with that ticket's data (status, error text, the client's version and plan) plus the matching FAQ section. In scope: the `/support` command in the chat (WebSocket) and over the plain HTTP chat endpoint; its section in Settings; the support MCP server in `mcp_servers/`; a seed JSON file; a new `docs/FAQ.md` about this app; pytest coverage and an isolated-copy end-to-end script; documentation updates. Branch `Day33`. The course deliverable is a video plus code; the video is recorded by the user.

The roadmap has no requirement IDs for this phase yet; the planner defines them (suggested prefix `SUP-`) from the decisions below.

Out of scope here: write tools on the support server (creating or updating tickets), mapping app accounts to support clients, a separate support page or window, a control-question report (`Day33_report.md`) and a DeepSeek judge, per-tool MCP switches (MCPS-01), any change to `/help` behaviour.

</domain>

<decisions>
## Implementation Decisions

### Entry point
- **D-01:** The assistant is reached through a new slash command `/support <question>`, in any chat over WebSocket and over the plain HTTP chat endpoint, modelled on `/help` (Phase 23): the same kind of turn, the ordinary tool-call cards, sources and verified quotes under the answer, a label above the answer, and the same answer shape in the HTTP JSON body.
- **D-02:** `/support` and `/help` share one generalised "command turn" mechanism (recognition, notices without a model call, tool selection, warnings, payload on the assistant message), refactored out of `agent/help_command.py`. Each command keeps its own system prompt, its own label and its own settings. `/help` must behave exactly as today; its existing tests stay green unchanged.
- **D-03:** `/support` has its own section in Settings, next to «Справка по проекту (/help)»: search settings (the same field set as `/help`: top-K, candidate count, threshold, the four stage switches), the MCP server checkboxes (whole servers) and the three built-in tool checkboxes. Default tool choice: only the support MCP server is on; all other servers and the built-in groups are off, so a default `/support` turn changes nothing besides adding its two messages.
- **D-04:** Knowledge base: `/support` searches **the same base that is marked for `/help`** (README + `docs/`, which will include the new `docs/FAQ.md`). There is no second base mark. When no base is marked or it is not ready, `/support` gives the same kind of service notice as `/help` (not stored, no model call), pointing to the `/help` section where the base is chosen.
- **D-05:** Conversation: a `/support` turn sends the previous `/support` question/answer pairs of the same chat (along the current branch) to the model, so the ticket from the first question stays the topic of follow-up questions («а что ещё можно попробовать?»). Ordinary messages and `/help` pairs are not sent. The stored `/support` messages are ordinary messages for later ordinary turns (as `/help` D-11).
- **D-06:** Quotes and the gate behave as in `/help`: when fragments are found, the answer carries sources and server-verified quotes; when nothing passes the threshold, the model is still called (with a note that nothing was found in the documentation) and may answer from the ticket data alone, without sources.

### Ticket and user context
- **D-07:** The command accepts an optional identifier right after `/support`: `/support #T-1042 <question>` for a ticket or `/support @login <question>` for a user. When present, **the app itself calls the support MCP server in code** (no model involvement) and puts the ticket together with its client's data (or the user with their tickets) into the context of the turn. This direct call is shown in the answer the same way as a tool call (a card / an entry in `tool_calls`), so the MCP use is visible.
- **D-08:** Without an identifier, the support server's tools are offered to the model (per the D-03 tool choice), and the model decides itself whether to look up a ticket or user from the question text.
- **D-09:** The logged-in app user is the **support agent**, not a client: they may ask about any user or ticket in the JSON file. There is no mapping between app accounts and support clients.
- **D-10:** When the identifier is not found, or the support server is not configured / does not connect, the turn still runs from the documentation without ticket data, and a warning is shown under the answer and carried in the HTTP JSON body (as `/help` D-08), e.g. «тикет T-9999 не найден, ответ дан без данных тикета». An MCP problem never refuses or breaks a `/support` turn.

### Product, FAQ and data
- **D-11:** The product is this app (AiAdventAgentV2). A new `docs/FAQ.md` (Russian) describes real support problems of the app with their causes and fixes, at least: login and the session cookie (wrong password, expired session, cookie blocked, LAN mode Origin mismatch), API keys and `Authorization: Bearer`, LM Studio not running, an MCP server that does not connect, limits. Answers must be true for the real code.
- **D-12:** The seed JSON file is small: **3-4 clients and 5-6 tickets**. Client fields at least: id, login, name, email, plan, account status, app version / OS. Ticket fields at least: id (`T-NNNN`), client id, subject, category, status, priority, created/updated dates, error text, a short message history. The data are chosen so that the answer depends on the ticket: «Почему не работает авторизация?» gets a different correct answer for two different tickets (for example a blocked account vs a LAN Origin mismatch).
- **D-13:** The support MCP server is a new self-contained Python stdio server in `mcp_servers/` (as `project_git.py`, Phase 22 D-01: no `shared/` imports, diagnostics to stderr), read-only, with the JSON path given by a `--data <path>` launch argument. Four tools: `get_ticket(id)` (ticket with its client's data), `get_user(id or login)`, `list_user_tickets(user, status?)`, `search_tickets(text, status?, category?)`. Rejected calls return `isError` with an English reason and fix (Phase 22 D-15); results are never empty strings and stay under the app's 20 000-character tool result limit.
- **D-14:** The server re-reads the JSON file when it changes (by modification time), so editing a ticket's status is visible on the next call without reconnecting. A missing file at start stops the server with a clear stderr reason (Phase 22 D-03); a file that becomes invalid JSON later produces a clear tool error, not a crash.

### Verification
- **D-15:** Verification is pytest plus an isolated-copy end-to-end script (ports 18000/18001, scratch database; the user's app on 8000/8001 is never touched). No control-question report, no `Day33_report.md`, no DeepSeek judge.
- **D-16:** pytest covers at least: the support server tools (found / not found / bad arguments / file change re-read / invalid JSON), command parsing with and without `#T-…` / `@login`, the direct in-code lookup, the warning paths of D-10, history of D-05 (only `/support` pairs), the shared mechanism keeping `/help` unchanged, and `/support` over HTTP.
- **D-17:** The end-to-end script adds the support server through the real «MCP серверы» form, marks the documentation base, and runs `/support #T-… Почему не работает авторизация?` for two tickets in the browser and over HTTP. If LM Studio is not running, the live-model part is recorded as not run, never as passed.
- **D-18:** No separate video script is produced.

### Claude's Discretion
- Exact command grammar (`#T-1042` vs `#1042`, case, what counts as a login), as long as a message that is not `/support` behaves exactly as today and `/help` is untouched.
- The server file name and the documented server name for the form (expected: `mcp_servers/support_desk.py`, server name `support`, tools appear as `mcp__support__<tool>`), where the seed JSON lives (expected: a tracked file such as `data/support/support.json` or next to the server), and exact tool and parameter names.
- How the shared command-turn mechanism is factored (one module with per-command descriptors, or a base plus two thin modules), and where the per-user `/support` settings are stored (new table vs generalising `HelpConfig`).
- The Russian texts: system prompt, usage text, notices, warning, the label above the answer (expected «Поддержка · <тикет>» or similar).
- How the D-07 direct lookup is represented in the stored message and in the HTTP `tool_calls` list.
- Whether the user profile and invariants enter the `/support` system prompt, and whether side jobs (title, facts, task memory) run on a `/support` turn — guided by D-03 (a default turn changes nothing else).
- How many `/support` pairs D-05 sends at most, and how that interacts with the context budget.
- Documentation updates in `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md`, `docs/TESTING_GUIDE.md` and `README.md`, with the exact form values for the support server.

</decisions>

<specifics>
## Specific Ideas

- The demonstration path: mark the documentation base (already containing `docs/FAQ.md`) → add the support server through «MCP серверы» → ask `/support #T-1001 Почему не работает авторизация?` and see the ticket card and an answer that names this ticket's cause plus FAQ sources → ask the same about another ticket and get a different answer → ask a follow-up without the identifier and the ticket stays the topic → change the ticket status in the JSON file and ask again → the answer follows.
- Because the FAQ lives in the same base as the developer documentation (D-04), the search may return architecture fragments for support questions; FAQ.md should be written with clear, symptom-first headings so it ranks well. The `/help` threshold (0.45 default) is reused unless research shows FAQ needs a different value in the `/support` settings.
- The local model (`qwen/qwen3.5-9b`) may not call tools reliably; D-07 exists so the headline demo does not depend on that.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pattern to generalise (/help)
- `.planning/phases/23-help-command-verification-and-report-day-31/23-CONTEXT.md` — `/help` decisions D-01..D-16 that `/support` mirrors (gate, quotes, tool choice, warnings, notices, storage, label, HTTP behaviour)
- `agent/help_command.py` — recognition, per-user settings, notices, tool selection, prompt, payload; the code to refactor into the shared mechanism
- `agent/help_api.py` — REST routes of the `/help` settings
- `agent/http_chat.py` — plain HTTP chat endpoint and the notice/success JSON shapes
- `agent/ws.py`, `agent/rag_turn.py`, `agent/rag_cite.py` — where the command turn plugs into a chat turn, retrieval and quote verification
- `shared/models.py::HelpConfig` — per-user `/help` settings shape

### Own MCP server pattern
- `.planning/phases/22-project-mcp-servers-day-31/22-CONTEXT.md` — D-01..D-15: location, launch arguments, startup failures, error and truncation style
- `mcp_servers/project_git.py` — the self-contained FastMCP stdio server to model the support server on
- `agent/mcp_client.py`, `agent/mcp_tools.py` — app-side MCP sessions, tool result cap, calling a tool in code
- `agent/review_github.py` — precedent for the app calling an MCP tool directly in code (no model involvement)

### Project docs
- `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md`, `docs/TESTING_GUIDE.md`, `README.md` — to be updated as built
- `.planning/ROADMAP.md` §Phase 25 — goal statement

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `/help` turn (`agent/help_command.py::prepare_help_turn`, `finalize_help_turn`, `build_help_tools`, `resolve_help_notice`): almost the whole `/support` turn; the difference is the prompt, the optional identifier lookup, the history of D-05 and the default server.
- `scripts/e2e_help_playwright.py`, `scripts/e2e_mcp_git_playwright.py`: templates for the isolated-copy end-to-end script (form-driven MCP server add, base mark, browser + HTTP).
- `tests/` for `/help` and `project_git`: templates for the new tests.

### Established Patterns
- Whole MCP servers are the unit of tool choice (no per-tool switches).
- MCP problems degrade to a warning, never a refused turn.
- Service notices are not stored and make no model call.
- Assistant-message payload (`Message.rag_sources`) carries the label, sources, quotes and warnings so they survive reload.

### Integration Points
- Command recognition in the WebSocket and HTTP chat paths (where `is_help_command` is checked today).
- Settings window: a new section next to the `/help` section.
- «MCP серверы» form: the support server is added there by the user; the docs give exact values (name, `python`, `mcp_servers/<file>.py --data <path>`, working directory).

</code_context>

<deferred>
## Deferred Ideas

- Write tools on the support server (create ticket, change status, add a reply) — a later phase if needed.
- App accounts acting as clients who see only their own tickets.
- A separate FAQ-only knowledge base for `/support` with its own mark.
- A control-question run with a judge and `Day33_report.md`.

</deferred>

---

*Phase: 25-support-assistant-day-33*
*Context gathered: 2026-10-17*
