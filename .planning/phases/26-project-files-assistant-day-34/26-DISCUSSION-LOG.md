# Phase 26: Project files assistant (Day 34) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-17
**Phase:** 26-project-files-assistant-day-34
**Areas discussed:** Entry point, File tools, Writes and diff, Reproducibility and verification

---

## Entry point

| Option | Description | Selected |
|--------|-------------|----------|
| `/files` command (recommended) | New slash command on the Phase 25 command-turn mechanism, own prompt and Settings section | |
| Ordinary chat with tools | No new command; the file server is enabled and the goal is an ordinary message | ✓ |
| Separate script/REST | New route + CLI, no chat UI | |

| Option | Description | Selected |
|--------|-------------|----------|
| CLI script over HTTP (recommended) | Script sends the goal to the HTTP chat endpoint and saves answer/diff/log | ✓ |
| Chat and HTTP only | No separate script | |

| Option | Description | Selected |
|--------|-------------|----------|
| Path in /files settings | Root set in a new settings section | |
| Root from the MCP form | `--root` argument, as local-git | |
| Claude decides | | ✓ |

**User's choice:** ordinary chat with tools; CLI script over HTTP; root left to Claude (resolved as `--root` in the MCP form).

---

## File tools

| Option | Description | Selected |
|--------|-------------|----------|
| Own project-files server (recommended) | New `mcp_servers/project_files.py` | |
| External filesystem.exe | Already installed server + local-git for diff | |
| Extend local-git | Add read/search/write to `project_git.py` | ✓ |

| Option | Description | Selected |
|--------|-------------|----------|
| `--allow-write` flag (recommended) | Write tools only with the flag; second server entry for the assistant | ✓ |
| Always write, app filters | Filter write tools by name in `/help` etc. | |
| Always write, no filter | `/help` can write too | |

| Option | Description | Selected |
|--------|-------------|----------|
| Text/regex + Python symbols (recommended) | `search_text` + `ast`-based `find_usages` | ✓ |
| Text/regex only | One grep-like tool | |
| Text + code RAG | grep + Phase 24 code base | |

| Option | Description | Selected |
|--------|-------------|----------|
| read_file + search_text + find_usages (recommended) | | ✓ |
| plus read_many_files | Fewer rounds, hits the 20 000-char limit sooner | |
| Claude decides | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Fragment replacement (recommended) | `edit_file(old, new)` with exactly-once match + `write_file` | ✓ |
| Whole-file rewrite only | | |
| Unified diff apply | | |

**User's choice:** extend local-git, gated by `--allow-write`; text + Python-symbol search; read_file/search_text/find_usages; fragment edits.
**Notes:** Extending local-git was chosen against the recommendation; the `--allow-write` gate was then chosen to keep `/help` and other consumers of `local-git` read-only.

---

## Writes and diff

| Option | Description | Selected |
|--------|-------------|----------|
| Write immediately + diff (recommended) | Saved to disk, result is the unified diff; undo via git | ✓ |
| `--dry-run` mode flag | Edits kept in memory, only the diff shown | |
| Propose and apply by button | New UI and route | |

| Option | Description | Selected |
|--------|-------------|----------|
| Cards + coloured diff (recommended) | Diff results rendered with colours in the tool-call card | ✓ |
| Cards as they are | Plain text | |
| Summary block under the answer | App-collected «Изменения» block | |

| Option | Description | Selected |
|--------|-------------|----------|
| .git, .env*, git-ignored (recommended) | Plus binaries and a size limit; no delete | ✓ |
| Only .git and .env* | | |
| Claude decides | | |

**User's choice:** all recommended.

---

## Reproducibility and verification

| Option | Description | Selected |
|--------|-------------|----------|
| Script + log + .patch (recommended) | Clean-tree check, commit, model, temperature 0, log, `git diff` as .patch | ✓ |
| Same + auto-reset | `--reset` flag | |
| Log only | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Separate clone (recommended) | Write-enabled root points to a clone; user's working copy untouched | ✓ |
| This repository on a branch | | |

| Option | Description | Selected |
|--------|-------------|----------|
| pytest + e2e + Day34_report (recommended) | Scenarios on the local model with logs and patches, no judge | ✓ |
| Report on two models | | |
| No report | | |

| Option (multi-select) | Description | Selected |
|--------|-------------|----------|
| API usages | cleanup_chat_caches / get_effective_settings | ✓ |
| ADR | UI/Agent two-process split → docs/adr/0001-*.md | ✓ |
| CHANGELOG | From git history; prepares Phase 27 | ✓ |
| Folder README | README for mcp_servers/ | ✓ |

**User's choice:** all recommended; all four scenarios.

---

## Claude's Discretion

- Project root location (resolved: `--root` in the MCP form).
- Tool/parameter names, limits, ignored-file detection, binary detection, overwrite policy of `write_file`.
- System-prompt hint for the write-enabled server, duplicate read tools from two entries, `MAX_TOOL_ROUNDS`.
- Run-script settings mechanism, log format, output location, clone creation.
- Diff detection in the card, branch base for `Day34`, documentation updates.

## Deferred Ideas

- `/files` slash command with its own Settings section.
- Confirm-before-write / dry-run mode.
- App-collected «Изменения» summary block.
- Code RAG search for this assistant.
- Delete/rename tools; git write tools (PGIT-05).
- Two-model run with a judge.
