# Phase 24: Automated PR code review (Day 32) - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning

<domain>
## Phase Boundary

A pipeline runs on every Pull Request of the project repository: a GitHub Action on a self-hosted runner asks the local app for a review, the app obtains the PR diff and the changed files, searches the project documentation and the project code through RAG, and returns a review text with three parts — potential bugs, architectural problems, recommendations. The Action publishes the text as one comment in the PR. In scope: the workflow file and its thin caller script; a dedicated review REST route with API-key authentication; fetching the diff and the file list from `github-mcp-server` in code; a code knowledge base indexed from the repository through git; the review settings; a control run on a frozen set of test PRs with a report (`Day32_report.md`).

Out of scope here: `github-mcp-server` in write mode (GHUB-03 stays deferred — the comment is posted by the Action, not by the app), git write tools (PGIT-05), code files in the browser upload form (the upload form is unchanged; KBUP-07 as worded stays deferred), inline review comments bound to diff lines, a `/review` chat command, reviewing by polling GitHub from the app's scheduler, GitHub-hosted runners, per-tool MCP switches (MCPS-01).

The assignment text (Day 32, in Russian) asks for: a pipeline (for example a GitHub Action) that starts on a PR; the assistant gets the diff and the changed files; it uses RAG (documentation + code); it returns potential bugs, architectural problems and recommendations.

Phase 24 was added to the v5.0 roadmap next to the still-open Phase 23 by user decision. `REQUIREMENTS.md` has no requirement IDs for it yet; the planner defines them from the decisions below.

</domain>

<decisions>
## Implementation Decisions

### Where the pipeline runs
- **D-01:** The review is triggered by a GitHub Action that runs on a **self-hosted runner on the user's machine**. The job calls the local app over HTTP with an API key. GitHub-hosted runners are not used (they cannot reach the local app, LM Studio or the `bge-m3` embeddings).
- **D-02:** The runner passes only the PR identity (the PR number, plus what the route needs to identify the repository and the head commit). **The app itself obtains the diff and the list of changed files from `github-mcp-server`, in code, with no model involvement.** The model does not decide whether to fetch the diff. Chosen against the recommendation (runner computes the diff with git), so the review depends on the GitHub server being configured and connected for the API key's user and on the token being valid.
- **D-03:** The runner talks to a **new dedicated review REST route**, authenticated with an API key like the HTTP chat route. It returns structured JSON: the three review sections, the sources used from documentation and from code, the head commit reviewed, and the model used. The review prompt, the search rules and the splitting logic live in the app; the workflow and its script stay thin.
- **D-04:** When the review cannot be produced (the app is not running, the GitHub server is not configured or does not connect, LM Studio is not available for embeddings or generation, the model fails), **the Action job fails** and the job summary names what exactly was unavailable. The check is not made a required check, so merging is never blocked by it. The route returns distinct, readable error reasons so the job can print them.
- **D-05:** The provider and model that write the review are **set by the user in the review settings; the default is the local LM Studio model**. Chosen against the recommendation (DeepSeek by default): the code does not leave the machine unless the user switches the provider.
- **D-06:** When the diff does not fit the model's context budget, the review is done **per file (or per group of files within a budget), each part with its own search, and then one more call builds the overall review** from the partial ones. The whole PR is always covered; nothing is silently dropped. A diff that fits is reviewed in one call.

### RAG over code
- **D-07:** Code enters the search through a **new indexing path that reads the repository itself**: the user gives the repository path in the review settings and the app builds a code knowledge base from the files tracked by git. The browser upload form is not extended.
- **D-08:** **Two separate knowledge bases** are searched: the project documentation base (built in Phase 21) and the code base, each with its own quota of fragments, so documentation does not crowd out code and the reverse. The review output shows which sources came from documentation and which from code.
- **D-09:** The code base is **refreshed automatically before each review**: the app compares the files with the index and re-indexes only the changed ones (by content hash). The interface also has an explicit "refresh" action.
- **D-10:** The indexed state is the **base branch of the PR** (for example `main`), read through git, not the files that happen to be checked out on disk. Chosen against the recommendation (working copy as is). Consequence to resolve in research: how the local repository learns the current state of the base branch (a `git fetch` of the remote ref, or reading the base commit named by the PR), and what happens when two PRs target different base branches.
- **D-11:** Python files are split **by functions and classes** (the standard library `ast`), other files (JS, HTML, configs) by line windows. Each fragment carries the file path, the function or class name where there is one, and the line range, so a review remark can point at them.

### Where the review goes
- **D-12:** The app returns the review as JSON and **the Action job publishes it** as a PR comment with the workflow's built-in token (`pull-requests: write` permission), and repeats the text in the job summary. The app's `github-mcp-server` stays `--read-only`.
- **D-13:** The review is **one general comment** with three sections: «Потенциальные баги», «Архитектурные проблемы», «Рекомендации». Each remark names the file and the line in text. No inline comments bound to diff lines.
- **D-14:** When new commits arrive in the PR, the review is run again over the whole PR and **replaces the text of its own earlier comment** (found by a hidden marker). The comment states the commit it was made for.
- **D-15:** The review text is written in **Russian**; file names, identifiers and code stay as they are.

### Verification and report
- **D-16:** Quality is checked on a **frozen set of 4-5 test PRs with planted defects**, recorded before the run with what the review is expected to find: an obvious bug; a violation of a project rule stated in the documentation (for example `print` instead of structlog, `datetime.utcnow`, a write without commit); an architectural violation (for example `ui` importing from `agent`); a large PR that forces the per-file path of D-06; a clean PR with no defects (to see false remarks).
- **D-17:** The test PRs are **real Pull Requests in `AlekseyYakovlev/AI-Advent-Challenge-9-v2`**, reviewed by the real GitHub Action on the user's self-hosted runner, with the comments appearing in the PRs. They are closed without merging and stay as evidence.
- **D-18:** The control run is done on **two models — the local LM Studio model and DeepSeek** — and the report compares what each found and what each invented. The second run is the same PRs re-run through the Action after the model is switched in the review settings.
- **D-19:** The verdict comes from a **DeepSeek judge** modelled on `scripts/rag_judge.py`, which compares each review with the recorded reference: each planted defect found or not found, and the number of false remarks, shown as a table. The report states plainly that on the DeepSeek run the judge and the author are the same provider.
- **D-20:** `Day32_report.md` is written in Russian like the earlier reports. Honest recording applies as before: a run that could not be made (LM Studio not running, DeepSeek key refused, runner not registered) is recorded as not run, never as passed; failed PRs stay in the report with their review texts.

### Claude's Discretion
- The route path, request and response field names, HTTP statuses and error codes of the review route.
- Where the review settings live (a new table or columns; a new section in Settings next to «MCP серверы») and their exact field set. At minimum: repository path, documentation base, provider and model. Whether the documentation base setting is its own field or reuses the Phase 23 «Документация проекта» mark if Phase 23 lands first.
- How the app calls the GitHub server in code: which tools of the `pull_requests` toolset give the diff and the file list, and how the app-side truncation at `MCP_TOOL_RESULT_MAX_CHARS` (20 000 characters) is avoided or paged around for a direct call.
- What is used as the search query for each file or group (changed paths, changed function names, added lines), the per-base fragment quotas, and the token budget that decides between one call and the per-file path.
- Which tracked files go into the code base (extensions, size limit, exclusions such as binaries, `.planning/`, lock and generated files) and the line-window size for non-Python files.
- Whether the full text of a changed file at the PR head is given to the model in addition to the diff.
- Which PR events trigger the workflow (opened, synchronize, reopened; draft PRs), concurrency handling when commits arrive during a running review, and the job timeout.
- How the runner's API key and the app URL are supplied to the workflow (repository secrets and variables); the key is never written to a tracked file.
- Whether the review is also stored and visible inside the app, and whether RAG sources are listed in the PR comment.
- Whether remarks carry a severity level.
- The wording of the review prompt and of the Russian texts.
- pytest coverage, the documentation updates in `docs/` and `README.md`, and the names of new modules, scripts and test files, following the conventions of earlier phases.
- The git branch: the project convention is one branch per day (`Day32`); Day 31 is not merged yet, so the planner decides what `Day32` is branched from.

</decisions>

<specifics>
## Specific Ideas

- The demonstration path: open a PR with a planted defect → the Action starts on the self-hosted runner → the app fetches the diff, searches documentation and code, writes the review → a Russian comment with the three sections appears in the PR → push a fix → the same comment is replaced and names the new commit.
- Embeddings come from LM Studio (`bge-m3`) even when DeepSeek writes the review, so LM Studio must be running for the search in both control runs; if it is not, D-04 applies.
- A self-hosted runner executes workflow code on the user's machine. The research must cover how to keep Pull Requests from forks from running there (the repository's visibility and the fork-PR approval setting), and state the safe configuration in the documentation.
- A `pull_request` workflow runs from the workflow file present in the PR's own merge ref, so the test PR branches must contain the workflow; the research must confirm from which branch the test PRs are cut and against which base.
- Registering the self-hosted runner and creating the API key are done by the user; the documentation gives the exact steps. The runner calls the user's running app (ports 8000/8001), which is never stopped or restarted by a check; the app URL in the workflow is configurable so a run can target another instance.
- The planted defects of D-16 are chosen so that some can only be recognised with the documentation (project rules in `docs/` and `CLAUDE.md`-style conventions) and some only with the surrounding code, so the report can show what RAG contributed.
- The local model (`qwen/qwen3.5-9b` in the earlier reports) has a default context of 65 536 tokens; that is the budget behind D-06.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/ROADMAP.md` — Phase 24 goal and dependencies
- `.planning/REQUIREMENTS.md` — v5.0 requirements, the Future Requirements that stay deferred (PGIT-05, GHUB-03, MCPS-01, KBUP-07, KBUP-08) and the Out of Scope table (no arbitrary shell tool, no own GitHub API client)
- `.planning/PROJECT.md` — constraints (process model, vanilla JS frontend, MCP servers as stdio subprocesses, cookie for WebSocket and API key for REST only)

### Earlier phases this one builds on
- `.planning/phases/21-project-docs-in-rag-day-31/21-CONTEXT.md` — the documentation base: folder upload, relative paths as source names, addition and replacement of files
- `.planning/phases/22-project-mcp-servers-day-31/22-CONTEXT.md` — `github-mcp-server` read-only setup, the token scope (D-16..D-18), the `local-git` server and its output limits
- `.planning/phases/22-project-mcp-servers-day-31/22-VERIFICATION.md` and `22-CHECKS.md` — the recorded tool list of the GitHub server (the source for which tools return a PR diff and files)
- `.planning/phases/23-help-command-verification-and-report-day-31/23-CONTEXT.md` — the «Документация проекта» mark (D-12, D-13), the control-run and judge pattern (D-17..D-22), the DeepSeek key handling note
- `.planning/milestones/v4.0-ROADMAP.md` — Phase 19 decisions on the HTTP chat endpoint and API keys
- `.planning/milestones/v3.0-ROADMAP.md` — Phases 13-16 decisions on knowledge bases, chunking, retrieval and thresholds

### Behaviour as built
- `docs/ARCHITECTURE.md` — knowledge bases and indexing, retrieval pipeline, MCP client, HTTP chat, providers
- `docs/API_SPEC.md` — `POST /api/v1/chats/{chat_id}/messages`, API key routes, knowledge base routes, MCP routes
- `docs/USER_GUIDE.md` — Settings window sections, knowledge base window, the GitHub server form values
- `docs/TESTING_GUIDE.md` — required scenario coverage per test file
- `README.md` — project overview that is part of the documentation base

### Report and script precedents
- `Day30_report.md`, `Day25_report.md` — report structure, honest recording, two runs compared, judge column
- `scripts/rag_judge.py`, `scripts/rag_dialog.py`, `scripts/verify_service.py` — judge, frozen scenarios, HTTP calls with an API key

No workflow file exists in the repository yet (`.github/` is absent); there is no external spec for the review format beyond the decisions above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/http_chat.py` and `agent/api_keys_api.py` — the API-key authenticated REST pattern (`post_chat_message`, error mapping) to follow for the review route.
- `agent/headless.py` — `run_headless_turn`, `RecordingSink`, `_safe_mcp_toolset`: a model call outside a browser session, with provider resolution and error mapping.
- `agent/mcp_client.py`, `agent/mcp_tools.py` — per-user MCP sessions, `call_mcp_tool` (applies the 20 000 character truncation), `ensure_connected`-style lazy connection; the base for the in-code call to the GitHub server (D-02).
- `agent/kb_indexer.py`, `agent/kb_chunking.py`, `agent/kb_loaders.py`, `agent/kb_limits.py`, `agent/embeddings.py` — the indexing pipeline; today it accepts `.pdf`, `.txt`, `.md` uploaded through the browser and has a shared chunk-row builder. The code base (D-07, D-11) needs a new source (git) and a new chunker.
- `agent/rag_pipeline.py` (`run_retrieval_pipeline`, `config_from_row`), `agent/kb_search.py`, `agent/rag_fts.py`, `agent/rag_rank.py`, `agent/rag.py` (`build_rag_block`, `sources_from_chunks`) — retrieval to run once per base (D-08).
- `agent/providers.py`, `agent/llm_client.py` — provider and model resolution for the configurable review model (D-05).
- `mcp_servers/project_git.py` — fixed, shell-free git commands confined to a root, with timeouts; the pattern for reading the base branch through git (D-10).
- `shared/models.py` — `KnowledgeBase`, `McpServerConfig`, the provider tables; `ui/static/index.html` and `ui/static/app.js` — the Settings window sections to mirror for the review settings.
- `scripts/rag_judge.py` — DeepSeek judge with a `--check` preflight (D-19).

### Established Patterns
- An MCP failure never breaks a chat turn; for the review route the opposite holds by decision (D-04): no diff means a clear error.
- All new data is scoped by `user_id`; the API key identifies the user whose GitHub server configuration, knowledge bases and review settings are used.
- Subprocesses only through asyncio, no shell; structlog for logging; secrets only from `.env` or user-entered settings, never in tracked files, logs or reports.
- The database always keeps full data; RAG fragments are merged only into the outbound prompt.
- Frontend: vanilla JS, `textContent`, `DOMPurify.sanitize()` for any HTML, source-level UI tests.
- Checks that could not run are recorded as not run. The user's app on 8000/8001 is never stopped by a check.

### Integration Points
- A new `.github/workflows/` file and a small caller script in the repository.
- A new review router in `agent/` registered in `agent/main.py`, with its schemas, review settings storage and REST routes, and a new Settings section in the UI.
- A git-based code indexer next to the existing knowledge base indexer, and a code chunker.
- The generation queue and limits (`agent/limits.py`, `run_limited_turn`) — the review makes several model calls (D-06) and should respect the same queue.
- Results of the control run under `eval_out/day32/`; `Day32_report.md` at the repository root.

</code_context>

<deferred>
## Deferred Ideas

- Inline review comments bound to diff lines — offered and not chosen (D-13); needs line validation and a fallback path.
- The app posting the comment itself through `github-mcp-server` in write mode — this is GHUB-03; not chosen (D-12).
- A `/review` chat command and reviewing from the browser — not chosen (D-03); would depend on the slash-command mechanism of Phase 23.
- The app watching for new PRs by its scheduler, without a GitHub Action — not chosen (D-01).
- Code files in the browser upload form — KBUP-07 as worded; not chosen (D-07).
- A GitHub-hosted runner variant with a cloud model and its own index — not chosen (D-01).

</deferred>

---

*Phase: 24-automated-pr-code-review-day-32*
*Context gathered: 2026-10-13*
