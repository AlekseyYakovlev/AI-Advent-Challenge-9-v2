# Phase 23: /help command, verification and report (Day 31) - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning

<domain>
## Phase Boundary

A developer asks a question about the project with `/help` and gets an answer built from the project documentation and the live git state, and a recorded run on a real model proves it. In scope: marking one knowledge base as the project documentation base together with the `/help` settings; the `/help` command in the chat (WebSocket) and over the plain HTTP chat endpoint; the usage text and the "no base" instruction without a model call; storage of the exchange in the chat tree; pytest coverage, an isolated-copy end-to-end script, the three documents, and `Day31_report.md` (HELP-01..07, VER-07..10).

Out of scope here: git write tools (PGIT-05), `github-mcp-server` in write mode (GHUB-03), enabling or disabling single tools of an MCP server (MCPS-01), code and schema files in a knowledge base (KBUP-07), removal of a single file from a base (KBUP-08), any other slash command.

Phases 21 and 22 are complete (all plans have summaries, both verification documents exist), so the folder upload, the add-files flow and the `local-git` server are available as built.

</domain>

<decisions>
## Implementation Decisions

### Search and the «Не знаю» gate
- **D-01:** A `/help` turn never ends with the code-built «Не знаю» reply. When the documentation search returns no fragment above the threshold, the model is still called, with the chosen tools and a note that nothing was found in the documentation. It may answer from git; if it has no answer, it says so itself. Such an answer has no sources. Reason: a question such as "на какой ветке проект?" has no fragment in the documentation, and the strict gate in `agent/rag_turn.py` would otherwise make HELP-03 impossible.
- **D-02:** When fragments are found, the answer carries verified quotes as in strict RAG mode (the «Цитаты» block under the answer, quotes checked on the server), plus the source list. So `/help` uses the quote half of strict mode without its gate half. The user chose this over "sources only".
- **D-03:** The `/help` search settings are not fixed in code and are not taken from the chat. The user sets them in the same place as the documentation base mark.
- **D-04:** The set of `/help` search settings is the same as a chat has: top-K, candidate count, threshold (empty means the calibrated value of the base's embedding model), and the four stage switches (lexical, LLM rerank, hybrid, rewrite). The chat's own `ChatRagConfig` row is neither read nor written by `/help` (HELP-02).

### Tools in a /help turn
- **D-05:** The tool set of a `/help` turn is configurable by the user, in the same form as the base mark and the search settings. The unit of choice for MCP is a whole server (a checkbox per configured MCP server), never a single tool.
- **D-06:** The built-in tools are part of the configurable set as three separate checkboxes: memory, tasks, scheduler.
- **D-07:** The default, before the user changes anything: only the MCP server named `local-git` is on; every other MCP server and all three built-in groups are off. With the default a `/help` turn changes nothing (no memory write, no task, no scheduled job).
- **D-08:** When a chosen MCP server is not configured or fails to connect, the turn still runs from the documentation without that server's tools, and a warning is shown under the answer ("сервер local-git недоступен, его инструменты не использовались") and carried in the HTTP JSON body. An MCP problem never refuses or breaks a `/help` turn.
- **D-09:** A tool call made in a `/help` turn is shown with the ordinary tool-call card (HELP-03) and is returned in `tool_calls` of the HTTP reply (HELP-06).

### Relation to the chat's conversation
- **D-10:** A `/help` question is self-contained. The model gets the `/help` system prompt, the documentation fragments and the question; the chat's earlier messages are not sent. The same question gives the same kind of answer in any chat and over HTTP.
- **D-11:** The stored `/help` question and answer are ordinary messages for later turns: an ordinary message after `/help` sees them in its history like any other pair, with no special handling in the compression strategies.

### The documentation base mark and its form
- **D-12:** The mark, the `/help` search settings and the tool choice live in one new section of the Settings window, next to «MCP серверы»: a list of the user's ready knowledge bases to choose from (with a way to clear the choice), the search fields of D-04, the server checkboxes of D-05 and the three built-in checkboxes of D-06. The setting is per user.
- **D-13:** In the knowledge base window the card of the marked base shows a badge «Документация проекта». The mark is not set from the card; the section in Settings is the only place to set, change or clear it (HELP-01).

### Service replies and the look of the exchange
- **D-14:** The two replies that make no model call — the usage text for `/help` with no question (HELP-04) and the instruction for "no base marked or the marked base is not ready" (HELP-05) — are not stored in the chat tree. In the browser they are shown as a temporary notice that is gone after a reload. Over HTTP the reply is a JSON body with the text and an explicit sign that nothing was stored. They do not enter the model context of later turns and do not use up the per-chat message limit.
- **D-15:** A real `/help` exchange is stored as two ordinary messages (HELP-07). The user message is shown as typed, including the `/help` prefix. Above the answer there is a small label «Справка по проекту · <имя базы>»; below it are the ordinary tool-call cards, the sources and the quotes. The label, the sources and the quotes survive a page reload.
- **D-16:** An HTTP request that carries `kb_id` together with a `/help` message: `kb_id` is ignored. The answer comes from the marked base and the chat's RAG settings stay unchanged. `docs/API_SPEC.md` states this.

### Control questions and the report
- **D-17:** The control set has 10 questions, frozen in a file before the run (as in Day 22-25): 5 answered only from the documentation (run, `.env`, architecture, API, tests), 2 answered only from git (current branch, recent commits), 2 mixed (documentation plus git, for example what is changed in the working copy and which module it belongs to), 1 outside the project (expected: the model says it does not know). Each question has its expected source, expected tool and key facts recorded beforehand.
- **D-18:** The set is run on two models: the local LM Studio model (`qwen/qwen3.5-9b`, as in the earlier reports) and DeepSeek. The report compares them.
- **D-19:** The verdict on the correctness of each answer comes from a DeepSeek judge that grades against the recorded reference, following `scripts/rag_judge.py`. The report states plainly that on the DeepSeek run the judge and the answering model are the same provider.
- **D-20:** The run is done by a separate script over HTTP on an isolated copy (ports 18000/18001, scratch database): it loads `README.md` and `docs/` into a base, connects `local-git`, marks the base, and sends the 10 `/help` questions to the HTTP chat endpoint with an API key. The answer, the sources and the tool calls are read from the JSON body and saved under `eval_out/day31/`. The browser path is covered by the separate end-to-end script of VER-08, not by this run.
- **D-21:** Honest recording, as in earlier phases: if LM Studio is not running, or the DeepSeek key is not accepted, that run (or the judge column) is recorded as not run, never as passed. Failed questions stay in the report with their answers (VER-10).
- **D-22:** `Day31_report.md` is written in Russian, like `Day22_report.md` … `Day30_report.md`; for each question it gives the answer, the sources, the tool calls and the verdict.

### Claude's Discretion
- Where the per-user `/help` configuration is stored (a new table or columns) and the shape of its REST routes.
- What happens to the mark when the marked base is deleted (the expected behaviour: the mark is cleared), and whether a ready base that is being added to (`status=ready`, `phase=adding`, Phase 21 D-08) counts as ready for `/help` (the expected behaviour: yes).
- The exact rule that recognises the command (case, leading whitespace, `/helpme`, other `/words`), as long as a message that is not `/help` behaves exactly as today.
- How the "this was a /help turn" fact and the warning of D-08 are stored on the assistant message so that the label survives a reload (the existing `Message.rag_sources` payload is the obvious carrier).
- The wording of the `/help` system prompt, the usage text, the instruction text, the warning and the label (Russian UI texts).
- The HTTP status and field names of the service replies of D-14, and the name of the warning field of D-08.
- Whether the chat's side jobs run on a `/help` turn: task memory update, fact extraction, automatic chat title. The guiding rule is D-07: with the default settings `/help` changes nothing besides adding its two messages.
- Whether the user profile and invariants are added to the `/help` system prompt; D-10 excludes the chat history, not necessarily these.
- Whether the «Детали поиска» block is shown under a `/help` answer.
- How the quote instructions of D-02 and the tool calls of D-09 are combined in one prompt, and the fallback if the local model cannot do both reliably (this must be checked on the model during research; a failure is reported, not hidden).
- Default values of the `/help` search settings before the user changes them.
- The names of the new scripts and test files, and the layout of the Settings section within the existing Tailwind look.

</decisions>

<specifics>
## Specific Ideas

- The demonstration path: mark the documentation base in Settings, ask `/help как запустить приложение?` in any chat and see sources such as `README.md` and `docs/USER_GUIDE.md`; ask `/help на какой ветке проект?` and see the `mcp__local-git__…` tool-call card; reload the page and see both exchanges with their label; send the same message to the HTTP endpoint with an API key and get the answer, the sources and the tool calls in one JSON body.
- The 0.67 threshold was calibrated for `bge-m3` on legal texts and falsely refused 3 of 8 answerable questions in Day 24. With D-01 a miss no longer blocks the answer, but it still removes the sources, so the threshold on Markdown documentation is worth a look during research.
- Quote verification (`agent/rag_cite.py`) was built and tested on legal prose; Markdown with code blocks and tables is a new kind of text for it.
- The DeepSeek key: the user stated on 2026-10-13 that a working key is set in the DeepSeek provider settings of the app (the placeholder recorded as blocked in Phase 12 is no longer the case). The user allowed two ways to give the control run that key: copy the provider configuration and key into the scratch copy, or run on the main database. The first is preferred because it keeps D-20 (isolated copy, the user's app on 8000/8001 untouched, no test chats left in the main database); `scripts/rag_dialog.py` already copies the database for its runs (`_copy_database`), and provider rows reference `.env`-declared names (Phase 12-01), so research must confirm what exactly has to be copied. Running on the main database is the permitted fallback if the copy cannot carry the key. A scratch copy that holds the key stays outside tracked files and is removed after the run. The key is never written to a file in the repository, a planning document, a log or the report. D-21 still applies if the key is refused at run time.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/REQUIREMENTS.md` — HELP-01..07, VER-07..10, the Future Requirements (PGIT-05, GHUB-03, MCPS-01, KBUP-07, KBUP-08) and the Out of Scope table
- `.planning/ROADMAP.md` — Phase 23 goal and the five success criteria
- `.planning/PROJECT.md` — constraints (vanilla JS frontend, process model, cookie for WebSocket and API key for REST only) and the v4.0 key decision "HTTP chat reuses the WebSocket turn"

### Sibling phases of the same day
- `.planning/phases/21-project-docs-in-rag-day-31/21-CONTEXT.md` — relative paths as source names (D-05), a ready base stays ready during an addition (D-08)
- `.planning/phases/21-project-docs-in-rag-day-31/21-VERIFICATION.md` — what was built and checked for folder upload and addition
- `.planning/phases/22-project-mcp-servers-day-31/22-CONTEXT.md` — the `local-git` server name and its five tools (D-05, D-06), output limits, the isolated-copy check (D-20)
- `.planning/phases/22-project-mcp-servers-day-31/22-VERIFICATION.md` — the recorded checks of both servers, to be carried into `Day31_report.md`

### Behaviour as built
- `docs/ARCHITECTURE.md` — chat turn, RAG pre-step, strict mode and quotes, MCP client and tool rounds, HTTP chat
- `docs/API_SPEC.md` — `POST /api/v1/chats/{chat_id}/messages` (request, response, error codes), RAG config routes, knowledge base routes, MCP routes
- `docs/USER_GUIDE.md` — the Settings window, the knowledge base window, the chat RAG panel as described for users
- `docs/TESTING_GUIDE.md` — required scenario coverage; VER-07 adds sections here
- `README.md` — part of the documentation base and of the control questions

### Report and script precedents
- `Day30_report.md` — the latest report: structure, honest recording, links to `eval_out/`
- `Day25_report.md` — frozen question set, two runs compared, DeepSeek judge column
- `.planning/milestones/v3.0-ROADMAP.md` — Phases 14-16 decisions on RAG, thresholds, strict mode and quotes
- `.planning/milestones/v4.0-ROADMAP.md` — Phase 19 decisions on the HTTP chat endpoint and API keys

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/ws.py::_run_chat_turn` — the one chat turn used by WebSocket and HTTP; the natural place to recognise `/help` once for both channels. `run_limited_turn` wraps it with the generation queue.
- `agent/ws.py::_complete_gated_turn` — an existing example of a turn that replies without a model call (it does store the reply, unlike D-14).
- `agent/http_chat.py` — `post_chat_message`, `_run_http_turn`, `_build_success`; takes `tool_calls` from the `RecordingSink` frames and `rag` from the done frame; applies `kb_id` through `apply_chat_kb` before the turn (must be skipped for `/help`, D-16); calls `limits.check_message_rate` before the turn (relevant to D-14).
- `agent/schemas.py` — `MessagePayload`, `HttpChatRequest`, `HttpChatResponse`.
- `agent/rag_turn.py::prepare_rag_turn` — reads `ChatRagConfig` by chat id, runs the pipeline, applies the strict gate and builds the payload; `finalize_rag_turn` verifies quotes. `/help` needs the same pipeline with its own configuration and without the gate (D-01, D-02).
- `agent/rag_pipeline.py::config_from_row`, `run_retrieval_pipeline`; `agent/rag.py` (`build_rag_block`, `merge_rag_block`, `merge_no_fragments_note`, `sources_from_chunks`, payload helpers); `agent/rag_cite.py` (`process_answer`, `build_idk_reply`).
- `agent/rag_api.py` — `RagConfigIn` validation and `_apply_search_settings` for the same field set as D-04; `_get_owned_chat`.
- `shared/models.py::ChatRagConfig` — the field set to mirror for the `/help` configuration; `KnowledgeBase` (status, phase); `McpServerConfig` (name, enabled, auto_connect, per user).
- `agent/mcp_tools.py::build_mcp_toolset`, `build_toolset_from_servers`, `server_slug` — the per-turn MCP toolset; today it takes every enabled server of the user. `agent/tools.py` — `TOOL_REGISTRY`, `build_tool_schemas`, and `dispatch_tool_calls` with an `allowed_tools` argument; built-in tools are one flat registry with no grouping (D-06 needs groups).
- `agent/tool_guard.py` — `MULTI_STEP_TOOL_HINT`, `TOOL_USE_RULE` and the reminders appended to the system prompt when tools are present.
- `mcp_servers/project_git.py` — the `local-git` server as built: `git_current_branch`, `git_status`, `git_diff`, `git_tracked_files`, `git_recent_commits`.
- `ui/static/app.js` — `renderRagControls`, `renderRagSearchPopover`, `saveRagSearchSetting` (the chat's search settings UI to mirror), `buildRagSourcesBlock`, `buildRagQuotesBlock`, `buildRagDetailsBlock`, `buildKbCard`; `ui/static/index.html` — the Settings window with the «MCP серверы», «Провайдеры LLM», «API ключи» and «Сеть» sections.
- Scripts: `scripts/rag_dialog.py` (isolated copy start-up, login, base check, frozen scenarios, raw results), `scripts/rag_judge.py` (DeepSeek judge with `--check` preflight), `scripts/verify_service.py` (HTTP chat with an API key), `scripts/e2e_kb_folder_playwright.py` and `scripts/e2e_mcp_git_playwright.py` (browser scripts for folder upload and for the git server on the isolated copy).

### Established Patterns
- No slash command exists in the app; `/help` is the first one, and it is recognised on the server so that both channels behave the same.
- RAG fragments are merged only into the outbound copy of the context, after the system prompt and tool schemas are final; the database always keeps the full history.
- An MCP failure never breaks a chat turn (`_load_mcp_toolset` falls back to an empty toolset and logs a warning).
- WebSocket authenticates by cookie only; API keys authenticate REST only.
- Frontend text is set with `textContent`; no HTML insertion without `DOMPurify.sanitize()`; frontend behaviour is guarded by Node-free source tests (`tests/test_*_ui.py`, `tests/test_*_static.py`) and `tests/test_static_js_syntax.py`.
- Documentation as built is guarded by docs tests (`tests/test_docs_kb_upload.py`, `tests/test_docs_mcp_servers.py`, `tests/test_docs_day30.py`).
- Browser and model checks run on an isolated copy at ports 18000/18001; the user's app on 8000/8001 is never stopped. A check that could not run is recorded as not run.

### Integration Points
- A branch for `/help` at the start of the chat turn in `agent/ws.py`, before the user message is stored (D-14 needs the no-model replies to leave no row).
- A new per-user configuration with its REST routes, a new section in the Settings window, and a badge on the knowledge base card.
- The done frame and `HttpChatResponse` gain what `/help` needs: the mark that the turn was `/help`, the base name for the label, and the warning of D-08.
- New tests next to `tests/test_http_chat*.py`, `tests/test_rag_ws.py`, `tests/test_rag_turn.py`, `tests/test_mcp_chat_ws.py`; VER-07 also lists coverage already written in Phases 21 and 22 (`tests/test_kb_add_*.py`, `tests/test_kb_folder_ui.py`, `tests/test_mcp_project_git*.py`), which needs matching sections in `docs/TESTING_GUIDE.md`.
- Results of the control run under `eval_out/day31/`, next to `eval_out/day22` … `eval_out/day30`.

</code_context>

<deferred>
## Deferred Ideas

- Choosing single tools of an MCP server for `/help` — this is MCPS-01 (per-tool enable/disable in Settings), a future requirement. In this phase `/help` chooses whole servers only (D-05).
- A hint in the message field when the user types `/` — offered during discussion and not chosen; a new interface element, can be revisited if more slash commands appear.
- Follow-up questions inside `/help` (earlier `/help` pairs sent to the model) — not chosen (D-10); a follow-up is asked as an ordinary message, which sees the stored exchange (D-11).

</deferred>

---

*Phase: 23-help-command-verification-and-report-day-31*
*Context gathered: 2026-10-13*
