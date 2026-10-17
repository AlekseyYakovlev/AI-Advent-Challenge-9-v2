# Phase 27: AI release pipeline (Day 35) - Context

**Gathered:** 2026-10-17
**Status:** Ready for planning

<domain>
## Phase Boundary

A real task automated end to end: release preparation for this repository. The user types `/release` in a chat; the app collects the commits and merged changes since the last version tag, the AI writes a Russian changelog and picks the next version with a rationale, and after an explicit confirmation the app commits `CHANGELOG.md`, creates and pushes an annotated tag, and creates a draft GitHub Release through MCP. The report (`Day35_report.md`) states the task, the steps and where the AI takes part. Steps may be recorded as non-working where keys or write access are missing. Branch `Day35`; deliverable is a video (recorded by the user) plus code.

Out of scope: publishing the Release (it stays a draft), Pull Request write operations, a CI/Action trigger, RAG over docs for the changelog, a dedicated release settings section.

</domain>

<decisions>
## Implementation Decisions

### Launch and write
- **D-01:** Release preparation is started by a **`/release` slash command in the chat** (alongside `/help`, `/support`). Not a GitHub Action, not a CLI script.
- **D-02:** Two steps: `/release` produces a **plan** (range, filtered commit count, changelog draft, proposed version, title, rationale, the list of write steps); nothing is written. Writing happens only after an **explicit confirmation** — a button on the plan or `/release confirm`. The version (and title, D-14) can be overridden before confirming.
- **D-03:** **Code calls the MCP tools** (both reads and writes); the model never decides which tool to call. The AI's part is writing the changelog, choosing among version candidates and writing the rationale and title. Every MCP call is shown as an ordinary tool-call card in the chat.
- **D-04:** Writes go through MCP: a narrow **tag create + push** operation and a narrow **commit of `CHANGELOG.md`** operation in the own git server `mcp_servers/project_git.py` (a slice of PGIT-05), and a **draft Release** through a second `github-mcp-server` entry **without `--read-only`** (a slice of GHUB-03). If a needed tool or permission is missing, that step is recorded as non-working, not faked.
- **D-05:** If the tag is pushed but the Release step fails: **keep the tag**, show a per-step status report with the ready release text and manual instructions; confirming again **skips the steps already done**. No rollback.

### Changelog
- **D-06:** Range: **from the last version tag (now `v4.0`) to `HEAD`**, overridable as `/release v4.0..HEAD`. Sources: commits through `local-git`, `DayNN` merge commits as merged changes, and merged PRs from the GitHub server when there are any.
- **D-07:** Noise is **filtered by code rules** before the model sees anything: planning/state docs commits, `chore`, `wip`, executor worktree merges, commits that touch only `.planning/`. The dropped count is recorded and shown. The rest goes to the AI **grouped by phase**, and the AI writes human-readable items.
- **D-08:** Output is a **Russian Keep a Changelog section** (Добавлено / Исправлено / Изменено) **prepended to the root `CHANGELOG.md`** (created if missing). The app commits `CHANGELOG.md` and the tag points at that commit. The same section text is the body of the draft Release.
- **D-09:** Release branch is the **current working-copy branch**; the plan warns when it is not `main`. Writing is **blocked on a dirty working tree** or when **`HEAD` changed since the plan**. The commit touches **only `CHANGELOG.md`**. Push branch and tag, **never force**.
- **D-10:** The model is the **chat's own provider/model**, no RAG. No new Settings section beyond what is needed to name the repository path and the server entries.

### Version
- **D-11:** **Code computes candidates, AI chooses and explains.** Code derives candidates from the last tag and collects signals (feat/fix counts, `DayNN` merges, `BREAKING` markers); the AI picks **one of the candidates** and writes a 2–4 sentence rationale. A number outside the candidates from the AI is rejected.
- **D-12:** Format **`vX.Y`** like the existing tags (`v1.0`…`v4.0`). Two candidates: **next major** (e.g. `v5.0` — new milestone/week) and **next minor** (e.g. `v4.1`).
- **D-13:** A candidate that already exists as a tag (locally or on GitHub) is excluded. A manual version that is taken or **not greater than the last tag** gives a clear error and the plan **cannot be confirmed**.
- **D-14:** **Annotated tag.** The AI proposes a short release title; it becomes the tag message in the existing format (`vX.Y Week N: …`, as `v4.0 Week 6: Local LLM as a Private Service`) and the Release name. The title is editable together with the version before confirmation.

### Verification and report
- **D-15:** Control runs and the end-to-end check run on an **isolated copy pointed at a separate scratch GitHub repository** (its tags and Releases can be deleted). The real release in the project repository is done **once, by the user, on the video** — never by test or report runs.
- **D-16:** Control runs for the report use the **local LM Studio model only**.
- **D-17:** Changelog quality: a **code check** (every phase / `DayNN` merge in the range is mentioned, sections follow the Keep a Changelog format) plus a **short written comment** in the report on what the model missed or invented. No judge model.
- **D-18:** Scenarios in the sandbox, all recorded in `Day35_report.md`:
  1. Full path: `/release` → plan → confirm → `CHANGELOG.md` commit, tag, push, draft Release.
  2. No GitHub write access: tag pushed, Release fails → per-step report; re-confirm skips done steps.
  3. Blocks: dirty tree, `HEAD` changed after the plan, taken or smaller version.
  4. Manual version and range: `/release` with own range, overridden version and title.
- **D-19:** `Day35_report.md` is in Russian like earlier reports, states the task and where the AI takes part, and follows honest recording: a run that could not be made (LM Studio not running, no token, no write access) is recorded as not run / non-working, never as passed.

### Amendments after research (2026-10-17, confirmed by user)
- **D-20 (amends D-04):** `github-mcp-server` v2.0.2 (latest) has no tool that creates a Release, so the draft Release does NOT go through a second write-enabled `github-mcp-server` entry. Instead add one narrow tool `github_create_draft_release` to `mcp_servers/project_git.py`: `httpx` `POST /repos/{owner}/{repo}/releases` with `draft: true`, token read from the server's own env, registered only when a new `--allow-release` flag is passed. It shows as an ordinary tool card. The report states this deviation from D-04 and why. If the token or permission is missing, the step is recorded as non-working (D-04 rule still applies).
- **D-21 (clarifies D-09):** "Dirty working tree" is strict: any change blocks writing, including untracked files. The block message lists the offending paths.

### Claude's Discretion
- Exact names and parameters of the new narrow git tools and whether they sit behind the existing `--allow-write` flag of Phase 26 (D-05 there: `local-git` stays read-only; the write-enabled entry is `project-files`) — consistency with Phase 26 is expected.
- Exact filter rules and how commits are mapped to phases; how merged PRs are fetched and deduplicated against `DayNN` merges.
- How the plan is stored between `/release` and confirmation (per chat, in memory or DB) and how the confirm button is rendered.
- How the local model's context budget is handled for ~200 commits (grouping, truncation).
- Signals and rule details behind the candidate list; prompt wording.
- pytest coverage, the isolated-copy e2e script, and documentation updates (`docs/USER_GUIDE.md` form values for the write-enabled GitHub entry, `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `README.md`).
- What `Day35` is branched from, given the open Day33/Day34 branches.

</decisions>

<specifics>
## Specific Ideas

- Existing tag messages to match: `v1.0 Week 3: Agent Memory & Task State`, `v2.0 Week 4: MCP Integration`, `v3.0 Week 5: RAG`, `v4.0 Week 6: Local LLM as a Private Service`.
- From `v4.0` to `HEAD` there are ~195 commits, many of them `chore: merge executor worktree` — the noise filter matters.
- The video shows `/release` in the chat with tool-call cards, the plan, the confirmation and the resulting draft Release; the report shows the sandbox scenarios.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/ROADMAP.md` §Phase 27 — goal, dependencies (Phase 22, Phase 26)
- `.planning/REQUIREMENTS.md` — PGIT-01..05, GHUB-01..03 (write slices of PGIT-05 and GHUB-03 are used here)
- `.planning/PROJECT.md` — key decisions on own git MCP server and official `github-mcp-server`

### Prior phase decisions
- `.planning/phases/22-project-mcp-servers-day-31/22-CONTEXT.md` — `local-git` server rules (no shell, root gate, isError reasons, truncation), GitHub server setup
- `.planning/phases/23-help-command-verification-and-report-day-31/23-CONTEXT.md` — slash-command turn pattern, honest recording
- `.planning/phases/24-automated-pr-code-review-day-32/24-CONTEXT.md` and `24-CHECKS.md` — github-mcp-server live behaviour, token handling
- `.planning/phases/26-project-files-assistant-day-34/26-CONTEXT.md` — `--allow-write` flag, `project-files` entry, isolated clone for write runs, tool-card rendering

### Code and docs
- `mcp_servers/project_git.py` — own git server to extend
- `agent/command_turn.py` — shared slash-command turn mechanism (`/help`, `/support`)
- `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`
- `Day32_report.md`, `Day31_report.md` — report format and honest-recording style

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mcp_servers/project_git.py`: `run_git`, `check_ref_syntax`, `resolve_ref_or_range`, `git_recent_commits` — basis for reading the range and for the new narrow write tools.
- `agent/command_turn.py`: shared slash-command turn mechanism — `/release` can register beside `/help` and `/support`.
- `scripts/pr_review_ci.py`, `scripts/review_control.py`: script and control-run precedents; `scripts/e2e_*_playwright.py` for the isolated-copy e2e.

### Established Patterns
- Rejected MCP calls return `isError` with an English reason; tool results under 20 000 chars with an explicit truncation marker.
- Secrets only from `.env` / user-entered settings, never shown back or written to reports.
- Isolated copy on ports 18000/18001 with a scratch DB; the user's app on 8000/8001 is never touched.

### Integration Points
- Chat WS flow (`agent/ws.py`) and tool-call cards in `ui/static/app.js` (plan view + confirm button).
- «MCP серверы» settings form for the write-enabled `github-mcp-server` entry.

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 27-ai-release-pipeline-day-35*
*Context gathered: 2026-10-17*
