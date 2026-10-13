# Phase 23: /help command, verification and report (Day 31) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-13
**Phase:** 23-help-command-verification-and-report-day-31
**Areas discussed:** Search and the «Не знаю» gate, Tools and history, Docs base mark and service replies, Control questions and report

---

## Search and the «Не знаю» gate

### No fragments above the threshold

| Option | Description | Selected |
|--------|-------------|----------|
| Call the model anyway (recommended) | Model is called with the git tools and a note that the documentation has nothing; answer without sources | ✓ |
| Keep the code-built «Не знаю» | Template reply without a model call, as strict mode does today; git-only questions do not work through /help | |
| Call the model only when the git server is connected | Two behaviours depending on the server state | |

**User's choice:** Call the model anyway.

### Quotes

| Option | Description | Selected |
|--------|-------------|----------|
| Sources only (recommended) | Source list under the answer, no verbatim quotes required | |
| With verified quotes | Model must quote the fragments, the server verifies, «Цитаты» block under the answer | ✓ |
| You decide | Left to research and planning | |

**User's choice:** With verified quotes.
**Notes:** Chosen against the recommendation. Quote verification was not yet tried on Markdown with code, nor together with tool calls on the local model; recorded in CONTEXT.md as something to check.

### Source of the search settings

| Option | Description | Selected |
|--------|-------------|----------|
| Fixed set in code (recommended) | Named constants, same behaviour everywhere | |
| Configured together with the mark | User sets the values next to the documentation base mark | ✓ |
| Variables in .env | Defaults in code, overridden through .env | |

**User's choice:** Configured together with the mark.

### Which settings

| Option | Description | Selected |
|--------|-------------|----------|
| Same set as a chat (recommended) | top-K, candidates, threshold, four stage switches | ✓ |
| Only top-K and threshold | Two fields, stages off | |
| Top-K, threshold and hybrid search | Three fields | |

**User's choice:** Same set as a chat.

---

## Tools and history

### Tool set of a /help turn

| Option | Description | Selected |
|--------|-------------|----------|
| Only local-git (recommended) | Five read-only git tools and nothing else | |
| local-git and github | Both project servers of Phase 22 | |
| All tools as usual | Same set as an ordinary turn | |

**User's choice:** Free text — "Настраиваемый набор" (a configurable set).
**Notes:** Clarified in plain text: (1) only whole MCP servers are chosen, not single tools; (2) in the same place as the base mark and the /help search settings; (3) built-in tools are part of the configurable set; (4) the default is only local-git.

### Built-in tools in the form

| Option | Description | Selected |
|--------|-------------|----------|
| One switch (recommended) | One checkbox for memory, tasks and scheduler together | |
| Three separate checkboxes | Memory, tasks and scheduler chosen one by one | ✓ |

**User's choice:** Three separate checkboxes.

### Chosen server unavailable

| Option | Description | Selected |
|--------|-------------|----------|
| Answer with a warning (recommended) | Turn runs from the documentation; warning under the answer and in the HTTP JSON | ✓ |
| Answer silently | No warning, log entry only | |
| Refuse with an instruction | No model call, text telling the user to connect the server | |

**User's choice:** Answer with a warning.

### Chat history in a /help turn

| Option | Description | Selected |
|--------|-------------|----------|
| No, self-contained (recommended) | /help system prompt, fragments and the question only | ✓ |
| Only earlier /help pairs | Follow-ups inside /help work | |
| The whole conversation | Like an ordinary turn | |

**User's choice:** No, self-contained.

### /help pairs on later ordinary turns

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, like ordinary messages (recommended) | No special handling in context building | ✓ |
| No, hidden from the model | Needs a mark on the message and changes in every compression strategy | |

**User's choice:** Yes, like ordinary messages.

---

## Docs base mark and service replies

### Place of the form

| Option | Description | Selected |
|--------|-------------|----------|
| Section in Settings (recommended) | New section next to «MCP серверы»; badge on the marked base's card | ✓ |
| In the knowledge base window | Button on the card, settings block in the same window | |
| Both places | Mark on the card, search and tools in Settings | |

**User's choice:** Section in Settings.

### Service replies without a model call

| Option | Description | Selected |
|--------|-------------|----------|
| Not stored (recommended) | Temporary notice in the browser; JSON with the text and a nothing-stored sign over HTTP | ✓ |
| Stored as messages | Pair of messages in the tree, like the template «Не знаю» | |

**User's choice:** Not stored.

### Look of the exchange

| Option | Description | Selected |
|--------|-------------|----------|
| Label on the answer (recommended) | User message as typed; label «Справка по проекту · <base>» above the answer | ✓ |
| No special look | Like an ordinary RAG answer | |
| Label plus input hint | Also a hint when «/» is typed in the message field | |

**User's choice:** Label on the answer.

### kb_id in an HTTP /help request

| Option | Description | Selected |
|--------|-------------|----------|
| Ignore kb_id (recommended) | Marked base is always used, chat settings unchanged | ✓ |
| Reject the request | 422 with an explanation | |
| Search the base from kb_id | One-request override | |

**User's choice:** Ignore kb_id.

---

## Control questions and report

### Model

| Option | Description | Selected |
|--------|-------------|----------|
| Local LM Studio (recommended) | qwen/qwen3.5-9b as in earlier reports | |
| DeepSeek | Cloud model, needs a working key | |
| Both models | Same set on both, compared in the report | ✓ |

**User's choice:** Both models.

### Composition of the set

| Option | Description | Selected |
|--------|-------------|----------|
| Mixed set of 10 (recommended) | 5 documentation, 2 git, 2 mixed, 1 outside the project | ✓ |
| Mostly documentation | 7 documentation, 2 git, 1 outside | |
| I will give my own questions | User writes the list | |

**User's choice:** Mixed set of 10.

### Verdict

| Option | Description | Selected |
|--------|-------------|----------|
| Code checks plus Claude's assessment (recommended) | Script checks source and tool; text judged against the documentation with a written reason | |
| DeepSeek judge | DeepSeek grades against the reference, as in Day 23 and Day 25 | ✓ |
| Code checks only | Formal signs only | |

**User's choice:** DeepSeek judge.
**Notes:** On the DeepSeek run the judge and the answering model are the same provider; the report must say so.

### Run channel

| Option | Description | Selected |
|--------|-------------|----------|
| HTTP script on the isolated copy (recommended) | Script starts the copy, loads docs, connects local-git, sends 10 /help questions over HTTP | ✓ |
| In the browser through Playwright | All questions typed in the chat UI | |
| On the user's running app | Script against 8000/8001 with the user's base and key | |

**User's choice:** HTTP script on the isolated copy.
**Notes:** Carried forward from Phase 12: if the DeepSeek key is not accepted, the DeepSeek run and the judge are recorded as not run, not as passed.

---

## Claude's Discretion

- Storage and routes of the per-user /help configuration
- Behaviour of the mark when the base is deleted or is being added to
- Exact command recognition rule
- How the /help fact and the warning are stored on the assistant message
- Russian wording of the prompt, usage text, instruction, warning and label
- HTTP shape of the service replies
- Side jobs on a /help turn (task memory, facts, chat title), profile and invariants in the /help prompt
- «Детали поиска» under a /help answer
- Combining quote instructions with tool calls, and the fallback
- Default values of the /help search settings; names of new scripts and tests

## Deferred Ideas

- Choosing single tools of an MCP server for /help — MCPS-01
- Input hint when «/» is typed — not chosen
- Follow-up questions inside /help — not chosen
