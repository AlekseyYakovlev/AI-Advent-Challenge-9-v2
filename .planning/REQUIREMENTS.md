# Requirements: AiAdventAgentV2 — Week 7: Pipeline

**Defined:** 2026-10-13
**Core Value:** The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where, rather than dumping everything into one undifferentiated context window.

**Milestone goal:** The app becomes a developer assistant that understands its own project: project documentation is searchable through RAG, the project's git state is reachable through MCP, and a `/help` command answers questions about the project (Day 31 assignment).

## v5.0 Requirements

Requirements for milestone v5.0. Each maps to exactly one roadmap phase.

### Project Documentation

- [x] **DOC-01**: The repository has a `README.md` at its root that describes what the app is, the directory structure, how to install and run it, the main settings in `.env`, and links to the documents in `docs/`.

### Knowledge Base Upload

- [x] **KBUP-01**: In the knowledge base form the user can choose a whole folder; supported files (`.pdf`, `.txt`, `.md`) are taken recursively and unsupported files are skipped, with the number of skipped files shown before the upload starts.
- [x] **KBUP-02**: The user can build one pending list from several selections (single files and folders in any order), see every entry, and remove an entry before the upload starts.
- [x] **KBUP-03**: A file chosen through a folder keeps its relative path (for example `docs/API_SPEC.md`) as the source name in the knowledge base file list, in search results and in answer sources; two files with the same base name in different folders are stored as two separate sources.
- [x] **KBUP-04**: One upload accepts more files than the previous limit of 10 (the new limit is a named constant shown in the form); exceeding the file-count or total-size limit is rejected with a clear message and nothing is stored.
- [x] **KBUP-05**: The user can add files and folders to an existing ready knowledge base; only the added files are indexed, with the base's own chunking and embedding settings, and the fragments indexed earlier stay searchable during and after the addition.
- [x] **KBUP-06**: Adding a file whose source name already exists in the knowledge base replaces that file's fragments with the new content, so an updated document does not produce duplicate fragments.

### Project Git MCP Server

- [x] **PGIT-01**: The repository contains a Python stdio MCP server, built on the already pinned `mcp` SDK with no new dependencies, with read-only tools: current branch, working tree status, diff (working tree, staged, or against a ref), list of tracked files, and recent commits.
- [x] **PGIT-02**: Each tool runs one fixed `git` command without a shell, only inside the configured repository root; arguments that would leave the root or inject options are rejected with a clear error, every call has a timeout, and long output is truncated with an explicit marker.
- [x] **PGIT-03**: The user can add the server through the existing "MCP серверы" form in Settings, and its tools become callable in a chat turn; the documentation gives the exact form values.
- [x] **PGIT-04**: Asked which branch the project is on, the assistant calls the server and answers with the branch that is checked out in the working copy at that moment.

### GitHub MCP Server

- [x] **GHUB-01**: The documentation describes how to connect the official `github-mcp-server` binary in read-only mode with the `repos` and `pull_requests` toolsets (command, arguments, token variable), and the token value is never shown back after saving.
- [x] **GHUB-02**: With that configuration the app connects and lists the server's read-only tools, confirmed by a recorded check.

### Help Command

- [x] **HELP-01**: The user can mark one of their knowledge bases as the project documentation base, see which one is marked, and change or clear the mark.
- [x] **HELP-02**: A message that starts with `/help` followed by a question is answered from the project documentation base with sources, whatever the chat's own RAG settings are, and those settings are left unchanged.
- [x] **HELP-03**: During a `/help` turn the project git tools are available to the model, and a tool call made in that turn is visible in the chat like any other tool call.
- [x] **HELP-04**: `/help` with no question shows a short usage text without calling the model.
- [x] **HELP-05**: When no project documentation base is marked, or the marked base is not ready, `/help` with a question answers with a clear instruction on what to do and makes no model call.
- [x] **HELP-06**: `/help` works through the plain HTTP chat endpoint with an API key, with the same behaviour as in the browser; the JSON reply carries the answer, the sources and the tool calls.
- [x] **HELP-07**: The `/help` question and its answer are stored in the chat tree as ordinary messages and survive a page reload with their sources.

### Verification and Report

- [x] **VER-07**: pytest covers folder and accumulated upload, addition to an existing base, replacement of an updated file, the git MCP server (tools, root confinement, bad arguments), and `/help` over WebSocket and HTTP; `docs/TESTING_GUIDE.md` has matching sections.
- [x] **VER-08**: An isolated-copy end-to-end script (ports 18000/18001, scratch database) uploads a folder through the real UI, connects the git MCP server and runs `/help` in the browser and over HTTP.
- [x] **VER-09**: `docs/API_SPEC.md`, `docs/ARCHITECTURE.md` and `docs/USER_GUIDE.md` describe the new upload options, the git MCP server, the GitHub server setup and the `/help` command as built.
- [x] **VER-10**: `Day31_report.md` records a run of 8-10 control questions about the project through `/help` on a real model, with the answer, the sources, the tool calls and a verdict for each question, including the questions that failed.

### Automated PR Review

- [x] **REV-01**: A workflow on `pull_request` on a self-hosted runner calls the local review route with an API key, publishes or replaces one PR comment and a job summary, and fails with a named reason when the review cannot be produced.
- [x] **REV-02**: A dedicated API-key route returns structured JSON (three sections, documentation and code sources, head commit, model) with distinct error codes.
- [x] **REV-03**: The app fetches PR metadata, the file list and the diff from `github-mcp-server` in code, without the 20 000-character tool-result cut.
- [x] **REV-04**: A code knowledge base is built from the files tracked by git at the base branch tip and refreshed incrementally before each review and on demand.
- [x] **REV-05**: Retrieval runs once per base with separate quotas; the model writes a Russian review; oversized PRs go through the per-file path plus a merge call.
- [x] **REV-06**: Per-user review settings (repository path, documentation base, provider, model) with a Settings section.
- [x] **REV-07**: A frozen set of 4-5 real PRs, two model runs, a DeepSeek judge and `Day32_report.md`. The GitHub REST calls in `scripts/pr_review_ci.py` (PR comment) and `scripts/review_control.py` (opening, reading and closing the test PRs) are CI and verification tooling outside the app; the app itself has no GitHub API client and reads GitHub only through `github-mcp-server`, so the Out of Scope row "Own GitHub API client" is unchanged.
- [x] **REV-08**: pytest coverage and documentation, including the safe configuration of the self-hosted runner.

### Support Assistant

- [ ] **SUP-01**: `/support [#T-NNNN | @login] <question>` is recognised on the server for WebSocket and the plain HTTP chat endpoint; an empty question gives the usage notice (not stored, no model call); a message that is not `/support` behaves exactly as before.
- [ ] **SUP-02**: `/support` and `/help` share one command-turn mechanism extracted from `agent/help_command.py` (`agent/command_turn.py`); `/help` behaves exactly as before and its existing tests pass unchanged.
- [ ] **SUP-03**: Per-user `/support` settings (top-K, candidate count, threshold, four stage switches, whole-server MCP choice, three built-in tool groups; by default only the MCP server named `support` is on) are stored, exposed through `GET/PUT /api/v1/support/config`, and edited in a Settings section «Поддержка (/support)» next to the `/help` section.
- [ ] **SUP-04**: `/support` searches the knowledge base marked for `/help`; when no base is marked or the base is not ready, `/support` answers with a service notice that points to the `/help` section and makes no model call.
- [ ] **SUP-05**: `mcp_servers/support_desk.py` is a self-contained read-only stdio MCP server started with `--data <path>`, with the tools `get_ticket`, `get_user`, `list_user_tickets`, `search_tickets`; rejected calls return `isError` with an English reason and fix; results are never empty and stay under 20 000 characters.
- [ ] **SUP-06**: The support server re-reads the JSON file when it changes; a missing or invalid file at start stops it with a stderr reason; a file that becomes invalid later gives a tool error, not a crash.
- [ ] **SUP-07**: A tracked seed JSON file (4 clients, 6 tickets, fictitious data) and a Russian `docs/FAQ.md` that is true for the real code; two different tickets lead to different correct answers to «Почему не работает авторизация?».
- [ ] **SUP-08**: With `#T-NNNN` or `@login` the app calls the support server in code (no model involvement), shows the call as an ordinary tool-call card and `tool_calls` entry, and puts the ticket with its client (or the user with their tickets) into the turn as a fenced data block.
- [ ] **SUP-09**: A missing ticket or user, or a support server that is not added, disabled or not connecting, never refuses or breaks the turn: the answer is given from the documentation with a warning under the answer and in the HTTP body.
- [ ] **SUP-10**: A `/support` turn sends the earlier `/support` question/answer pairs of the current branch (bounded) to the model; ordinary messages and `/help` pairs are not sent.
- [ ] **SUP-11**: The label («Поддержка · T-1001»), sources, verified quotes and warnings are stored in the assistant message payload under an additive `support` key and survive a reload; the HTTP JSON body carries the same (`support`, `rag`, `tool_calls`).
- [ ] **SUP-12**: pytest covers the support server tools (found, not found, bad arguments, re-read on change, invalid JSON), command parsing with and without identifiers, the direct lookup, every warning path, the `/support` history rule, the shared mechanism keeping `/help` unchanged, the settings routes, the UI source guard and `/support` over WebSocket and HTTP.
- [ ] **SUP-13**: An isolated-copy end-to-end script (ports 18000/18001, scratch database) adds the support server through the real «MCP серверы» form, marks the documentation base and runs `/support #T-… Почему не работает авторизация?` for two tickets in the browser and over HTTP; the live-model part is recorded as not run when LM Studio is down.
- [ ] **SUP-14**: `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md`, `docs/TESTING_GUIDE.md` and `README.md` describe `/support`, the support server with exact form values, and the new tests as built, guarded by a docs test.

## Future Requirements

Deferred to later days of Week 7. Tracked but not in the current roadmap.

### Git Write Access

- **PGIT-05**: Write tools in the project git server: stage, commit, create and switch branch, push, pull.
- **GHUB-03**: `github-mcp-server` in write mode for Pull Requests (create, update, review, merge).

### MCP Settings

- **MCPS-01**: Enable or disable individual tools of an MCP server in Settings, so that only the chosen tool schemas are sent to the model.

### Knowledge Base Upload

- **KBUP-07**: Text files of code and schemas (`.py`, `.json`, `.yaml`, `.sql`) accepted into a knowledge base.
- **KBUP-08**: Remove a single file from an existing knowledge base.

## Out of Scope

| Feature | Reason |
|---------|--------|
| A shell or "skill" tool that runs arbitrary commands | The assignment requires MCP; arbitrary shell from a local model on a LAN-exposed service is an unacceptable risk |
| Third-party local git MCP servers (`@cyanheads/git-mcp-server`, `git-mcp-go`, `mcp-server-git`) | Checked live on 2026-10-13: 9261 schema tokens for 28 tools, or unmaintained without licence, or no push; the app cannot switch off single tools |
| GitLab mirror with GitLabMcpMini | Sees only the remote repository, not the checked-out branch or the working tree |
| Automatic re-indexing of project documents at start-up | Slows start-up and needs LM Studio running; the user re-adds changed files instead (KBUP-06) |
| Own GitHub API client | The official server already covers repositories and Pull Requests |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DOC-01 | Phase 21 | Complete |
| KBUP-01 | Phase 21 | Complete |
| KBUP-02 | Phase 21 | Complete |
| KBUP-03 | Phase 21 | Complete |
| KBUP-04 | Phase 21 | Complete |
| KBUP-05 | Phase 21 | Complete |
| KBUP-06 | Phase 21 | Complete |
| PGIT-01 | Phase 22 | Complete |
| PGIT-02 | Phase 22 | Complete |
| PGIT-03 | Phase 22 | Complete |
| PGIT-04 | Phase 22 | Complete |
| GHUB-01 | Phase 22 | Complete |
| GHUB-02 | Phase 22 | Complete |
| HELP-01 | Phase 23 | Complete |
| HELP-02 | Phase 23 | Complete |
| HELP-03 | Phase 23 | Complete |
| HELP-04 | Phase 23 | Complete |
| HELP-05 | Phase 23 | Complete |
| HELP-06 | Phase 23 | Complete |
| HELP-07 | Phase 23 | Complete |
| VER-07 | Phase 23 | Complete |
| VER-08 | Phase 23 | Complete |
| VER-09 | Phase 23 | Complete |
| VER-10 | Phase 23 | Complete |
| REV-01 | Phase 24 | Complete |
| REV-02 | Phase 24 | Complete |
| REV-03 | Phase 24 | Complete |
| REV-04 | Phase 24 | Complete |
| REV-05 | Phase 24 | Complete |
| REV-06 | Phase 24 | Complete |
| REV-07 | Phase 24 | Complete |
| REV-08 | Phase 24 | Complete |
| SUP-01 | Phase 25 | Pending |
| SUP-02 | Phase 25 | Pending |
| SUP-03 | Phase 25 | Pending |
| SUP-04 | Phase 25 | Pending |
| SUP-05 | Phase 25 | Pending |
| SUP-06 | Phase 25 | Pending |
| SUP-07 | Phase 25 | Pending |
| SUP-08 | Phase 25 | Pending |
| SUP-09 | Phase 25 | Pending |
| SUP-10 | Phase 25 | Pending |
| SUP-11 | Phase 25 | Pending |
| SUP-12 | Phase 25 | Pending |
| SUP-13 | Phase 25 | Pending |
| SUP-14 | Phase 25 | Pending |

**Coverage:**
- v5.0 requirements: 46 total
- Mapped to phases: 46
- Unmapped: 0 ✓

---
*Requirements defined: 2026-10-13*
*Last updated: 2026-10-17 — SUP-01..SUP-14 added for Phase 25 (Day 33)*
