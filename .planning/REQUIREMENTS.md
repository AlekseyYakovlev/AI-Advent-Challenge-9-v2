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

- [ ] **REV-01**: A workflow on `pull_request` on a self-hosted runner calls the local review route with an API key, publishes or replaces one PR comment and a job summary, and fails with a named reason when the review cannot be produced.
- [ ] **REV-02**: A dedicated API-key route returns structured JSON (three sections, documentation and code sources, head commit, model) with distinct error codes.
- [ ] **REV-03**: The app fetches PR metadata, the file list and the diff from `github-mcp-server` in code, without the 20 000-character tool-result cut.
- [ ] **REV-04**: A code knowledge base is built from the files tracked by git at the base branch tip and refreshed incrementally before each review and on demand.
- [ ] **REV-05**: Retrieval runs once per base with separate quotas; the model writes a Russian review; oversized PRs go through the per-file path plus a merge call.
- [ ] **REV-06**: Per-user review settings (repository path, documentation base, provider, model) with a Settings section.
- [ ] **REV-07**: A frozen set of 4-5 real PRs, two model runs, a DeepSeek judge and `Day32_report.md`. The GitHub REST calls in `scripts/pr_review_ci.py` (PR comment) and `scripts/review_control.py` (opening, reading and closing the test PRs) are CI and verification tooling outside the app; the app itself has no GitHub API client and reads GitHub only through `github-mcp-server`, so the Out of Scope row "Own GitHub API client" is unchanged.
- [ ] **REV-08**: pytest coverage and documentation, including the safe configuration of the self-hosted runner.

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
| REV-01 | Phase 24 | Pending |
| REV-02 | Phase 24 | Pending |
| REV-03 | Phase 24 | Pending |
| REV-04 | Phase 24 | Pending |
| REV-05 | Phase 24 | Pending |
| REV-06 | Phase 24 | Pending |
| REV-07 | Phase 24 | Pending |
| REV-08 | Phase 24 | Pending |

**Coverage:**
- v5.0 requirements: 32 total
- Mapped to phases: 32
- Unmapped: 0 ✓

---
*Requirements defined: 2026-10-13*
*Last updated: 2026-10-13 after initial definition*
