# Phase 24: Automated PR code review (Day 32) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-13
**Phase:** 24-automated-pr-code-review-day-32
**Areas discussed:** Where the pipeline runs, RAG over code, Where the review goes, Verification and report

---

## How Day 32 was added

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 24 in the current roadmap (recommended) | One phase next to the open Phase 23, nothing archived | ✓ |
| New milestone v6.0 now | New requirements and roadmap; Phase 23 left unfinished in v5.0 | |
| Close Day 31 first | Plan and execute Phase 23, close v5.0, then start Day 32 | |

**User's choice:** Phase 24 in the current roadmap.

---

## Where the pipeline runs

### Runner

| Option | Description | Selected |
|--------|-------------|----------|
| Self-hosted runner + app (recommended) | Action on the user's machine calls the local app over HTTP with an API key | ✓ |
| GitHub-hosted runner + script | Standalone script with a cloud model and its own index | |
| App watches PRs itself | Scheduler polls GitHub through MCP, no Action | |

**User's choice:** Self-hosted runner + app.

### Who obtains the diff

| Option | Description | Selected |
|--------|-------------|----------|
| Runner through git (recommended) | Action computes `git diff base...head` and sends it in the request | |
| App by PR number, itself | App fetches diff and files from github-mcp-server in code | ✓ |
| Model through MCP tools | Model calls the GitHub tools during a chat turn | |

**User's choice:** App by PR number, itself.
**Notes:** Chosen against the recommendation; the review then depends on the GitHub server being connected and the token being valid.

### Entry point

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated review route (recommended) | New REST route with an API key, structured JSON | ✓ |
| Existing HTTP chat | Script composes a chat message with `kb_id` | |
| /review chat command | Second slash command, depends on Phase 23 | |

**User's choice:** Dedicated review route.

### Failure behaviour

| Option | Description | Selected |
|--------|-------------|----------|
| Fail with a clear reason (recommended) | Job fails, summary names what was unavailable; check not required | ✓ |
| Green with a warning | Job passes, "review not performed" in summary and comment | |

**User's choice:** Fail with a clear reason.

### Model

| Option | Description | Selected |
|--------|-------------|----------|
| Configurable, default DeepSeek (recommended) | Stronger on long diffs; the diff goes to the cloud | |
| Configurable, default LM Studio | Code stays on the machine; slower, likely weaker on large diffs | ✓ |
| Same as global chat settings | No separate setting | |

**User's choice:** Configurable, default LM Studio.
**Notes:** Chosen against the recommendation.

### Large diff

| Option | Description | Selected |
|--------|-------------|----------|
| Per-file review, then summary (recommended) | Each part reviewed with its own search, one call builds the overall review | ✓ |
| Truncate and report | One call, unreviewed files listed | |
| Refuse above a limit | "PR too large" | |

**User's choice:** Per-file review, then summary.

---

## RAG over code

### Source of code

| Option | Description | Selected |
|--------|-------------|----------|
| Index from the repository folder (recommended) | App reads git-tracked files itself and builds a code base | ✓ |
| Allow code in the knowledge base form | Upload form accepts code extensions (KBUP-07) | |
| Both | | |

**User's choice:** Index from the repository folder.

### Bases

| Option | Description | Selected |
|--------|-------------|----------|
| Two bases, both searched (recommended) | Documentation base and code base, each with its own quota | ✓ |
| One shared base | Code added to the documentation base | |

**User's choice:** Two bases, both searched.

### Freshness

| Option | Description | Selected |
|--------|-------------|----------|
| Automatically before review (recommended) | Changed files re-indexed by content hash; plus a refresh button | ✓ |
| Only by button | User refreshes manually; review states the index date | |

**User's choice:** Automatically before review.

### Indexed state

| Option | Description | Selected |
|--------|-------------|----------|
| Working copy as is (recommended) | Files currently on disk | |
| Base branch of the PR | Code read from git at the branch the PR targets | ✓ |

**User's choice:** Base branch of the PR.
**Notes:** Chosen against the recommendation; how the local repository learns the base branch state is left to research.

### Chunking

| Option | Description | Selected |
|--------|-------------|----------|
| By functions and classes (recommended) | Python through `ast`, other files by line windows; fragments carry path, name, lines | ✓ |
| Line windows for all files | | |
| You decide | | |

**User's choice:** By functions and classes.

---

## Where the review goes

### Destination

| Option | Description | Selected |
|--------|-------------|----------|
| PR comment from the Action (recommended) | Action posts with the workflow token; text repeated in the job summary | ✓ |
| App posts the comment | github-mcp-server in write mode (GHUB-03) | |
| Job summary and artifact only | Nothing written to the PR | |

**User's choice:** PR comment from the Action.

### Form

| Option | Description | Selected |
|--------|-------------|----------|
| One general comment (recommended) | Three sections, file and line named in text | ✓ |
| General + inline comments | Remarks bound to diff lines | |

**User's choice:** One general comment.

### New commits

| Option | Description | Selected |
|--------|-------------|----------|
| Update the same comment (recommended) | Re-review of the whole PR replaces the earlier comment, names the commit | ✓ |
| New comment per run | | |

**User's choice:** Update the same comment.

### Language

| Option | Description | Selected |
|--------|-------------|----------|
| Russian (recommended) | | ✓ |
| English | | |
| Configurable | | |

**User's choice:** Russian.

---

## Verification and report

### Control material

| Option | Description | Selected |
|--------|-------------|----------|
| Set of PRs with planted defects (recommended) | 4-5 frozen test PRs with expected findings, including a large and a clean one | ✓ |
| One demonstration PR | | |
| Real PRs of earlier days | | |

**User's choice:** Set of PRs with planted defects.

### Where the run happens

| Option | Description | Selected |
|--------|-------------|----------|
| Real PRs in this repository (recommended) | Real Action on the self-hosted runner, PRs closed without merging | ✓ |
| One real PR, the rest by script | | |
| Separate sandbox repository | | |

**User's choice:** Real PRs in this repository.

### Models

| Option | Description | Selected |
|--------|-------------|----------|
| LM Studio and DeepSeek, compared (recommended) | Each PR reviewed twice | ✓ |
| LM Studio only | | |

**User's choice:** LM Studio and DeepSeek, compared.

### Verdict

| Option | Description | Selected |
|--------|-------------|----------|
| DeepSeek judge + table (recommended) | Judge compares each review with the reference; false remarks counted | ✓ |
| Keyword check | | |
| Manual | | |

**User's choice:** DeepSeek judge + table.

---

## Claude's Discretion

Route shape and error codes; storage and layout of the review settings; the in-code call to the GitHub server and truncation handling; search queries, quotas and budgets; which files are indexed; workflow trigger events, concurrency and timeout; how the API key and app URL reach the workflow; whether the review is stored in the app; severity levels; prompt wording; tests, docs and file names; what the `Day32` branch is cut from.

## Deferred Ideas

Inline comments; the app posting through the GitHub server in write mode (GHUB-03); a `/review` chat command; scheduler-based PR watching; code files in the upload form (KBUP-07); a GitHub-hosted runner variant.
