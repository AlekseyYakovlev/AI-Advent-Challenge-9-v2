# Phase 27: AI release pipeline (Day 35) - Research

**Researched:** 2026-10-17
**Domain:** Slash-command pipeline in a FastAPI/WebSocket chat app; git write tools in an own stdio MCP server; GitHub draft Release creation; changelog and version drafting with a local LLM
**Confidence:** HIGH on repo facts and the GitHub MCP gap (verified against the real binary); MEDIUM on design choices left to discretion

## Summary

The phase is mostly assembly on top of Phase 22 (`mcp_servers/project_git.py`, `github-mcp-server`), the command-turn pattern of Phases 23/25 and the tool-card UI. The pipeline is deterministic code with two small LLM calls (changelog + version choice/title/rationale), a plan held in memory per chat, and an explicit confirm that runs four write steps through MCP.

**Blocking finding: `github-mcp-server` has no tool that creates a GitHub Release.** I ran the installed v2.0.2 binary (`C:\Users\Aleksey\go\bin\github-mcp-server.exe`, also the latest upstream release, 2026-10-08) with `--toolsets=repos,pull_requests` (29 tools, no `--read-only`) and with `--toolsets=all` (91 tools). Release-related tools in both: only `get_latest_release`, `get_release_by_tag`, `list_releases`, `get_tag`, `list_tags`. No `create_release`. Upstream issues for it (#2853, #1909) are open. So D-04's "draft Release through a second `github-mcp-server` entry without `--read-only`" cannot work as written, and a write-enabled `github-mcp-server` entry is not needed for anything else in this phase. Neither `gh` nor any other release tool is installed (`gh` not found).

**Recommended resolution (stays inside D-03/D-04's intent: code calls an MCP tool, visible as a tool card):** add one narrow tool `github_create_draft_release` to the own server `project_git.py`, implemented with `httpx` against `POST /repos/{owner}/{repo}/releases` with `draft: true`, reading the token from the server's own env (`GITHUB_PERSONAL_ACCESS_TOKEN`, set in the MCP form). Read-side GitHub calls (`list_tags`, `list_releases`, `list_pull_requests`) keep using the existing read-only `github` entry. The planner should surface this deviation from D-04 in the plan's objective and in `Day35_report.md` (honest recording, D-19). If the user prefers to keep a literal D-04, the fallback is recording the Release step as non-working (`⊘`) with the ready text for manual publication, which the UI already supports (D-05, UI-SPEC item 5).

**Primary recommendation:** New module `agent/release_command.py` (grammar, plan building, version logic, confirm executor) + `agent/release_pipeline.py` if size demands; four new narrow write tools plus one read tool in `project_git.py` behind a new launch flag `--allow-release`; plan stored in `agent/state.py`; one `buildReleasePlanCard` in `ui/static/app.js`; all tests against a local bare git remote and a mocked GitHub REST endpoint.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Release preparation is started by a **`/release` slash command in the chat** (alongside `/help`, `/support`). Not a GitHub Action, not a CLI script.
- **D-02:** Two steps: `/release` produces a **plan** (range, filtered commit count, changelog draft, proposed version, title, rationale, the list of write steps); nothing is written. Writing happens only after an **explicit confirmation** — a button on the plan or `/release confirm`. The version (and title, D-14) can be overridden before confirming.
- **D-03:** **Code calls the MCP tools** (both reads and writes); the model never decides which tool to call. The AI's part is writing the changelog, choosing among version candidates and writing the rationale and title. Every MCP call is shown as an ordinary tool-call card in the chat.
- **D-04:** Writes go through MCP: a narrow **tag create + push** operation and a narrow **commit of `CHANGELOG.md`** operation in the own git server `mcp_servers/project_git.py` (a slice of PGIT-05), and a **draft Release** through a second `github-mcp-server` entry **without `--read-only`** (a slice of GHUB-03). If a needed tool or permission is missing, that step is recorded as non-working, not faked.
- **D-05:** If the tag is pushed but the Release step fails: **keep the tag**, show a per-step status report with the ready release text and manual instructions; confirming again **skips the steps already done**. No rollback.
- **D-06:** Range: **from the last version tag (now `v4.0`) to `HEAD`**, overridable as `/release v4.0..HEAD`. Sources: commits through `local-git`, `DayNN` merge commits as merged changes, and merged PRs from the GitHub server when there are any.
- **D-07:** Noise is **filtered by code rules** before the model sees anything: planning/state docs commits, `chore`, `wip`, executor worktree merges, commits that touch only `.planning/`. The dropped count is recorded and shown. The rest goes to the AI **grouped by phase**, and the AI writes human-readable items.
- **D-08:** Output is a **Russian Keep a Changelog section** (Добавлено / Исправлено / Изменено) **prepended to the root `CHANGELOG.md`** (created if missing). The app commits `CHANGELOG.md` and the tag points at that commit. The same section text is the body of the draft Release.
- **D-09:** Release branch is the **current working-copy branch**; the plan warns when it is not `main`. Writing is **blocked on a dirty working tree** or when **`HEAD` changed since the plan**. The commit touches **only `CHANGELOG.md`**. Push branch and tag, **never force**.
- **D-10:** The model is the **chat's own provider/model**, no RAG. No new Settings section beyond what is needed to name the repository path and the server entries.
- **D-11:** **Code computes candidates, AI chooses and explains.** Code derives candidates from the last tag and collects signals (feat/fix counts, `DayNN` merges, `BREAKING` markers); the AI picks **one of the candidates** and writes a 2–4 sentence rationale. A number outside the candidates from the AI is rejected.
- **D-12:** Format **`vX.Y`** like the existing tags (`v1.0`…`v4.0`). Two candidates: **next major** (e.g. `v5.0`) and **next minor** (e.g. `v4.1`).
- **D-13:** A candidate that already exists as a tag (locally or on GitHub) is excluded. A manual version that is taken or **not greater than the last tag** gives a clear error and the plan **cannot be confirmed**.
- **D-14:** **Annotated tag.** The AI proposes a short release title; it becomes the tag message in the existing format (`vX.Y Week N: …`, as `v4.0 Week 6: Local LLM as a Private Service`) and the Release name. The title is editable together with the version before confirmation.
- **D-15:** Control runs and the end-to-end check run on an **isolated copy pointed at a separate scratch GitHub repository**. The real release in the project repository is done **once, by the user, on the video** — never by test or report runs.
- **D-16:** Control runs for the report use the **local LM Studio model only**.
- **D-17:** Changelog quality: a **code check** (every phase / `DayNN` merge in the range is mentioned, sections follow the Keep a Changelog format) plus a **short written comment** in the report on what the model missed or invented. No judge model.
- **D-18:** Scenarios in the sandbox, all recorded in `Day35_report.md`: (1) full path; (2) no GitHub write access: tag pushed, Release fails, re-confirm skips done steps; (3) blocks: dirty tree, `HEAD` changed after the plan, taken or smaller version; (4) manual version and range.
- **D-19:** `Day35_report.md` is in Russian, states the task and where the AI takes part, and follows honest recording: a run that could not be made is recorded as not run / non-working, never as passed.

### Claude's Discretion
- Exact names and parameters of the new narrow git tools and whether they sit behind the existing `--allow-write` flag of Phase 26 (D-05 there: `local-git` stays read-only; the write-enabled entry is `project-files`).
- Exact filter rules and how commits are mapped to phases; how merged PRs are fetched and deduplicated against `DayNN` merges.
- How the plan is stored between `/release` and confirmation and how the confirm button is rendered.
- How the local model's context budget is handled for ~200 commits.
- Signals and rule details behind the candidate list; prompt wording.
- pytest coverage, the isolated-copy e2e script, and documentation updates (`docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `README.md`).
- What `Day35` is branched from, given the open Day33/Day34 branches.

### Deferred Ideas (OUT OF SCOPE)
None in CONTEXT.md. Out of scope per the phase boundary: publishing the Release (stays a draft), Pull Request write operations, a CI/Action trigger, RAG over docs for the changelog, a dedicated release settings section.
</user_constraints>

<phase_requirements>
## Phase Requirements

No requirement IDs are mapped (ROADMAP: TBD). Suggested prefix `REL-`; derived from decisions:

| ID (suggested) | Description | Research Support |
|----|-------------|------------------|
| REL-01 | `/release` grammar, plan in memory per chat, `/release confirm`, ephemeral notices | Command pattern, plan storage, WS control frame |
| REL-02 | Range resolution, noise filter, phase grouping, candidates, DayNN/PR sources | Range data, filter measured on this repo, version logic |
| REL-03 | LLM changelog + version choice with candidate validation | Prompt/budget, strict JSON, local-model handling |
| REL-04 | Guards: dirty tree, HEAD changed, taken/smaller version, branch warning | Git commands, validation order |
| REL-05 | Narrow write tools: commit CHANGELOG.md, annotated tag, push (no force) | Tool design, `--allow-release` flag |
| REL-06 | Draft GitHub Release | GitHub REST, MCP gap, token permission |
| REL-07 | Step state machine: partial failure, retry skipping done steps | Executor design |
| REL-08 | Plan card UI | 27-UI-SPEC (approved) |
| REL-09 | Isolated-copy e2e on scratch repo, pytest, `Day35_report.md`, docs | Test strategy |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| `/release` grammar, plan building, version logic, step executor | API / Backend (`agent/`) | — | Orchestration in code; the model never chooses tools (D-03) |
| Reading commits/tags, writing CHANGELOG.md, tag, push | MCP server `project_git.py` (separate stdio process) | — | Only place that touches git; path/ref gates already live there |
| Draft Release creation | MCP server `project_git.py` (HTTP to GitHub) | — | `github-mcp-server` has no create-release tool (verified) |
| Reading GitHub tags/PRs/releases | Existing `github` MCP entry (read-only) | — | Already approved and token-wired (Phases 22/24) |
| Changelog text, version pick, title, rationale | LLM (chat's provider) via Backend | — | The only AI part (D-03, D-10) |
| Plan storage between `/release` and confirm | Backend in-memory (`agent/state.py`) | — | Per-chat, lost on restart (UI-SPEC item 8); cleanup on chat delete |
| Plan card, version/title inputs, per-step list | Browser (`ui/static/app.js`) | — | Vanilla JS; DOMPurify for changelog markdown |
| Validation of version | Backend (authoritative) | Browser (instant echo) | UI-SPEC item 3 |

## Standard Stack

No new packages. Everything below is already in `requirements.txt` or the stdlib.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `mcp` (FastMCP + client) | 1.30.0 [VERIFIED: pip list] | New tools in `project_git.py`; calls from the agent | Already the project's MCP layer |
| `httpx` | 0.28.1 [VERIFIED: pip list] | GitHub REST call inside the MCP server | Already installed (dependency of `mcp`); async; `respx`-mockable |
| `git` | 2.39.1.windows.1 [VERIFIED: git --version] | All local git operations (no shell, fixed argv) | Existing `run_git` runner |
| `packaging` | 26.3 [VERIFIED: pip list] | Not required: versions are `vX.Y`, compare as `(int, int)` tuples | Do not add; hand-parse with a regex |
| `github-mcp-server` | 2.0.2 [VERIFIED: binary `--version`; latest upstream release 2026-10-08] | Read tools only: `list_tags`, `list_releases`, `list_pull_requests` | Existing `github` entry |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `respx` | 0.21+ (project) | Mock GitHub REST in server tests | Release tool tests |
| Playwright script pattern | project | `scripts/e2e_release_playwright.py` modelled on `e2e_mcp_git_playwright.py` | Isolated-copy end-to-end |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Own `github_create_draft_release` tool | `gh release create --draft` | `gh` not installed; not MCP; adds an external dependency |
| Own tool | Agent calls GitHub REST directly (no MCP) | Violates D-03/D-04 (no tool card, different token path) |
| Own tool | Wait for upstream PR #2853 | Unknown timing; cannot depend on it |
| Own tool | `create_or_update_file`/`push_files` of `github-mcp-server` | Cannot create tags or Releases; wrong abstraction |

**Installation:** none.

## Package Legitimacy Audit

No external packages are added in this phase (`mcp`, `httpx`, `respx`, `psutil` are existing pinned dependencies). slopcheck not applicable.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
 user "/release [A..B]"                         user clicks "Подтвердить релиз"
        |                                        or "/release confirm [vX.Y] [title]"
        v                                                   |
 ws_chat (agent/ws.py) --parse_release()--+                 v
        |                                  |        release confirm handler (chat lock)
        v                                  |                |
 PLAN PHASE (no writes)                    |      re-validate: version, dirty tree, HEAD sha
  1 git_status / current branch  (MCP card)|                |
  2 resolve last tag + tags list (MCP card)|      STEP 1 commit CHANGELOG.md   (MCP release_commit_changelog)
  3 github list_tags/PRs (MCP card, opt.)  |      STEP 2 annotated tag          (MCP release_create_tag)
  4 release range log, paged (MCP card)    |      STEP 3 push branch + tag      (MCP release_push)
  5 code: noise filter -> group by phase   |      STEP 4 draft Release          (MCP github_create_draft_release)
  6 code: candidates + signals             |                |
  7 LLM #1 changelog (Russian)             |      per-step frame: running/done/skipped/failed/unavailable
  8 LLM #2 pick candidate+title+rationale  |      (each MCP call = ordinary tool_call card)
  9 store ReleasePlan in state.release_plans[chat_id]
 10 send "release_plan" frame ------------> browser buildReleasePlanCard
                                                           |
                                                           v
                                            "release_result" frame updates same card
```

### Recommended Project Structure
```
agent/
├── release_command.py     # parse_release(), grammar, notices texts, ReleasePlan dataclass
├── release_pipeline.py    # range collect, noise filter, grouping, candidates, prompts, LLM calls, validators
├── release_steps.py       # confirm executor: step state machine, MCP call helper emitting tool_call frames
├── state.py               # + release_plans: dict[int, ReleasePlan], cleanup in cleanup_chat_caches
mcp_servers/project_git.py # + --allow-release flag, release_* tools, github_create_draft_release
ui/static/app.js           # + buildReleasePlanCard, release_plan / release_result / release_progress frames
scripts/e2e_release_playwright.py
tests/test_release_*.py
Day35_report.md
```
Keep modules small (project rule: functions up to ~50 lines). `ws.py` is already 1 500 lines and Phase 25 is editing it: add only a thin hook (parse + delegate), not logic.

### Pattern 1: Command hook next to `/help` (and `/support`)
`ws_chat` currently special-cases `help_command.is_help_command(raw)` before `MessagePayload` validation and `_run_chat_turn` re-parses. `/release` does not use RAG, a knowledge base or chat history, so it must NOT go through `command_turn.CommandSpec`/`prepare_command_turn` (those are KB-retrieval specific). Reuse only the **notice frame** idea (`command_turn.notice_frame`, type `help_notice`, `stored: False`) and add new frame types: `release_plan`, `release_progress`, `release_result`. Hook order: Phase 25 will have added `/support` to the same two places in `ws.py`; sequence Phase 27's `ws.py` edit after Phase 25 lands, or expect a merge conflict (Open Question 3).

`/release` grammar (regex, like `_HELP_PATTERN`): `^\s*/release(?:\s+(?P<arg>.*))?$`, args: empty -> default range; `v4.0..HEAD` form (validate with the existing `check_ref_syntax` rules on the server side); `confirm [vX.Y] [title...]`. A `/release` message is not stored as a chat message (like `/help` notices) and does not call the model's chat flow.

### Pattern 2: Code calls MCP tools and still shows cards
D-03 requires tool-call cards for code-initiated calls. The building blocks exist:
- Resolve server rows with `mcp_config.list_servers(session, user_id)`; connect with `ensure_connected(...)` as in `agent/review_github.py::_live_session` (lines ~118-140).
- Build an `McpToolBinding(exposed_name, user_id, server_id, server_name, tool_name)` and call `agent.mcp_tools.call_mcp_tool(binding, args)` (30 s timeout, 20 000-char cap, returns `{ok, is_error, text, truncated}`).
- Emit the card with the same frame shape as `ws._tool_call_frame` (`ToolCallEvent`: `tool_call_id, name, server, tool, arguments (<=2000), ok, result (<=4000), truncated`). Reuse/extract `_tool_call_frame` rather than duplicating; the frontend `case 'tool_call'` -> `appendToolCallCard` needs no change.
- Push needs a longer timeout than 30 s only in unusual cases; the app timeout `settings.MCP_TOOL_CALL_TIMEOUT` applies. Check its value; if it is below the push timeout, call `session.call_tool` directly with `asyncio.wait_for(..., 120)` as `review_github._read` does (REVIEW_MCP_TIMEOUT 120 s).
- Tool-call cards are live-only (STATE.md: Phase 23-03). `release_plan` cards are also live-only (UI-SPEC item 8).

### Pattern 3: Narrow write tools, gated by a launch flag
Follow the Phase 26 design (D-05 there) but with its own flag so that the Phase 26 `project-files` entry never gains push power:
- New launch flag `--allow-release`. Without it the server exposes exactly what it does today (existing tests that assert the five tools stay green). With it, add the tools below.
- Recommended form entry (second entry of the same script): name `release-git`, command `python`, args `mcp_servers/project_git.py --root=<repo> --allow-release --timeout=120`, env `GITHUB_PERSONAL_ACCESS_TOKEN=...` (only needed for the Release tool). Slug `release-git` is 11 chars (limit 20); exposed names `mcp__release-git__<tool>`.
- Tools (names are discretionary):
  - `release_range_commits(range, page)`: read; JSON lines `{hash, parents, subject, dirs[], files_n}` 100 per page with an explicit `next_page` field, so each result stays under the 20 000-char cap (existing `git_recent_commits` has no range paging, max 50, no file info). Can be exposed without the flag since it is read-only; put it on the default entry too if convenient, but keep the `--allow-release` gating for writes.
  - `release_commit_changelog(version, section)`: prepend `section` to root `CHANGELOG.md` (create with the standard header if missing; write UTF-8 with `\n`), `git add -- CHANGELOG.md`, `git commit -m "docs(release): <version> changelog" -- CHANGELOG.md`; return the new commit SHA. Refuses when anything other than `CHANGELOG.md` is staged (guard against sweeping up other staged files) and returns `isError` with reason.
  - `release_create_tag(version, message, commit)`: validate `^v\d+\.\d+$`, `git tag -a <v> -m <msg> <commit>`; refuses if the tag exists. Message passed as a single argv element (no shell).
  - `release_push(branch, tag)`: `git push origin refs/heads/<branch>:refs/heads/<branch> refs/tags/<tag>:refs/tags/<tag>`. Never `--force`, never `--force-with-lease`, never `--tags`, never delete refs; reject branch/tag names failing `check_ref_syntax` plus the version regex.
  - `github_create_draft_release(owner, repo, tag, name, body)` (details under Pattern 4).
- Reuse `run_git`, `check_ref_syntax`, `resolve_ref`, `_git_error`; `git_env()` removes inherited `GIT_*` variables and sets `GIT_TERMINAL_PROMPT=0` (good for push: fails instead of hanging).
- The server's default `--timeout` is 10 s; push over SSH needs a larger value. Per-call timeout for `release_push` should be separate (e.g. 60 s), not the global `--timeout`, so reads stay fast. `run_command` already kills the process tree on timeout.

### Pattern 4: Draft Release via GitHub REST (the gap workaround)
`POST https://api.github.com/repos/{owner}/{repo}/releases` with JSON `{tag_name, name, body, draft: true, target_commitish?}`, headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`. Success is 201; 422 is validation/spam [CITED: docs.github.com/en/rest/releases/releases]. A fine-grained PAT needs **Contents: write** for create and Contents: read for list [CITED: docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens]. `target_commitish` is unused if the tag already exists, which is our case because steps 2-3 push the tag first.
- Idempotency (re-confirm after partial failure): `GET /releases/tags/{tag}` does **not** return drafts [CITED], so to detect an existing draft list `GET /repos/{owner}/{repo}/releases` (drafts are listed only for users with push access [CITED]) and look for `tag_name == tag`. The plan's own step state (in memory) is the primary "already done" signal; the list check protects against an agent restart between retries.
- Parse `owner/repo` from the `origin` URL in code (`git@AiAdventAgentV2:AlekseyYakovlev/AI-Advent-Challenge-9-v2.git` is an SSH **host alias**, so the owner/repo is the path part after `:`; handle `https://github.com/o/r(.git)` too) with the same `REPO_PATTERN` as `review_github`. Do not use the SSH alias as a hostname.
- Never echo the token; map 401 -> "token rejected", 403/404 -> "no write access to the repository (Contents: write required)", 422 -> show GitHub's `message`. The English reason is shown verbatim on the card (UI-SPEC).
- Return the release `html_url` in the result text so the UI can link it; parse it in code with a strict `https://github.com/` prefix check before rendering as a link.

### Pattern 5: Plan object and step state machine
```python
@dataclass
class ReleasePlan:
    chat_id: int; user_id: int; range_spec: str; branch: str; head_sha: str
    last_tag: str; candidates: list[str]; version: str; title: str; rationale: str
    section_md: str; counts: dict[str, int]; steps: dict[str, StepState]  # commit/tag/push/release
    commit_sha: str | None; running: bool; created_at: datetime
```
- Stored in `state.release_plans[chat_id]` (one per chat; a new `/release` replaces it and marks the old card stale). Add the pop to `cleanup_chat_caches` and extend `tests/test_cascade_delete.py` (project rule).
- Confirm executes under the per-chat lock (`state.chat_locks[chat_id]`), ignores a duplicate confirm while `running` (UI-SPEC). Re-validate at confirm time: version format/greater/not taken (locally + remote tags), `git_status` clean, `HEAD` equals `plan.head_sha`. Changing version/title after the commit step (retry) must not rewrite a finished commit: lock version and title once step 1 is done, otherwise tag and CHANGELOG disagree.
- Step order and "done" semantics: commit -> tag -> push -> release. Retry skips steps with status done. Step 3 failure (e.g. SSH) leaves a local commit and tag: surface it and allow retry (same skip rule). Step 4 `unavailable` when no `github` token/permission.
- No rollback anywhere (D-05).

### Pattern 6: Range, noise filter, phase grouping (measured on this repo)
- Last tag: the highest `^v\d+\.\d+$` tag reachable from `HEAD` (`git tag --merged HEAD`) sorted numerically, not lexicographically (`v10.0` vs `v9.0`). Locally: v1.0, v2.0, v3.0, v4.0. Remote has only v1.0, v3.0, v4.0 (v2.0 was never pushed) [VERIFIED: `git ls-remote --tags origin`], so "taken" must check both sets. There is also a non-version tag `backup/day30-before-reset` that the regex must ignore.
- Measured on `v4.0..HEAD` on the current branch: 210 commits total, 201 non-merge; after the proposed filter 88 remain (feat 42, test 19, docs 16, fix 9, refactor 2); 87 of those carry a phase scope like `feat(25-04)` or `(quick-re4)`. Merge commits in the range are `chore: merge executor worktree` (noise) and `Merge branch 'Day32': ...` (the DayNN merge to report as a merged change).
- Filter rules (code): drop a commit if (a) type is `chore` or `wip`, (b) subject starts with `Merge` and is a worktree merge, (c) all touched files start with `.planning/`, (d) it touches nothing. Keep `Merge branch 'DayNN'` as a merged-change entry. The `docs(…)` commits that touch `docs/` or `README.md` are user documentation and stay; `docs(NN-MM)` that only touch `.planning/` are dropped by rule (c). Show `dropped` always.
- Phase grouping: scope regex `\((?P<phase>\d+)(?:-\d+)?\)` on the subject, plus `quick-…` scope and an "other" bucket. Do not read ROADMAP for phase names; let the model name groups from subjects (CONTEXT: grouping by phase, AI writes items).
- Context budget for the local model: 88 subjects is roughly 9-10 K characters (~3-4 K tokens), which fits a typical LM Studio context; still count with `count_tokens` and, if the prompt exceeds ~60% of `context_length`, shrink by dropping `test` commits first, then collapse a group to its first N subjects with an explicit "+M more" line (and record this in the plan facts). Pass only hash-free subjects, grouped, plus the list of DayNN merges. Keep `dirs[]` out of the prompt.
- Merged PRs: call `list_pull_requests` (`state=closed`, base = current default branch) on the read-only `github` entry only when that entry is available; keep items with `merged_at` set and `merge_commit_sha` inside the range; dedupe against DayNN merge commits by SHA. The project repo may have none; zero PRs is the normal case, not an error. GitHub unavailability only adds a warning line, never blocks the plan.

### Pattern 7: Version candidates and validation
- Parse last tag `(major, minor)`; candidates `[(major+1, 0), (major, minor+1)]` formatted `vX.Y`; remove any in the taken set (local and remote tags, and optionally release tags from `list_releases`). If both are removed, the plan is blocked with the "taken" copy.
- Signals for the prompt: counts of feat/fix, list of `DayNN` merges, any `BREAKING`/`!:` marker, count of kept commits. Reasonable default if the model fails: the minor candidate unless `BREAKING` or a DayNN merge exists, then major.
- LLM #2 returns strict JSON `{"version": "...", "title": "...", "rationale": "..."}`; parse leniently (strip code fences and `<think>` blocks; reuse approach from `agent/rag_llm.py` / `titles.clean_title`). If `version` is not in `candidates` -> reject and use the deterministic default (and log `release_version_rejected`), per D-11. Rationale length 2-4 sentences; title <= ~80 chars, no newlines, strip markup (it ends up in a tag message and a Release name).
- Tag message: `f"{version} Week {major + 2}: {title}"` for major candidates (observed mapping v1.0->Week 3 ... v4.0->Week 6 [VERIFIED: `git tag -l -n1`], so v5.0 -> Week 7). For a minor version there is no Week; use `f"{version}: {title}"`. This mapping is an inference (Assumption A1); the user can edit the title but the "Week N" prefix is built by code.
- Manual override validation order: format `^v\d+\.\d+$` -> greater than last tag as numeric tuple -> not in taken set; same function used by plan building, UI echo data (send `last_tag`, `taken` list in the plan frame) and confirm.

### Pattern 8: LLM calls
- Use the chat's resolved client (`resolve_client(chat.user_id, payload.provider_id)` as `_run_chat_turn` does) and `client.complete_chat_detailed(messages, model, temperature=0, max_tokens=...)`, non-streaming, under `asyncio.wait_for` (see `rag_llm.complete_stage`). Two calls: changelog (large `max_tokens`, e.g. 1500) and decision JSON (small). Non-streaming is fine: progress comes from tool-card frames, and `release_progress` frames cover the LLM wait (UI-SPEC planning state: typing indicator).
- Local model quirks seen in this project: reasoning models need `extra_body={"reasoning_effort": ...}` toggled off with a retry on HTTP 400/422 (`rag_llm._complete`); reuse that helper (it is private; extract or import carefully) and handle `content is None` / `has_reasoning` empty-content cases as "model failed" with the UI copy "Не удалось получить черновик от модели…".
- Changelog output contract: ask for three headings exactly `### Добавлено`, `### Исправлено`, `### Изменено` (omit empty ones), a `## [vX.Y] - YYYY-MM-DD` heading built **by code**, not by the model (date from `datetime.now(timezone.utc)` or local date; version is known only after step 2, so build the heading from the plan's final version at confirm time and keep the body free of it). Code post-checks: sections present and only those three, no content outside them, every phase number and every DayNN merge appears in the text (D-17); on failure retry once with the missing list, then keep the draft and record the miss in the plan facts rather than inventing text.
- Prompt injection: commit subjects are untrusted input in the prompt. Delimit them, tell the model they are data, and never let model output choose tools, refs or paths (it does not; only free text and one of two version strings).

### Anti-Patterns to Avoid
- **Letting the model call tools** (violates D-03) or routing `/release` through the chat tool loop.
- **Using `--tags` or `git push --follow-tags`/`--force*`:** pushes unrelated local tags (e.g. `backup/day30-before-reset`, `v2.0`) or rewrites history. Use explicit refspecs.
- **`git add -A` / `git commit -a`:** would commit the dirty-tree files; D-09 says only `CHANGELOG.md`.
- **Treating the SSH host alias as a hostname** when deriving `owner/repo` for the REST call.
- **Lexicographic tag sort.**
- **Rewriting an already-committed CHANGELOG on retry** with a changed version.
- **Storing the plan in the DB or as a chat message:** UI-SPEC says live-only, and a persisted plan outliving a HEAD change invites stale writes.
- **Real release from tests/scripts:** only the scratch repository (D-15).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process running with timeout and tree kill | New subprocess helper | `project_git.run_command` / `run_git` | Handles Windows git launcher orphan, output cap, no shell |
| Ref and path validation | New regexes | `check_ref_syntax`, `resolve_ref`, `resolve_inside_root` | Already reviewed (Phase 22) |
| MCP connect/call/timeout/cap | Direct stdio code | `ensure_connected`, `get_live_session`, `call_mcp_tool` (`agent/mcp_client.py`, `agent/mcp_tools.py`) | Existing lifecycle and error mapping |
| Tool-call card frame | Custom card | `ws._tool_call_frame` / `ToolCallEvent` + `appendToolCallCard` | D-03 requires ordinary cards |
| Markdown rendering/sanitising | Own renderer | `DOMPurify.sanitize(marked.parse(...))` (UI-SPEC) | XSS rule in CLAUDE.md |
| Non-streaming LLM call with reasoning toggle | New HTTP code | `client.complete_chat_detailed` + the `rag_llm._complete` fallback logic | Existing provider abstraction (DeepSeek/LM Studio) |
| GitHub release creation | `gh`, ad-hoc `urllib` in the agent | One `httpx` tool in the MCP server | Keeps token out of the agent, gives a real tool card |
| Semver ordering | `packaging` / string compare | `(int, int)` tuple from `^v(\d+)\.(\d+)$` | Format is fixed to `vX.Y` |
| Local "remote" for tests | Network | `git init --bare` temp dir as `origin` | Real tag/push semantics offline |

**Key insight:** every risky step (arbitrary refs, paths, force pushes, secrets) already has a gate in `project_git.py`; the safest implementation adds a handful of fixed-argv tools there and keeps all decisions in code.

## Runtime State Inventory

Not a rename/refactor phase: omitted.

## Common Pitfalls

### Pitfall 1: Assuming `github-mcp-server` can create a Release
**What goes wrong:** Plan builds a write-enabled `github` entry; the tool does not exist.
**Why it happens:** CONTEXT D-04 was written from the roadmap slice name GHUB-03, not from the tool list.
**How to avoid:** Use Pattern 4; document the deviation. Evidence: `python scripts/mcp_list_tools.py ...github-mcp-server.exe stdio --toolsets=all` lists 91 tools, none create a release.
**Warning signs:** `Unknown tool` / `isError` from MCP.

### Pitfall 2: Dirty tree is the user's normal state
**What goes wrong:** `git status` right now shows modified `.planning/*`, `requirements.txt`, `tests/conftest.py` and an untracked `.planning/.pending-auth-captures.jsonl`; a strict "dirty = block" rule will block the real release on the video unless the tree is cleaned first.
**How to avoid:** Implement D-09 exactly (block), make the block message list the offending paths (from `git_status`), and put "clean tree" in the video preparation notes. Decision needed on untracked files (Open Question 2). Staged-but-uncommitted changes must block regardless.
**Warning signs:** Plan shows "Заблокировано" in every demo.

### Pitfall 3: MCP SDK child environment is a whitelist
**What goes wrong:** The server subprocess does not inherit the shell env (on Windows only `APPDATA, HOMEDRIVE, HOMEPATH, LOCALAPPDATA, PATH, PATHEXT, PROCESSOR_ARCHITECTURE, SYSTEMDRIVE, SYSTEMROOT, TEMP, USERNAME, USERPROFILE` plus the form's env) [CITED: 22-RESEARCH.md, verified in mcp SDK]. The token must be put in the form env.
**Checked:** `git ls-remote --tags origin` under exactly that whitelist works on this machine (SSH key via `~/.ssh/config` alias `AiAdventAgentV2`, `IdentitiesOnly yes`), so `git push` should also work from the server process [VERIFIED locally for ls-remote; push itself not run]. The key must not require a passphrase prompt (stdin is DEVNULL, `GIT_TERMINAL_PROMPT=0`).
**How to avoid:** Run a push once in the isolated copy against the scratch repo before the report runs; never test push against the project remote.

### Pitfall 4: `git_env()` strips every `GIT_*` variable
`GIT_SSH_COMMAND`, `GIT_ASKPASS` and similar from the user's environment are removed. Fine for the current SSH-config setup; mention in docs if the scratch remote needs a different transport (HTTPS needs a credential helper, which also may not work non-interactively: prefer SSH or a local bare repo).

### Pitfall 5: Tool-result cap and tool-card previews
`call_mcp_tool` cuts text at 20 000 chars; the WS frame preview is cut at 4 000 (`TOOL_EVENT_PREVIEW_CHARS`). 210 commits as JSON lines exceed 20 000. Page the range tool (100 per page) and parse the full text in code, never from the card preview.

### Pitfall 6: Annotated tag needs an identity
`git tag -a` and `git commit` fail without `user.name`/`user.email`. In the scratch clone and in pytest temp repos set them locally (`git config user.name ...`), not globally. Surface git's last stderr line as the `isError` reason.

### Pitfall 7: CRLF noise and encoding
Windows git prints `LF will be replaced by CRLF` warnings to **stderr**; never merge stderr into results (existing rule). Write `CHANGELOG.md` as UTF-8 with LF and read the existing file as UTF-8; Russian text must not pass through the console codepage.

### Pitfall 8: `HEAD changed since plan` race and the chat lock
Plan creation and confirm happen at different times; compare `rev-parse HEAD` at confirm to `plan.head_sha`. Hold `chat_locks[chat_id]` during planning and confirm, but do not hold it while the user thinks (the plan is just data between the two).

### Pitfall 9: Local model returns invalid JSON or invents versions
D-11 already requires rejecting out-of-candidate versions; also handle code fences, `<think>`, trailing text, and empty `content`. Always have a deterministic fallback for version and a fixed fallback title (e.g. derived from the dominant phase) so the plan is still produced and the failure is shown, not fatal. Changelog failure, by contrast, is shown as the provider-failure copy and no plan is stored.

### Pitfall 10: LM Studio is not running now
`curl http://localhost:1234/v1/models` returned nothing [VERIFIED]. Control runs (D-16) need it started; if not, record them as "не запускалось" (D-19).

### Pitfall 11: Release body vs tag existing on the remote
A draft Release with `tag_name` of an existing remote tag is fine; if push failed (tag not on the remote) the REST call would otherwise create the tag from `target_commitish` at the default branch head, silently tagging the wrong commit. Pass `target_commitish=<commit sha>` always and refuse the Release step when step 3 is not done.

## Code Examples

### MCP tool with a launch-flag gate (shape follows existing tools)
```python
# Source: mcp_servers/project_git.py existing pattern (FastMCP, ToolInputError -> isError)
if args.allow_release:
    @server.tool(structured_output=False)
    async def release_create_tag(version: str, message: str, commit: str) -> str:
        """LOCAL: create an annotated tag at a commit. Never moves or deletes an existing tag."""
        if not _VERSION_TAG.fullmatch(version):
            raise ToolInputError('version must look like v4.1')
        sha = await resolve_ref(commit)
        existing = await run_git(["tag", "-l", "--", version])
        if existing.text.strip():
            raise ToolInputError(f"tag {version} already exists; choose a higher version")
        made = await run_git(["tag", "-a", version, "-m", message, sha])
        if made.returncode != 0:
            raise _git_error(made, "tag")
        return f"Created annotated tag {version} at {sha[:8]}"
```
(Tools must be registered conditionally, e.g. inside a `register_release_tools()` called from `main()` after `configure(...)`, because `@server.tool` at import time would expose them without the flag.)

### Push without force, explicit refspecs
```python
argv = ["push", "origin",
        f"refs/heads/{branch}:refs/heads/{branch}",
        f"refs/tags/{tag}:refs/tags/{tag}"]   # never --force, --tags, --delete, --mirror
```

### Draft release REST call
```python
# Source: docs.github.com/en/rest/releases/releases (Create a release)
async with httpx.AsyncClient(timeout=30.0) as client:
    resp = await client.post(
        f"https://api.github.com/repos/{owner}/{repo}/releases",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        json={"tag_name": tag, "target_commitish": sha, "name": name,
              "body": body, "draft": True, "prerelease": False},
    )
```
Token is read from `os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]` in the server; absent -> `isError` "GITHUB_PERSONAL_ACCESS_TOKEN is not set in this server's environment" (maps to the `⊘ недоступно` status).

### Tag helpers
```python
_VERSION_TAG = re.compile(r"v(\d+)\.(\d+)")
def parse_version(tag: str) -> tuple[int, int] | None:
    m = _VERSION_TAG.fullmatch(tag); return (int(m[1]), int(m[2])) if m else None
last = max((v for t in tags if (v := parse_version(t))), default=None)
candidates = [f"v{last[0]+1}.0", f"v{last[0]}.{last[1]+1}"]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| GitHub MCP "does everything" | `github-mcp-server` v2.0.2 has no release-write tools; PR #2853 open | as of 2026-10-17 | Own REST tool needed |
| `go install` / Docker for the GitHub server | Prebuilt Windows zip, binary already at `C:\Users\Aleksey\go\bin` | Phase 22 | No install work |
| Dynamic `@server.tool` at import | Conditional registration for flag-gated tools | this phase | Keeps default entry read-only |

**Deprecated/outdated:** none relevant.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Tag message "Week N" = major + 2 for major releases; minor releases have no Week | Pattern 7 | Title format differs from what the user wants; easy to change, but user should confirm |
| A2 | Fine-grained PAT for the scratch repo needs Contents: write for both push-over-API-free flow and release creation (docs say Contents write for create release) | Pattern 4 | Token creation instructions in docs wrong; release step fails with 403/404 |
| A3 | `list_pull_requests` of `github-mcp-server` returns `merged_at`/`merge_commit_sha` per item | Pattern 6 | PR source needs another call (`pull_request_read`) or is dropped; low impact since PRs may be none |
| A4 | An SSH push from the MCP server process works non-interactively (ls-remote verified, push not run) | Pitfall 3 | Push step unavailable until credentials are arranged |
| A5 | `docs(NN-MM)` commits touching only `.planning/` are the bulk of the 113 filtered commits; kept count 88 comes from my simulated filter, not the final rules | Pattern 6 | Final numbers in the report differ; irrelevant to correctness |
| A6 | `MCP_TOOL_CALL_TIMEOUT` (30 s per mcp_tools) is too short for push over SSH on slow links | Pattern 2 | Push shows a timeout error although it succeeded; mitigate by direct session call with 120 s |
| A7 | User will create the scratch GitHub repository and token for the sandbox (D-15) | Environment | Scenarios 1-2 cannot be run against GitHub; record as not run |

## Open Questions (RESOLVED)

1. **D-04 cannot be met literally (no create-release tool).**
   - Known: verified against v2.0.2, `--toolsets=all`, 91 tools.
   - Unclear: user acceptance of the own REST tool in `project_git.py` (adds network + token to that file, which today is local-only and self-contained).
   - Recommendation: take the own-tool route behind `--allow-release`; call it out in PLAN objective, docs and `Day35_report.md`. Alternative: record Release as non-working by design (weaker demo).
   - **RESOLVED:** D-20 — own narrow `github_create_draft_release` tool in `project_git.py` behind `--allow-release` (user-confirmed); implemented in 27-01 Task 3, deviation documented in 27-07 and `Day35_report.md` (27-08).

2. **Does an untracked file count as "dirty"?**
   - Known: D-09 says blocked on dirty tree; current tree has modified tracked files and an untracked file.
   - Unclear: untracked-only handling.
   - Recommendation: block on staged/unstaged tracked changes and on untracked files that are not ignored (strict, matches D-09); the message lists the paths. Revisit only if the user complains.
   - **RESOLVED:** D-21 — strict: untracked (non-ignored) files block the release and the message lists the paths (27-01 `release_context.dirty`, 27-02 `dirty_paths`, enforced in 27-03/27-04).

3. **Execution order and `ws.py` conflicts with Phases 25/26.**
   - Known: Phase 25 (executing, `Day33`) edits `ws.py`/`command_turn`; Phase 26 plans exist but nothing is implemented (no `--allow-write` in the code; no read/search tools). Phase 27 depends on 26 per ROADMAP, but nothing in the recommended design needs Phase 26 code except the tool-card diff view and the flag convention.
   - Recommendation: implement Phase 27 server tools in their own `register_release_tools()` and flag, so they do not depend on 26; plan `Day35` from whatever branch holds Phases 25/26 once merged (or from `Day33` plus a merge of `Day34`); verify `parse_args` still passes both flags.
   - **RESOLVED:** separate `--allow-release` flag and `register_release_tools()` (27-01), independent of Phase 26; a thin `ws.py` hook (27-06). Executed on branch `Day35` created from `Day33` (holds the Phase 25 `ws.py` hooks) by the execute-phase orchestrator before wave 1.

4. **How is the UI "Пересоздать план"/button wired to the server?**
   - Recommendation: a typed WS control frame `{"type": "release_confirm", "version": "...", "title": "..."}` handled in `ws_chat` before `MessagePayload.model_validate` (same place as `is_help_command`), plus the typed `/release confirm [version] [title]` equivalent; both call one handler.
   - **RESOLVED:** `release_confirm` WS frame plus typed `/release confirm` through one handler (frame contract in 27-02, wiring in 27-06, button in 27-05).

5. **Which repository path does the pipeline use?**
   - The `release-git` MCP server's `--root` defines it (CONTEXT D-10: no new settings). The agent finds the server by name; default name `release-git`, fallback error "Сервер release-git не настроен" with docs pointer. The scratch/e2e copy simply points `--root` at the isolated clone.
   - **RESOLVED:** per D-10, the `release-git` server's `--root` defines the repository (no new settings). Control runs use a scratch GitHub repo and a token the user supplies at the 27-08 checkpoint; without them the runs are recorded as «не запускалось» (D-19). Related: the minor-version tag message format (Assumption A1) is fixed explicitly in 27-02 `tag_message` and reported in `Day35_report.md` (27-08).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| git | all git tools | ✓ | 2.39.1.windows.1 | — |
| Python / mcp / httpx | server, tests | ✓ | 3.13.15 / 1.30.0 / 0.28.1 | — |
| `github-mcp-server.exe` | read tools (tags, PRs, releases) | ✓ | 2.0.2 | pipeline proceeds from git only, with a warning |
| GitHub release-write capability in that binary | draft Release | ✗ | — | own REST tool (recommended) or non-working step |
| `gh` CLI | — | ✗ | — | not used |
| Network to github.com over SSH | push, ls-remote | ✓ (ls-remote tested, whitelist env) | — | local bare repo for tests |
| GitHub token with Contents: write for a scratch repo | scenarios 1, 2 on GitHub | unknown (`.env` has one GITHUB entry, not inspected) | — | record scenarios as not run (D-19) |
| Scratch GitHub repository | D-15 | unknown (user must create) | — | local bare remote covers tag/push only; Release step not run |
| LM Studio (localhost:1234) | control runs (D-16) | ✗ at research time | — | start it; otherwise record "не запускалось" |
| Playwright | e2e script | existing scripts use it | — | — |

**Missing dependencies with no fallback:** none blocking planning.
**Missing with fallback:** release-write tool (own tool), scratch repo/token (user action), LM Studio (start before runs).

## Security Domain

(Config has `nyquist_validation: false`, so the Validation Architecture section is omitted; security enforcement is not disabled, so it is included.)

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2/V3 Authentication, Session | yes (existing) | WS already authenticates the user; release plan keyed by chat the user owns (`_user_owns_chat`) |
| V4 Access Control | yes | Plan lookup by `chat_id` + `user_id`; MCP servers resolved per user (`mcp_config.list_servers(session, user_id)`) |
| V5 Input Validation | yes | Version regex, `check_ref_syntax`, branch/tag name checks, title sanitising (no newlines/markup), `owner/repo` pattern |
| V6 Cryptography / secrets | yes | Token only in server env from the MCP form; never logged, never in tool results, never in `Day35_report.md` |
| V12 Files | yes | Server writes only `CHANGELOG.md` (fixed path through `resolve_inside_root`) |
| V13/V14 API & config | yes | `--allow-release` off by default; no force push |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via commit subjects steering the model | Tampering | Model output limited to text + one of two version strings; delimited untrusted data; code decides all actions |
| Option injection in refs/branch (`--upload-pack=...`) | Tampering/EoP | `check_ref_syntax`, `--end-of-options`, explicit refspecs, no shell |
| Force push / history rewrite | Tampering | No force flags exist in tools; refspec without `+` |
| Writing outside `CHANGELOG.md` / sweeping staged files | Tampering | Fixed path, refuse when other paths are staged, commit with pathspec |
| Token disclosure in card/log/error | Info disclosure | Redact env values; error text from GitHub's `message` only; structlog without request headers |
| Stored XSS via changelog/title/error in the card | Tampering | `createElement`+`textContent`, markdown only through `DOMPurify.sanitize`, link `href` restricted to `https://github.com/` with `rel="noopener noreferrer"` |
| Duplicate/parallel confirm | Tampering | `running` flag + chat lock; steps idempotent via recorded state |
| Real release triggered by tests | Repudiation/Integrity | Tests use temp bare remotes and respx; e2e only against the scratch repo; assert in e2e script that `origin` URL != project remote before confirming |

## Sources

### Primary (HIGH confidence)
- Local binary `github-mcp-server.exe` v2.0.2: tool lists for `--toolsets=repos,pull_requests` (29) and `--toolsets=all` (91) via `scripts/mcp_list_tools.py` (no create-release tool).
- GitHub API `releases/latest` for `github/github-mcp-server`: v2.0.2, 2026-10-08; issues #2853/#1909 open ("Add create_release …").
- docs.github.com/en/rest/releases/releases: create/list release parameters, draft visibility, tag behaviour.
- docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens: Contents write for create release.
- Repo code read: `mcp_servers/project_git.py`, `agent/command_turn.py`, `agent/ws.py` (frames, command hook), `agent/mcp_tools.py` (`call_mcp_tool`), `agent/review_github.py` (code-side MCP session use), `agent/state.py`, `agent/llm_client.py`, `agent/rag_llm.py`.
- Local experiments: `git tag -l -n1`, `git ls-remote --tags origin` under the SDK env whitelist, commit-range statistics for `v4.0..HEAD`.
- `.planning/phases/22-…/22-RESEARCH.md`, `26-CONTEXT.md`, `26-02-PLAN.md`, `27-CONTEXT.md`, `27-UI-SPEC.md`.

### Secondary (MEDIUM confidence)
- github.com/github/github-mcp-server README (toolsets, `--read-only`): partially read (truncated page).

### Tertiary (LOW confidence)
- none

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; versions verified locally.
- Architecture: MEDIUM-HIGH — follows existing patterns; flag naming/tool naming are discretionary.
- GitHub gap: HIGH — verified on the actual binary and upstream.
- Pitfalls: MEDIUM-HIGH — mostly verified locally; push-from-server and scratch repo untested.

**Research date:** 2026-10-17
**Valid until:** 2026-11-16 (re-check `github-mcp-server` releases for `create_release` before executing; if it ships, Pattern 4 can be replaced by a write-enabled `github` entry as D-04 originally said)
