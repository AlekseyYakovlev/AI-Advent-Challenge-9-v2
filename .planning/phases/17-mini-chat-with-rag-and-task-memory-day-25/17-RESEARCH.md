# Phase 17: Mini-chat with RAG and task memory (Day 25) - Research

**Researched:** 2026-10-10
**Domain:** extension of an existing FastAPI/SQLModel/vanilla-JS RAG chat (per-chat task memory, history-aware retrieval, multi-turn eval over WebSocket)
**Confidence:** HIGH (everything below was read from the actual code in this repo; no new external libraries are needed)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
(verbatim summary of 17-CONTEXT.md `## Implementation Decisions`; the full text is in 17-CONTEXT.md and the planner MUST read it)

**Task memory update rules**
- **D-01:** Task memory is produced by an LLM extraction call that **code always makes** after every answered turn: one non-streaming, temperature-0 call on the chat's own provider/model (same choice as 15 D-11) that returns JSON with `goal`, clarified points and constraints/terms. Code validates the JSON and merges it. It is not an LLM tool call and the model does not choose whether to save. This is the RCHAT-02 "deterministic" update.
- **D-02:** The update runs **inside the turn, before `done`**: after the answer stream ends, under the per-chat lock, and the fresh memory goes out in the same `done` frame. The debounced fire-and-forget pattern of `extract_and_update_facts` is explicitly not used.
- **D-03:** Merge is done by code and the goal is sticky. The model is asked only for what is new in this turn. Code appends unique clarified points and constraints, and replaces the goal only when the model flags an explicit change of goal by the user. Items are never silently dropped by an extraction; lists have a size cap.
- **D-04:** Only **user-stated** content enters task memory (not the assistant's answer, not law text); the answer is passed to the extraction call only as context.
- **D-05:** Task memory exists only in chats with **RAG on**. Chats without RAG make no extraction call and show no task-memory UI.
- **D-06:** A gated «не знаю» turn (16 D-10, answer LLM not called) **still updates** task memory from the user's message.
- **D-07:** Extraction is fail-soft: on timeout, unparsable JSON or any error, memory stays as it was, the turn completes normally, failure only logged / marked.

**Task memory in the UI**
- **D-08:** Own block «Память задачи» in the sidebar memory panel, above «Рабочая (этот чат)», parts «Цель», «Уточнено», «Ограничения и термины». If stored as a reserved working-memory row, that row is hidden from the plain working-memory list. Shown only in RAG chats, refreshes from the `done` frame.
- **D-09:** Every assistant message stores a **snapshot** of task memory after its turn. Under the answer, a collapsed «Память задачи» block shows it, items added this turn marked «новое».
- **D-10:** Manual control: «Сбросить» (native `confirm()`), «×» on each clarified point / constraint, editable goal. New user-scoped routes, Phase 11 style (Origin + JSON content-type checks).
- **D-11:** Task memory **follows the active branch**. On regenerate / edit of an earlier message / branch switch, current memory is restored from the snapshot of the last assistant message on the active path. Manual edits made after that snapshot are lost on a switch (accepted).

**History-aware retrieval**
- **D-12:** From the second turn of a RAG chat on, **every turn** condenses the new question with recent history and task memory into a standalone search query. Always on, independent of the «Переписывание запроса» switch. Turn 1 behaves exactly as in Phase 15. No UI switch to turn it off.
- **D-13:** The condensing call sees the last **N user/assistant pairs** plus task memory. Assistant answers trimmed to a few hundred chars, no quote blocks. N = per-chat «Ходов истории» in the «Поиск ⚙» popover: **0-10, default 3**, stored on `ChatRagConfig`. 0 = task memory only.
- **D-14:** On a follow-up **both** raw question and condensed query are embedded and searched; candidates merged by best cosine per chunk (15 D-12). The strict «не знаю» gate is decided on the merged result. A bad condensed output (empty, too long, multi-line, chatty) falls back to the raw question only. Gate not softened.
- **D-15:** Dialog-meta turns («подытожь», «спасибо») get no special case; retrieval runs on every turn.
- **D-16:** Task memory is rendered into the answering LLM's system prompt as labelled lines (goal / clarified / constraints).

**Scenarios and Day25 report**
- **D-17:** Claude drafts both scenarios; the user approves; frozen as a fixture before the first run. A = concrete driver case (speeding, repeat offence, «только по КоАП»); B = study/overview task on ФЗ-196 with a term fixed early. Each 10-15 messages with pronoun/ellipsis follow-ups, one out-of-corpus turn, one goal check near the end; fixture carries per-turn expectations.
- **D-18:** Scenarios run through the **real chat over WebSocket** on the isolated copy (ports 18000/18001, never 8000/8001): a new multi-turn mode in `scripts/rag_eval.py`. Per turn records answer, `done.rag` sources, quotes and states, verdict, condensed query, task-memory snapshot. One Playwright pass on the isolated copy for UI screenshots / UAT.
- **D-19:** Per-turn checks are **fully automatic** (sources present, quotes, verdict, expected memory items, expected article in sources on marked follow-ups, «goal kept» code proxy). No manual verdict column.
- **D-20:** `scripts/rag_judge.py` gets a goal-adherence rubric and a **DeepSeek judge** column; needs the real `DEEPSEEK_API_KEY` and the pause-for-key checkpoint from 15 D-18.
- **D-21:** Report contains two runs of both scenarios on the Day 24 configuration (bge-m3 KB, threshold 0.67, strict on, local `qwen/qwen3.5-9b` at temperature 0): the **main run** and a **«no memory» baseline** (eval-only flag disables task memory and history-aware rewrite, not a UI switch).
- **D-22:** `Day25_report.md` at repo root next to `Day22_report.md`..`Day24_report.md`, Russian, both transcripts with per-turn checks and task-memory contents (RCHAT-05). Gated turns, false refusals and extraction failures shown, not hidden.

### Claude's Discretion
- Where task memory is stored (reserved `dialog_state` `WorkingMemory` row holding JSON, or a dedicated table) and where the per-message snapshot goes (next `rag_sources` payload version, or a new `Message` column with idempotent `ALTER TABLE`). If the reserved row is used, `save_working_memory` must not be able to overwrite it.
- Extraction prompt wording, JSON schema, how "explicit change of goal" is flagged, dedup/normalization, list caps, extraction timeout and `max_tokens`.
- How labelled task memory replaces or sits next to the generic "Working memory" JSON line in `build_system_prompt`.
- Condensing prompt wording, trimmed-answer length, how `validate_rewrite` limits change for a condensed query, what «Детали поиска» shows for it.
- How the first-turn single-turn rewrite switch and the always-on follow-up condensing share code and trace fields.
- Route shapes for reset / delete item / edit goal, `done` frame field names, how a manual edit interacts with the latest snapshot.
- Mechanism of the eval-only «no memory» flag, fixture and raw-output locations, judge rubric text, report layout.
- How the extraction call interacts with tool-call rounds and with turns that end in an error.

### Deferred Ideas (OUT OF SCOPE)
- Rules-only (no LLM) extraction / hybrid with code-collected facts; storing assistant-established facts; task memory in non-RAG chats; collapsible task-memory strip above the chat; a UI switch for history-aware rewrite; re-sending previous fragments when a follow-up is gated or softening the gate; a goal-change scenario (the sticky-goal replace rule is still implemented and **must be covered by a test**); re-running the main run with DeepSeek as answerer; manual per-turn verdicts.
- Out of phase: separate mini-chat UI/CLI, new retrieval stages or threshold changes (Phase 15), changes to quote verification / «не знаю» gate rules (Phase 16), cross-encoder rerankers.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RCHAT-01 | Existing chat works as RAG mini-chat: history kept, retrieval on every new question, every answer shows its sources | Already true for single turns (`prepare_rag_turn` runs on every message, `rag_sources` persisted, sources block rendered). Phase work: make the retrieval query history-aware (Pattern 2) and ensure sources are present on every turn of the scripted scenarios; gated turns show the «не знаю» template, which is the documented honest outcome (see Pitfall 1) |
| RCHAT-02 | Task memory per chat: goal, clarified, constraints/terms; deterministic post-turn update; shown in UI | New `ChatTaskMemory` table + `agent/task_memory.py` (extract -> validate -> merge), run in `ws.py` between `finalize_rag_turn` and `_persist_assistant_message`; sidebar block C1 and per-message block C2 from UI-SPEC |
| RCHAT-03 | Task memory and recent history feed system prompt and retrieval query rewrite | `build_system_prompt` labelled lines; `prepare_rag_turn` gets history pairs + memory; `rewrite_query` sibling `condense_query`; `_rewrite_stage` searches raw + condensed and merges |
| RCHAT-04 | Two scripted 10-15 message scenarios run end to end; goal kept, sources every turn | New `dialog` sub-command in `scripts/rag_eval.py` that drives `WS /ws/chat/{id}` on the isolated copy; frozen scenario fixture (checkpoint) |
| RCHAT-05 | `Day25_report.md` with both transcripts and per-turn checks (goal kept, sources present, memory contents) | Report renderer modelled on Day22-24 report generators (`tests/test_rag_report*.py`), Russian, repo root |
</phase_requirements>

## Summary

The RAG pipeline of Phases 13-16 is a single pre-step: `agent/ws.py::_handle_chat_message` calls `prepare_rag_turn(session, chat, payload.content, llm_messages, ...)` after the system prompt and tool schemas are final. It runs `run_retrieval_pipeline` (embed question, optional single-turn rewrite that embeds and searches a second query and merges by best cosine, optional FTS5/lexical/LLM stages, raw-cosine threshold), applies the strict gate, merges a fragments block into the last user message of the **outbound** copy only, and returns a `RagTurn` whose payload (versioned, metadata only) is stored in `Message.rag_sources` and sent as `done.rag`. After the stream, `finalize_rag_turn` verifies quotes and strips the quote tail, then the assistant message is persisted. All of this runs under `chat_locks[chat_id]`. The existing machinery already does almost everything RCHAT-01 needs; what is missing is (a) any notion of history in the retrieval query (the pipeline sees only the raw last user text), (b) any task memory, and (c) a multi-turn eval driver that goes through the real WebSocket.

`dialog_state` does **not exist in the code today** (grep finds it only in `.planning/research/*` and ROADMAP). The roadmap flag "confirm `dialog_state` rendering in the memory panel" therefore resolves to: nothing renders it yet. Working memory is rendered as plain key/value rows (`renderMemoryEntries`, values truncated at 160 chars) and is injected into the system prompt as one JSON line. A reserved `WorkingMemory` row would leak through three places (`build_system_prompt` JSON line, `GET /chats/{id}/memory`, the `save_working_memory` tool), so this research recommends a **dedicated table** (allowed by CONTEXT "Claude's Discretion"), which keeps the API shape UI-SPEC expects (`task_state` object separate from `working`).

**Primary recommendation:** Add table `ChatTaskMemory` (one row per chat, JSON column) and a new module `agent/task_memory.py` (schema, extraction prompt, validation, merge, snapshot/diff, prompt rendering); store the per-message snapshot as an additive `task_memory` key inside the existing `rag_sources` payload (bump `PAYLOAD_VERSION` to 4, update the one test pinning `3`), so no `Message` column or `ALTER TABLE` is needed for it; add one `ALTER TABLE` for `chatragconfig.history_turns`; extend the existing rewrite stage into a history-aware condensing stage that reuses the already-built both-queries-merged search; write the extraction result **in the same commit** as the assistant message.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Task-memory extraction call + merge + caps | API / Backend (`agent/task_memory.py`) | — | Must be deterministic code under the per-chat lock; LLM only proposes deltas |
| Task-memory persistence + per-message snapshot | Database (`ChatTaskMemory`, `Message.rag_sources`) | API | Snapshot rides in the existing payload; row is the live state |
| History-aware condensed query + two-query merge | API / Backend (`rag_rank`, `rag_llm`, `rag_pipeline`) | — | Retrieval is server-side; the gate must see the merged result |
| History pairs for condensing | API / Backend (reads `Message` tree from DB) | — | Must come from the DB active branch, not from the compressed `llm_messages` |
| System-prompt rendering of task memory | API / Backend (`build_system_prompt`) | — | Single place that already composes memory layers |
| Branch restore of task memory | API / Backend (`POST .../branch`) | Database | The only backend path that moves `current_leaf_message_id` besides new turns |
| Manual edit / reset / delete-item routes | API / Backend | Browser | Origin + JSON checks and user scoping are server-side |
| Sidebar block, per-message snapshot block, «Ходов истории» input | Browser / Client (`app.js`, `index.html`) | — | Vanilla JS, `textContent` only |
| Scenario driver, per-turn auto checks, report | Scripts (`scripts/rag_eval.py`, `rag_judge.py`) | Isolated app copy | Real WS chat on ports 18000/18001 |

## Standard Stack

No new runtime dependency is required. Everything is built from what the repo already uses.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI / Starlette | >=0.115 (existing) | New routes, WS frame field | Project stack |
| SQLModel + aiosqlite | existing | New `ChatTaskMemory` table (created by `create_all`) | Project stack; new tables need no migration |
| httpx | existing | LLM calls through `LLMClient.complete_chat_detailed` | Project stack |
| pydantic v2 | existing | Validating extraction JSON (`TaskMemoryDelta`) and route bodies | Project stack |
| numpy / faiss (existing) | existing | Embeddings for the second query (already inside `retrieve_vectors`) | Reused unchanged |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `websockets` | 17.1 installed (registry latest 17.2) | WS **client** for the scenario driver | Only in `scripts/rag_eval.py` dialog mode. It is already installed in this environment (the uvicorn server needs it to serve WS) but is **not listed in `requirements.txt`** [VERIFIED: `pip show websockets` + `pip index versions websockets`; requirements.txt read]. Package legitimacy: slopcheck CLI lacks `--json`; the package is the long-standing aaugustin library and already runs in this environment, but the name was not confirmed via Context7/official docs this session, so treat as `[ASSUMED]` for provenance and have the planner add `websockets>=15` to `requirements.txt` with a one-line human confirmation. Alternative with zero new dependency: Starlette `TestClient` cannot reach a separate process, so it is not an option for the isolated copy. |
| Playwright (Python) | already used by `scripts/e2e_*_playwright.py` | One UI pass for screenshots / UAT | Same pattern as the Day 22-24 E2E scripts; not part of pytest |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Dedicated `ChatTaskMemory` table | Reserved `WorkingMemory` row `dialog_state` | Reserved row leaks into `build_system_prompt` JSON, `GET /memory` working list and `save_working_memory` (all three need special-casing, each a regression risk). Table needs only a `create_all` entry and cascade test |
| Snapshot inside `rag_sources` payload | New `Message.task_memory` column | Column needs `ALTER TABLE` plus `MessageResponse` change; payload key is additive and already flows to `done.rag` and `GET /tree` |
| Eval-only env flag for «no memory» baseline | Hidden `ChatRagConfig` column / WS payload field | Env flag adds zero API surface (matches "no UI switch"); cost: baseline needs a second app start |

**Installation:** none for the app. For the driver: `pip install "websockets>=15"` (already satisfied locally) and add it to `requirements.txt`.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| websockets | PyPI | 10+ yrs (versions back to 1.0) | very high | github.com/python-websockets/websockets | not run (CLI has no `--json`; `slopcheck install` would install) | `[ASSUMED]` legit; already installed locally; planner: add checkpoint:human-verify before adding to requirements.txt |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
 user msg ──WS /ws/chat/{id}──► _handle_chat_message  (chat_locks[chat_id] held for everything below)
                                   │ persist user msg (raw)
                                   │ build_llm_context ──► build_system_prompt
                                   │        └─ NEW: labelled task-memory lines (only if chat has RAG on)
                                   │ + clock/tool suffix, tool schemas
                                   ▼
                          prepare_rag_turn(..., parent_id, history_turns, task_memory)  [fail-soft]
                                   │  turn >= 2 ?  load last N (user,assistant) pairs from DB active branch
                                   │               + ChatTaskMemory row
                                   ▼
                       run_retrieval_pipeline
                          embed raw question ─────────────────────────┐
                          NEW: condense_query(raw, pairs, memory) ─► validate_condensed
                               ok ─► embed condensed ─► search ─► merge by best cosine
                               bad/empty ─► skip row "history" (raw only)
                          [hybrid / lexical / LLM rerank unchanged] ─► threshold (0.67) ─► top_k
                                   │
                  gate? (nothing survives both queries) ──yes──► _complete_gated_turn
                                   │ no                              │ NEW: update_task_memory(user msg only)
                                   ▼                                 │ persist reply + snapshot, done
                       merge fragments block into last user msg (outbound copy only)
                                   ▼
                       stream answer / tool rounds / invariants critique (unchanged)
                                   ▼
                       finalize_rag_turn (quotes, clean answer)
                                   ▼
                  NEW: update_task_memory(session, chat, user_text, answer, client, model)
                          extraction LLM call (temp 0, reasoning off, timeout) ─► JSON
                          validate (pydantic) ─► merge (sticky goal, append-unique, caps)
                          ok ─► stage row upsert (no commit) + snapshot into rag payload
                          fail ─► keep old memory, snapshot = carried-over, failed=true
                                   ▼
                  _persist_assistant_message  (ONE commit: message + task-memory row)
                                   ▼
                  done { ..., rag: { ..., task_memory: {goal, clarified[], constraints[], new, failed} } }

 branch switch ─► POST /api/v1/chats/{id}/branch ─► (NEW, under chat lock) restore ChatTaskMemory
                  from rag_sources.task_memory of the last assistant message on the new active path
```

### Recommended Project Structure
```
agent/
├── task_memory.py        # NEW: models (TaskMemoryDoc/Delta), prompts, extract, merge, snapshot, render, restore
├── rag_rank.py           # + CONDENSE_SYSTEM_PROMPT, build_condense_messages, validate_condensed, trim_answer
├── rag_llm.py            # + condense_query (sibling of rewrite_query), + run extraction through same _call
├── rag_pipeline.py       # _rewrite_stage generalised (history-aware branch), PipelineConfig.history, trace fields
├── rag_turn.py           # prepare_rag_turn(+parent_id), RagTurn.with_task_memory(...)
├── rag_api.py            # RagConfigIn/Out + history_turns
├── context_engine.py     # build_system_prompt: labelled task-memory lines
├── ws.py                 # two call sites: normal turn + _complete_gated_turn
└── main.py               # GET /memory (+task_state), task-memory routes, branch restore
shared/
├── models.py             # + ChatTaskMemory, ChatRagConfig.history_turns
└── database.py           # idempotent ALTER for chatragconfig.history_turns
ui/static/{app.js,index.html}   # C1 sidebar block, C2 snapshot block, C3 input, C4 details tweaks
scripts/
├── rag_eval.py           # + `dialog` sub-command (WS driver, auto checks, outputs)
└── rag_judge.py          # + `goal_adherence` rubric
tests/fixtures/rag/dialog_scenarios.json   # NEW frozen fixture (checkpoint)
tests/test_task_memory*.py, test_rag_history*.py, test_dialog_eval.py, test_rag_report_day25.py ...
Day25_report.md           # repo root
```

### Pattern 1: Task-memory update inside the turn (where exactly to hook)

**What:** In `_handle_chat_message` (agent/ws.py, around line 1111) the order today is `finalize_rag_turn` -> `_persist_assistant_message` -> conflict record -> `extract_and_update_facts` (debounced legacy facts) -> `compute_chat_stats` -> `done`. Insert the task-memory step **between `finalize_rag_turn` and `_persist_assistant_message`**:

```python
# Source: this repo, agent/ws.py (existing order) + new step
assistant_text, rag_turn = await asyncio.to_thread(finalize_rag_turn, rag_turn, payload.content, assistant_text)
if rag_turn.mode == MODE_RAG:                       # D-05: RAG chats only
    rag_turn = await update_task_memory(session, chat, rag_turn, payload.content, assistant_text, client, payload.model)
assistant_msg = await _persist_assistant_message(session, chat, user_msg.id, assistant_text,
                                                 tool_trace=tool_trace, rag_sources=rag_turn.sources_json)
```

`update_task_memory` (never raises except `CancelledError`): loads the row, calls the extraction LLM (outside any open write), validates, merges, **stages** the upsert with `session.add(...)` (no commit), returns `replace(rag_turn, payload={**payload, "task_memory": snapshot})`. `_persist_assistant_message` already does `session.add; flush; commit`, so the row and the assistant message commit atomically; a crash cannot leave the row ahead of (or behind) the snapshot. On extraction failure nothing is staged, the snapshot is the carried-over memory with `failed: true`.

Same call in `_complete_gated_turn` (D-06) **before** its `_persist_assistant_message`, passing the user message and an empty/none assistant text (the templated «Не знаю» reply carries no information). The `done` frame needs no new field because `rag_turn.done_payload` is the payload, so `done.rag.task_memory` arrives for free (UI-SPEC C5 "done frame carries fresh task memory" is satisfied by `done.rag.task_memory`).

Notes for the planner:
- `active_streams.pop(chat_id)` happens in `finally` right after streaming, so the Stop mechanism cannot interrupt the extraction; a WebSocket disconnect cancels the task (same exposure as the invariant critique call). Acceptable; do not add a tool-round special case.
- Tool rounds: extraction runs once after all rounds and the reprompt/fallback text, using the final `assistant_text`; it does not interact with `pending_tool_calls`. `save_working_memory` / tasks remain independent layers.
- Error turns (`LLM_ERROR`, context overflow) return before this step and delete the user message, so memory is untouched (correct).
- Latency: one more non-streaming call on the local 9B after the answer; Day 24 saw a 60 s `LLM_TIMEOUT` ReadTimeout on one question. Use a dedicated `TASK_MEMORY_TIMEOUT` setting (suggest 30 s) rather than `RAG_LLM_STAGE_TIMEOUT` (45 s); the eval driver's per-frame read timeout must be larger than answer + extraction time (use >= 240 s).

### Pattern 2: History-aware condensing reusing the existing two-query merge

**What:** `agent/rag_pipeline.py::_rewrite_stage` already (a) calls `rewrite_query`, (b) embeds and searches the rewritten query, (c) merges into `candidates` by best cosine (`FOUND_BOTH` / `FOUND_REWRITTEN`), (d) appends the second vector so hybrid cosine recomputation uses both, (e) records `rewritten` and `rewrite_cosine` in the trace. D-14 is exactly this behaviour. The strict gate (`verdict = below_threshold if ordered and not survivors`) is evaluated after the merge, so "fires only when nothing survives from either query" holds with no gate change.

Implementation shape (prescriptive):
- Add `PipelineConfig.history: HistoryContext | None` (frozen dataclass: `pairs: tuple[tuple[str, str], ...]`, `memory: TaskMemoryDoc | None`) populated by `prepare_rag_turn` only when turn >= 2 and (`pairs` non-empty or memory non-empty). `run_retrieval_pipeline` runs the stage when `config.rewrite or config.history is not None`.
- In `_rewrite_stage` choose the callable: `condense_query(client, model, question, history)` when `config.history` else `rewrite_query(...)`. When condensing runs, the single-turn rewrite is **not** also called (one extra LLM call per follow-up, not two).
- New stage name `STAGE_HISTORY = "history"` appended to `trace["stages"]` instead of `"rewrite"` when condensing ran, plus `trace["condensed"] = True` and `trace["history_pairs"] = len(pairs)`. UI-SPEC C4 needs exactly those two facts («Уточнён:» label, `история ×N`). Skip reasons reuse `run.skip(STAGE_HISTORY, reason)`; add `history` to the frontend maps `RAG_SKIP_STAGE_NAMES` / `ragSkipReasonText` (the UI-SPEC wording «Уточнение запроса»).
- Hybrid FTS text already becomes `f"{question} {rewritten}"`, so FTS also benefits from the condensed query with no change. The lexical and LLM-rerank stages still score against the raw `question` (Phase 15 owns them; they are off by default and off in the Day 24 configuration). Do not change them; mention it in the report only if they are enabled.
- Eval-only baseline: the flag turns the whole `history`/memory feature off in `prepare_rag_turn` (history=None) and in `update_task_memory` / `build_system_prompt`, i.e. one module-level check of a `settings.TASK_MEMORY_ENABLED` (env `TASK_MEMORY_ENABLED=false`, default true) added to `shared/config.py`. The driver starts a second copy of the app for the baseline.

**History source (important):** take the pairs from the **database active branch** via the user message's `parent_id` (walk `Message.parent_id` up from `user_msg.parent_id`, collect up to N complete user/assistant pairs, oldest first), not from `llm_messages`. `llm_messages` is compression-dependent (sliding window, summaries), contains expanded tool traces and the fragments-merged last message. Stored assistant content is already clean (16 D-02: no quote tail). Trim each assistant answer to about 300 characters, remove `[N]` markers, and for turns whose stored payload has `gated: true` or `verdict == "model_idk"` replace the assistant text by a fixed short marker (for example «(ответа в базе знаний не нашлось)») so the templated «Не знаю: ... ближайшие темы» text does not pollute the condensed query.

**Turn definition:** "from the second turn on" == `user_msg.parent_id is not None` and an assistant ancestor exists. Turn 1 -> Phase 15 behaviour exactly (the switch still controls rewrite). With `history_turns = 0` and empty memory on turn >= 2 there is nothing to condense with -> skip silently (no trace row).

**Validation of the condensed query:** do **not** reuse `validate_rewrite` as is. It rejects when (1) the original's digit tokens are not a subset of the output (fine to keep: article numbers must survive), but (2) requires stem overlap with the original (`original_stems & stems(text)`), which breaks pronoun/ellipsis follow-ups like «а за повторное?» only if the original has stems and none appear; and (3) the `endswith("?")` rule, 25-word and `max(120, 3*len(original))` char caps are OK for a condensed query. Add `validate_condensed(original, raw)` in `rag_rank.py` that reuses `clean_llm_text`, `_REWRITE_LABEL_RE`, `CHATTY_PREFIXES`, the newline / fence / length / word caps, and the digit-subset rule, but drops the stem-overlap rule and treats "equal to the raw question" as `unchanged` (single search). Keep the same `StageOutcome(value, reason)` contract so `_rewrite_stage` stays generic.

**Prompt-injection hygiene:** `_neutralize_data_tags` / `_DATA_TAG_RE` only break `<question>` and `<fragment>` tags. The condensing and extraction prompts will wrap history, memory and the user text in new tags (suggest `<history>`, `<memory>`, `<question>`, `<user_message>`, `<assistant_answer>`); extend the regex (or add a sibling) so these names are neutralised in untrusted text too, with a unit test.

### Pattern 3: Task-memory document, merge and snapshot

**Doc shape (stored as JSON text in the table and in the payload):**
```json
{"goal": "…" | null,
 "clarified":   [{"id": 3, "text": "…"}],
 "constraints": [{"id": 4, "text": "…"}],
 "next_id": 5}
```
Snapshot in the payload = the doc without `next_id` plus `"new": {"goal": bool, "ids": [..]}` and `"failed": bool` (computed at write time so history is stable; UI-SPEC C2 reads it). Stable integer ids make the `×` route race-free (no index shifting) and give a trivial diff for «новое».

**Extraction schema (what the model returns, validated with a pydantic model, extra keys ignored):**
`{"goal": str|null, "goal_changed": bool, "clarified": [str], "constraints": [str]}` — "only what is new in this turn" (D-03). Inputs to the call: current memory (so the model does not repeat it), the user message inside `<user_message>`, the assistant answer inside `<assistant_answer>` marked as context only (D-04). Output rules in the prompt: Russian, short phrases, no law quotes, no article text, only things the **user** said; empty arrays are fine.

**Merge rules (pure function, unit-tested without any LLM):**
- Goal: if stored goal is empty -> take model goal (trimmed, <= 300 chars, matching the UI textarea `maxlength`); else replace only if `goal_changed is True` and the new goal is non-empty and differs after normalisation (casefold, collapse whitespace). **Required test (CONTEXT deferred list):** explicit goal change replaces; absent flag keeps.
- Items: strip, collapse whitespace, cap each at ~200 chars, drop empties; dedup against existing by normalised text and by high token overlap (reuse `rag_rank.stems`, Jaccard >= 0.8) so a paraphrase of a stored point is not added twice; append with new ids.
- Caps: ~12 per list. When a list is full, **new items are dropped, never old ones** (D-03), counted and logged (`task_memory_cap_hit`). Total doc size therefore bounded (~12*200*2 + 300 chars), which keeps the system-prompt growth and the RAG budget impact small.
- Parsing: reuse `rag_rank.clean_llm_text` (strips `<think>`), then strip ```json fences and take the outermost `{...}`; pydantic validation failure -> fail-soft (D-07). Run the call through `rag_llm._complete` semantics (temperature 0, `reasoning_effort: "none"`, one retry without it on HTTP 400/422); expose a small public wrapper rather than importing the private `_call` from another module. `max_tokens` ~ 400.

**System prompt rendering (D-16):** in `build_system_prompt`, after the existing "Working memory" line, when the chat has `ChatRagConfig.mode == "rag"` and the doc is non-empty append, in Russian to match the rest of the RAG prompts:
```
Память задачи (этот чат):
Цель диалога: …
Уточнено пользователем: …; …
Ограничения и термины (соблюдай их): …; …
```
Because `ChatTaskMemory` is a separate table, the generic "Working memory" JSON line needs no change (this is the main advantage over the reserved-row design). `compute_chat_stats` also calls `build_system_prompt`, so the stats panel automatically accounts for the extra tokens. Gate the lines on `settings.TASK_MEMORY_ENABLED`.

### Pattern 4: Branch-following memory (D-11)

There is **no regenerate or edit endpoint** in the backend. "Edit an earlier message" and "regenerate" are implemented in the frontend as `branchFromMessage(parentId)` -> `POST /api/v1/chats/{id}/branch` (moves `current_leaf_message_id`), then a normal new user message; `switchBranch` uses the same route. So the single hook is `branch_chat` in `agent/main.py`:
1. Take `chat_locks[chat_id]` (same pattern as the task routes at `agent/main.py` ~line 925) so a restore cannot interleave with an in-flight turn.
2. After repointing the leaf, walk `_build_tree_path` from the new leaf, find the first message with `role == "assistant"` whose parsed `rag_sources` has `task_memory`, and rewrite the `ChatTaskMemory` row from it; none found -> delete the row (empty memory). A regenerated turn branches from the parent user message, so the nearest assistant ancestor is the right snapshot automatically.
3. Skip the restore when `body.message_id == chat.current_leaf_message_id` (no-op branch) so manual edits are not discarded for nothing.
4. The frontend must call `loadChatMemory` after `branchFromMessage` and `switchBranch` (today they call only `loadChatTree` + `loadChatStats`, app.js lines 1574-1605). Without this the sidebar would show stale memory.
Extraction-failed turns store the carried-over memory in the snapshot (with `failed: true`), so every Phase 17 RAG assistant message has a snapshot; pre-Phase-17 messages have none and restore to empty.

### Pattern 5: Routes (user-scoped, Origin + JSON)

Follow Phase 11 (`agent/main.py` ~715-765): `Depends(require_allowed_origin)` on every mutation, plus `require_json_content_type` on routes with a body; ownership via `_get_chat_or_404(session, chat_id, current_user.id)` (404, not 403); take `chat_locks[chat_id]` around the read-modify-write.

| Route | Body | Notes |
|-------|------|-------|
| `PUT /api/v1/chats/{chat_id}/task-memory/goal` | `{goal: str (1..300)}` | edit goal; returns the `task_state` object |
| `DELETE /api/v1/chats/{chat_id}/task-memory/items/{item_id}` | — | removes one clarified/constraint item by stable id; 404 if absent |
| `POST /api/v1/chats/{chat_id}/task-memory/reset` | `{}` (JSON) | clears the row (UI wraps it in native `confirm()`) |
| `GET /api/v1/chats/{chat_id}/memory` | — | add optional `task_state: TaskStateOut | None` to `ChatMemoryResponse` (None when RAG is off so the UI hides the block; default `None` keeps old tests green) |

Routes should refuse (409 or 404) when the chat has RAG off (D-05). Manual edits mutate the live row only; they do not rewrite snapshots (D-11 accepted loss). Add `ChatTaskMemory` to the cascade test (`tests/test_cascade_delete.py` pattern); no `agent/state.py` cache is added, so `cleanup_chat_caches` is unchanged.

### Pattern 6: «Ходов истории» setting

Copy the `strict`/`candidate_k` plumbing: `ChatRagConfig.history_turns: int = Field(default=3)`; tuple entry `("history_turns", "INTEGER DEFAULT 3")` in `_CHATRAGCONFIG_RANK_COLUMNS` (`shared/database.py`, idempotent loop already exists); `RagConfigIn.history_turns: int | None = Field(default=None, ge=0, le=10)`; `RagConfigOut.history_turns`; `_apply_search_settings` sets it when not None (note: `0` is valid, so test `is not None`, not truthiness); `_config_out` default row returns 3; log line in `put_rag_config`; frontend: add `'history_turns'` to the key list near app.js:4038, popover row per UI-SPEC C3 (`rag-history-turns`), same clamp-on-blur as `rag-candidate-k` (app.js:4001-4006). Add an `rag_search_static` assertion for the new input.

### Anti-Patterns to Avoid
- **Do not** build the history from `llm_messages`: compression strategies and tool-trace expansion distort it, and the last user message already carries the fragments block.
- **Do not** let the extraction call see or store law text, quote blocks or fragments (D-04, Pitfall 21). Pass only the clean stored answer text, trimmed.
- **Do not** write the memory row with its own `commit` before the assistant message is persisted; stage it and let `_persist_assistant_message` commit once.
- **Do not** touch quote verification, the gate rules or the threshold (Phase 15/16 own them).
- **Do not** use `datetime.utcnow()`, bare `except:`, `print()`; use `structlog` with `snake_case` keys and no user text in logs (log counts and ids only).
- **Do not** put task-memory strings into HTML; `textContent` only (`test_rag_static.py` guards this style).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Calling the chat's model non-streaming with reasoning off, timeout, 400/422 retry | A new HTTP wrapper | `rag_llm._complete` / `_call` (expose a public wrapper) | Already handles Qwen reasoning control, timeouts, `StageOutcome` reasons, logging without text |
| `<think>` stripping, chatty-prefix rejection, label stripping | New regexes | `rag_rank.clean_llm_text`, `CHATTY_PREFIXES`, `_REWRITE_LABEL_RE` | Behaviour proven on Day 23 evals |
| Searching two queries and merging by best cosine, gate on merged result | A second retrieval path | `rag_pipeline._rewrite_stage` generalisation | Already implements D-14 incl. hybrid vectors list |
| Atomic persistence | Separate commits | `session.add` + existing `_persist_assistant_message` commit | Avoids row/snapshot divergence |
| Origin / JSON content-type guards | Custom checks | `require_allowed_origin`, `require_json_content_type` | Phase 11 pattern |
| Idempotent column add | Ad-hoc ALTER | `_CHATRAGCONFIG_RANK_COLUMNS` loop | Existing |
| Token dedupe of paraphrased items | Own stemmer | `rag_rank.stems` | Russian stemming already there |
| WS client for the driver | Raw sockets | `websockets` | Installed; handle headers (`Cookie`, `Origin`) natively |
| Per-turn quote/verdict classification | Reimplement | Read `done.rag` fields (`verdict`, `gated`, `quotes`, `answer_supported`) and reuse `classify_reply`-style mapping from `rag_eval.py` | Same semantics as Day 24 tables |

**Key insight:** this phase is wiring and state management on top of a finished retrieval pipeline. The risk is not missing libraries but breaking invariants the earlier phases established (clean stored answers, fragments only in the outbound copy, fail-soft stages, single commit per turn).

## Runtime State Inventory

Not a rename/refactor phase; one migration-like item applies.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Existing `chatragconfig` rows lack `history_turns`; existing assistant messages lack `task_memory` in `rag_sources` | Idempotent `ALTER TABLE ... DEFAULT 3` (code edit in `shared/database.py`). Old messages: no snapshot -> block not rendered, restore -> empty memory. No data migration |
| Live service config | None — verified: no external service stores chat config | none |
| OS-registered state | None — verified: nothing registered by name | none |
| Secrets/env vars | New optional `TASK_MEMORY_ENABLED` (+ `TASK_MEMORY_TIMEOUT`) in `shared/config.py`; `DEEPSEEK_API_KEY` needed only for the judge run | add to `.env.example` / config docs |
| Build artifacts | None | none |

## Common Pitfalls

### Pitfall 1: The strict gate will refuse follow-ups (false refusals) on the Day 24 configuration
**What goes wrong:** Day24_report.md measured that threshold 0.67 with strict on refused 3 of 8 answerable single questions (Q01, Q07, Q08). Short elliptic follow-ups («а за повторное?») embed far from any chunk, so the raw query alone will usually be gated; the condensed query is what rescues them, and only if the condensing call returns a valid query.
**Why it happens:** bge-m3 cosine for terse questions is low; D-14/D-15 deliberately do not soften the gate.
**How to avoid:** make the condensing robust (always include goal/terms in the prompt, validate leniently, fall back to raw). Expect gated turns in the transcripts and report them as they happen (D-22). The "no memory" baseline exists to show the difference.
**Warning signs:** `trace.skipped` containing `history` with `bad_output`; `search.condensed` false on a follow-up.

### Pitfall 2: Reasoning model eats the token budget / times out
**What goes wrong:** `qwen/qwen3.5-9b` with reasoning left on spends `max_tokens` on thinking (Day 24 doubled `max_tokens` to 8192 and still had a 60 s ReadTimeout). Two extra non-streaming calls per turn (condense + extract) add latency.
**How to avoid:** keep `reasoning_effort: "none"` with the 400/422 retry (already in `_complete`), small `max_tokens` (condense 96 like rewrite, extract ~400), and a dedicated extraction timeout. In the driver, per-frame timeout >= 240 s and an explicit `error` frame path that is recorded, not raised.

### Pitfall 3: Frontend refreshes the tree before memory is consistent
**What goes wrong:** On `done` the client calls `loadChatTree` and `loadChatMemory` (app.js ~1485). Because extraction and the table write happen **before** `done` and commit with the assistant message, both reads are consistent. If extraction were moved after `done` they would race.
**How to avoid:** keep the order of Pattern 1; do not make extraction async.

### Pitfall 4: Stale memory after branching, and dirty ORM state
**What goes wrong:** Branch route does not take the chat lock and does not restore memory; or the memory row is staged on the session and then a later `rollback` (e.g. in an error path) silently drops it while the snapshot says otherwise.
**How to avoid:** Pattern 4 (lock + restore + no-op skip); stage the row immediately before `_persist_assistant_message` with nothing awaiting in between except that commit.

### Pitfall 5: Memory pollution and bloat
**What goes wrong:** The model copies article text or its own answer into "clarified"; lists grow every turn; goal drifts (Pitfall 21 / 18 in PITFALLS.md).
**How to avoid:** prompt restricts to the user's statements; code caps and dedups; sticky goal; tests with a stub LLM returning hostile output (law text, giant lists, goal without `goal_changed`).

### Pitfall 6: Task-memory text reaches a prompt unescaped
**What goes wrong:** A user types `</memory>` or `</question>` in a clarification and later escapes the data wrapper of the condensing/extraction prompt.
**How to avoid:** extend the data-tag neutraliser to the new tag names and test it (see Pattern 2).

### Pitfall 7: `0` treated as "unset" for «Ходов истории»
**What goes wrong:** `if body.history_turns:` or `value or 3` turns 0 into 3 (the whole point of the setting is that 0 means memory only).
**How to avoid:** `is not None` everywhere (server and the JS clamp: empty -> 3, but `0` stays 0).

### Pitfall 8: PAYLOAD_VERSION bump breaks an existing test
**What goes wrong:** `tests/test_rag.py:303` asserts `payload["v"] == 3`; `line 244` compares to the constant.
**How to avoid:** bump to 4 deliberately and update line 303; keep `parse_rag_payload` tolerant of v1-v3 (tests at lines 340-352 already check old versions parse).

### Pitfall 9: Eval driver uses the wrong DB/user/ports
**What goes wrong:** Pointing the driver at `app.db` or ports 8000/8001; or forgetting that the Day 23 scratch DB user `rag_eval` has a random password.
**How to avoid:** reuse `e2e_kb_playwright.prepare_copy` (patches ports in `app.js`, `login.html`, `agent/state.py`, refuses leftovers) and a **copy** of `eval_out/day23/eval.db` + `eval_out/day23/eval_kb` (KB id 2 = `day22-bge`, bge-m3, status ready, owner user id 1 `rag_eval`) via `DB_PATH` / `KB_STORAGE_DIR`; set a known password with `shared.auth.hash_password` directly in the copied DB; assert `Path(DB_PATH) != app.db`. Never kill the user's app on 8000/8001.

## Code Examples

### Hook in the gated path (D-06)
```python
# Source: this repo, agent/ws.py::_complete_gated_turn (existing shape) + new step
rag_turn = await update_task_memory(session, chat, rag_turn, payload.content, "", client, payload.model)
assistant_msg = await _persist_assistant_message(
    session, chat, user_msg.id, reply_text, rag_sources=rag_turn.sources_json,
)
```
`_complete_gated_turn` currently has no `client` parameter; add it (it is available in `_handle_chat_message`).

### Pure merge function (testable without LLM)
```python
# Source: new, agent/task_memory.py (prescriptive sketch)
def merge_delta(doc: TaskMemoryDoc, delta: TaskMemoryDelta) -> tuple[TaskMemoryDoc, MergeInfo]:
    """Sticky goal, append-unique items, hard caps; never removes existing items."""
    goal = doc.goal
    if not goal and delta.goal:
        goal = clip(delta.goal, GOAL_MAX)
    elif goal and delta.goal_changed and delta.goal and norm(delta.goal) != norm(goal):
        goal = clip(delta.goal, GOAL_MAX)
    ...
```

### Multi-turn WS driver skeleton
```python
# Source: websockets client + this repo's ws_chat (cookie auth, Origin check)
async with websockets.connect(
    f"ws://localhost:{AGENT_PORT}/ws/chat/{chat_id}",
    additional_headers={"Cookie": f"{SESSION_COOKIE_NAME}={session_id}", "Origin": f"http://localhost:{UI_PORT}"},
) as ws:
    await ws.send(json.dumps({"content": text, "model": model, "provider_id": provider_id}))
    tokens, frame = [], None
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=240))
        if frame["type"] == "token": tokens.append(frame["content"])
        elif frame["type"] in ("done", "error"): break
```
Cookie name from `shared.auth` (`SESSION_COOKIE_NAME`); log in first with `POST /api/v1/auth/login` (httpx, Origin header) to obtain it. Provider id: `GET /api/v1/llm-providers` (seeded LM Studio provider). Per-chat settings via `PUT /api/v1/settings` with `chat_id` (temperature 0, `max_tokens` 8192, `context_length` 16384 as in Day 24) and RAG via `PUT /api/v1/chats/{id}/rag` (`kb_id` 2, `mode` rag, `top_k` 5, `candidate_k` 20, `threshold` null -> calibrated 0.67, `strict` true, all stage flags false, `history_turns` 3). The WS rate limiter and `context_length` are per chat; use a fresh chat per scenario run.

## Scenario driver, fixture and report (RCHAT-04/05)

- **Existing eval structure** (`scripts/rag_eval.py`, 1909 lines): sub-commands `build-kbs`, `run`, `calibrate`, `ablate`, `cite`; all run the pipeline **in process** against a scratch DB (`_use_scratch_storage`), emit `raw/*.json`, `*.md`, `answers.csv`, `run_meta.json` under `eval_out/dayNN/`. The `cite` sub-command (lines 1336-1860) is the closest template for outputs, `--render-only`, user-verdict preservation and `run_meta.json`. The new `dialog` sub-command is different in kind: it drives the real app over HTTP+WS, so it must manage an app process (copy `prepare_copy` + start/teardown from `e2e_kb_playwright.py`, exit codes 0/1/2 preflight as in the e2e scripts).
- **Fixture:** `tests/fixtures/rag/dialog_scenarios.json`, same envelope as `control_set.json` (`version`, `status: draft|frozen`, `frozen_at`, `corpus`, plus `scenarios[]`), a `require_frozen` loader and sha256 recorded in `run_meta.json` (copy `load_fixture` / `fixture_sha256`). Per turn: `id`, `text`, `kind` (`direct` / `followup` / `out_of_corpus` / `goal_check`), `expect_memory` (substrings or keyword groups expected in goal / clarified / constraints after the turn), `expect_article` (for marked follow-ups; article number in `done.rag.sources[*].section`), `expect_keywords` (answer). Add a `tests/test_dialog_fixture.py` modelled on `test_rag_fixture.py` (counts 10-15 per scenario, scenario A/B ids, one out-of-corpus and one goal-check turn each, expected articles must exist in the corpus). Freeze only after the user checkpoint (14 D-13 / 15 D-15 pattern): the plan needs a `checkpoint:human-verify` task between drafting and the first run.
- **Per-turn automatic checks (D-19):** sources present (`done.rag.sources` non-empty), quotes (model verified / auto counts, as in `_quotes_cell`), verdict (`ok` / `below_threshold`-gated / `model_idk`, from `done.rag.verdict` and `gated`), expected memory items present (case-insensitive substring/keyword match on the snapshot), expected article in sources on marked follow-ups, and "goal kept" = goal text unchanged since turn 1 AND `expect_keywords` found in the answer. Gated and error turns are first-class outcomes (counted, shown), not failures to hide.
- **Raw data per turn:** `question`, `answer` (concatenated `token` frames; the stored clean answer is also in `GET /tree`), the whole `done.rag` (carries `search.query`, `search.rewritten` = condensed query, `search.condensed`, `candidates`, `quotes`) and `done.rag.task_memory`. This is why putting the snapshot into `done.rag` is convenient: one frame feeds the report.
- **Runs (D-21):** main (memory + history on) and baseline (`TASK_MEMORY_ENABLED=false`) x scenarios A and B; the baseline app instance is started with the env flag. In the baseline, `done.rag.task_memory` is absent, the memory columns show «—».
- **Judge (D-20):** add a `goal_adherence` rubric to `RUBRICS` in `scripts/rag_judge.py` following the existing `faithfulness` structure (`build_*_messages`, `parse_judge_reply`, verdict set, `_write_meta`); it must see the scenario goal, the memory snapshot, the question and the answer. Reuse `check_access` and the pause-for-key checkpoint (15 D-18): the plan needs a `checkpoint:human-action` task for the real `DEEPSEEK_API_KEY` (tests run with the key forced empty by `tests/conftest.py`; only the live run needs it).
- **Report:** `Day25_report.md` in the Day22-24 style: `## Постановка` (corpus, KB `bge`, config, runs, fixture sha256, honesty notes), per-scenario transcript tables (turn, question, condensed query, sources/articles, verdict, quotes, memory delta, auto checks, judge), side-by-side main vs baseline for the marked follow-ups, `## Отказы и сбои` (gated turns, false refusals, extraction failures `память не обновлена`), `## Ограничения`. Add a pure renderer (like the `render_*_md` helpers) with `tests/test_rag_report_day25.py` modelled on `test_rag_report_day24.py`.
- **UI UAT:** one Playwright script `scripts/e2e_rag_dialog_playwright.py` reusing the building blocks of `e2e_kb_playwright.py` / `e2e_rag_playwright.py` / `e2e_rag_search_playwright.py` (as `e2e_rag_cite_playwright.py` does); screenshots saved next to the report outputs. Document it in `docs/TESTING_GUIDE.md`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Retrieve on the last user message only | Condense-question rewrite using last turns + a state summary | standard conversational-RAG practice; recommended in `.planning/research/FEATURES.md` §Day 25 and PITFALLS 21 | Follow-ups with pronouns/ellipsis retrieve correctly |
| LLM decides what to save via tool call (Day 11 working memory) | Code always runs a deterministic extraction + merge for task memory | this phase (D-01) | Reliable on a local 9B model |
| Debounced fire-and-forget fact extraction (`extract_and_update_facts`) | Synchronous, in-turn, lock-held update | this phase (D-02) | No stale memory for the next question or the UI |

**Deprecated/outdated:** none relevant. `extract_and_update_facts` (legacy facts into `Settings.facts_json`) stays as is; it is a different layer and must not be reused for task memory.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `websockets` is a legitimate, intended dependency to add to `requirements.txt` | Standard Stack | Low; already installed and required by uvicorn for WS; planner adds a human check |
| A2 | Extraction timeout of ~30 s and `max_tokens` ~400 are enough for `qwen3.5-9b` with reasoning off | Pattern 1/3 | Medium; if too tight, extraction failures show up in the report as «память не обновлена». Tune after the first live run |
| A3 | List caps ~12 per list and 200/300 character limits are reasonable | Pattern 3 | Low; user decision D-03 only asks for "a size cap" |
| A4 | `eval_out/day23/eval.db` + `eval_kb` are still intact, usable as the KB for the isolated copy (KB id 2, `text-embedding-bge-m3`, status ready, owner `rag_eval`) | Pitfall 9 | Medium; I read the DB rows (verified), but the on-disk FAISS index and LM Studio embedder were not exercised this session. Fallback: re-run `build-kbs` |
| A5 | LM Studio is not running right now (curl to :1234 returned nothing) | Environment | Live runs/UAT need it started with `qwen/qwen3.5-9b` and bge-m3 loaded |
| A6 | Placing the snapshot in `rag_sources` rather than a new column is acceptable | Pattern 1 | Low; CONTEXT leaves this open |

## Open Questions (RESOLVED)

1. **Dedicated table vs. the reserved `dialog_state` row**
   - Known: CONTEXT/UI-SPEC wording says "reserved row", but both leave the choice to the planner; UI-SPEC only requires a separate `task_state` object in the API and exclusion from `working`.
   - Recommendation: dedicated table `ChatTaskMemory` (no leaks into `build_system_prompt`, `GET /memory`, `save_working_memory`). If the user insists on the row, `save_working_memory` (`agent/tools.py:331`) must reject key `dialog_state`, `list_working_memory` consumers (`context_engine.py:254`, `main.py:697`) must filter it, and `SaveWorkingMemoryArgs` needs a validator.
   - RESOLVED: dedicated table `ChatTaskMemory` (plan 17-01, Task 1; rationale in that plan's objective). The reserved `dialog_state` row is not used, so `save_working_memory`, the `list_working_memory` consumers and `SaveWorkingMemoryArgs` stay unchanged; the API exposes it as a separate `task_state` object (plan 17-04, Task 1).
2. **Manual edit vs. snapshot after a no-op or later turn**
   - Known: D-11 accepts losing manual edits on a real branch switch.
   - Recommendation: skip restore on a no-op branch call (Pattern 4 step 3); otherwise follow D-11. Mention in `docs/USER_GUIDE.md`.
   - RESOLVED: a no-op branch call skips the restore and keeps manual edits; a real branch switch restores the document from the nearest snapshot on the new active path, or clears it, per D-11 (plan 17-04, Task 2). The user-guide note is written in plan 17-11, Task 2.
3. **Does UI-SPEC's "POST reset" vs a REST `DELETE` matter?** Either works; `POST .../reset` with `{}` JSON is recommended because `require_json_content_type` is applied to POST/PUT in this app. RESOLVED: `POST /api/v1/chats/{chat_id}/task-memory/reset` with a `{}` JSON body (plan 17-04, Task 1); no `DELETE` route.
4. **Baseline run mechanism requires restarting the isolated app** (env flag). Alternative is a hidden request field; not recommended because it adds API surface. RESOLVED: env flag `TASK_MEMORY_ENABLED` (default true), defined in plan 17-01 Task 1 and honoured in plans 17-01, 17-03, 17-04 and 17-06; the `baseline` run restarts the isolated app with `TASK_MEMORY_ENABLED=false` (plan 17-05 driver, plan 17-09 runs). No request field and no UI switch (D-21).
5. **LM Studio model/embedder availability at execution time** (see Environment). RESOLVED: handled at execution time in plan 17-09: Task 1 preflight on the isolated copy, then Task 2, a blocking `checkpoint:human-action` where the user starts LM Studio with the chat and embedding models before the live runs.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | — |
| pytest / pytest-asyncio / respx | tests | yes | pytest 9.1.1 | — |
| `websockets` (client) | scenario driver | yes (not in requirements.txt) | 17.1 | add to requirements.txt |
| Playwright (Python) | UI UAT | yes (`import playwright` ok) | — | skip UAT with exit 4 like other e2e scripts |
| LM Studio on :1234 (`qwen/qwen3.5-9b`, bge-m3) | live scenario runs, UAT | no (nothing answered at time of research) | — | Start it before execution; unit tests use respx and need no LM Studio |
| DeepSeek API key (`.env` has a `DEEPSEEK_API_KEY` line; value not inspected) | judge column only | unknown | — | Pause-for-key checkpoint (15 D-18); report can be generated without the judge column and re-rendered |
| Corpus PDFs | only if KB must be rebuilt | partial: `C:\Projects\RAG` lists `КОАП РФ.pdf`, `ПДД.pdf` (ФЗ-196 file name not confirmed in the listing) | — | Reuse the existing `eval_out/day23/eval.db` KB (id 2) |
| Ports 18000/18001 | isolated copy | not checked (user's app on 8000 was not running) | — | `e2e_kb_playwright.preflight()` checks |

**Missing dependencies with no fallback:** LM Studio for the live runs (a human must start it).
**Missing dependencies with fallback:** DeepSeek key (checkpoint), PDFs (reuse existing KB).

## Validation Architecture

`workflow.nyquist_validation` is `false` in `.planning/config.json`, so the full section is skipped. Compact test hints for the planner (project conventions: `pytest` with `asyncio_mode = auto`, `respx` for HTTP, `starlette.testclient.TestClient` + `login_test_client` for WS, Playwright scripts are not part of pytest):

- `tests/test_task_memory.py`: merge (sticky goal, **explicit goal change replaces**, dedup incl. paraphrase, caps drop new not old, stable ids), parse/validate (fences, `<think>`, garbage -> fail-soft), snapshot diff («новое»), prompt-tag neutralising, prompt rendering lines.
- `tests/test_task_memory_ws.py` (pattern of `test_rag_ws.py`): after a turn the row and `rag_sources.task_memory` exist and `done.rag.task_memory` matches; extraction timeout/bad JSON -> turn completes, memory unchanged, `failed: true`; gated turn still updates (D-06); non-RAG chat makes no extraction call (assert respx call count); user message deleted on LLM error leaves memory untouched; row and message committed atomically.
- `tests/test_task_memory_api.py`: GET memory `task_state` (None without RAG), PUT goal, DELETE item, POST reset: Origin 403, content-type 415, other user's chat 404, cross-user isolation; branch restore (switch to older leaf restores older snapshot; no-op branch keeps edits; root -> empty).
- `tests/test_cascade_delete.py` extension: `ChatTaskMemory` removed with the chat.
- `tests/test_rag_history.py`: history pair loader (active branch only, gated reply replaced by marker, trimming, N=0), `validate_condensed`, `_rewrite_stage` history branch (raw+condensed merged by best cosine, bad output falls back to raw, gate still fires when neither survives, first turn unchanged), eval flag off = Phase 15 behaviour. Existing `tests/test_rag_pipeline.py`, `test_rag_rank.py`, `test_rag_turn.py` patterns apply.
- `tests/test_rag_api.py` extension: `history_turns` default 3, range 0-10 (422 outside), `0` persists, omitted field unchanged; `tests/test_database.py` pattern for the idempotent column.
- `tests/test_rag_search_static.py` / `test_memory_panel_ui.py` / `test_static_js_syntax.py`: source guards for the new JS (`textContent` only, no `innerHTML` with server text, `data-memory-action="task-reset"`, `rag-history-turns`, `loadChatMemory` called after branch calls).
- Fixture/report/driver tests: `test_dialog_fixture.py`, `test_dialog_eval.py` (per-turn auto checks on canned frames; refuses ports 8000/8001 and `app.db`), `test_rag_report_day25.py`, `test_rag_judge.py` extension (new rubric parse).
- Quick run: `pytest tests/test_task_memory.py tests/test_rag_history.py -q`; full: `pytest tests/ -v` (the suite does not need LM Studio).
- Update `docs/TESTING_GUIDE.md` (new test files + the E2E/driver script), `docs/API_SPEC.md` (new routes, `task_state`, `history_turns`, `done.rag.task_memory`), `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md` per CONTEXT.

## Security Domain

`security_enforcement` is not set to false, so it applies.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no change | Existing session cookie; WS auth unchanged; the driver logs in via `/api/v1/auth/login` |
| V3 Session Management | no change | HTTP-only cookie (no JWT/localStorage) |
| V4 Access Control | yes | Every new route resolves the chat with `_get_chat_or_404(..., current_user.id)` (404, not 403); `ChatTaskMemory.user_id` set from the chat owner; branch restore only for the caller's chat |
| V5 Input Validation | yes | pydantic bodies (goal 1..300 chars, `history_turns` 0..10); extraction output validated and clipped before storage; item ids are ints |
| V6 Cryptography | no | none |
| V13/V14 API & config | yes | `require_allowed_origin` on all mutating routes, `require_json_content_type` on routes with a body |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via user text stored in memory and replayed into the system prompt / condensing prompt | Tampering / Elevation | Memory is user-stated by design; keep it inside labelled data tags, neutralise tag-closing sequences, cap lengths, keep the instruction hierarchy ("data, not instructions") as in `REWRITE_SYSTEM_PROMPT` |
| Memory poisoning from retrieved law text / model output | Tampering | D-04: only user messages feed items; answer is context only; tests with hostile stub output |
| Cross-user read/write of another chat's memory (IDOR) | Info disclosure | Ownership check + 404; tests with two users |
| CSRF on mutation routes | Tampering | Origin check dependency (cookie auth) |
| XSS via memory strings | Tampering | `textContent` only (`test_rag_static.py` style guard); no `innerHTML` |
| Logging user text | Info disclosure | Log ids, counts, error type only (project convention) |
| DoS via huge memory / many history turns | DoS | Caps on items, goal, `history_turns` <= 10, trimmed answers, timeouts |

## Project Constraints (from CLAUDE.md)

- Hard constraints: no Docker/npm/Node/Redis/Celery; no `multiprocessing`/`os.fork`; IPC only REST + WS; vanilla JS + CDN only (Tailwind, Marked.js, DOMPurify); HTTP-only session cookie auth; **all new data scoped by `user_id`** (the new table carries `user_id` and cascades from both `user` and `chat`).
- Code conventions: type hints everywhere, `async`/`await` for all I/O, `structlog` (no `print` except `run.py`), `datetime.now(timezone.utc)`, import order stdlib -> third-party -> local, `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` (never `Field(ondelete=...)`), `.is_(None)` in `where()`, `await session.commit()` after writes and `await session.rollback()` in exception handlers, no bare `except:`, never insert HTML without `DOMPurify.sanitize()` (here: `textContent` only), no hardcoded secrets, wrap `websocket.receive_json()` in `asyncio.wait_for`.
- Testing: `pytest` + `pytest-asyncio` (`asyncio_mode = auto`), `respx` for HTTP; tests use a separate DB; `docs/TESTING_GUIDE.md` must be consulted/updated.
- Process rules from memory/CLAUDE.md: UAT/browser checks run on an isolated copy at ports 18000/18001, never kill the user's app on 8000/8001; branch `Day25`; push and merge to `main` on phase completion; **no `Co-Authored-By: Claude` line in commits** (user's global and project instruction; this overrides the attribution reminder).
- No linter/formatter is configured; follow PEP 8 by hand. GSD workflow enforcement: file changes go through a GSD command.
- No project skills directory exists (`.claude/skills`, `.agents/skills` absent).

## Sources

### Primary (HIGH confidence — read directly from this repository)
- `agent/ws.py` (turn flow, `_complete_gated_turn`, persist/`done` frames), `agent/rag_turn.py`, `agent/rag_pipeline.py`, `agent/rag_llm.py`, `agent/rag_rank.py`, `agent/rag.py` (payload v3, block merge), `agent/rag_api.py`, `agent/context_engine.py` (`build_system_prompt`, `build_llm_context`, `extract_and_update_facts`), `agent/memory.py`, `agent/main.py` (memory/branch/tree routes, `_message_to_response`), `agent/schemas.py`, `shared/models.py`, `shared/database.py` (migrations), `ui/static/app.js` (memory panel, `done` handling, branch functions, RAG blocks/popover), `ui/static/index.html`, `scripts/rag_eval.py`, `scripts/rag_judge.py`, `scripts/e2e_kb_playwright.py`, `scripts/e2e_rag_cite_playwright.py`, `tests/test_rag_ws.py`, `tests/test_rag.py`, `tests/conftest.py`, `docs/TESTING_GUIDE.md`, `Day24_report.md`, `tests/fixtures/rag/control_set.json`, `eval_out/day23/eval.db` (rows inspected).
- `.planning/phases/17-.../17-CONTEXT.md`, `17-UI-SPEC.md`, `.planning/REQUIREMENTS.md`, `.planning/config.json`, `.planning/research/{SUMMARY,ARCHITECTURE}.md` (grep).

### Secondary (MEDIUM confidence)
- `pip show` / `pip index versions` output for `websockets` (version facts only).

### Tertiary (LOW confidence)
- None used for decisions; no web search was needed because the phase adds no new external library or API.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries; one test-script dependency already installed.
- Architecture: HIGH — hook points, ordering and data flow read from code; the dedicated-table recommendation deviates from the "reserved row" wording but is within CONTEXT discretion and keeps the UI-SPEC API shape.
- Pitfalls: HIGH for code-derived ones (payload version test, branch not reloading memory, `0` handling, gate false refusals from Day24 numbers); MEDIUM for local-model latency/timeouts (needs the first live run to tune).

**Research date:** 2026-10-10
**Valid until:** ~30 days (internal code; revalidate if Phases 15/16 files change before planning)
