# Phase 23: /help command, verification and report (Day 31) - Research

**Researched:** 2026-10-13
**Domain:** Server-side slash command on the existing chat turn (WebSocket + HTTP), RAG without the gate, per-turn tool selection (MCP + built-in), per-user config, isolated-copy verification and a Russian report
**Confidence:** HIGH on code integration points (read in the live code); MEDIUM on model behaviour (qwen3.5-9b combining quotes and tools is NOT yet tested)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** A `/help` turn never ends with the code-built «Не знаю» reply. When the documentation search returns no fragment above the threshold, the model is still called, with the chosen tools and a note that nothing was found in the documentation. It may answer from git; if it has no answer, it says so itself. Such an answer has no sources.
- **D-02:** When fragments are found, the answer carries verified quotes as in strict RAG mode (the «Цитаты» block under the answer, quotes checked on the server), plus the source list. So `/help` uses the quote half of strict mode without its gate half.
- **D-03:** The `/help` search settings are not fixed in code and are not taken from the chat. The user sets them in the same place as the documentation base mark.
- **D-04:** The set of `/help` search settings is the same as a chat has: top-K, candidate count, threshold (empty means the calibrated value of the base's embedding model), and the four stage switches (lexical, LLM rerank, hybrid, rewrite). The chat's own `ChatRagConfig` row is neither read nor written by `/help` (HELP-02).
- **D-05:** The tool set of a `/help` turn is configurable by the user, in the same form as the base mark and the search settings. The unit of choice for MCP is a whole server (a checkbox per configured MCP server), never a single tool.
- **D-06:** The built-in tools are part of the configurable set as three separate checkboxes: memory, tasks, scheduler.
- **D-07:** Default, before the user changes anything: only the MCP server named `local-git` is on; every other MCP server and all three built-in groups are off. With the default a `/help` turn changes nothing (no memory write, no task, no scheduled job).
- **D-08:** When a chosen MCP server is not configured or fails to connect, the turn still runs from the documentation without that server's tools, and a warning is shown under the answer ("сервер local-git недоступен, его инструменты не использовались") and carried in the HTTP JSON body. An MCP problem never refuses or breaks a `/help` turn.
- **D-09:** A tool call made in a `/help` turn is shown with the ordinary tool-call card (HELP-03) and is returned in `tool_calls` of the HTTP reply (HELP-06).
- **D-10:** A `/help` question is self-contained. The model gets the `/help` system prompt, the documentation fragments and the question; the chat's earlier messages are not sent.
- **D-11:** The stored `/help` question and answer are ordinary messages for later turns: an ordinary message after `/help` sees them in its history like any other pair, with no special handling in the compression strategies.
- **D-12:** The mark, the `/help` search settings and the tool choice live in one new section of the Settings window, next to «MCP серверы»: a list of the user's ready knowledge bases (with a way to clear the choice), the search fields of D-04, the server checkboxes of D-05 and the three built-in checkboxes of D-06. The setting is per user.
- **D-13:** In the knowledge base window the card of the marked base shows a badge «Документация проекта». The mark is not set from the card; the Settings section is the only place to set, change or clear it (HELP-01).
- **D-14:** The two replies that make no model call — usage text for `/help` with no question (HELP-04) and the instruction for "no base marked or the marked base is not ready" (HELP-05) — are not stored in the chat tree. In the browser they are a temporary notice gone after reload. Over HTTP the reply is a JSON body with the text and an explicit sign that nothing was stored. They do not enter the model context of later turns and do not use up the per-chat message limit.
- **D-15:** A real `/help` exchange is stored as two ordinary messages (HELP-07). The user message is shown as typed, including the `/help` prefix. Above the answer there is a small label «Справка по проекту · <имя базы>»; below it are the ordinary tool-call cards, the sources and the quotes. The label, the sources and the quotes survive a page reload.
- **D-16:** An HTTP request that carries `kb_id` together with a `/help` message: `kb_id` is ignored. The answer comes from the marked base and the chat's RAG settings stay unchanged. `docs/API_SPEC.md` states this.
- **D-17:** The control set has 10 questions, frozen in a file before the run: 5 doc-only (run, `.env`, architecture, API, tests), 2 git-only (current branch, recent commits), 2 mixed, 1 outside the project (expected: the model says it does not know). Each question has expected source, expected tool and key facts recorded beforehand.
- **D-18:** The set is run on two models: local LM Studio `qwen/qwen3.5-9b` and DeepSeek. The report compares them.
- **D-19:** Verdicts come from a DeepSeek judge against the recorded reference, following `scripts/rag_judge.py`. The report states that on the DeepSeek run judge and answering model are the same provider.
- **D-20:** The run is a separate script over HTTP on an isolated copy (ports 18000/18001, scratch DB): loads `README.md` and `docs/` into a base, connects `local-git`, marks the base, sends the 10 `/help` questions to the HTTP chat endpoint with an API key; answer, sources and tool calls are read from the JSON body and saved under `eval_out/day31/`. The browser path is covered by the VER-08 script, not by this run.
- **D-21:** Honest recording: if LM Studio is not running, or the DeepSeek key is not accepted, that run (or judge column) is recorded as not run, never as passed. Failed questions stay in the report (VER-10).
- **D-22:** `Day31_report.md` is written in Russian; for each question it gives the answer, the sources, the tool calls and the verdict.

### Claude's Discretion
- Where the per-user `/help` configuration is stored (new table or columns) and the shape of its REST routes.
- What happens to the mark when the marked base is deleted (expected: cleared), and whether a ready base being added to (`status=ready`, `phase=adding`) counts as ready (expected: yes).
- The exact rule that recognises the command (case, leading whitespace, `/helpme`, other `/words`), as long as a message that is not `/help` behaves exactly as today.
- How the "this was a /help turn" fact and the D-08 warning are stored on the assistant message so the label survives reload (`Message.rag_sources` is the obvious carrier).
- Wording of the `/help` system prompt, usage text, instruction text, warning and label (Russian UI texts).
- HTTP status and field names of the D-14 service replies, and the name of the D-08 warning field.
- Whether the chat's side jobs run on a `/help` turn: task memory update, fact extraction, automatic chat title. Guiding rule D-07: with default settings `/help` changes nothing besides adding its two messages.
- Whether the user profile and invariants are added to the `/help` system prompt; D-10 excludes the chat history, not necessarily these.
- Whether the «Детали поиска» block is shown under a `/help` answer.
- How the quote instructions of D-02 and the tool calls of D-09 are combined in one prompt, and the fallback if the local model cannot do both reliably (must be checked on the model during research; a failure is reported, not hidden).
- Default values of the `/help` search settings.
- Names of the new scripts and test files, and the layout of the Settings section within the Tailwind look.

### Deferred Ideas (OUT OF SCOPE)
- Choosing single tools of an MCP server for `/help` (MCPS-01). `/help` chooses whole servers only.
- A hint in the message field when the user types `/`.
- Follow-up questions inside `/help` (earlier `/help` pairs sent to the model).
- Also out of scope: git write tools (PGIT-05), `github-mcp-server` write mode (GHUB-03), KBUP-07, KBUP-08, any other slash command.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HELP-01 | Mark / see / change / clear one KB as project docs base | New per-user `HelpConfig` table (FK `kb_id` `ondelete="SET NULL"` via `sa_column`), REST `GET/PUT /api/v1/help/config`, Settings section, KB card badge. KB deletion clears the mark through the FK (see Architecture). |
| HELP-02 | `/help <q>` answered from the base with sources, chat RAG settings untouched | Branch at the top of `_run_chat_turn`; own pipeline call with a config built from `HelpConfig` (same field names as `ChatRagConfig`, so `config_from_row` works as is); never read/write `ChatRagConfig`; skip `apply_chat_kb` in HTTP (D-16). |
| HELP-03 | Git tools available during `/help`, call visible | `build_mcp_toolset` gets an `only_server_ids` filter; `_ToolTurn.allowed_tools` restricts built-ins; existing `tool_call` frames/cards unchanged. Tool cards are live-only (not restored on reload for ANY chat today — see Pitfall 7). |
| HELP-04 | `/help` alone shows usage, no model call | Syntactic check only (no DB); `help_notice` frame; nothing persisted. |
| HELP-05 | No base marked / not ready: instruction, no model call | DB check of `HelpConfig` + `KnowledgeBase.status`; `help_notice` frame; nothing persisted. |
| HELP-06 | Same over HTTP with API key; JSON carries answer, sources, tool calls | HTTP already reuses `ws.run_limited_turn` with a `RecordingSink`; `HttpChatResponse.rag` already carries sources/quotes; add optional `help` block and a notice JSON body for D-14. |
| HELP-07 | Exchange stored as ordinary messages, survives reload with sources | Existing `_persist_*` + `Message.rag_sources`; `/tree` returns `rag_sources` as an arbitrary dict, so a `help` sub-object round-trips with no schema change. |
| VER-07 | pytest for upload/add/replace, git MCP, `/help` WS+HTTP; TESTING_GUIDE sections | Phase 21/22 tests and TESTING_GUIDE sections ALREADY exist (verified); only `/help` tests and a `/help` guide section are new. |
| VER-08 | Isolated-copy E2E script: folder upload via UI, git MCP, `/help` in browser + HTTP | Compose existing helpers from `e2e_kb_playwright.py`, `e2e_kb_folder_playwright.py`, `e2e_mcp_git_playwright.py`. |
| VER-09 | API_SPEC / ARCHITECTURE / USER_GUIDE describe upload options, git MCP, GitHub server, `/help` | Guarded by a new `tests/test_docs_help.py` modelled on `test_docs_day30.py`. Upload/git sections exist; add GitHub-server check and `/help`. |
| VER-10 | `Day31_report.md` with 8-10 questions on a real model, answer/sources/tool calls/verdict, failures included | 10-question frozen fixture + `scripts/help_eval.py` over HTTP + judge. |
</phase_requirements>

## Summary

Everything `/help` needs already exists as parts; the work is assembling them behind one early branch in `agent/ws.py::_run_chat_turn` so both channels behave identically. The chat turn is a single ~370-line function that persists the user message, builds context from the whole chat tree, runs `prepare_rag_turn` (which reads `ChatRagConfig` and may return the code-built «не знаю»), streams, runs tool rounds, runs invariants self-critique, verifies quotes, persists, and fires side jobs. `/help` needs a different front half (own system prompt + question only + own pipeline config + own toolset) and a trimmed back half (no invariants critique, no task memory, no fact extraction), while the middle (stream + action-claim retry + tool rounds) is shared. HTTP needs only two additive changes: skip `apply_chat_kb` for a `/help` message and map a new `help_notice` frame to a JSON body.

Three findings change the plan and are not visible from CONTEXT.md. (1) **The calibrated threshold 0.67 would remove every source on this documentation**: measured on `bge-m3` over `README.md` + `docs/*.md` (269 chunks), the best raw cosine for 5 realistic doc questions is 0.54-0.63, all below 0.67; out-of-project questions score 0.35-0.38. A fixed default of about 0.45 separates them. (2) **`STRICT_INSTRUCTION` says "answer ONLY from the fragments"**, which directly contradicts calling git tools; `/help` needs its own instruction text, and `finalize_rag_turn`/UI would otherwise attach auto-quotes and a «ответ не подтверждён фрагментами» warning to a pure-git answer. (3) **The DeepSeek judge default model `deepseek-chat` is no longer served** — `rag_judge.py --check` on the real key returned "available: deepseek-flash, deepseek-v4-pro" — and the isolated copy blanks `.env`, drops `docs/`, `.git/` and `scripts/` from the copy, so the control-run script must pass the key through the subprocess environment, upload `README.md`/`docs/` from the real repo, and point `local-git --root` at the real repository.

**Primary recommendation:** Add `agent/help_command.py` (parse, config, texts, turn preparation, finalize) and a `HelpConfig` table; in `_run_chat_turn` branch on `parse_help(payload.content)` before `_persist_user_message`; extract the shared stream-and-tool-rounds middle into a helper; give `/help` a default threshold of 0.45 (explicit), its own instruction (fragments with quotes OR git tools), and defer the rate-limit slot until the turn is known to call the model.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Recognise `/help` | Agent (`agent/ws.py` turn) | — | Must behave identically over WS and HTTP; client never special-cases the text (UI-SPEC rule 1) |
| Mark base + `/help` settings storage | Database (`HelpConfig` table, per user) | Agent REST | User-scoped data (project constraint); FK handles base deletion |
| Retrieval + quote verification | Agent (`rag_pipeline`, `rag_cite`) | Database (`KbChunk`) | Existing pipeline; only the config source and gate differ |
| Tool selection per turn | Agent (`mcp_tools`, `tools`, `_ToolTurn.allowed_tools`) | Database (`McpServerConfig`) | Toolset is built per turn server-side |
| Service replies (usage / no base) | Agent (frame) | Browser (transient render) | Not persisted; browser keeps them out of `state.messages` |
| Settings section, KB badge, answer label, notice | Browser (vanilla JS) | — | Render-only from frames/payload |
| Control run + report | Scripts over HTTP on isolated copy | `eval_out/day31/` | Same pattern as Day 22-25/30 |

## Standard Stack

### Core
No new packages. Everything is built from what the repo already has. [VERIFIED: codebase — requirements.txt unchanged by this design]

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI / SQLModel / aiosqlite | as pinned in repo | New routes and `HelpConfig` table | Project stack; new tables are created by `SQLModel.metadata.create_all` in `init_db` (no ALTER migration needed for a new table) [VERIFIED: shared/database.py:439] |
| httpx | as pinned | Control-run script HTTP client; judge | Already used by `scripts/rag_dialog.py` |
| playwright (Python) | already installed | VER-08 browser script | `python -c "import playwright"` succeeded; used by 9 existing e2e scripts |
| mcp (FastMCP) | as pinned | `mcp_servers/project_git.py` (local-git) | Built in Phase 22 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| New `HelpConfig` table | Columns on `User` or `Settings` | `Settings` is chat-scoped/global-NULL and has its own fallback rules (`test_settings_fallback.py`); a dedicated table keeps `/help` isolated from them. Use a table. |
| Frame `help_notice` | Reuse `error` frame | An error frame triggers toast, `lastFailedMessage` restore and HTTP error mapping; the notice is guidance, not failure (UI-SPEC). Use a new frame type. |

**Installation:** none.

## Package Legitimacy Audit

No external packages are installed by this phase. slopcheck not needed.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
Browser (cookie WS)                         HTTP client (API key)
  send "/help как запустить?"                 POST /api/v1/chats/{id}/messages
        |                                         | (kb_id ignored if /help, D-16)
        v                                         v
  ws_chat loop ---- parse payload ---------- post_chat_message
        |   is /help? -> skip rate-limit slot now (taken later, only if a model call follows)
        v
  run_limited_turn (generation slot)  <-- NOT taken for notices
        |
        v
  _run_chat_turn --- parse_help(content) --+-- not /help ------> unchanged path
        |                                   |
        |                                   +-- "/help" no question ---> frame help_notice(usage)        --> return (nothing stored)
        |                                   +-- no base / base not ready -> frame help_notice(no_base)   --> return (nothing stored)
        |                                   |
        |                                   +-- real /help turn:
        |        resolve_client -> persist user msg (content as typed)
        |        load HelpConfig -> PipelineConfig (history=None) -> run_retrieval_pipeline(kb)
        |             |-- fragments -> strict-style block with /help instruction (fragments OR git tools)
        |             |-- none      -> "nothing found in docs" note (model still called, D-01)
        |        toolset = build_mcp_toolset(only chosen server ids) + built-in groups ; warnings for missing servers
        |        messages = [help system prompt (+clock, tool rule), user(block + question)]   # no chat history (D-10)
        |        shared middle: stream -> (action-claim retry) -> tool rounds  -> tool_call frames
        |        finalize: process_answer -> verified quotes ; drop auto-quotes if tools answered
        |        persist assistant (rag_sources = RAG payload + {"help": {...}})   # no invariants critique, no task memory, no facts
        v
  done frame {message_id, stats, rag(+help), ...}
        |                                         |
        v                                         v
  loadChatTree -> label/sources/quotes       _build_success -> HttpChatResponse(content, rag, tool_calls, help)
```

### Recommended Project Structure
```
agent/
├── help_command.py      # parse_help, HelpConfig helpers, texts, prepare_help_turn, finalize_help_turn
├── help_api.py          # GET/PUT /api/v1/help/config (user-scoped), router included in agent/main.py
├── ws.py                # early /help branch in _run_chat_turn; extracted shared stream/tool-rounds helper
├── http_chat.py         # skip apply_chat_kb for /help; map help_notice frame -> JSON
├── mcp_tools.py         # build_mcp_toolset(..., only_server_ids=None)
shared/models.py         # HelpConfig table
ui/static/{index.html,app.js}   # #help-section, buildKbCard badge, label chip, notice element
mcp_servers/project_git.py      # unchanged
tests/
├── test_help_command.py        # parse rules, texts, no-model replies
├── test_help_api.py            # config routes, ownership, KB delete clears the mark
├── test_help_ws.py             # WS turn: sources, quotes, tool call, persistence, side-effect-free default
├── test_help_http.py           # HTTP: JSON body, kb_id ignored, notice body, rate slot
├── test_help_ui.py             # frontend source guard
├── test_docs_help.py           # documentation source guard (VER-09)
tests/fixtures/help/control_set.json   # frozen 10 questions
scripts/help_eval.py                   # D-20 control run over HTTP + judge column
scripts/e2e_help_playwright.py         # VER-08
eval_out/day31/
Day31_report.md
```

### Pattern 1: Recognise the command on the server, once
**What:** One pure function, tested in isolation.
**Recommendation (discretion item):** command = message whose text after `lstrip()` starts with `/help` as a whole word (next char is end of string or whitespace), case-insensitive. `/helpme`, `/ help`, `/HELPX`, and any other `/word` are ordinary messages. Question = remainder `.strip()`. Empty question -> usage. Apply on the raw `payload.content`; the stored user content stays exactly as typed (D-15).
```python
# Source: designed against agent/ws.py::_run_chat_turn (this research)
_HELP_RE = re.compile(r"^\s*/help(?:\s+(?P<q>.*))?$", re.IGNORECASE | re.DOTALL)

def parse_help(content: str) -> str | None:
    """Return the question ('' when empty) for a /help message, None for any other message."""
    m = _HELP_RE.match(content)
    return None if m is None else (m.group("q") or "").strip()
```
Note `/help` followed immediately by non-space (`/helpme`) does not match because the optional group requires `\s+` and `$` follows. [ASSUMED: regex edge cases need unit tests — listed in Wave 0]

### Pattern 2: Build the pipeline config from `HelpConfig` without touching `ChatRagConfig`
`config_from_row(row, kb, history)` only reads attributes `candidate_k, top_k, threshold, lexical, llm_rerank, hybrid, rewrite` [VERIFIED: agent/rag_pipeline.py:109-124]. Give `HelpConfig` the same attribute names and pass it directly; pass `history=None` (D-10, and `run_retrieval_pipeline` only calls the history/condense stage when `config.rewrite or config.history is not None`). `retrieve_vectors` raises `RagFailure` on embedder problems; catch it in the `/help` prep exactly like `prepare_rag_turn` does and continue with a `warning` payload and no fragments (D-08 spirit: never break the turn).

### Pattern 3: Quote half of strict mode without the gate half
`RagTurn(strict=True, reply_text=None)` makes `finalize_rag_turn` run `process_answer`; the UI quotes block requires `rag.strict === true && rag.gated !== true && verdict !== 'model_idk'` [VERIFIED: ui/static/app.js:5409-5416]. So build the payload with `mode="rag"`, `strict=True`, `gated=False`. Never return `reply_text` (D-01). When the pipeline verdict is `below_threshold` or no chunks: append the existing `NO_FRAGMENTS_INSTRUCTION` style note (`merge_no_fragments_note` exists) but reword for `/help` ("nothing found in docs; you may answer from git tools; say so if you cannot"), payload `sources=[]`, `strict=False` is acceptable for this branch (no quotes block, no unsupported warning).

### Pattern 4: Per-turn toolset by whole server
- MCP: add `only_server_ids: set[int] | None = None` to `build_mcp_toolset`; filter `rows` before the auto-connect and live-tool loops. Warnings (D-08) = chosen server names whose id has no binding in `toolset.bindings` (not configured, disabled, failed connect). Server slug is `server_slug(name, id)` and tool names are `mcp__local-git__git_status` etc.
- Built-ins: groups by name. `memory = {save_working_memory, save_long_term_memory}`, `tasks = TASK_TOOL_NAMES`, `scheduler = SCHEDULER_TOOL_NAMES` (constants already in `agent/ws.py`). Schemas: `[s for s in build_tool_schemas() if s["function"]["name"] in allowed]`. Pass `allowed_tools=frozenset(allowed)` into `_ToolTurn` — an **empty frozenset blocks all built-ins** while `None` means unrestricted [VERIFIED: agent/tools.py:139], so the default must be `frozenset()`, not `None`.
- Default (D-07) when no `HelpConfig` row or `mcp_server_ids` is NULL: MCP servers whose `name == "local-git"` (case-sensitive exact) for this user; built-ins all off.

### Pattern 5: Where to store "this was /help" and the warning
Put a sub-object in the existing payload: `payload["help"] = {"kb_name": ..., "warnings": [{"server": "local-git", "text": "..."}]}`. `Message.rag_sources` is free-form JSON, `MessageResponse.rag_sources: dict[str, Any]` passes it through, `parse_rag_payload` accepts any dict [VERIFIED: agent/rag.py:310, agent/main.py:414]. `kb_name` also exists top-level in the payload, but the label must survive base deletion/rename (UI-SPEC: "stored name still shown"), which the stored copy gives. Do not bump `PAYLOAD_VERSION`; it is additive.
Caveat: `finalize_rag_turn` and `RagTurn.with_task_memory` rebuild the payload with `{**payload, ...}`, so a `help` key survives. [VERIFIED: agent/rag_turn.py:73-75, 360]

### Pattern 6: Service replies (D-14) and the rate-limit slot
Today both channels call `limits.check_message_rate(chat_id)` BEFORE the payload is parsed, and the call records a slot as a side effect [VERIFIED: agent/limits.py:28-47, agent/ws.py:1290, agent/http_chat.py:188]. To keep notices from using the limit:
- In `ws_chat`: validate payload first; `if parse_help(payload.content) is None` take the rate slot as before; for `/help` messages take it inside the help path only when a model call will follow. Same in `post_chat_message`.
- Cheapest correct implementation: `limits.check_message_rate` stays; add a thin `rate_gate(chat_id, content)` wrapper used by both channels that skips the call for `/help` commands, and call `check_message_rate` inside `_run_chat_turn` right before the user message is persisted in the real-turn branch (returning the existing `RATE_LIMITED` frame; HTTP maps it already? **No** — `_map_error_frame` does not map `RATE_LIMITED`; the HTTP rate check is done in the route, not from frames. Add a `RATE_LIMITED` case to `_map_error_frame` returning 429 with `Retry-After` if this route is taken.) Alternative with less surface: keep the check where it is, but call a new `limits.refund_message(chat_id)` (pop the last timestamp) when the turn turned out to be a notice. Planner picks; the refund variant touches fewer files but is a mutation of a shared limiter — document in `docs/`.
- Notices must also not take a generation-queue slot: decide notices before `limits.generation_gate.slot(...)` (i.e. in `_handle_chat_message`/`_run_http_turn`, not inside `_run_chat_turn`), or accept that a notice may wait in the queue. Recommendation: handle the *syntactic* usage reply and the DB-checked no-base reply in a small `async def maybe_help_notice(chat, content) -> dict | None` called before `run_limited_turn` in both channels; it returns the frame to send or None.

### Pattern 7: HTTP surface
- `kb_given` branch: `if kb_given and parse_help(body.content) is None:` run `apply_chat_kb` (D-16; do not even validate the ignored `kb_id`).
- Notice reply: HTTP 200 JSON, shape `{"chat_id", "stored": false, "help": {"kind": "usage"|"no_base", "text": "..."}}`; `assistant_message_id`/`user_message_id` absent. `response_model=HttpChatResponse` on the route does not apply to a returned `JSONResponse` (already how errors work). Update API_SPEC accordingly.
- Real reply: `HttpChatResponse` gets optional `help: dict | None = None` (additive, other tests unaffected) copied from the done frame; `rag` already carries sources/quotes; `tool_calls` already comes from the recording sink [VERIFIED: agent/http_chat.py:88-112].

### Anti-Patterns to Avoid
- **Reusing `prepare_rag_turn`.** It reads `ChatRagConfig` by `chat_id` and may return a gated reply; both are forbidden by HELP-02/D-01.
- **Calling `build_llm_context` for `/help`.** It returns the chat history (D-10 forbids) and can raise `ContextOverflowError`, which would delete the message. Build `[system, user]` directly and size the fragment budget with `rag_budget(effective.context_length, used, max_tokens)`.
- **Using `STRICT_INSTRUCTION` verbatim.** "опираясь ТОЛЬКО на фрагменты" forbids tool use and tells the model to answer «Не знаю» when fragments lack the answer.
- **Letting the unsupported-answer warning fire on git answers.** See Pitfall 2.
- **Passing `allowed_tools=None` for "no built-ins".** None = unrestricted.
- **Persisting the notice, or the user message, before knowing the turn is a notice.** `_persist_user_message` commits immediately and advances `current_leaf_message_id`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retrieval, threshold, hybrid/rerank/rewrite | A second search | `run_retrieval_pipeline` + `config_from_row` | Already supports all D-04 switches and a trace for «Детали поиска» |
| Fragment budget and block | New block builder | `rag_budget`, `build_rag_block(strict=True)`, `merge_rag_block`, `sources_from_chunks` | Handles Cyrillic safety factor, delimiter neutralisation (documents can contain `===`) |
| Quote verification | New matcher | `rag_cite.process_answer` | Exact/fuzzy matching, invalid-ref count, auto-quote fallback |
| Tool rounds, nudges, loop guard, fallback summary | New loop | `_run_tool_rounds`, `_stream_action_claim_retry` (extract the middle of `_run_chat_turn`) | 15-round cap, empty-answer retry, error nudge, `tool_call` frames |
| MCP connection/dispatch | New client | `build_mcp_toolset`, `dispatch_tool_calls(mcp_bindings=...)` | Auto-connect, timeouts, result truncation |
| HTTP turn capture | New HTTP path | `RecordingSink` + `run_limited_turn` | Phase 19 decision "HTTP chat reuses the WebSocket turn" |
| Judge | New grader | `scripts/rag_judge.py` prompts (`build_judge_messages`, `parse_judge_reply`) | Frozen rubric; import, don't copy |
| Isolated-copy start-up | New launcher | `e2e_kb_playwright.prepare_copy/seed_scratch_users/wait_for_app/teardown` | Port-literal safety checks, never touches 8000/8001 |

**Key insight:** the new code is mostly *selection and wiring* (which config, which tools, which instruction, which frame); the risky parts are the three places where existing behaviour is subtly wrong for `/help` (gate, strict instruction, rate slot).

## Runtime State Inventory

Not a rename/refactor/migration phase — omitted. (One data-lifecycle note: `HelpConfig.kb_id` must be an FK with `ondelete="SET NULL"` so deleting a base clears the mark; `kb_indexer.delete_kb` removes the row with a Core `DELETE` and PRAGMA `foreign_keys=ON` is set, so the DB does it. Verify with a test; if the FK is declared with `Field(ondelete=...)` it is silently ignored — project rule says use `sa_column=Column(ForeignKey(...))`.)

## Common Pitfalls

### Pitfall 1: Calibrated threshold 0.67 returns zero sources on the project docs
**What goes wrong:** With `threshold=None` and `bge-m3`, `/help как запустить приложение?` gets verdict `below_threshold`, no sources, no quotes; the model is called with "nothing found" and answers from general knowledge.
**Evidence:** [VERIFIED: experiment this session] LM Studio `text-embedding-bge-m3`, `agent.kb_chunking.chunk_structural` on `README.md` + `docs/*.md` (269 chunks), best raw cosine per query:

| Query | Best cosine | Top source |
|-------|------------|-----------|
| как запустить приложение? | 0.630 | docs/USER_GUIDE.md |
| как запустить тесты? | 0.614 | docs/TESTING_GUIDE.md |
| какие REST эндпоинты есть для чатов? | 0.612 | docs/API_SPEC.md |
| какие переменные нужны в .env? | 0.579 | docs/TESTING_GUIDE.md (wrong; README 0.56) |
| из каких процессов состоит архитектура? | 0.541 | README.md |
| что изменено в рабочей копии? | 0.555 | docs/TESTING_GUIDE.md |
| на какой ветке проект? | 0.503 | docs/ARCHITECTURE.md |
| какие последние коммиты? | 0.397 | docs/USER_GUIDE.md |
| какая погода в Париже? (outside) | 0.377 | — |
| кто выиграл чемпионат мира по футболу? (outside) | 0.352 | — |

No doc question reaches 0.67; outside-the-project questions stay under 0.40. Sample is small and uses my own chunk settings (the real base uses the user's chunk size/strategy), so treat as indicative.
**How to avoid:** `HelpConfig.threshold` defaults to an explicit **0.45** (stored value, not NULL), with «Сбросить» still setting NULL = calibrated per D-04. Lexical-only filtering is not needed. Re-measure on the real indexed base before freezing the control set and record the numbers in the report. Document that the default differs from the calibrated one and why.
**Warning signs:** `/help` answers have no «Источники» although docs obviously cover the question; `search.verdict == "below_threshold"` in «Детали поиска».

### Pitfall 2: Strict machinery misfires on tool-only answers
**What goes wrong:** `process_answer` adds code-picked "auto quotes" whenever no model quote verifies, and sets `answer_supported=False` when there are no `[N]` refs and no verified quote; the UI then shows «⚠ ответ не подтверждён фрагментами» (`rag.strict === true && rag.answer_supported === false`). A correct git answer ("ветка Day31") with irrelevant fragments above 0.45 (the branch question scored 0.50) would get bogus quotes and that warning.
**How to avoid:** A `/help`-specific finalize: call `process_answer`; if any tool call succeeded in the turn and no model quote verified, drop the auto quotes and set `answer_supported=None` (UI test is `=== false`, so `null` hides it). If no tool was called, keep standard behaviour (quotes + warning are correct there). Also write the `/help` instruction so the model says which source it used.
**Warning signs:** quote rows on a branch/commit answer.

### Pitfall 3: Quote instruction vs tool use on a 9B local model
**What goes wrong:** `qwen/qwen3.5-9b` must (a) decide to call a git tool, (b) when answering from fragments, emit `[N]` refs and a verbatim «Цитаты:» tail, (c) not emit the tail on a tool-only answer. Not tested yet. [ASSUMED]
**How to avoid:** Prompt shape: one short system prompt (role, "use git tools for the state of the repository; use the fragments for documentation"), fragments block + `/help` instruction in the user message (as `merge_rag_block` does), and keep `TOOL_USE_RULE`. Do NOT append `MULTI_STEP_TOOL_HINT` (it is about filesystem/GitLab and would add noise for `local-git`). The code already tolerates a missing tail (auto-quotes) so quotes degrade gracefully; a model that never calls the git tool is the real failure mode — record it in the report, do not hide it (D-21). Plan one explicit spike task: run 3 questions (doc, git, mixed) on qwen3.5-9b and on DeepSeek before freezing the prompt. If qwen cannot do both reliably, fallback is documented prompt tweak, not a code-forced tool call.

### Pitfall 4: Rate-limit slot and queue slot consumed by notices
See Pattern 6. Test: with `RATE_LIMIT_MESSAGES_PER_MINUTE=1`, send `/help`, `/help` (usage) then an ordinary message: the ordinary message must pass.

### Pitfall 5: HTTP failure to build a success body for a no-model reply
`_run_http_turn` treats "no `done` frame" as an error (`_map_error_frame` -> 500 `INTERNAL_ERROR` when no error frame). A notice has neither. Check for the `help_notice` frame BEFORE that branch.

### Pitfall 6: Isolated copy differs from the real project
`prepare_copy` ignores `.git`, `docs`, `tests`, `scripts`, `.env` and writes an empty `.env` [VERIFIED: scripts/e2e_kb_playwright.py:56-59, 143]. Consequences: (a) upload `README.md`/`docs/` from `REPO_ROOT`, never from the copy; (b) `local-git --root` must be the real repository (the server is read-only; safe), and the script must copy `mcp_servers/project_git.py` path from the copy (it is not ignored) or the real repo; (c) DeepSeek needs the key in the subprocess env (`DEEPSEEK_API_KEY` read from the real `.env` in the script and put only in `env`, never written); (d) real-repo git state is non-deterministic (dirty tree, branch `Day31`): record `git rev-parse HEAD`, branch and `git status --short` in `run_meta.json` and judge "mixed/git" questions against that snapshot, not a fixed string.

### Pitfall 7: Tool-call cards are not restored after reload (existing behaviour)
`state.toolCallsByMessage` is in-memory; `/tree` does not return `tool_trace` and `app.js` never reads it for chat messages [VERIFIED: grep]. So after reload a `/help` exchange shows label + sources + quotes (D-15 explicitly lists only these) but no tool cards, same as any chat today. ROADMAP success criterion 3 says "the call is visible in the chat; the exchange survives a page reload" — satisfiable as D-15 reads it. VER-08 must assert label/sources after reload and the card only before reload. Flag to user as Open Question 1 if they expect cards to survive.

### Pitfall 8: DeepSeek model ids and judge default
`rag_judge.py` default `deepseek-chat` is not served any more: `--check` with the real key printed `model deepseek-chat is not available; available: ['deepseek-flash', 'deepseek-v4-pro']` [VERIFIED: ran `python scripts/rag_judge.py --check`; the key itself is accepted]. Use `--model deepseek-flash` for the judge and pick the answering model from `GET /api/v1/llm-providers/models` of the isolated copy at run time; record the exact ids in `run_meta.json` and the report.

### Pitfall 9: Unicode console on Windows
`print()` of Cyrillic under the default cp1252 console raised `UnicodeEncodeError` in my experiment. Existing scripts set `sys.stdout.reconfigure(encoding="utf-8")`; do the same in new scripts, and write files with `encoding="utf-8"`.

### Pitfall 10: Side jobs
Default rule D-07 ("changes nothing besides two messages"). Recommendation: on a `/help` turn skip `update_task_memory`, `extract_and_update_facts`, invariants self-critique/`record_conflict`, and chat-title generation. Title generation is cosmetic but is an extra model call and would also name a chat after a command; skipping it is simplest, and tests monkeypatch it anyway (`agent.ws.schedule_title_generation`). If the product wants titles for `/help`-first chats, allow it later; not required by any HELP-/VER- item.

## Code Examples

### Early branch in the turn (shape, not final code)
```python
# Source: agent/ws.py::_run_chat_turn (existing structure) + this research
question = parse_help(payload.content)
if question is not None:
    await _run_help_turn(websocket, chat, chat_id, payload, question)  # notice or real turn
    return
# ... existing path unchanged below
```
Order inside the help path: resolve client (needed only for a real turn) -> decide notice (usage / no base) -> **only then** persist the user message.

### Notice frame (WS) and HTTP mapping
```python
await websocket.send_json({"type": "help_notice", "kind": "usage", "text": HELP_USAGE_TEXT,
                           "stored": False})
# http_chat._run_http_turn: before the `done is None` branch
notice = next((f for f in sink.frames if f.get("type") == "help_notice"), None)
if notice is not None:
    return JSONResponse(status_code=200, content={
        "chat_id": chat_id, "stored": False,
        "help": {"kind": notice["kind"], "text": notice["text"]},
    })
```

### Settings model sketch (sa_column FK rule from CLAUDE.md)
```python
class HelpConfig(SQLModel, table=True):
    """Per-user /help settings: documentation base, search settings and tool choice."""
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True))
    kb_id: int | None = Field(default=None, sa_column=Column(Integer, ForeignKey("knowledgebase.id", ondelete="SET NULL"), nullable=True))
    top_k: int = Field(default=5)
    candidate_k: int = Field(default=20)
    threshold: float | None = Field(default=0.45)      # NULL = calibrated (user pressed «Сбросить»)
    lexical: bool = Field(default=False)
    llm_rerank: bool = Field(default=False)
    hybrid: bool = Field(default=False)
    rewrite: bool = Field(default=False)
    mcp_server_ids_json: str | None = Field(default=None)  # NULL = default (server named local-git)
    tools_memory: bool = Field(default=False)
    tools_tasks: bool = Field(default=False)
    tools_scheduler: bool = Field(default=False)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```
Rows are created lazily on first PUT; `GET` with no row returns the same defaults (including threshold 0.45) so the UI and the turn agree (mirror `_config_out` default branch). Whether a "ready" check for the marked base uses `status == READY` only (a base in `phase="adding"` stays `ready`, so it counts) — matches the expected behaviour in CONTEXT. Use a JSON list of server ids rather than names so renaming a server does not silently drop it; the default (NULL) matches by name `local-git`. Dangling ids (deleted server) are simply ignored, and shown as D-08 warnings only if the user selected them while they existed — treat a missing chosen id as "not configured" -> warn with the stored name is not possible, so the PUT should store `{id}` and the warning for a vanished id uses the text "выбранный сервер"; simplest: prune missing ids on GET and never warn about them.

### Judge reuse
```python
# Source: scripts/rag_judge.py (build_judge_messages, parse_judge_reply, JUDGE_VERDICTS)
messages = build_judge_messages(question, expected_answer, answer, category)  # "out_of_corpus" adds the honest-"не знаю" rule
```
Use category `out_of_corpus` for the outside-the-project question so a refusal counts as correct.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Judge on `deepseek-chat` | `deepseek-flash` / `deepseek-v4-pro` | observed 2026-10-13 on live `/models` | pass `--model`; previous reports' model names are historical |
| Calibrated 0.67 as the only default | Per-use-case explicit threshold | this phase | `/help` default 0.45 |

**Deprecated/outdated:** none beyond the above.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Default `/help` threshold ~0.45 is good on the real indexed base (my sample used my own chunking, 5+5 queries) | Pitfall 1 | Sources missing or noisy; mitigated by re-measuring before freezing and by user-editable setting |
| A2 | qwen3.5-9b can call a git tool and still produce `[N]` + «Цитаты:» on mixed questions | Pitfall 3 | Mixed/git questions fail in the report; must be reported honestly, prompt tweaked |
| A3 | Skipping title generation, fact extraction, task memory and invariants critique on `/help` is acceptable to the user | Pitfall 10 | User may expect chat titles; low impact |
| A4 | Dropping auto-quotes and the unsupported warning when a tool answered is acceptable | Pitfall 2 | Slightly different UI than a pure RAG answer; low |
| A5 | `/help` recognition rule (whole word, case-insensitive, leading whitespace allowed) | Pattern 1 | Edge-case disagreement; trivially changed |
| A6 | Handling notices before the generation-queue slot is better than inside it | Pattern 6 | If skipped, a notice may wait behind long generations |

## Open Questions

1. **Should tool-call cards survive a page reload for `/help`?**
   - Known: they do not for any chat today (in-memory only); D-15 lists label, sources, quotes only.
   - Unclear: ROADMAP SC3 wording "exchange survives a page reload".
   - Recommendation: keep today's behaviour; assert label/sources/quotes after reload in VER-08; mention in the report. If the user wants cards, a separate small change would expose a parsed `tool_trace` on `/tree` and render it in `renderMessages` for all chats.

2. **Rate slot: refund vs deferred check** (Pattern 6). Recommendation: deferred check inside the help path, plus a `RATE_LIMITED` mapping for HTTP; planner to confirm the touched files.

3. **What if the marked base's embedding model is not loaded in LM Studio?** `retrieve_vectors` raises `RagFailure("embedder_unavailable")`. Recommendation: continue the turn without fragments, with a `rag.warning` (existing UI toast/line) and tools still available. Not specified in CONTEXT; low risk.

4. **Which DeepSeek answering model id?** Take from the isolated copy's model list at run time; record it. Cost note: 10 questions x tool rounds is small.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | — |
| git | local-git server, control questions | yes | 2.39.1.windows.1 | — |
| Node | not needed | yes | v22.23.2 | — |
| Playwright (Python) | VER-08 | yes | importable | blocked exit 4 if absent |
| LM Studio (`localhost:1234`) | qwen run, embeddings | yes (running now) | models: `qwen/qwen3.5-9b`, `text-embedding-bge-m3`, `giga-embeddings-instruct-480m-0826` | record run as "not run" (D-21) |
| DeepSeek API | DeepSeek run + judge | key accepted (`/models` 200) | models `deepseek-flash`, `deepseek-v4-pro`; `deepseek-chat` NOT served | none; record as not run if key refused |
| Ports 18000/18001 | isolated copy | not checked (agent on 8001 not running at probe time) | — | script's preflight blocks if busy |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** `deepseek-chat` judge default -> pass `--model deepseek-flash`.

## Validation Architecture

Skipped: `workflow.nyquist_validation=false`. Test-planning hints for the planner anyway (VER-07): pytest + pytest-asyncio + respx as in `tests/test_http_chat_rag.py` (fake embedder `install_fake_embedder`, `seed_kb`, `run_index_job`, `_queue_responses`, `mcp_stdio_server.py` fixture for tool calls). Cases worth having: parse rules; usage and no-base notices store nothing and call no model (assert no `Message` rows, LLM mock not hit); marked-but-not-ready base; real turn stores two messages with `rag_sources.help.kb_name`; chat `ChatRagConfig` row unchanged (and unchanged-absent) after a `/help`; default tools = `local-git` only (assert request `tools` list); built-in groups enabled individually; missing/failed chosen server -> turn still answers + warning in `rag.help.warnings` and HTTP body; `kb_id` ignored over HTTP and `ChatRagConfig` untouched; rate slot not consumed by notices; KB delete clears the mark; ordinary message after `/help` sees the pair (D-11); `/tree` returns `help` object after reload; UI source guards; docs guards; existing `test_tool_rounds_ws.py`, `test_ws_turn_failures.py`, `test_multistep_tools_ws.py`, `test_rag_ws.py`, `test_http_chat*.py` stay green after extracting the shared middle of `_run_chat_turn`.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2/V3 Authentication, Session | yes (reuse) | Cookie for WS, API key for REST (no change); new routes use `get_current_user` |
| V4 Access Control | yes | `HelpConfig` keyed by `user_id`; `PUT` must verify the chosen `kb_id` and MCP server ids belong to the caller (use `_get_owned_kb`; list servers by `user_id`); 404 not 403 (IDOR pattern in `_get_owned_chat`) |
| V5 Input Validation | yes | Pydantic body with ranges mirroring `RagConfigIn` (top_k 1-20, candidate_k 1-50, threshold 0-1, StrictBool); `/help` question length is bounded by `CONTENT_MAX_LENGTH` |
| V6 Cryptography | no | — |
| V12 Files / V13 API | partial | Control script never writes the DeepSeek key to a file; API key via env var only |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection from documentation chunks | Tampering | Existing `_neutralize` delimiter handling and "data, not instructions" block; unchanged. Docs are the user's own, but a tool-capable turn raises the stakes — defaults give read-only git tools only |
| Write tools reachable from `/help` | Elevation | Defaults off (D-07); built-in groups are explicit opt-in; MCP choice is per whole server, so a user who enables a write-capable server accepts it. State this in USER_GUIDE and the Settings hint (UI-SPEC already warns) |
| Cross-user base/server mark | Info disclosure | Ownership checks on PUT and again at turn time (`kb.user_id == chat.user_id`, as `_load_kb` does) |
| Leaking exception text via notice | Info disclosure | Notices are fixed strings |
| Secret in scripts/reports | Info disclosure | Key only in subprocess env; report records key prefix at most (Day 30 pattern) |

## Sources

### Primary (HIGH confidence — code read in this session)
- `agent/ws.py` (turn, tool rounds, gated reply, rate/queue wiring), `agent/http_chat.py`, `agent/rag_turn.py`, `agent/rag.py`, `agent/rag_pipeline.py`, `agent/rag_cite.py`, `agent/rag_api.py`, `agent/mcp_tools.py`, `agent/tools.py`, `agent/headless.py`, `agent/limits.py`, `agent/schemas.py`, `shared/models.py`, `shared/database.py`
- `ui/static/app.js` (`renderMessages`, `handleWsMessage`, `buildKbCard`, `buildRagQuotesBlock`, `buildRagSourcesBlock`, `buildRagMeta`, `buildToolCallCard`, `openSettingsModal`), `ui/static/index.html`
- `scripts/rag_judge.py`, `scripts/rag_dialog.py`, `scripts/e2e_kb_playwright.py`, `scripts/e2e_mcp_git_playwright.py`, `scripts/verify_service.py`, `Day30_report.md`
- `docs/TESTING_GUIDE.md` (Phase 21/22 sections already present), `.planning/phases/23-.../23-CONTEXT.md`, `23-UI-SPEC.md`, `.planning/REQUIREMENTS.md`

### Secondary (MEDIUM — executed this session)
- Retrieval experiment: LM Studio `text-embedding-bge-m3` over the repo's README/docs with `agent.kb_chunking.chunk_structural` (results in Pitfall 1)
- `python scripts/rag_judge.py --check` against the real DeepSeek key (model list)
- `pytest tests/test_http_chat.py` -> 20 passed (baseline green)

### Tertiary (LOW)
- None used.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies
- Architecture: HIGH — every integration point read in the live code; MEDIUM on the rate-slot approach (two viable options)
- Pitfalls: HIGH for threshold, strict-instruction conflict, copy-environment limits, judge model id (all verified); MEDIUM for local-model behaviour (untested)

**Research date:** 2026-10-13
**Valid until:** 2026-11-12 (code-bound; re-measure thresholds if the docs or embedding model change)
