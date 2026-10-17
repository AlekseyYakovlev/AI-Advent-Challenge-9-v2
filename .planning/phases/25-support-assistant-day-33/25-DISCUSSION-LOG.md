# Phase 25: Support assistant (Day 33) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-17
**Phase:** 25-support-assistant-day-33
**Areas discussed:** Entry point, Ticket context source, Product/FAQ/JSON, Verification and report

---

## Entry point

| Option | Description | Selected |
|--------|-------------|----------|
| `/support` command | Like `/help`: own command in any chat and over HTTP, own Settings section | ✓ |
| Ordinary chat + configuration | Attach FAQ base + MCP server to a chat, role via system prompt | |
| Separate mode/page | Own route or window | |

| Option (history) | Description | Selected |
|--------|-------------|----------|
| Sees earlier `/support` pairs | Ticket stays the topic of follow-ups | ✓ |
| Self-contained like `/help` | Each question alone | |
| Whole chat history | As an ordinary turn | |

| Option (relation to /help) | Description | Selected |
|--------|-------------|----------|
| Shared mechanism, own settings | Generalise the /help turn; own section, prompt, default server | ✓ |
| Separate copy | New modules and table, /help untouched | |
| You decide | | |

| Option (quotes/gate) | Description | Selected |
|--------|-------------|----------|
| As in `/help` | Sources + verified quotes; model still called on a miss | ✓ |
| Sources only | | |

## Ticket context source

| Option | Description | Selected |
|--------|-------------|----------|
| Command prefix + model | Optional `#T-…`/`@login` looked up in code; otherwise model uses tools | ✓ |
| Model via tools only | | |
| Code only, explicit field/prefix required | | |

| Option (role) | Description | Selected |
|--------|-------------|----------|
| Logged-in user is the support agent | Can ask about any client/ticket | ✓ |
| Logged-in user is a client | Sees only own tickets | |

| Option (not found / server down) | Description | Selected |
|--------|-------------|----------|
| Answer from FAQ + warning | As `/help` D-08 | ✓ |
| Service refusal without model | | |

## Product, FAQ and JSON

| Option | Description | Selected |
|--------|-------------|----------|
| This app | New `docs/FAQ.md` about real app problems | ✓ |
| Fictional SaaS | | |

| Option (data size) | Description | Selected |
|--------|-------------|----------|
| ~10 clients, ~25 tickets | | |
| Minimum: 3-4 clients, 5-6 tickets | | ✓ |
| You decide | | |

| Option (tools) | Description | Selected |
|--------|-------------|----------|
| 4 tools | get_ticket, get_user, list_user_tickets, search_tickets | ✓ |
| 2 tools | get_ticket, get_user | |
| You decide | | |

| Option (re-read) | Description | Selected |
|--------|-------------|----------|
| On every call (by mtime) | Edits visible without reconnect | ✓ |
| Only at start | | |

## Verification and report

| Option (knowledge base) | Description | Selected |
|--------|-------------|----------|
| FAQ.md + USER_GUIDE.md as a separate base | | |
| Same base as `/help` | README + docs/ incl. FAQ.md | ✓ |
| FAQ.md only | | |

| Option (verification) | Description | Selected |
|--------|-------------|----------|
| As Day31 (frozen set, two models, judge, report) | | |
| One model, no judge | | |
| pytest + e2e only, no report | | ✓ |

| Option (video) | Description | Selected |
|--------|-------------|----------|
| Demo script in docs | | |
| Nothing special | | ✓ |

**Notes:** Choosing "same base as /help" was interpreted as: no second base mark; `/support` reads the base marked in the `/help` section, while keeping its own search and tool settings.

## Claude's Discretion

- Command grammar, server/file/tool names, JSON location, storage of `/support` settings, factoring of the shared mechanism, Russian texts, representation of the direct lookup, side jobs, history cap, documentation layout.

## Deferred Ideas

- Write tools on the support server; clients as app accounts; FAQ-only base; control run with judge and `Day33_report.md`.
