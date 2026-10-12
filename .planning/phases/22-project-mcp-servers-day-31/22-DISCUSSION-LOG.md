# Phase 22: Project MCP servers (Day 31) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-13
**Phase:** 22-project-mcp-servers-day-31
**Areas discussed:** Launch and repository root, Tool shape, Output limits, GitHub server and verification

---

## Launch and repository root

### Where the server file lives

| Option | Description | Selected |
|--------|-------------|----------|
| `mcp_servers/` | New top-level directory; separate process, room for later write tools | ✓ |
| `scripts/` | Next to `scripts/mcp_list_tools.py`; that folder holds utilities and e2e scripts | |
| `agent/` | Close to the MCP client, but mixes Agent code with an external process | |

**User's choice:** `mcp_servers/`

### How the server learns the root

| Option | Description | Selected |
|--------|-------------|----------|
| `--root`, otherwise cwd | Explicit in the config, convenient in tests and in the CLI | ✓ |
| cwd only | No own arguments; an empty cwd silently gives the Agent's directory | |
| Repository of the file itself | Shortest form, but the isolated e2e copy would look at itself | |

**User's choice:** `--root`, otherwise cwd

### Root missing, not a git repository, or git not in PATH

| Option | Description | Selected |
|--------|-------------|----------|
| Does not start | Reason in stderr, shown as a connection error in Settings | ✓ |
| Starts, tools return errors | The problem shows up only in a chat | |

**User's choice:** Option 1, with a free-text addition: for "not a git repository" the app should offer to make the folder a new git repository.
**Notes:** Three ways to "offer" were laid out: a hint in the error text, a `--init` launch flag, a button in Settings. The user chose the button. It is a new UI element, a new Agent route and a write operation, so it was recorded as a deferred idea; in this phase the error text suggests `git init`. The user was told to say so if the button is wanted in this phase.

### Server name for the form

| Option | Description | Selected |
|--------|-------------|----------|
| `project-git` | Says it is this project's git | |
| `git` | Shortest; may be confused with the `github` server | |
| `local-git` | Stresses the local working copy against the remote GitHub | ✓ |

**User's choice:** `local-git`

---

## Tool shape

### Diff

| Option | Description | Selected |
|--------|-------------|----------|
| One diff with parameters | `staged`, `ref`, `path`; five tools in total | ✓ |
| Three separate tools | No parameter combinations, seven tools | |
| One diff with a `mode` field | Explicit enum; `ref` required only for one mode | |

**User's choice:** One diff with parameters

### Tracked files

| Option | Description | Selected |
|--------|-------------|----------|
| Optional `path` | Whole list truncated with marker and total, or one folder | ✓ |
| Always the whole list | Simplest schema; the end of the list is never seen | |
| `path` and pattern | More flexible; one more input to validate | |

**User's choice:** Optional `path`
**Notes:** The question was first declined: the user asked where the 20 000 limit comes from. Answer given: `MCP_TOOL_RESULT_MAX_CHARS` in `shared/config.py:67`, applied in `agent/mcp_tools.py:321` (`call_mcp_tool`), added in Day 16, not overridden in `.env`. The question was then asked again.

### Recent commits

| Option | Description | Selected |
|--------|-------------|----------|
| `count` + `path` | Default 10, maximum 50; history of one file | |
| `count` only | Simplest schema | |
| `count` + `path` + `ref` | Also the history of another branch or tag | ✓ |

**User's choice:** `count` + `path` + `ref`

### Result format

| Option | Description | Selected |
|--------|-------------|----------|
| Git text with a summary line | Never an empty result | ✓ |
| Raw git output | A clean tree gives an empty reply | |
| JSON | Easy to test, more tokens, worse in the card | |

**User's choice:** Git text with a summary line

---

## Output limits

### Server's own truncation limit

| Option | Description | Selected |
|--------|-------------|----------|
| Slightly below the app limit (about 18 000) | The server's marker always survives | |
| Exactly 20 000 | The app would cut the server's marker | |
| 12 000 | Reserve for several calls | |
| Configurable | `--max-chars`, default about 18 000 | ✓ |

**User's choice:** Configurable
**Notes:** The first version of the question stated a default context of 16 384 tokens. The user corrected it: the default is 65 536. Checked in `shared/models.py:99` and the Settings slider; the user is right, the 16 384 figure came from an outdated note in STATE.md. The options were re-stated with the correct figure.

### Diff that does not fit

| Option | Description | Selected |
|--------|-------------|----------|
| Summary + start of diff | Per-file summary first, then text, then marker; two git commands | ✓ |
| Start of diff only | One command; files at the end are invisible | |
| `stat_only` parameter | The model chooses; one more parameter and round | |

**User's choice:** Summary + start of diff

### Timeout

| Option | Description | Selected |
|--------|-------------|----------|
| 10 seconds, configurable | `--timeout`, below the app's 30 s | ✓ |
| 10 seconds, constant | Fewer parameters in the documentation | |
| 20 seconds | Reserve for large repositories | |

**User's choice:** 10 seconds, configurable

### Rejected call

| Option | Description | Selected |
|--------|-------------|----------|
| MCP error with reason and hint | `isError`, English text says how to fix the call | ✓ |
| MCP error, reason only | Shorter; a local model repeats the bad call more often | |
| Plain text with "Error:" | The card shows no failure, the app's reminder does not fire | |

**User's choice:** MCP error with reason and hint

---

## GitHub server and verification

### Getting the binary

| Option | Description | Selected |
|--------|-------------|----------|
| Ready release from GitHub | Windows archive, exe into `go\bin`; no Go needed | ✓ |
| `go install` | One command, needs Go | |
| Both | Both described; check on the release binary | |

**User's choice:** Ready release from GitHub

### Token

| Option | Description | Selected |
|--------|-------------|----------|
| Fine-grained, read only | One repository; a new token later for write | |
| Fine-grained with write reserve | Contents and Pull requests read and write; server still `--read-only` | ✓ |
| Classic token (`repo`) | Full access to all repositories | |

**User's choice:** Fine-grained with write reserve

### Recorded check for GHUB-02

| Option | Description | Selected |
|--------|-------------|----------|
| Test + output in the phase document | Real-binary pytest (skipped without binary or token) plus saved CLI output | ✓ |
| CLI output only | Not repeatable with one pytest command | |
| Screenshot of Settings | Playwright on the isolated copy; also checks the mask | |

**User's choice:** Test + output in the phase document

### Model for PGIT-04

| Option | Description | Selected |
|--------|-------------|----------|
| Local LM Studio model | Playwright on the isolated copy; "not run" if LM Studio is down | ✓ |
| DeepSeek | More reliable tool calls; the key was a placeholder in Phase 12 | |
| Both | More evidence, more time | |
| LLM stub in pytest only | Real model only in Phase 23 | |

**User's choice:** Local LM Studio model

---

## Claude's Discretion

- Tool names and English descriptions
- Status output format under the summary line
- Accepted ref forms and their validation
- Split of the truncation budget between diff summary and diff text
- Which document carries the form values and whether README links to it
- Whether the model gets a hint about `local-git` versus `github`
- Name of the environment variable that gates the real-binary GitHub test

## Deferred Ideas

- "Создать репозиторий" button in Settings that runs `git init` for a root that is not a git repository (with PGIT-05)
- Git write tools — PGIT-05
- `github-mcp-server` in write mode — GHUB-03
- Per-tool enable/disable in Settings — MCPS-01
